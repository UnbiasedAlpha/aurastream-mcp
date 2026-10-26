"""Thin async client for the AuraStream /timeseries endpoint."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

import httpx

from .sources import SOURCES, intervals_for

API_URL = os.environ.get("AURASTREAM_API_URL", "https://api.aurastream.unbiased-alpha.com/timeseries")
DASHBOARD_URL = "https://aurastream.unbiased-alpha.com"
TIMEOUT_SECONDS = 130.0  # the API itself times out at 120 s

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AuraStreamError(Exception):
    """An error with a short, user-facing message."""


def credentials_from_env(source: str, env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """Build the ``credentials`` object for ``source`` from environment variables.

    Returns None for public sources (optional fields are sent when set). Raises
    AuraStreamError naming the missing variables if a credentialed source is not configured.
    """
    env = os.environ if env is None else env
    spec = SOURCES[source]
    if not spec.credentials:
        return None
    creds: dict[str, str] = {}
    missing: list[str] = []
    for cf in spec.credentials:
        value = env.get(cf.env) or cf.default
        if value:
            for name in cf.fields:
                creds[name] = value
        elif not cf.optional:
            missing.append(cf.env)
    if spec.credentials_one_of and not creds:
        missing = [" or ".join(c.env for c in spec.credentials)]
    if missing:
        raise AuraStreamError(
            f"Source '{source}' needs your own {spec.label} credentials, which are not configured. "
            f"Set {', '.join(missing)} in the MCP server's environment (e.g. the 'env' block of the "
            "client config) and restart it. Credentials are never accepted as tool arguments."
        )
    return creds or None


def parse_location(ticker: str) -> dict[str, float]:
    """'48.85,2.35' -> {"latitude": 48.85, "longitude": 2.35} (weather sources)."""
    try:
        lat, lon = (float(x) for x in ticker.split(","))
    except ValueError:
        raise AuraStreamError(f"Locations are written 'latitude,longitude', e.g. '48.85,2.35'; got '{ticker}'.") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise AuraStreamError(f"Location '{ticker}' is out of range (latitude -90..90, longitude -180..180).")
    return {"latitude": lat, "longitude": lon}


def location_key(location: dict[str, float]) -> str:
    """How the API keys a location's result, e.g. '48.85_2.35'."""
    return f"{location['latitude']}_{location['longitude']}"


def build_body(
    source: str,
    tickers: list[str],
    start: str,
    end: str,
    interval: str = "1d",
    dtype: str = "bars",
    env: Mapping[str, str] | None = None,
    **params: Any,
) -> dict[str, Any]:
    """Validate the arguments and build the JSON request body.

    ``params`` are source-specific request parameters (price_type, instrument,
    dataset, ...); None values are ignored.
    """
    source = source.strip().lower()
    if source not in SOURCES:
        raise AuraStreamError(f"Unknown source '{source}'. Available: {', '.join(SOURCES)}. Call list_sources for details.")
    spec = SOURCES[source]

    tickers = [t.strip() for t in tickers if t and t.strip()]
    if not tickers:
        raise AuraStreamError("Pass at least one ticker.")
    for label, value in (("start", start), ("end", end)):
        if not _DATE_RE.match(value.strip()):
            raise AuraStreamError(f"{label} must be YYYY-MM-DD, got '{value}'.")
    if interval not in intervals_for(source):
        raise AuraStreamError(
            f"Source '{source}' does not support interval '{interval}'. Use one of: {', '.join(intervals_for(source))}."
        )
    if dtype not in spec.dtypes:
        raise AuraStreamError(f"Source '{source}' supports dtype {', '.join(spec.dtypes)}; got '{dtype}'.")

    extras = {name: value for name, value in params.items() if value is not None}
    for name, value in list(extras.items()):
        param = spec.params.get(name)
        if param is None:
            raise AuraStreamError(f"Parameter '{name}' does not apply to source '{source}'.")
        if param.is_bool:
            if not isinstance(value, bool):
                raise AuraStreamError(f"{name} for '{source}' must be true or false; got '{value}'.")
        elif param.is_int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise AuraStreamError(f"{name} for '{source}' must be a whole number; got '{value}'.")
        elif param.is_dict:
            if not isinstance(value, dict):
                raise AuraStreamError(f"{name} for '{source}' must be a JSON object.")
        elif param.is_list:
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                raise AuraStreamError(f"{name} for '{source}' must be a list of names.")
            extras[name] = [v.strip() for v in value]
        elif param.choices is not None and value not in param.choices:
            raise AuraStreamError(f"{name} for '{source}' must be one of {', '.join(param.choices)}; got '{value}'.")
        elif param.choices is None and not (isinstance(value, str) and value.strip()):
            raise AuraStreamError(f"{name} for '{source}' must be a non-empty string.")
        if param.api_name:
            extras[param.api_name] = extras.pop(name)

    tickerlist: list[Any] = tickers
    if spec.ticker_kind == "latlon":
        tickerlist = [parse_location(t) for t in tickers]

    body: dict[str, Any] = {
        "tickerlist": tickerlist,
        "start": start.strip(),
        "end": end.strip(),
        "interval": interval,
        "source": source,
    }
    if spec.dtype_fields is not None:
        # Sources whose data type is not a plain "dtype" field (e.g. binance funding).
        for name, value in spec.dtype_fields[dtype].items():
            if extras.get(name, value) != value:
                raise AuraStreamError(f"dtype '{dtype}' for '{source}' needs {name} '{value}'; got '{extras[name]}'.")
            body[name] = value
    elif len(spec.dtypes) > 1:
        # Only send dtype where the source has more than one; matches the docs examples.
        body["dtype"] = dtype
    body.update(extras)
    creds = credentials_from_env(source, env)
    if creds:
        body["credentials"] = creds
    return body


def _error_message(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("message") or payload.get("error") or payload.get("detail") or ""
    else:
        detail = resp.text.strip()
    return str(detail)[:300]


def map_error(resp: httpx.Response) -> AuraStreamError:
    """Turn a non-200 response into a short, actionable message."""
    status = resp.status_code
    detail = _error_message(resp)
    if status == 429:
        if "limit exceeded" in detail.lower():
            return AuraStreamError(
                "AuraStream monthly call allowance is used up. It resets on the 1st of next month (00:00 UTC); "
                f"to keep going now, upgrade the plan at {DASHBOARD_URL}."
            )
        return AuraStreamError("AuraStream rate limit hit (too many requests in a short time). Wait a few seconds and retry.")
    if status in (401, 403):
        return AuraStreamError(
            f"AuraStream rejected the API key (HTTP {status}). Check AURASTREAM_API_KEY "
            f"(or the key your client sends); keys are on the dashboard at {DASHBOARD_URL}."
        )
    if status == 400:
        return AuraStreamError(f"AuraStream rejected the request parameters (HTTP 400): {detail or 'no detail'}. "
                               "Check ticker format, interval and dates for this source (see list_sources).")
    if status in (502, 504):
        return AuraStreamError(f"AuraStream timed out or the gateway failed (HTTP {status}). "
                               "Try a shorter date range, a coarser interval or fewer tickers.")
    if status >= 500:
        return AuraStreamError(f"The upstream data source returned an error (HTTP {status}): {detail or 'no detail'}. "
                               "Common causes: wrong ticker format for this source, invalid provider credentials, "
                               "or an interval the source does not support.")
    return AuraStreamError(f"AuraStream returned HTTP {status}: {detail or 'no detail'}")


async def fetch(body: dict[str, Any], api_key: str | None) -> dict[str, Any]:
    """POST the body and return the parsed JSON, or raise AuraStreamError."""
    if not api_key:
        raise AuraStreamError(
            "No AuraStream API key. Set AURASTREAM_API_KEY in the MCP server's environment, or in HTTP mode "
            "send it as an x-api-key or 'Authorization: Bearer' header, or add ?api_key=<key> to the server URL. "
            f"Free keys: {DASHBOARD_URL}."
        )
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
            resp = await http.post(API_URL, json=body, headers={"x-api-key": api_key})
    except httpx.TimeoutException:
        raise AuraStreamError("AuraStream did not answer in time. Try a shorter date range or fewer tickers.") from None
    except httpx.HTTPError as exc:
        raise AuraStreamError(f"Could not reach AuraStream: {exc.__class__.__name__}: {exc}") from None
    if resp.status_code != 200:
        raise map_error(resp)
    try:
        data = resp.json()
    except ValueError:
        raise AuraStreamError("AuraStream returned a response that is not JSON.") from None
    if not isinstance(data, dict):
        raise AuraStreamError(f"Unexpected response shape from AuraStream: {str(data)[:200]}")
    # Errors that come back with a 200, e.g. {"error": "..."}.
    if len(data) == 1 and next(iter(data)) in ("error", "message") and isinstance(next(iter(data.values())), str):
        raise AuraStreamError(f"AuraStream error: {next(iter(data.values()))[:300]}")
    return data
