"""AuraStream MCP server: tools, API-key resolution and the CLI entry point."""

from __future__ import annotations

import argparse
import math
import os
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from . import __version__
from .client import AuraStreamError, build_body, fetch, location_key
from .formatting import DEFAULT_MAX_ROWS, HARD_MAX_ROWS, format_response
from .sources import DTYPES, INTERVALS, SOURCES, intervals_for

INSTRUCTIONS = """\
AuraStream is a historical data API behind one endpoint: market data (crypto exchanges and DEXs,
equities, FX, futures, CFDs), crypto order book signals (Nemeton) and Unbiased Alpha's derived datasets
(signals: crypto crowding, Fed tone, 8-K flags, insider conviction, energy surprises; Nemeton key), macro and energy (FRED, EIA, US Treasury,
World Bank, SDMX central bank series, Trading Economics, Nasdaq Data Link), positioning and flows (CFTC,
SEC insider trades and planned sales, 8-K events, fails-to-deliver, 13D/13G stakes, 13F and fund holdings and votes, EU short positions, USDA WASDE, WARN layoffs, degree days, Fed texts, federal contracts, UK/EU/US/Hong Kong trade), crypto fundamentals and news
(DefiLlama, CoinGecko, GDELT) and historical weather (Open-Meteo, Visual Crossing).
Call list_sources first if unsure which source covers something or how its tickers are written.
Intervals: upper-case M is minutes, lower-case m is months (15M = 15 minutes, 1m = one month).
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


def resolve_api_key(
    headers: Mapping[str, str] | None,
    env: Mapping[str, str] | None = None,
    query: Mapping[str, str] | None = None,
) -> str | None:
    """Pick the AuraStream key: what the client sent (HTTP mode) first, then env.

    Checked in order: the ``x-api-key`` header, ``Authorization: Bearer <key>``, and the
    ``api_key`` URL parameter (for clients such as ChatGPT that cannot send custom headers).
    """
    env = os.environ if env is None else env
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    key = lowered.get("x-api-key", "").strip()
    if key:
        return key
    auth = lowered.get("authorization", "").strip()
    if auth.lower().startswith("bearer ") and auth[7:].strip():
        return auth[7:].strip()
    key = (query or {}).get("api_key", "").strip()
    if key:
        return key
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


def _query(ctx: Context | None) -> Mapping[str, str] | None:
    if ctx is None:
        return None
    try:
        return getattr(ctx.request_context.request, "query_params", None)
    except ValueError:
        return None


def list_sources_text(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    lines = [
        "AuraStream sources available through this server.",
        "Credentialed sources use the user's own provider account; keys are read from the server's "
        "environment, never passed as tool arguments.",
        "",
    ]
    if _require_client_key:
        lines[1:2] = [
            "This is a shared hosted server: it holds no provider keys, so sources marked "
            "'NOT configured' only work when the user runs aurastream-mcp locally with their own keys "
            "(pip install aurastream-mcp).",
        ]
    for s in SOURCES.values():
        if s.credentials_one_of:
            either = " or ".join(c.env for c in s.credentials)
            creds = "required, configured" if any(env.get(c.env) for c in s.credentials) \
                else f"required, NOT configured (set {either})"
        elif s.needs_credentials:
            missing = [c.env for c in s.credentials if not c.optional and not env.get(c.env) and c.default is None]
            creds = "required, configured" if not missing else f"required, NOT configured (set {', '.join(missing)})"
        elif s.credentials:
            creds = f"not required (optional: {', '.join(c.env for c in s.credentials)})"
        else:
            creds = "not required"
        lines.append(f"- {s.name} ({s.label}): {s.asset_classes}")
        lines.append(f"  tickers: {s.ticker_format}")
        lines.append(f"  intervals: {', '.join(intervals_for(s.name))}")
        lines.append(f"  dtypes: {', '.join(s.dtypes)}; credentials: {creds}")
        for name, param in s.params.items():
            lines.append(f"  param {name}: {param.description}")
        if s.fields:
            lines.append(f"  fields: {s.fields}")
        if s.notes:
            lines.append(f"  note: {s.notes}")
    return "\n".join(lines)


@mcp.tool(annotations=_READ_ONLY)
def list_sources() -> str:
    """List the AuraStream data sources: what each covers, ticker format, intervals, supported dtypes,
    optional source-specific parameters, the fields returned when they are not OHLCV, and whether
    provider credentials are needed and configured. Free to call (does not use the API allowance)."""
    return list_sources_text()


async def run_timeseries(
    *,
    source: str,
    tickers: list[str],
    start: str,
    end: str,
    interval: str = "1d",
    dtype: str = "bars",
    max_rows: int = DEFAULT_MAX_ROWS,
    api_key: str | None,
    **params: Any,
) -> str:
    body = build_body(source, tickers, start, end, interval, dtype, **params)
    data = await fetch(body, api_key)
    head = f"source={body['source']} interval={interval} dtype={dtype} range={body['start']}..{body['end']}"
    keys = [location_key(t) if isinstance(t, dict) else t for t in body["tickerlist"]]
    return head + "\n\n" + format_response(data, keys, max_rows, body["start"], body["end"])


@mcp.tool(annotations=_READ_ONLY)
async def get_timeseries(
    source: Annotated[SourceName, Field(description="Data source id, see list_sources.")],
    tickers: Annotated[list[str], Field(description="Tickers in the source's own format, e.g. ['BTCUSDT'] for binance, ['EUR_USD'] for oanda, ['SPY'] for yahoo, ['GC'] for cftc, ['gold:CH'] for hmrc; weather sources take 'latitude,longitude', e.g. ['48.85,2.35']. Batch several tickers in one call.")],
    start: Annotated[str, Field(description="Start date, YYYY-MM-DD.")],
    end: Annotated[str, Field(description="End date, YYYY-MM-DD.")],
    interval: Annotated[Interval, Field(description="Bar size: upper-case M is minutes, lower-case m is months (15M = 15 minutes, 1m = one month). Support varies by source (list_sources shows each source's intervals).")] = "1d",
    dtype: Annotated[DType, Field(description="'bars' everywhere; binance also 'funding' (futures); oanda also 'orderbook'/'positionbook'; alpaca also 'trades'/'quotes'; yahoo also 'earnings'.")] = "bars",
    price_type: Annotated[Literal["M", "A", "B", "MID", "BID", "ASK"] | None, Field(description="oanda: M mid (default), A ask, B bid. cityindex: MID (default), BID or ASK.")] = None,
    instrument: Annotated[Literal["spot", "futures", "stock", "etf", "fx", "index", "crypto", "options", "option", "perp"] | None, Field(description="binance: spot (default) or futures (USD-M perpetuals). kraken: futures (default) or spot. firstrate: stock (default), etf, futures, fx, index, crypto or options. lyra: option (default) or perp.")] = None,
    continuous_contracts: Annotated[bool | None, Field(description="firstrate futures only: true (default) for continuous series, false for one series per contract.")] = None,
    ticktype: Annotated[Literal["trade", "mark", "spot"] | None, Field(description="kraken futures only: trade (default), mark or spot (index) price.")] = None,
    assetclass: Annotated[Literal["equity", "crypto"] | None, Field(description="alpaca only.")] = None,
    category: Annotated[Literal["linear", "inverse"] | None, Field(description="bybit only: linear (default) or inverse.")] = None,
    feed: Annotated[Literal["iex", "sip"] | None, Field(description="alpaca stocks only: iex (default) or sip (consolidated tape).")] = None,
    adjustment: Annotated[Literal["raw", "split", "dividend", "all"] | None, Field(description="alpaca stock bars only: raw (default), split, dividend or all.")] = None,
    indicator: Annotated[str | None, Field(description="tradingeconomics (required): indicator name, e.g. 'gdp', 'inflation rate'.")] = None,
    metric: Annotated[str | None, Field(description="nemeton (required): signal name, e.g. cumulative-depth, orderbook-pressure, spread (list_sources has all).")] = None,
    level: Annotated[int | None, Field(description="nemeton only: order book depth the metric is computed over (default 1000).")] = None,
    db: Annotated[str | None, Field(description="sdmx (required): provider ID, e.g. BIS, ECB, ESTAT, IMF, OECD.")] = None,
    filters: Annotated[dict[str, dict[str, Any]] | None, Field(description='nasdaq only: column filters per table, e.g. {"QDL/FON": {"contract_code": "967654"}}.')] = None,
    assetcol: Annotated[str | None, Field(description="nasdaq only: one series per value of this column, e.g. contract_code.")] = None,
    index: Annotated[str | None, Field(description="nasdaq only: date column used as the index (default date).")] = None,
    variables: Annotated[list[str] | None, Field(description="openmeteo (required): weather variables, e.g. ['temperature_2m'] with 1h, ['temperature_2m_max'] with 1d. visualcrossing: only these fields.")] = None,
    timezone: Annotated[str | None, Field(description="openmeteo only: IANA time zone, e.g. Europe/Paris (default GMT).")] = None,
    unit_group: Annotated[Literal["us", "metric", "uk", "base"] | None, Field(description="visualcrossing only: units (default us).")] = None,
    vs_currency: Annotated[str | None, Field(description="coingecko only: quote currency (default USD).")] = None,
    sessiontemplate: Annotated[str | None, Field(description="tradestation only: trading session, e.g. Default or USEQPreAndPost.")] = None,
    closed_only: Annotated[bool | None, Field(description="tradestation only: leave out the bar still forming (default true).")] = None,
    bartype: Annotated[Literal["bar", "tick"] | None, Field(description="cityindex only: bar (default) or tick.")] = None,
    dataset: Annotated[str | None, Field(description="edgar: insider (default), financials, earnings, events, ftd, 13dg, 144, 13f, nport or npx. usaspending: monthly (default) or awards. signals: crypto_leverage (default), fed_tone, event_flags, insider_conviction or energy_ag. uscensus: trade (default) or retail. gdelt: themes.")] = None,
    report: Annotated[Literal["disaggregated", "tff", "legacy"] | None, Field(description="cftc only; default depends on the symbol.")] = None,
    options: Annotated[bool | None, Field(description="cftc only: include options (futures-equivalent).")] = None,
    as_of: Annotated[Literal["release", "report", "realtime_start", "date"] | None, Field(description="cftc: release (default, stamped at Friday publication, no look-ahead) or report (Tuesday position date). fred: realtime_start (default, stamped at publication) or date (period described).")] = None,
    concepts: Annotated[list[str] | None, Field(description="edgar financials only: XBRL concepts, e.g. ['Revenues'].")] = None,
    period: Annotated[Literal["quarter", "annual"] | None, Field(description="edgar financials only: quarter (default) or annual.")] = None,
    mode: Annotated[Literal["timelinevol", "timelinevolraw", "timelinetone"] | None, Field(description="gdelt search queries only: share of coverage (default), article count, or tone.")] = None,
    reporter: Annotated[str | None, Field(description="comext only: EU27_2020 (default) or a member state, e.g. DE.")] = None,
    adjusted: Annotated[bool | None, Field(description="uscensus retail only: seasonally adjusted (default true).")] = None,
    frequency: Annotated[Literal["M", "Q", "A"] | None, Field(description="bea only: M, Q or A (default: the series' own).")] = None,
    top: Annotated[int | None, Field(ge=1, description="edgar nport only: keep the largest N positions of each filing.")] = None,
    items: Annotated[list[str] | None, Field(description="edgar events only: 8-K item numbers, e.g. ['5.02'].")] = None,
    issuer: Annotated[str | None, Field(description="edgar npx only: company whose votes to return (CUSIP, ISIN or name).")] = None,
    country: Annotated[list[str] | None, Field(description="netshort only: FR, NL and/or ES (default all).")] = None,
    holder: Annotated[str | None, Field(description="netshort only: keep positions whose holder contains this text.")] = None,
    state: Annotated[list[str] | None, Field(description="warn only: CA and/or NJ (default both).")] = None,
    weights: Annotated[Literal["population", "gas", "electricity", "fuel_oil"] | None, Field(description="degreedays only: population (default); gas, electricity, fuel_oil for heating by division or US.")] = None,
    speaker: Annotated[str | None, Field(description="fedtext speeches only: speaker's last name, e.g. powell.")] = None,
    award_type: Annotated[Literal["contracts", "grants"] | None, Field(description="usaspending only: contracts (default) or grants.")] = None,
    min_amount: Annotated[int | None, Field(ge=0, description="usaspending awards only: minimum award size in US dollars.")] = None,
    max_rows: Annotated[int, Field(ge=1, le=HARD_MAX_ROWS, description="Max rows returned per ticker; the most recent rows are kept.")] = DEFAULT_MAX_ROWS,
    ctx: Context | None = None,
) -> str:
    """Fetch historical data from AuraStream and return it as a CSV table per ticker
    (timestamp,Open,High,Low,Close,Volume for market bars; source-specific fields otherwise, see
    list_sources). Long results are cut to the most recent max_rows rows per ticker, with the
    total row count noted. Each call uses one unit of the user's monthly AuraStream allowance.
    Provider credentials come from the server's environment; never ask the user to paste them."""
    try:
        return await run_timeseries(
            source=source, tickers=tickers, start=start, end=end, interval=interval, dtype=dtype,
            max_rows=max_rows, api_key=resolve_api_key(_headers(ctx), query=_query(ctx)),
            price_type=price_type, instrument=instrument, continuous_contracts=continuous_contracts,
            ticktype=ticktype, assetclass=assetclass,
            category=category, feed=feed, adjustment=adjustment, indicator=indicator, dataset=dataset,
            report=report, options=options,
            as_of=as_of, concepts=concepts, period=period, mode=mode, reporter=reporter,
            metric=metric, level=level, db=db, filters=filters, assetcol=assetcol, index=index,
            variables=variables, timezone=timezone, unit_group=unit_group, vs_currency=vs_currency,
            sessiontemplate=sessiontemplate, closed_only=closed_only, bartype=bartype,
            adjusted=adjusted, frequency=frequency, top=top, items=items, issuer=issuer,
            country=country, holder=holder, state=state, weights=weights, speaker=speaker,
            award_type=award_type, min_amount=min_amount,
        )
    except AuraStreamError as exc:
        raise ToolError(str(exc)) from None


# API convention: M = minutes, m = months.
_UNIT_SECONDS = {"s": 1, "M": 60, "h": 3600, "d": 86400, "w": 604800, "m": 2678400, "q": 7948800, "y": 31622400}
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
            max_rows=bars, api_key=resolve_api_key(_headers(ctx), query=_query(ctx)),
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
        help="HTTP mode: only use the key sent by the client (x-api-key, Authorization: Bearer or ?api_key=); "
        "ignore AURASTREAM_API_KEY. Use this for a shared server.",
    )
    parser.add_argument(
        "--json-response", action="store_true",
        help="HTTP mode: answer with plain JSON instead of an SSE stream (simpler behind a proxy).",
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
        json_response=args.json_response,
        transport_security=security,
    )


if __name__ == "__main__":
    main()
