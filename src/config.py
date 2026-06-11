"""
OKX Adaptive Grid Agent - 설정 파일
.env 파일이 있으면 우선 로드, 없으면 아래 기본값 사용.
"""

import os
import sys

# ─── .env 로더 ────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(__file__), ".env")

def _load_env(override: bool = False):
    """`.env`를 os.environ에 로드.

    override=True면 이미 설정된 환경변수도 .env 값으로 덮어쓴다
    (메뉴에서 설정 변경 후 reload()용 — setdefault만 쓰면 첫 로드 값이
    영원히 남아서 변경이 반영되지 않는다).
    """
    if not os.path.exists(_env_path):
        return
    try:
        with open(_env_path, encoding="utf-8") as f:
            for line in f:
                try:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    if override:
                        os.environ[key.strip()] = value.strip()
                    else:
                        os.environ.setdefault(key.strip(), value.strip())
                except (ValueError, UnicodeDecodeError):
                    continue
    except (OSError, UnicodeDecodeError) as e:
        print(f"  [config] .env 파일 읽기 오류: {e}", file=sys.stderr)

_load_env()


def reload():
    """설정 강제 재로드 — 메뉴에서 .env 변경 후 호출.

    .env를 override 모드로 다시 읽고 이 모듈을 재실행해서
    모든 `config.X` 참조가 새 값을 보게 한다.
    (다른 모듈은 반드시 `import config` 후 `config.X`로 참조할 것 —
     `from config import X`는 reload가 반영되지 않는다)
    """
    import importlib
    _load_env(override=True)
    importlib.reload(sys.modules[__name__])

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(_env(key, str(default)))
    except (ValueError, TypeError):
        return default

def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key, str(default)))
    except (ValueError, TypeError):
        return default

def _env_bool(key: str, default: bool = True) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")

# ─── OKX API ───────────────────────────────────────────
OKX_API_KEY    = _env("OKX_API_KEY", "YOUR_API_KEY")
OKX_SECRET_KEY = _env("OKX_SECRET_KEY", "YOUR_SECRET_KEY")
OKX_PASSPHRASE = _env("OKX_PASSPHRASE", "YOUR_PASSPHRASE")
OKX_BASE_URL   = _env("OKX_BASE_URL", "https://www.okx.com")
DEMO_MODE      = _env_bool("DEMO_MODE", True)
OKX_TIMEOUT_SEC = _env_float("OKX_TIMEOUT_SEC", 10.0)

# ─── 거래 대상 ─────────────────────────────────────────
SYMBOL         = _env("SYMBOL", "ETH-USDT")
TOTAL_BUDGET   = _env_float("TOTAL_BUDGET", 88000.0)
GRID_BUDGET    = _env_float("GRID_BUDGET", 48000.0)
RESERVE_BUDGET = _env_float("RESERVE_BUDGET", 40000.0)

# ─── 그리드 기본 설정 (중심봇 기준 — 래더 2봇 구성의 주력) ─
# 2026-04-20 전환: 3봇 20그리드 → 2봇 10그리드 기하 래더
# 하단봇 1700~2100 / 중심봇 2000~2400 (이 설정이 중심봇)
GRID_LOWER     = _env_float("GRID_LOWER", 2000.0)
GRID_UPPER     = _env_float("GRID_UPPER", 2400.0)
GRID_COUNT     = _env_int("GRID_COUNT", 10)
GRID_MODE      = _env("GRID_MODE", "geometric")

# ─── 래더 전략 2봇 구성 (보조 봇: 하단봇) ─────────────
LADDER_MODE        = _env_bool("LADDER_MODE", True)
LADDER_LOW_ENABLED = _env_bool("LADDER_LOW_ENABLED", True)
LADDER_LOW_LOWER   = _env_float("LADDER_LOW_LOWER", 1700.0)
LADDER_LOW_UPPER   = _env_float("LADDER_LOW_UPPER", 2100.0)
LADDER_LOW_COUNT   = _env_int("LADDER_LOW_COUNT", 10)
LADDER_LOW_BUDGET  = _env_float("LADDER_LOW_BUDGET", 40000.0)

# ─── 리스크 스코어 임계값 ──────────────────────────────
SCORE_NORMAL    = 30
SCORE_CAUTION   = 60
SCORE_WARNING   = 80
SCORE_EMERGENCY = 100

# ─── 분석 파라미터 ─────────────────────────────────────
ATR_PERIOD              = _env_int("ATR_PERIOD", 14)
ATR_SPIKE_MULTIPLIER    = _env_float("ATR_SPIKE_MULTIPLIER", 3.0)
RSI_PERIOD              = _env_int("RSI_PERIOD", 14)
RSI_OVERBOUGHT          = _env_int("RSI_OVERBOUGHT", 75)
RSI_OVERSOLD            = _env_int("RSI_OVERSOLD", 25)
BOLLINGER_PERIOD        = _env_int("BOLLINGER_PERIOD", 20)
BOLLINGER_STD           = _env_float("BOLLINGER_STD", 2.0)
VOLUME_SPIKE_MULTIPLIER = _env_float("VOLUME_SPIKE_MULTIPLIER", 5.0)

# ─── 손절 조건 ─────────────────────────────────────────
# MAX_LOSS_PERCENT는 최후의 백스톱. 그 전에 DERISK_LEVELS 래더가 단계적으로 작동한다.
MAX_LOSS_PERCENT = _env_float("MAX_LOSS_PERCENT", 15.0)

# ─── 시장 레짐 필터 (2026-06-04 손절 사고 사후 도입) ────
# 일봉 기준 RANGE / TREND_UP / TREND_DOWN을 코드 규칙으로 분류.
# TREND_DOWN 확정 시: 헤지 가동 + 방어 SHIFT_DOWN 허용 + LLM 입증 책임 역전.
REGIME_FILTER_ENABLED = _env_bool("REGIME_FILTER_ENABLED", True)
REGIME_BAR            = _env("REGIME_BAR", "1D")          # 레짐 판별용 캔들 봉
REGIME_LOOKBACK       = _env_int("REGIME_LOOKBACK", 120)  # 캔들 개수
REGIME_ADX_MIN        = _env_float("REGIME_ADX_MIN", 20.0)
REGIME_SLOPE_PCT      = _env_float("REGIME_SLOPE_PCT", 1.0)   # EMA20 5봉 기울기 임계 (%)
REGIME_CONFIRM_TICKS  = _env_int("REGIME_CONFIRM_TICKS", 3)   # 연속 평가 횟수로 확정
REGIME_REFRESH_MIN    = _env_int("REGIME_REFRESH_MIN", 60)    # 일봉 재조회 간격 (분)

# ─── 단계적 디리스킹 래더 ──────────────────────────────
# "드로다운%:액션" 콤마 구분. 액션은 헤지 비율(0~1) 또는 "stop"(전량 청산).
# 기본: -5% → 재고 50% 숏 헤지, -8% → 100% 헤지, -12% → 검증된 전량 청산.
# 그리드 재고는 봇에 잠겨 있어 부분 매도가 어려우므로, 부분 디리스킹은
# 선물 숏 헤지로 수행한다 (그리드는 계속 회전, 방향 노출만 중립화).
DERISK_LEVELS = _env("DERISK_LEVELS", "5:0.5,8:1.0,12:stop")

# ─── 재고(인벤토리) 상한 ────────────────────────────────
# TREND_DOWN 레짐에서 재고 평가액이 그리드 예산의 이 비율을 넘으면
# 초과분만큼 헤지를 추가한다 (하락장 무한 물타기 노출 차단).
INVENTORY_CAP_PCT = _env_float("INVENTORY_CAP_PCT", 60.0)

# ─── 선물 숏 헤지 ──────────────────────────────────────
HEDGE_ENABLED       = _env_bool("HEDGE_ENABLED", True)
HEDGE_RATIO         = _env_float("HEDGE_RATIO", 1.0)    # TREND_DOWN 시 재고 대비 헤지 비율
HEDGE_LEVERAGE      = _env_int("HEDGE_LEVERAGE", 2)     # cross 마진 레버리지
HEDGE_MIN_DELTA_USD = _env_float("HEDGE_MIN_DELTA_USD", 200.0)  # 이보다 작은 조정은 스킵

# ─── 그리드 이탈 대응 ──────────────────────────────────
# 이탈 후 LLM에게 재배치 판단 요청까지 대기 (시간)
# 24h→6h 단축 (2026-06-04: 5분 틱 시장에서 24h 대기는 -10%를 더 맞는 시간)
BREAKOUT_WAIT_HOURS = _env_int("BREAKOUT_WAIT_HOURS", 6)
# 이 시간 초과 시 LLM 판단 무시하고 강제 SHIFT (수수료 가드는 여전히 적용)
BREAKOUT_HARD_TIMEOUT_HOURS = _env_int("BREAKOUT_HARD_TIMEOUT_HOURS", 18)
# 복귀 판정 히스테리시스: 경계에서 이 비율(%) 이상 안쪽으로 들어와야 타이머 리셋.
# (2026-05-23 $1,994 윅처럼 경계를 들락거리는 완만한 붕괴에서 타이머가
#  매번 리셋되어 이탈 시간이 영영 누적되지 않던 결함의 수정)
BREAKOUT_REENTRY_BUFFER_PCT = _env_float("BREAKOUT_REENTRY_BUFFER_PCT", 1.0)
# TREND_DOWN 레짐 + 하단 이탈이면 이 시간 후 LLM 없이 즉시 방어 SHIFT_DOWN
BREAKOUT_TREND_FAST_HOURS = _env_float("BREAKOUT_TREND_FAST_HOURS", 2.0)

# ─── 하단 존 선제 재배치 ────────────────────────────────
# TREND_DOWN 레짐에서 가격이 그리드 범위 내 위치 하위 N% 구간에
# CONFIRM_TICKS틱 연속 머물면 이탈을 기다리지 않고 선제 SHIFT_DOWN.
# (2026-05-29 "버퍼 0.55%뿐, 다음 재배치 땐 1900~2500" 분석을 하고도
#  트리거가 없어 행동하지 못했던 케이스의 자동화)
BOTTOM_ZONE_PCT           = _env_float("BOTTOM_ZONE_PCT", 10.0)
BOTTOM_ZONE_CONFIRM_TICKS = _env_int("BOTTOM_ZONE_CONFIRM_TICKS", 12)  # 5분 틱 기준 1시간

# ─── 상태 저장 (재시작 시 이어받기) ─────────────────────
# 이탈 타이머 / 일일 카운터 / 재시작 기록 / 포지션을 파일에 저장.
# 빈 문자열이면 저장 비활성화. 경로가 상대경로면 src/ 기준.
STATE_FILE = _env("STATE_FILE", "agent_state.json")

# ─── 모니터링 주기 ─────────────────────────────────────
LOOP_INTERVAL_SEC    = _env_int("LOOP_INTERVAL_SEC", 300)
CANDLE_INTERVAL      = _env("CANDLE_INTERVAL", "1m")
CANDLE_LOOKBACK      = _env_int("CANDLE_LOOKBACK", 100)

# ─── 텔레그램 알림 ─────────────────────────────────────
TELEGRAM_TOKEN   = _env("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "")
NOTIFY_ON_STATES = ["CAUTION", "WARNING", "EMERGENCY"]
DAILY_REPORT_HOUR = _env_int("DAILY_REPORT_HOUR", 21)  # 일일 리포트 발송 시간 (0~23)

# 매 틱(5분 간격) 요약 리포트 발송 여부.
# False면 알림은 이벤트 발생 시(LLM 합의/체결/상태변화/이탈/일일리포트)에만.
# 알림 폭주 방지용 — 4/27 사용자 피드백으로 도입.
NOTIFY_TICK_REPORTS = _env_bool("NOTIFY_TICK_REPORTS", False)

# 알림 메시지에 OKX 활성 그리드봇 리스트를 footer로 자동 첨부.
# 멀티봇 운영 시 매 알림에서 어떤 봇들이 돌고 있는지 한눈에 확인.
NOTIFY_INCLUDE_BOT_LIST = _env_bool("NOTIFY_INCLUDE_BOT_LIST", True)

# ─── LLM 판단 조건 ─────────────────────────────────────
LLM_TRIGGER_SCORE  = _env_int("LLM_TRIGGER_SCORE", 55)
LLM_PROVIDER       = _env("LLM_PROVIDER", "anthropic")
LLM_API_KEY        = _env("LLM_API_KEY", "")
LLM_MODEL          = _env("LLM_MODEL", "")
MULTI_AGENT_MODE   = _env_bool("MULTI_AGENT_MODE", True)  # True=멀티에이전트 합의, False=단일 LLM
