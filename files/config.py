"""
OKX Adaptive Grid Agent - 설정 파일
"""

# ─── OKX API ───────────────────────────────────────────
OKX_API_KEY    = "YOUR_API_KEY"
OKX_SECRET_KEY = "YOUR_SECRET_KEY"
OKX_PASSPHRASE = "YOUR_PASSPHRASE"
OKX_BASE_URL   = "https://www.okx.com"
DEMO_MODE      = True   # True = 모의거래, False = 실거래

# ─── 거래 대상 ─────────────────────────────────────────
SYMBOL         = "BTC-USDT"          # BTC-USDT or ETH-USDT
TOTAL_BUDGET   = 1000.0              # USDT 총 투입 예산
GRID_BUDGET    = 400.0               # 그리드에 쓸 금액 (40%)
RESERVE_BUDGET = 600.0               # 예비 자금 (60%)

# ─── 그리드 기본 설정 ──────────────────────────────────
GRID_LOWER     = 90000.0             # 하단 가격 (BTC 예시)
GRID_UPPER     = 110000.0            # 상단 가격
GRID_COUNT     = 20                  # 그리드 분할 개수
GRID_MODE      = "arithmetic"        # arithmetic | geometric

# ─── 리스크 스코어 임계값 ──────────────────────────────
SCORE_NORMAL    = 30    # 0~30: 정상 운영
SCORE_CAUTION   = 60    # 31~60: 주의 (간격 확대)
SCORE_WARNING   = 80    # 61~80: 경고 (신규 주문 중단)
SCORE_EMERGENCY = 100   # 81~100: 긴급 (전체 청산)

# ─── 분석 파라미터 ─────────────────────────────────────
ATR_PERIOD              = 14         # ATR 계산 기간
ATR_SPIKE_MULTIPLIER    = 3.0        # ATR이 평균의 몇 배면 이상으로 볼지
RSI_PERIOD              = 14
RSI_OVERBOUGHT          = 75
RSI_OVERSOLD            = 25
BOLLINGER_PERIOD        = 20
BOLLINGER_STD           = 2.0
VOLUME_SPIKE_MULTIPLIER = 5.0        # 거래량이 평균의 몇 배면 급등으로 볼지

# ─── 손절 조건 ─────────────────────────────────────────
MAX_LOSS_PERCENT = 15.0              # 진입 평단 대비 -15% 시 강제 청산

# ─── 모니터링 주기 ─────────────────────────────────────
LOOP_INTERVAL_SEC    = 120            # 메인 루프 실행 간격 (초)
CANDLE_INTERVAL      = "1m"          # 캔들 기준
CANDLE_LOOKBACK      = 100           # 분석에 쓸 캔들 개수

# ─── 텔레그램 알림 ─────────────────────────────────────
TELEGRAM_TOKEN   = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"
NOTIFY_ON_STATES = ["CAUTION", "WARNING", "EMERGENCY"]

# ─── LLM 판단 조건 ─────────────────────────────────────
LLM_TRIGGER_SCORE = 55               # 이 점수 이상이면 LLM에 판단 요청
LLM_PROVIDER      = "anthropic"      # "anthropic" | "openai"
LLM_API_KEY       = "YOUR_API_KEY"
LLM_MODEL         = ""               # 비워두면 기본값 사용 (anthropic: claude-sonnet-4-20250514, openai: gpt-4o)
