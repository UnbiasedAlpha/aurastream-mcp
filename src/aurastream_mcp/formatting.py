"""Render AuraStream responses as compact text tables for a model to read."""

from __future__ import annotations

import json
from typing import Any

DEFAULT_MAX_ROWS = 100
HARD_MAX_ROWS = 1000

_PREFERRED = ["Open", "High", "Low", "Close", "Volume"]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return repr(value) if value == value else ""  # NaN -> empty
    if isinstance(value, (dict, list)):
        text = json.dumps(value, separators=(",", ":"))
    else:
        text = str(value)
    if any(c in text for c in ',"\n'):
        text = '"' + text.replace('"', '""') + '"'
    return text


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    preferred = [c for c in _PREFERRED if c in seen]
    return preferred + [c for c in seen if c not in preferred]


def format_ticker(ticker: str, series: Any, max_rows: int, start: str, end: str) -> str:
    if not series:
        return f"## {ticker}\nNo data returned for {start} to {end}. Check the ticker format for this source (see list_sources)."
    if not isinstance(series, dict):
        return f"## {ticker}\n{_cell(series)[:2000]}"

    timestamps = sorted(series)
    total = len(timestamps)
    shown = timestamps[-max_rows:]
    header = f"## {ticker}\n{total} rows, {timestamps[0]} to {timestamps[-1]}."
    if total > len(shown):
        header += (
            f" Showing the last {len(shown)}. Narrow start/end, use a coarser interval, "
            f"or raise max_rows (up to {HARD_MAX_ROWS}) to see more."
        )

    rows = [series[t] for t in shown]
    if all(isinstance(r, dict) for r in rows):
        cols = _columns(rows)
        lines = ["timestamp," + ",".join(cols)]
        lines += [_cell(t) + "," + ",".join(_cell(series[t].get(c)) for c in cols) for t in shown]
    else:
        lines = ["timestamp,value"] + [f"{_cell(t)},{_cell(series[t])}" for t in shown]
    return header + "\n" + "\n".join(lines)


def format_response(data: dict[str, Any], tickers: list[str], max_rows: int, start: str, end: str) -> str:
    """One CSV block per ticker, each capped at ``max_rows`` (most recent rows kept)."""
    max_rows = max(1, min(int(max_rows), HARD_MAX_ROWS))
    order = [t for t in tickers if t in data] + [t for t in data if t not in tickers]
    parts = [format_ticker(t, data[t], max_rows, start, end) for t in order]
    missing = [t for t in tickers if t not in data]
    if missing:
        parts.append(f"Not in response: {', '.join(missing)}. Check the ticker format for this source.")
    return "\n\n".join(parts) if parts else "Empty response: no tickers returned."
