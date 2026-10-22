"""Static description of the AuraStream sources this server exposes.

Derived from the AuraStream docs (aurastream-docs/docs/data-sources/*.md and
getting-started/credentials.md). Credentials are never taken from tool
arguments; each source lists the environment variables they are read from
and the field in the request's ``credentials`` object they map to.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CredentialField:
    field: str  # name inside the request's "credentials" object
    env: str  # environment variable it is read from
    default: str | None = None  # used when the env var is unset


@dataclass(frozen=True)
class Source:
    name: str
    label: str
    asset_classes: str
    ticker_format: str
    dtypes: tuple[str, ...] = ("bars",)
    credentials: tuple[CredentialField, ...] = ()
    # Optional source-specific request parameters, name -> description.
    params: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    @property
    def needs_credentials(self) -> bool:
        return bool(self.credentials)


SOURCES: dict[str, Source] = {
    s.name: s
    for s in [
        Source(
            name="binance",
            label="Binance",
            asset_classes="Crypto spot and futures",
            ticker_format="Binance pair symbols, e.g. BTCUSDT, ETHUSDT, SOLUSDT",
            params={"instrument": '"spot" (default) or "futures"'},
            notes="Second-level intervals (1s, 5s, 30s) are supported.",
        ),
        Source(
            name="bybit",
            label="Bybit",
            asset_classes="Crypto perpetuals",
            ticker_format="Bybit symbols, e.g. BTCUSDT, ETHUSDT, SOLUSDT",
            params={"category": '"linear" (default) or "inverse"'},
            notes="Second-level intervals (1s, 5s, 30s) are supported.",
        ),
        Source(
            name="kraken",
            label="Kraken",
            asset_classes="Crypto futures",
            ticker_format="Kraken futures symbols, e.g. PF_XBTUSD, PF_ETHUSD, PI_ETHUSD",
        ),
        Source(
            name="yahoo",
            label="Yahoo Finance",
            asset_classes="Equities, ETFs, indices, commodities, FX",
            ticker_format="Yahoo symbols, e.g. AAPL, SPY, ^GSPC (S&P 500), GC=F (gold futures), EURUSD=X",
        ),
        Source(
            name="firstrate",
            label="FirstRate",
            asset_classes="Stocks, ETFs, futures, FX, options, crypto",
            ticker_format="Stocks/ETFs: AAPL, SPY. Futures: root symbol ES, NQ, ZW. FX: EURUSD. Options: SPX. Crypto: BTC",
            credentials=(CredentialField("key", "FIRSTRATE_API_KEY"),),
            params={
                "instrument": '"stock" (default), "etf", "futures", "fx", "options" or "crypto"',
                "continuous_contracts": "futures only: true (default) for continuous contracts",
            },
        ),
        Source(
            name="oanda",
            label="Oanda",
            asset_classes="Forex, metals, CFDs, order book and position book",
            ticker_format="Oanda instruments with underscore, e.g. EUR_USD, GBP_USD, XAU_USD, USD_JPY",
            dtypes=("bars", "orderbook", "positionbook"),
            credentials=(
                CredentialField("id", "OANDA_ACCOUNT_ID"),
                CredentialField("token", "OANDA_TOKEN"),
                CredentialField("environment", "OANDA_ENVIRONMENT", default="practice"),
            ),
            params={"price_type": 'bars only: "M" mid (default), "A" ask, "B" bid'},
            notes='OANDA_ENVIRONMENT is "practice" (default) or "live".',
        ),
        Source(
            name="alpaca",
            label="Alpaca",
            asset_classes="US equities and crypto (bars, trades, quotes)",
            ticker_format="Equities: AAPL, TSLA. Crypto: slash pairs, e.g. BTC/USD, ETH/USD",
            dtypes=("bars", "trades", "quotes"),
            credentials=(
                CredentialField("key", "ALPACA_API_KEY"),
                CredentialField("secret", "ALPACA_SECRET_KEY"),
            ),
            params={"assetclass": '"equity" (default) or "crypto"'},
            notes="Trades and quotes are tick-level; keep the date range short (a day or less).",
        ),
        Source(
            name="polygon",
            label="Polygon",
            asset_classes="US equities, options, forex, crypto",
            ticker_format="US symbols, e.g. AAPL, MSFT, GOOGL",
            credentials=(CredentialField("key", "POLYGON_API_KEY"),),
        ),
        Source(
            name="fred",
            label="FRED",
            asset_classes="US macroeconomic indicators",
            ticker_format="FRED series IDs, e.g. UNRATE, DFF, GDP, USACPIALLMINMEI",
            credentials=(CredentialField("key", "FRED_API_KEY"),),
            notes='Series come at their native frequency (monthly, quarterly...); use interval "1d".',
        ),
        Source(
            name="finnhub",
            label="Finnhub",
            asset_classes="Company financials and market data",
            ticker_format="US symbols, e.g. AAPL, MSFT",
            credentials=(CredentialField("key", "FINNHUB_API_KEY"),),
        ),
    ]
}

# From the API reference. Support varies by source; the API rejects
# intervals a source does not offer.
INTERVALS = ("1s", "5s", "30s", "1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M")
