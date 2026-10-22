import json
from datetime import datetime, timezone

import httpx
import pytest
import respx
from mcp import Client

from aurastream_mcp import server
from aurastream_mcp.client import API_URL, AuraStreamError, build_body, credentials_from_env
from aurastream_mcp.formatting import format_response
from aurastream_mcp.server import latest_window, list_sources_text, mcp, resolve_api_key

CRED_VARS = [
    "OANDA_ACCOUNT_ID", "OANDA_TOKEN", "OANDA_ENVIRONMENT", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
    "POLYGON_API_KEY", "FRED_API_KEY", "FINNHUB_API_KEY",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in CRED_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AURASTREAM_API_KEY", "test-key")
    monkeypatch.setattr(server, "_require_client_key", False)


def bars(n, start_day=1):
    return {
        f"2025-01-{d:02d} 00:00:00": {"Open": 1.0 + d, "High": 2.0 + d, "Low": 0.5 + d, "Close": 1.5 + d, "Volume": 100 * d}
        for d in range(start_day, start_day + n)
    }


def text_of(result):
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


# --- request body -----------------------------------------------------------

def test_body_public_source_has_no_credentials():
    body = build_body("yahoo", ["SPY", "AAPL"], "2025-01-01", "2025-02-01", "1d")
    assert body == {
        "tickerlist": ["SPY", "AAPL"], "start": "2025-01-01", "end": "2025-02-01",
        "interval": "1d", "source": "yahoo", "dtype": "bars",
    }
    # dtype is only sent for sources with more than one data type.
    assert "dtype" not in build_body("bybit", ["BTCUSDT"], "2025-01-01", "2025-02-01")


def test_body_public_sources_with_extra_params():
    funding = build_body("binance", ["BTCUSDT"], "2025-06-01", "2025-06-30", "1h", "funding", instrument="perpetual")
    assert funding["dtype"] == "funding" and funding["instrument"] == "perpetual"
    assert "credentials" not in funding
    assert "credentials" not in build_body("firstrate", ["ES"], "2025-01-01", "2025-02-01")
    assert build_body("kraken", ["XXBTZUSD"], "2025-06-01", "2025-06-30", "1h", instrument="spot")["instrument"] == "spot"
    assert build_body("bybit", ["BTCUSD"], "2025-06-01", "2025-06-30", category="inverse")["category"] == "inverse"


def test_body_oanda_from_env(monkeypatch):
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "101-004-1-001")
    monkeypatch.setenv("OANDA_TOKEN", "tok")
    body = build_body("oanda", ["EUR_USD"], "2025-01-01", "2025-01-10", "1h", "bars", price_type="B")
    assert body["credentials"] == {"id": "101-004-1-001", "token": "tok", "environment": "practice"}
    assert body["dtype"] == "bars"
    assert body["price_type"] == "B"


def test_body_alpaca_and_fred_field_names(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "ak")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "as")
    monkeypatch.setenv("FRED_API_KEY", "fk")
    alpaca = build_body("alpaca", ["BTC/USD"], "2025-01-09", "2025-01-10", "1d", "trades", assetclass="crypto", feed="iex")
    # Docs say api_key/secret_key, the backend library reads key/secret: both are sent.
    assert alpaca["credentials"] == {"api_key": "ak", "key": "ak", "secret_key": "as", "secret": "as"}
    assert alpaca["feed"] == "iex"
    assert alpaca["dtype"] == "trades" and alpaca["assetclass"] == "crypto"
    fred = build_body("fred", ["UNRATE"], "2020-01-01", "2025-01-01", "1M", frequency="annual")
    assert fred["credentials"] == {"api_key": "fk", "key": "fk"}
    assert fred["frequency"] == "annual"


def test_missing_credentials_names_env_vars():
    with pytest.raises(AuraStreamError, match="POLYGON_API_KEY"):
        credentials_from_env("polygon")
    with pytest.raises(AuraStreamError, match="OANDA_ACCOUNT_ID, OANDA_TOKEN"):
        build_body("oanda", ["EUR_USD"], "2025-01-01", "2025-01-10")


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"source": "nope"}, "Unknown source"),
        ({"start": "01/01/2025"}, "start must be"),
        ({"interval": "2d"}, "does not support interval"),
        ({"interval": "1s"}, "does not support interval"),
        ({"start": "2025-01-01 10:00"}, "start must be"),
        ({"source": "binance", "tickers": ["BTCUSDT"], "instrument": "futures"}, "instrument for 'binance' must be"),
        ({"source": "binance", "tickers": ["BTCUSDT"], "frequency": "annual"}, "does not apply"),
        ({"dtype": "trades"}, "supports dtype"),
        ({"price_type": "M"}, "does not apply"),
        ({"tickers": [" "]}, "at least one ticker"),
    ],
)
def test_body_validation(kwargs, match):
    args = {"source": "yahoo", "tickers": ["SPY"], "start": "2025-01-01", "end": "2025-02-01"} | kwargs
    with pytest.raises(AuraStreamError, match=match):
        build_body(**args)


async def test_tool_has_no_credential_arguments():
    tools = {t.name: t for t in await mcp.list_tools()}
    assert set(tools) == {"list_sources", "get_timeseries", "get_latest"}
    props = set(tools["get_timeseries"].input_schema["properties"])
    assert not props & {"credentials", "api_key", "token", "key", "secret", "ctx"}


# --- end to end through the MCP layer ---------------------------------------

@respx.mock
async def test_get_timeseries_sends_env_credentials_and_api_key(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "poly-secret")
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json={"AAPL": bars(3)}))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_timeseries",
            {"source": "polygon", "tickers": ["AAPL"], "start": "2025-01-01", "end": "2025-01-05"},
        )
    assert not result.is_error
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "test-key"
    sent = json.loads(request.content)
    assert sent["credentials"] == {"api_key": "poly-secret", "key": "poly-secret"}
    assert sent["tickerlist"] == ["AAPL"] and sent["source"] == "polygon"
    out = text_of(result)
    assert "timestamp,Open,High,Low,Close,Volume" in out
    assert "2025-01-03 00:00:00,4.0,5.0,3.5,4.5,300" in out
    assert "poly-secret" not in out


async def test_unknown_argument_cannot_inject_credentials(monkeypatch):
    # Extra arguments are not part of the schema and never reach the request body.
    monkeypatch.setenv("FRED_API_KEY", "env-fred")
    with respx.mock:
        route = respx.post(API_URL).mock(return_value=httpx.Response(200, json={"UNRATE": bars(1)}))
        async with Client(mcp) as client:
            await client.call_tool(
                "get_timeseries",
                {"source": "fred", "tickers": ["UNRATE"], "start": "2020-01-01", "end": "2025-01-01",
                 "credentials": {"key": "from-chat"}},
            )
    assert route.called
    assert json.loads(route.calls.last.request.content)["credentials"] == {"api_key": "env-fred", "key": "env-fred"}


@respx.mock
async def test_row_cap_keeps_most_recent():
    respx.post(API_URL).mock(return_value=httpx.Response(200, json={"BTCUSDT": bars(20)}))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_timeseries",
            {"source": "binance", "tickers": ["BTCUSDT"], "start": "2025-01-01", "end": "2025-01-21", "max_rows": 5},
        )
    out = text_of(result)
    assert "20 rows" in out and "Showing the last 5" in out
    data_lines = [line for line in out.splitlines() if line.startswith("2025-01-")]
    assert len(data_lines) == 5
    assert data_lines[0].startswith("2025-01-16") and data_lines[-1].startswith("2025-01-20")


def test_format_empty_and_missing_tickers():
    out = format_response({"GOOD": bars(2), "BAD": {}}, ["GOOD", "BAD", "GONE"], 100, "2025-01-01", "2025-01-03")
    assert "## BAD\nNo data returned" in out
    assert "Not in response: GONE" in out
    assert "Showing the last" not in out


# --- error mapping ------------------------------------------------------------

@pytest.mark.parametrize(
    "status, payload, match",
    [
        (429, {"message": "Limit Exceeded"}, "monthly call allowance is used up"),
        (429, {"message": "Too Many Requests"}, "rate limit"),
        (403, {"message": "Forbidden"}, "rejected the API key"),
        (400, {"error": "bad interval"}, "bad interval"),
        (500, {"error": "upstream boom"}, "upstream data source"),
        (504, {"message": "Endpoint request timed out"}, "shorter date range"),
        (200, {"error": "Invalid source"}, "Invalid source"),
    ],
)
@respx.mock
async def test_error_mapping(status, payload, match):
    respx.post(API_URL).mock(return_value=httpx.Response(status, json=payload))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_timeseries", {"source": "yahoo", "tickers": ["SPY"], "start": "2025-01-01", "end": "2025-02-01"}
        )
    assert result.is_error
    assert match in text_of(result)


@respx.mock
async def test_timeout_is_reported():
    respx.post(API_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_timeseries", {"source": "yahoo", "tickers": ["SPY"], "start": "2025-01-01", "end": "2025-02-01"}
        )
    assert result.is_error and "did not answer in time" in text_of(result)


async def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("AURASTREAM_API_KEY")
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_timeseries", {"source": "yahoo", "tickers": ["SPY"], "start": "2025-01-01", "end": "2025-02-01"}
        )
    assert result.is_error and "AURASTREAM_API_KEY" in text_of(result)


# --- API key resolution / misc ---------------------------------------------------

def test_resolve_api_key_prefers_headers(monkeypatch):
    env = {"AURASTREAM_API_KEY": "env-key"}
    assert resolve_api_key(None, env) == "env-key"
    assert resolve_api_key({"X-Api-Key": "hdr"}, env) == "hdr"
    assert resolve_api_key({"Authorization": "Bearer tok"}, env) == "tok"
    assert resolve_api_key({"host": "x"}, env) == "env-key"
    monkeypatch.setattr(server, "_require_client_key", True)
    assert resolve_api_key({"host": "x"}, env) is None
    assert resolve_api_key({"authorization": "Bearer tok"}, env) == "tok"


def test_list_sources_reports_configuration(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "x")
    text = list_sources_text()
    assert "- yahoo (Yahoo Finance)" in text
    fred = text.split("- fred")[1].split("\n- ")[0]
    assert "required, configured" in fred
    polygon = text.split("- polygon")[1].split("\n- ")[0]
    assert "NOT configured (set POLYGON_API_KEY)" in polygon


def test_latest_window():
    now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
    assert latest_window("yahoo", "1d", 10, now) == ("2025-05-26", "2025-06-16")
    assert latest_window("binance", "1h", 10, now) == ("2025-06-13", "2025-06-16")
    assert latest_window("oanda", "1M", 3, now)[0] == "2025-01-21"


@respx.mock
async def test_get_latest_returns_requested_bars():
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json={"SPY": bars(15)}))
    async with Client(mcp) as client:
        result = await client.call_tool("get_latest", {"source": "yahoo", "tickers": ["SPY"], "bars": 3})
    sent = json.loads(route.calls.last.request.content)
    assert sent["interval"] == "1d" and sent["source"] == "yahoo"
    data_lines = [line for line in text_of(result).splitlines() if line.startswith("2025-01-")]
    assert len(data_lines) == 3
