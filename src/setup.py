"""
setup.py
초기 설정 진입점 (레거시 호환용).

과거에는 자체 텍스트 위저드가 있었지만, menu.py의 인터랙티브 위저드와
기본값이 달라 (BTC 90000~110000 등) 설정 흐름이 둘로 갈라지는 문제가 있었다.
이제 menu.py의 위저드 하나로 통일한다 — `python main_agent.py` 실행 시
자동으로 같은 위저드가 뜨므로 이 파일을 직접 실행할 필요는 없다.
"""

from menu import initial_setup_wizard


def run_setup() -> bool:
    """menu.py의 초기 설정 위저드로 위임."""
    return bool(initial_setup_wizard())


if __name__ == "__main__":
    run_setup()
