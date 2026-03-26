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

        total = atr_score + rsi_score + bb_score + volume_score
        state = self._classify(total)
        reason = self._summarize(
            total, atr_score, rsi_score, bb_score, volume_score,
            atr_cur, atr_avg, rsi, bb_width, vol_ratio
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
        """볼린저밴드 폭 급팽창 → 최대 25점"""
        bb_closes = closes[-BOLLINGER_PERIOD:]
        mean  = float(np.mean(bb_closes))
        std   = float(np.std(bb_closes))
        width = (std * BOLLINGER_STD * 2) / (mean + 1e-9)   # 가격 대비 밴드 폭

        # 볼린저밴드 폭 기준: 정상 ~2%, 위험 ~6% 이상
        score = min(25.0, max(0.0, (width - 0.02) / 0.04 * 25.0))
        return round(score, 1), round(width * 100, 2)         # width를 %로 반환

    def _volume_score(self, volumes):
        """거래량 급등 여부 → 최대 20점"""
        avg = float(np.mean(volumes[-30:]))
        cur = float(volumes[-1])
        ratio = cur / (avg + 1e-9)

        score = min(20.0, max(0.0, (ratio - 1.0) / (VOLUME_SPIKE_MULTIPLIER - 1.0) * 20.0))
        return round(score, 1), round(ratio, 2)

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
                   atr_cur, atr_avg, rsi, bb_width, vol_ratio) -> str:
        reasons = []
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
