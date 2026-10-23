"""Static description of the AuraStream sources this server exposes.

Derived from the AuraStream docs (https://aurastream.unbiased-alpha.com/docs/,
data-sources/*.md and getting-started/credentials.md), checked against the
backend library and the live API. Credentials are never
taken from tool arguments; each source lists the environment variables they
are read from and the field(s) in the request's ``credentials`` object they
map to.

Intervals follow the API's convention: upper-case M is minutes, lower-case m
is months (15M = 15 minutes, 1m = one month); q is quarters, y years.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CredentialField:
    # Names inside the request's "credentials" object.
    fields: tuple[str, ...]
    env: str  # environment variable the value is read from
    default: str | None = None  # used when the env var is unset


@dataclass(frozen=True)
class Param:
    description: str
    # Allowed values; None = free text (or a list, see is_list).
    choices: tuple[str, ...] | None = None
    is_list: bool = False
    is_bool: bool = False
    # Name in the request body when it differs from the tool argument.
    api_name: str | None = None


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
    # Request fields for each dtype, for sources that do not take a plain
    # "dtype" field; None = send "dtype" when the source has more than one.
    dtype_fields: dict[str, dict[str, str]] | None = None
    credentials: tuple[CredentialField, ...] = ()
    # Optional source-specific request parameters.
    params: dict[str, Param] = field(default_factory=dict)
    fields: str = ""  # what each row holds, when it is not OHLCV
    notes: str = ""

    @property
    def needs_credentials(self) -> bool:
        return bool(self.credentials)


# General interval list from api-reference/timeseries.md.
GENERAL_INTERVALS = ("1s", "5s", "30s", "1M", "5M", "15M", "30M", "1h", "4h", "1d", "1w", "1m")

_TRADE_PRODUCTS = "gold (HS 7108), silver (7106), platinum_group (7110), copper_refined (7403), copper_ore (2603), aluminium (7601), nickel (7502), zinc (7901)"
_MONTHLY_NOTE = "Monthly, stamped at the first day of the month; interval is always 1m."

SOURCES: dict[str, Source] = {
    s.name: s
    for s in [
        # --- market data -------------------------------------------------------
        Source(
            name="binance",
            label="Binance",
            asset_classes="Crypto spot and USD-M perpetual futures, funding rates",
            ticker_format="Concatenated pairs, e.g. BTCUSDT, ETHUSDT, SOLUSDT (same symbols for spot and futures)",
            intervals=("1M", "3M", "5M", "15M", "30M", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"),
            dtypes=("bars", "funding"),
            dtype_fields={"bars": {}, "funding": {"bars": "funding", "instrument": "futures"}},
            params={"instrument": Param('"spot" (default) or "futures" (USD-M perpetuals); dtype "funding" is futures only',
                                        ("spot", "futures"))},
            fields="bars: Open, High, Low, Close, Volume, QuoteAssetVolume, NumberTrades, TakerBuyBaseVolume, "
                   "TakerBuyQuoteVolume. funding: fundingRate",
            notes="Bars are stamped at their close time. No monthly bars.",
        ),
        Source(
            name="bybit",
            label="Bybit",
            asset_classes="Crypto perpetuals",
            ticker_format="Concatenated pairs, e.g. BTCUSDT (linear), BTCUSD (inverse)",
            intervals=("1M", "3M", "5M", "15M", "30M", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1m"),
            params={"category": Param('"linear" (default) or "inverse"', ("linear", "inverse"))},
        ),
        Source(
            name="kraken",
            label="Kraken",
            asset_classes="Crypto spot and futures",
            ticker_format="Futures: PF_XBTUSD, PF_ETHUSD, PI_XBTUSD. Spot: XXBTZUSD, XETHZUSD",
            intervals=("1M", "5M", "15M", "30M", "1h", "4h", "12h", "1d", "1w"),
            params={
                "instrument": Param('"futures" (default) or "spot"', ("spot", "futures")),
                "ticktype": Param('futures only: "trade" (default), "mark" or "spot" (index) price', ("trade", "mark", "spot")),
            },
            notes="Spot returns only the latest 720 bars whatever the dates, and has no 12h; use futures for history.",
        ),
        Source(
            name="yahoo",
            label="Yahoo Finance",
            asset_classes="Equities, ETFs, indices, commodities, forex; earnings",
            ticker_format="AAPL, SPY, ^GSPC, ^DJI, GC=F, CL=F, EURUSD=X, DX-Y.NYB, BTC-USD",
            intervals=("1M", "2M", "5M", "15M", "30M", "60M", "90M", "1h", "1d", "5d", "1w", "1m", "3m"),
            dtypes=("bars", "earnings"),
            dtype_fields={"bars": {}, "earnings": {"dtype": "earnings"}},
            fields="earnings: EPS Estimate, Reported EPS, Surprise(%)",
            notes="1-minute bars cover the last 7 days, other intraday intervals the last 60 days.",
        ),
        Source(
            name="oanda",
            label="Oanda",
            asset_classes="Forex, metals, CFDs, order book",
            ticker_format="Underscore pairs, e.g. EUR_USD, GBP_JPY, XAU_USD, XAG_USD",
            intervals=("1M", "2M", "4M", "5M", "10M", "15M", "30M",
                       "1h", "2h", "3h", "4h", "6h", "8h", "12h", "1d", "1w", "1m"),
            dtypes=("bars", "orderbook", "positionbook"),
            credentials=(
                CredentialField(("id",), "OANDA_ACCOUNT_ID"),
                CredentialField(("token",), "OANDA_TOKEN"),
                CredentialField(("environment",), "OANDA_ENVIRONMENT", default="practice"),
            ),
            params={"price_type": Param('"M" mid (default), "A" ask, "B" bid', ("M", "A", "B"))},
            notes="Bars are stamped at their close; only completed bars. Order and position books are snapshots every "
                  "20 minutes (interval ignored), for a subset of instruments. "
                  'OANDA_ENVIRONMENT is "practice" (default) or "live".',
        ),
        Source(
            name="alpaca",
            label="Alpaca",
            asset_classes="US equities and crypto (bars, trades, quotes)",
            ticker_format="Equities: AAPL, SPY. Crypto: BTC/USD, ETH/USD",
            intervals=("1M", "1h", "1d", "1w", "1m"),
            dtypes=("bars", "trades", "quotes"),
            credentials=(
                CredentialField(("key",), "ALPACA_API_KEY"),
                CredentialField(("secret",), "ALPACA_SECRET_KEY"),
            ),
            params={
                "assetclass": Param('"equity" (default) or "crypto"', ("equity", "crypto")),
                "feed": Param('stocks: "iex" (default, IEX trades only) or "sip" (consolidated tape from 2016; '
                              "history works on any account)", ("iex", "sip")),
                "adjustment": Param('stock bars: "raw" (default), "split", "dividend" or "all"',
                                    ("raw", "split", "dividend", "all")),
            },
            fields="bars: open, high, low, close, volume, trade_count, vwap",
            notes="Single units only: the number in the interval is ignored (15M returns 1-minute bars). "
                  "Trades and quotes are tick-level; keep the date range short.",
        ),
        Source(
            name="polygon",
            label="Polygon",
            asset_classes="US equities, options, forex, crypto",
            ticker_format="AAPL, SPY; prefixes X: crypto (X:BTCUSD), C: forex (C:EURUSD), O: options",
            intervals=("1s", "1M", "5M", "15M", "30M", "1h", "1d", "1w", "1m", "1q", "1y"),
            credentials=(CredentialField(("key",), "POLYGON_API_KEY"),),
            notes="Bars are stamped at their open time (UTC) and include VWAP and NTrades besides OHLCV.",
        ),
        # --- macro and fundamentals ---------------------------------------------
        Source(
            name="fred",
            label="FRED",
            asset_classes="Economic indicators (CPI, GDP, interest rates)",
            ticker_format="FRED series IDs (CPIAUCSL, GDPC1, UNRATE, DFF), or 2-letter country codes (US, UK, DE, JP) "
                          "with indicator set",
            credentials=(CredentialField(("key",), "FRED_API_KEY"),),
            params={
                "indicator": Param('country shortcut, tickers are then country codes (UK, not GB): "cpi", "gdp", '
                                   '"3mrate" or "10yrate"', ("cpi", "gdp", "3mrate", "10yrate"), api_name="bars"),
                "as_of": Param('"realtime_start" (default, rows stamped at publication) or "date" (period the value '
                               "describes)", ("realtime_start", "date")),
            },
            notes="Data comes at the series' native frequency; interval is accepted but does not resample.",
        ),
        Source(
            name="finnhub",
            label="Finnhub",
            asset_classes="Company basic financials (revenue, earnings, margins, ratios)",
            ticker_format="US symbols, e.g. AAPL, MSFT",
            intervals=("1q", "1y"),
            credentials=(CredentialField(("key",), "FINNHUB_API_KEY"),),
            fields="one field per Finnhub metric, e.g. eps, netMargin, currentRatio",
            notes='interval "1y" gives annual figures, "1q" quarterly. The whole history is returned regardless of '
                  "start and end.",
        ),
        Source(
            name="worldbank",
            label="World Bank",
            asset_classes="Monthly commodity prices since 1960 (Pink Sheet): metals, energy, agriculture, fertilizers",
            ticker_format="Commodity name or part of it, case-insensitive: Gold, Silver, Copper, Aluminum, Iron ore, 'Crude oil, Brent', 'Natural gas, US', Wheat",
            intervals=("1m",),
            fields="price, units (e.g. $/troy oz), commodity",
            notes=_MONTHLY_NOTE,
        ),
        Source(
            name="eia",
            label="EIA",
            asset_classes="US energy inventories, production and prices",
            ticker_format="EIA series IDs, or crude_stocks, cushing_stocks, gasoline_stocks, distillate_stocks, crude_production, crude_imports, refinery_utilization, natgas_storage (weekly); wti_spot, brent_spot, henry_hub_spot (daily)",
            intervals=("1d", "1w"),
            credentials=(CredentialField(("api_key",), "EIA_API_KEY"),),
            notes="Rows are stamped at the period they describe. Weekly petroleum data is published the next Wednesday, "
                  "gas storage on Thursday: shift before backtesting. Free key at eia.gov/opendata.",
        ),
        Source(
            name="treasury",
            label="US Treasury",
            asset_classes="Government cash (TGA), debt, auctions",
            ticker_format="tga, debt_to_penny, auctions (or auctions:10-Year to filter by term), avg_interest_rates, or any Fiscal Data endpoint path",
            intervals=("1d",),
            notes="TGA is in USD millions; a falling balance adds liquidity. Daily Treasury Statement data is published around 16:00 New York the next business day.",
        ),
        # --- positioning and flows ------------------------------------------------
        Source(
            name="cftc",
            label="CFTC Commitments of Traders",
            asset_classes="Weekly futures positioning by trader type, since 2006",
            ticker_format="Futures symbols, e.g. GC, SI, HG, CL, NG, ZC, ZS, ZN, ZB, SR3, 6E, 6J, DX, BTC, VX, or any CFTC contract market code (088691)",
            intervals=("1w",),
            params={
                "report": Param('"disaggregated" (commodities), "tff" (financials) or "legacy"; default depends on the symbol',
                                ("disaggregated", "tff", "legacy")),
                "options": Param("true to include options (futures-equivalent); default false", is_bool=True),
                "as_of": Param('"release" (default, stamped at Friday publication, no look-ahead) or "report" (Tuesday position date)',
                               ("release", "report")),
            },
        ),
        Source(
            name="finra",
            label="FINRA",
            asset_classes="US stock short interest and daily short-sale volume",
            ticker_format="US stock symbols, e.g. AAPL, TSLA, GME",
            intervals=("1d",),
            params={"dataset": Param('"short_interest" (default, twice monthly) or "short_volume" (daily)',
                                     ("short_interest", "short_volume"))},
            fields="short_interest: short_interest, previous_short_interest, change, change_pct, avg_daily_volume, days_to_cover. "
                   "short_volume: short_volume, short_exempt_volume, total_volume, short_ratio",
            notes="Short interest is published about 7 business days after the settlement date it is stamped at: shift before backtesting.",
        ),
        Source(
            name="edgar",
            label="SEC EDGAR",
            asset_classes="US insider trades (Form 4) and reported company financials (XBRL)",
            ticker_format="US stock symbols (AAPL) or SEC CIK numbers (320193)",
            intervals=("1d",),
            credentials=(CredentialField(("user_agent",), "EDGAR_USER_AGENT"),),
            params={
                "dataset": Param('"insider" (default, Form 4 trades) or "financials"', ("insider", "financials")),
                "concepts": Param("financials only: XBRL concepts, e.g. Revenues; default is a standard set", is_list=True),
                "period": Param('financials only: "quarter" (default) or "annual"', ("quarter", "annual")),
            },
            fields="insider: owner, officer_title, buy_shares, buy_value, sell_shares, sell_value, other_shares_net, plan_10b5_1, "
                   "shares_owned_after, ... (one row per filing, stamped when it became public). financials: one row per filing with each concept's value and period",
            notes='EDGAR_USER_AGENT is a name and contact email, e.g. "Your Company contact@yourcompany.com" (SEC rule; no registration).',
        ),
        Source(
            name="hmrc",
            label="UK Trade (HMRC)",
            asset_classes="UK imports and exports by product and country",
            ticker_format=f"<product> or <product>:<partner>, e.g. gold, gold:CH. product: HS code (2-8 digits) or {_TRADE_PRODUCTS}. partner: ISO 2-letter code; omitted = all",
            intervals=("1m",),
            fields="import_value_gbp, import_mass_kg, export_value_gbp, export_mass_kg",
            notes=_MONTHLY_NOTE + " Published about six weeks after the month ends.",
        ),
        Source(
            name="comext",
            label="EU Trade (Eurostat Comext)",
            asset_classes="EU imports and exports by product and country",
            ticker_format="<product> or <product>:<partner>, e.g. gold:CH, copper_refined:CN. product: HS/CN code or the same names as hmrc. "
                          "partner: ISO 2-letter code, EXT_EU27_2020 (outside the EU) or WORLD",
            intervals=("1m",),
            params={"reporter": Param('"EU27_2020" (default) or a member state, e.g. "DE", "IT"')},
            fields="import_value_eur, import_mass_kg, export_value_eur, export_mass_kg",
            notes=_MONTHLY_NOTE + " Published about six weeks after the month ends.",
        ),
        Source(
            name="uscensus",
            label="US Trade (Census)",
            asset_classes="US imports and exports by product and country",
            ticker_format="<product> or <product>:<partner>, e.g. gold, gold:SWITZERLAND. product: HS code (2, 4, 6 or 10 digits) or the same names as hmrc. "
                          "partner: Census country code (4419) or country name in capitals",
            intervals=("1m",),
            credentials=(CredentialField(("api_key",), "CENSUS_API_KEY"),),
            fields="import_value_usd, export_value_usd; import_quantity, export_quantity for 10-digit codes",
            notes=_MONTHLY_NOTE + " Published about five weeks after the month ends. Free key at api.census.gov.",
        ),
        Source(
            name="hktrade",
            label="Hong Kong Trade",
            asset_classes="Hong Kong gold, silver, platinum, palladium trade by country (gauge of Chinese demand)",
            ticker_format="<metal> or <metal>:<partner>, e.g. gold:CN. metal: gold, gold_unwrought, gold_monetary, silver, platinum, palladium. "
                          "partner: CN, CH, GB, US, AU, ZA, AE, IN, SG, TH, JP, TW, KR, CA, TR, MO, MY, ID, PH, DE; omitted = all",
            intervals=("1m",),
            fields="import_value_hkd, import_mass_kg, export_value_hkd, export_mass_kg",
            notes=_MONTHLY_NOTE + " From 2014; published about four weeks after the month ends.",
        ),
        # --- crypto fundamentals and news ---------------------------------------------
        Source(
            name="defillama",
            label="DefiLlama",
            asset_classes="Stablecoin supply, DeFi TVL, DEX volume, protocol fees",
            ticker_format="stablecoins, tvl, dex_volume, fees (all chains) or with a chain, e.g. tvl:Solana; protocol:aave for one protocol",
            intervals=("1d",),
            notes="Daily, stamped at 00:00 UTC.",
        ),
        Source(
            name="gdelt",
            label="GDELT",
            asset_classes="Worldwide news coverage and tone, from 2017",
            ticker_format='Search queries, e.g. gold price, "federal reserve" inflation. With dataset "themes": GDELT themes, e.g. ECON_GOLDPRICE, ECON_INFLATION, WB_1235_CENTRAL_BANKS, SANCTIONS',
            intervals=("15M", "30M", "1h", "4h", "1d", "1w"),
            params={
                "mode": Param('search queries only: "timelinevol" (default, share of coverage), "timelinevolraw" (article count) or "timelinetone"',
                              ("timelinevol", "timelinevolraw", "timelinetone")),
                "dataset": Param('"themes" to count articles by GDELT theme instead of search query', ("themes",)),
            },
            fields="themes: articles, share, tone",
            notes="Search queries are throttled by GDELT (beta): keep ranges short and queries few; their resolution is set by "
                  "the range length. Themes are not throttled and use the interval (15-minute base).",
        ),
    ]
}


def intervals_for(source: str) -> tuple[str, ...]:
    return SOURCES[source].intervals or GENERAL_INTERVALS


# Every interval any source accepts, in a sensible order (used for the tool schema).
_ORDER = ("1s", "5s", "10s", "15s", "30s", "1M", "2M", "3M", "4M", "5M", "10M", "15M", "30M", "60M", "90M",
          "1h", "2h", "3h", "4h", "6h", "8h", "12h", "1d", "3d", "5d", "1w", "1m", "3m", "1q", "1y")
assert _ORDER and all(set(s.intervals or ()) <= set(_ORDER) for s in SOURCES.values())
_ALL = set(GENERAL_INTERVALS).union(*(s.intervals or () for s in SOURCES.values()))
INTERVALS = tuple(i for i in _ORDER if i in _ALL)

DTYPES = tuple(dict.fromkeys(d for s in SOURCES.values() for d in s.dtypes))
PARAMS = tuple(dict.fromkeys(p for s in SOURCES.values() for p in s.params))
CREDENTIAL_ENV_VARS = tuple(dict.fromkeys(c.env for s in SOURCES.values() for c in s.credentials))
