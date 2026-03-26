"""
multi_agent.py
멀티 에이전트 합의 시스템.

4명의 전문가 에이전트가 독립적으로 분석한 뒤,
조율자(Coordinator)가 합의를 도출합니다.

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

from config import LLM_PROVIDER, LLM_API_KEY, LLM_MODEL


VALID_ACTIONS = ("MAINTAIN", "WIDEN", "PAUSE", "STOP", "REDUCE", "SHIFT_UP", "SHIFT_DOWN")


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


# ─── 에이전트 프롬프트 ──────────────────────────────────

def _build_market_context(signal, current_price: float) -> str:
    return f"""=== 시장 데이터 ===
현재 가격: {current_price:,.0f} USDT
리스크 스코어: {signal.risk_score}/100
상태: {signal.state}

ATR (현재/평균): {signal.atr_current:.1f} / {signal.atr_avg:.1f}
RSI: {signal.rsi:.1f}
볼린저밴드 폭: {signal.bb_width:.1f}%
거래량 배율: {signal.volume_ratio:.1f}x
추세: {signal.trend} (ADX={getattr(signal, 'adx', 0):.1f})
단기 EMA: {getattr(signal, 'ema_short', 0):,.1f}
장기 EMA: {getattr(signal, 'ema_long', 0):,.1f}"""


AGENT_PROMPTS = {
    "technical": {
        "role": "기술적 분석가",
        "system": """당신은 암호화폐 기술적 분석 전문가입니다.
차트 패턴, EMA 크로스오버, ATR, 볼린저밴드, RSI 등 기술적 지표만으로 판단합니다.
감정이나 뉴스는 무시하고 오직 가격 액션과 지표에 집중하세요.

판단 기준:
- EMA 9/21 크로스: 골든크로스=상승, 데드크로스=하락
- RSI 극단값: 30 이하 과매도, 70 이상 과매수
- ATR 급등: 변동성 확대 시 간격 확대 또는 중단
- 볼린저밴드 수축→확장: 브레이크아웃 임박 신호"""
    },
    "sentiment": {
        "role": "감성 분석가",
        "system": """당신은 시장 심리 및 거래량 분석 전문가입니다.
거래량 패턴, RSI의 다이버전스, 시장 공포/탐욕 수준으로 판단합니다.
'시장 참여자들이 지금 어떤 심리 상태인가'에 집중하세요.

판단 기준:
- 거래량 급등 + 가격 하락: 패닉 셀링, 주의 필요
- 거래량 급등 + 가격 상승: FOMO 매수, 과열 주의
- 거래량 감소 + 횡보: 관망세, 그리드 유지 적합
- RSI 극단값 + 거래량 패턴 조합으로 반전 감지"""
    },
    "risk": {
        "role": "리스크 관리자",
        "system": """당신은 보수적인 리스크 관리 전문가입니다.
자본 보전이 최우선입니다. 확신이 없으면 항상 방어적으로 판단합니다.
'이 상황에서 최악의 시나리오는 무엇인가'를 항상 고려하세요.

판단 기준:
- 리스크 스코어 50 이상: 최소한 WIDEN 또는 PAUSE 권고
- 하락 추세 + 높은 변동성: PAUSE 또는 STOP 권고
- 변동성 낮음 + 횡보: MAINTAIN 허용
- 의심스러우면 무조건 방어적 판단 (PAUSE > MAINTAIN)"""
    },
    "macro": {
        "role": "거시 전략가",
        "system": """당신은 거시 경제 및 크로스마켓 전략가입니다.
개별 지표보다 큰 그림을 봅니다. 추세의 방향과 강도, 시장 사이클 위치를 판단합니다.

판단 기준:
- ADX 높은 추세장: 추세 방향으로 그리드 시프트 권고
- ADX 낮은 횡보장: 그리드 트레이딩 최적 환경, MAINTAIN
- 추세 전환 초기 신호: 방어적 전환 (REDUCE/PAUSE)
- 강한 상승 추세: SHIFT_UP, 강한 하락 추세: SHIFT_DOWN 또는 PAUSE"""
    }
}


COORDINATOR_SYSTEM = """당신은 4명의 전문가 의견을 종합하는 투자 조율자입니다.

규칙:
1. 과반수(3/4 이상) 동의하는 액션이 있으면 그것을 채택
2. 리스크 관리자가 STOP/PAUSE를 권고하면 가중치 2배 부여
3. 의견이 분산되면 가장 방어적인 액션 채택 (STOP > PAUSE > REDUCE > WIDEN > SHIFT > MAINTAIN)
4. 전체 신뢰도(confidence) 평균이 3 이하면 MAINTAIN (확신 부족)

반드시 아래 JSON 형식으로만 응답:
{"action": "ACTION", "reasoning": "합의 도출 이유 한줄"}"""


# ─── 멀티 에이전트 클래스 ────────────────────────────────

class MultiAgentJudge:
    """4명의 전문가 에이전트 + 조율자로 합의 기반 의사결정."""

    DEFAULT_MODELS = {
        "anthropic": "claude-sonnet-4-20250514",
        "openai": "gpt-4o",
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

    def judge_with_detail(self, signal, current_price: float) -> ConsensusResult:
        """상세 합의 결과 반환 (텔레그램 리포트용)."""
        if not self.available:
            return ConsensusResult(
                final_action="MAINTAIN",
                opinions=[],
                agreement_rate=0,
                reasoning="멀티 에이전트 비활성화"
            )
        try:
            return self._consensus(signal, current_price)
        except Exception as e:
            return ConsensusResult(
                final_action="MAINTAIN",
                opinions=[],
                agreement_rate=0,
                reasoning=f"합의 실패: {e}"
            )

    # ─── 내부 로직 ───────────────────────────────────────

    def _consensus(self, signal, current_price: float) -> ConsensusResult:
        """에이전트 병렬 호출 → 조율자 합의."""
        context = _build_market_context(signal, current_price)

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
- MAINTAIN: 현재 그리드 유지
- WIDEN: 그리드 간격 확대
- PAUSE: 신규 주문 중단
- STOP: 전체 청산
- REDUCE: 매수 주문만 취소 (하락 방어)
- SHIFT_UP: 그리드를 위로 이동
- SHIFT_DOWN: 그리드를 아래로 이동

반드시 아래 JSON 형식으로만 응답:
{{"action": "ACTION", "confidence": 숫자, "reason": "이유 한줄"}}"""

        try:
            raw = self._call_llm(config["system"], prompt)
            return self._parse_opinion(raw, config["role"])
        except Exception as e:
            print(f"[MultiAgent] {config['role']} 호출 오류: {e}")
            return None

    def _coordinate(self, opinions: list, context: str) -> tuple:
        """조율자가 최종 합의를 도출."""
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
            return action, reasoning
        except Exception as e:
            # 조율자 실패 시 다수결 폴백
            return self._majority_vote(opinions), f"조율자 실패, 다수결 적용: {e}"

    def _majority_vote(self, opinions: list) -> str:
        """폴백: 단순 다수결 (동률 시 방어적 액션 우선)."""
        if not opinions:
            return "MAINTAIN"

        priority = {"STOP": 0, "PAUSE": 1, "REDUCE": 2, "WIDEN": 3,
                     "SHIFT_DOWN": 4, "SHIFT_UP": 5, "MAINTAIN": 6}

        counts = {}
        for o in opinions:
            counts[o.action] = counts.get(o.action, 0) + 1

        max_count = max(counts.values())
        candidates = [a for a, c in counts.items() if c == max_count]

        # 동률이면 더 방어적인 액션
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
        else:
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


def format_consensus_for_telegram(result: ConsensusResult) -> str:
    """텔레그램 알림용 합의 결과 포맷."""
    lines = [
        f"🤖 멀티 에이전트 합의",
        f"{'─' * 28}",
        f"최종 결정: {result.final_action}",
        f"합의율: {result.agreement_rate:.0f}%",
        f"{'─' * 28}",
    ]
    for o in result.opinions:
        emoji = {"MAINTAIN": "🟢", "WIDEN": "🟡", "PAUSE": "🟠",
                 "STOP": "🔴", "REDUCE": "🟡", "SHIFT_UP": "⬆️",
                 "SHIFT_DOWN": "⬇️"}.get(o.action, "⚪")
        lines.append(f"{emoji} {o.role}: {o.action} ({o.confidence}/10)")
        lines.append(f"   {o.reason}")
    lines.append(f"{'─' * 28}")
    lines.append(f"📋 {result.reasoning}")
    return "\n".join(lines)
