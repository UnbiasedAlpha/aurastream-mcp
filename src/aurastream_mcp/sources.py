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
    optional: bool = False  # sent when set, never required


@dataclass(frozen=True)
class Param:
    description: str
    # Allowed values; None = free text (or a list, see is_list).
    choices: tuple[str, ...] | None = None
    is_list: bool = False
    is_bool: bool = False
    is_int: bool = False
    is_dict: bool = False  # a JSON object, e.g. nasdaq filters
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
    # True when the optional credential fields are alternatives and one of them is needed.
    credentials_one_of: bool = False
    # "latlon": tickers are locations, written "lat,lon" and sent as {"latitude", "longitude"}.
    ticker_kind: str = "text"
    # Optional source-specific request parameters.
    params: dict[str, Param] = field(default_factory=dict)
    fields: str = ""  # what each row holds, when it is not OHLCV
    notes: str = ""

    @property
    def needs_credentials(self) -> bool:
        return self.credentials_one_of or any(not c.optional for c in self.credentials)


# General interval list from api-reference/timeseries.md.
GENERAL_INTERVALS = ("1s", "5s", "30s", "1M", "5M", "15M", "30M", "1h", "4h", "1d", "1w", "1m")

_TRADE_PRODUCTS = "gold (HS 7108), silver (7106), platinum_group (7110), copper_refined (7403), copper_ore (2603), aluminium (7601), nickel (7502), zinc (7901)"
_MONTHLY_NOTE = "Monthly, stamped at the first day of the month; interval is always 1m."
_NEMETON_METRICS = (
    "cumulative-depth", "depth-ratio", "dollar-value-imbalance", "dynamic-orderflow-imbalance", "ivdepth",
    "liquidity-consumption-rate", "liquidity-imbalance", "liquidity-spread-depth", "maxdepth", "mid",
    "normalized-spread", "orderbook-pressure", "orderbook-slope", "price-movement-potential", "quantity-imbalance",
    "quote-asymmetry", "quote-concentration", "quote-intensity", "quote-skewness", "spread",
    "static-orderflow-imbalance", "trade-imbalance", "weighted-mid",
)

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
            name="deribit",
            label="Deribit",
            asset_classes="Crypto futures, perpetuals and options",
            ticker_format="Deribit instrument names, e.g. BTC-PERPETUAL, ETH-PERPETUAL, BTC-27MAR26 (future), BTC-27MAR26-100000-C (option)",
            intervals=("1M", "3M", "5M", "10M", "15M", "30M", "1h", "2h", "3h", "6h", "12h", "1d"),
            fields="Open, High, Low, Close, Volume, QuoteVolume",
        ),
        Source(
            name="kucoin",
            label="KuCoin",
            asset_classes="Crypto futures contracts and KuCoin indices",
            ticker_format="KuCoin Futures symbols, e.g. XBTUSDTM, ETHUSDTM; indices start with a dot, e.g. .KXBT, .KETH",
            intervals=("1M", "5M", "15M", "30M", "1h", "2h", "4h", "8h", "12h", "1d", "1w"),
            notes="Spot pairs such as BTC-USDT are not supported. KuCoin writes Bitcoin as XBT in futures symbols.",
        ),
        Source(
            name="dydx",
            label="dYdX",
            asset_classes="Crypto perpetuals on dYdX v4",
            ticker_format="Market names BASE-USD, e.g. BTC-USD, ETH-USD, SOL-USD",
            intervals=("1M", "5M", "15M", "30M", "1h", "4h", "1d"),
            fields="open, high, low, close, baseTokenVolume, usdVolume, trades, startingOpenInterest, "
                   "orderbookMidPriceOpen, orderbookMidPriceClose",
            notes="Bars are stamped at their open time (UTC).",
        ),
        Source(
            name="hyperliquid",
            label="Hyperliquid",
            asset_classes="Crypto perpetuals on Hyperliquid",
            ticker_format="Coin names, e.g. BTC, ETH, HYPE",
            intervals=("1M", "3M", "5M", "15M", "30M", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w", "1m"),
            fields="Open, High, Low, Close, Volume, Count (trades)",
            notes="Only the most recent 5,000 bars of each interval are available; use a coarser interval for long ranges.",
        ),
        Source(
            name="lyra",
            label="Derive (Lyra)",
            asset_classes="Crypto options and perpetuals trades on Derive, aggregated into bars",
            ticker_format="The underlying, e.g. BTC, ETH; the result has one series per traded instrument (e.g. BTC-20250131-100000-C)",
            intervals=("1M", "5M", "15M", "30M", "1h", "4h", "1d", "1w"),
            params={"instrument": Param('"option" (default) or "perp"', ("option", "perp"))},
            fields="Open, High, Low, Close (volume-weighted trade prices), Mark_Price_open..close, Index_Price_open..close, Volume",
        ),
        Source(
            name="coinapi",
            label="CoinAPI",
            asset_classes="Crypto spot and derivatives across hundreds of exchanges",
            ticker_format="CoinAPI symbol IDs EXCHANGE_TYPE_BASE_QUOTE, e.g. BITSTAMP_SPOT_BTC_USD, COINBASE_SPOT_ETH_USD, KRAKENFTS_PERP_BTC_USD",
            intervals=("1s", "5s", "10s", "15s", "30s", "1M", "2M", "3M", "5M", "10M", "15M", "30M",
                       "1h", "2h", "3h", "4h", "6h", "8h", "12h", "1d", "1m", "1y"),
            credentials=(CredentialField(("key",), "COINAPI_KEY"),),
            fields="price_open, price_high, price_low, price_close, volume_traded, trades_count",
            notes="Uses your CoinAPI plan's credits. Bars are stamped at their close (UTC).",
        ),
        Source(
            name="coingecko",
            label="CoinGecko",
            asset_classes="Daily crypto price, market cap and volume for thousands of coins",
            ticker_format="CoinGecko coin IDs (last part of the coin's page URL), e.g. bitcoin, ethereum, solana",
            intervals=("1d",),
            credentials=(
                CredentialField(("key",), "COINGECKO_API_KEY", optional=True),
                CredentialField(("demo_key",), "COINGECKO_DEMO_API_KEY", optional=True),
            ),
            credentials_one_of=True,
            params={"vs_currency": Param('quote currency, e.g. "USD" (default), "EUR"')},
            fields="prices, market_caps, total_volumes",
            notes="Needs COINGECKO_API_KEY (Pro) or COINGECKO_DEMO_API_KEY (free Demo key). Demo keys reach only the last "
                  "365 days. Stamped at 00:00 UTC.",
        ),
        Source(
            name="nemeton",
            label="Nemeton",
            asset_classes="Crypto order book signals (depth, imbalance, spread, order flow)",
            ticker_format="SYMBOL_EXCHANGE_INSTRUMENT, e.g. BTCUSDT_BINANCE_PERPETUAL",
            intervals=("1M", "5M", "15M", "30M", "1h", "4h", "1d"),
            credentials=(CredentialField(("key",), "NEMETON_API_KEY"),),
            params={
                "metric": Param("signal to return (required)", _NEMETON_METRICS),
                "level": Param("order book depth the metric is computed over; default 1000", is_int=True),
            },
            notes="Nemeton is a separate subscription from Unbiased Alpha ($250/month); NEMETON_API_KEY is the key it "
                  "gives you. Data from 2023-12-27; at most 10,000 bars per request (ending at end).",
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
            name="firstrate",
            label="FirstRate Data",
            asset_classes="Historical US stocks, ETFs, futures (continuous or by contract), FX, indices, crypto; option chains",
            ticker_format="Stocks/ETFs: AAPL, SPY. Futures: root symbols, e.g. CL, GC, ZW. FX: EURUSD. Index: SPX. Crypto: BTC. Options: the underlying, e.g. SPY",
            intervals=("1M", "5M", "30M", "1h", "1d"),
            credentials=(CredentialField(("key",), "FIRSTRATE_USER_ID"),),
            params={
                "instrument": Param('"stock" (default), "etf", "futures", "fx", "index", "crypto" or "options"',
                                    ("stock", "etf", "futures", "fx", "index", "crypto", "options")),
                "continuous_contracts": Param("futures only: true (default) for continuous series, false for one series per contract",
                                              is_bool=True),
            },
            notes="Needs your own FirstRate Data subscription; FIRSTRATE_USER_ID is the user ID FirstRate gives you for API "
                  "downloads. Whole files are downloaded and trimmed to the dates, so large intraday requests are slow.",
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
        Source(
            name="tradestation",
            label="TradeStation",
            asset_classes="US stocks, ETFs, futures and indices",
            ticker_format="TradeStation symbols, e.g. AAPL, SPY, @ES (continuous E-mini S&P 500)",
            intervals=("1M", "5M", "15M", "30M", "1h", "2h", "4h", "1d"),
            credentials=(
                CredentialField(("key",), "TRADESTATION_CLIENT_ID"),
                CredentialField(("secret",), "TRADESTATION_CLIENT_SECRET"),
                CredentialField(("refresh_token",), "TRADESTATION_REFRESH_TOKEN"),
            ),
            params={
                "sessiontemplate": Param('trading session, e.g. "Default" (default) or "USEQPreAndPost"'),
                "closed_only": Param("leave out the bar still forming; default true", is_bool=True),
            },
            fields="Open, High, Low, Close, TotalVolume, UpVolume, DownVolume, TotalTicks, OpenInterest, ...",
            notes="Your TradeStation API client ID and secret, and a refresh token from TradeStation's OAuth sign-in. "
                  "Bars are stamped at their close (UTC).",
        ),
        Source(
            name="cityindex",
            label="City Index",
            asset_classes="CFDs on indices, FX, commodities and shares (bars and ticks)",
            ticker_format="City Index market IDs as text, e.g. 401204130",
            intervals=("1M", "5M", "15M", "30M", "1h", "2h", "4h", "1d"),
            credentials=(
                CredentialField(("username",), "CITYINDEX_USERNAME"),
                CredentialField(("password",), "CITYINDEX_PASSWORD"),
                CredentialField(("key",), "CITYINDEX_APP_KEY"),
            ),
            params={
                "bartype": Param('"bar" (default) or "tick"', ("bar", "tick")),
                "price_type": Param('"MID" (default), "BID" or "ASK"', ("MID", "BID", "ASK"), api_name="dtype"),
            },
            fields="bars: Open, High, Low, Close. ticks: Price",
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
        Source(
            name="sdmx",
            label="SDMX (BIS, ECB, Eurostat, IMF, OECD...)",
            asset_classes="Central bank and statistics agency series",
            ticker_format="Dataflow/key, e.g. BIS,WS_CBPOL,1.0/D.US (BIS policy rate, US, daily); each ticker should be one series",
            intervals=("1d",),
            params={"db": Param('SDMX provider ID (required): "BIS", "ECB", "ESTAT", "IMF", "OECD", ... (sdmx1 source list)')},
            notes="interval, start and end are ignored: the full history comes at the series' own frequency. Pass 1d.",
        ),
        Source(
            name="nasdaq",
            label="Nasdaq Data Link",
            asset_classes="Nasdaq Data Link (Quandl) tables",
            ticker_format="Table codes PUBLISHER/TABLE, e.g. QDL/FON; the table needs a date column (old dataset codes like LBMA/GOLD do not work)",
            intervals=("1d",),
            credentials=(CredentialField(("key",), "NASDAQ_DATA_LINK_API_KEY"),),
            params={
                "filters": Param('column filters per table, e.g. {"QDL/FON": {"contract_code": "967654"}}', is_dict=True),
                "assetcol": Param('split the table into one series per value of this column, e.g. "contract_code"'),
                "index": Param('date column used as the time index; default "date"'),
            },
            notes="interval, start and end are ignored: the whole table (after filters) is downloaded, so filter. Pass 1d. "
                  "Needs access to the tables you request.",
        ),
        Source(
            name="econcalendar",
            label="US Economic Calendar",
            asset_classes="Scheduled release times of US economic data (jobs, CPI, GDP, retail sales, housing) and FOMC statements",
            ticker_format="Agencies: bls, bea, census, fomc",
            intervals=("1d",),
            credentials=(CredentialField(("user_agent",), "EDGAR_USER_AGENT", optional=True),),
            fields="release, period (BEA, Census; fomc: meeting dates), projections (fomc: true with the Summary of Economic Projections)",
            notes="Times are in UTC. bls needs EDGAR_USER_AGENT (BLS refuses anonymous scripts); the other agencies need nothing. "
                  "Schedules only, no released values. Coverage: bls current and next year, bea upcoming, census from 2025, fomc from 2021.",
        ),
        Source(
            name="tradingeconomics",
            label="Trading Economics",
            asset_classes="Economic indicators by country",
            ticker_format="Country names as Trading Economics writes them, e.g. united states, germany, mexico",
            intervals=("1d",),
            credentials=(CredentialField(("key",), "TRADINGECONOMICS_API_KEY"),),
            params={"indicator": Param('indicator name (required), e.g. "gdp", "inflation rate", "interest rate"')},
            fields="Value, Category, Frequency, HistoricalDataSymbol, LastUpdate, DateTime",
            notes="interval is ignored: values come at the indicator's own frequency. Pass 1d.",
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
            name="edgar",
            label="SEC EDGAR",
            asset_classes="US insider trades (Form 4), reported company financials (XBRL), earnings release filings, "
                          "fails-to-deliver and institutional holdings (13F)",
            ticker_format="US stock symbols (AAPL) or SEC CIK numbers (320193); 13f: the manager's CIK (1067983 = Berkshire); "
                          "ftd: symbol or CUSIP",
            intervals=("1d",),
            credentials=(CredentialField(("user_agent",), "EDGAR_USER_AGENT"),),
            params={
                "dataset": Param('"insider" (default, Form 4 trades), "financials", "earnings" (8-K item 2.02 filings), '
                                 '"ftd" (fails-to-deliver) or "13f" (institutional holdings)',
                                 ("insider", "financials", "earnings", "ftd", "13f")),
                "concepts": Param("financials only: XBRL concepts, e.g. Revenues; default is a standard set", is_list=True),
                "period": Param('financials only: "quarter" (default) or "annual"', ("quarter", "annual")),
            },
            fields="insider: owner, officer_title, buy_shares, buy_value, sell_shares, sell_value, other_shares_net, plan_10b5_1, "
                   "shares_owned_after, ... (one row per filing, stamped when it became public). financials: one row per filing with each concept's value and period. "
                   "earnings: event_date, items, url (one row per earnings 8-K, stamped at SEC acceptance). ftd: fails, price, cusip, symbol. "
                   "13f: report_period, n_positions, total_value, holdings (list of issuer, cusip, value, shares, put_call)",
            notes='EDGAR_USER_AGENT is a name and contact email, e.g. "Your Company contact@yourcompany.com" (SEC rule; no registration). '
                  "ftd is published twice a month about two weeks after each half-month and stamped at the settlement date; "
                  "13f is filed up to 45 days after the quarter end (request a year or less for large managers).",
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
        # --- weather ----------------------------------------------------------------
        Source(
            name="openmeteo",
            label="Open-Meteo",
            asset_classes="Historical weather by location",
            ticker_format='Locations as "latitude,longitude", e.g. "48.85,2.35" (Paris); results are keyed latitude_longitude',
            intervals=("1h", "1d"),
            ticker_kind="latlon",
            credentials=(CredentialField(("key",), "OPENMETEO_API_KEY", optional=True),),
            params={
                "variables": Param("Open-Meteo variables (required): hourly names with 1h (temperature_2m, precipitation, "
                                   "wind_speed_10m), daily with 1d (temperature_2m_max, precipitation_sum)", is_list=True),
                "timezone": Param('IANA time zone for timestamps and daily aggregation, e.g. "Europe/Paris"; default "GMT"'),
            },
            notes="No key needed; OPENMETEO_API_KEY is sent when set.",
        ),
        Source(
            name="visualcrossing",
            label="Visual Crossing",
            asset_classes="Historical weather by location",
            ticker_format='Locations as "latitude,longitude", e.g. "40.71,-74.01" (New York); results are keyed latitude_longitude',
            intervals=("1h", "1d"),
            ticker_kind="latlon",
            credentials=(CredentialField(("key",), "VISUALCROSSING_API_KEY"),),
            params={
                "unit_group": Param('"us" (default: Fahrenheit, mph, inches), "metric", "uk" or "base"', ("us", "metric", "uk", "base")),
                "variables": Param("only these fields, e.g. tempmax, tempmin, precip; default all", is_list=True),
            },
            fields="daily: tempmax, tempmin, temp, feelslike, humidity, precip, snow, windspeed, pressure, cloudcover, conditions, ...",
            notes="1d rows are local calendar days stamped at 00:00; 1h rows are stamped in UTC.",
        ),
        Source(
            name="tsa",
            label="TSA Checkpoint Numbers",
            asset_classes="Daily US airport checkpoint travelers, from 2019 (travel demand)",
            ticker_format="throughput",
            intervals=("1d",),
            fields="travelers",
            notes="Published the next morning (around 9:00 New York). Compare with the same weekday a year earlier.",
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
