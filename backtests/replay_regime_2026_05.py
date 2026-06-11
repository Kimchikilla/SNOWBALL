"""
replay_regime_2026_05.py
2026년 5월 ETH 폭락 구간에 대해 RegimeDetector를 워크포워드 리플레이.

질문: 새 레짐 필터가 있었다면 추세 하락을 언제 감지했을까?
(운영 환경에선 1시간마다 평가 + confirm 3회라, 일봉 raw 신호 발생 후
 약 3시간 내 확정된다 — 여기선 raw 신호의 날짜 전환만 본다)

실행: python backtests/replay_regime_2026_05.py
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from risk_manager import RegimeDetector  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "eth_1d.json")


def main():
    with open(DATA, encoding="utf-8-sig") as f:
        raw = json.load(f)

    # OKX 캔들: [ts, o, h, l, c, vol, ...] 최신→과거 또는 과거→최신일 수 있어 정렬
    rows = sorted(raw, key=lambda r: int(r[0]))
    candles = [
        {"ts": r[0], "open": r[1], "high": r[2], "low": r[3],
         "close": r[4], "vol": r[5]}
        for r in rows
    ]

    detector = RegimeDetector(adx_min=20.0, slope_pct=1.0, confirm_ticks=1)

    print(f"{'date':12} {'close':>9} {'raw':>11} "
          f"{'ema20':>9} {'ema50':>9} {'slope%':>7} {'adx':>6}")
    prev_raw = None
    for i in range(60, len(candles)):
        window = candles[: i + 1]
        date = datetime.fromtimestamp(int(window[-1]["ts"]) / 1000).strftime("%Y-%m-%d")
        if not date.startswith("2026-0"):
            continue
        raw_regime, ema_f, ema_s, slope, adx = detector.classify_raw(window)
        close = float(window[-1]["close"])
        marker = "  ◀── 전환" if raw_regime != prev_raw else ""
        if date >= "2026-04-20" or marker:
            print(f"{date:12} {close:9,.0f} {raw_regime:>11} "
                  f"{ema_f:9,.0f} {ema_s:9,.0f} {slope:7.2f} {adx:6.1f}{marker}")
        prev_raw = raw_regime


if __name__ == "__main__":
    main()
