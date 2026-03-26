"""
OKX Adaptive Grid Agent - 설정 파일
.env 파일이 있으면 우선 로드, 없으면 아래 기본값 사용.
"""

import os
import sys

# ─── .env 로더 ────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(__file__), ".env")

def _load_env():
    if not os.path.exists(_env_path):
        return
    with open(_env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

_load_env()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_float(key: str, default: float = 0.0) -> float:
    return float(_env(key, str(default)))

def _env_int(key: str, default: int = 0) -> int:
    return int(_env(key, str(default)))

def _env_bool(key: str, default: bool = True) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")

# ─── OKX API ───────────────────────────────────────────
OKX_API_KEY    = _env("OKX_API_KEY", "YOUR_API_KEY")
OKX_SECRET_KEY = _env("OKX_SECRET_KEY", "YOUR_SECRET_KEY")
OKX_PASSPHRASE = _env("OKX_PASSPHRASE", "YOUR_PASSPHRASE")
OKX_BASE_URL   = _env("OKX_BASE_URL", "https://www.okx.com")
DEMO_MODE      = _env_bool("DEMO_MODE", True)

# ─── 거래 대상 ─────────────────────────────────────────
SYMBOL         = _env("SYMBOL", "BTC-USDT")
TOTAL_BUDGET   = _env_float("TOTAL_BUDGET", 1000.0)
GRID_BUDGET    = _env_float("GRID_BUDGET", 400.0)
RESERVE_BUDGET = _env_float("RESERVE_BUDGET", 600.0)

# ─── 그리드 기본 설정 ──────────────────────────────────
GRID_LOWER     = _env_float("GRID_LOWER", 90000.0)
GRID_UPPER     = _env_float("GRID_UPPER", 110000.0)
GRID_COUNT     = _env_int("GRID_COUNT", 20)
GRID_MODE      = _env("GRID_MODE", "arithmetic")

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
MAX_LOSS_PERCENT = _env_float("MAX_LOSS_PERCENT", 15.0)

# ─── 모니터링 주기 ─────────────────────────────────────
LOOP_INTERVAL_SEC    = _env_int("LOOP_INTERVAL_SEC", 120)
CANDLE_INTERVAL      = _env("CANDLE_INTERVAL", "1m")
CANDLE_LOOKBACK      = _env_int("CANDLE_LOOKBACK", 100)

# ─── 텔레그램 알림 ─────────────────────────────────────
TELEGRAM_TOKEN   = _env("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "")
NOTIFY_ON_STATES = ["CAUTION", "WARNING", "EMERGENCY"]

# ─── LLM 판단 조건 ─────────────────────────────────────
LLM_TRIGGER_SCORE = _env_int("LLM_TRIGGER_SCORE", 55)
LLM_PROVIDER      = _env("LLM_PROVIDER", "anthropic")
LLM_API_KEY       = _env("LLM_API_KEY", "")
LLM_MODEL         = _env("LLM_MODEL", "")
