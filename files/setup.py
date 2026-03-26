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


def ask(prompt: str, default: str = "", secret: bool = False, choices: list = None) -> str:
    """사용자 입력을 받습니다."""
    suffix = ""
    if choices:
        suffix = f" [{'/'.join(choices)}]"
    if default:
        suffix += f" (기본: {default})"
    suffix += ": "

    while True:
        value = input(f"  {prompt}{suffix}").strip()
        if not value and default:
            return default
        if choices and value not in choices:
            print(f"    ⚠ {', '.join(choices)} 중에서 선택해주세요.")
            continue
        if not value:
            print(f"    ⚠ 값을 입력해주세요.")
            continue
        return value


def ask_optional(prompt: str, default: str = "") -> str:
    """선택 입력."""
    suffix = f" (기본: {default})" if default else " (Enter로 건너뛰기)"
    value = input(f"  {prompt}{suffix}: ").strip()
    return value if value else default


def run_setup():
    print(BANNER)

    env = {}

    # ─── 1. OKX API ─────────────────────────────────────
    print("─── OKX API 설정 ───────────────────────────────")
    print()
    env["OKX_API_KEY"] = ask("API Key")
    env["OKX_SECRET_KEY"] = ask("Secret Key")
    env["OKX_PASSPHRASE"] = ask("Passphrase")

    demo = ask("거래 모드", default="demo", choices=["demo", "live"])
    env["DEMO_MODE"] = "true" if demo == "demo" else "false"
    print()

    # ─── 2. 거래 설정 ───────────────────────────────────
    print("─── 거래 설정 ──────────────────────────────────")
    print()
    env["SYMBOL"] = ask("거래 심볼", default="BTC-USDT")
    env["TOTAL_BUDGET"] = ask("총 예산 (USDT)", default="1000")
    env["GRID_BUDGET"] = ask_optional("그리드 예산 (USDT)", default="400")
    env["GRID_LOWER"] = ask_optional("그리드 하단 가격", default="90000")
    env["GRID_UPPER"] = ask_optional("그리드 상단 가격", default="110000")
    env["GRID_COUNT"] = ask_optional("그리드 개수", default="20")
    env["GRID_MODE"] = ask_optional("그리드 모드", default="arithmetic")
    print()

    # ─── 3. LLM 설정 ───────────────────────────────────
    print("─── LLM 설정 (리스크 판단용) ───────────────────")
    print()
    env["LLM_PROVIDER"] = ask("LLM 제공자", default="anthropic", choices=["anthropic", "openai"])
    env["LLM_API_KEY"] = ask("LLM API Key")

    default_model = "claude-sonnet-4-20250514" if env["LLM_PROVIDER"] == "anthropic" else "gpt-4o"
    env["LLM_MODEL"] = ask_optional("모델명", default=default_model)
    print()

    # ─── 4. 텔레그램 (선택) ─────────────────────────────
    print("─── 텔레그램 알림 (선택) ───────────────────────")
    print()
    env["TELEGRAM_TOKEN"] = ask_optional("Bot Token")
    env["TELEGRAM_CHAT_ID"] = ask_optional("Chat ID")
    print()

    # ─── 5. 고급 설정 ───────────────────────────────────
    advanced = ask("고급 설정 변경?", default="n", choices=["y", "n"])
    if advanced == "y":
        print()
        print("─── 고급 설정 ──────────────────────────────────")
        print()
        env["LOOP_INTERVAL_SEC"] = ask_optional("루프 간격 (초)", default="120")
        env["MAX_LOSS_PERCENT"] = ask_optional("손절 기준 (%)", default="15")
        env["LLM_TRIGGER_SCORE"] = ask_optional("LLM 판단 최소 점수", default="55")
        env["CANDLE_INTERVAL"] = ask_optional("캔들 기준", default="1m")
        env["CANDLE_LOOKBACK"] = ask_optional("캔들 개수", default="100")
        print()

    # ─── 저장 ───────────────────────────────────────────
    lines = []
    for key, value in env.items():
        if value:
            lines.append(f"{key}={value}")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("═══════════════════════════════════════════════════")
    print(f"  ✅ 설정 완료! .env 파일 저장됨: {ENV_PATH}")
    print()
    print("  실행하려면:")
    print("    python main_agent.py")
    print("═══════════════════════════════════════════════════")


if __name__ == "__main__":
    run_setup()
