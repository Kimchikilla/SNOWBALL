"""
ui.py
Claude Code 스타일 터미널 UI 헬퍼.

디자인 언어:
  ● 불릿 (상태색)  +  ⎿ 들여쓴 자식 라인 (라벨은 dim)
  ╭─╮ 둥근 모서리 패널, 오렌지 액센트, 절제된 24bit 컬러
"""

import os
import sys
import time
import unicodedata


# ─── Windows VT 활성화 ──────────────────────────────────

def _enable_vt():
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


_enable_vt()


# ─── 팔레트 (24bit) ─────────────────────────────────────

ACCENT = (215, 119, 87)    # 클로드 오렌지
GREEN = (152, 195, 121)
RED = (224, 108, 117)
YELLOW = (229, 192, 123)
CYAN = (86, 182, 194)
MAGENTA = (198, 120, 221)
GRAY = (140, 140, 140)
DGRAY = (95, 95, 95)
WHITE = (220, 220, 220)

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def fg(rgb) -> str:
    r, g, b = rgb
    return f"\033[38;2;{r};{g};{b}m"


def c(text, rgb=None, bold=False, dim=False) -> str:
    """색/스타일 적용 문자열."""
    prefix = ""
    if rgb:
        prefix += fg(rgb)
    if bold:
        prefix += BOLD
    if dim:
        prefix += DIM
    return f"{prefix}{text}{RESET}" if prefix else str(text)


# 상태 → 색
STATE_COLOR = {
    "NORMAL": GREEN, "CAUTION": YELLOW, "WARNING": ACCENT, "EMERGENCY": RED,
}
ACTION_COLOR = {
    "MAINTAIN": GREEN, "WIDEN": YELLOW,
    "SHIFT_UP": CYAN, "SHIFT_DOWN": CYAN, "STOP": RED,
}
REGIME_COLOR = {
    "RANGE": GRAY, "TREND_UP": GREEN, "TREND_DOWN": RED,
}
TREND_COLOR = {
    "BULLISH": GREEN, "BEARISH": RED, "SIDEWAYS": GRAY,
}


def pnl_c(value: float) -> str:
    """손익 숫자 색칠 (+초록 / -빨강)."""
    return c(f"{value:+,.2f}", GREEN if value >= 0 else RED)


# ─── 표시 폭 (CJK 2칸) ──────────────────────────────────

def _vw(s: str) -> int:
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def vpad(s: str, width: int) -> str:
    return s + " " * max(0, width - _vw(s))


# ─── 핵심 컴포넌트 ──────────────────────────────────────

def bullet(text: str, color=WHITE):
    """● 헤드라인."""
    print(f"{fg(color)}●{RESET} {text}")


def child(text: str, label: str = ""):
    """  ⎿ 자식 라인. label은 dim 고정폭."""
    prefix = f"  {fg(DGRAY)}⎿{RESET}  "
    if label:
        prefix += c(vpad(label, 8), GRAY)
    print(prefix + text)


def child_ok(text: str, label: str = ""):
    child(text, label)


def child_warn(text: str, label: str = ""):
    child(c(text, YELLOW), label)


def child_err(text: str, label: str = ""):
    child(c(text, RED), label)


def sep(value: str = "·") -> str:
    """dim 구분점."""
    return c(f" {value} ", DGRAY)


def rule(width: int = 60):
    print(c("─" * width, DGRAY))


def _panel_top(title: str, color, width: int):
    bc = fg(color)
    dashes = "─" * max(0, width - _vw(title) - 3)
    print(f"{bc}╭─{RESET} {c(title, color, bold=True)} {bc}{dashes}╮{RESET}")


def panel(title: str, lines: list[str], color=ACCENT, width: int = 58):
    """╭─╮ 둥근 패널. lines는 ANSI 없는 평문 권장 — 색 값은 panel_kv 사용."""
    bc = fg(color)
    _panel_top(title, color, width)
    for line in lines:
        print(f"{bc}│{RESET} {vpad(line, width - 1)}{bc}│{RESET}")
    print(f"{bc}╰{'─' * width}╯{RESET}")


def panel_kv(title: str, pairs: list[tuple[str, str]], color=ACCENT,
             width: int = 58, label_w: int = 10):
    """라벨: 값 패널. 값에 ANSI 색이 있어도 폭을 맞춘다."""
    bc = fg(color)
    _panel_top(title, color, width)
    for label, value in pairs:
        if label == "─":
            print(f"{bc}├{'─' * width}┤{RESET}")
            continue
        body = f" {c(vpad(label, label_w), GRAY)} {value}"
        pad = max(0, width - 2 - label_w - _vw(_strip_ansi(value)))
        print(f"{bc}│{RESET}{body}{' ' * pad}{bc}│{RESET}")
    print(f"{bc}╰{'─' * width}╯{RESET}")


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", str(s))


def banner():
    """시작 배너 — Claude Code 웰컴 스타일."""
    print()
    a = fg(ACCENT)
    print(f" {a}✻{RESET} {BOLD}Snowball{RESET} {c('— OKX Adaptive Grid Trading Agent', GRAY)}")
    print(f"   {c('결정론적 리스크 레이어 · 레짐 필터 · 디리스킹 래더 · 숏 헤지', DGRAY)}")
    print()


def disclaimer():
    print(f" {fg(RED)}⚠{RESET}  {c('이 소프트웨어는 투자 조언이 아닙니다. 모든 손실은 사용자 책임이며,', GRAY)}")
    print(f"    {c('암호화폐는 원금 손실 위험이 있습니다. 감당 가능한 금액만 투자하세요.', GRAY)}")
    print()


# ─── 대기 스피너 ────────────────────────────────────────

_SPINNER = "⣾⣽⣻⢿⡿⣟⣯⣷"


def wait_progress(seconds: int, status: str = ""):
    """다음 틱까지 스피너 + 카운트다운 한 줄."""
    bar_w = 28
    status_part = f"{sep()}{c(status, GRAY)}" if status else ""
    for elapsed in range(seconds):
        remaining = seconds - elapsed
        mins, secs = divmod(remaining, 60)
        time_str = f"{mins}:{secs:02d}" if mins else f"{secs}s"
        filled = int(bar_w * elapsed / seconds)
        bar = (c("━" * filled, ACCENT) + c("━" * (bar_w - filled), DGRAY))
        spin = _SPINNER[elapsed % len(_SPINNER)]
        print(
            f"\r {fg(ACCENT)}{spin}{RESET} {c('다음 틱', GRAY)} {bar} "
            f"{c(time_str, WHITE)}{status_part}  ",
            end="", flush=True,
        )
        time.sleep(1)
    print(f"\r {c('●', GREEN)} {c('틱 시작', GRAY)} {c('━' * bar_w, ACCENT)} {c('0s', WHITE)}{status_part}  ")
