**English** | [한국어](README.md)

> [!CAUTION]
> **This software is not financial advice.**
> All financial losses arising from the use of this program are solely the responsibility of the user.
> Cryptocurrency trading carries the risk of losing your principal, and past returns do not guarantee future results.
> Only invest what you can afford to lose.

# Snowball - OKX Adaptive Grid Trading Agent

An adaptive grid trading bot for the OKX exchange. It analyzes market volatility in real-time, automatically adjusts grid spacing, and uses a **multi-agent AI consensus system** (4 specialists + coordinator) for risk decisions.

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

When the score falls in an ambiguous range (55~80), the **multi-agent consensus system** makes the decision.

### Multi-Agent Consensus System

Instead of a single LLM, 4 specialist agents analyze independently, then a coordinator derives consensus:

```
┌─────────────────────────────────────────┐
│        Market Data (shared context)      │
└─────┬────────┬────────┬────────┬────────┘
      │        │        │        │
 ┌────▼───┐┌───▼────┐┌──▼────┐┌──▼──────┐
 │Technical││Sentiment││ Risk  ││  Macro  │
 │ Analyst ││ Analyst ││Manager││Strategist│
 └────┬───┘└───┬────┘└──┬────┘└──┬──────┘
      │        │     (2x)│       │
      └────────┴────────┴───────┘
                    │
              ┌─────▼─────┐
              │ Coordinator│
              └─────┬─────┘
                    │
              Final Action
```

| Agent | Role | Focus |
|-------|------|-------|
| Technical Analyst | Chart analysis | EMA cross, ATR, BB, RSI patterns |
| Sentiment Analyst | Market psychology | Volume patterns, panic/FOMO detection |
| Risk Manager | Capital preservation | Worst-case scenarios **(2x vote weight)** |
| Macro Strategist | Big picture | Trend direction, market cycle, ADX |
| Coordinator | Consensus | Majority vote + defense bias + confidence weighting |

**Consensus rules:**
- 3/4+ agreement → adopted
- Risk Manager STOP/PAUSE → 2x vote weight
- Split opinions → most defensive action wins
- Average confidence ≤ 3 → MAINTAIN (low conviction)
- Set `MULTI_AGENT_MODE=false` to fall back to single LLM mode

### Trend Detection & Auto-Response

EMA 9/21 crossover + ADX for trend detection and automatic response:

| Condition | Action | Description |
|-----------|--------|-------------|
| Bearish + ADX≥50 | PAUSE | Strong downtrend, pause new orders |
| Bearish + ADX≥30 | REDUCE | Cancel buy orders only (downside defense) |
| Bullish + near grid top | SHIFT_UP | Shift grid upward |
| Bearish + near grid bottom | SHIFT_DOWN | Shift grid downward |

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
| `LLM_PROVIDER` | `anthropic` | LLM provider (`anthropic` / `openai` / `grok` / `gemini`) |
| `LLM_MODEL` | auto | Model name (auto-selected per provider) |
| `LLM_TRIGGER_SCORE` | `55` | Minimum score to trigger LLM judgment |
| `MULTI_AGENT_MODE` | `true` | Multi-agent consensus mode (`true` / `false`) |

## File Structure

```
src/
├── main_agent.py        # Entry point, state machine, Telegram alerts
├── multi_agent.py       # Multi-agent consensus (4 specialists + coordinator)
├── cost_guard.py        # Cost-aware system (circuit breaker, cache, budget)
├── market_analyzer.py   # ATR/RSI/BB/Volume/EMA/ADX → risk score + trend detection
├── grid_controller.py   # OKX Grid Bot API (start/widen/shift/pause/liquidate)
├── menu.py              # Arrow-key interactive menu (questionary)
├── config.py            # .env loader + defaults
└── requirements.txt     # Dependencies
```

## Supported LLM Providers

| Provider | Models | API |
|----------|--------|-----|
| **Anthropic (Claude)** | Sonnet 4, Opus 4, Haiku 4 | `anthropic` SDK |
| **OpenAI (GPT)** | GPT-4o, GPT-4o Mini, GPT-4.1 | `openai` SDK |
| **xAI (Grok)** | Grok 3 Mini, Grok 3 | `openai` SDK (compatible) |
| **Google (Gemini)** | 2.5 Flash, 2.5 Pro, 2.0 Flash | `google-genai` SDK |

## Estimated LLM API Cost

Based on 2-minute loop interval (21,600 loops/month). Only called when risk score falls in the 55~80 range.
Multi-agent mode uses **5 calls per judgment** (4 agents + 1 coordinator).

**Multi-Agent Mode (default)**

| Model | Stable (~5%) | Normal (~15%) | Volatile (~30%) |
|-------|:----------:|:------------:|:--------------:|
| Claude Haiku 4 | $0.88 | $2.64 | $5.28 |
| GPT-4o Mini | $0.49 | $1.46 | $2.92 |
| Grok 3 Mini | $0.49 | $1.46 | $2.92 |
| Gemini 2.5 Flash | $0.41 | $1.22 | $2.43 |
| GPT-4o | $8.10 | $24.30 | $48.60 |
| **Claude Sonnet 4** (default) | **$10.53** | **$31.59** | **$63.18** |

**Single LLM Mode** (`MULTI_AGENT_MODE=false`)

| Model | Stable (~5%) | Normal (~15%) | Volatile (~30%) |
|-------|:----------:|:------------:|:--------------:|
| Claude Haiku 4 | $0.18 | $0.53 | $1.06 |
| GPT-4o Mini | $0.10 | $0.29 | $0.58 |
| Grok 3 Mini | $0.10 | $0.29 | $0.58 |
| Gemini 2.5 Flash | $0.08 | $0.24 | $0.49 |
| GPT-4o | $1.62 | $4.86 | $9.72 |
| **Claude Sonnet 4** (default) | **$2.11** | **$6.32** | **$12.64** |

> Multi-agent + budget: **Haiku 4** / **GPT-4o Mini** / **Grok 3 Mini** / **Gemini 2.5 Flash** (under $3/mo).
> Multi-agent + quality: **Sonnet 4** (~$32/mo normal market).
> Cost saving: set `MULTI_AGENT_MODE=false` for single LLM mode.

### CostGuard System

Cost optimization system inspired by [Claude Code's architecture](https://blog.aldente0630.com/insights/claude-code-architecture-analysis/):

**Error Recovery Cascade** — always try free options first:

```
On LLM failure:
  Level 0 (free)  → cache reuse / repeat last action
  Level 1 (free)  → rule-based fallback from risk score
  Level 2 (cheap) → single LLM call instead of multi-agent
  Level 3 (full)  → full multi-agent retry
```

**Circuit Breaker** — block LLM calls for 5 min after 5 consecutive failures:

```
CLOSED ──5 failures──→ OPEN ──5min cooldown──→ HALF_OPEN ──success──→ CLOSED
```

**Diminishing Returns** — skip API calls when 3 consecutive identical decisions:

```
MAINTAIN → MAINTAIN → MAINTAIN → Skip! ($0 cost)
Market change detected (score Δ > 5pts) → auto-reset → call again
```

**Response Cache** — prevent duplicate calls for same market conditions:

```
Market state hash (5-point quantized score + state + trend)
→ Same condition within 5min TTL → cached result (0 API calls)
```

**Daily Budget Limit** — auto rule-based fallback when daily $5 limit exceeded.

## Caution

- Test thoroughly with `DEMO_MODE = True` before switching to live trading
- API keys are stored in `.env` and managed via `.gitignore`
- Auto-liquidation triggers at `MAX_LOSS_PERCENT` (default 15%)

