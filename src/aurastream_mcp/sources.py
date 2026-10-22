"""Static description of the AuraStream sources this server exposes.

Derived from the live AuraStream docs (https://aurastream.unbiased-alpha.com/docs/,
data-sources/*.md and getting-started/credentials.md). Credentials are never
taken from tool arguments; each source lists the environment variables they
are read from and the field(s) in the request's ``credentials`` object they
map to.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CredentialField:
    # Names inside the request's "credentials" object. Usually one; see the
    # note on _DOCS_VS_LIBRARY below for why some carry two.
    fields: tuple[str, ...]
    env: str  # environment variable the value is read from
    default: str | None = None  # used when the env var is unset


# The live docs name these fields api_key / secret_key, but the backend
# library (aurastream/interfaces/*_interface.py) reads key / secret. Until one
# side is fixed and we can test live, send the value under both names so the
# request works whichever is right.
def _both(docs_name: str, library_name: str, env: str) -> CredentialField:
    return CredentialField((docs_name, library_name), env)


@dataclass(frozen=True)
class Source:
    name: str
    label: str
    asset_classes: str
    ticker_format: str
    # Intervals from the source's docs page; None = page lists none, fall back
    # to the general list in the API reference.
    intervals: tuple[str, ...] | None = None
    dtypes: tuple[str, ...] = ("bars",)
    credentials: tuple[CredentialField, ...] = ()
    # Optional source-specific request parameters: name -> allowed values / description.
    params: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    @property
    def needs_credentials(self) -> bool:
        return bool(self.credentials)


# General interval list from api-reference/timeseries.md.
GENERAL_INTERVALS = ("1s", "5s", "30s", "1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M")

SOURCES: dict[str, Source] = {
    s.name: s
    for s in [
        Source(
            name="binance",
            label="Binance",
            asset_classes="Crypto spot and perpetual futures, funding rates, order book",
            ticker_format="Concatenated pairs, e.g. BTCUSDT, ETHUSDT, SOLUSDT",
            intervals=("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"),
            dtypes=("bars", "funding", "orderbook"),
            params={"instrument": '"spot" (default) or "perpetual"; dtype "funding" needs "perpetual"'},
        ),
        Source(
            name="bybit",
            label="Bybit",
            asset_classes="Crypto perpetuals",
            ticker_format="Concatenated pairs, e.g. BTCUSDT (linear), BTCUSD (inverse)",
            intervals=("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1M"),
            params={"category": '"linear" (default) or "inverse"'},
        ),
        Source(
            name="kraken",
            label="Kraken",
            asset_classes="Crypto spot and futures",
            ticker_format="Spot: XXBTZUSD, XETHZUSD. Futures: PI_XBTUSD",
            intervals=("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"),
            params={"instrument": '"futures" (default) or "spot"'},
            notes="Max 720 bars per request for spot, 5000 for futures.",
        ),
        Source(
            name="yahoo",
            label="Yahoo Finance",
            asset_classes="Equities, ETFs, indices, commodities, forex; earnings",
            ticker_format="AAPL, SPY, ^GSPC, ^DJI, GC=F, CL=F, EURUSD=X, DX-Y.NYB, BTC-USD",
            intervals=("1m", "2m", "5m", "15m", "30m", "1h", "1d", "5d", "1w", "1M"),
            dtypes=("bars", "earnings"),
            notes="Intraday intervals (below 1d) only cover the most recent 60 days.",
        ),
        Source(
            name="firstrate",
            label="FirstRate",
            asset_classes="Historical futures",
            ticker_format="Futures root symbols, e.g. ES, NQ",
            notes="No docs page yet; ticker format and intervals are not documented.",
        ),
        Source(
            name="oanda",
            label="Oanda",
            asset_classes="Forex, metals, CFDs, order book",
            ticker_format="Underscore pairs, e.g. EUR_USD, GBP_JPY, XAU_USD, XAG_USD",
            intervals=("5s", "10s", "15s", "30s", "1m", "2m", "5m", "10m", "15m", "30m",
                       "1h", "2h", "4h", "8h", "1d", "1w", "1M"),
            dtypes=("bars", "orderbook"),
            credentials=(
                CredentialField(("id",), "OANDA_ACCOUNT_ID"),
                CredentialField(("token",), "OANDA_TOKEN"),
                CredentialField(("environment",), "OANDA_ENVIRONMENT", default="practice"),
            ),
            params={"price_type": '"A" ask (default), "B" bid, "M" mid'},
            notes='OANDA_ENVIRONMENT is "practice" (default) or "live".',
        ),
        Source(
            name="alpaca",
            label="Alpaca",
            asset_classes="US equities and crypto (bars, trades, quotes)",
            ticker_format="Equities: AAPL, SPY. Crypto: BTC/USD, ETH/USD",
            intervals=("1m", "5m", "15m", "30m", "1h", "1d", "1w", "1M"),
            dtypes=("bars", "trades", "quotes"),
            credentials=(
                _both("api_key", "key", "ALPACA_API_KEY"),
                _both("secret_key", "secret", "ALPACA_SECRET_KEY"),
            ),
            params={
                "assetclass": '"equity" (default) or "crypto"',
                "feed": '"iex" (default) or "sip" (SIP needs a paid Alpaca plan)',
            },
            notes="Trades and quotes are tick-level; keep the date range short.",
        ),
        Source(
            name="polygon",
            label="Polygon",
            asset_classes="US equities, options, forex, crypto",
            ticker_format="AAPL, SPY; crypto with X: prefix, e.g. X:BTCUSD",
            intervals=("1s", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1M", "1q", "1y"),
            credentials=(_both("api_key", "key", "POLYGON_API_KEY"),),
            notes="Bars include VWAP and NTrades besides OHLCV.",
        ),
        Source(
            name="fred",
            label="FRED",
            asset_classes="Economic indicators (CPI, GDP, interest rates)",
            ticker_format="FRED series IDs (CPIAUCSL, GDPC1, UNRATE) or 2-letter country codes for CPI (US, GB, DE, JP)",
            credentials=(_both("api_key", "key", "FRED_API_KEY"),),
            params={"frequency": '"quarterly" (default) or "annual"'},
            notes="Data comes at the series' native frequency; interval is accepted but does not resample.",
        ),
        Source(
            name="finnhub",
            label="Finnhub",
            asset_classes="Company basic financials (revenue, earnings, margins, ratios)",
            ticker_format="US symbols, e.g. AAPL, MSFT",
            credentials=(_both("api_key", "key", "FINNHUB_API_KEY"),),
            params={"frequency": '"quarterly" (default) or "annual"'},
        ),
    ]
}


def intervals_for(source: str) -> tuple[str, ...]:
    return SOURCES[source].intervals or GENERAL_INTERVALS


# Every interval any source accepts, in a sensible order (used for the tool schema).
_ORDER = ("1s", "5s", "10s", "15s", "30s", "1m", "2m", "3m", "5m", "10m", "15m", "30m",
          "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "5d", "1w", "1M", "1q", "1y")
_ALL = set(GENERAL_INTERVALS).union(*(s.intervals or () for s in SOURCES.values()))
INTERVALS = tuple(i for i in _ORDER if i in _ALL)

DTYPES = tuple(dict.fromkeys(d for s in SOURCES.values() for d in s.dtypes))
PARAMS = tuple(dict.fromkeys(p for s in SOURCES.values() for p in s.params))
