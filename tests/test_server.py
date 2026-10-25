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
from aurastream_mcp.sources import CREDENTIAL_ENV_VARS, SOURCES

OANDA_ENV = {"OANDA_ACCOUNT_ID": "101-004-1-001", "OANDA_TOKEN": "tok"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in CREDENTIAL_ENV_VARS:
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
        "interval": "1d", "source": "yahoo",
    }
    # The live API rejects dtype "bars" for yahoo (its bars are "candles", the default).
    earnings = build_body("yahoo", ["AAPL"], "2025-01-01", "2025-02-01", dtype="earnings")
    assert earnings["dtype"] == "earnings"
    # dtype is only sent for sources with more than one data type.
    assert "dtype" not in build_body("bybit", ["BTCUSDT"], "2025-01-01", "2025-02-01")
    assert build_body("oanda", ["EUR_USD"], "2025-01-01", "2025-01-10", env=OANDA_ENV)["dtype"] == "bars"


def test_body_public_sources_with_extra_params():
    # Binance funding is requested with bars="funding" on futures; the API has no "perpetual" instrument.
    funding = build_body("binance", ["BTCUSDT"], "2025-06-01", "2025-06-30", "1h", "funding")
    assert funding["bars"] == "funding" and funding["instrument"] == "futures"
    assert "dtype" not in funding and "credentials" not in funding
    assert build_body("binance", ["BTCUSDT"], "2025-06-01", "2025-06-30", "1h", "funding", instrument="futures")["instrument"] == "futures"
    spot = build_body("binance", ["BTCUSDT"], "2025-06-01", "2025-06-30", "1h")
    assert "bars" not in spot and "instrument" not in spot
    kraken = build_body("kraken", ["PF_XBTUSD"], "2025-06-01", "2025-06-30", "1h", ticktype="mark")
    assert kraken["ticktype"] == "mark"
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
    # The live API reads key/secret (api_key returns {"error": "'key'"}).
    assert alpaca["credentials"] == {"key": "ak", "secret": "as"}
    assert alpaca["feed"] == "iex"
    assert alpaca["dtype"] == "trades" and alpaca["assetclass"] == "crypto"
    fred = build_body("fred", ["UK", "US"], "2020-01-01", "2025-01-01", "1m", indicator="cpi", as_of="date")
    assert fred["credentials"] == {"key": "fk"}
    # The country shortcut is the API's "bars" field.
    assert fred["bars"] == "cpi" and "indicator" not in fred and fred["as_of"] == "date"


def test_intervals_follow_api_case_convention():
    # Upper-case M is minutes, lower-case m is months, as the API parses them.
    assert build_body("yahoo", ["SPY"], "2025-01-01", "2025-01-05", "15M")["interval"] == "15M"
    assert build_body("yahoo", ["SPY"], "2020-01-01", "2025-01-01", "1m")["interval"] == "1m"
    for source in SOURCES.values():
        for interval in source.intervals or ():
            unit = interval.lstrip("0123456789")
            assert unit in {"s", "M", "h", "d", "w", "m", "q", "y"}, (source.name, interval)


def test_body_new_public_sources():
    cftc = build_body("cftc", ["GC", "ZN"], "2020-01-01", "2025-01-01", "1w", report="legacy", options=True, as_of="report")
    assert cftc == {
        "tickerlist": ["GC", "ZN"], "start": "2020-01-01", "end": "2025-01-01", "interval": "1w", "source": "cftc",
        "report": "legacy", "options": True, "as_of": "report",
    }
    assert build_body("tsa", ["throughput"], "2025-01-01", "2025-02-01")["source"] == "tsa"
    assert "credentials" not in build_body("econcalendar", ["fomc"], "2025-01-01", "2025-12-31")
    assert build_body("comext", ["gold:CH"], "2020-01-01", "2025-01-01", "1m", reporter="DE")["reporter"] == "DE"
    themes = build_body("gdelt", ["ECON_GOLDPRICE"], "2024-01-01", "2024-02-01", "1h", dataset="themes")
    assert themes["dataset"] == "themes" and "credentials" not in themes
    for name in ("treasury", "defillama", "worldbank", "hmrc", "hktrade"):
        interval = SOURCES[name].intervals[0]
        assert "credentials" not in build_body(name, ["x"], "2020-01-01", "2025-01-01", interval)


def test_body_new_credentialed_sources(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "eia")
    monkeypatch.setenv("CENSUS_API_KEY", "census")
    monkeypatch.setenv("EDGAR_USER_AGENT", "Acme ops@acme.test")
    assert build_body("eia", ["crude_stocks"], "2020-01-01", "2025-01-01", "1w")["credentials"] == {"api_key": "eia"}
    assert build_body("uscensus", ["gold"], "2020-01-01", "2025-01-01", "1m")["credentials"] == {"api_key": "census"}
    retail = build_body("uscensus", ["total"], "2020-01-01", "2025-01-01", "1m", dataset="retail", adjusted=False)
    assert retail["dataset"] == "retail" and retail["adjusted"] is False
    monkeypatch.setenv("BEA_API_KEY", "bea")
    bea = build_body("bea", ["pce"], "2024-01-01", "2025-01-01", "1m", frequency="M")
    assert bea["credentials"] == {"api_key": "bea"} and bea["frequency"] == "M"
    edgar = build_body("edgar", ["AAPL"], "2024-01-01", "2025-01-01", dataset="financials",
                       concepts=[" Revenues ", "NetIncomeLoss"], period="annual")
    assert edgar["credentials"] == {"user_agent": "Acme ops@acme.test"}
    assert edgar["concepts"] == ["Revenues", "NetIncomeLoss"] and edgar["period"] == "annual"
    assert build_body("edgar", ["GME"], "2025-01-01", "2025-02-01", dataset="ftd")["dataset"] == "ftd"
    nport = build_body("edgar", ["VTSAX"], "2025-01-01", "2026-01-01", dataset="nport", top=50)
    assert nport["dataset"] == "nport" and nport["top"] == 50
    ev = build_body("edgar", ["TSLA"], "2025-01-01", "2026-01-01", dataset="events", items=["5.02"])
    assert ev["items"] == ["5.02"]
    npx = build_body("edgar", ["VTSAX"], "2025-01-01", "2026-01-01", dataset="npx", issuer="Tesla")
    assert npx["issuer"] == "Tesla"
    assert build_body("econcalendar", ["bls"], "2025-01-01", "2025-12-31")["credentials"] == {"user_agent": "Acme ops@acme.test"}


def test_body_firstrate(monkeypatch):
    with pytest.raises(AuraStreamError, match="FIRSTRATE_USER_ID"):
        build_body("firstrate", ["CL"], "2025-01-01", "2025-02-01")
    monkeypatch.setenv("FIRSTRATE_USER_ID", "fr-user")
    body = build_body("firstrate", ["CL"], "2025-01-01", "2025-02-01", "1h", instrument="futures", continuous_contracts=False)
    assert body["credentials"] == {"key": "fr-user"}
    assert body["instrument"] == "futures" and body["continuous_contracts"] is False


def test_missing_credentials_names_env_vars():
    with pytest.raises(AuraStreamError, match="POLYGON_API_KEY"):
        credentials_from_env("polygon")
    with pytest.raises(AuraStreamError, match="OANDA_ACCOUNT_ID, OANDA_TOKEN"):
        build_body("oanda", ["EUR_USD"], "2025-01-01", "2025-01-10")
    with pytest.raises(AuraStreamError, match="EDGAR_USER_AGENT"):
        build_body("edgar", ["AAPL"], "2024-01-01", "2025-01-01")


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"source": "nope"}, "Unknown source"),
        ({"start": "01/01/2025"}, "start must be"),
        ({"interval": "2d"}, "does not support interval"),
        ({"interval": "1s"}, "does not support interval"),
        ({"start": "2025-01-01 10:00"}, "start must be"),
        ({"source": "binance", "tickers": ["BTCUSDT"], "instrument": "perpetual"}, "instrument for 'binance' must be"),
        ({"source": "binance", "tickers": ["BTCUSDT"], "dtype": "funding", "instrument": "spot"}, "needs instrument 'futures'"),
        ({"source": "binance", "tickers": ["BTCUSDT"], "interval": "1m"}, "does not support interval"),
        ({"source": "binance", "tickers": ["BTCUSDT"], "dtype": "orderbook"}, "supports dtype"),
        ({"source": "binance", "tickers": ["BTCUSDT"], "indicator": "cpi"}, "does not apply"),
        ({"source": "fred", "tickers": ["UK"], "indicator": "cpi", "as_of": "release"}, "as_of for 'fred' must be"),
        ({"source": "firstrate", "tickers": ["CL"], "instrument": "spot"}, "instrument for 'firstrate' must be"),
        ({"source": "binance", "tickers": ["BTCUSDT"], "instrument": "stock"}, "instrument for 'binance' must be"),
        ({"dtype": "trades"}, "supports dtype"),
        ({"price_type": "M"}, "does not apply"),
        ({"tickers": [" "]}, "at least one ticker"),
        # Lower-case m is months; yahoo has no 15-month interval.
        ({"interval": "15m"}, "does not support interval"),
        ({"source": "hmrc", "tickers": ["gold"], "interval": "1d"}, "does not support interval"),
        ({"source": "cftc", "tickers": ["GC"], "interval": "1w", "options": "yes"}, "must be true or false"),
        ({"source": "cftc", "tickers": ["GC"], "interval": "1w", "report": "weekly"}, "report for 'cftc' must be"),
        ({"source": "edgar", "tickers": ["AAPL"], "dataset": "short_volume"}, "dataset for 'edgar' must be"),
        ({"source": "comext", "tickers": ["gold"], "interval": "1m", "reporter": " "}, "non-empty string"),
        ({"source": "yahoo", "dataset": "themes"}, "does not apply"),
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
    assert sent["credentials"] == {"key": "poly-secret"}
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
    assert json.loads(route.calls.last.request.content)["credentials"] == {"key": "env-fred"}


@respx.mock
async def test_get_timeseries_new_source_params_and_fields():
    payload = {"GC": {"2025-01-03 20:30:00": {"report_date": "2024-12-31", "mm_long": 250000, "mm_short": 40000}}}
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=payload))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_timeseries",
            {"source": "cftc", "tickers": ["GC"], "start": "2024-12-01", "end": "2025-01-10",
             "interval": "1w", "report": "disaggregated", "options": False},
        )
    assert not result.is_error
    sent = json.loads(route.calls.last.request.content)
    assert sent["report"] == "disaggregated" and sent["options"] is False and "dataset" not in sent
    assert "timestamp,report_date,mm_long,mm_short" in text_of(result)


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
    assert "- hktrade (Hong Kong Trade)" in text
    edgar = text.split("- edgar")[1].split("\n- ")[0]
    assert "param dataset:" in edgar and "13f" in edgar and "nport" in edgar and "param top:" in edgar and "param issuer:" in edgar
    assert "- finra" not in text


def test_latest_window():
    now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
    assert latest_window("yahoo", "1d", 10, now) == ("2025-05-26", "2025-06-16")
    assert latest_window("binance", "1h", 10, now) == ("2025-06-13", "2025-06-16")
    assert latest_window("oanda", "1m", 3, now)[0] == "2025-01-21"
    assert latest_window("binance", "15M", 96, now) == ("2025-06-12", "2025-06-16")


@respx.mock
async def test_get_latest_returns_requested_bars():
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json={"SPY": bars(15)}))
    async with Client(mcp) as client:
        result = await client.call_tool("get_latest", {"source": "yahoo", "tickers": ["SPY"], "bars": 3})
    sent = json.loads(route.calls.last.request.content)
    assert sent["interval"] == "1d" and sent["source"] == "yahoo"
    data_lines = [line for line in text_of(result).splitlines() if line.startswith("2025-01-")]
    assert len(data_lines) == 3


# --- sources added in 0.1.2 -------------------------------------------------

def test_all_api_sources_are_exposed():
    assert len(SOURCES) == 39
    for name in ("deribit", "kucoin", "dydx", "hyperliquid", "lyra", "sdmx", "openmeteo"):
        assert not SOURCES[name].needs_credentials


def test_body_nemeton_nasdaq_sdmx(monkeypatch):
    with pytest.raises(AuraStreamError, match="NEMETON_API_KEY"):
        build_body("nemeton", ["BTCUSDT_BINANCE_PERPETUAL"], "2025-01-01", "2025-02-01", metric="spread")
    monkeypatch.setenv("NEMETON_API_KEY", "nem")
    body = build_body("nemeton", ["BTCUSDT_BINANCE_PERPETUAL"], "2025-01-01", "2025-02-01", "1h",
                      metric="cumulative-depth", level=1)
    assert body["credentials"] == {"key": "nem"} and body["metric"] == "cumulative-depth" and body["level"] == 1
    with pytest.raises(AuraStreamError, match="whole number"):
        build_body("nemeton", ["X"], "2025-01-01", "2025-02-01", metric="spread", level="1")
    with pytest.raises(AuraStreamError, match="must be one of"):
        build_body("nemeton", ["X"], "2025-01-01", "2025-02-01", metric="nope")

    monkeypatch.setenv("NASDAQ_DATA_LINK_API_KEY", "ndl")
    filters = {"QDL/FON": {"contract_code": "967654"}}
    body = build_body("nasdaq", ["QDL/FON"], "2020-01-01", "2025-01-01", filters=filters, assetcol="contract_code")
    assert body["filters"] == filters and body["credentials"] == {"key": "ndl"}
    with pytest.raises(AuraStreamError, match="JSON object"):
        build_body("nasdaq", ["QDL/FON"], "2020-01-01", "2025-01-01", filters="contract_code=1")

    assert build_body("sdmx", ["BIS,WS_CBPOL,1.0/D.US"], "2020-01-01", "2025-01-01", db="BIS")["db"] == "BIS"


def test_body_cityindex_price_type_and_coingecko_either_key(monkeypatch):
    for var, value in (("CITYINDEX_USERNAME", "u"), ("CITYINDEX_PASSWORD", "p"), ("CITYINDEX_APP_KEY", "k")):
        monkeypatch.setenv(var, value)
    body = build_body("cityindex", ["401204130"], "2025-01-01", "2025-01-02", "1h", price_type="BID", bartype="tick")
    assert body["dtype"] == "BID" and body["bartype"] == "tick" and "price_type" not in body
    assert body["credentials"] == {"username": "u", "password": "p", "key": "k"}

    with pytest.raises(AuraStreamError, match="COINGECKO_API_KEY or COINGECKO_DEMO_API_KEY"):
        build_body("coingecko", ["bitcoin"], "2025-01-01", "2025-02-01")
    monkeypatch.setenv("COINGECKO_DEMO_API_KEY", "demo")
    assert build_body("coingecko", ["bitcoin"], "2025-01-01", "2025-02-01")["credentials"] == {"demo_key": "demo"}


def test_body_weather_locations(monkeypatch):
    body = build_body("openmeteo", ["48.85,2.35", " 40.71, -74.01 "], "2025-01-01", "2025-01-10", "1h",
                      variables=["temperature_2m"])
    assert body["tickerlist"] == [{"latitude": 48.85, "longitude": 2.35}, {"latitude": 40.71, "longitude": -74.01}]
    assert "credentials" not in body
    monkeypatch.setenv("OPENMETEO_API_KEY", "om")
    assert build_body("openmeteo", ["48.85,2.35"], "2025-01-01", "2025-01-10", "1d")["credentials"] == {"key": "om"}
    with pytest.raises(AuraStreamError, match="latitude,longitude"):
        build_body("openmeteo", ["Paris"], "2025-01-01", "2025-01-10", "1d")
    with pytest.raises(AuraStreamError, match="VISUALCROSSING_API_KEY"):
        build_body("visualcrossing", ["47.37,8.54"], "2025-01-01", "2025-01-10", "1d")


def test_list_sources_shows_optional_and_either_credentials():
    text = list_sources_text(env={})
    assert "not required (optional: OPENMETEO_API_KEY)" in text
    assert "NOT configured (set COINGECKO_API_KEY or COINGECKO_DEMO_API_KEY)" in text


@respx.mock
async def test_get_timeseries_weather_matches_location_keys():
    payload = {"48.85_2.35": {"2025-01-01 00:00:00": {"temperature_2m": 3.1}}}
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=payload))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_timeseries",
            {"source": "openmeteo", "tickers": ["48.85,2.35"], "start": "2025-01-01", "end": "2025-01-02",
             "interval": "1h", "variables": ["temperature_2m"], "timezone": "Europe/Paris"},
        )
    assert not result.is_error
    sent = json.loads(route.calls.last.request.content)
    assert sent["tickerlist"] == [{"latitude": 48.85, "longitude": 2.35}] and sent["timezone"] == "Europe/Paris"
    out = text_of(result)
    assert "timestamp,temperature_2m" in out and "Not in response" not in out
