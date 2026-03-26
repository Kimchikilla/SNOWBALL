"""
main_agent.py
OKX Adaptive Grid Agent 메인 루프.

실행: python main_agent.py
"""

import sys
import time
import json
import asyncio
from datetime import datetime
from typing import Optional

import httpx
import anthropic
import openai

from config import (
    OKX_BASE_URL, SYMBOL, DEMO_MODE,
    CANDLE_INTERVAL, CANDLE_LOOKBACK,
    LOOP_INTERVAL_SEC,
    SCORE_CAUTION, SCORE_WARNING, SCORE_EMERGENCY,
    MAX_LOSS_PERCENT,
    LLM_TRIGGER_SCORE, LLM_PROVIDER, LLM_API_KEY, LLM_MODEL,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, NOTIFY_ON_STATES,
)
from market_analyzer import MarketAnalyzer, MarketSignal
from grid_controller import GridController


# ──────────────────────────────────────────────────────────────
class Notifier:
    """텔레그램 알림 발송"""

    def send(self, message: str):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            print(f"[Notifier] (Telegram 미설정) {message}")
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            httpx.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=5)
        except Exception as e:
            print(f"[Notifier] 텔레그램 발송 실패: {e}")


# ──────────────────────────────────────────────────────────────
class LLMJudge:
    """리스크 스코어가 애매한 상황에서 LLM에게 판단 요청. (Anthropic / OpenAI 지원)"""

    DEFAULT_MODELS = {
        "anthropic": "claude-sonnet-4-20250514",
        "openai": "gpt-4o",
    }

    def __init__(self):
        self.provider = LLM_PROVIDER.lower()
        self.model = LLM_MODEL or self.DEFAULT_MODELS.get(self.provider, "gpt-4o")

        if self.provider == "anthropic":
            self.client = anthropic.Anthropic(api_key=LLM_API_KEY)
        elif self.provider == "openai":
            self.client = openai.OpenAI(api_key=LLM_API_KEY)
        else:
            raise ValueError(f"지원하지 않는 LLM provider: {self.provider} (anthropic 또는 openai)")

    def judge(self, signal: MarketSignal, current_price: float) -> str:
        """
        Returns: "MAINTAIN" | "WIDEN" | "PAUSE" | "STOP"
        """
        prompt = f"""
당신은 BTC/ETH 그리드 거래 전문가입니다.
현재 시장 상황을 분석하고 최적의 행동을 결정해주세요.

=== 현재 상태 ===
리스크 스코어: {signal.risk_score}/100
상태: {signal.state}
현재 가격: {current_price:,.0f} USDT

=== 세부 지표 ===
ATR (현재/평균): {signal.atr_current:.1f} / {signal.atr_avg:.1f}
RSI: {signal.rsi:.1f}
볼린저밴드 폭: {signal.bb_width:.1f}%
거래량 배율: {signal.volume_ratio:.1f}x

=== 판단 요청 ===
다음 4가지 중 하나로만 답하세요 (이유 한 줄 포함):
- MAINTAIN: 현재 그리드를 그대로 유지
- WIDEN: 그리드 간격을 넓혀서 재시작
- PAUSE: 신규 주문 중단, 기존 유지
- STOP: 전체 청산

형식: ACTION|이유
예시: WIDEN|ATR이 평균의 2.8배로 단기 급등 가능성 높음
"""
        try:
            raw = self._call(prompt)
            action = raw.split("|")[0].strip().upper()
            if action not in ("MAINTAIN", "WIDEN", "PAUSE", "STOP"):
                return "MAINTAIN"
            return action
        except Exception as e:
            print(f"[LLMJudge] 오류 ({self.provider}): {e}")
            return "MAINTAIN"

    def _call(self, prompt: str) -> str:
        if self.provider == "anthropic":
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}]
            )
            return resp.content[0].text.strip()
        else:
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}]
            )
            return resp.choices[0].message.content.strip()


# ──────────────────────────────────────────────────────────────
class OKXDataFetcher:
    """OKX Public API에서 캔들 데이터를 가져옵니다."""

    def __init__(self):
        self.client = httpx.Client(base_url=OKX_BASE_URL, timeout=10)

    def get_candles(self) -> list[dict]:
        resp = self.client.get(
            "/api/v5/market/candles",
            params={"instId": SYMBOL, "bar": CANDLE_INTERVAL, "limit": CANDLE_LOOKBACK}
        )
        data = resp.json().get("data", [])
        # OKX 반환: [[ts, open, high, low, close, vol, ...], ...]
        # 최신이 앞에 오므로 역순 정렬
        return [
            {"ts": d[0], "open": d[1], "high": d[2], "low": d[3], "close": d[4], "vol": d[5]}
            for d in reversed(data)
        ]

    def get_current_price(self) -> float:
        resp = self.client.get(
            "/api/v5/market/ticker",
            params={"instId": SYMBOL}
        )
        return float(resp.json()["data"][0]["last"])


# ──────────────────────────────────────────────────────────────
class GridAgent:
    """
    메인 오케스트레이터.

    루프마다:
    1. 시장 데이터 수집
    2. 리스크 스코어 계산
    3. 상태 결정 (NORMAL/CAUTION/WARNING/EMERGENCY)
    4. 액션 실행
    5. 알림 발송
    """

    def __init__(self):
        self.analyzer    = MarketAnalyzer()
        self.controller  = GridController()
        self.fetcher     = OKXDataFetcher()
        self.notifier    = Notifier()
        self.llm_judge   = LLMJudge()

        self.prev_state:  str   = "NORMAL"
        self.entry_price: Optional[float] = None   # 첫 진입 가격 (손절 기준)
        self.loop_count:  int   = 0

    def run(self):
        """무한 루프 실행."""
        self._log("🚀 OKX Adaptive Grid Agent 시작")
        self._log(f"   심볼: {SYMBOL} | 데모: {DEMO_MODE} | 간격: {LOOP_INTERVAL_SEC}초")
        self.notifier.send(f"🚀 Grid Agent 시작 | {SYMBOL} | Demo={DEMO_MODE}")

        # 초기 그리드 시작
        self.controller.ensure_grid_running()

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                self._log("사용자 중단 요청")
                self.notifier.send("⛔ Grid Agent 수동 종료")
                break
            except Exception as e:
                self._log(f"루프 오류: {e}", level="ERROR")
                self.notifier.send(f"❌ Agent 오류: {e}")

            time.sleep(LOOP_INTERVAL_SEC)

    # ─── 단일 루프 ─────────────────────────────────────────

    def _tick(self):
        self.loop_count += 1
        ts = datetime.now().strftime("%H:%M:%S")

        # 1. 데이터 수집
        candles = self.fetcher.get_candles()
        price   = self.fetcher.get_current_price()

        # 2. 리스크 분석
        signal  = self.analyzer.analyze(candles)

        # 3. 손절 조건 우선 체크
        if self._check_stop_loss(price):
            self._log(f"💀 손절 조건 도달 | price={price:,.0f}")
            self.controller.emergency_stop()
            self.notifier.send(f"💀 손절 청산 | {SYMBOL} | 현재가={price:,.0f}")
            return

        # 4. 점수가 애매한 구간이면 LLM 판단 요청
        action = self._decide_action(signal, price)

        # 5. 액션 실행
        self._execute(action, signal, price)

        # 6. 로그 출력
        state_emoji = {"NORMAL": "🟢", "CAUTION": "🟡", "WARNING": "🟠", "EMERGENCY": "🔴"}
        emoji = state_emoji.get(signal.state, "⚪")
        self._log(
            f"[{ts}] {emoji} {signal.reason} | 가격={price:,.0f} | 액션={action}"
        )

        # 7. 상태 변화 시 텔레그램 알림
        if signal.state != self.prev_state:
            if signal.state in NOTIFY_ON_STATES:
                self.notifier.send(
                    f"{emoji} 상태 변화: {self.prev_state} → {signal.state}\n"
                    f"리스크 점수: {signal.risk_score}/100\n"
                    f"{signal.reason}\n"
                    f"현재가: {price:,.0f}\n"
                    f"액션: {action}"
                )
            self.prev_state = signal.state

    # ─── 의사결정 ──────────────────────────────────────────

    def _decide_action(self, signal: MarketSignal, price: float) -> str:
        """상태 머신으로 기본 액션 결정, 애매한 구간은 LLM에게 위임."""

        score = signal.risk_score

        # 점수가 애매한 구간 (CAUTION 경계) → LLM 판단
        if LLM_TRIGGER_SCORE <= score <= SCORE_WARNING:
            llm_action = self.llm_judge.judge(signal, price)
            self._log(f"LLM 판단: {llm_action} (score={score})")
            return llm_action

        # 명확한 구간은 룰 베이스로 결정
        if score <= SCORE_CAUTION:
            return "MAINTAIN"
        elif score <= SCORE_WARNING:
            return "WIDEN"
        elif score <= SCORE_EMERGENCY:
            return "PAUSE"
        else:
            return "STOP"

    def _execute(self, action: str, signal: MarketSignal, price: float):
        """액션을 실제 API 호출로 변환."""

        if action == "MAINTAIN":
            # 일시정지 상태였으면 재개
            if self.controller.paused:
                self.controller.resume_grid()
            else:
                self.controller.ensure_grid_running()

        elif action == "WIDEN":
            self.controller.widen_grid(
                atr_value=signal.atr_current,
                current_price=price
            )

        elif action == "PAUSE":
            if not self.controller.paused:
                self.controller.pause_new_orders()

        elif action == "STOP":
            self.controller.emergency_stop()
            self.notifier.send(
                f"🔴 긴급 청산 완료 | {SYMBOL}\n"
                f"리스크 점수: {signal.risk_score}/100\n"
                f"사유: {signal.reason}"
            )

    # ─── 손절 체크 ─────────────────────────────────────────

    def _check_stop_loss(self, current_price: float) -> bool:
        """진입가 대비 MAX_LOSS_PERCENT 이상 손실 시 True."""
        if self.entry_price is None:
            self.entry_price = current_price
            return False
        loss_pct = (self.entry_price - current_price) / self.entry_price * 100
        return loss_pct >= MAX_LOSS_PERCENT

    # ─── 로그 ──────────────────────────────────────────────

    def _log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [{level}] {msg}")


# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    from menu import main_menu, clear

    result = main_menu()

    if result == "start":
        clear()
        # 메뉴에서 설정 변경 가능하므로 프로세스 재시작으로 config 새로 로드
        src_dir = os.path.dirname(os.path.abspath(__file__))
        os.execv(sys.executable, [
            sys.executable, "-c",
            f"import sys; sys.path.insert(0, r'{src_dir}'); "
            f"from main_agent import GridAgent; GridAgent().run()"
        ])
