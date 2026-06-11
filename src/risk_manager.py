"""
risk_manager.py
결정론적 리스크 관리 — 레짐 판별 / 단계적 디리스킹 / 헤지 목표 계산.

2026-06-04 손절 사고(-14.85%) 사후 분석으로 도입:
  - 그리드 봇은 박스권 전용 전략인데, 시장 체제(레짐)를 판별해서
    행동을 바꾸는 장치가 없어 -24% 추세 하락을 -15% 손절까지 그대로 탔다.
  - 안전장치는 LLM 합의가 아니라 코드 규칙이어야 한다. 이 모듈의 판단은
    전부 결정론적이며 LLM을 호출하지 않는다.

구성:
  RegimeDetector — 일봉 기반 RANGE / TREND_UP / TREND_DOWN 분류 (확정 카운터로 휩쏘 방지)
  DeriskLadder   — 총손익 드로다운 단계별 디리스킹 (-5% / -8% / -12% 등)
  hedge_target_qty — 레짐 + 래더 + 재고 상한을 종합한 선물 숏 헤지 목표 수량
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np


# ─── 지표 헬퍼 (market_analyzer와 동일 정의, 의존성 없이 복제) ──

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    ema = np.zeros_like(arr, dtype=float)
    ema[0] = arr[0]
    for i in range(1, len(arr)):
        ema[i] = alpha * arr[i] + (1 - alpha) * ema[i - 1]
    return ema


def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
         period: int = 14) -> float:
    high_diff = highs[1:] - highs[:-1]
    low_diff = lows[:-1] - lows[1:]

    plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
    minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)

    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1])
        )
    )

    atr = _ema(tr, period)
    plus_di = 100 * _ema(plus_dm, period) / (atr + 1e-9)
    minus_di = 100 * _ema(minus_dm, period) / (atr + 1e-9)
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
    return float(_ema(dx, period)[-1])


# ─── 레짐 판별 ──────────────────────────────────────────

REGIME_RANGE = "RANGE"
REGIME_TREND_UP = "TREND_UP"
REGIME_TREND_DOWN = "TREND_DOWN"


@dataclass
class RegimeState:
    regime: str          # 확정 레짐 (RANGE / TREND_UP / TREND_DOWN)
    raw: str             # 이번 평가의 원시 판정 (확정 전)
    streak: int          # 원시 판정 연속 횟수
    confirmed: bool      # 이번 평가에서 레짐이 전환됐는지
    ema_fast: float
    ema_slow: float
    slope_pct: float     # EMA fast의 slope_window 기간 변화율 (%)
    adx: float
    reason: str


class RegimeDetector:
    """일봉 캔들로 시장 체제를 분류한다.

    원시 판정 규칙 (일봉 기준):
      TREND_DOWN: EMA20 < EMA50  AND  EMA20 5일 기울기 < -slope_pct%  AND  ADX >= adx_min
      TREND_UP  : EMA20 > EMA50  AND  EMA20 5일 기울기 > +slope_pct%  AND  ADX >= adx_min
      그 외     : RANGE

    원시 판정이 confirm_ticks회 연속 같아야 확정 레짐이 바뀐다 (휩쏘 방지).
    확정 레짐은 보수적으로 유지된다 — 전환에는 증거 누적이 필요하다.
    """

    def __init__(self, adx_min: float = 20.0, slope_pct: float = 1.0,
                 confirm_ticks: int = 3, fast_period: int = 20,
                 slow_period: int = 50, slope_window: int = 5):
        self.adx_min = adx_min
        self.slope_pct = slope_pct
        self.confirm_ticks = max(1, int(confirm_ticks))
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.slope_window = slope_window

        self.regime: str = REGIME_RANGE
        self._pending: str = REGIME_RANGE
        self._streak: int = 0

    # 상태 영속화 (agent_state.json 저장/복원용)
    def to_state(self) -> dict:
        return {"regime": self.regime, "pending": self._pending,
                "streak": self._streak}

    def from_state(self, state: dict):
        if not isinstance(state, dict):
            return
        regime = state.get("regime")
        if regime in (REGIME_RANGE, REGIME_TREND_UP, REGIME_TREND_DOWN):
            self.regime = regime
        pending = state.get("pending")
        if pending in (REGIME_RANGE, REGIME_TREND_UP, REGIME_TREND_DOWN):
            self._pending = pending
        try:
            self._streak = max(0, int(state.get("streak", 0)))
        except (TypeError, ValueError):
            self._streak = 0

    def classify_raw(self, candles: list[dict]) -> tuple[str, float, float, float, float]:
        """확정 로직 없이 원시 판정만 수행. Returns (raw, ema_fast, ema_slow, slope_pct, adx)."""
        need = max(self.slow_period, 14 * 2) + self.slope_window
        if len(candles) < need:
            raise ValueError(f"레짐 판별에 캔들 {need}개 이상 필요 (현재 {len(candles)}개)")

        highs = np.array([float(c["high"]) for c in candles])
        lows = np.array([float(c["low"]) for c in candles])
        closes = np.array([float(c["close"]) for c in candles])

        ema_fast_series = _ema(closes, self.fast_period)
        ema_fast = float(ema_fast_series[-1])
        ema_slow = float(_ema(closes, self.slow_period)[-1])
        prev = float(ema_fast_series[-1 - self.slope_window])
        slope = (ema_fast - prev) / (prev + 1e-9) * 100
        adx = _adx(highs, lows, closes)

        if ema_fast < ema_slow and slope < -self.slope_pct and adx >= self.adx_min:
            raw = REGIME_TREND_DOWN
        elif ema_fast > ema_slow and slope > self.slope_pct and adx >= self.adx_min:
            raw = REGIME_TREND_UP
        else:
            raw = REGIME_RANGE

        return raw, ema_fast, ema_slow, slope, adx

    def update(self, candles: list[dict]) -> RegimeState:
        """원시 판정 + 확정 카운터 갱신. 캔들은 과거→최신 순."""
        raw, ema_fast, ema_slow, slope, adx = self.classify_raw(candles)

        if raw == self._pending:
            self._streak += 1
        else:
            self._pending = raw
            self._streak = 1

        confirmed = False
        if raw != self.regime and self._streak >= self.confirm_ticks:
            self.regime = raw
            confirmed = True

        reason = (
            f"EMA{self.fast_period}={ema_fast:,.1f} "
            f"{'<' if ema_fast < ema_slow else '>'} EMA{self.slow_period}={ema_slow:,.1f}, "
            f"기울기({self.slope_window}봉)={slope:+.2f}%, ADX={adx:.1f} "
            f"→ 원시={raw} (연속 {self._streak}/{self.confirm_ticks}), 확정={self.regime}"
        )

        return RegimeState(
            regime=self.regime, raw=raw, streak=self._streak,
            confirmed=confirmed, ema_fast=ema_fast, ema_slow=ema_slow,
            slope_pct=slope, adx=adx, reason=reason,
        )


# ─── 단계적 디리스킹 래더 ────────────────────────────────

#: 래더 항목의 액션 종류
DERISK_HEDGE = "HEDGE"   # 재고의 fraction만큼 선물 숏 헤지
DERISK_STOP = "STOP"     # 전량 청산 (봇 정지 + 보유 매도 + 헤지 종료)


@dataclass
class DeriskLevel:
    loss_pct: float            # 트리거 드로다운 (그리드 예산 대비 총손익 %, 양수로 표기)
    action: str                # DERISK_HEDGE | DERISK_STOP
    fraction: float            # HEDGE일 때 재고 대비 헤지 비율 (0~1)


@dataclass
class DeriskDecision:
    level_index: int           # 발동한 레벨 (1-based)
    level: DeriskLevel
    reason: str


def parse_derisk_levels(spec: str) -> list[DeriskLevel]:
    """설정 문자열 파싱. 예: "5:0.5,8:1.0,12:stop"

    "손실%:헤지비율" 또는 "손실%:stop". 손실% 오름차순으로 정렬해 반환.
    잘못된 항목은 건너뛴다.
    """
    levels: list[DeriskLevel] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        loss_str, _, act_str = part.partition(":")
        try:
            loss_pct = float(loss_str)
        except ValueError:
            continue
        if loss_pct <= 0:
            continue
        act_str = act_str.strip().lower()
        if act_str == "stop":
            levels.append(DeriskLevel(loss_pct, DERISK_STOP, 1.0))
        else:
            try:
                fraction = float(act_str)
            except ValueError:
                continue
            if not 0 < fraction <= 1:
                continue
            levels.append(DeriskLevel(loss_pct, DERISK_HEDGE, fraction))
    levels.sort(key=lambda lv: lv.loss_pct)
    return levels


class DeriskLadder:
    """총손익 드로다운에 따라 단계적으로 디리스킹한다.

    레벨은 래칫(ratchet) 구조 — 한 번 발동한 레벨은 드로다운이 일시 회복해도
    유지되며, 첫 레벨의 절반 이내로 회복했을 때만 0으로 리셋한다.
    (회복 직후 재발동으로 인한 헤지 사다리 왕복 방지)
    """

    def __init__(self, levels: list[DeriskLevel]):
        self.levels = levels
        self.current_level: int = 0   # 0 = 미발동, 1-based index

    def to_state(self) -> dict:
        return {"current_level": self.current_level}

    def from_state(self, state: dict):
        if not isinstance(state, dict):
            return
        try:
            level = int(state.get("current_level", 0))
        except (TypeError, ValueError):
            level = 0
        self.current_level = max(0, min(level, len(self.levels)))

    def evaluate(self, total_pnl_pct: float) -> Optional[DeriskDecision]:
        """드로다운이 새 레벨을 넘었으면 해당 레벨 결정을 반환.

        Args:
            total_pnl_pct: 그리드 예산 대비 총손익 % (손실이면 음수)
        """
        if not self.levels:
            return None

        drawdown = -total_pnl_pct  # 손실을 양수로
        # 회복 리셋: 첫 레벨의 절반 이내로 돌아오면 래더 초기화
        if self.current_level > 0 and drawdown < self.levels[0].loss_pct / 2:
            self.current_level = 0
            return None

        # 현재 레벨보다 높은 레벨 중 드로다운이 넘은 가장 깊은 레벨로 점프
        triggered: Optional[int] = None
        for idx in range(self.current_level, len(self.levels)):
            if drawdown >= self.levels[idx].loss_pct:
                triggered = idx + 1
        if triggered is None:
            return None

        self.current_level = triggered
        level = self.levels[triggered - 1]
        action_str = (
            f"재고 {level.fraction * 100:.0f}% 헤지" if level.action == DERISK_HEDGE
            else "전량 청산"
        )
        return DeriskDecision(
            level_index=triggered,
            level=level,
            reason=(
                f"총손익 {total_pnl_pct:+.2f}% ≤ -{level.loss_pct:.1f}% "
                f"(L{triggered}/{len(self.levels)}) → {action_str}"
            ),
        )

    def hedge_fraction(self) -> float:
        """현재 발동 레벨 기준 유지해야 할 헤지 비율 (0~1)."""
        if self.current_level <= 0:
            return 0.0
        level = self.levels[self.current_level - 1]
        if level.action == DERISK_STOP:
            return 1.0
        return level.fraction


# ─── 헤지 목표 계산 ──────────────────────────────────────

def hedge_target_qty(
    holding_qty: float,
    price: float,
    regime: str,
    ladder_fraction: float,
    regime_hedge_ratio: float,
    inventory_cap_usd: float,
    enabled: bool = True,
) -> float:
    """유지해야 할 선물 숏 헤지 수량 (기초자산 단위).

    세 가지 요구의 최댓값:
      1. 래더 — 드로다운 레벨이 요구하는 재고 비율
      2. 레짐 — TREND_DOWN 확정 시 재고의 regime_hedge_ratio
      3. 재고 상한 — TREND_DOWN에서 노출이 상한을 넘는 초과분

    헤지는 재고가 있을 때만 의미가 있다 (naked short 금지) — 재고 수량을 cap.
    """
    if not enabled or holding_qty <= 0 or price <= 0:
        return 0.0

    targets = [holding_qty * max(0.0, min(ladder_fraction, 1.0))]

    if regime == REGIME_TREND_DOWN:
        targets.append(holding_qty * max(0.0, min(regime_hedge_ratio, 1.0)))
        exposure = holding_qty * price
        if inventory_cap_usd > 0 and exposure > inventory_cap_usd:
            targets.append((exposure - inventory_cap_usd) / price)

    return min(max(targets), holding_qty)
