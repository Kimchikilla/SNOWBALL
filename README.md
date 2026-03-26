[English](README_EN.md) | **한국어**

# Snowball - OKX Adaptive Grid Trading Agent

OKX 거래소에서 동작하는 적응형 그리드 트레이딩 봇이다람쥐. 시장 변동성을 실시간 분석하여 그리드 간격을 자동 조절하고, 위험 상황에서는 Claude AI의 판단을 받아 대응하는 다람쥐.

## 아키텍처

![Architecture](okx_adaptive_grid_agent_architecture.svg)

## 작동 방식

2분마다 아래 사이클을 반복하는 다람쥐:

1. **시장 데이터 수집** - OKX API에서 캔들 데이터 조회
2. **리스크 스코어 산출** (0~100) - ATR, RSI, 볼린저밴드, 거래량 4개 지표 종합
3. **상태 결정 및 액션 실행**
4. **텔레그램 알림** (상태 변화 시)

### 상태 머신

| 스코어 | 상태 | 액션 |
|--------|------|------|
| 0~30 | NORMAL | 그리드 유지 |
| 31~60 | CAUTION | 그리드 간격 확대 |
| 61~80 | WARNING | 신규 주문 중단 |
| 81~100 | EMERGENCY | 전체 청산 |

스코어가 애매한 구간(55~80)에서는 Claude API에 판단을 위임하는 다람쥐.

### 리스크 스코어 구성

| 지표 | 최대 점수 | 설명 |
|------|-----------|------|
| ATR | 30점 | 변동성 급등 감지 |
| RSI | 25점 | 과매수/과매도 극단값 |
| Bollinger Band | 25점 | 밴드 폭 급팽창 |
| Volume | 20점 | 거래량 급등 |

## 설치

```bash
pip install -r files/requirements.txt
```

## 설정

`files/config.py`에서 아래 값을 수정하는 다람쥐:

```python
# 필수
OKX_API_KEY    = "your_api_key"
OKX_SECRET_KEY = "your_secret_key"
OKX_PASSPHRASE = "your_passphrase"
LLM_PROVIDER   = "anthropic"       # "anthropic" 또는 "openai"
LLM_API_KEY    = "your_api_key"

# 선택 (텔레그램 알림)
TELEGRAM_TOKEN   = "your_bot_token"
TELEGRAM_CHAT_ID = "your_chat_id"

# 거래 설정
SYMBOL       = "BTC-USDT"
TOTAL_BUDGET = 1000.0
DEMO_MODE    = True  # True = 모의거래, False = 실거래
```

### 설정 가능한 항목

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `SYMBOL` | `BTC-USDT` | 거래 대상 심볼 |
| `TOTAL_BUDGET` | `1000.0` | USDT 총 투입 예산 |
| `GRID_BUDGET` | `400.0` | 그리드에 사용할 금액 |
| `RESERVE_BUDGET` | `600.0` | 예비 자금 |
| `GRID_LOWER` / `GRID_UPPER` | `90000` / `110000` | 그리드 하단/상단 가격 |
| `GRID_COUNT` | `20` | 그리드 분할 개수 |
| `GRID_MODE` | `arithmetic` | 그리드 모드 (`arithmetic` / `geometric`) |
| `LOOP_INTERVAL_SEC` | `120` | 메인 루프 실행 간격 (초) |
| `CANDLE_INTERVAL` | `1m` | 캔들 기준 |
| `CANDLE_LOOKBACK` | `100` | 분석에 사용할 캔들 개수 |
| `ATR_PERIOD` | `14` | ATR 계산 기간 |
| `ATR_SPIKE_MULTIPLIER` | `3.0` | ATR 이상 판단 배수 |
| `RSI_PERIOD` | `14` | RSI 계산 기간 |
| `RSI_OVERBOUGHT` / `RSI_OVERSOLD` | `75` / `25` | RSI 과매수/과매도 기준 |
| `BOLLINGER_PERIOD` | `20` | 볼린저밴드 기간 |
| `BOLLINGER_STD` | `2.0` | 볼린저밴드 표준편차 배수 |
| `VOLUME_SPIKE_MULTIPLIER` | `5.0` | 거래량 급등 판단 배수 |
| `MAX_LOSS_PERCENT` | `15.0` | 손절 기준 (진입가 대비 %) |
| `LLM_PROVIDER` | `anthropic` | LLM 제공자 (`anthropic` / `openai`) |
| `LLM_API_KEY` | - | LLM API 키 |
| `LLM_MODEL` | 자동 | 모델명 (비워두면 기본값: `claude-sonnet-4-20250514` / `gpt-4o`) |
| `LLM_TRIGGER_SCORE` | `55` | LLM 판단 요청 최소 점수 |

## 실행

처음 실행하면 셋업 위저드가 자동으로 뜨는 다람쥐:

```bash
cd files
python main_agent.py
```

```
╔══════════════════════════════════════════════════╗
║         ❄️  Snowball Setup Wizard  ❄️            ║
║         OKX Adaptive Grid Trading Agent          ║
╚══════════════════════════════════════════════════╝

─── OKX API 설정 ───────────────────────────────
  API Key: ********
  Secret Key: ********
  Passphrase: ********
  거래 모드 [demo/live] (기본: demo): demo

─── LLM 설정 (리스크 판단용) ───────────────────
  LLM 제공자 [anthropic/openai] (기본: anthropic): openai
  LLM API Key: ********
  ...
```

설정은 `.env` 파일에 저장되는 다람쥐. 나중에 다시 설정하려면:

```bash
python main_agent.py --setup
```

`Ctrl+C`로 종료하는 다람쥐.

## 파일 구조

```
files/
├── config.py            # 설정값 (API 키, 거래 파라미터, 임계값)
├── main_agent.py        # 메인 루프, 상태 머신, LLM 판단, 텔레그램 알림
├── market_analyzer.py   # ATR/RSI/BB/거래량 분석 → 리스크 스코어 산출
├── grid_controller.py   # OKX Grid Bot API 제어 (시작/확대/중단/청산)
└── requirements.txt     # 의존성
```

## 주의사항

- `DEMO_MODE = True` 상태에서 충분히 테스트한 후 실거래로 전환하는 다람쥐
- `config.py`에 API 키가 포함되므로 절대 공개 저장소에 올리지 않는 다람쥐
- `MAX_LOSS_PERCENT` (기본 15%)에 도달하면 자동 청산되는 다람쥐
