"""
main_agent.py
OKX Adaptive Grid Agent 메인 루프.

실행: python main_agent.py
"""

import sys
import time
import json
import asyncio
from datetime import datetime, timedelta
from typing import Optional

import httpx
import anthropic
import openai
from google import genai

import config
from market_analyzer import MarketAnalyzer, MarketSignal
from grid_controller import GridController
from multi_agent import MultiAgentJudge, format_consensus_for_telegram
from cost_guard import CostGuard


# ──────────────────────────────────────────────────────────────
class Notifier:
    """텔레그램 알림 발송"""

    def send(self, message: str):
        # 항상 터미널에도 출력
        ts = datetime.now().strftime("%H:%M:%S")
        DIM = "\033[2m"
        RESET = "\033[0m"
        print(f"{DIM}[{ts}] [TG →]{RESET}")
        for line in message.split("\n"):
            print(f"  {DIM}{line}{RESET}")

        if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
            return
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        try:
            httpx.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message}, timeout=10)
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
            self.provider = config.LLM_PROVIDER.lower()
            self.model = config.LLM_MODEL or self.DEFAULT_MODELS.get(self.provider, "gpt-4o")

            if not config.LLM_API_KEY:
                print("[LLMJudge] API 키가 설정되지 않음 — LLM 판단 비활성화")
                return

            if self.provider == "anthropic":
                self.client = anthropic.Anthropic(api_key=config.LLM_API_KEY)
            elif self.provider == "openai":
                self.client = openai.OpenAI(api_key=config.LLM_API_KEY)
            elif self.provider == "grok":
                self.client = openai.OpenAI(
                    api_key=config.LLM_API_KEY,
                    base_url="https://api.x.ai/v1",
                )
            elif self.provider == "gemini":
                self.client = genai.Client(api_key=config.LLM_API_KEY)
            else:
                print(f"[LLMJudge] 지원하지 않는 LLM provider: {self.provider} — LLM 판단 비활성화")
                return

            self.available = True
        except Exception as e:
            print(f"[LLMJudge] 초기화 실패: {e} — LLM 판단 비활성화")

    def judge(self, signal: MarketSignal, current_price: float,
              fee_context: str = "") -> str:
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
{fee_context}
=== 판단 요청 ===
다음 4가지 중 하나로만 답하세요 (이유 한 줄 포함):
- MAINTAIN: 현재 그리드를 그대로 유지
- WIDEN: 그리드 간격을 넓혀서 재시작 (수수료 발생!)
- PAUSE: 신규 주문 중단, 기존 유지
- STOP: 전체 청산

수수료가 예상 수익보다 크면 MAINTAIN을 우선하세요.

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
        self.client = httpx.Client(base_url=config.OKX_BASE_URL, timeout=10)

    def get_candles(self) -> list[dict]:
        try:
            resp = self.client.get(
                "/api/v5/market/candles",
                params={"instId": config.SYMBOL, "bar": config.CANDLE_INTERVAL, "limit": config.CANDLE_LOOKBACK}
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
                params={"instId": config.SYMBOL}
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
        self.cost_guard  = CostGuard(model=config.LLM_MODEL, daily_budget=5.0)

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
        self.daily_buy_cost: float = 0.0   # 당일 매수 총 비용 (USDT)
        self.daily_sell_revenue: float = 0.0  # 당일 매도 총 수익 (USDT)
        self.daily_fees:     float = 0.0   # 당일 수수료 합계
        # 포지션 추적 (매수 평균가 기반 손익 계산)
        self.holding_qty:    float = 0.0   # 보유 수량
        self.holding_cost:   float = 0.0   # 보유분 총 매수 비용 (USDT)
        self.realized_pnl:   float = 0.0   # 누적 실현 손익
        self.daily_realized: float = 0.0   # 당일 실현 손익
        # 일일 리포트 발송 여부
        self._report_sent_date: Optional[str] = None
        # 그리드 재시작 추적 (수수료 가드용)
        self.grid_restart_times: list = []   # 재시작 시각 기록
        self.grid_restart_count: int = 0     # 당일 재시작 횟수
        self.total_fees_paid: float = 0.0    # 누적 수수료
        # 그리드 이탈 추적
        self.grid_breakout_time: Optional[datetime] = None  # 이탈 시작 시각
        self.grid_breakout_dir: Optional[str] = None        # "ABOVE" | "BELOW"
        self.grid_breakout_notified: bool = False            # 이탈 알림 발송 여부
        self.BREAKOUT_WAIT_SEC: int = 6 * 3600               # 재배치 판단까지 대기 (6시간)

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
        self._log(f"   심볼: {config.SYMBOL} | 데모: {config.DEMO_MODE} | 간격: {config.LOOP_INTERVAL_SEC}초")
        self.notifier.send(f"🚀 Grid Agent 시작 | {config.SYMBOL} | Demo={config.DEMO_MODE}")

        # 초기 그리드: 기존 봇 동기화 시도 → 없으면 새로 시작
        resp = self.controller.ensure_grid_running()

        GREEN = "\033[92m"
        CYAN = "\033[96m"
        RED = "\033[91m"
        BOLD = "\033[1m"
        RESET = "\033[0m"

        if resp.get("status") == "synced":
            # 기존 봇에 동기화 성공
            print(f"\n{GREEN}{BOLD}{'═' * 56}{RESET}")
            print(f"{GREEN}{BOLD}  ✅ 기존 그리드봇에 연결되었습니다{RESET}")
            print(f"{GREEN}{'═' * 56}{RESET}")
            print(f"  봇 ID    : {resp.get('bot_id')}")
            print(f"  상태     : {resp.get('state')}")
            print(f"  범위     : {resp.get('lower'):,.2f} ~ {resp.get('upper'):,.2f}")
            print(f"  그리드   : {resp.get('grid_num')}개 ({resp.get('mode')})")
            print(f"  투자금   : {resp.get('investment'):,.2f} USDT")
            pnl = resp.get('total_pnl', 0)
            pnl_color = GREEN if pnl >= 0 else RED
            print(f"  현재 손익 : {pnl_color}{pnl:+,.2f} USDT{RESET}")
            print(f"{GREEN}{'─' * 56}{RESET}")
            print(f"  {CYAN}기존 설정에 맞춰 에이전트를 시작합니다.{RESET}\n")

            # 진입가를 현재가로 세팅 (손절 기준)
            entry = self.fetcher.get_current_price()
            if entry:
                self.entry_price = entry

            pnl_emoji = "📈" if pnl >= 0 else "📉"
            entry_str = f"{self.entry_price:,.2f}" if self.entry_price else "N/A"
            self.notifier.send(
                f"🔄 기존 그리드봇 연결 | {config.SYMBOL}\n"
                f"{'─' * 28}\n"
                f"봇 ID   : {resp.get('bot_id')}\n"
                f"상태    : {resp.get('state')}\n"
                f"모드    : {'Demo (모의거래)' if config.DEMO_MODE else '⚠ Live (실거래)'}\n"
                f"{'─' * 28}\n"
                f"범위    : {resp.get('lower'):,.2f} ~ {resp.get('upper'):,.2f}\n"
                f"그리드  : {resp.get('grid_num')}개 ({resp.get('mode')})\n"
                f"투자금  : {resp.get('investment'):,.2f} USDT\n"
                f"현재가  : {entry_str} USDT\n"
                f"{'─' * 28}\n"
                f"{pnl_emoji} 손익: {pnl:+,.2f} USDT\n"
                f"{'─' * 28}\n"
                f"루프 간격: {config.LOOP_INTERVAL_SEC}초\n"
                f"손절 기준: {config.MAX_LOSS_PERCENT}%"
            )

        elif resp.get("code") == "0":
            # 새 봇 시작 성공
            self._log("✅ 새 그리드봇 시작 성공")
            self.notifier.send(
                f"🚀 새 그리드봇 시작 | {config.SYMBOL}\n"
                f"{'─' * 28}\n"
                f"봇 ID   : {self.controller.bot_id}\n"
                f"모드    : {'Demo (모의거래)' if config.DEMO_MODE else '⚠ Live (실거래)'}\n"
                f"{'─' * 28}\n"
                f"범위    : {config.GRID_LOWER:,.2f} ~ {config.GRID_UPPER:,.2f}\n"
                f"그리드  : {config.GRID_COUNT}개 ({config.GRID_MODE})\n"
                f"예산    : {config.GRID_BUDGET:,.2f} USDT\n"
                f"{'─' * 28}\n"
                f"루프 간격: {config.LOOP_INTERVAL_SEC}초\n"
                f"손절 기준: {config.MAX_LOSS_PERCENT}%"
            )

        else:
            # 시작 실패
            error_msg = ""
            if isinstance(resp.get("data"), list) and resp["data"]:
                error_msg = resp["data"][0].get("sMsg", "")

            print(f"\n{RED}{BOLD}{'═' * 56}{RESET}")
            print(f"{RED}{BOLD}  ❌ 그리드봇 시작 실패{RESET}")
            print(f"{RED}{'═' * 56}{RESET}")

            if "Insufficient balance" in str(error_msg):
                print(f"{RED}  원인: 잔고 부족 (Insufficient balance){RESET}")
                print(f"{RED}  현재 설정 예산: {config.GRID_BUDGET} USDT{RESET}")
                print()
                print(f"  💡 해결 방법:")
                if config.DEMO_MODE:
                    print(f"     1. OKX 데모 계정에 충분한 USDT를 충전하세요")
                    print(f"        (okx.com → 데모 트레이딩 → 자산 → 충전)")
                    print(f"     2. 또는 설정에서 그리드 예산을 줄여주세요")
                else:
                    print(f"     1. OKX 계정에 충분한 USDT를 입금하세요")
                    print(f"     2. 또는 설정에서 그리드 예산을 줄여주세요")
            else:
                print(f"{RED}  에러: {error_msg or resp}{RESET}")

            print(f"{RED}{'═' * 56}{RESET}")
            print(f"\n  프로그램을 종료합니다. 문제를 해결한 후 다시 실행해주세요.\n")
            self.notifier.send(f"❌ 그리드봇 시작 실패: {error_msg}")
            sys.exit(1)

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
                    self.notifier.send(f"❌ Agent 오류 발생 (상세 내용은 터미널 확인)")
                except Exception:
                    pass

            try:
                self._wait_with_progress(config.LOOP_INTERVAL_SEC)
            except KeyboardInterrupt:
                self._log("사용자 중단 요청 (대기 중)")
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
        print(f"{CYAN}{BOLD}  TICK #{self.loop_count}  [{ts}]  {config.SYMBOL}{RESET}")
        print(f"{CYAN}{'═' * 60}{RESET}")

        # 1. 데이터 수집
        print(f"\n{DIM}[1/10]{RESET} {BOLD}데이터 수집{RESET} ─ OKX API 호출 중...")
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
        print(f"\n{DIM}[2/10]{RESET} {BOLD}리스크 분석{RESET} ─ ATR / RSI / BB / Volume / EMA / ADX")
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
        print(f"\n{DIM}[3/10]{RESET} {BOLD}손절 조건 체크{RESET} ─ 진입가 대비 {config.MAX_LOSS_PERCENT}% 이상 손실?")
        try:
            if self._check_stop_loss(price):
                print(f"  {RED}{BOLD}✗ 손절 조건 도달! 긴급 청산 실행{RESET}")
                self.controller.emergency_stop()
                self.notifier.send(f"💀 손절 청산 | {config.SYMBOL} | 현재가={price:,.0f}")
                return
        except Exception as e:
            print(f"  {RED}✗ 체크 실패: {e}{RESET}")

        if self.entry_price:
            loss_pct = (self.entry_price - price) / self.entry_price * 100
            print(f"  {GREEN}✓{RESET} 진입가={self.entry_price:,.0f} | 현재 손익={-loss_pct:+.2f}% | 한도={config.MAX_LOSS_PERCENT}%")
        else:
            print(f"  {GREEN}✓{RESET} 정상 (진입가 미설정)")

        # 4. 체결 감시
        print(f"\n{DIM}[4/10]{RESET} {BOLD}체결 내역 감시{RESET} ─ 신규 매수/매도 확인")
        try:
            self._check_fills(price)
            # 미실현 손익 계산
            unrealized = 0.0
            if self.holding_qty > 0 and price:
                avg_buy = self.holding_cost / self.holding_qty
                unrealized = (price - avg_buy) * self.holding_qty
            total_day = self.daily_realized + unrealized
            pnl_c = GREEN if total_day >= 0 else RED
            print(f"  {GREEN}✓{RESET} 매수={self.daily_buys} 매도={self.daily_sells} | "
                  f"보유={self.holding_qty:.6f} | "
                  f"실현={self.daily_realized:+,.4f} | "
                  f"미실현={unrealized:+,.4f} | "
                  f"{pnl_c}합계={total_day:+,.4f}{RESET}")
        except Exception as e:
            print(f"  {RED}✗ 감시 실패: {e}{RESET}")

        # 4.5 그리드 이탈 체크
        breakout_action = self._check_grid_breakout(signal, price)
        if breakout_action is not None:
            gl = self.controller.current_lower
            gu = self.controller.current_upper
            elapsed_str = ""
            if self.grid_breakout_time:
                elapsed = (datetime.now() - self.grid_breakout_time).total_seconds()
                elapsed_str = f" | 이탈 {elapsed/60:.0f}분"
            print(f"  {YELLOW}⚠ 그리드 이탈 감지{RESET} | "
                  f"범위: {gl:,.2f}~{gu:,.2f} | "
                  f"현재가: {price:,.2f}"
                  f"{elapsed_str} → {breakout_action}")
            action = breakout_action
            # 바로 실행으로 점프
            action_colors = {
                "MAINTAIN": GREEN, "WIDEN": YELLOW, "PAUSE": YELLOW,
                "STOP": RED, "REDUCE": YELLOW, "SHIFT_UP": CYAN, "SHIFT_DOWN": CYAN
            }
            ac = action_colors.get(action, RESET)
            print(f"\n{DIM}[6/10]{RESET} {BOLD}액션 실행{RESET} ─ {ac}{action}{RESET}")
            try:
                self._execute(action, signal, price)
                print(f"  {GREEN}✓{RESET} 실행 완료")
            except Exception as e:
                print(f"  {RED}✗ 실행 실패: {e}{RESET}")
            # 나머지 스텝 계속 (리포트, 상태변화 등)
            self._post_action_steps(signal, price, action, trend, trend_strength,
                                     emoji, score_color, trend_color, ac,
                                     DIM, RESET, BOLD, GREEN, RED, YELLOW, CYAN)
            return

        # 5. 의사결정
        print(f"\n{DIM}[5/10]{RESET} {BOLD}의사결정{RESET} ─ 추세 판단 → 리스크 스코어 → 에이전트 합의")
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
        print(f"\n{DIM}[6/10]{RESET} {BOLD}액션 실행{RESET} ─ {ac}{action}{RESET}")
        try:
            self._execute(action, signal, price)
            print(f"  {GREEN}✓{RESET} 실행 완료")
        except Exception as e:
            print(f"  {RED}✗ 실행 실패: {e}{RESET}")

        self._post_action_steps(signal, price, action, trend, trend_strength,
                                 emoji, score_color, trend_color, ac,
                                 DIM, RESET, BOLD, GREEN, RED, YELLOW, CYAN)

    def _post_action_steps(self, signal, price, action, trend, trend_strength,
                            emoji, score_color, trend_color, ac,
                            DIM, RESET, BOLD, GREEN, RED, YELLOW, CYAN):
        """틱의 7~10 스텝 (일일 리포트, 상태 변화, 비용, 요약)."""
        # 7. 일일 리포트
        print(f"\n{DIM}[7/10]{RESET} {BOLD}일일 리포트 체크{RESET} ─ {config.DAILY_REPORT_HOUR}시 발송")
        try:
            self._check_daily_report(price)
            sent = "발송됨" if self._report_sent_date == datetime.now().strftime("%Y-%m-%d") else "미발송"
            print(f"  {GREEN}✓{RESET} {sent}")
        except Exception as e:
            print(f"  {RED}✗ 실패: {e}{RESET}")

        # 8. 상태 변화 알림
        print(f"\n{DIM}[8/10]{RESET} {BOLD}상태 변화 감지{RESET} ─ {self.prev_state} → {signal.state}")
        try:
            if signal.state != self.prev_state:
                if signal.state in config.NOTIFY_ON_STATES:
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

        # 10. 요약 + 텔레그램 틱 리포트
        print(f"\n{DIM}[10/10]{RESET} {BOLD}틱 완료{RESET}")
        summary_line = (
            f"{emoji} {signal.state} | {score_color}{signal.risk_score:.1f}/100{RESET} | "
            f"{trend_color}{trend}(ADX={trend_strength:.1f}){RESET} | "
            f"{ac}{action}{RESET} | {price:,.0f} USDT"
        )
        print(f"  {summary_line}")

        # 매 틱 텔레그램 발송
        self._send_tick_report(signal, price, action, trend, trend_strength)
        print(f"{CYAN}{'─' * 60}{RESET}")

    # ─── 의사결정 ──────────────────────────────────────────

    def _decide_action(self, signal: MarketSignal, price: float) -> str:
        """상태 머신으로 기본 액션 결정, 트렌드 감지 및 LLM 위임 포함."""

        score = signal.risk_score
        trend = getattr(signal, "trend", "SIDEWAYS")
        trend_strength = getattr(signal, "trend_strength", 0.0)

        # 에이전트 호출 조건:
        # 1. 리스크 스코어 55~80 (기존 애매한 구간)
        # 2. 강한 추세 (ADX >= 30) — 스코어 상관없이 에이전트 판단 필요
        need_agent = (
            config.LLM_TRIGGER_SCORE <= score <= config.SCORE_WARNING
            or trend_strength >= 30
        )

        if need_agent:
            # CostGuard: 예산/서킷/캐시/감소수익 체크
            should_call, reason, cached_action = self.cost_guard.pre_check(signal)

            if not should_call:
                self._log(f"💰 CostGuard 스킵: {reason} → {cached_action}")
                return cached_action

            # 수수료 컨텍스트 생성
            fee_ctx = self._build_fee_context(price)

            # 실제 LLM 호출
            try:
                if config.MULTI_AGENT_MODE and self.multi_agent.available:
                    result = self.multi_agent.judge_with_detail(
                        signal, price, fee_context=fee_ctx
                    )
                    self._log(
                        f"멀티 에이전트 합의: {result.final_action} "
                        f"(동의율={result.agreement_rate:.0f}%, score={score})"
                    )
                    self.notifier.send(format_consensus_for_telegram(result))
                    self.cost_guard.post_success(signal, result.final_action, num_calls=5)
                    # 수수료 가드: 재시작 액션이면 체크
                    allowed, skip_reason = self._check_restart_allowed(
                        result.final_action, price
                    )
                    if not allowed:
                        self._log(f"🛡️ 수수료 가드: {skip_reason} → MAINTAIN 유지")
                        self.notifier.send(
                            f"🛡️ 그리드 조정 스킵\n"
                            f"에이전트 판단: {result.final_action}\n"
                            f"사유: {skip_reason}\n"
                            f"→ MAINTAIN 유지"
                        )
                        return "MAINTAIN"
                    return result.final_action
                else:
                    llm_action = self.llm_judge.judge(
                        signal, price, fee_context=fee_ctx
                    )
                    self._log(f"LLM 단독 판단: {llm_action} (score={score})")
                    self.cost_guard.post_success(signal, llm_action, num_calls=1)
                    # 수수료 가드
                    allowed, skip_reason = self._check_restart_allowed(
                        llm_action, price
                    )
                    if not allowed:
                        self._log(f"🛡️ 수수료 가드: {skip_reason} → MAINTAIN 유지")
                        self.notifier.send(
                            f"🛡️ 그리드 조정 스킵\n"
                            f"에이전트 판단: {llm_action}\n"
                            f"사유: {skip_reason}\n"
                            f"→ MAINTAIN 유지"
                        )
                        return "MAINTAIN"
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
        if score <= config.SCORE_CAUTION:
            action = "MAINTAIN"
        elif score <= config.SCORE_WARNING:
            action = "WIDEN"
        elif score <= config.SCORE_EMERGENCY:
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
            self._record_grid_restart()
            old_lower = self.controller.current_lower
            old_upper = self.controller.current_upper
            self.controller.widen_grid(
                atr_value=signal.atr_current,
                current_price=price
            )
            new_lower = self.controller.current_lower
            new_upper = self.controller.current_upper
            est_fee = self.holding_qty * price * 0.002 if self.holding_qty > 0 else 0
            self.notifier.send(
                f"🔄 그리드 확대 (WIDEN) | {config.SYMBOL}\n"
                f"{'─' * 28}\n"
                f"이전 범위: {old_lower:,.2f} ~ {old_upper:,.2f}\n"
                f"새 범위  : {new_lower:,.2f} ~ {new_upper:,.2f}\n"
                f"현재가   : {price:,.0f} USDT\n"
                f"ATR      : {signal.atr_current:.1f}\n"
                f"{'─' * 28}\n"
                f"예상 수수료: ~{est_fee:,.4f} USDT\n"
                f"당일 누적 수수료: {self.daily_fees:,.4f} USDT\n"
                f"당일 재시작: {self.grid_restart_count}회"
            )

        elif action == "PAUSE":
            if not self.controller.paused:
                self.controller.pause_new_orders()
                self.notifier.send(
                    f"⏸️ 신규 주문 중단 (PAUSE) | {config.SYMBOL}\n"
                    f"리스크 점수: {signal.risk_score:.1f}/100\n"
                    f"현재가: {price:,.0f} USDT\n"
                    f"사유: {signal.reason}"
                )

        elif action == "REDUCE":
            try:
                self.controller.reduce_exposure()
                trend_strength = getattr(signal, "trend_strength", 0.0)
                self.notifier.send(
                    f"⚠️ 매수 주문 축소 (REDUCE) | {config.SYMBOL}\n"
                    f"{'─' * 28}\n"
                    f"추세: BEARISH (ADX={trend_strength:.1f})\n"
                    f"현재가: {price:,.0f} USDT\n"
                    f"리스크 점수: {signal.risk_score:.1f}/100"
                )
            except Exception as e:
                self._log(f"REDUCE 실행 실패: {e}", level="ERROR")

        elif action == "SHIFT_UP":
            self._record_grid_restart()
            try:
                grid_lower = getattr(self.controller, "current_lower", None)
                grid_upper = getattr(self.controller, "current_upper", None)
                if grid_lower is not None and grid_upper is not None:
                    old_lower, old_upper = grid_lower, grid_upper
                    grid_range = grid_upper - grid_lower
                    offset = grid_range * 0.1
                    new_center = price + offset
                    self.controller.shift_grid_center(new_center, price)
                    self.last_shift_time = datetime.now()
                    trend_strength = getattr(signal, "trend_strength", 0.0)
                    est_fee = self.holding_qty * price * 0.002 if self.holding_qty > 0 else 0
                    self.notifier.send(
                        f"📈 그리드 상향 시프트 | {config.SYMBOL}\n"
                        f"{'─' * 28}\n"
                        f"이전 범위: {old_lower:,.2f} ~ {old_upper:,.2f}\n"
                        f"새 범위  : {self.controller.current_lower:,.2f} ~ {self.controller.current_upper:,.2f}\n"
                        f"새 중심  : {new_center:,.0f} USDT\n"
                        f"현재가   : {price:,.0f} USDT\n"
                        f"추세: BULLISH (ADX={trend_strength:.1f})\n"
                        f"{'─' * 28}\n"
                        f"예상 수수료: ~{est_fee:,.4f} USDT\n"
                        f"당일 누적 수수료: {self.daily_fees:,.4f} USDT\n"
                        f"당일 재시작: {self.grid_restart_count}회"
                    )
            except Exception as e:
                self._log(f"SHIFT_UP 실행 실패: {e}", level="ERROR")

        elif action == "SHIFT_DOWN":
            self._record_grid_restart()
            try:
                grid_lower = getattr(self.controller, "current_lower", None)
                grid_upper = getattr(self.controller, "current_upper", None)
                if grid_lower is not None and grid_upper is not None:
                    old_lower, old_upper = grid_lower, grid_upper
                    grid_range = grid_upper - grid_lower
                    offset = grid_range * 0.1
                    new_center = price - offset
                    self.controller.shift_grid_center(new_center, price)
                    self.last_shift_time = datetime.now()
                    trend_strength = getattr(signal, "trend_strength", 0.0)
                    est_fee = self.holding_qty * price * 0.002 if self.holding_qty > 0 else 0
                    self.notifier.send(
                        f"📉 그리드 하향 시프트 | {config.SYMBOL}\n"
                        f"{'─' * 28}\n"
                        f"이전 범위: {old_lower:,.2f} ~ {old_upper:,.2f}\n"
                        f"새 범위  : {self.controller.current_lower:,.2f} ~ {self.controller.current_upper:,.2f}\n"
                        f"새 중심  : {new_center:,.0f} USDT\n"
                        f"현재가   : {price:,.0f} USDT\n"
                        f"추세: BEARISH (ADX={trend_strength:.1f})\n"
                        f"{'─' * 28}\n"
                        f"예상 수수료: ~{est_fee:,.4f} USDT\n"
                        f"당일 누적 수수료: {self.daily_fees:,.4f} USDT\n"
                        f"당일 재시작: {self.grid_restart_count}회"
                    )
            except Exception as e:
                self._log(f"SHIFT_DOWN 실행 실패: {e}", level="ERROR")

        elif action == "STOP":
            self.controller.emergency_stop()
            self.notifier.send(
                f"🔴 긴급 청산 완료 | {config.SYMBOL}\n"
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

            cost = px * sz  # 이 체결의 USDT 금액
            self.daily_fees += abs(fee)
            self.total_fees_paid += abs(fee)

            if side == "buy":
                emoji = "🟢"
                label = "매수"
                self.daily_buys += 1
                self.daily_buy_vol += sz
                self.daily_buy_cost += cost
                # 포지션 추적: 매수 → 보유량/비용 증가
                self.holding_qty += sz
                self.holding_cost += cost
                avg_price = self.holding_cost / self.holding_qty if self.holding_qty > 0 else px
                pnl_line = f"평균 매수가: {avg_price:,.2f} USDT\n보유: {self.holding_qty:.6f}"
            else:
                emoji = "🔴"
                label = "매도"
                self.daily_sells += 1
                self.daily_sell_vol += sz
                self.daily_sell_revenue += cost
                # 포지션 추적: 매도 → 실현 손익 계산
                if self.holding_qty > 0:
                    avg_buy = self.holding_cost / self.holding_qty
                    profit = (px - avg_buy) * sz - abs(fee)
                    self.realized_pnl += profit
                    self.daily_realized += profit
                    # 보유량/비용 차감
                    sell_ratio = min(sz / self.holding_qty, 1.0)
                    self.holding_cost -= self.holding_cost * sell_ratio
                    self.holding_qty = max(self.holding_qty - sz, 0.0)
                    profit_emoji = "💰" if profit >= 0 else "💸"
                    pnl_line = (
                        f"{profit_emoji} 실현 손익: {profit:+,.4f} USDT\n"
                        f"누적 실현: {self.realized_pnl:+,.4f} USDT\n"
                        f"잔여 보유: {self.holding_qty:.6f}"
                    )
                else:
                    pnl_line = "보유: 0 (포지션 없음)"

            diff = current_price - px
            diff_pct = diff / px * 100 if px > 0 else 0
            diff_emoji = "🔺" if diff > 0 else "🔻" if diff < 0 else "▪️"

            msg = (
                f"{emoji} {label} 체결 | {config.SYMBOL}\n"
                f"{'━' * 28}\n"
                f"💵 {label} 가격 : {px:,.2f} USDT\n"
                f"📦 수량      : {sz:.6f}\n"
                f"💲 금액      : {cost:,.2f} USDT\n"
                f"🏷️ 수수료    : {abs(fee):.6f}\n"
                f"{'━' * 28}\n"
                f"{pnl_line}\n"
                f"{'━' * 28}\n"
                f"📊 현재 {config.SYMBOL} 시세\n"
                f"   {current_price:,.2f} USDT\n"
                f"   {diff_emoji} 체결가 대비 {diff:+,.2f} ({diff_pct:+.2f}%)"
            )
            self.notifier.send(msg)
            self._log(f"{emoji} {label} 체결 | 가격={px:,.2f} | 수량={sz} | 금액={cost:,.2f}")

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
            if now.hour < config.DAILY_REPORT_HOUR:
                return

            # 날짜가 바뀌었는지 체크 (리포트 발송 후 리셋)
            is_new_day = self._report_sent_date and self._report_sent_date != today

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
                f"심볼: {config.SYMBOL}\n"
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

            # 리포트 발송 후 날짜가 바뀌었으면 카운터 리셋
            if is_new_day:
                self.daily_buys = 0
                self.daily_sells = 0
                self.daily_buy_vol = 0.0
                self.daily_sell_vol = 0.0
                self.daily_buy_cost = 0.0
                self.daily_sell_revenue = 0.0
                self.daily_fees = 0.0
                self.daily_realized = 0.0
        except Exception as e:
            self._log(f"일일 리포트 생성 실패: {e}", level="ERROR")

    # ─── 수수료 컨텍스트 ─────────────────────────────────────

    def _build_fee_context(self, current_price: float) -> str:
        """에이전트에게 제공할 수수료/손익 컨텍스트."""
        # 미실현 손익
        unrealized = 0.0
        avg_buy = 0.0
        if self.holding_qty > 0:
            avg_buy = self.holding_cost / self.holding_qty
            unrealized = (current_price - avg_buy) * self.holding_qty

        # 1시간 내 그리드 재시작 횟수
        now = datetime.now()
        recent_restarts = [t for t in self.grid_restart_times
                           if (now - t).total_seconds() < 3600]

        # 예상 재시작 수수료 (보유분 매도 + 새 주문 체결 = 약 0.2%)
        est_restart_fee = self.holding_qty * current_price * 0.002 if self.holding_qty > 0 else 0

        return (
            f"\n=== 수수료/손익 현황 ===\n"
            f"당일 누적 수수료: {self.daily_fees:,.4f} USDT\n"
            f"총 누적 수수료: {self.total_fees_paid:,.4f} USDT\n"
            f"당일 실현 손익: {self.daily_realized:+,.4f} USDT\n"
            f"미실현 손익: {unrealized:+,.4f} USDT\n"
            f"보유 수량: {self.holding_qty:.6f} (평균 매수가: {avg_buy:,.2f})\n"
            f"당일 그리드 재시작: {self.grid_restart_count}회\n"
            f"최근 1시간 재시작: {len(recent_restarts)}회\n"
            f"그리드 재시작 시 예상 수수료: ~{est_restart_fee:,.2f} USDT\n"
            f"\n⚠ WIDEN/SHIFT는 그리드 재시작(중지→재시작)이며 수수료가 발생합니다.\n"
            f"수수료가 예상 수익보다 크면 MAINTAIN을 권고하세요."
        )

    def _check_restart_allowed(self, action: str, current_price: float) -> tuple[bool, str]:
        """그리드 재시작이 수수료 대비 합리적인지 체크."""
        if action not in ("WIDEN", "SHIFT_UP", "SHIFT_DOWN"):
            return True, ""

        now = datetime.now()
        recent = [t for t in self.grid_restart_times
                  if (now - t).total_seconds() < 3600]

        # 하드 리밋: 1시간 내 최대 2회
        if len(recent) >= 2:
            return False, f"1시간 내 재시작 {len(recent)}회 도달 (최대 2회)"

        # 수수료 가드: 예상 수수료 > 최근 실현 수익이면 차단
        if self.holding_qty > 0:
            est_fee = self.holding_qty * current_price * 0.002
            if self.daily_realized > 0 and est_fee > self.daily_realized * 0.5:
                return False, (
                    f"수수료 비효율: 예상 수수료 ~{est_fee:,.4f} > "
                    f"실현 수익의 50% ({self.daily_realized * 0.5:,.4f})"
                )

        return True, ""

    def _record_grid_restart(self):
        """그리드 재시작 기록."""
        self.grid_restart_times.append(datetime.now())
        self.grid_restart_count += 1

    # ─── 그리드 이탈 감지 & 대응 ─────────────────────────────

    def _check_grid_breakout(self, signal, price: float) -> Optional[str]:
        """
        가격이 그리드 범위를 이탈했는지 감지.
        - 이탈 직후: 알림 + 대기 (급락이면 PAUSE)
        - 6시간 이상 이탈: 에이전트에게 재배치 판단 요청
        - 복귀 시: 타이머 리셋
        Returns: 오버라이드할 액션 or None (정상 흐름)
        """
        gl = self.controller.current_lower
        gu = self.controller.current_upper
        if gl is None or gu is None:
            return None

        now = datetime.now()

        # ── 범위 안이면 이탈 상태 리셋 ──
        if gl <= price <= gu:
            if self.grid_breakout_time is not None:
                elapsed = (now - self.grid_breakout_time).total_seconds()
                self._log(f"✅ 그리드 범위 복귀 (이탈 {elapsed/60:.0f}분 만)")
                self.notifier.send(
                    f"✅ 그리드 범위 복귀 | {config.SYMBOL}\n"
                    f"{'─' * 28}\n"
                    f"현재가: {price:,.2f} USDT\n"
                    f"범위: {gl:,.2f} ~ {gu:,.2f}\n"
                    f"이탈 시간: {elapsed/60:.0f}분\n"
                    f"→ 자동 매매 재개"
                )
                self.grid_breakout_time = None
                self.grid_breakout_dir = None
                self.grid_breakout_notified = False
            return None

        # ── 이탈 감지 ──
        direction = "ABOVE" if price > gu else "BELOW"

        # 첫 이탈 감지
        if self.grid_breakout_time is None:
            self.grid_breakout_time = now
            self.grid_breakout_dir = direction
            self.grid_breakout_notified = False

        elapsed = (now - self.grid_breakout_time).total_seconds()
        elapsed_min = elapsed / 60
        elapsed_hr = elapsed / 3600

        # 이탈 알림 (최초 1회)
        if not self.grid_breakout_notified:
            self.grid_breakout_notified = True
            dir_emoji = "⬆️" if direction == "ABOVE" else "⬇️"
            dir_label = "상단 이탈" if direction == "ABOVE" else "하단 이탈"
            boundary = gu if direction == "ABOVE" else gl
            diff = abs(price - boundary)
            diff_pct = diff / boundary * 100

            self._log(f"⚠️ 그리드 {dir_label} | {price:,.2f} (경계: {boundary:,.2f})")
            self.notifier.send(
                f"{dir_emoji} 그리드 {dir_label} | {config.SYMBOL}\n"
                f"{'━' * 28}\n"
                f"현재가  : {price:,.2f} USDT\n"
                f"경계    : {boundary:,.2f} USDT\n"
                f"이탈 폭 : {diff:,.2f} ({diff_pct:.2f}%)\n"
                f"{'━' * 28}\n"
                f"범위: {gl:,.2f} ~ {gu:,.2f}\n"
                f"{'─' * 28}\n"
                f"⏳ 6시간 대기 후 재배치 여부 판단\n"
                f"가격이 범위로 돌아오면 자동 매매 재개"
            )

        # 급락 이탈이면 PAUSE (추가 매수 방지)
        if direction == "BELOW" and not self.controller.paused:
            self._log("하단 이탈 → PAUSE (추가 매수 방지)")
            return "PAUSE"

        # ── 6시간 이상 이탈 → 에이전트에게 재배치 판단 요청 ──
        if elapsed >= self.BREAKOUT_WAIT_SEC:
            self._log(f"그리드 이탈 {elapsed_hr:.1f}시간 경과 → 에이전트 재배치 판단 요청")

            # 수수료 가드 체크
            allowed, skip_reason = self._check_restart_allowed("SHIFT_UP", price)
            if not allowed:
                self._log(f"수수료 가드: {skip_reason} → 대기 유지")
                return "MAINTAIN"

            # 에이전트 판단
            fee_ctx = self._build_fee_context(price)
            breakout_ctx = (
                f"\n=== 그리드 이탈 상황 ===\n"
                f"이탈 방향: {'상단 (가격이 그리드 위)' if direction == 'ABOVE' else '하단 (가격이 그리드 아래)'}\n"
                f"이탈 시간: {elapsed_hr:.1f}시간\n"
                f"현재 그리드: {gl:,.2f} ~ {gu:,.2f}\n"
                f"현재가: {price:,.2f}\n"
                f"그리드 범위로 돌아올 가능성과 재배치 수수료를 비교해서 판단하세요.\n"
                f"재배치 = WIDEN, 계속 대기 = MAINTAIN\n"
            )

            try:
                if config.MULTI_AGENT_MODE and self.multi_agent.available:
                    result = self.multi_agent.judge_with_detail(
                        signal, price, fee_context=fee_ctx + breakout_ctx
                    )
                    action = result.final_action
                    self.notifier.send(
                        f"🤖 이탈 재배치 판단 | {config.SYMBOL}\n"
                        f"이탈: {elapsed_hr:.1f}시간 ({direction})\n"
                        f"에이전트 결정: {action}\n"
                        f"합의율: {result.agreement_rate:.0f}%\n"
                        f"사유: {result.reasoning}"
                    )
                else:
                    action = self.llm_judge.judge(
                        signal, price, fee_context=fee_ctx + breakout_ctx
                    )
                    self.notifier.send(
                        f"🤖 이탈 재배치 판단 | {config.SYMBOL}\n"
                        f"이탈: {elapsed_hr:.1f}시간 ({direction})\n"
                        f"LLM 결정: {action}"
                    )

                if action == "WIDEN":
                    # 재배치 실행
                    self._record_grid_restart()
                    old_lower, old_upper = gl, gu
                    self.controller.shift_grid_center(price, price)
                    self.grid_breakout_time = None
                    self.grid_breakout_dir = None
                    self.grid_breakout_notified = False
                    self.notifier.send(
                        f"🔄 이탈 후 그리드 재배치 | {config.SYMBOL}\n"
                        f"{'─' * 28}\n"
                        f"이전: {old_lower:,.2f} ~ {old_upper:,.2f}\n"
                        f"새 범위: {self.controller.current_lower:,.2f} ~ "
                        f"{self.controller.current_upper:,.2f}\n"
                        f"중심: {price:,.2f} USDT\n"
                        f"이탈 시간: {elapsed_hr:.1f}시간"
                    )
                    return "MAINTAIN"  # 재배치 완료, 정상 흐름
                else:
                    # 에이전트가 대기 결정 → 타이머를 1시간 뒤로 리셋 (매 틱 재판단 방지)
                    self.grid_breakout_time = now - timedelta(
                        seconds=self.BREAKOUT_WAIT_SEC - 3600
                    )
                    return "MAINTAIN"

            except Exception as e:
                self._log(f"이탈 재배치 판단 실패: {e}", level="ERROR")
                return "MAINTAIN"

        # 6시간 미만 이탈 → 대기
        if self.loop_count % 5 == 0:  # 5틱마다 대기 알림
            self.notifier.send(
                f"⏳ 그리드 이탈 대기 중 | {config.SYMBOL}\n"
                f"방향: {'상단' if direction == 'ABOVE' else '하단'}\n"
                f"경과: {elapsed_min:.0f}분 / {self.BREAKOUT_WAIT_SEC//60}분\n"
                f"현재가: {price:,.2f} USDT\n"
                f"범위: {gl:,.2f} ~ {gu:,.2f}"
            )
        return "MAINTAIN"

    # ─── 틱 리포트 텔레그램 발송 ─────────────────────────────

    def _send_tick_report(self, signal, price: float, action: str,
                          trend: str, trend_strength: float):
        """매 틱마다 텔레그램으로 요약 발송. EMERGENCY 시 반복 알림."""
        state_emoji = {"NORMAL": "🟢", "CAUTION": "🟡", "WARNING": "🟠", "EMERGENCY": "🔴"}
        emoji = state_emoji.get(signal.state, "⚪")
        pnl_str = ""
        try:
            pnl = self.controller.get_grid_pnl()
            if pnl:
                total = pnl.get("total_pnl", 0)
                pnl_emoji = "📈" if total >= 0 else "📉"
                pnl_str = f"\n{pnl_emoji} 손익: {total:+,.2f} USDT"
        except Exception:
            pass

        loss_str = ""
        if self.entry_price and price:
            loss_pct = (price - self.entry_price) / self.entry_price * 100
            loss_str = f"\n진입가 대비: {loss_pct:+.2f}%"

        # 체결 기반 손익 섹션
        fill_section = ""
        if self.daily_buys > 0 or self.daily_sells > 0:
            # 미실현 손익 (보유분 평가)
            unrealized = 0.0
            if self.holding_qty > 0 and price:
                avg_buy = self.holding_cost / self.holding_qty
                unrealized = (price - avg_buy) * self.holding_qty
            unrealized_emoji = "📈" if unrealized >= 0 else "📉"
            total_pnl = self.daily_realized + unrealized
            total_emoji = "💰" if total_pnl >= 0 else "💸"

            fill_section = (
                f"\n{'─' * 28}\n"
                f"📋 당일 체결\n"
                f"  매수: {self.daily_buys}건 / {self.daily_buy_cost:,.2f} USDT\n"
                f"  매도: {self.daily_sells}건 / {self.daily_sell_revenue:,.2f} USDT\n"
                f"  수수료: {self.daily_fees:,.4f} USDT\n"
                f"  보유: {self.holding_qty:.6f}\n"
                f"{'─' * 28}\n"
                f"  실현 손익: {self.daily_realized:+,.4f} USDT\n"
                f"  {unrealized_emoji} 미실현: {unrealized:+,.4f} USDT\n"
                f"  {total_emoji} 합계: {total_pnl:+,.4f} USDT"
            )

        # 그리드봇 상태 & 포지션
        grid_section = ""
        gl = self.controller.current_lower
        gu = self.controller.current_upper
        bot_id = self.controller.bot_id
        paused = self.controller.paused

        if gl is not None and gu is not None and gu > gl:
            grid_range = gu - gl
            position_pct = (price - gl) / grid_range * 100
            position_pct = max(0, min(100, position_pct))

            # 위치 바 (10칸)
            bar_pos = int(position_pct / 10)
            bar = "░" * bar_pos + "●" + "░" * (10 - bar_pos)

            # 범위 이탈 감지
            if price > gu:
                pos_label = "⚠️ 상단 이탈!"
            elif price < gl:
                pos_label = "⚠️ 하단 이탈!"
            elif position_pct >= 80:
                pos_label = "상단 근접"
            elif position_pct <= 20:
                pos_label = "하단 근접"
            else:
                pos_label = "범위 내"

            # 봇 상태
            if not bot_id:
                bot_status = "❌ 봇 없음"
            elif paused:
                bot_status = "⏸️ 일시정지"
            else:
                bot_status = "✅ 가동 중"

            # 포지션 요약
            avg_buy_str = ""
            if self.holding_qty > 0 and self.holding_cost > 0:
                avg_buy = self.holding_cost / self.holding_qty
                avg_buy_str = f" (평균 {avg_buy:,.2f})"

            # 그리드 개수 & 간격
            gn = self.controller.current_grid_num
            gm = self.controller.current_mode or "?"
            spacing = grid_range / gn if gn and gn > 0 else 0
            grid_info = f"{gn}칸 ({gm})" if gn else "?"
            spacing_str = f" | 간격: {spacing:,.2f}" if spacing > 0 else ""

            # OKX 실제 포지션 조회
            pos = self.controller.get_grid_positions()
            coin = config.SYMBOL.split("-")[0]  # ETH-USDT → ETH

            # OKX 계좌 전체 잔고
            balances = self.controller.get_account_balance()

            # 포지션 테이블 (계좌 잔고 기준)
            now_dt = datetime.now()
            ampm = "오후" if now_dt.hour >= 12 else "오전"
            hr = now_dt.hour % 12 or 12
            now_ts = f"{now_dt.month}/{now_dt.day} {ampm} {hr}:{now_dt.minute:02d}"
            portfolio_lines = ""
            if balances:
                total_eq = 0.0
                rows = []
                for ccy, bal in sorted(balances.items()):
                    total_bal = bal.get("total", 0)
                    eq_usd = bal.get("eq_usd", 0)
                    if total_bal <= 0 and eq_usd <= 0:
                        continue
                    total_eq += eq_usd
                    if ccy == coin:
                        rows.append(
                            f"  {ccy:<8}{total_bal:>10.2f}개  {price:>10,.0f}  ~{eq_usd:>10,.0f} USDT"
                        )
                    elif ccy == "USDT":
                        rows.append(
                            f"  {ccy:<8}{total_bal:>10,.0f}     {'-':>10}  {total_bal:>11,.0f} USDT"
                        )
                    else:
                        if eq_usd >= 1:
                            rows.append(
                                f"  {ccy:<8}{total_bal:>10.4f}     {'-':>10}  ~{eq_usd:>10,.0f} USDT"
                            )

                # 그리드봇 손익 (OKX API 기준)
                bot_pnl_lines = ""
                if pos:
                    investment = pos.get("investment", 0)
                    grid_profit = pos.get("grid_profit", 0)
                    float_profit = pos.get("float_profit", 0)
                    total_pnl_bot = pos.get("total_pnl", 0)
                    pnl_pct = (total_pnl_bot / investment * 100) if investment > 0 else 0
                    pnl_emoji = "✅" if total_pnl_bot >= 0 else "🔻"
                    bot_pnl_lines = (
                        f"\n"
                        f"  그리드봇 투자금: {investment:,.0f} USDT\n"
                        f"  그리드 수익: {grid_profit:+,.2f} USDT\n"
                        f"  평가 손익:   {float_profit:+,.2f} USDT\n"
                        f"  봇 총손익:   {total_pnl_bot:+,.2f} USDT ({pnl_pct:+.2f}%) {pnl_emoji}"
                    )

                portfolio_lines = (
                    f"\n{'─' * 28}\n"
                    f"현재 포지션 ({now_ts})\n"
                    f"  {'통화':<8}{'보유량':>10}  {'현재가':>10}  {'평가액':>14}\n"
                    + "\n".join(rows)
                    + f"\n  {'총 평가':<8}{'':>10}  {'':>10}  ~{total_eq:>10,.0f} USDT"
                    + bot_pnl_lines
                )

            # 당일 체결 집계 (OKX API)
            fill_info = ""
            try:
                tf = self.controller.get_today_fills()
                bc = tf["buy_count"]
                sc = tf["sell_count"]
                rt = tf["round_trips"]
                net = tf["net_profit"]
                fees = tf["total_fees"]
                gross = tf.get("gross_per_trip", 0)
                fee_rt = tf.get("fee_per_trip", 0)
                net_rt = tf.get("net_per_trip", 0)
                net_emoji = "🔥" if net > 0 else "🧊" if net < 0 else ""

                fill_info = (
                    f"\n{'─' * 28}\n"
                    f"📊 오늘 체결\n"
                    f"  {'구분':<12}{'건수':>8}\n"
                    f"  {'매수':<12}{bc:>7}건\n"
                    f"  {'매도':<12}{sc:>7}건\n"
                    f"  {'왕복':<12}{rt:>7}회\n"
                    f"  {'수수료':<12}~{fees:,.2f} USDT\n"
                    f"  {'─' * 26}\n"
                    f"  1회 차익: ~{gross:,.2f} USDT\n"
                    f"  1회 수수료: ~{fee_rt:,.2f} USDT\n"
                    f"  1회 순수익: ~{net_rt:,.2f} USDT\n"
                    f"  {'─' * 26}\n"
                    f"  오늘 순수익: ~{net:+,.2f} USDT {net_emoji}"
                )
            except Exception:
                pass

            # 미체결 주문 그리드 시각화
            grid_visual = ""
            try:
                pending = self.controller.get_pending_orders()
                sells = pending.get("sell", [])
                buys = pending.get("buy", [])

                if sells or buys:
                    coin = config.SYMBOL.split("-")[0]
                    lines = []
                    lines.append(f"\n{'─' * 28}")
                    lines.append(f"{coin} 봇 현재 포지션 (현재가: {price:,.0f})")
                    lines.append("")

                    # 매도 주문 (높은 가격부터)
                    for s in sells:
                        px_int = int(s['price'])
                        diff = abs(px_int - int(price))
                        if diff <= 1:
                            lines.append(
                                f"  {px_int:,} — 매도 대기 ⬆ ← 현재가 {int(price):,} (거의 체결!!)"
                            )
                        else:
                            lines.append(f"  {px_int:,} — 매도 대기 ⬆")

                    # 현재가 라인
                    lines.append(f"  ----- 현재가 {int(price):,} -----")

                    # 매수 주문 (높은 가격부터)
                    for b in buys:
                        px_int = int(b['price'])
                        diff = abs(int(price) - px_int)
                        if diff <= 1:
                            lines.append(
                                f"  {px_int:,} — 매수 대기 ⬇ ← 현재가 {int(price):,} (거의 체결!!)"
                            )
                        else:
                            lines.append(f"  {px_int:,} — 매수 대기 ⬇")

                    # 포지션 요약
                    total_sell_sz = sum(s['size'] for s in sells)
                    total_sell_amt = sum(s['amount'] for s in sells)
                    total_buy_sz = sum(b['size'] for b in buys)
                    total_buy_amt = sum(b['amount'] for b in buys)

                    sell_prices = [int(s['price']) for s in sells]
                    buy_prices = [int(b['price']) for b in buys]
                    sell_range = f"{min(sell_prices):,}~{max(sell_prices):,}" if sell_prices else "-"
                    buy_range = f"{min(buy_prices):,}~{max(buy_prices):,}" if buy_prices else "-"

                    lines.append("")
                    lines.append("포지션 요약")
                    lines.append(f"  {'구분':<8}{'가격':<18}{'수량':<12}{'금액'}")
                    lines.append(
                        f"  {'매도대기':<8}{sell_range:<18}"
                        f"{total_sell_sz:.4f} {coin:<6}~{total_sell_amt:,.0f} USDT"
                    )
                    lines.append(
                        f"  {'매수대기':<8}{buy_range} ({len(buys)}칸)  "
                        f"{total_buy_sz:.4f} {coin:<6}~{total_buy_amt:,.0f} USDT"
                    )

                    # 가장 가까운 체결 알림
                    nearest_sell = sells[-1]['price'] if sells else None
                    nearest_buy = buys[0]['price'] if buys else None
                    if nearest_sell and abs(nearest_sell - price) <= 2:
                        lines.append(
                            f"\n🔥 {int(nearest_sell):,} 매도가 딱 "
                            f"{abs(nearest_sell - price):.0f} USDT 차이! "
                            f"조금만 올라오면 바로 체결!"
                        )
                    elif nearest_buy and abs(price - nearest_buy) <= 2:
                        lines.append(
                            f"\n🔥 {int(nearest_buy):,} 매수가 딱 "
                            f"{abs(price - nearest_buy):.0f} USDT 차이! "
                            f"조금만 내려오면 바로 체결!"
                        )

                    grid_visual = "\n".join(lines)
            except Exception:
                pass

            grid_section = (
                f"\n{'─' * 28}\n"
                f"🤖 그리드봇: {bot_status}\n"
                f"📐 {gl:,.2f} [{bar}] {gu:,.2f}\n"
                f"   위치: {position_pct:.0f}% ({pos_label})\n"
                f"   {grid_info}{spacing_str}"
                f"{fill_info}"
                f"{portfolio_lines}\n"
                f"🔄 재시작: 당일 {self.grid_restart_count}회 | 수수료: {self.daily_fees:,.4f}"
            )

        # 메시지 1: 틱 요약
        msg = (
            f"{emoji} TICK #{self.loop_count} | {config.SYMBOL}\n"
            f"{'━' * 28}\n"
            f"💰 {config.SYMBOL} : {price:,.2f} USDT\n"
            f"{'━' * 28}\n"
            f"상태: {signal.state} | 점수: {signal.risk_score:.1f}/100\n"
            f"추세: {trend} (ADX={trend_strength:.1f})\n"
            f"액션: {action}"
            f"{pnl_str}"
            f"{loss_str}"
            f"{grid_section}"
            f"{fill_section}\n"
            f"{'─' * 28}\n"
            f"ATR={signal.atr_current:.1f} | RSI={signal.rsi:.1f} | "
            f"BB={signal.bb_width:.2f}% | Vol={signal.volume_ratio:.1f}x"
        )
        self.notifier.send(msg)

        # 메시지 2: 그리드 주문 래더 (별도 메시지)
        if grid_visual:
            self.notifier.send(grid_visual)

        # EMERGENCY: 10초 간격으로 3회 반복 알림
        if signal.state == "EMERGENCY":
            emergency_msg = (
                f"🚨🚨🚨 긴급 알림 🚨🚨🚨\n\n"
                f"리스크 점수: {signal.risk_score:.1f}/100\n"
                f"현재가: {price:,.2f} USDT\n"
                f"사유: {signal.reason}\n"
                f"액션: {action}\n\n"
                f"즉시 확인이 필요합니다!"
            )
            for i in range(3):
                time.sleep(10)
                self.notifier.send(f"[{i+2}/4] {emergency_msg}")

    # ─── 대기 프로그레스 바 ────────────────────────────────────

    def _wait_with_progress(self, seconds: int):
        """다음 틱까지 프로그레스 바로 대기 시간 시각화."""
        BAR_WIDTH = 40
        DIM = "\033[2m"
        CYAN = "\033[96m"
        GREEN = "\033[92m"
        RESET = "\033[0m"
        BOLD = "\033[1m"

        for elapsed in range(seconds):
            remaining = seconds - elapsed
            progress = elapsed / seconds
            filled = int(BAR_WIDTH * progress)
            bar = "█" * filled + "░" * (BAR_WIDTH - filled)

            mins, secs = divmod(remaining, 60)
            time_str = f"{mins}:{secs:02d}" if mins else f"{secs}초"

            print(
                f"\r  {DIM}⏳{RESET} {CYAN}{bar}{RESET} "
                f"{GREEN}{progress*100:5.1f}%{RESET} "
                f"{DIM}(다음 틱까지 {time_str}){RESET}",
                end="", flush=True
            )
            time.sleep(1)

        # 완료
        bar = "█" * BAR_WIDTH
        print(
            f"\r  {DIM}✓{RESET}  {GREEN}{bar}{RESET} "
            f"{GREEN}{BOLD}100.0%{RESET} "
            f"{DIM}(시작!){RESET}              "
        )

    # ─── 손절 체크 ─────────────────────────────────────────

    def _check_stop_loss(self, current_price: float) -> bool:
        """진입가 대비 config.MAX_LOSS_PERCENT 이상 손실 시 True."""
        if self.entry_price is None:
            self.entry_price = current_price
            return False
        loss_pct = (self.entry_price - current_price) / self.entry_price * 100
        return loss_pct >= config.MAX_LOSS_PERCENT

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
        # 메뉴에서 설정 변경했을 수 있으므로 config 모듈 다시 로드
        import importlib
        import config
        importlib.reload(config)
        GridAgent().run()
