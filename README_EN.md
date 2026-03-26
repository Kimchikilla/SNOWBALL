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

## Run

```bash
cd src
pip install -r requirements.txt
python main_agent.py
```

An interactive arrow-key menu launches on start:

```
╔══════════════════════════════════════════════════╗
║            ❄️  Snowball Agent  ❄️                ║
║         OKX Adaptive Grid Trading Agent          ║
╚══════════════════════════════════════════════════╝

  Status: ❌ Setup required

? Menu (↑↓ navigate, Enter select)
 ❯ 🚀 Start Agent
   ⚙️  Settings
   📋 View Config
   🚪 Exit
```

### Settings Menu

Configure each section individually:

```
? Settings (↑↓ navigate, Enter select)
 ❯ ❌ OKX API
   ❌ Trading
   ❌ LLM
   ⬜ Telegram Alerts
   ⬜ Advanced
   ──────────────
   ← Back
```

- **Arrow keys (↑↓)** to navigate, **Enter** to select
- **API keys** are entered with password masking (`****`)
- **LLM provider/model** etc. are selected via arrow keys
- **Telegram alert states** support multi-select with **Space**

```
? Alert states (↑↓ navigate, Space toggle, Enter confirm)
 ❯ ◉ CAUTION
   ◉ WARNING
   ◉ EMERGENCY
```

Settings are saved to `.env`. Stop with `Ctrl+C`.

### Configurable Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SYMBOL` | `BTC-USDT` | Trading pair |
| `TOTAL_BUDGET` | `1000.0` | Total USDT budget |
| `GRID_BUDGET` | `400.0` | Budget allocated to grid |
| `GRID_LOWER` / `GRID_UPPER` | `90000` / `110000` | Grid lower/upper price |
| `GRID_COUNT` | `20` | Number of grid levels |
| `GRID_MODE` | `arithmetic` | Grid mode (`arithmetic` / `geometric`) |
| `LOOP_INTERVAL_SEC` | `120` | Main loop interval (seconds) |
| `MAX_LOSS_PERCENT` | `15.0` | Stop-loss threshold (% from entry) |
| `LLM_PROVIDER` | `anthropic` | LLM provider (`anthropic` / `openai`) |
| `LLM_MODEL` | auto | Model name (`claude-sonnet-4-20250514` / `gpt-4o`) |
| `LLM_TRIGGER_SCORE` | `55` | Minimum score to trigger LLM judgment |

## File Structure

```
src/
├── main_agent.py        # Entry point, state machine, LLM judgment, Telegram alerts
├── menu.py              # Arrow-key interactive menu (questionary)
├── setup.py             # Setup wizard (legacy)
├── config.py            # .env loader + defaults
├── market_analyzer.py   # ATR/RSI/BB/Volume analysis → risk score
├── grid_controller.py   # OKX Grid Bot API control (start/widen/pause/liquidate)
└── requirements.txt     # Dependencies
```

## Caution

- Test thoroughly with `DEMO_MODE = True` before switching to live trading
- API keys are stored in `.env` and managed via `.gitignore`
- Auto-liquidation triggers at `MAX_LOSS_PERCENT` (default 15%)

## Disclaimer

> **This software is not financial advice.** All financial losses arising from the use of this program are solely the responsibility of the user. Cryptocurrency trading carries the risk of losing your principal, and past returns do not guarantee future results. Only invest what you can afford to lose.
