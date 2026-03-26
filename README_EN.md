**English** | [한국어](README.md)

# Snowball - OKX Adaptive Grid Trading Agent

An adaptive grid trading bot for the OKX exchange. It analyzes market volatility in real-time, automatically adjusts grid spacing, and delegates decisions to Claude AI in risky situations.

## Architecture

![Architecture](okx_adaptive_grid_agent_architecture.svg)

## How It Works

Repeats the following cycle every 2 minutes:

1. **Market Data Collection** - Fetches candle data from OKX API
2. **Risk Score Calculation** (0~100) - Combines ATR, RSI, Bollinger Bands, and Volume
3. **State Decision & Action Execution**
4. **Telegram Alerts** (on state changes)

### State Machine

| Score | State | Action |
|-------|-------|--------|
| 0~30 | NORMAL | Maintain grid |
| 31~60 | CAUTION | Widen grid spacing |
| 61~80 | WARNING | Pause new orders |
| 81~100 | EMERGENCY | Liquidate all |

When the score falls in an ambiguous range (55~80), the decision is delegated to the Claude API.

### Risk Score Breakdown

| Indicator | Max Score | Description |
|-----------|-----------|-------------|
| ATR | 30 | Volatility spike detection |
| RSI | 25 | Overbought/oversold extremes |
| Bollinger Band | 25 | Band width expansion |
| Volume | 20 | Volume spike |

## Installation

```bash
pip install -r files/requirements.txt
```

## Configuration

Edit `files/config.py`:

```python
# Required
OKX_API_KEY    = "your_api_key"
OKX_SECRET_KEY = "your_secret_key"
OKX_PASSPHRASE = "your_passphrase"
LLM_PROVIDER   = "anthropic"       # "anthropic" or "openai"
LLM_API_KEY    = "your_api_key"

# Optional (Telegram alerts)
TELEGRAM_TOKEN   = "your_bot_token"
TELEGRAM_CHAT_ID = "your_chat_id"

# Trading
SYMBOL       = "BTC-USDT"
TOTAL_BUDGET = 1000.0
DEMO_MODE    = True  # True = paper trading, False = live trading
```

### Configurable Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SYMBOL` | `BTC-USDT` | Trading pair |
| `TOTAL_BUDGET` | `1000.0` | Total USDT budget |
| `GRID_BUDGET` | `400.0` | Budget allocated to grid |
| `RESERVE_BUDGET` | `600.0` | Reserve funds |
| `GRID_LOWER` / `GRID_UPPER` | `90000` / `110000` | Grid lower/upper price |
| `GRID_COUNT` | `20` | Number of grid levels |
| `GRID_MODE` | `arithmetic` | Grid mode (`arithmetic` / `geometric`) |
| `LOOP_INTERVAL_SEC` | `120` | Main loop interval (seconds) |
| `CANDLE_INTERVAL` | `1m` | Candle timeframe |
| `CANDLE_LOOKBACK` | `100` | Number of candles for analysis |
| `ATR_PERIOD` | `14` | ATR calculation period |
| `ATR_SPIKE_MULTIPLIER` | `3.0` | ATR anomaly threshold multiplier |
| `RSI_PERIOD` | `14` | RSI calculation period |
| `RSI_OVERBOUGHT` / `RSI_OVERSOLD` | `75` / `25` | RSI overbought/oversold levels |
| `BOLLINGER_PERIOD` | `20` | Bollinger Bands period |
| `BOLLINGER_STD` | `2.0` | Bollinger Bands std dev multiplier |
| `VOLUME_SPIKE_MULTIPLIER` | `5.0` | Volume spike threshold multiplier |
| `MAX_LOSS_PERCENT` | `15.0` | Stop-loss threshold (% from entry) |
| `LLM_PROVIDER` | `anthropic` | LLM provider (`anthropic` / `openai`) |
| `LLM_API_KEY` | - | LLM API key |
| `LLM_MODEL` | auto | Model name (defaults: `claude-sonnet-4-20250514` / `gpt-4o`) |
| `LLM_TRIGGER_SCORE` | `55` | Minimum score to trigger LLM judgment |

## Run

```bash
cd files
python main_agent.py
```

Stop with `Ctrl+C`.

## File Structure

```
files/
├── config.py            # Settings (API keys, trading params, thresholds)
├── main_agent.py        # Main loop, state machine, LLM judgment, Telegram alerts
├── market_analyzer.py   # ATR/RSI/BB/Volume analysis → risk score
├── grid_controller.py   # OKX Grid Bot API control (start/widen/pause/liquidate)
└── requirements.txt     # Dependencies
```

## Caution

- Test thoroughly with `DEMO_MODE = True` before switching to live trading
- Never push `config.py` with real API keys to a public repository
- Auto-liquidation triggers at `MAX_LOSS_PERCENT` (default 15%)
