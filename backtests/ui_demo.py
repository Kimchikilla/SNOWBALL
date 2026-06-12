"""ui_demo.py — 새 터미널 UI 렌더링 확인용 데모. 실행: python backtests/ui_demo.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import ui  # noqa: E402

ui.banner()
ui.disclaimer()

ui.panel_kv("기존 그리드봇 연결됨", [
    ("봇 ID", "812345678901234567"),
    ("상태", "running"),
    ("범위", "2,000.00 ~ 2,500.00"),
    ("그리드", "10개 (arithmetic)"),
    ("투자금", "38,000.00 USDT"),
    ("손익", ui.pnl_c(-2464.37) + " USDT"),
], color=ui.GREEN)
print()

ui.bullet(
    f"{ui.c('틱 #42', bold=True)}{ui.sep()}{ui.c('14:05:00', ui.GRAY)}"
    f"{ui.sep()}ETH-USDT {ui.c('2,076.40', ui.WHITE, bold=True)}"
    f"{ui.sep()}{ui.c('NORMAL', ui.GREEN, bold=True)} {ui.c('21.5/100', ui.GRAY)}",
    ui.GREEN,
)
ui.child(f"ATR 12.3{ui.c('/', ui.DGRAY)}10.1{ui.sep()}RSI 41.2{ui.sep()}BB 1.21%"
         f"{ui.sep()}Vol 0.9x{ui.sep()}EMA 2,081/2,079", "지표")
ui.child(f"{ui.c('SIDEWAYS', ui.GRAY)} {ui.c('(ADX 18.2)', ui.GRAY)}"
         f"{ui.sep()}레짐 {ui.c('TREND_DOWN', ui.RED, bold=True)}"
         f"{ui.c('  기울기 -2.50% · ADX 33.9 · TREND_DOWN 3/3', ui.DGRAY)}", "추세")
ui.child(f"기준 2,174 (avg_cost){ui.sep()}가격 -4.50%{ui.sep()}총손익 "
         f"{ui.pnl_c(-2464.37)} (-6.49%){ui.sep()}{ui.c('한도 15.0%', ui.DGRAY)}", "손절")
ui.child(f"매수 2{ui.sep()}매도 1{ui.sep()}보유 10.8229{ui.sep()}실현 {ui.pnl_c(-128.93)}"
         f"{ui.sep()}미실현 {ui.pnl_c(-1056.0)}{ui.sep()}합계 {ui.pnl_c(-1184.93)}", "체결")
ui.child_warn("하단 존 체류 4/12틱 (범위 내 위치 8.3%)", "리스크")
ui.child(f"헤지 목표 10.8229{ui.sep()}숏 5.4000{ui.sep()}래더 L1"
         f"{ui.sep()}{ui.c('adjusted', ui.DGRAY)}", "리스크")
ui.child(f"{ui.c('MAINTAIN', ui.GREEN, bold=True)}", "결정")
ui.child(ui.c("비용: $0.012 / $5.00 · 리포트 21시 발송됨", ui.DGRAY), "기타")
print()
ui.child_err("OKX API 타임아웃 — 재시도 2/3", "수집")
print()
print(" " + ui.c("✻", ui.YELLOW) + " " + ui.c("10:42:11", ui.DGRAY) + " "
      + ui.c("WARNING", ui.YELLOW, bold=True) + " 🔻 레짐 전환 확정: RANGE → TREND_DOWN")
print()
ui.wait_progress(3, status="ETH-USDT · 레짐 RANGE")
