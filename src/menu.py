"""
menu.py
방향키 기반 인터랙티브 메뉴 인터페이스.
"""

import os
import sys

import questionary
from questionary import Choice, Style

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

# ─── 스타일 ──────────────────────────────────────────────

STYLE = Style([
    ("qmark", "fg:cyan bold"),
    ("question", "fg:white bold"),
    ("answer", "fg:cyan bold"),
    ("pointer", "fg:cyan bold"),
    ("highlighted", "fg:cyan bold"),
    ("selected", "fg:green bold"),
    ("separator", "fg:gray"),
    ("instruction", "fg:gray"),
])

BANNER = """
╔══════════════════════════════════════════════════╗
║            ❄️  Snowball Agent  ❄️                ║
║         OKX Adaptive Grid Trading Agent          ║
╚══════════════════════════════════════════════════╝
"""


# ─── 유틸 ────────────────────────────────────────────────

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def header(title: str = ""):
    clear()
    print(BANNER)
    if title:
        print(f"  [{title}]")
        print()


def pause():
    input("\n  아무 키나 눌러 계속...")


def mask(value: str) -> str:
    if not value or len(value) <= 6:
        return "****"
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


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


def is_configured() -> bool:
    env = load_env()
    return bool(env.get("OKX_API_KEY") and env.get("LLM_API_KEY"))


# ─── 메인 메뉴 ──────────────────────────────────────────

def main_menu():
    while True:
        header()
        configured = is_configured()
        status = "✅ 설정 완료" if configured else "❌ 설정 필요"
        print(f"  상태: {status}\n")

        action = questionary.select(
            "메뉴 선택",
            choices=[
                Choice("🚀 에이전트 시작", value="start"),
                Choice("⚙️  설정", value="settings"),
                Choice("📋 현재 설정 보기", value="view"),
                Choice("🚪 종료", value="quit"),
            ],
            style=STYLE,
            instruction="(↑↓ 이동, Enter 선택)",
        ).ask()

        if action is None or action == "quit":
            clear()
            print("  👋 종료합니다.")
            sys.exit(0)

        if action == "start":
            if not configured:
                print("\n  ⚠ 먼저 설정을 완료해주세요.")
                pause()
                continue
            return "start"

        if action == "settings":
            settings_menu()

        if action == "view":
            view_settings()


# ─── 설정 메뉴 ──────────────────────────────────────────

def settings_menu():
    while True:
        header("설정")
        env = load_env()

        okx = "✅" if env.get("OKX_API_KEY") else "❌"
        llm = "✅" if env.get("LLM_API_KEY") else "❌"
        tg = "✅" if env.get("TELEGRAM_TOKEN") else "⬜"

        action = questionary.select(
            "설정 항목",
            choices=[
                Choice(f"{okx} OKX API", value="okx"),
                Choice(f"{okx} 거래 설정", value="trading"),
                Choice(f"{llm} LLM 설정", value="llm"),
                Choice(f"{tg} 텔레그램 알림", value="telegram"),
                Choice("⬜ 고급 설정", value="advanced"),
                questionary.Separator(),
                Choice("← 뒤로", value="back"),
            ],
            style=STYLE,
            instruction="(↑↓ 이동, Enter 선택)",
        ).ask()

        if action is None or action == "back":
            return

        if action == "okx":
            setup_okx(env)
        elif action == "trading":
            setup_trading(env)
        elif action == "llm":
            setup_llm(env)
        elif action == "telegram":
            setup_telegram(env)
        elif action == "advanced":
            setup_advanced(env)


# ─── OKX API 설정 ───────────────────────────────────────

def setup_okx(env: dict):
    header("OKX API 설정")

    api_key = questionary.text(
        "API Key:",
        style=STYLE,
    ).ask()
    if api_key is None:
        return
    env["OKX_API_KEY"] = api_key

    secret = questionary.password(
        "Secret Key:",
        style=STYLE,
    ).ask()
    if secret is None:
        return
    env["OKX_SECRET_KEY"] = secret

    passphrase = questionary.password(
        "Passphrase:",
        style=STYLE,
    ).ask()
    if passphrase is None:
        return
    env["OKX_PASSPHRASE"] = passphrase

    mode = questionary.select(
        "거래 모드",
        choices=[
            Choice("Demo (모의거래)", value="true"),
            Choice("Live (실거래)", value="false"),
        ],
        style=STYLE,
    ).ask()
    if mode is None:
        return
    env["DEMO_MODE"] = mode

    save_env(env)
    print("\n  ✅ OKX API 설정 저장 완료")
    pause()


# ─── 거래 설정 ───────────────────────────────────────────

def setup_trading(env: dict):
    header("거래 설정")

    symbol = questionary.select(
        "거래 심볼",
        choices=[
            Choice("BTC-USDT", value="BTC-USDT"),
            Choice("ETH-USDT", value="ETH-USDT"),
            Choice("SOL-USDT", value="SOL-USDT"),
            Choice("XRP-USDT", value="XRP-USDT"),
        ],
        default="BTC-USDT",
        style=STYLE,
    ).ask()
    if symbol is None:
        return
    env["SYMBOL"] = symbol

    budget = questionary.text(
        "총 예산 (USDT):",
        default=env.get("TOTAL_BUDGET", "1000"),
        validate=lambda v: True if _is_number(v) else "숫자를 입력해주세요",
        style=STYLE,
    ).ask()
    if budget is None:
        return
    env["TOTAL_BUDGET"] = budget

    grid_budget = questionary.text(
        "그리드 예산 (USDT):",
        default=env.get("GRID_BUDGET", "400"),
        validate=lambda v: True if _is_number(v) else "숫자를 입력해주세요",
        style=STYLE,
    ).ask()
    if grid_budget is None:
        return
    env["GRID_BUDGET"] = grid_budget

    lower = questionary.text(
        "그리드 하단 가격:",
        default=env.get("GRID_LOWER", "90000"),
        validate=lambda v: True if _is_number(v) else "숫자를 입력해주세요",
        style=STYLE,
    ).ask()
    if lower is None:
        return
    env["GRID_LOWER"] = lower

    upper = questionary.text(
        "그리드 상단 가격:",
        default=env.get("GRID_UPPER", "110000"),
        validate=lambda v: True if _is_number(v) else "숫자를 입력해주세요",
        style=STYLE,
    ).ask()
    if upper is None:
        return
    env["GRID_UPPER"] = upper

    count = questionary.text(
        "그리드 개수:",
        default=env.get("GRID_COUNT", "20"),
        validate=lambda v: True if _is_int(v) else "정수를 입력해주세요",
        style=STYLE,
    ).ask()
    if count is None:
        return
    env["GRID_COUNT"] = count

    mode = questionary.select(
        "그리드 모드",
        choices=[
            Choice("Arithmetic (등차)", value="arithmetic"),
            Choice("Geometric (등비)", value="geometric"),
        ],
        style=STYLE,
    ).ask()
    if mode is None:
        return
    env["GRID_MODE"] = mode

    save_env(env)
    print("\n  ✅ 거래 설정 저장 완료")
    pause()


# ─── LLM 설정 ───────────────────────────────────────────

def setup_llm(env: dict):
    header("LLM 설정")

    provider = questionary.select(
        "LLM 제공자",
        choices=[
            Choice("Anthropic (Claude)", value="anthropic"),
            Choice("OpenAI (GPT)", value="openai"),
        ],
        style=STYLE,
    ).ask()
    if provider is None:
        return
    env["LLM_PROVIDER"] = provider

    api_key = questionary.password(
        "API Key:",
        style=STYLE,
    ).ask()
    if api_key is None:
        return
    env["LLM_API_KEY"] = api_key

    if provider == "anthropic":
        models = [
            Choice("Claude Sonnet 4 (추천)", value="claude-sonnet-4-20250514"),
            Choice("Claude Opus 4", value="claude-opus-4-20250514"),
            Choice("Claude Haiku 4 (경량)", value="claude-haiku-4-20250414"),
        ]
    else:
        models = [
            Choice("GPT-4o (추천)", value="gpt-4o"),
            Choice("GPT-4o Mini (경량)", value="gpt-4o-mini"),
            Choice("GPT-4.1", value="gpt-4.1"),
        ]

    model = questionary.select(
        "모델 선택",
        choices=models,
        style=STYLE,
    ).ask()
    if model is None:
        return
    env["LLM_MODEL"] = model

    save_env(env)
    print("\n  ✅ LLM 설정 저장 완료")
    pause()


# ─── 텔레그램 설정 ───────────────────────────────────────

def setup_telegram(env: dict):
    header("텔레그램 알림 설정")

    use = questionary.confirm(
        "텔레그램 알림을 사용할까요?",
        default=bool(env.get("TELEGRAM_TOKEN")),
        style=STYLE,
    ).ask()

    if use is None:
        return

    if not use:
        env.pop("TELEGRAM_TOKEN", None)
        env.pop("TELEGRAM_CHAT_ID", None)
        save_env(env)
        print("\n  ✅ 텔레그램 알림 해제됨")
        pause()
        return

    token = questionary.text(
        "Bot Token:",
        default=env.get("TELEGRAM_TOKEN", ""),
        style=STYLE,
    ).ask()
    if token is None:
        return
    env["TELEGRAM_TOKEN"] = token

    chat_id = questionary.text(
        "Chat ID:",
        default=env.get("TELEGRAM_CHAT_ID", ""),
        style=STYLE,
    ).ask()
    if chat_id is None:
        return
    env["TELEGRAM_CHAT_ID"] = chat_id

    # 알림 받을 상태 선택 (중복 선택)
    notify_states = questionary.checkbox(
        "알림 받을 상태 (Space로 선택, Enter로 확인)",
        choices=[
            Choice("CAUTION (주의)", value="CAUTION", checked=True),
            Choice("WARNING (경고)", value="WARNING", checked=True),
            Choice("EMERGENCY (긴급)", value="EMERGENCY", checked=True),
        ],
        style=STYLE,
        instruction="(↑↓ 이동, Space 선택/해제, Enter 확인)",
    ).ask()
    if notify_states is None:
        return
    env["NOTIFY_ON_STATES"] = ",".join(notify_states)

    save_env(env)
    print("\n  ✅ 텔레그램 설정 저장 완료")
    pause()


# ─── 고급 설정 ───────────────────────────────────────────

def setup_advanced(env: dict):
    header("고급 설정")

    loop = questionary.select(
        "루프 간격",
        choices=[
            Choice("30초", value="30"),
            Choice("1분", value="60"),
            Choice("2분 (기본)", value="120"),
            Choice("5분", value="300"),
            Choice("10분", value="600"),
        ],
        default="120",
        style=STYLE,
    ).ask()
    if loop is None:
        return
    env["LOOP_INTERVAL_SEC"] = loop

    loss = questionary.text(
        "손절 기준 (%):",
        default=env.get("MAX_LOSS_PERCENT", "15"),
        validate=lambda v: True if _is_number(v) else "숫자를 입력해주세요",
        style=STYLE,
    ).ask()
    if loss is None:
        return
    env["MAX_LOSS_PERCENT"] = loss

    trigger = questionary.text(
        "LLM 판단 최소 점수:",
        default=env.get("LLM_TRIGGER_SCORE", "55"),
        validate=lambda v: True if _is_int(v) else "정수를 입력해주세요",
        style=STYLE,
    ).ask()
    if trigger is None:
        return
    env["LLM_TRIGGER_SCORE"] = trigger

    candle = questionary.select(
        "캔들 기준",
        choices=[
            Choice("1분", value="1m"),
            Choice("5분", value="5m"),
            Choice("15분", value="15m"),
            Choice("1시간", value="1H"),
        ],
        style=STYLE,
    ).ask()
    if candle is None:
        return
    env["CANDLE_INTERVAL"] = candle

    lookback = questionary.text(
        "캔들 개수:",
        default=env.get("CANDLE_LOOKBACK", "100"),
        validate=lambda v: True if _is_int(v) else "정수를 입력해주세요",
        style=STYLE,
    ).ask()
    if lookback is None:
        return
    env["CANDLE_LOOKBACK"] = lookback

    save_env(env)
    print("\n  ✅ 고급 설정 저장 완료")
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

    print("  ┌──────────────────────────────────────────┐")
    print(f"  │ {'OKX API':<13}: {mask(env.get('OKX_API_KEY', '')):<26}│")
    print(f"  │ {'거래 모드':<11}: {mode:<26}│")
    print("  ├──────────────────────────────────────────┤")
    print(f"  │ {'심볼':<12}: {env.get('SYMBOL', '-'):<26}│")
    print(f"  │ {'총 예산':<11}: {env.get('TOTAL_BUDGET', '-') + ' USDT':<26}│")
    print(f"  │ {'그리드 범위':<10}: {env.get('GRID_LOWER', '-') + ' ~ ' + env.get('GRID_UPPER', '-'):<26}│")
    print(f"  │ {'그리드 개수':<10}: {env.get('GRID_COUNT', '-'):<26}│")
    print(f"  │ {'그리드 모드':<10}: {env.get('GRID_MODE', '-'):<26}│")
    print("  ├──────────────────────────────────────────┤")
    llm_info = f"{env.get('LLM_PROVIDER', '-')} / {env.get('LLM_MODEL', '-')}"
    print(f"  │ {'LLM':<13}: {llm_info:<26}│")
    print(f"  │ {'LLM Key':<13}: {mask(env.get('LLM_API_KEY', '')):<26}│")
    print("  ├──────────────────────────────────────────┤")
    tg = "설정됨" if env.get("TELEGRAM_TOKEN") else "미설정"
    loop = env.get("LOOP_INTERVAL_SEC", "120")
    loss = env.get("MAX_LOSS_PERCENT", "15")
    print(f"  │ {'텔레그램':<11}: {tg:<26}│")
    print(f"  │ {'루프 간격':<11}: {loop + '초':<26}│")
    print(f"  │ {'손절 기준':<11}: {loss + '%':<26}│")
    print("  └──────────────────────────────────────────┘")
    pause()


# ─── 검증 헬퍼 ───────────────────────────────────────────

def _is_number(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _is_int(v: str) -> bool:
    try:
        int(v)
        return True
    except ValueError:
        return False
