"""
market_analyzer.py
실시간 시장 데이터를 분석해서 리스크 스코어(0~100)를 산출합니다.
"""

import numpy as np
from dataclasses import dataclass
from config import (
    ATR_PERIOD, ATR_SPIKE_MULTIPLIER,
    RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD,
    BOLLINGER_PERIOD, BOLLINGER_STD,
    VOLUME_SPIKE_MULTIPLIER
)


@dataclass
class MarketSignal:
    """분석 결과를 담는 데이터 클래스"""
    risk_score: float           # 0~100 종합 리스크 점수
    atr_score: float            # ATR 기여 점수 (0~30)
    rsi_score: float            # RSI 기여 점수 (0~25)
    bb_score: float             # 볼린저밴드 기여 점수 (0~25)
    volume_score: float         # 거래량 기여 점수 (0~20)
    atr_current: float
    atr_avg: float
    rsi: float
    bb_width: float
    volume_ratio: float         # 현재 거래량 / 평균 거래량
    trend: str                  # BULLISH | BEARISH | SIDEWAYS
    trend_strength: float       # 0~100 추세 강도 (ADX 값)
    ema_short: float            # 단기 EMA (9) 값
    ema_long: float             # 장기 EMA (21) 값
    adx: float                  # ADX 값
    state: str                  # NORMAL | CAUTION | WARNING | EMERGENCY
    reason: str                 # 사람이 읽기 좋은 요약


class MarketAnalyzer:
    """
    캔들 데이터를 받아 리스크 스코어를 계산합니다.

    Usage:
        analyzer = MarketAnalyzer()
        candles = okx_client.get_candles("BTC-USDT", bar="1m", limit=100)
        signal = analyzer.analyze(candles)
        print(signal.state, signal.risk_score)
    """

    def analyze(self, candles: list[dict]) -> MarketSignal:
        """
        Args:
            candles: OKX candle list.
                     각 원소: {"ts", "open", "high", "low", "close", "vol"}
        Returns:
            MarketSignal
        """
        if len(candles) < max(ATR_PERIOD, RSI_PERIOD, BOLLINGER_PERIOD) + 5:
            raise ValueError("캔들 데이터가 부족합니다 (최소 100개 필요)")

        highs   = np.array([float(c["high"])  for c in candles])
        lows    = np.array([float(c["low"])   for c in candles])
        closes  = np.array([float(c["close"]) for c in candles])
        volumes = np.array([float(c["vol"])   for c in candles])

        atr_score,    atr_cur, atr_avg = self._atr_score(highs, lows, closes)
        rsi_score,    rsi                = self._rsi_score(closes)
        bb_score,     bb_width           = self._bollinger_score(closes)
        volume_score, vol_ratio          = self._volume_score(volumes)

        trend, trend_strength, ema_short, ema_long, adx = self._detect_trend(
            highs, lows, closes
        )

        total = atr_score + rsi_score + bb_score + volume_score
        state = self._classify(total)
        reason = self._summarize(
            total, atr_score, rsi_score, bb_score, volume_score,
            atr_cur, atr_avg, rsi, bb_width, vol_ratio, trend, trend_strength
        )

        return MarketSignal(
            risk_score=round(total, 1),
            atr_score=atr_score,
            rsi_score=rsi_score,
            bb_score=bb_score,
            volume_score=volume_score,
            atr_current=atr_cur,
            atr_avg=atr_avg,
            rsi=rsi,
            bb_width=bb_width,
            volume_ratio=vol_ratio,
            trend=trend,
            trend_strength=round(trend_strength, 1),
            ema_short=round(ema_short, 2),
            ema_long=round(ema_long, 2),
            adx=round(adx, 1),
            state=state,
            reason=reason
        )

    # ─── 개별 지표 계산 ──────────────────────────────────────

    def _atr_score(self, highs, lows, closes):
        """ATR 급등 여부 → 최대 30점"""
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:]  - closes[:-1])
            )
        )
        atr_series = self._ema(tr, ATR_PERIOD)
        cur = atr_series[-1]
        avg = float(np.mean(atr_series[-ATR_PERIOD * 2:-ATR_PERIOD]))

        ratio = cur / (avg + 1e-9)
        # ratio 1x→0점, 3x→30점, 그 이상은 30점 cap
        score = min(30.0, max(0.0, (ratio - 1.0) / (ATR_SPIKE_MULTIPLIER - 1.0) * 30.0))
        return round(score, 1), round(cur, 2), round(avg, 2)

    def _rsi_score(self, closes):
        """RSI 극단값 → 최대 25점"""
        delta = np.diff(closes)
        gains  = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)

        avg_gain = self._ema(gains,  RSI_PERIOD)[-1]
        avg_loss = self._ema(losses, RSI_PERIOD)[-1]

        # 변동 없으면 RSI=50 (중립)
        if avg_gain < 1e-9 and avg_loss < 1e-9:
            rsi = 50.0
        else:
            rs  = avg_gain / (avg_loss + 1e-9)
            rsi = 100 - 100 / (1 + rs)

        # 과매수(>75) 또는 과매도(<25)일수록 점수 높음
        if rsi > RSI_OVERBOUGHT:
            score = (rsi - RSI_OVERBOUGHT) / (100 - RSI_OVERBOUGHT) * 25
        elif rsi < RSI_OVERSOLD:
            score = (RSI_OVERSOLD - rsi) / RSI_OVERSOLD * 25
        else:
            score = 0.0

        return round(min(25.0, score), 1), round(rsi, 1)

    def _bollinger_score(self, closes):
        """볼린저밴드 폭 급팽창 → 최대 25점 (1분봉 적응형 임계값)"""
        bb_closes = closes[-BOLLINGER_PERIOD:]
        mean  = float(np.mean(bb_closes))
        std   = float(np.std(bb_closes))
        width = (std * BOLLINGER_STD * 2) / (mean + 1e-9)   # 가격 대비 밴드 폭

        # 타임프레임 적응형: 전체 캔들의 평균 변동폭으로 기준선 산출
        all_returns = np.abs(np.diff(closes)) / (closes[:-1] + 1e-9)
        baseline = float(np.mean(all_returns)) * BOLLINGER_PERIOD
        # 기준선이 너무 작으면 최소값 보장 (일봉 기준 ~0.02)
        threshold_low = max(baseline * 0.5, 0.001)
        threshold_high = max(baseline * 2.0, threshold_low * 3)

        score = min(25.0, max(0.0, (width - threshold_low) / (threshold_high - threshold_low + 1e-9) * 25.0))
        return round(score, 1), round(width * 100, 2)         # width를 %로 반환

    def _volume_score(self, volumes):
        """거래량 급등 여부 → 최대 20점 (3캔들 롤링 평균)"""
        avg = float(np.mean(volumes[-30:-3])) if len(volumes) > 30 else float(np.mean(volumes[:-3]))
        # 단일 캔들 노이즈 방지: 최근 3개 캔들 평균
        cur = float(np.mean(volumes[-3:])) if len(volumes) >= 3 else float(volumes[-1])
        ratio = cur / (avg + 1e-9)

        score = min(20.0, max(0.0, (ratio - 1.0) / (VOLUME_SPIKE_MULTIPLIER - 1.0) * 20.0))
        return round(score, 1), round(ratio, 2)

    # ─── 추세 감지 ──────────────────────────────────────────

    def _detect_trend(self, highs, lows, closes):
        """EMA 9/21 크로스오버 + ADX로 추세 방향·강도를 판별합니다.

        Returns:
            (trend, trend_strength, ema_short, ema_long, adx)
        """
        ema_short = self._ema(closes, 9)[-1]
        ema_long  = self._ema(closes, 21)[-1]
        adx       = self._calc_adx(highs, lows, closes, period=14)

        if adx < 20:
            trend = "SIDEWAYS"
        elif ema_short > ema_long:
            trend = "BULLISH"
        else:
            trend = "BEARISH"

        return trend, adx, ema_short, ema_long, adx

    def _calc_adx(self, highs, lows, closes, period=14):
        """ADX(Average Directional Index)를 계산합니다.

        +DM, -DM → +DI, -DI (ATR 사용) → DX → ADX(EMA smoothing)
        """
        high_diff = highs[1:] - highs[:-1]
        low_diff  = lows[:-1] - lows[1:]

        plus_dm  = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
        minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)

        # True Range
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:]  - closes[:-1])
            )
        )

        # EMA smoothing
        atr       = self._ema(tr, period)
        plus_dm_s = self._ema(plus_dm, period)
        minus_dm_s = self._ema(minus_dm, period)

        # +DI, -DI
        plus_di  = 100 * plus_dm_s / (atr + 1e-9)
        minus_di = 100 * minus_dm_s / (atr + 1e-9)

        # DX → ADX
        dx  = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        adx = self._ema(dx, period)

        return float(adx[-1])

    # ─── 유틸 ────────────────────────────────────────────────

    def _ema(self, arr: np.ndarray, period: int) -> np.ndarray:
        alpha = 2 / (period + 1)
        ema   = np.zeros_like(arr)
        ema[0] = arr[0]
        for i in range(1, len(arr)):
            ema[i] = alpha * arr[i] + (1 - alpha) * ema[i - 1]
        return ema

    def _classify(self, score: float) -> str:
        if score <= 30:
            return "NORMAL"
        elif score <= 60:
            return "CAUTION"
        elif score <= 80:
            return "WARNING"
        else:
            return "EMERGENCY"

    def _summarize(self, total, atr_s, rsi_s, bb_s, vol_s,
                   atr_cur, atr_avg, rsi, bb_width, vol_ratio,
                   trend="SIDEWAYS", trend_strength=0.0) -> str:
        reasons = []
        if trend != "SIDEWAYS":
            label = "상승 추세" if trend == "BULLISH" else "하락 추세"
            reasons.append(f"{label} (ADX {trend_strength:.1f})")
        if atr_s >= 15:
            reasons.append(f"ATR 급등 ({atr_cur:.1f} → 평균 {atr_avg:.1f}의 {atr_cur/max(atr_avg,1):.1f}배)")
        if rsi_s >= 10:
            reasons.append(f"RSI 극단값 ({rsi:.1f})")
        if bb_s >= 10:
            reasons.append(f"볼린저밴드 폭 팽창 ({bb_width:.1f}%)")
        if vol_s >= 10:
            reasons.append(f"거래량 급등 (평균의 {vol_ratio:.1f}배)")

        state = self._classify(total)
        base  = f"[{state}] 리스크 스코어 {total:.1f}/100"
        if reasons:
            return base + " | " + ", ".join(reasons)
        return base + " | 정상 범위"
