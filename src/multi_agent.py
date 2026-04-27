"""
multi_agent.py
멀티 에이전트 합의 시스템.

4명의 전문가 에이전트가 독립적으로 분석한 뒤,
조율자(Coordinator)가 합의를 도출합니다.

모든 에이전트는 "그리드봇 운영자" 관점으로 사고합니다.
일반 트레이더와 달리, 변동성은 위험이 아니라 수익 원천이며,
가격이 그리드 경계 근처에 있는 것은 "위험"이 아니라 "체결 자리"입니다.

에이전트 구성:
  1. 기술적 분석가 (Technical Analyst)
  2. 감성 분석가 (Sentiment Analyst)
  3. 리스크 관리자 (Risk Manager)
  4. 거시 경제 전문가 (Macro Strategist)
  + 조율자 (Coordinator) — 최종 합의 도출
"""

import json
from dataclasses import dataclass
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import openai
from google import genai

from config import LLM_PROVIDER, LLM_API_KEY, LLM_MODEL


VALID_ACTIONS = ("MAINTAIN", "WIDEN", "STOP", "SHIFT_UP", "SHIFT_DOWN")


@dataclass
class AgentOpinion:
    role: str
    action: str
    confidence: int      # 1~10
    reason: str


@dataclass
class ConsensusResult:
    final_action: str
    opinions: list        # list[AgentOpinion]
    agreement_rate: float # 0~100%
    reasoning: str


# ─── 그리드봇 헌법 (모든 에이전트 공통 전제) ──────────────
# ① 메커니즘 명시 — 그리드봇은 횡보로 돈 번다는 사실을 시스템 프롬프트 최상단에 박는다.
# ② MAINTAIN 디폴트 — 액션 변경의 입증 책임은 항상 변경 쪽에 있다.
# ③ STOP 재정의 — STOP은 "잠시 멈춤"이 아니라 "그리드 전략 영구 폐기"에 가깝다.

GRID_BOT_CONSTITUTION = """【그리드봇 운영 헌법 — 모든 판단의 전제】

1. 메커니즘:
   - 이 봇은 가격이 위아래로 출렁일 때 매수/매도를 반복해서 돈을 번다.
   - 변동성은 친구다. 횡보는 최적 환경이다.
   - 가격이 그리드 경계 근처에 있다는 것은 "곧 체결될 자리"이며 "위험"이 아니다.
   - 봇을 멈추는 것은 곧 수익 기회를 영구히 포기하는 것이다.

2. 디폴트는 MAINTAIN:
   - 액션을 바꾸려면 명확한 증거가 필요하다. 모호하면 무조건 MAINTAIN.
   - WIDEN/SHIFT: ADX ≥ 25 + 명확한 단방향 추세, 또는 그리드 이탈 24h+ 지속.
   - 1분봉 wick 한 번, 일시적 거래량 spike, 단발성 score 급등은 무시한다.

3. STOP은 거의 없다:
   - STOP은 "잠시 멈춤"이 아니다. 봇 종료 + 자산 청산 + 재시작 시 수수료 발생.
   - 시장이 잠깐 출렁이는 정도로는 절대 STOP하지 마라.
   - STOP 후보 시나리오는 다음과 같이 외부적/시스템적 위험에 한정한다:
     · 1시간 내 ±20% 폭락 같은 시스템적 시장 붕괴
     · 거래소 장애, 디레버리지 이벤트
     · 그리드 전략 자체가 무효한 영구 추세 전환 (ADX 35+ 지속)
   - 위 조건이 아니면 STOP은 답이 아니다. 차라리 SHIFT/WIDEN으로 적응하라.

4. 정지의 비용:
   - 일일 기회비용 = 봇이 돌면 하루에 벌었을 평균 수익.
   - STOP은 자본 보전이 아니라 자본 효율 손실이다.
"""


# ─── 에이전트 프롬프트 ──────────────────────────────────

def _build_market_context(signal, current_price: float,
                          fee_context: str = "") -> str:
    bot_label = getattr(signal, "bot_label", "") or ""
    bot_header = f"[봇: {bot_label}] " if bot_label else ""
    return f"""=== 시장 데이터 ({bot_header}현재가 {current_price:,.0f} USDT) ===
리스크 스코어: {signal.risk_score}/100
상태: {signal.state}

ATR (현재/평균): {signal.atr_current:.1f} / {signal.atr_avg:.1f}
RSI: {signal.rsi:.1f}
볼린저밴드 폭: {signal.bb_width:.1f}%
거래량 배율: {signal.volume_ratio:.1f}x
추세: {signal.trend} (ADX={getattr(signal, 'adx', 0):.1f})
단기 EMA: {getattr(signal, 'ema_short', 0):,.1f}
장기 EMA: {getattr(signal, 'ema_long', 0):,.1f}
{fee_context}"""


AGENT_PROMPTS = {
    "technical": {
        "role": "기술적 분석가",
        "system": GRID_BOT_CONSTITUTION + """
당신은 그리드봇을 운영하는 기술적 분석가입니다.
차트 지표(EMA, ATR, 볼린저, RSI)로 그리드 작동 환경을 판단하되,
"트레이더 관점"이 아니라 "그리드 운영자 관점"으로 사고하세요.

해석 가이드:
- ADX 낮음 (< 20): 횡보장 → MAINTAIN (그리드 황금기)
- ADX 중간 (20~30): 약한 추세 → 일반적으로 MAINTAIN. 이탈이 길어지면 SHIFT 검토.
- ADX 강함 (> 30) + 일관된 EMA 정렬: SHIFT 후보. 단발성이면 무시.
- ATR 급등 + 가격이 그리드 안: WIDEN 후보 (체결 빈도는 늘지만 wick 위험).
- ATR 급등 + 가격이 그리드 밖 + 6h 이상 지속: SHIFT 후보.
- RSI 극단값 한 번: 무시. 그리드는 mean-reversion 자체로 처리한다.

절대 STOP을 가볍게 권고하지 마세요. STOP은 시스템적 시장 붕괴에만 해당합니다.
신뢰도 낮으면(1~4) MAINTAIN으로 답하세요."""
    },
    "sentiment": {
        "role": "감성 분석가",
        "system": GRID_BOT_CONSTITUTION + """
당신은 그리드봇을 운영하는 시장 심리 분석가입니다.
거래량과 RSI 다이버전스로 시장 참여자 심리를 읽되, 그리드봇의 입장에서 판단하세요.

해석 가이드:
- 거래량 감소 + 횡보: 그리드의 황금 환경. MAINTAIN.
- 거래량 평이 + 가격 출렁임: MAINTAIN (체결 빈도 정상).
- 거래량 5배+ 급등 + 가격 하락: 패닉 셀링 가능성. 가격이 그리드 안이면 MAINTAIN
  (그리드는 패닉 매수 자리에서 돈 번다). 그리드 밖으로 이탈하면 SHIFT_DOWN 후보.
- 거래량 급등 + FOMO 상승: 이탈 시 SHIFT_UP 후보. 그리드 안이면 MAINTAIN.
- 단발성 거래량 spike: 무시.

심리적 두려움은 STOP 정당화 사유가 아닙니다. 객관적 가격 이탈만 봅니다.
신뢰도 낮으면(1~4) MAINTAIN으로 답하세요."""
    },
    "risk": {
        "role": "리스크 관리자",
        "system": GRID_BOT_CONSTITUTION + """
당신은 그리드봇 운영의 리스크 관리자입니다.
주의: 당신의 목표는 "자본 보전"이 아니라 "자본 효율"입니다.
봇을 멈추는 것은 자본 효율 손실입니다 (일일 기회비용 발생).

판단 가이드:
- 빈번한 재시작 자체가 리스크 (수수료 누적): 1시간 내 재시작 2회+ 발생 시 무조건 MAINTAIN.
- 예상 재시작 수수료 > 당일 실현 수익의 50% → MAINTAIN.
- 수수료/손익 컨텍스트의 숫자를 반드시 확인하세요.
- 그리드 이탈 24h 미만: MAINTAIN (복귀 대기가 합리적).
- 그리드 이탈 24~48h + ADX≥20: WIDEN/SHIFT 검토.
- 그리드 이탈 48h+: WIDEN 강력 권고.

STOP에 대한 입장:
- STOP은 거의 발동하지 않는다. 보수적이라는 이유로 STOP을 추천하지 마세요.
- "자본 보전을 위한 STOP"은 잘못된 접근입니다. 차라리 SHIFT/WIDEN으로 적응합니다.
- STOP은 당일 손익 -10%+ 손실 + 추가 하락 시그널 명확 같은 시스템 위험에만.
- 모호하면 MAINTAIN."""
    },
    "macro": {
        "role": "거시 전략가",
        "system": GRID_BOT_CONSTITUTION + """
당신은 그리드봇을 운영하는 거시 전략가입니다.
큰 그림을 봅니다. 추세의 방향과 강도, 시장 사이클 위치를 판단합니다.

판단 가이드:
- ADX < 20 (횡보장): 그리드 트레이딩 최적 환경. 무조건 MAINTAIN.
- ADX 20~25 (약한 추세): MAINTAIN 디폴트. 이탈이 길어지면 SHIFT 검토.
- ADX > 25 + 명확한 단방향 EMA 정렬:
   상승 추세 → SHIFT_UP, 하락 추세 → SHIFT_DOWN.
- ADX 급변 (한 틱 만에 +10): 단발성 신호일 가능성. MAINTAIN.

추세 전환 초기에는 변동성이 커서 그리드가 오히려 잘 일합니다.
"트렌드가 시작된 것 같다"는 직감으로 SHIFT/STOP 권고하지 마세요.
신뢰도 낮으면(1~4) MAINTAIN으로 답하세요."""
    }
}


COORDINATOR_SYSTEM = GRID_BOT_CONSTITUTION + """
당신은 그리드봇 운영자로서 4명의 전문가 의견을 종합합니다.
모든 판단은 위의 그리드봇 헌법을 따릅니다.

【디폴트 행동】
액션 변경의 입증 책임은 변경하는 쪽에 있습니다. 모호하면 무조건 MAINTAIN.

【합의 규칙】
1. 4명 중 3명 이상이 같은 NON-MAINTAIN 액션에 동의 → 그 액션 채택.
2. 4명 중 2명 이하만 NON-MAINTAIN 동의 → MAINTAIN.
3. 의견이 갈리면 MAINTAIN.
4. 전체 신뢰도(confidence) 평균이 6 이하면 MAINTAIN (확신 부족).
5. 어떤 에이전트도 STOP에 가중치를 받지 않음 (모든 에이전트 동등).

【STOP 보호 장치 — 누구든 한 명이라도 만족 안 하면 STOP 금지】
STOP을 채택하려면 다음을 모두 만족해야 합니다:
- 4명 중 4명 전원이 STOP 동의 (만장일치).
- 모든 에이전트의 STOP 신뢰도가 8/10 이상.
- 사유에 "시스템적 시장 붕괴", "거래소 장애", "ADX 35+ 영구 추세 전환" 중 하나 이상 포함.
위 조건 미충족이면 STOP 후보가 있어도 SHIFT/WIDEN 또는 MAINTAIN으로 강등하세요.

【수수료/안전 가드】
6. 예상 재시작 수수료 > 당일 실현 수익의 50% → WIDEN/SHIFT 대신 MAINTAIN.
7. 최근 1시간 내 그리드 재시작 2회 이상 → WIDEN/SHIFT 금지 (MAINTAIN 강제).

【이탈 대응 (MAINTAIN을 깨는 거의 유일한 정상 사유)】
8. 컨텍스트에 `=== 그리드 이탈 상황 ===`이 있고 이탈 24h+ → MAINTAIN 대신 WIDEN/SHIFT 다수결 채택.
9. 이탈 48h+ + ADX≥20 + 수수료 가드 통과 → WIDEN 적극 채택.

반드시 아래 JSON 형식으로만 응답:
{"action": "ACTION", "reasoning": "합의 도출 이유 한줄"}"""


# ─── 멀티 에이전트 클래스 ────────────────────────────────

class MultiAgentJudge:
    """4명의 전문가 에이전트 + 조율자로 합의 기반 의사결정."""

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
                print("[MultiAgent] API 키 미설정 — 멀티 에이전트 비활성화")
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
                print(f"[MultiAgent] 미지원 provider: {self.provider}")
                return

            self.available = True
        except Exception as e:
            print(f"[MultiAgent] 초기화 실패: {e}")

    def judge(self, signal, current_price: float) -> str:
        """멀티 에이전트 합의 → 최종 액션 반환."""
        if not self.available:
            return "MAINTAIN"

        try:
            result = self._consensus(signal, current_price)
            self._log_result(result)
            return result.final_action
        except Exception as e:
            print(f"[MultiAgent] 합의 실패: {e}")
            return "MAINTAIN"

    def judge_with_detail(self, signal, current_price: float,
                          fee_context: str = "") -> ConsensusResult:
        """상세 합의 결과 반환 (텔레그램 리포트용)."""
        if not self.available:
            return ConsensusResult(
                final_action="MAINTAIN",
                opinions=[],
                agreement_rate=0,
                reasoning="멀티 에이전트 비활성화"
            )
        try:
            return self._consensus(signal, current_price, fee_context)
        except Exception as e:
            return ConsensusResult(
                final_action="MAINTAIN",
                opinions=[],
                agreement_rate=0,
                reasoning=f"합의 실패: {e}"
            )

    # ─── 내부 로직 ───────────────────────────────────────

    def _consensus(self, signal, current_price: float,
                    fee_context: str = "") -> ConsensusResult:
        """에이전트 병렬 호출 → 조율자 합의."""
        context = _build_market_context(signal, current_price, fee_context)

        # 4명 병렬 호출
        opinions = self._gather_opinions(context)

        if not opinions:
            return ConsensusResult("MAINTAIN", [], 0, "에이전트 응답 없음")

        # 조율자에게 합의 요청
        final_action, reasoning = self._coordinate(opinions, context)

        # 합의율 계산
        action_counts = {}
        for o in opinions:
            action_counts[o.action] = action_counts.get(o.action, 0) + 1
        max_agree = max(action_counts.values()) if action_counts else 0
        agreement_rate = max_agree / len(opinions) * 100

        return ConsensusResult(
            final_action=final_action,
            opinions=opinions,
            agreement_rate=agreement_rate,
            reasoning=reasoning
        )

    def _gather_opinions(self, context: str) -> list:
        """4명의 에이전트를 병렬로 호출."""
        opinions = []

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            for agent_id, agent_config in AGENT_PROMPTS.items():
                future = executor.submit(
                    self._ask_agent, agent_id, agent_config, context
                )
                futures[future] = agent_id

            for future in as_completed(futures, timeout=30):
                agent_id = futures[future]
                try:
                    opinion = future.result()
                    if opinion:
                        opinions.append(opinion)
                except Exception as e:
                    print(f"[MultiAgent] {agent_id} 응답 실패: {e}")

        return opinions

    def _ask_agent(self, agent_id: str, config: dict, context: str) -> Optional[AgentOpinion]:
        """개별 에이전트에게 판단 요청."""
        prompt = f"""{context}

다음 액션 중 하나를 선택하고 신뢰도(1~10)와 이유를 답하세요:
- MAINTAIN: 현재 그리드 유지 (디폴트, 모호하면 이걸로)
- WIDEN: 그리드 간격 확대 (재시작 → 수수료 발생)
- SHIFT_UP: 그리드를 위로 이동 (재시작 → 수수료 발생)
- SHIFT_DOWN: 그리드를 아래로 이동 (재시작 → 수수료 발생)
- STOP: 봇 종료 + 자산 청산 (시스템적 시장 붕괴 한정 — 일반적으로 답이 아님)

【판단 가이드라인 — 그리드봇 운영자 관점】
1. 디폴트는 MAINTAIN. 액션 변경에는 명확한 객관적 증거가 필요합니다.
2. 가격이 그리드 경계 80% 도달 → 그건 "체결 자리"이지 위험이 아닙니다. MAINTAIN.
3. 단발성 spike (1분봉 wick, score 일시 급등) → 무시. MAINTAIN.
4. 이탈 상황 (`=== 그리드 이탈 상황 ===`):
   * 이탈 24h 미만: MAINTAIN (복귀 대기).
   * 이탈 24~48h + ADX≥20: WIDEN 또는 SHIFT 권고.
   * 이탈 48h 이상: WIDEN 강력 권고 (복귀 가능성 낮음, 기회비용 > 수수료).
5. 수수료/손익 컨텍스트 확인. 예상 수수료 > 일일 수익의 50% → MAINTAIN.

【STOP 사용 제한】
STOP은 다음 중 하나여야만 후보가 됩니다:
- 1시간 내 시장 ±20% 폭락 같은 시스템적 붕괴.
- ADX 35+ 영구 추세 전환이 명백한 경우.
- 거래소 장애/디레버리지 같은 외부 사건.
"위험해 보여서", "안전하게", "보수적으로" 같은 정성적 사유로 STOP을 권고하지 마세요.
STOP 권고 시 신뢰도는 8 이상이어야 하며, 사유에 위 시스템 위험을 명시해야 합니다.

반드시 아래 JSON 형식으로만 응답:
{{"action": "ACTION", "confidence": 숫자, "reason": "이유 한줄"}}"""

        try:
            raw = self._call_llm(config["system"], prompt)
            return self._parse_opinion(raw, config["role"])
        except Exception as e:
            print(f"[MultiAgent] {config['role']} 호출 오류: {e}")
            return None

    def _coordinate(self, opinions: list, context: str) -> tuple:
        """조율자가 최종 합의를 도출. STOP 가드는 코드로 강제."""
        opinions_text = "\n".join([
            f"- {o.role}: {o.action} (신뢰도 {o.confidence}/10) — {o.reason}"
            for o in opinions
        ])

        prompt = f"""{context}

=== 전문가 의견 ===
{opinions_text}

위 의견들을 종합하여 최종 액션을 결정하세요."""

        try:
            raw = self._call_llm(COORDINATOR_SYSTEM, prompt)
            data = self._parse_json(raw)
            action = data.get("action", "MAINTAIN").upper()
            reasoning = data.get("reasoning", "")
            if action not in VALID_ACTIONS:
                action = "MAINTAIN"
        except Exception as e:
            # 조율자 실패 → 다수결 폴백
            action = self._majority_vote(opinions)
            reasoning = f"조율자 실패, 다수결 적용: {e}"

        # ─── STOP 보호 장치 (코드 레벨 강제) ───
        # LLM이 STOP을 답해도 다음 조건을 모두 만족 안 하면 강등:
        #  - 4명 만장일치 STOP
        #  - 모든 STOP 의견 신뢰도 8 이상
        if action == "STOP":
            stop_opinions = [o for o in opinions if o.action == "STOP"]
            unanimous = len(stop_opinions) == len(opinions) and len(opinions) > 0
            high_conf = all(o.confidence >= 8 for o in stop_opinions) if stop_opinions else False
            if not (unanimous and high_conf):
                downgraded = self._fallback_non_stop(opinions)
                reasoning = (
                    f"STOP 보호 장치 발동 (만장일치={unanimous}, "
                    f"전원 신뢰도≥8={high_conf}) → {downgraded}로 강등. "
                    f"원래 사유: {reasoning}"
                )
                action = downgraded

        return action, reasoning

    def _fallback_non_stop(self, opinions: list) -> str:
        """STOP 강등 시 차선 액션 — STOP 제외 다수결, 동률이면 MAINTAIN."""
        non_stop = [o for o in opinions if o.action != "STOP"]
        if not non_stop:
            return "MAINTAIN"
        counts = {}
        for o in non_stop:
            counts[o.action] = counts.get(o.action, 0) + 1
        max_count = max(counts.values())
        candidates = [a for a, c in counts.items() if c == max_count]
        # 동률이면 MAINTAIN 우선 (그리드봇 헌법 ②: 모호하면 MAINTAIN)
        priority = {"MAINTAIN": 0, "WIDEN": 1, "SHIFT_UP": 2, "SHIFT_DOWN": 3}
        candidates.sort(key=lambda a: priority.get(a, 99))
        return candidates[0]

    def _majority_vote(self, opinions: list) -> str:
        """폴백: 단순 다수결 (동률 시 MAINTAIN 우선 — 그리드봇 헌법 ②)."""
        if not opinions:
            return "MAINTAIN"

        # 그리드봇 헌법 ②: 디폴트는 MAINTAIN. 동률 시 MAINTAIN 우선.
        # STOP은 만장일치 + 신뢰도 8+ 가드를 _coordinate에서 별도 적용하므로
        # 여기서는 우선순위만 가장 낮게 둔다 (선택돼도 강등됨).
        priority = {"MAINTAIN": 0, "WIDEN": 1, "SHIFT_UP": 2,
                    "SHIFT_DOWN": 3, "STOP": 4}

        counts = {}
        for o in opinions:
            counts[o.action] = counts.get(o.action, 0) + 1

        max_count = max(counts.values())
        candidates = [a for a, c in counts.items() if c == max_count]

        # 동률이면 MAINTAIN 우선 (변경의 입증 책임은 변경하는 쪽)
        candidates.sort(key=lambda a: priority.get(a, 99))
        return candidates[0]

    # ─── LLM 호출 ────────────────────────────────────────

    def _call_llm(self, system: str, prompt: str) -> str:
        if self.provider == "anthropic":
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=system,
                messages=[{"role": "user", "content": prompt}]
            )
            return resp.content[0].text.strip()
        elif self.provider == "gemini":
            resp = self.client.models.generate_content(
                model=self.model,
                contents=f"{system}\n\n{prompt}",
                config={"max_output_tokens": 200},
            )
            return resp.text.strip()
        else:  # openai, grok (OpenAI 호환)
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=200,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ]
            )
            return resp.choices[0].message.content.strip()

    # ─── 파싱 ────────────────────────────────────────────

    def _parse_json(self, raw: str) -> dict:
        """JSON 파싱 (```json``` 래핑 대응)."""
        text = raw.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())

    def _parse_opinion(self, raw: str, role: str) -> Optional[AgentOpinion]:
        """에이전트 응답을 AgentOpinion으로 파싱."""
        try:
            data = self._parse_json(raw)
            action = data.get("action", "MAINTAIN").upper()
            confidence = int(data.get("confidence", 5))
            reason = data.get("reason", "")

            if action not in VALID_ACTIONS:
                action = "MAINTAIN"
            confidence = max(1, min(10, confidence))

            return AgentOpinion(
                role=role,
                action=action,
                confidence=confidence,
                reason=reason
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            print(f"[MultiAgent] {role} 응답 파싱 실패: {e}")
            return None

    # ─── 로그 ────────────────────────────────────────────

    def _log_result(self, result: ConsensusResult):
        opinions_str = " | ".join([
            f"{o.role}={o.action}({o.confidence})"
            for o in result.opinions
        ])
        print(
            f"[MultiAgent] 합의={result.final_action} "
            f"(동의율={result.agreement_rate:.0f}%) | {opinions_str}"
        )


def format_consensus_for_telegram(result: ConsensusResult,
                                  bot_label: str = "") -> str:
    """텔레그램 알림용 합의 결과 포맷.

    bot_label이 주어지면 멀티봇 환경에서 어느 봇 합의인지 식별 가능하게
    헤더에 봇 라벨을 표시한다 (Notifier도 메시지 전체에 prefix 추가하지만,
    합의 결과는 핵심 메시지라 헤더에서 한 번 더 노출한다).
    """
    label_part = f" [{bot_label}]" if bot_label else ""
    lines = [
        f"🤖 멀티 에이전트 합의{label_part}",
        f"{'─' * 28}",
        f"최종 결정: {result.final_action}",
        f"합의율: {result.agreement_rate:.0f}%",
        f"{'─' * 28}",
    ]
    for o in result.opinions:
        emoji = {"MAINTAIN": "🟢", "WIDEN": "🟡",
                 "STOP": "🔴", "SHIFT_UP": "⬆️",
                 "SHIFT_DOWN": "⬇️"}.get(o.action, "⚪")
        lines.append(f"{emoji} {o.role}: {o.action} ({o.confidence}/10)")
        lines.append(f"   {o.reason}")
    lines.append(f"{'─' * 28}")
    lines.append(f"📋 {result.reasoning}")
    return "\n".join(lines)
