"""
menu.py
메뉴 기반 인터페이스.
"""

import os
import sys

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


# ─── 유틸 ────────────────────────────────────────────────

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def pause():
    input("\n  아무 키나 눌러 계속...")


def header(title: str = ""):
    clear()
    print("╔══════════════════════════════════════════════════╗")
    print("║            ❄️  Snowball Agent  ❄️                ║")
    print("╚══════════════════════════════════════════════════╝")
    if title:
        print(f"  [{title}]")
    print()


# ─── 입력 헬퍼 ──────────────────────────────────────────

def pick(prompt: str, options: list[tuple[str, str]], default_idx: int = 0) -> str:
    """번호 선택형."""
    print(f"  {prompt}")
    for i, (_, label) in enumerate(options):
        marker = " >" if i == default_idx else "  "
        print(f"  {marker} {i + 1}. {label}")

    while True:
        raw = input(f"\n  선택 (기본: {default_idx + 1}): ").strip()
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
    """필수 키 입력."""
    while True:
        value = input(f"  {prompt}: ").strip()
        if value:
            return value
        print("    ⚠ 값을 입력해주세요.")


def ask_number(prompt: str, default: float) -> str:
    """숫자 입력."""
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
    """정수 입력."""
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
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ─── .env 읽기/쓰기 ─────────────────────────────────────

def load_env() -> dict:
    env = {}
    if not os.path.exists(ENV_PATH):
        return env
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def save_env(env: dict):
    lines = [f"{k}={v}" for k, v in env.items() if v]
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def mask(value: str) -> str:
    if not value or len(value) <= 6:
        return "****"
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


def is_configured() -> bool:
    env = load_env()
    return bool(env.get("OKX_API_KEY") and env.get("LLM_API_KEY"))


# ─── 메인 메뉴 ──────────────────────────────────────────

def main_menu():
    while True:
        header()
        configured = is_configured()
        status = "✅ 설정 완료" if configured else "❌ 설정 필요"

        print(f"  상태: {status}")
        print()
        print("  1. 🚀 에이전트 시작")
        print("  2. ⚙️  설정")
        print("  3. 📋 현재 설정 보기")
        print("  4. 🚪 종료")
        print()

        choice = input("  선택: ").strip()

        if choice == "1":
            if not configured:
                header()
                print("  ⚠ 먼저 설정을 완료해주세요.")
                pause()
                continue
            return "start"

        elif choice == "2":
            settings_menu()

        elif choice == "3":
            view_settings()

        elif choice == "4":
            clear()
            print("  👋 종료합니다.")
            sys.exit(0)


# ─── 설정 메뉴 ──────────────────────────────────────────

def settings_menu():
    while True:
        header("설정")
        env = load_env()

        has_okx = bool(env.get("OKX_API_KEY"))
        has_llm = bool(env.get("LLM_API_KEY"))
        has_tg = bool(env.get("TELEGRAM_TOKEN"))

        okx_status = "✅" if has_okx else "❌"
        llm_status = "✅" if has_llm else "❌"
        tg_status = "✅" if has_tg else "⬜"

        print(f"  1. {okx_status} OKX API")
        print(f"  2. {okx_status} 거래 설정")
        print(f"  3. {llm_status} LLM 설정")
        print(f"  4. {tg_status} 텔레그램 알림")
        print(f"  5. ⬜ 고급 설정")
        print()
        print("  0. ← 뒤로")
        print()

        choice = input("  선택: ").strip()

        if choice == "1":
            setup_okx(env)
        elif choice == "2":
            setup_trading(env)
        elif choice == "3":
            setup_llm(env)
        elif choice == "4":
            setup_telegram(env)
        elif choice == "5":
            setup_advanced(env)
        elif choice == "0":
            return


def setup_okx(env: dict):
    header("OKX API 설정")
    env["OKX_API_KEY"] = ask_key("API Key")
    env["OKX_SECRET_KEY"] = ask_key("Secret Key")
    env["OKX_PASSPHRASE"] = ask_key("Passphrase")
    print()
    env["DEMO_MODE"] = pick("거래 모드", [
        ("true", "Demo (모의거래)"),
        ("false", "Live (실거래)"),
    ], default_idx=0)
    save_env(env)
    print("\n  ✅ 저장 완료")
    pause()


def setup_trading(env: dict):
    header("거래 설정")
    env["SYMBOL"] = pick("거래 심볼", [
        ("BTC-USDT", "BTC-USDT"),
        ("ETH-USDT", "ETH-USDT"),
        ("SOL-USDT", "SOL-USDT"),
        ("XRP-USDT", "XRP-USDT"),
    ], default_idx=0)
    print()
    env["TOTAL_BUDGET"] = ask_number("총 예산 (USDT)", float(env.get("TOTAL_BUDGET", 1000)))
    env["GRID_BUDGET"] = ask_number("그리드 예산 (USDT)", float(env.get("GRID_BUDGET", 400)))
    env["GRID_LOWER"] = ask_number("그리드 하단 가격", float(env.get("GRID_LOWER", 90000)))
    env["GRID_UPPER"] = ask_number("그리드 상단 가격", float(env.get("GRID_UPPER", 110000)))
    env["GRID_COUNT"] = ask_int("그리드 개수", int(env.get("GRID_COUNT", 20)))
    print()
    env["GRID_MODE"] = pick("그리드 모드", [
        ("arithmetic", "Arithmetic (등차)"),
        ("geometric", "Geometric (등비)"),
    ], default_idx=0)
    save_env(env)
    print("\n  ✅ 저장 완료")
    pause()


def setup_llm(env: dict):
    header("LLM 설정")
    env["LLM_PROVIDER"] = pick("LLM 제공자", [
        ("anthropic", "Anthropic (Claude)"),
        ("openai", "OpenAI (GPT)"),
    ], default_idx=0)
    print()
    env["LLM_API_KEY"] = ask_key("API Key")
    print()

    if env["LLM_PROVIDER"] == "anthropic":
        models = [
            ("claude-sonnet-4-20250514", "Claude Sonnet 4 (추천)"),
            ("claude-opus-4-20250514", "Claude Opus 4"),
            ("claude-haiku-4-20250414", "Claude Haiku 4 (경량)"),
        ]
    else:
        models = [
            ("gpt-4o", "GPT-4o (추천)"),
            ("gpt-4o-mini", "GPT-4o Mini (경량)"),
            ("gpt-4.1", "GPT-4.1"),
        ]

    env["LLM_MODEL"] = pick("모델 선택", models, default_idx=0)
    save_env(env)
    print("\n  ✅ 저장 완료")
    pause()


def setup_telegram(env: dict):
    header("텔레그램 알림 설정")

    if env.get("TELEGRAM_TOKEN"):
        print(f"  현재: {mask(env['TELEGRAM_TOKEN'])}")
        print()
        if not confirm("다시 설정할까요?", default=True):
            return

    use = confirm("텔레그램 알림을 사용할까요?", default=True)
    if use:
        env["TELEGRAM_TOKEN"] = ask_key("Bot Token")
        env["TELEGRAM_CHAT_ID"] = ask_key("Chat ID")
    else:
        env.pop("TELEGRAM_TOKEN", None)
        env.pop("TELEGRAM_CHAT_ID", None)

    save_env(env)
    print("\n  ✅ 저장 완료")
    pause()


def setup_advanced(env: dict):
    header("고급 설정")
    env["LOOP_INTERVAL_SEC"] = ask_int("루프 간격 (초)", int(env.get("LOOP_INTERVAL_SEC", 120)))
    env["MAX_LOSS_PERCENT"] = ask_number("손절 기준 (%)", float(env.get("MAX_LOSS_PERCENT", 15)))
    env["LLM_TRIGGER_SCORE"] = ask_int("LLM 판단 최소 점수", int(env.get("LLM_TRIGGER_SCORE", 55)))
    print()
    env["CANDLE_INTERVAL"] = pick("캔들 기준", [
        ("1m", "1분"),
        ("5m", "5분"),
        ("15m", "15분"),
        ("1H", "1시간"),
    ], default_idx=0)
    print()
    env["CANDLE_LOOKBACK"] = ask_int("캔들 개수", int(env.get("CANDLE_LOOKBACK", 100)))
    save_env(env)
    print("\n  ✅ 저장 완료")
    pause()


# ─── 설정 보기 ───────────────────────────────────────────

def view_settings():
    header("현재 설정")
    env = load_env()

    if not env:
        print("  설정 파일이 없습니다. 먼저 설정을 진행해주세요.")
        pause()
        return

    mode = "Demo (모의거래)" if env.get("DEMO_MODE", "true") == "true" else "⚠ Live (실거래)"
    provider = env.get("LLM_PROVIDER", "-")
    model = env.get("LLM_MODEL", "-")
    tg = "설정됨" if env.get("TELEGRAM_TOKEN") else "미설정"
    loop = env.get("LOOP_INTERVAL_SEC", "120")
    loss = env.get("MAX_LOSS_PERCENT", "15")

    print("  ┌─────────────────────────────────────────────┐")
    print(f"  │ OKX API Key    : {mask(env.get('OKX_API_KEY', '')):<27}│")
    print(f"  │ 거래 모드       : {mode:<27}│")
    print("  ├─────────────────────────────────────────────┤")
    print(f"  │ 심볼            : {env.get('SYMBOL', '-'):<27}│")
    print(f"  │ 총 예산          : {env.get('TOTAL_BUDGET', '-')} USDT{'':<19}│")
    print(f"  │ 그리드 범위      : {env.get('GRID_LOWER', '-')} ~ {env.get('GRID_UPPER', '-'):<14}│")
    print(f"  │ 그리드 개수      : {env.get('GRID_COUNT', '-'):<27}│")
    print(f"  │ 그리드 모드      : {env.get('GRID_MODE', '-'):<27}│")
    print("  ├─────────────────────────────────────────────┤")
    print(f"  │ LLM             : {provider} / {model:<14}│")
    print(f"  │ LLM API Key     : {mask(env.get('LLM_API_KEY', '')):<27}│")
    print("  ├─────────────────────────────────────────────┤")
    print(f"  │ 텔레그램         : {tg:<27}│")
    print(f"  │ 루프 간격        : {loop}초{'':<24}│")
    print(f"  │ 손절 기준        : {loss}%{'':<24}│")
    print("  └─────────────────────────────────────────────┘")
    pause()
