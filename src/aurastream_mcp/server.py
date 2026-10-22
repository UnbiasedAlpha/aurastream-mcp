"""AuraStream MCP server: tools, API-key resolution and the CLI entry point."""

from __future__ import annotations

import argparse
import math
import os
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from . import __version__
from .client import AuraStreamError, build_body, fetch
from .formatting import DEFAULT_MAX_ROWS, HARD_MAX_ROWS, format_response
from .sources import DTYPES, INTERVALS, SOURCES, intervals_for

INSTRUCTIONS = """\
AuraStream is a historical market data API (crypto, equities, FX, futures, macro) behind one endpoint.
Call list_sources first if unsure which source covers an instrument or how its tickers are written.
Every get_timeseries/get_latest call counts against the user's monthly AuraStream allowance
(free plan: 500 calls), so fetch several tickers in one call and avoid repeating identical calls.
Data is historical, not a real-time feed."""

# Set by main(); in HTTP mode with require_client_key the env key is never used.
_require_client_key = False

mcp = MCPServer(name="aurastream", instructions=INSTRUCTIONS, version=__version__)

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True)

SourceName = Literal[tuple(SOURCES)]  # type: ignore[valid-type]
Interval = Literal[INTERVALS]  # type: ignore[valid-type]
DType = Literal[DTYPES]  # type: ignore[valid-type]


def resolve_api_key(headers: Mapping[str, str] | None, env: Mapping[str, str] | None = None) -> str | None:
    """Pick the AuraStream key: client request header first (HTTP mode), then env.

    Headers checked: ``x-api-key`` and ``Authorization: Bearer <key>``.
    """
    env = os.environ if env is None else env
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    key = lowered.get("x-api-key", "").strip()
    if key:
        return key
    auth = lowered.get("authorization", "").strip()
    if auth.lower().startswith("bearer ") and auth[7:].strip():
        return auth[7:].strip()
    if _require_client_key:
        return None
    return env.get("AURASTREAM_API_KEY") or None


def _headers(ctx: Context | None) -> Mapping[str, str] | None:
    if ctx is None:
        return None
    try:
        return ctx.headers
    except ValueError:  # no active request (e.g. called directly in tests)
        return None


def list_sources_text(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    lines = [
        "AuraStream sources available through this server.",
        "Credentialed sources use the user's own provider account; keys are read from the server's "
        "environment, never passed as tool arguments.",
        "",
    ]
    for s in SOURCES.values():
        if s.needs_credentials:
            missing = [c.env for c in s.credentials if not env.get(c.env) and c.default is None]
            creds = "required, configured" if not missing else f"required, NOT configured (set {', '.join(missing)})"
        else:
            creds = "not required"
        lines.append(f"- {s.name} ({s.label}): {s.asset_classes}")
        lines.append(f"  tickers: {s.ticker_format}")
        lines.append(f"  intervals: {', '.join(intervals_for(s.name))}")
        lines.append(f"  dtypes: {', '.join(s.dtypes)}; credentials: {creds}")
        for name, desc in s.params.items():
            lines.append(f"  param {name}: {desc}")
        if s.notes:
            lines.append(f"  note: {s.notes}")
    return "\n".join(lines)


@mcp.tool(annotations=_READ_ONLY)
def list_sources() -> str:
    """List the AuraStream data sources: asset classes, ticker format, intervals, supported dtypes,
    optional source-specific parameters, and whether provider credentials are needed and
    configured. Free to call (does not use the API allowance)."""
    return list_sources_text()


async def run_timeseries(
    *,
    source: str,
    tickers: list[str],
    start: str,
    end: str,
    interval: str = "1d",
    dtype: str = "bars",
    price_type: str | None = None,
    instrument: str | None = None,
    assetclass: str | None = None,
    category: str | None = None,
    feed: str | None = None,
    frequency: str | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    api_key: str | None,
) -> str:
    body = build_body(
        source, tickers, start, end, interval, dtype, price_type,
        instrument, assetclass, category, feed, frequency,
    )
    data = await fetch(body, api_key)
    head = f"source={body['source']} interval={interval} dtype={dtype} range={body['start']}..{body['end']}"
    return head + "\n\n" + format_response(data, body["tickerlist"], max_rows, body["start"], body["end"])


@mcp.tool(annotations=_READ_ONLY)
async def get_timeseries(
    source: Annotated[SourceName, Field(description="Data source id, see list_sources.")],
    tickers: Annotated[list[str], Field(description="Tickers in the source's own format, e.g. ['BTCUSDT'] for binance, ['EUR_USD'] for oanda, ['SPY'] for yahoo. Batch several tickers in one call.")],
    start: Annotated[str, Field(description="Start date, YYYY-MM-DD.")],
    end: Annotated[str, Field(description="End date, YYYY-MM-DD.")],
    interval: Annotated[Interval, Field(description="Bar size. Support varies by source (list_sources shows each source's intervals).")] = "1d",
    dtype: Annotated[DType, Field(description="'bars' (OHLCV) everywhere; binance also 'funding'/'orderbook'; oanda also 'orderbook'; alpaca also 'trades'/'quotes'; yahoo also 'earnings'.")] = "bars",
    price_type: Annotated[Literal["M", "A", "B"] | None, Field(description="oanda only: A ask (default), B bid, M mid.")] = None,
    instrument: Annotated[str | None, Field(description="binance: spot (default) or perpetual (needed for dtype funding). kraken: futures (default) or spot.")] = None,
    assetclass: Annotated[Literal["equity", "crypto"] | None, Field(description="alpaca only.")] = None,
    category: Annotated[Literal["linear", "inverse"] | None, Field(description="bybit only: linear (default) or inverse.")] = None,
    feed: Annotated[Literal["iex", "sip"] | None, Field(description="alpaca only: iex (default) or sip (needs a paid Alpaca plan).")] = None,
    frequency: Annotated[Literal["quarterly", "annual"] | None, Field(description="fred and finnhub only: quarterly (default) or annual.")] = None,
    max_rows: Annotated[int, Field(ge=1, le=HARD_MAX_ROWS, description="Max rows returned per ticker; the most recent rows are kept.")] = DEFAULT_MAX_ROWS,
    ctx: Context | None = None,
) -> str:
    """Fetch historical market data from AuraStream and return it as a CSV table per ticker
    (timestamp,Open,High,Low,Close,Volume for bars). Long results are cut to the most recent
    max_rows rows per ticker, with the total row count noted. Each call uses one unit of the
    user's monthly AuraStream allowance. Provider credentials (oanda, alpaca, polygon, fred,
    finnhub) come from the server's environment; never ask the user to paste them."""
    try:
        return await run_timeseries(
            source=source, tickers=tickers, start=start, end=end, interval=interval, dtype=dtype,
            price_type=price_type, instrument=instrument, assetclass=assetclass, category=category,
            feed=feed, frequency=frequency, max_rows=max_rows,
            api_key=resolve_api_key(_headers(ctx)),
        )
    except AuraStreamError as exc:
        raise ToolError(str(exc)) from None


_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "M": 2678400, "q": 7948800, "y": 31622400}
_ROUND_THE_CLOCK = {"binance", "bybit", "kraken"}


def latest_window(source: str, interval: str, bars: int, now: datetime | None = None) -> tuple[str, str]:
    """A start/end date window that should contain at least ``bars`` recent bars.

    The API takes whole dates, so the window is padded: one extra day for
    24/7 crypto venues, five for markets with weekends and holidays.
    """
    now = now or datetime.now(timezone.utc)
    seconds = int(interval[:-1]) * _UNIT_SECONDS[interval[-1]]
    span = timedelta(seconds=seconds * bars * 1.5)
    days = math.ceil(span.total_seconds() / 86400) + (1 if source in _ROUND_THE_CLOCK else 5)
    start = now - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")


@mcp.tool(annotations=_READ_ONLY)
async def get_latest(
    source: Annotated[SourceName, Field(description="Data source id, see list_sources.")],
    tickers: Annotated[list[str], Field(description="Tickers in the source's own format.")],
    interval: Annotated[Interval, Field(description="Bar size, e.g. 1h or 1d.")] = "1d",
    bars: Annotated[int, Field(ge=1, le=500, description="How many of the most recent bars to return per ticker.")] = 10,
    ctx: Context | None = None,
) -> str:
    """Return the most recent N bars (OHLCV) per ticker, without having to pick dates.
    Uses one unit of the monthly AuraStream allowance. Data is historical/delayed,
    not a live quote; the last bar may still be forming."""
    start, end = latest_window(source, interval, bars)
    try:
        return await run_timeseries(
            source=source, tickers=tickers, start=start, end=end, interval=interval,
            max_rows=bars, api_key=resolve_api_key(_headers(ctx)),
        )
    except AuraStreamError as exc:
        raise ToolError(str(exc)) from None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="aurastream-mcp", description="AuraStream market data MCP server.")
    parser.add_argument("--http", action="store_true", help="Serve streamable HTTP instead of stdio.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000).")
    parser.add_argument("--path", default="/mcp", help="HTTP endpoint path (default /mcp).")
    parser.add_argument(
        "--allowed-host", action="append", default=[], metavar="HOST",
        help="Public Host header to accept, e.g. mcp.example.com (repeatable). Needed behind a tunnel or reverse proxy.",
    )
    parser.add_argument(
        "--require-client-key", action="store_true",
        help="HTTP mode: only use the key sent by the client (x-api-key or Authorization: Bearer); ignore AURASTREAM_API_KEY.",
    )
    args = parser.parse_args(argv)

    if not args.http:
        mcp.run()
        return

    global _require_client_key
    _require_client_key = args.require_client_key

    from mcp.server.transport_security import TransportSecuritySettings

    security = None
    if args.allowed_host:
        hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *args.allowed_host]
        origins = ["http://127.0.0.1:*", "http://localhost:*", *(f"https://{h}" for h in args.allowed_host)]
        security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)

    mcp.run(
        "streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path=args.path,
        stateless_http=True,
        transport_security=security,
    )


if __name__ == "__main__":
    main()
