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

**Event-driven architecture** — 5-min monitoring (288/day, 8,640/month).
No events = **$0 LLM cost**. ~1,000 input / ~100 output tokens per call.

Event trigger rates: stable ~2%, normal ~8%, volatile ~20%

**Multi-Agent Mode (default)** — Monthly Cost (5 calls/judgment)

| Model | Per Judgment | Stable (~2%) | Normal (~8%) | Volatile (~20%) |
|-------|:-----------:|:----------:|:------------:|:--------------:|
| Gemini 2.0 Flash | $0.0005 | $0.09 | $0.35 | $0.86 |
| GPT-4o Mini | $0.0010 | $0.17 | $0.69 | $1.73 |
| Grok 3 Mini | $0.0018 | $0.31 | $1.24 | $3.11 |
| Gemini 2.5 Flash | $0.0025 | $0.43 | $1.73 | $4.32 |
| Claude Haiku 4 | $0.0075 | $1.30 | $5.18 | $12.96 |
| GPT-4.1 | $0.0140 | $2.42 | $9.68 | $24.19 |
| GPT-4o | $0.0175 | $3.02 | $12.10 | $30.24 |
| Gemini 2.5 Pro | $0.0113 | $1.95 | $7.78 | $19.45 |
| **Claude Sonnet 4** | **$0.0225** | **$3.89** | **$15.55** | **$38.88** |
| Grok 3 | $0.0225 | $3.89 | $15.55 | $38.88 |
| Claude Opus 4 | $0.1125 | $19.44 | $77.76 | $194.40 |

**Single LLM Mode** (`MULTI_AGENT_MODE=false`) — Monthly Cost

| Model | Per Call | Stable (~2%) | Normal (~8%) | Volatile (~20%) |
|-------|:-------:|:----------:|:------------:|:--------------:|
| Gemini 2.0 Flash | $0.0001 | $0.02 | $0.07 | $0.17 |
| GPT-4o Mini | $0.0002 | $0.03 | $0.14 | $0.35 |
| Grok 3 Mini | $0.0004 | $0.06 | $0.25 | $0.62 |
| Gemini 2.5 Flash | $0.0005 | $0.09 | $0.35 | $0.86 |
| Claude Haiku 4 | $0.0015 | $0.26 | $1.04 | $2.59 |
| GPT-4.1 | $0.0028 | $0.48 | $1.94 | $4.84 |
| GPT-4o | $0.0035 | $0.60 | $2.42 | $6.05 |
| Gemini 2.5 Pro | $0.0023 | $0.39 | $1.56 | $3.89 |
| **Claude Sonnet 4** | **$0.0045** | **$0.78** | **$3.11** | **$7.78** |
| Grok 3 | $0.0045 | $0.78 | $3.11 | $7.78 |
| Claude Opus 4 | $0.0225 | $3.89 | $15.55 | $38.88 |

> 🏆 **Budget** (multi-agent, volatile): Gemini 2.0 Flash $0.86/mo, GPT-4o Mini $1.73/mo
> 🧠 **Quality**: Claude Sonnet 4 $15.55/mo (normal), GPT-4.1 $9.68/mo
> 💰 **Ultra savings**: Single + Gemini 2.0 Flash = **$0.17/mo** even in volatile markets
> 📉 **~75% cost reduction** vs previous always-on architecture

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

