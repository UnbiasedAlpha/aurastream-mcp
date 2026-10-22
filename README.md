# aurastream-mcp

An [MCP](https://modelcontextprotocol.io) server for [AuraStream](https://aurastream.unbiased-alpha.com), the unified historical market data API. It lets Claude Desktop, Claude Code, ChatGPT and other MCP clients pull OHLCV bars (and, for some sources, trades, quotes and order/position books) from crypto exchanges, Yahoo Finance, Oanda, Alpaca, Polygon, FRED and others, through one tool.

Ask "plot BTC daily closes for Q1 from Binance" or "compare SPY and QQQ drawdowns this year" and the model calls AuraStream for the data.

## Tools

| Tool | What it does |
|---|---|
| `list_sources()` | Sources, asset classes, ticker formats, supported dtypes and source-specific parameters, and whether provider credentials are needed and configured. No API call. |
| `get_timeseries(source, tickers, start, end, interval="1d", dtype="bars", price_type=None, instrument=None, assetclass=None, category=None, continuous_contracts=None, max_rows=100)` | Calls `POST /timeseries`. Returns one CSV block per ticker (`timestamp,Open,High,Low,Close,Volume` for bars), capped at the most recent `max_rows` rows (max 1000) with the total row count noted. |
| `get_latest(source, tickers, interval="1d", bars=10)` | The most recent N bars per ticker, without picking dates. |

Each `get_timeseries` / `get_latest` call is one AuraStream API call.

Sources exposed: `binance`, `bybit`, `kraken`, `yahoo` (no provider credentials) and `firstrate`, `oanda`, `alpaca`, `polygon`, `fred`, `finnhub` (your own provider credentials).

## Install

Requires Python 3.10+.

```bash
# from a local checkout, for now
pip install /path/to/aurastream-mcp
```

A PyPI release (`pip install aurastream-mcp` / `uvx aurastream-mcp`) is planned; the snippets below will work unchanged once it exists.

This installs an `aurastream-mcp` command. Check it with `aurastream-mcp --help`.

## Configuration

All secrets come from environment variables. Provider credentials are **never** tool arguments, so they never pass through the chat or the model.

| Variable | Needed for |
|---|---|
| `AURASTREAM_API_KEY` | Everything. Get a free key from the [dashboard](https://aurastream.unbiased-alpha.com). |
| `OANDA_ACCOUNT_ID`, `OANDA_TOKEN` | `oanda` |
| `OANDA_ENVIRONMENT` | `oanda`, optional: `practice` (default) or `live` |
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | `alpaca` |
| `POLYGON_API_KEY` | `polygon` |
| `FRED_API_KEY` | `fred` |
| `FINNHUB_API_KEY` | `finnhub` |
| `FIRSTRATE_API_KEY` | `firstrate` |

Set only the ones you use. `list_sources` tells the model which sources are configured.

### Claude Desktop

Edit `claude_desktop_config.json` (Settings > Developer > Edit Config):

```json
{
  "mcpServers": {
    "aurastream": {
      "command": "aurastream-mcp",
      "env": {
        "AURASTREAM_API_KEY": "your-aurastream-key",
        "OANDA_ACCOUNT_ID": "101-004-12345678-001",
        "OANDA_TOKEN": "your-oanda-token"
      }
    }
  }
}
```

If Claude Desktop can't find the command, use the full path (e.g. `C:\\Users\\you\\venv\\Scripts\\aurastream-mcp.exe` or `/Users/you/venv/bin/aurastream-mcp`). Restart Claude Desktop afterwards.

### Claude Code

```bash
claude mcp add aurastream -e AURASTREAM_API_KEY=your-aurastream-key -- aurastream-mcp
```

Add `-e FRED_API_KEY=...` etc. for credentialed sources, and `--scope user` to make it available in every project.

### ChatGPT (remote connector)

ChatGPT only talks to remote MCP servers over HTTPS, so run the server in HTTP mode somewhere reachable and register the URL as a connector (Settings > Connectors, developer mode).

```bash
AURASTREAM_API_KEY=your-key aurastream-mcp --http --host 127.0.0.1 --port 8000
# endpoint: http://127.0.0.1:8000/mcp
```

Put it behind something that gives you a public HTTPS URL (a reverse proxy with TLS, or a tunnel such as Cloudflare Tunnel or ngrok), then tell the server which public host name to accept:

```bash
aurastream-mcp --http --port 8000 --allowed-host mcp.example.com
```

and add `https://mcp.example.com/mcp` in ChatGPT. Requests with any other `Host` header are rejected (DNS-rebinding protection).

**How the API key works in HTTP mode.** If the client sends `x-api-key: <key>` or `Authorization: Bearer <key>`, that key is used. Otherwise the server falls back to `AURASTREAM_API_KEY`. The fallback is what makes ChatGPT work today, but it also means **anyone who knows the URL spends your allowance and uses your provider credentials**. Treat the HTTP mode as single-user self-hosting: keep the URL private, or put it behind your own auth. For clients that can send headers, start with `--require-client-key` to disable the fallback. Provider credentials always come from the server's environment.

### Other options

```
aurastream-mcp [--http] [--host HOST] [--port PORT] [--path /mcp]
               [--allowed-host HOST ...] [--require-client-key]
```

`AURASTREAM_API_URL` overrides the endpoint (useful for testing).

## Example prompts

- "What sources does AuraStream have for forex?"
- "Get daily BTCUSDT and ETHUSDT from Binance for 2025 and compute their 30-day rolling correlation."
- "Show the last 20 hourly bars of EUR_USD from Oanda."
- "Pull UNRATE and DFF from FRED since 2015 and describe how they moved together."
- "Fetch SPY and QQQ daily from Yahoo for the last 6 months and compare max drawdowns."

## Limits

- Historical data, not a real-time feed. The last bar may still be forming.
- Every data call counts against your monthly AuraStream allowance (free plan: 500 calls/month). When it runs out the tool says so; it resets on the 1st.
- Results are capped per ticker (default 100 rows, max 1000) to keep the model's context manageable. For long, fine-grained histories use the REST API or the Python client directly.
- The API times out after 120 seconds; split very large requests.
- Interval support varies by source (see the [docs](https://unbiasedalpha.github.io/aurastream-docs/data-sources/overview/)).

## Development

```bash
python -m venv .venv
.venv/Scripts/activate        # or: source .venv/bin/activate
pip install -e ".[test]"
pytest
```

The tests mock all HTTP calls; no API key is needed.
