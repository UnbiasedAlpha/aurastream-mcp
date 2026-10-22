"""Thin async client for the AuraStream /timeseries endpoint."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

import httpx

from .sources import INTERVALS, SOURCES

API_URL = os.environ.get("AURASTREAM_API_URL", "https://api.aurastream.unbiased-alpha.com/timeseries")
DASHBOARD_URL = "https://aurastream.unbiased-alpha.com"
TIMEOUT_SECONDS = 130.0  # the API itself times out at 120 s

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$")


class AuraStreamError(Exception):
    """An error with a short, user-facing message."""


def credentials_from_env(source: str, env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """Build the ``credentials`` object for ``source`` from environment variables.

    Returns None for public sources. Raises AuraStreamError naming the missing
    variables if a credentialed source is not configured.
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
            creds[cf.field] = value
        else:
            missing.append(cf.env)
    if missing:
        raise AuraStreamError(
            f"Source '{source}' needs your own {spec.label} credentials, which are not configured. "
            f"Set {', '.join(missing)} in the MCP server's environment (e.g. the 'env' block of the "
            "client config) and restart it. Credentials are never accepted as tool arguments."
        )
    return creds


def build_body(
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
    continuous_contracts: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate the arguments and build the JSON request body."""
    source = source.strip().lower()
    if source not in SOURCES:
        raise AuraStreamError(f"Unknown source '{source}'. Available: {', '.join(SOURCES)}. Call list_sources for details.")
    spec = SOURCES[source]

    tickers = [t.strip() for t in tickers if t and t.strip()]
    if not tickers:
        raise AuraStreamError("Pass at least one ticker.")
    for label, value in (("start", start), ("end", end)):
        if not _DATE_RE.match(value.strip()):
            raise AuraStreamError(f"{label} must be YYYY-MM-DD or 'YYYY-MM-DD HH:MM', got '{value}'.")
    if interval not in INTERVALS:
        raise AuraStreamError(f"Unsupported interval '{interval}'. Use one of: {', '.join(INTERVALS)}.")
    if dtype not in spec.dtypes:
        raise AuraStreamError(f"Source '{source}' supports dtype {', '.join(spec.dtypes)}; got '{dtype}'.")

    extras = {
        "price_type": price_type,
        "instrument": instrument,
        "assetclass": assetclass,
        "category": category,
        "continuous_contracts": continuous_contracts,
    }
    for name, value in extras.items():
        if value is not None and name not in spec.params:
            raise AuraStreamError(f"Parameter '{name}' does not apply to source '{source}'.")
    if price_type is not None and price_type not in ("M", "A", "B"):
        raise AuraStreamError('price_type must be "M" (mid), "A" (ask) or "B" (bid).')

    body: dict[str, Any] = {
        "tickerlist": tickers,
        "start": start.strip(),
        "end": end.strip(),
        "interval": interval,
        "source": source,
    }
    # Only send dtype where the source has more than one; matches the docs examples.
    if len(spec.dtypes) > 1:
        body["dtype"] = dtype
    body.update({k: v for k, v in extras.items() if v is not None})
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
                "AuraStream monthly call allowance is used up. It resets on the 1st of next month; "
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
            f"send it as an x-api-key or 'Authorization: Bearer' header. Free keys: {DASHBOARD_URL}."
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
