"""
setup.py
초기 설정 CLI 위저드. .env 파일을 생성합니다.
"""

import os
import sys

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

BANNER = """
╔══════════════════════════════════════════════════╗
║         ❄️  Snowball Setup Wizard  ❄️            ║
║         OKX Adaptive Grid Trading Agent          ║
╚══════════════════════════════════════════════════╝
"""


# ─── 입력 헬퍼 ──────────────────────────────────────────

def pick(prompt: str, options: list[tuple[str, str]], default_idx: int = 0) -> str:
    """번호 선택형 입력. options: [(value, label), ...]"""
    print(f"  {prompt}")
    for i, (_, label) in enumerate(options):
        marker = " >" if i == default_idx else "  "
        print(f"  {marker} {i + 1}. {label}")

    while True:
        raw = input(f"  선택 (기본: {default_idx + 1}): ").strip()
        if not raw:
            return options[default_idx][0]
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass
        print(f"    ⚠ 1~{len(options)} 사이 번호를 입력해주세요.")


def ask_key(prompt: str) -> str:
    """필수 키 입력 (빈 값 불가)."""
    while True:
        value = input(f"  {prompt}: ").strip()
        if value:
            return value
        print("    ⚠ 값을 입력해주세요.")


def ask_key_optional(prompt: str) -> str:
    """선택 키 입력 (Enter로 건너뛰기)."""
    value = input(f"  {prompt} (Enter로 건너뛰기): ").strip()
    return value


def ask_number(prompt: str, default: float) -> str:
    """숫자 입력 (검증 포함)."""
    while True:
        raw = input(f"  {prompt} (기본: {default}): ").strip()
        if not raw:
            return str(default)
        try:
            float(raw)
            return raw
        except ValueError:
            print("    ⚠ 숫자를 입력해주세요.")


def ask_int(prompt: str, default: int) -> str:
    """정수 입력 (검증 포함)."""
    while True:
        raw = input(f"  {prompt} (기본: {default}): ").strip()
        if not raw:
            return str(default)
        try:
            int(raw)
            return raw
        except ValueError:
            print("    ⚠ 정수를 입력해주세요.")


def confirm(prompt: str, default: bool = True) -> bool:
    """Y/N 확인."""
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ─── 메인 셋업 ──────────────────────────────────────────

def run_setup():
    print(BANNER)

    env = {}

    # ─── 1. OKX API ─────────────────────────────────────
    print("─── OKX API 설정 ───────────────────────────────")
    print()
    env["OKX_API_KEY"] = ask_key("API Key")
    env["OKX_SECRET_KEY"] = ask_key("Secret Key")
    env["OKX_PASSPHRASE"] = ask_key("Passphrase")
    print()

    demo = pick("거래 모드", [
        ("true", "Demo (모의거래)"),
        ("false", "Live (실거래)"),
    ], default_idx=0)
    env["DEMO_MODE"] = demo
    print()

    # ─── 2. 거래 설정 ───────────────────────────────────
    print("─── 거래 설정 ──────────────────────────────────")
    print()

    env["SYMBOL"] = pick("거래 심볼", [
        ("BTC-USDT", "BTC-USDT"),
        ("ETH-USDT", "ETH-USDT"),
        ("SOL-USDT", "SOL-USDT"),
        ("XRP-USDT", "XRP-USDT"),
    ], default_idx=0)
    print()

    env["TOTAL_BUDGET"] = ask_number("총 예산 (USDT)", 1000)
    env["GRID_BUDGET"] = ask_number("그리드 예산 (USDT)", 400)
    env["GRID_LOWER"] = ask_number("그리드 하단 가격", 90000)
    env["GRID_UPPER"] = ask_number("그리드 상단 가격", 110000)
    env["GRID_COUNT"] = ask_int("그리드 개수", 20)
    print()

    env["GRID_MODE"] = pick("그리드 모드", [
        ("arithmetic", "Arithmetic (등차)"),
        ("geometric", "Geometric (등비)"),
    ], default_idx=0)
    print()

    # ─── 3. LLM 설정 ───────────────────────────────────
    print("─── LLM 설정 (리스크 판단용) ───────────────────")
    print()

    env["LLM_PROVIDER"] = pick("LLM 제공자", [
        ("anthropic", "Anthropic (Claude)"),
        ("openai", "OpenAI (GPT)"),
        ("grok", "xAI (Grok)"),
        ("gemini", "Google (Gemini)"),
    ], default_idx=0)
    print()

    env["LLM_API_KEY"] = ask_key("LLM API Key")
    print()

    if env["LLM_PROVIDER"] == "anthropic":
        models = [
            ("claude-sonnet-4-20250514", "Claude Sonnet 4 (추천)"),
            ("claude-opus-4-20250514", "Claude Opus 4"),
            ("claude-haiku-4-20250414", "Claude Haiku 4 (경량)"),
        ]
    elif env["LLM_PROVIDER"] == "openai":
        models = [
            ("gpt-4o", "GPT-4o (추천)"),
            ("gpt-4o-mini", "GPT-4o Mini (경량)"),
            ("gpt-4.1", "GPT-4.1"),
        ]
    elif env["LLM_PROVIDER"] == "grok":
        models = [
            ("grok-3-mini", "Grok 3 Mini (추천)"),
            ("grok-3", "Grok 3"),
        ]
    else:  # gemini
        models = [
            ("gemini-2.5-flash", "Gemini 2.5 Flash (추천)"),
            ("gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("gemini-2.0-flash", "Gemini 2.0 Flash (경량)"),
        ]

    env["LLM_MODEL"] = pick("모델 선택", models, default_idx=0)
    print()

    # ─── 4. 텔레그램 (선택) ─────────────────────────────
    print("─── 텔레그램 알림 (선택) ───────────────────────")
    print()
    use_telegram = confirm("텔레그램 알림을 사용할까요?", default=False)
    if use_telegram:
        env["TELEGRAM_TOKEN"] = ask_key("Bot Token")
        env["TELEGRAM_CHAT_ID"] = ask_key("Chat ID")
    print()

    # ─── 5. 고급 설정 ───────────────────────────────────
    if confirm("고급 설정을 변경할까요?", default=False):
        print()
        print("─── 고급 설정 ──────────────────────────────────")
        print()
        env["LOOP_INTERVAL_SEC"] = ask_int("루프 간격 (초)", 120)
        env["MAX_LOSS_PERCENT"] = ask_number("손절 기준 (%)", 15)
        env["LLM_TRIGGER_SCORE"] = ask_int("LLM 판단 최소 점수", 55)
        print()

        env["CANDLE_INTERVAL"] = pick("캔들 기준", [
            ("1m", "1분"),
            ("5m", "5분"),
            ("15m", "15분"),
            ("1H", "1시간"),
        ], default_idx=0)
        print()

        env["CANDLE_LOOKBACK"] = ask_int("캔들 개수", 100)
        print()

    # ─── 요약 ───────────────────────────────────────────
    print("═══════════════════════════════════════════════════")
    print("  📋 설정 요약")
    print("═══════════════════════════════════════════════════")
    print(f"  거래 모드  : {'Demo (모의거래)' if env['DEMO_MODE'] == 'true' else '⚠ Live (실거래)'}")
    print(f"  심볼      : {env['SYMBOL']}")
    print(f"  총 예산    : {env['TOTAL_BUDGET']} USDT")
    print(f"  그리드     : {env['GRID_LOWER']} ~ {env['GRID_UPPER']} ({env['GRID_COUNT']}개)")
    print(f"  LLM       : {env['LLM_PROVIDER']} / {env['LLM_MODEL']}")
    tg = "설정됨" if env.get("TELEGRAM_TOKEN") else "미설정"
    print(f"  텔레그램   : {tg}")
    print("═══════════════════════════════════════════════════")
    print()

    if not confirm("이대로 저장할까요?", default=True):
        print("  ❌ 취소되었습니다. 다시 실행해주세요.")
        return False

    # ─── 저장 ───────────────────────────────────────────
    lines = []
    for key, value in env.items():
        if value:
            lines.append(f"{key}={value}")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print()
    print(f"  ✅ 설정 완료! .env 저장됨")
    print()
    return True


if __name__ == "__main__":
    run_setup()
