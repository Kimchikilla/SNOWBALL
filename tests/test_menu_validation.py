import os
import sys
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from menu import validate_grid_settings, MIN_PER_GRID_USDT


class ValidateGridSettingsTests(unittest.TestCase):
    """AI 추천/수동 입력 공통 검증 — 무검증 저장으로 인한 사고 방지."""

    PRICE = 2_000.0

    def good_raw(self, **overrides):
        raw = {
            "grid_budget": 4_000.0,
            "grid_lower": 1_800.0,
            "grid_upper": 2_200.0,
            "grid_count": 10,
            "grid_mode": "arithmetic",
        }
        raw.update(overrides)
        return raw

    def test_valid_settings_pass_unchanged(self):
        settings, warnings = validate_grid_settings(10_000.0, self.good_raw(), self.PRICE)

        self.assertEqual(warnings, [])
        self.assertEqual(settings["grid_budget"], 4_000.0)
        self.assertEqual(settings["reserve_budget"], 6_000.0)
        self.assertEqual(settings["grid_lower"], 1_800.0)
        self.assertEqual(settings["grid_upper"], 2_200.0)
        self.assertEqual(settings["grid_count"], 10)

    def test_grid_budget_exceeding_total_is_clamped(self):
        # LLM이 총예산보다 큰 그리드 예산을 추천해도 70%로 클램프
        settings, warnings = validate_grid_settings(
            10_000.0, self.good_raw(grid_budget=15_000.0), self.PRICE)

        self.assertEqual(settings["grid_budget"], 7_000.0)
        self.assertEqual(settings["reserve_budget"], 3_000.0)
        self.assertTrue(any("70%" in w for w in warnings))

    def test_reserve_is_always_recomputed(self):
        # reserve+grid ≠ total인 추천이 와도 코드가 재계산
        settings, _ = validate_grid_settings(
            10_000.0, self.good_raw(grid_budget=3_000.0, reserve_budget=99_999.0),
            self.PRICE)

        self.assertEqual(settings["grid_budget"] + settings["reserve_budget"], 10_000.0)

    def test_price_outside_range_recenters(self):
        # 현재가 2000인데 범위가 2200~2600 → 같은 폭으로 현재가 중심 재배치
        settings, warnings = validate_grid_settings(
            10_000.0, self.good_raw(grid_lower=2_200.0, grid_upper=2_600.0),
            self.PRICE)

        self.assertLess(settings["grid_lower"], self.PRICE)
        self.assertGreater(settings["grid_upper"], self.PRICE)
        self.assertAlmostEqual(
            settings["grid_upper"] - settings["grid_lower"], 400.0, places=2)
        self.assertTrue(any("범위 밖" in w for w in warnings))

    def test_inverted_range_falls_back_to_default_band(self):
        settings, warnings = validate_grid_settings(
            10_000.0, self.good_raw(grid_lower=2_500.0, grid_upper=1_500.0),
            self.PRICE)

        self.assertLess(settings["grid_lower"], settings["grid_upper"])
        self.assertLess(settings["grid_lower"], self.PRICE)
        self.assertGreater(settings["grid_upper"], self.PRICE)
        self.assertTrue(warnings)

    def test_count_reduced_when_per_grid_below_minimum(self):
        # 그리드 예산 1000의 10% 하한 → 1000, 개수 50이면 라인당 20... 만들자:
        # 총 1000, 그리드 40% = 400 → 50개면 라인당 8 < 10 → 개수 축소
        settings, warnings = validate_grid_settings(
            1_000.0, self.good_raw(grid_budget=400.0, grid_count=50), self.PRICE)

        self.assertGreaterEqual(
            settings["grid_budget"] / settings["grid_count"], MIN_PER_GRID_USDT)
        self.assertTrue(any("주문액" in w for w in warnings))

    def test_invalid_mode_defaults_to_arithmetic(self):
        settings, _ = validate_grid_settings(
            10_000.0, self.good_raw(grid_mode="banana"), self.PRICE)

        self.assertEqual(settings["grid_mode"], "arithmetic")

    def test_garbage_values_do_not_crash(self):
        settings, warnings = validate_grid_settings(
            10_000.0,
            {"grid_budget": "??", "grid_lower": None, "grid_upper": "x",
             "grid_count": "many", "grid_mode": 7},
            self.PRICE)

        self.assertGreater(settings["grid_upper"], settings["grid_lower"])
        self.assertGreaterEqual(settings["grid_count"], 2)
        self.assertTrue(warnings)

    def test_narrow_spacing_warns_about_fees(self):
        # 폭 1% 범위에 50그리드 → 간격 0.02% << 수수료
        settings, warnings = validate_grid_settings(
            100_000.0,
            self.good_raw(grid_budget=40_000.0, grid_lower=1_990.0,
                          grid_upper=2_010.0, grid_count=50),
            self.PRICE)

        self.assertTrue(any("수수료" in w for w in warnings))


class ConfigReloadTests(unittest.TestCase):
    """menu에서 .env 변경 후 config.reload()가 실제로 새 값을 반영하는지."""

    def test_reload_picks_up_changed_env_value(self):
        import config

        original = config.GRID_BUDGET
        env_path = config._env_path
        with open(env_path, encoding="utf-8") as f:
            original_content = f.read()

        try:
            lines = []
            replaced = False
            for line in original_content.splitlines():
                if line.startswith("GRID_BUDGET="):
                    lines.append("GRID_BUDGET=12345.0")
                    replaced = True
                else:
                    lines.append(line)
            if not replaced:
                lines.append("GRID_BUDGET=12345.0")
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

            config.reload()
            self.assertEqual(config.GRID_BUDGET, 12345.0)
        finally:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(original_content)
            config.reload()
            self.assertEqual(config.GRID_BUDGET, original)


if __name__ == "__main__":
    unittest.main()
