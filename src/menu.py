"""
menu.py
방향키 기반 인터랙티브 메뉴 인터페이스.
"""

import os
import sys
import json
import unicodedata

import httpx
import questionary
from questionary import Choice, Style


# ─── 한글 더블 위드 대응 유틸 ────────────────────────────────

def _vw(s: str) -> int:
    """문자열의 터미널 표시 폭 계산 (CJK/이모지 = 2칸)."""
    w = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 2
        else:
            w += 1
    return w


def _vpad(s: str, width: int, align: str = "left") -> str:
    """터미널 표시 폭 기준으로 패딩."""
    pad = max(0, width - _vw(s))
    if align == "right":
        return " " * pad + s
    return s + " " * pad


def _vtrunc(s: str, width: int) -> str:
    """터미널 표시 폭 기준으로 자르기."""
    w = 0
    for i, ch in enumerate(s):
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width - 3:
            return s[:i] + "..."
        w += cw
    return s


def _box_line(content: str, inner: int = 46) -> str:
    """박스 라인 생성 (내부 폭 고정)."""
    return f"  │ {_vpad(content, inner)}│"

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
║           Snowball Agent                         ║
║       OKX Adaptive Grid Trading Agent            ║
╚══════════════════════════════════════════════════╝
"""


# ─── 유틸 ────────────────────────────────────────────────

def clear():
    print("\033[2J\033[H", end="", flush=True)


def print_disclaimer():
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    print(f"{RED}  ⚠️  이 소프트웨어는 투자 조언이 아니며, 모든 손실은{RESET}")
    print(f"{RED}  사용자 본인 책임입니다. 감당 가능한 금액만 투자하세요.{RESET}")
    print()


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
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
                except (ValueError, UnicodeDecodeError):
                    continue
    except (OSError, UnicodeDecodeError) as e:
        print(f"\n  ⚠ 설정 파일 읽기 오류: {e}")
    return env


def save_env(env: dict):
    lines = [f"{k}={v}" for k, v in env.items() if v]
    try:
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print(f"\n  ⚠ 설정 파일 저장 오류: {e}")


def is_configured() -> bool:
    env = load_env()
    return bool(env.get("OKX_API_KEY") and env.get("LLM_API_KEY"))


# ─── 초기 설정 위자드 ──────────────────────────────────────

def initial_setup_wizard():
    """필수 설정이 없을 때 자동 실행되는 초기 설정 위자드."""
    env = load_env()

    header("초기 설정")
    print("  처음 실행이시군요! 필수 설정을 순서대로 진행합니다.")
    print()

    # ── Step 1: OKX API ──
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  📌 Step 1/3 — OKX API 설정")
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    setup_okx(env)

    if not env.get("OKX_API_KEY"):
        print("\n  ⚠ OKX API 설정이 필요합니다. 다시 시도해주세요.")
        pause()
        return False

    # ── Step 2: LLM ──
    header("초기 설정")
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  📌 Step 2/3 — LLM 설정")
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    setup_llm(env)

    if not env.get("LLM_API_KEY"):
        print("\n  ⚠ LLM API 설정이 필요합니다. 다시 시도해주세요.")
        pause()
        return False

    # ── Step 3: 거래 설정 ──
    header("초기 설정")
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  📌 Step 3/3 — 거래 설정")
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    setup_trading(env)

    # ── 텔레그램 (선택) ──
    header("초기 설정")
    try:
        use_tg = questionary.confirm(
            "텔레그램 알림도 지금 설정할까요? (나중에 해도 됩니다)",
            default=False,
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        use_tg = False

    if use_tg:
        setup_telegram(env)

    # ── 완료 ──
    header("초기 설정 완료")
    print("  ✅ 모든 필수 설정이 완료되었습니다!")
    print()
    view_settings()
    return True


# ─── 메인 메뉴 ──────────────────────────────────────────

def main_menu():
    try:
        # 필수 설정이 없으면 초기 설정 위자드 자동 실행
        if not is_configured():
            try:
                success = initial_setup_wizard()
            except KeyboardInterrupt:
                clear()
                print("\n  👋 종료합니다.")
                sys.exit(0)
            if not success:
                # 위자드 실패 시 일반 메뉴로 진입
                pass

        while True:
            header()
            print_disclaimer()
            configured = is_configured()
            status = "✅ 설정 완료" if configured else "❌ 설정 필요"
            print(f"  상태: {status}\n")

            try:
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
            except (KeyboardInterrupt, EOFError):
                action = None

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
    except KeyboardInterrupt:
        clear()
        print("\n  👋 종료합니다.")
        sys.exit(0)


# ─── 설정 메뉴 ──────────────────────────────────────────

def settings_menu():
    while True:
        header("설정")
        env = load_env()

        okx = "✅" if env.get("OKX_API_KEY") else "❌"
        llm = "✅" if env.get("LLM_API_KEY") else "❌"
        tg = "✅" if env.get("TELEGRAM_TOKEN") else "⬜"

        try:
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
        except (KeyboardInterrupt, EOFError):
            return

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

    try:
        api_key = questionary.text(
            "API Key:",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if api_key is None:
        return
    env["OKX_API_KEY"] = api_key

    try:
        secret = questionary.password(
            "Secret Key:",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if secret is None:
        return
    env["OKX_SECRET_KEY"] = secret

    try:
        passphrase = questionary.password(
            "Passphrase:",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if passphrase is None:
        return
    env["OKX_PASSPHRASE"] = passphrase

    try:
        mode = questionary.select(
            "거래 모드",
            choices=[
                Choice("Demo (모의거래)", value="true"),
                Choice("Live (실거래)", value="false"),
            ],
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if mode is None:
        return
    env["DEMO_MODE"] = mode

    save_env(env)
    print("\n  ✅ OKX API 설정 저장 완료")
    pause()


# ─── 거래 설정 ───────────────────────────────────────────

def setup_trading(env: dict):
    header("거래 설정")

    try:
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
    except (KeyboardInterrupt, EOFError):
        return
    if symbol is None:
        return
    env["SYMBOL"] = symbol

    try:
        budget = questionary.text(
            "총 예산 (USDT):",
            default=env.get("TOTAL_BUDGET", "1000"),
            validate=lambda v: True if _is_number(v) else "숫자를 입력해주세요",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if budget is None:
        return
    env["TOTAL_BUDGET"] = budget

    # AI 자동 추천 (예산 배분 + 그리드 범위 모두 LLM이 결정)
    print("\n  🤖 AI가 시세를 분석하고 최적 설정을 추천합니다...")
    auto_result = _auto_grid_settings(env, symbol, float(budget))
    if auto_result:
        env.update(auto_result)
        save_env(env)
        print("\n  ✅ AI 추천 설정 저장 완료")
        pause()
        return

    # AI 추천 실패 시 수동 입력 폴백
    print("\n  ⚠ AI 추천 실패. 수동 입력으로 전환합니다.")
    print()

    try:
        grid_budget = questionary.text(
            "그리드 예산 (USDT):",
            default=env.get("GRID_BUDGET", str(int(float(budget) * 0.4))),
            validate=lambda v: True if _is_number(v) else "숫자를 입력해주세요",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if grid_budget is None:
        return
    env["GRID_BUDGET"] = grid_budget
    env["RESERVE_BUDGET"] = str(round(float(budget) - float(grid_budget), 2))

    try:
        lower = questionary.text(
            "그리드 하단 가격:",
            default=env.get("GRID_LOWER", "90000"),
            validate=lambda v: True if _is_number(v) else "숫자를 입력해주세요",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if lower is None:
        return
    env["GRID_LOWER"] = lower

    try:
        upper = questionary.text(
            "그리드 상단 가격:",
            default=env.get("GRID_UPPER", "110000"),
            validate=lambda v: True if _is_number(v) else "숫자를 입력해주세요",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if upper is None:
        return
    env["GRID_UPPER"] = upper

    try:
        count = questionary.text(
            "그리드 개수:",
            default=env.get("GRID_COUNT", "20"),
            validate=lambda v: True if _is_int(v) else "정수를 입력해주세요",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if count is None:
        return
    env["GRID_COUNT"] = count

    try:
        mode = questionary.select(
            "그리드 모드",
            choices=[
                Choice("Arithmetic (등차)", value="arithmetic"),
                Choice("Geometric (등비)", value="geometric"),
            ],
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if mode is None:
        return
    env["GRID_MODE"] = mode

    save_env(env)
    print("\n  ✅ 거래 설정 저장 완료")
    pause()


# ─── AI 자동 그리드 설정 ─────────────────────────────────

def _fetch_market_data(symbol: str) -> dict | None:
    """OKX Public API에서 시세 데이터를 가져옵니다."""
    try:
        client = httpx.Client(base_url="https://www.okx.com", timeout=10)

        # 현재가
        resp = client.get("/api/v5/market/ticker", params={"instId": symbol})
        resp.raise_for_status()
        ticker = resp.json()
        if not ticker.get("data"):
            print("  ⚠ 시세 응답에 데이터가 없습니다.")
            client.close()
            return None
        price = float(ticker["data"][0]["last"])
        vol_24h = float(ticker["data"][0]["vol24h"])
        high_24h = float(ticker["data"][0]["high24h"])
        low_24h = float(ticker["data"][0]["low24h"])

        # 일봉 캔들 30개 (약 한 달)
        candles_resp = client.get(
            "/api/v5/market/candles",
            params={"instId": symbol, "bar": "1D", "limit": "30"}
        )
        candles_resp.raise_for_status()
        candles_data = candles_resp.json()
        candles = candles_data.get("data", [])

        # 고가/저가 범위
        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        closes = [float(c[4]) for c in candles]

        month_high = max(highs) if highs else high_24h
        month_low = min(lows) if lows else low_24h

        # 단순 변동성 (일봉 종가 기준 표준편차)
        import numpy as np
        if len(closes) >= 2:
            returns = np.diff(closes) / np.array(closes[:-1])
            volatility = float(np.std(returns) * 100)
        else:
            volatility = 0.0

        client.close()

        return {
            "symbol": symbol,
            "current_price": price,
            "high_24h": high_24h,
            "low_24h": low_24h,
            "vol_24h": vol_24h,
            "month_high": month_high,
            "month_low": month_low,
            "daily_volatility_pct": round(volatility, 2),
        }

    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
        print(f"  ⚠ 시세 조회 네트워크 오류: {e}")
        return None
    except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
        print(f"  ⚠ 시세 데이터 파싱 오류: {e}")
        return None
    except httpx.HTTPError as e:
        print(f"  ⚠ 시세 조회 HTTP 오류: {e}")
        return None


def _call_llm_for_grid(env: dict, market: dict, budget: float) -> dict:
    """LLM에게 최적 그리드 설정을 요청합니다."""
    provider = env.get("LLM_PROVIDER", "anthropic")
    api_key = env.get("LLM_API_KEY", "")
    model = env.get("LLM_MODEL", "")

    if not api_key:
        return {}

    prompt = f"""당신은 암호화폐 그리드 트레이딩 전문가입니다.
아래 시장 데이터를 분석하고 최적의 그리드 설정과 예산 배분을 JSON으로 추천해주세요.

=== 시장 데이터 ===
심볼: {market['symbol']}
현재가: {market['current_price']:,.2f} USDT
24시간 고가: {market['high_24h']:,.2f}
24시간 저가: {market['low_24h']:,.2f}
30일 최고가: {market['month_high']:,.2f}
30일 최저가: {market['month_low']:,.2f}
일일 변동성: {market['daily_volatility_pct']:.2f}%
24시간 거래량: {market['vol_24h']:,.0f}

=== 투자 조건 ===
총 예산: {budget:,.0f} USDT

=== 추천 규칙 ===
- grid_budget: 그리드에 투입할 금액 (총 예산의 30~70%, 변동성 높으면 비율 낮게)
- reserve_budget: 나머지는 예비 자금 (급락 시 추가 매수, 리밸런싱용)
- 그리드 하단/상단은 30일 범위와 현재 변동성을 고려해서 설정
- 너무 좁으면 수익 기회 적고, 너무 넓으면 자금 효율 떨어짐
- 변동성 높으면 간격 넓게, 낮으면 좁게
- 그리드 개수는 15~30 사이 추천
- arithmetic vs geometric: 가격대가 높고 변동 큰 자산은 geometric 추천

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만:
{{"grid_budget": 숫자, "reserve_budget": 숫자, "grid_lower": 숫자, "grid_upper": 숫자, "grid_count": 정수, "grid_mode": "arithmetic"|"geometric", "reason": "추천 이유 한줄"}}"""

    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            default_model = model or "claude-sonnet-4-20250514"
            resp = client.messages.create(
                model=default_model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text.strip()
        elif provider == "gemini":
            from google import genai
            client = genai.Client(api_key=api_key)
            default_model = model or "gemini-2.5-flash"
            resp = client.models.generate_content(
                model=default_model,
                contents=prompt,
                config={"max_output_tokens": 300},
            )
            raw = resp.text.strip()
        else:  # openai, grok (OpenAI 호환)
            import openai
            base_url = "https://api.x.ai/v1" if provider == "grok" else None
            client = openai.OpenAI(api_key=api_key, base_url=base_url)
            default_model = model or ("grok-3-mini" if provider == "grok" else "gpt-4o")
            resp = client.chat.completions.create(
                model=default_model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.choices[0].message.content.strip()

        # JSON 파싱 (```json ... ``` 감싸는 경우 대응)
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        try:
            parsed = json.loads(raw.strip())
        except json.JSONDecodeError as e:
            print(f"    ⚠ LLM 응답 JSON 파싱 오류: {e}")
            return {}

        required_keys = ("grid_budget", "grid_lower", "grid_upper", "grid_count")
        missing = [k for k in required_keys if k not in parsed]
        if missing:
            print(f"    ⚠ LLM 응답에 필수 키 누락: {missing}")
            return {}

        return parsed

    except (ImportError, AttributeError, IndexError, TypeError) as e:
        print(f"    ⚠ LLM 클라이언트 오류: {e}")
        return {}
    except Exception as e:
        print(f"    ⚠ LLM 호출 오류: {e}")
        return {}


def _auto_grid_settings(env: dict, symbol: str, budget: float) -> dict:
    """시세 조회 → LLM 추천 → 사용자 확인."""

    if not env.get("LLM_API_KEY"):
        print("\n  ⚠ LLM API 키가 설정되지 않았습니다. 먼저 LLM 설정을 완료해주세요.")
        return {}

    print("\n  🔍 시세 데이터 조회 중...")
    try:
        market = _fetch_market_data(symbol)
    except Exception as e:
        print(f"  ⚠ 시세 조회 실패: {e}")
        return {}

    if market is None:
        print("  ⚠ 시세 데이터를 가져올 수 없습니다.")
        return {}

    print(f"  현재가: {market['current_price']:,.2f} USDT")
    print(f"  24h 범위: {market['low_24h']:,.2f} ~ {market['high_24h']:,.2f}")
    print(f"  30일 범위: {market['month_low']:,.2f} ~ {market['month_high']:,.2f}")
    print(f"  일일 변동성: {market['daily_volatility_pct']:.2f}%")

    print("\n  🤖 AI가 최적 그리드를 분석 중...")
    result = _call_llm_for_grid(env, market, budget)

    if not result or "grid_lower" not in result:
        return {}

    grid_budget = result.get("grid_budget", budget * 0.4)
    reserve_budget = result.get("reserve_budget", budget - grid_budget)
    lower = result["grid_lower"]
    upper = result["grid_upper"]
    count = result["grid_count"]
    mode = result.get("grid_mode", "arithmetic")
    reason = result.get("reason", "")
    spread = (upper - lower) / count
    grid_pct = grid_budget / budget * 100

    W = 46
    print()
    print(f"  ┌{'─' * W}┐")
    print(_box_line("AI 추천 설정", W))
    print(f"  ├{'─' * W}┤")
    print(_box_line(f"그리드 예산 : {grid_budget:>12,.0f} USDT ({grid_pct:.0f}%)", W))
    print(_box_line(f"예비 자금   : {reserve_budget:>12,.0f} USDT ({100-grid_pct:.0f}%)", W))
    print(f"  ├{'─' * W}┤")
    print(_box_line(f"하단 가격   : {lower:>15,.2f} USDT", W))
    print(_box_line(f"상단 가격   : {upper:>15,.2f} USDT", W))
    print(_box_line(f"그리드 개수 : {count:>15} 개", W))
    print(_box_line(f"그리드 간격 : {spread:>15,.2f} USDT", W))
    print(_box_line(f"모드        : {mode:>15}", W))
    print(f"  ├{'─' * W}┤")
    reason_trunc = _vtrunc(reason, W - 6) if _vw(reason) > W - 6 else reason
    print(_box_line(f"사유: {reason_trunc}", W))
    print(f"  └{'─' * W}┘")
    print()

    try:
        accept = questionary.confirm(
            "이 설정을 적용할까요?",
            default=True,
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return {}

    if not accept:
        return {}

    return {
        "GRID_BUDGET": str(round(grid_budget, 2)),
        "RESERVE_BUDGET": str(round(reserve_budget, 2)),
        "GRID_LOWER": str(lower),
        "GRID_UPPER": str(upper),
        "GRID_COUNT": str(count),
        "GRID_MODE": mode,
    }


# ─── LLM 설정 ───────────────────────────────────────────

def setup_llm(env: dict):
    header("LLM 설정")

    try:
        provider = questionary.select(
            "LLM 제공자",
            choices=[
                Choice("Anthropic (Claude)", value="anthropic"),
                Choice("OpenAI (GPT)", value="openai"),
                Choice("xAI (Grok)", value="grok"),
                Choice("Google (Gemini)", value="gemini"),
            ],
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if provider is None:
        return
    env["LLM_PROVIDER"] = provider

    try:
        api_key = questionary.password(
            "API Key:",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if api_key is None:
        return
    env["LLM_API_KEY"] = api_key

    if provider == "anthropic":
        models = [
            Choice("Claude Sonnet 4 (추천)", value="claude-sonnet-4-20250514"),
            Choice("Claude Opus 4", value="claude-opus-4-20250514"),
            Choice("Claude Haiku 4 (경량)", value="claude-haiku-4-20250414"),
        ]
    elif provider == "openai":
        models = [
            Choice("GPT-4o (추천)", value="gpt-4o"),
            Choice("GPT-4o Mini (경량)", value="gpt-4o-mini"),
            Choice("GPT-4.1", value="gpt-4.1"),
        ]
    elif provider == "grok":
        models = [
            Choice("Grok 3 Mini (추천)", value="grok-3-mini"),
            Choice("Grok 3", value="grok-3"),
        ]
    else:  # gemini
        models = [
            Choice("Gemini 2.5 Flash (추천)", value="gemini-2.5-flash"),
            Choice("Gemini 2.5 Pro", value="gemini-2.5-pro"),
            Choice("Gemini 2.0 Flash (경량)", value="gemini-2.0-flash"),
        ]

    try:
        model = questionary.select(
            "모델 선택",
            choices=models,
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if model is None:
        return
    env["LLM_MODEL"] = model

    save_env(env)
    print("\n  ✅ LLM 설정 저장 완료")
    pause()


# ─── 텔레그램 설정 ───────────────────────────────────────

def setup_telegram(env: dict):
    header("텔레그램 알림 설정")

    try:
        use = questionary.confirm(
            "텔레그램 알림을 사용할까요?",
            default=bool(env.get("TELEGRAM_TOKEN")),
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return

    if use is None:
        return

    if not use:
        env.pop("TELEGRAM_TOKEN", None)
        env.pop("TELEGRAM_CHAT_ID", None)
        save_env(env)
        print("\n  ✅ 텔레그램 알림 해제됨")
        pause()
        return

    try:
        token = questionary.text(
            "Bot Token (@BotFather에서 발급):",
            default=env.get("TELEGRAM_TOKEN", ""),
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if token is None:
        return
    env["TELEGRAM_TOKEN"] = token

    # 봇 토큰 유효성 먼저 확인
    print("\n  🔍 봇 토큰 확인 중...")
    base = f"https://api.telegram.org/bot{token}"
    try:
        resp = httpx.get(f"{base}/getMe", timeout=10)
        data = resp.json()
        if not data.get("ok"):
            print(f"  ❌ Bot Token이 유효하지 않습니다: {data.get('description', '')}")
            print(f"     @BotFather에서 토큰을 다시 확인해주세요.")
            pause()
            return
        bot_name = data["result"].get("username", "unknown")
        print(f"  ✅ 봇 확인: @{bot_name}")
    except Exception as e:
        print(f"  ❌ 봇 검증 실패: {e}")
        pause()
        return

    # Chat ID 자동 감지
    chat_id = _detect_chat_id(token, env.get("TELEGRAM_CHAT_ID"))
    if chat_id is None:
        return
    env["TELEGRAM_CHAT_ID"] = chat_id

    # 알림 받을 상태 선택 (중복 선택)
    try:
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
    except (KeyboardInterrupt, EOFError):
        return
    if notify_states is None:
        return
    env["NOTIFY_ON_STATES"] = ",".join(notify_states)

    # 연결 테스트
    print("\n  🔍 텔레그램 연결 테스트 중...")
    test_ok, test_msg = _test_telegram(token, chat_id)

    if test_ok:
        print(f"  ✅ 테스트 메시지 발송 성공!")
        print(f"  📱 텔레그램에서 메시지를 확인해주세요.")
        save_env(env)
        print("\n  ✅ 텔레그램 설정 저장 완료")
    else:
        print(f"  ❌ 테스트 실패: {test_msg}")
        print()
        try:
            save_anyway = questionary.confirm(
                "설정을 그래도 저장할까요?",
                default=False,
                style=STYLE,
            ).ask()
        except (KeyboardInterrupt, EOFError):
            save_anyway = False
        if save_anyway:
            save_env(env)
            print("\n  ✅ 텔레그램 설정 저장 완료 (테스트 미통과)")
        else:
            print("\n  ⚠ 텔레그램 설정이 저장되지 않았습니다.")
    pause()


def _detect_chat_id(token: str, existing_chat_id: str = None) -> str | None:
    """텔레그램 봇에게 메시지를 보내게 해서 Chat ID를 자동 감지."""
    import time as _time
    base = f"https://api.telegram.org/bot{token}"

    # 기존 Chat ID가 있으면 그대로 쓸지 물어봄
    if existing_chat_id:
        try:
            reuse = questionary.confirm(
                f"기존 Chat ID ({existing_chat_id})를 그대로 사용할까요?",
                default=True,
                style=STYLE,
            ).ask()
        except (KeyboardInterrupt, EOFError):
            return None
        if reuse:
            return existing_chat_id

    # getUpdates offset 초기화 — 기존 메시지 무시하기 위해 현재까지의 update 소비
    try:
        resp = httpx.get(f"{base}/getUpdates", params={"limit": 1, "offset": -1, "timeout": 0}, timeout=10)
        data = resp.json()
        if data.get("ok") and data.get("result"):
            last_id = data["result"][-1]["update_id"]
            # 이 ID+1 부터가 새 메시지
            flush_offset = last_id + 1
        else:
            flush_offset = 0
    except Exception:
        flush_offset = 0

    BW = 52
    print()
    print(f"  ┌{'─' * BW}┐")
    print(_box_line("텔레그램에서 봇에게 아무 메시지나 보내주세요", BW))
    print(_box_line("(예: /start 또는 아무 텍스트)", BW))
    print(_box_line("60초 안에 보내주시면 자동으로 감지합니다.", BW))
    print(f"  └{'─' * BW}┘")
    print()
    print("  ⏳ 메시지 대기 중...", end="", flush=True)

    # 최대 60초 동안 폴링 (long polling 5초씩)
    for i in range(12):
        try:
            resp = httpx.get(
                f"{base}/getUpdates",
                params={"limit": 10, "offset": flush_offset, "timeout": 5},
                timeout=15,
            )
            data = resp.json()

            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    chat_id = str(chat.get("id", ""))
                    chat_name = chat.get("first_name", "") or chat.get("title", "")
                    chat_type = chat.get("type", "")

                    if chat_id:
                        print(f"\r  ✅ Chat ID 감지 완료!                        ")
                        print(f"     Chat ID  : {chat_id}")
                        print(f"     이름     : {chat_name}")
                        print(f"     타입     : {chat_type}")
                        return chat_id
        except httpx.TimeoutException:
            pass
        except Exception:
            pass
        print(".", end="", flush=True)

    print(f"\r  ⚠ 60초 내에 메시지를 감지하지 못했습니다.        ")
    print()

    # 실패 시 수동 입력 폴백
    try:
        manual = questionary.text(
            "Chat ID를 직접 입력해주세요 (빈칸이면 취소):",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return None
    if not manual:
        return None
    return manual


def _test_telegram(token: str, chat_id: str) -> tuple[bool, str]:
    """텔레그램 봇 토큰 검증 + 테스트 메시지 발송 + 수신 확인."""
    base = f"https://api.telegram.org/bot{token}"

    # 1. 봇 토큰 유효성 검증 (getMe)
    try:
        resp = httpx.get(f"{base}/getMe", timeout=10)
        data = resp.json()
        if not data.get("ok"):
            return False, f"Bot Token이 유효하지 않습니다: {data.get('description', 'unknown error')}"
        bot_name = data["result"].get("username", "unknown")
        print(f"  ✓ 봇 확인: @{bot_name}")
    except httpx.TimeoutException:
        return False, "Telegram API 응답 타임아웃"
    except Exception as e:
        return False, f"봇 검증 요청 실패: {e}"

    # 2. 테스트 메시지 발송
    test_text = "🔔 Snowball Agent 텔레그램 연결 테스트\n\n이 메시지가 보이면 설정이 정상입니다!"
    try:
        resp = httpx.post(
            f"{base}/sendMessage",
            json={"chat_id": chat_id, "text": test_text},
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            err = data.get("description", "unknown error")
            if "chat not found" in err.lower():
                return False, f"Chat ID가 잘못되었습니다. 봇에게 먼저 /start 메시지를 보내주세요."
            return False, f"메시지 발송 실패: {err}"
        print(f"  ✓ 테스트 메시지 발송 완료")
    except httpx.TimeoutException:
        return False, "메시지 발송 타임아웃"
    except Exception as e:
        return False, f"메시지 발송 요청 실패: {e}"

    # 3. 수신 확인 (getUpdates로 봇이 받은 최근 메시지 확인)
    try:
        resp = httpx.get(
            f"{base}/getUpdates",
            params={"limit": 5, "offset": -5},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok") and data.get("result"):
            # chat_id에서 온 메시지가 있는지 확인
            found = False
            for update in data["result"]:
                msg = update.get("message", {})
                if str(msg.get("chat", {}).get("id")) == str(chat_id):
                    found = True
                    break
            if found:
                print(f"  ✓ 수신 확인: Chat ID {chat_id}에서 메시지 수신 이력 있음")
            else:
                print(f"  ⚠ 수신 이력 없음 (봇에게 /start를 보낸 적 없을 수 있음)")
                print(f"    → 메시지 발송은 성공했으니 텔레그램에서 확인해보세요")
        else:
            print(f"  ⚠ 수신 이력 조회 불가 (Webhook 모드일 수 있음)")
    except Exception:
        print(f"  ⚠ 수신 확인 스킵 (발송은 성공)")

    return True, ""


# ─── 고급 설정 ───────────────────────────────────────────

def setup_advanced(env: dict):
    header("고급 설정")

    try:
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
    except (KeyboardInterrupt, EOFError):
        return
    if loop is None:
        return
    env["LOOP_INTERVAL_SEC"] = loop

    try:
        loss = questionary.text(
            "손절 기준 (%):",
            default=env.get("MAX_LOSS_PERCENT", "15"),
            validate=lambda v: True if _is_number(v) else "숫자를 입력해주세요",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if loss is None:
        return
    env["MAX_LOSS_PERCENT"] = loss

    try:
        trigger = questionary.text(
            "LLM 판단 최소 점수:",
            default=env.get("LLM_TRIGGER_SCORE", "55"),
            validate=lambda v: True if _is_int(v) else "정수를 입력해주세요",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
    if trigger is None:
        return
    env["LLM_TRIGGER_SCORE"] = trigger

    try:
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
    except (KeyboardInterrupt, EOFError):
        return
    if candle is None:
        return
    env["CANDLE_INTERVAL"] = candle

    try:
        lookback = questionary.text(
            "캔들 개수:",
            default=env.get("CANDLE_LOOKBACK", "100"),
            validate=lambda v: True if _is_int(v) else "정수를 입력해주세요",
            style=STYLE,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return
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

    mode = "Demo" if env.get("DEMO_MODE", "true") == "true" else "Live"
    W = 44
    L = 12  # 라벨 폭
    V = W - L - 2  # 값 폭 (": " 포함)

    def _row(label, value):
        val_str = str(value)
        if _vw(val_str) > V:
            val_str = _vtrunc(val_str, V)
        return _box_line(f"{_vpad(label, L)}: {_vpad(val_str, V)}", W)

    print(f"  ┌{'─' * W}┐")
    print(_row("OKX API", mask(env.get("OKX_API_KEY", ""))))
    print(_row("거래 모드", mode))
    print(f"  ├{'─' * W}┤")
    print(_row("심볼", env.get("SYMBOL", "-")))
    print(_row("총 예산", env.get("TOTAL_BUDGET", "-") + " USDT"))
    grid_range = env.get("GRID_LOWER", "-") + " ~ " + env.get("GRID_UPPER", "-")
    print(_row("그리드 범위", grid_range))
    print(_row("그리드 개수", env.get("GRID_COUNT", "-")))
    print(_row("그리드 모드", env.get("GRID_MODE", "-")))
    print(f"  ├{'─' * W}┤")
    llm_info = f"{env.get('LLM_PROVIDER', '-')} / {env.get('LLM_MODEL', '-')}"
    print(_row("LLM", llm_info))
    print(_row("LLM Key", mask(env.get("LLM_API_KEY", ""))))
    print(f"  ├{'─' * W}┤")
    tg = "ON" if env.get("TELEGRAM_TOKEN") else "OFF"
    print(_row("텔레그램", tg))
    print(_row("루프 간격", env.get("LOOP_INTERVAL_SEC", "120") + "s"))
    print(_row("손절 기준", env.get("MAX_LOSS_PERCENT", "15") + "%"))
    print(f"  └{'─' * W}┘")
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
