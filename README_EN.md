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

**Event-driven architecture** — monitors every 5 minutes, but agents are only called when events are detected:

```
Every 5 min (monitoring)
  → Collect price/indicators → Telegram report
  → Event detected? → NO → MAINTAIN (bot untouched)
                     → YES → Call agents → Decision
```

### Agent Trigger Events

The bot monitors continuously but only calls LLM agents when these events occur:

| Event | Condition | Description |
|-------|-----------|-------------|
| Grid boundary near | Price at 80%+ of upper/lower | Grid shift needed? |
| Volatility spike | ATR ≥ 3× average | Grid widening needed? |
| High risk | Risk score 60+ | Overall market assessment |
| Volume explosion | Volume ≥ 5× average | Rapid change response |
| Grid breakout | Price outside range 6hr+ | Reposition decision |

When no events are detected, LLM cost is **$0**.

### Available Actions

| Action | Description | Cost |
|--------|-------------|------|
| **MAINTAIN** | Keep current grid (default) | $0 |
| **WIDEN** | Widen grid range, restart bot | Trading fees |
| **SHIFT_UP** | Move grid upward | Trading fees |
| **SHIFT_DOWN** | Move grid downward | Trading fees |
| **STOP** | Emergency liquidation (extreme only) | Trading fees |

> ~~PAUSE/REDUCE~~ — Removed. OKX grid bots have no pause API, so "pausing" required
> stopping and restarting the bot, incurring fees each time. The bot stays running;
> use STOP only for genuine emergencies.

### Multi-Agent Consensus System

When events trigger, 4 specialist agents analyze independently, then a coordinator derives consensus:

| Agent | Role | Focus |
|-------|------|-------|
| Technical Analyst | Chart analysis | EMA cross, ATR, BB, RSI patterns |
| Sentiment Analyst | Market psychology | Volume patterns, panic/FOMO detection |
| Risk Manager | Capital preservation | Fee efficiency, worst-case scenarios **(2x vote weight)** |
| Macro Strategist | Big picture | Trend direction, market cycle, ADX |
| Coordinator | Consensus | Majority vote + confidence weighting |

**Consensus rules:**
- 3/4+ agreement → adopted
- Risk Manager STOP → 2x vote weight
- Split opinions → MAINTAIN (don't touch the bot by default)
- Average confidence ≤ 3 → MAINTAIN (low conviction)
- Set `MULTI_AGENT_MODE=false` to fall back to single LLM mode

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

On first run, an **initial setup wizard** starts automatically:

1. OKX API setup (Key, Secret, Passphrase, Demo/Live)
2. LLM setup (Provider, API Key, Model)
3. Trading setup (symbol, total budget → AI auto-recommends grid budget/range/count)
4. Telegram alerts (optional, auto-detects Chat ID)

After setup, the main menu launches:

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

### Auto-Sync with Existing Grid Bots

On agent start, if a grid bot is already running on OKX for the configured symbol, the agent **auto-detects and syncs** with it — no duplicate bots. If none exists, a new grid bot is created. If balance is insufficient, the agent shows the cause and solution, then exits cleanly.

### AI Auto Grid Setup

Just enter your total budget — the AI analyzes market data and recommends **budget allocation + grid range/count/mode** all at once. User confirms before applying.

### Telegram Alerts

- **Every tick**: status/score/trend/price/action/PnL summary
- **EMERGENCY**: 4x repeated alerts at 10-second intervals
- **State changes**: immediate notification
- **Daily report**: PnL and fill summary at scheduled time
- **Setup verification**: auto Bot Token validation + Chat ID auto-detection + test message

### Wait Time Visualization

Visual progress bar countdown between ticks:

```
  ⏳ ████████████████░░░░░░░░░░░░░░░░░░░░░░░░  42.5% (next tick in 1:09)
```

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

Based on 2-minute loop (720/day, 21,600/month). Only called when risk score is 55~80.
Includes fee context: **~1,000 input tokens / ~100 output tokens** per call.
Multi-agent mode uses **5 calls per judgment** (4 agents + 1 coordinator).

**Multi-Agent Mode (default)** — Monthly Cost

| Model | Per Judgment | Stable (~5%) | Normal (~15%) | Volatile (~30%) |
|-------|:-----------:|:----------:|:------------:|:--------------:|
| Gemini 2.0 Flash | $0.0005 | $0.54 | $1.62 | $3.24 |
| GPT-4o Mini | $0.0010 | $1.08 | $3.24 | $6.48 |
| Grok 3 Mini | $0.0018 | $1.94 | $5.83 | $11.66 |
| Gemini 2.5 Flash | $0.0025 | $2.61 | $7.83 | $15.66 |
| Claude Haiku 4 | $0.0075 | $8.10 | $24.30 | $48.60 |
| GPT-4.1 | $0.0140 | $15.12 | $45.36 | $90.72 |
| GPT-4o | $0.0175 | $18.90 | $56.70 | $113.40 |
| Gemini 2.5 Pro | $0.0113 | $12.15 | $36.45 | $72.90 |
| **Claude Sonnet 4** | **$0.0225** | **$24.30** | **$72.90** | **$145.80** |
| Grok 3 | $0.0225 | $24.30 | $72.90 | $145.80 |
| Claude Opus 4 | $0.1125 | $121.50 | $364.50 | $729.00 |

**Single LLM Mode** (`MULTI_AGENT_MODE=false`) — Monthly Cost

| Model | Per Call | Stable (~5%) | Normal (~15%) | Volatile (~30%) |
|-------|:-------:|:----------:|:------------:|:--------------:|
| Gemini 2.0 Flash | $0.0001 | $0.11 | $0.32 | $0.65 |
| GPT-4o Mini | $0.0002 | $0.22 | $0.65 | $1.30 |
| Grok 3 Mini | $0.0004 | $0.39 | $1.17 | $2.33 |
| Gemini 2.5 Flash | $0.0005 | $0.52 | $1.56 | $3.13 |
| Claude Haiku 4 | $0.0015 | $1.62 | $4.86 | $9.72 |
| GPT-4.1 | $0.0028 | $3.02 | $9.07 | $18.14 |
| GPT-4o | $0.0035 | $3.78 | $11.34 | $22.68 |
| Gemini 2.5 Pro | $0.0023 | $2.43 | $7.29 | $14.58 |
| **Claude Sonnet 4** | **$0.0045** | **$4.86** | **$14.58** | **$29.16** |
| Grok 3 | $0.0045 | $4.86 | $14.58 | $29.16 |
| Claude Opus 4 | $0.0225 | $24.30 | $72.90 | $145.80 |

> 🏆 **Budget picks** (multi-agent, volatile market):
> - **Gemini 2.0 Flash**: $3.24/mo — cheapest
> - **GPT-4o Mini**: $6.48/mo — best value
> - **Grok 3 Mini**: $11.66/mo
>
> 🧠 **Quality picks**:
> - **Claude Sonnet 4**: $72.90/mo (normal market)
> - **GPT-4.1**: $45.36/mo — balanced cost/quality
>
> 💰 **Ultra savings**: `MULTI_AGENT_MODE=false` + Gemini 2.0 Flash = **$0.65/mo** even in volatile markets

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

