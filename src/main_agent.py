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
from google import genai

from config import (
    OKX_BASE_URL, SYMBOL, DEMO_MODE,
    CANDLE_INTERVAL, CANDLE_LOOKBACK,
    LOOP_INTERVAL_SEC,
    SCORE_CAUTION, SCORE_WARNING, SCORE_EMERGENCY,
    MAX_LOSS_PERCENT,
    LLM_TRIGGER_SCORE, LLM_PROVIDER, LLM_API_KEY, LLM_MODEL,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, NOTIFY_ON_STATES,
    DAILY_REPORT_HOUR, MULTI_AGENT_MODE,
)
from market_analyzer import MarketAnalyzer, MarketSignal
from grid_controller import GridController
from multi_agent import MultiAgentJudge, format_consensus_for_telegram
from cost_guard import CostGuard


# ──────────────────────────────────────────────────────────────
class Notifier:
    """텔레그램 알림 발송"""

    def send(self, message: str):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            print(f"[Notifier] (Telegram 미설정) {message}")
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            httpx.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        except httpx.TimeoutException:
            print(f"[Notifier] 텔레그램 발송 타임아웃")
        except Exception as e:
            print(f"[Notifier] 텔레그램 발송 실패: {e}")


# ──────────────────────────────────────────────────────────────
class LLMJudge:
    """리스크 스코어가 애매한 상황에서 LLM에게 판단 요청. (Anthropic / OpenAI 지원)"""

    DEFAULT_MODELS = {
        "anthropic": "claude-sonnet-4-20250514",
        "openai": "gpt-4o",
        "grok": "grok-3-mini",
        "gemini": "gemini-2.5-flash",
    }

    def __init__(self):
        self.available = False
        try:
            self.provider = LLM_PROVIDER.lower()
            self.model = LLM_MODEL or self.DEFAULT_MODELS.get(self.provider, "gpt-4o")

            if not LLM_API_KEY:
                print("[LLMJudge] API 키가 설정되지 않음 — LLM 판단 비활성화")
                return

            if self.provider == "anthropic":
                self.client = anthropic.Anthropic(api_key=LLM_API_KEY)
            elif self.provider == "openai":
                self.client = openai.OpenAI(api_key=LLM_API_KEY)
            elif self.provider == "grok":
                self.client = openai.OpenAI(
                    api_key=LLM_API_KEY,
                    base_url="https://api.x.ai/v1",
                )
            elif self.provider == "gemini":
                self.client = genai.Client(api_key=LLM_API_KEY)
            else:
                print(f"[LLMJudge] 지원하지 않는 LLM provider: {self.provider} — LLM 판단 비활성화")
                return

            self.available = True
        except Exception as e:
            print(f"[LLMJudge] 초기화 실패: {e} — LLM 판단 비활성화")

    def judge(self, signal: MarketSignal, current_price: float) -> str:
        """
        Returns: "MAINTAIN" | "WIDEN" | "PAUSE" | "STOP"
        """
        if not self.available:
            return "MAINTAIN"

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
        elif self.provider == "gemini":
            resp = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={"max_output_tokens": 100},
            )
            return resp.text.strip()
        else:  # openai, grok (OpenAI 호환)
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
        try:
            resp = self.client.get(
                "/api/v5/market/candles",
                params={"instId": SYMBOL, "bar": CANDLE_INTERVAL, "limit": CANDLE_LOOKBACK}
            )
            data = resp.json().get("data", [])
            if not data:
                print("[OKXDataFetcher] 캔들 데이터가 비어있음")
                return []
            result = []
            for d in reversed(data):
                if not isinstance(d, (list, tuple)) or len(d) < 6:
                    continue
                result.append(
                    {"ts": d[0], "open": d[1], "high": d[2], "low": d[3], "close": d[4], "vol": d[5]}
                )
            return result
        except httpx.TimeoutException:
            print("[OKXDataFetcher] 캔들 요청 타임아웃")
            return []
        except (httpx.HTTPError, ConnectionError) as e:
            print(f"[OKXDataFetcher] 캔들 네트워크 오류: {e}")
            return []
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            print(f"[OKXDataFetcher] 캔들 데이터 파싱 오류: {e}")
            return []
        except Exception as e:
            print(f"[OKXDataFetcher] 캔들 조회 실패: {e}")
            return []

    def get_current_price(self) -> Optional[float]:
        try:
            resp = self.client.get(
                "/api/v5/market/ticker",
                params={"instId": SYMBOL}
            )
            return float(resp.json()["data"][0]["last"])
        except httpx.TimeoutException:
            print("[OKXDataFetcher] 가격 요청 타임아웃")
            return None
        except (httpx.HTTPError, ConnectionError) as e:
            print(f"[OKXDataFetcher] 가격 네트워크 오류: {e}")
            return None
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as e:
            print(f"[OKXDataFetcher] 가격 데이터 파싱 오류: {e}")
            return None
        except Exception as e:
            print(f"[OKXDataFetcher] 가격 조회 실패: {e}")
            return None


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
        self.multi_agent = MultiAgentJudge()
        self.cost_guard  = CostGuard(model=LLM_MODEL, daily_budget=5.0)

        self.prev_state:  str   = "NORMAL"
        self.entry_price: Optional[float] = None   # 첫 진입 가격 (손절 기준)
        self.loop_count:  int   = 0
        self.last_shift_time: Optional[datetime] = None  # 그리드 시프트 쿨다운

        # 체결 감시용: 마지막으로 확인한 체결 ID
        self.last_fill_id: Optional[str] = None
        # 당일 체결 누적 (리포트용)
        self.daily_buys:   int   = 0
        self.daily_sells:  int   = 0
        self.daily_buy_vol:  float = 0.0
        self.daily_sell_vol: float = 0.0
        # 일일 리포트 발송 여부
        self._report_sent_date: Optional[str] = None

    @staticmethod
    def _print_disclaimer():
        RED = "\033[91m"
        BOLD = "\033[1m"
        RESET = "\033[0m"
        print()
        print(f"{RED}{'═' * 56}{RESET}")
        print(f"{RED}{BOLD}  ⚠️  투자 위험 경고{RESET}")
        print(f"{RED}{'═' * 56}{RESET}")
        print(f"{RED}  이 소프트웨어는 투자 조언이 아닙니다.{RESET}")
        print(f"{RED}  본 프로그램 사용으로 발생하는 모든 금전적 손실에 대한{RESET}")
        print(f"{RED}  책임은 전적으로 사용자 본인에게 있습니다.{RESET}")
        print(f"{RED}  암호화폐 거래는 원금 손실 위험이 있으며,{RESET}")
        print(f"{RED}  과거 수익이 미래 수익을 보장하지 않습니다.{RESET}")
        print(f"{RED}  반드시 감당 가능한 금액만 투자하세요.{RESET}")
        print(f"{RED}{'═' * 56}{RESET}")
        print()

    def run(self):
        """무한 루프 실행."""
        self._print_disclaimer()
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
            except SystemExit:
                self._log("시스템 종료 요청")
                self.notifier.send("⛔ Grid Agent 시스템 종료")
                break
            except Exception as e:
                self._log(f"루프 오류: {e}", level="ERROR")
                try:
                    self.notifier.send(f"❌ Agent 오류: {e}")
                except Exception:
                    pass

            try:
                time.sleep(LOOP_INTERVAL_SEC)
            except KeyboardInterrupt:
                self._log("사용자 중단 요청 (sleep 중)")
                self.notifier.send("⛔ Grid Agent 수동 종료")
                break

    # ─── 단일 루프 ─────────────────────────────────────────

    def _tick(self):
        self.loop_count += 1
        ts = datetime.now().strftime("%H:%M:%S")
        DIM = "\033[2m"
        RESET = "\033[0m"
        CYAN = "\033[96m"
        YELLOW = "\033[93m"
        GREEN = "\033[92m"
        RED = "\033[91m"
        MAGENTA = "\033[95m"
        BOLD = "\033[1m"

        print()
        print(f"{CYAN}{BOLD}{'═' * 60}{RESET}")
        print(f"{CYAN}{BOLD}  TICK #{self.loop_count}  [{ts}]  {SYMBOL}{RESET}")
        print(f"{CYAN}{'═' * 60}{RESET}")

        # 1. 데이터 수집
        print(f"\n{DIM}[1/9]{RESET} {BOLD}데이터 수집{RESET} ─ OKX API 호출 중...")
        try:
            candles = self.fetcher.get_candles()
            price   = self.fetcher.get_current_price()
        except Exception as e:
            print(f"  {RED}✗ 실패: {e}{RESET}")
            return

        if price is None:
            print(f"  {RED}✗ 현재 가격 조회 불가 — 스킵{RESET}")
            return
        if not candles:
            print(f"  {RED}✗ 캔들 데이터 없음 — 스킵{RESET}")
            return
        print(f"  {GREEN}✓{RESET} 현재가: {BOLD}{price:,.0f} USDT{RESET} | 캔들: {len(candles)}개")

        # 2. 리스크 분석
        print(f"\n{DIM}[2/9]{RESET} {BOLD}리스크 분석{RESET} ─ ATR / RSI / BB / Volume / EMA / ADX")
        try:
            signal = self.analyzer.analyze(candles)
        except Exception as e:
            print(f"  {RED}✗ 분석 실패: {e}{RESET}")
            return

        trend = getattr(signal, "trend", "N/A")
        trend_strength = getattr(signal, "trend_strength", 0.0)
        ema_s = getattr(signal, "ema_short", 0)
        ema_l = getattr(signal, "ema_long", 0)
        state_emoji = {"NORMAL": "🟢", "CAUTION": "🟡", "WARNING": "🟠", "EMERGENCY": "🔴"}
        emoji = state_emoji.get(signal.state, "⚪")

        print(f"  ┌────────────────────────────────────────────┐")
        print(f"  │ ATR  = {signal.atr_score:>5.1f}/30  (현재={signal.atr_current:.1f} 평균={signal.atr_avg:.1f})")
        print(f"  │ RSI  = {signal.rsi_score:>5.1f}/25  (RSI={signal.rsi:.1f})")
        print(f"  │ BB   = {signal.bb_score:>5.1f}/25  (폭={signal.bb_width:.2f}%)")
        print(f"  │ Vol  = {signal.volume_score:>5.1f}/20  (배율={signal.volume_ratio:.1f}x)")
        print(f"  ├────────────────────────────────────────────┤")

        trend_color = GREEN if trend == "BULLISH" else RED if trend == "BEARISH" else YELLOW
        print(f"  │ 추세 = {trend_color}{BOLD}{trend}{RESET}  (ADX={trend_strength:.1f})")
        print(f"  │ EMA  = 단기 {ema_s:,.1f} / 장기 {ema_l:,.1f}")
        print(f"  ├────────────────────────────────────────────┤")

        score_color = GREEN if signal.risk_score <= 30 else YELLOW if signal.risk_score <= 60 else RED
        print(f"  │ {BOLD}총점 = {score_color}{signal.risk_score:.1f}/100{RESET}  →  {emoji} {signal.state}")
        print(f"  └────────────────────────────────────────────┘")

        # 3. 손절 체크
        print(f"\n{DIM}[3/9]{RESET} {BOLD}손절 조건 체크{RESET} ─ 진입가 대비 {MAX_LOSS_PERCENT}% 이상 손실?")
        try:
            if self._check_stop_loss(price):
                print(f"  {RED}{BOLD}✗ 손절 조건 도달! 긴급 청산 실행{RESET}")
                self.controller.emergency_stop()
                self.notifier.send(f"💀 손절 청산 | {SYMBOL} | 현재가={price:,.0f}")
                return
        except Exception as e:
            print(f"  {RED}✗ 체크 실패: {e}{RESET}")

        if self.entry_price:
            loss_pct = (self.entry_price - price) / self.entry_price * 100
            print(f"  {GREEN}✓{RESET} 진입가={self.entry_price:,.0f} | 현재 손익={-loss_pct:+.2f}% | 한도={MAX_LOSS_PERCENT}%")
        else:
            print(f"  {GREEN}✓{RESET} 정상 (진입가 미설정)")

        # 4. 체결 감시
        print(f"\n{DIM}[4/9]{RESET} {BOLD}체결 내역 감시{RESET} ─ 신규 매수/매도 확인")
        try:
            self._check_fills(price)
            print(f"  {GREEN}✓{RESET} 체결 확인 완료 (당일 매수={self.daily_buys} 매도={self.daily_sells})")
        except Exception as e:
            print(f"  {RED}✗ 감시 실패: {e}{RESET}")

        # 5. 의사결정
        print(f"\n{DIM}[5/9]{RESET} {BOLD}의사결정{RESET} ─ 추세 판단 → 리스크 스코어 → 에이전트 합의")
        action = "MAINTAIN"
        try:
            action = self._decide_action(signal, price)
        except Exception as e:
            print(f"  {RED}✗ 결정 실패, MAINTAIN 유지: {e}{RESET}")

        action_colors = {
            "MAINTAIN": GREEN, "WIDEN": YELLOW, "PAUSE": YELLOW,
            "STOP": RED, "REDUCE": YELLOW, "SHIFT_UP": CYAN, "SHIFT_DOWN": CYAN
        }
        ac = action_colors.get(action, RESET)
        print(f"  {BOLD}→ 결정: {ac}{action}{RESET}")

        # 6. 액션 실행
        print(f"\n{DIM}[6/9]{RESET} {BOLD}액션 실행{RESET} ─ {ac}{action}{RESET}")
        try:
            self._execute(action, signal, price)
            print(f"  {GREEN}✓{RESET} 실행 완료")
        except Exception as e:
            print(f"  {RED}✗ 실행 실패: {e}{RESET}")

        # 7. 일일 리포트
        print(f"\n{DIM}[7/9]{RESET} {BOLD}일일 리포트 체크{RESET} ─ {DAILY_REPORT_HOUR}시 발송")
        try:
            self._check_daily_report(price)
            sent = "발송됨" if self._report_sent_date == datetime.now().strftime("%Y-%m-%d") else "미발송"
            print(f"  {GREEN}✓{RESET} {sent}")
        except Exception as e:
            print(f"  {RED}✗ 실패: {e}{RESET}")

        # 8. 상태 변화 알림
        print(f"\n{DIM}[8/9]{RESET} {BOLD}상태 변화 감지{RESET} ─ {self.prev_state} → {signal.state}")
        try:
            if signal.state != self.prev_state:
                if signal.state in NOTIFY_ON_STATES:
                    self.notifier.send(
                        f"{emoji} 상태 변화: {self.prev_state} → {signal.state}\n"
                        f"리스크 점수: {signal.risk_score}/100\n"
                        f"추세: {trend}(ADX={trend_strength:.1f})\n"
                        f"{signal.reason}\n"
                        f"현재가: {price:,.0f}\n"
                        f"액션: {action}"
                    )
                    print(f"  {YELLOW}⚡ 상태 변화 알림 발송: {self.prev_state} → {signal.state}{RESET}")
                else:
                    print(f"  {DIM}상태 변화 (알림 대상 아님): {self.prev_state} → {signal.state}{RESET}")
                self.prev_state = signal.state
            else:
                print(f"  {DIM}변화 없음 ({signal.state}){RESET}")
        except Exception as e:
            print(f"  {RED}✗ 알림 실패: {e}{RESET}")

        # 9. 비용 현황
        print(f"\n{DIM}[9/10]{RESET} {BOLD}비용 현황{RESET}")
        for line in self.cost_guard.status_report().split("\n"):
            print(f"  {DIM}{line}{RESET}")

        # 10. 요약
        print(f"\n{DIM}[10/10]{RESET} {BOLD}틱 완료{RESET}")
        print(f"  {emoji} {signal.state} | {score_color}{signal.risk_score:.1f}/100{RESET} | "
              f"{trend_color}{trend}(ADX={trend_strength:.1f}){RESET} | "
              f"{ac}{action}{RESET} | {price:,.0f} USDT")
        print(f"\n{DIM}  다음 틱까지 {LOOP_INTERVAL_SEC}초 대기...{RESET}")
        print(f"{CYAN}{'─' * 60}{RESET}")

    # ─── 의사결정 ──────────────────────────────────────────

    def _decide_action(self, signal: MarketSignal, price: float) -> str:
        """상태 머신으로 기본 액션 결정, 트렌드 감지 및 LLM 위임 포함."""

        score = signal.risk_score
        trend = getattr(signal, "trend", "SIDEWAYS")
        trend_strength = getattr(signal, "trend_strength", 0.0)

        # ── 트렌드 기반 조기 판단 (리스크 스코어 전에 체크) ──
        if trend == "BEARISH":
            if trend_strength >= 50:
                self._log(f"강한 하락 추세 감지 (ADX={trend_strength:.1f}) → PAUSE")
                return "PAUSE"
            if trend_strength >= 30:
                self._log(f"하락 추세 감지 (ADX={trend_strength:.1f}) → REDUCE")
                return "REDUCE"

        # 점수가 애매한 구간 (CAUTION 경계) → 비용 가드 체크 후 LLM 호출
        if LLM_TRIGGER_SCORE <= score <= SCORE_WARNING:
            # CostGuard: 예산/서킷/캐시/감소수익 체크
            should_call, reason, cached_action = self.cost_guard.pre_check(signal)

            if not should_call:
                self._log(f"💰 CostGuard 스킵: {reason} → {cached_action}")
                return cached_action

            # 실제 LLM 호출
            try:
                if MULTI_AGENT_MODE and self.multi_agent.available:
                    result = self.multi_agent.judge_with_detail(signal, price)
                    self._log(
                        f"멀티 에이전트 합의: {result.final_action} "
                        f"(동의율={result.agreement_rate:.0f}%, score={score})"
                    )
                    self.notifier.send(format_consensus_for_telegram(result))
                    # 성공 기록 (멀티 에이전트 = 5회 호출)
                    self.cost_guard.post_success(signal, result.final_action, num_calls=5)
                    return result.final_action
                else:
                    llm_action = self.llm_judge.judge(signal, price)
                    self._log(f"LLM 단독 판단: {llm_action} (score={score})")
                    # 성공 기록 (단일 LLM = 1회 호출)
                    self.cost_guard.post_success(signal, llm_action, num_calls=1)
                    return llm_action
            except Exception as e:
                self._log(f"LLM 호출 실패: {e} → 에러 복구 캐스케이드", level="ERROR")
                self.cost_guard.post_failure()

                # 에러 복구 캐스케이드: 무료 → 저비용 → 고비용
                level = self.cost_guard.recovery.next_strategy()
                if level <= 1:
                    # Level 0-1: 룰 베이스 폴백 (무료)
                    fallback = self.cost_guard.recovery.rule_based_fallback(
                        score, trend, trend_strength
                    )
                    self._log(f"복구 Level {level}: 룰 베이스 → {fallback}")
                    return fallback
                elif level == 2:
                    # Level 2: 단일 LLM 재시도 (저비용)
                    try:
                        llm_action = self.llm_judge.judge(signal, price)
                        self._log(f"복구 Level 2: 단일 LLM → {llm_action}")
                        self.cost_guard.post_success(signal, llm_action, num_calls=1)
                        return llm_action
                    except Exception:
                        pass
                # Level 3 이상: 최종 룰 베이스 폴백
                return self.cost_guard.recovery.rule_based_fallback(
                    score, trend, trend_strength
                )

        # 명확한 구간은 룰 베이스로 결정
        if score <= SCORE_CAUTION:
            action = "MAINTAIN"
        elif score <= SCORE_WARNING:
            action = "WIDEN"
        elif score <= SCORE_EMERGENCY:
            action = "PAUSE"
        else:
            return "STOP"

        # ── MAINTAIN 시 트렌드 기반 그리드 시프트 ──
        if action == "MAINTAIN" and trend_strength >= 25:
            # 쿨다운 체크: 마지막 시프트 이후 10분 경과 필요
            now = datetime.now()
            shift_allowed = (
                self.last_shift_time is None
                or (now - self.last_shift_time).total_seconds() >= 600
            )

            if shift_allowed:
                grid_lower = getattr(self.controller, "current_lower", None)
                grid_upper = getattr(self.controller, "current_upper", None)

                if grid_lower is not None and grid_upper is not None:
                    grid_range = grid_upper - grid_lower
                    upper_threshold = grid_upper - grid_range * 0.2
                    lower_threshold = grid_lower + grid_range * 0.2

                    if trend == "BULLISH" and price >= upper_threshold:
                        self._log(f"상승 추세 + 그리드 상단 진입 (ADX={trend_strength:.1f}) → SHIFT_UP")
                        return "SHIFT_UP"
                    elif trend == "BEARISH" and price <= lower_threshold:
                        self._log(f"하락 추세 + 그리드 하단 진입 (ADX={trend_strength:.1f}) → SHIFT_DOWN")
                        return "SHIFT_DOWN"

        return action

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

        elif action == "REDUCE":
            try:
                self.controller.reduce_exposure()
                trend_strength = getattr(signal, "trend_strength", 0.0)
                self.notifier.send(
                    f"⚠️ 매수 주문 축소 | {SYMBOL}\n"
                    f"추세: BEARISH (ADX={trend_strength:.1f})\n"
                    f"현재가: {price:,.0f}"
                )
            except Exception as e:
                self._log(f"REDUCE 실행 실패: {e}", level="ERROR")

        elif action == "SHIFT_UP":
            try:
                grid_lower = getattr(self.controller, "current_lower", None)
                grid_upper = getattr(self.controller, "current_upper", None)
                if grid_lower is not None and grid_upper is not None:
                    grid_range = grid_upper - grid_lower
                    offset = grid_range * 0.1
                    new_center = price + offset
                    self.controller.shift_grid_center(new_center, price)
                    self.last_shift_time = datetime.now()
                    trend_strength = getattr(signal, "trend_strength", 0.0)
                    self.notifier.send(
                        f"📈 그리드 상향 시프트 | {SYMBOL}\n"
                        f"추세: BULLISH (ADX={trend_strength:.1f})\n"
                        f"새 중심: {new_center:,.0f}\n"
                        f"현재가: {price:,.0f}"
                    )
            except Exception as e:
                self._log(f"SHIFT_UP 실행 실패: {e}", level="ERROR")

        elif action == "SHIFT_DOWN":
            try:
                grid_lower = getattr(self.controller, "current_lower", None)
                grid_upper = getattr(self.controller, "current_upper", None)
                if grid_lower is not None and grid_upper is not None:
                    grid_range = grid_upper - grid_lower
                    offset = grid_range * 0.1
                    new_center = price - offset
                    self.controller.shift_grid_center(new_center, price)
                    self.last_shift_time = datetime.now()
                    trend_strength = getattr(signal, "trend_strength", 0.0)
                    self.notifier.send(
                        f"📉 그리드 하향 시프트 | {SYMBOL}\n"
                        f"추세: BEARISH (ADX={trend_strength:.1f})\n"
                        f"새 중심: {new_center:,.0f}\n"
                        f"현재가: {price:,.0f}"
                    )
            except Exception as e:
                self._log(f"SHIFT_DOWN 실행 실패: {e}", level="ERROR")

        elif action == "STOP":
            self.controller.emergency_stop()
            self.notifier.send(
                f"🔴 긴급 청산 완료 | {SYMBOL}\n"
                f"리스크 점수: {signal.risk_score}/100\n"
                f"사유: {signal.reason}"
            )

    # ─── 체결 감시 ─────────────────────────────────────────

    def _check_fills(self, current_price: float):
        """새로운 체결 내역을 감지하고 텔레그램으로 알림."""
        try:
            fills = self.controller.get_recent_fills(limit=10)
        except Exception as e:
            self._log(f"체결 내역 조회 실패: {e}", level="ERROR")
            return

        if not fills or not isinstance(fills, list):
            return

        # 첫 실행 시 마지막 ID만 기록
        if self.last_fill_id is None:
            self.last_fill_id = fills[0].get("tradeId", "") if isinstance(fills[0], dict) else ""
            return

        # 새 체결만 필터링 (최신순으로 오므로 last_fill_id 이전까지)
        new_fills = []
        for f in fills:
            if not isinstance(f, dict):
                continue
            if f.get("tradeId", "") == self.last_fill_id:
                break
            new_fills.append(f)

        if not new_fills:
            return

        self.last_fill_id = new_fills[0].get("tradeId", "")

        for f in reversed(new_fills):
            try:
                side = f.get("side", "")
                px   = float(f.get("fillPx", 0))
                sz   = float(f.get("fillSz", 0))
                fee  = float(f.get("fee", 0))
            except (ValueError, TypeError) as e:
                self._log(f"체결 데이터 파싱 오류: {e} | data={f}", level="ERROR")
                continue

            if side == "buy":
                emoji = "🟢"
                label = "매수"
                self.daily_buys += 1
                self.daily_buy_vol += sz
            else:
                emoji = "🔴"
                label = "매도"
                self.daily_sells += 1
                self.daily_sell_vol += sz

            msg = (
                f"{emoji} {label} 체결 | {SYMBOL}\n"
                f"가격: {px:,.2f} USDT\n"
                f"수량: {sz}\n"
                f"수수료: {fee:.6f}\n"
                f"현재가: {current_price:,.0f}"
            )
            self.notifier.send(msg)
            self._log(f"{emoji} {label} 체결 | 가격={px:,.2f} | 수량={sz}")

    # ─── 일일 리포트 ─────────────────────────────────────

    def _check_daily_report(self, current_price: float):
        """매일 지정 시간에 당일 손익 리포트를 텔레그램으로 발송."""
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")

            # 이미 오늘 보냈으면 스킵
            if self._report_sent_date == today:
                return

            # 지정 시간이 안 됐으면 스킵
            if now.hour < DAILY_REPORT_HOUR:
                return

            # 날짜가 바뀌었으면 당일 카운터 리셋
            if self._report_sent_date and self._report_sent_date != today:
                self.daily_buys = 0
                self.daily_sells = 0
                self.daily_buy_vol = 0.0
                self.daily_sell_vol = 0.0

            # PnL 조회
            pnl_available = True
            try:
                pnl = self.controller.get_grid_pnl()
                grid_profit = pnl.get("grid_profit", 0)
                float_profit = pnl.get("float_profit", 0)
                total_pnl = pnl.get("total_pnl", 0)
                investment = pnl.get("investment", 0)
                roi = (total_pnl / investment * 100) if investment > 0 else 0
            except Exception as e:
                self._log(f"PnL 조회 실패: {e}", level="ERROR")
                pnl_available = False

            if pnl_available:
                pnl_emoji = "📈" if total_pnl >= 0 else "📉"
                pnl_section = (
                    f"{pnl_emoji} 손익 현황\n"
                    f"  그리드 수익: {grid_profit:+,.2f} USDT\n"
                    f"  평가 손익: {float_profit:+,.2f} USDT\n"
                    f"  총 손익: {total_pnl:+,.2f} USDT\n"
                    f"  수익률: {roi:+.2f}%"
                )
            else:
                pnl_section = "⚠️ 손익 현황: 조회 실패"

            msg = (
                f"📊 일일 리포트 | {today}\n"
                f"{'─' * 28}\n"
                f"심볼: {SYMBOL}\n"
                f"현재가: {current_price:,.0f} USDT\n"
                f"{'─' * 28}\n"
                f"{pnl_section}\n"
                f"{'─' * 28}\n"
                f"📋 당일 체결\n"
                f"  매수: {self.daily_buys}건 ({self.daily_buy_vol:.6f})\n"
                f"  매도: {self.daily_sells}건 ({self.daily_sell_vol:.6f})\n"
                f"{'─' * 28}\n"
                f"상태: {self.prev_state}"
            )

            self.notifier.send(msg)
            if pnl_available:
                self._log(f"📊 일일 리포트 발송 | 총 손익={total_pnl:+,.2f} USDT")
            else:
                self._log("📊 일일 리포트 발송 (PnL 조회 실패, 간소화 리포트)")
            self._report_sent_date = today
        except Exception as e:
            self._log(f"일일 리포트 생성 실패: {e}", level="ERROR")

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

    try:
        result = main_menu()
    except KeyboardInterrupt:
        print("\n사용자 중단 — 종료합니다.")
        sys.exit(0)

    if result == "start":
        clear()
        # 메뉴에서 설정 변경 가능하므로 프로세스 재시작으로 config 새로 로드
        src_dir = os.path.dirname(os.path.abspath(__file__))
        os.execv(sys.executable, [
            sys.executable, "-c",
            f"import sys; sys.path.insert(0, r'{src_dir}'); "
            f"from main_agent import GridAgent; GridAgent().run()"
        ])
