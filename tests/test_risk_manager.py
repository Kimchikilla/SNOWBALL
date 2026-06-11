import math
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import config
from grid_controller import GridController, HedgeController
from main_agent import GridAgent
from risk_manager import (
    RegimeDetector, DeriskLadder, parse_derisk_levels, hedge_target_qty,
    REGIME_RANGE, REGIME_TREND_UP, REGIME_TREND_DOWN,
    DERISK_HEDGE, DERISK_STOP,
)

from test_risk_controls import FakeNotifier, FakeGridController, make_signal


# ─── 합성 캔들 ──────────────────────────────────────────

def downtrend_candles(n=120, start=2400.0, daily_drop=0.01):
    candles = []
    price = start
    for _ in range(n):
        new_price = price * (1 - daily_drop)
        candles.append({
            "high": price * 1.002,
            "low": new_price * 0.998,
            "close": new_price,
            "open": price,
            "vol": 1000,
        })
        price = new_price
    return candles


def range_candles(n=120, center=2200.0, amplitude=0.02):
    candles = []
    for i in range(n):
        close = center * (1 + amplitude * math.sin(i / 5))
        candles.append({
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "open": close,
            "vol": 1000,
        })
    return candles


def uptrend_candles(n=120, start=1800.0, daily_gain=0.01):
    candles = []
    price = start
    for _ in range(n):
        new_price = price * (1 + daily_gain)
        candles.append({
            "high": new_price * 1.002,
            "low": price * 0.998,
            "close": new_price,
            "open": price,
            "vol": 1000,
        })
        price = new_price
    return candles


# ─── 레짐 판별 ──────────────────────────────────────────

class RegimeDetectorTests(unittest.TestCase):
    def test_downtrend_raw_classification(self):
        detector = RegimeDetector(confirm_ticks=1)
        raw, ema_fast, ema_slow, slope, adx = detector.classify_raw(downtrend_candles())

        self.assertEqual(raw, REGIME_TREND_DOWN)
        self.assertLess(ema_fast, ema_slow)
        self.assertLess(slope, -1.0)
        self.assertGreaterEqual(adx, 20.0)

    def test_range_market_stays_range(self):
        detector = RegimeDetector(confirm_ticks=1)
        state = detector.update(range_candles())

        self.assertEqual(state.regime, REGIME_RANGE)

    def test_uptrend_classification(self):
        detector = RegimeDetector(confirm_ticks=1)
        state = detector.update(uptrend_candles())

        self.assertEqual(state.regime, REGIME_TREND_UP)

    def test_confirm_ticks_prevents_single_evaluation_flip(self):
        detector = RegimeDetector(confirm_ticks=3)
        candles = downtrend_candles()

        s1 = detector.update(candles)
        s2 = detector.update(candles)
        s3 = detector.update(candles)

        self.assertEqual(s1.regime, REGIME_RANGE)   # streak 1 — 미확정
        self.assertEqual(s2.regime, REGIME_RANGE)   # streak 2 — 미확정
        self.assertEqual(s3.regime, REGIME_TREND_DOWN)  # streak 3 — 확정
        self.assertTrue(s3.confirmed)

    def test_whipsaw_resets_streak(self):
        detector = RegimeDetector(confirm_ticks=3)
        down = downtrend_candles()
        flat = range_candles()

        detector.update(down)
        detector.update(down)
        detector.update(flat)   # streak 리셋
        state = detector.update(down)

        self.assertEqual(state.regime, REGIME_RANGE)
        self.assertEqual(state.streak, 1)

    def test_state_roundtrip(self):
        detector = RegimeDetector(confirm_ticks=1)
        detector.update(downtrend_candles())

        restored = RegimeDetector(confirm_ticks=1)
        restored.from_state(detector.to_state())

        self.assertEqual(restored.regime, REGIME_TREND_DOWN)


# ─── 디리스킹 래더 ──────────────────────────────────────

class DeriskLadderTests(unittest.TestCase):
    def make_ladder(self):
        return DeriskLadder(parse_derisk_levels("5:0.5,8:1.0,12:stop"))

    def test_parse_levels(self):
        levels = parse_derisk_levels("8:1.0, 5:0.5, 12:stop, bogus, -3:0.1")

        self.assertEqual(len(levels), 3)
        self.assertEqual([lv.loss_pct for lv in levels], [5.0, 8.0, 12.0])
        self.assertEqual(levels[0].action, DERISK_HEDGE)
        self.assertEqual(levels[2].action, DERISK_STOP)

    def test_no_trigger_above_first_level(self):
        ladder = self.make_ladder()
        self.assertIsNone(ladder.evaluate(-4.9))
        self.assertEqual(ladder.current_level, 0)

    def test_levels_trigger_once_and_ratchet(self):
        ladder = self.make_ladder()

        d1 = ladder.evaluate(-5.5)
        self.assertEqual(d1.level_index, 1)
        self.assertEqual(d1.level.fraction, 0.5)

        # 같은 드로다운에서 재발동 없음
        self.assertIsNone(ladder.evaluate(-5.6))

        d2 = ladder.evaluate(-8.2)
        self.assertEqual(d2.level_index, 2)
        self.assertEqual(ladder.hedge_fraction(), 1.0)

        d3 = ladder.evaluate(-12.5)
        self.assertEqual(d3.level.action, DERISK_STOP)

    def test_deep_drawdown_jumps_to_deepest_level(self):
        ladder = self.make_ladder()
        decision = ladder.evaluate(-13.0)

        self.assertEqual(decision.level_index, 3)
        self.assertEqual(decision.level.action, DERISK_STOP)

    def test_recovery_resets_ladder(self):
        ladder = self.make_ladder()
        ladder.evaluate(-5.5)

        # 첫 레벨의 절반(-2.5%) 이내로 회복 → 리셋
        self.assertIsNone(ladder.evaluate(-2.0))
        self.assertEqual(ladder.current_level, 0)
        # 재악화 시 다시 발동
        self.assertIsNotNone(ladder.evaluate(-5.5))

    def test_partial_recovery_does_not_reset(self):
        ladder = self.make_ladder()
        ladder.evaluate(-8.2)

        self.assertIsNone(ladder.evaluate(-6.0))
        self.assertEqual(ladder.current_level, 2)
        self.assertEqual(ladder.hedge_fraction(), 1.0)


# ─── 헤지 목표 ──────────────────────────────────────────

class HedgeTargetTests(unittest.TestCase):
    def test_disabled_returns_zero(self):
        self.assertEqual(
            hedge_target_qty(10.0, 2000.0, REGIME_TREND_DOWN, 1.0, 1.0,
                             0.0, enabled=False),
            0.0,
        )

    def test_range_regime_without_ladder_is_zero(self):
        self.assertEqual(
            hedge_target_qty(10.0, 2000.0, REGIME_RANGE, 0.0, 1.0, 0.0),
            0.0,
        )

    def test_trend_down_hedges_full_ratio(self):
        self.assertAlmostEqual(
            hedge_target_qty(10.0, 2000.0, REGIME_TREND_DOWN, 0.0, 1.0, 0.0),
            10.0,
        )

    def test_ladder_fraction_applies_in_any_regime(self):
        self.assertAlmostEqual(
            hedge_target_qty(10.0, 2000.0, REGIME_RANGE, 0.5, 1.0, 0.0),
            5.0,
        )

    def test_inventory_cap_excess_added_in_trend_down(self):
        # 노출 20,000 > 상한 12,000 → 초과 8,000 / 2,000 = 4 ETH
        target = hedge_target_qty(
            10.0, 2000.0, REGIME_TREND_DOWN,
            ladder_fraction=0.0, regime_hedge_ratio=0.0,
            inventory_cap_usd=12_000.0,
        )
        self.assertAlmostEqual(target, 4.0)

    def test_hedge_never_exceeds_holding(self):
        target = hedge_target_qty(
            3.0, 2000.0, REGIME_TREND_DOWN,
            ladder_fraction=1.0, regime_hedge_ratio=1.0,
            inventory_cap_usd=1.0,
        )
        self.assertAlmostEqual(target, 3.0)


# ─── 이탈 판정 히스테리시스 / 패스트패스 ─────────────────

def make_risk_agent(regime=REGIME_RANGE):
    agent = object.__new__(GridAgent)
    agent.holding_qty = 0.0
    agent.holding_cost = 0.0
    agent.realized_pnl = 0.0
    agent.daily_realized = 0.0
    agent.daily_fees = 0.0
    agent.daily_buys = 0
    agent.daily_sells = 0
    agent.total_fees_paid = 0.0
    agent.grid_restart_times = []
    agent.grid_restart_count = 0
    agent.entry_price = None
    agent.loop_count = 1
    agent.notifier = FakeNotifier()
    agent.controller = FakeGridController()
    agent.regime_detector = RegimeDetector()
    agent.regime_detector.regime = regime
    agent.derisk_ladder = DeriskLadder(parse_derisk_levels("5:0.5,8:1.0,12:stop"))
    agent.hedge = None
    agent.hedge_qty = 0.0
    agent.bottom_zone_ticks = 0
    agent.grid_breakout_time = None
    agent.grid_breakout_dir = None
    agent.grid_breakout_notified = False
    agent.BREAKOUT_WAIT_SEC = 6 * 3600
    agent.BREAKOUT_HARD_TIMEOUT_SEC = 18 * 3600
    agent._last_regime_state = None
    agent._regime_last_fetch = None
    agent._log = lambda *args, **kwargs: None
    agent._refresh_bot_label = lambda: None
    return agent


class BreakoutHysteresisTests(unittest.TestCase):
    def setUp(self):
        self.old_buffer = config.BREAKOUT_REENTRY_BUFFER_PCT
        self.old_fast = config.BREAKOUT_TREND_FAST_HOURS
        config.BREAKOUT_REENTRY_BUFFER_PCT = 1.0
        config.BREAKOUT_TREND_FAST_HOURS = 2.0

    def tearDown(self):
        config.BREAKOUT_REENTRY_BUFFER_PCT = self.old_buffer
        config.BREAKOUT_TREND_FAST_HOURS = self.old_fast

    def test_shallow_reentry_keeps_timer(self):
        agent = make_risk_agent()
        agent.grid_breakout_time = datetime.now() - timedelta(hours=3)
        agent.grid_breakout_dir = "BELOW"
        agent.grid_breakout_notified = True

        # 하단 2000 안쪽이지만 버퍼(2020) 미만 → 타이머 유지
        result = agent._check_grid_breakout(make_signal(), price=2_005.0)

        self.assertIsNone(result)
        self.assertIsNotNone(agent.grid_breakout_time)

    def test_buffered_reentry_resets_timer(self):
        agent = make_risk_agent()
        agent.grid_breakout_time = datetime.now() - timedelta(hours=3)
        agent.grid_breakout_dir = "BELOW"
        agent.grid_breakout_notified = True

        result = agent._check_grid_breakout(make_signal(), price=2_025.0)

        self.assertIsNone(result)
        self.assertIsNone(agent.grid_breakout_time)

    def test_trend_down_fast_path_forces_shift_down(self):
        agent = make_risk_agent(regime=REGIME_TREND_DOWN)
        agent.grid_breakout_time = datetime.now() - timedelta(hours=3)
        agent.grid_breakout_dir = "BELOW"
        agent.grid_breakout_notified = True

        result = agent._check_grid_breakout(make_signal(), price=1_950.0)

        self.assertEqual(result, "MAINTAIN")     # 재배치 완료 신호
        self.assertIsNotNone(agent.controller.started)  # shift 실행됨
        self.assertIsNone(agent.grid_breakout_time)

    def test_range_regime_does_not_fast_path(self):
        agent = make_risk_agent(regime=REGIME_RANGE)
        agent.multi_agent = type("X", (), {"available": False})()
        agent.llm_judge = type("J", (), {"judge": lambda *a, **k: "MAINTAIN"})()
        agent.grid_breakout_time = datetime.now() - timedelta(hours=3)
        agent.grid_breakout_dir = "BELOW"
        agent.grid_breakout_notified = True

        result = agent._check_grid_breakout(make_signal(), price=1_950.0)

        # 3h < 6h 대기 → 아직 재배치 없음 (대기 오버라이드만 반환)
        self.assertIsNone(agent.controller.started)
        self.assertEqual(result, "MAINTAIN")
        self.assertIsNotNone(agent.grid_breakout_time)


class RestartGuardRegimeTests(unittest.TestCase):
    def test_defensive_shift_down_allowed_in_trend_down_while_losing(self):
        agent = make_risk_agent(regime=REGIME_TREND_DOWN)
        agent.holding_qty = 10.0
        agent.holding_cost = 22_000.0
        agent.realized_pnl = -1_000.0
        agent.daily_realized = 0.0

        allowed, reason = agent._check_restart_allowed("SHIFT_DOWN", 2_000.0)

        self.assertTrue(allowed)

    def test_hard_restart_limit_still_applies_in_trend_down(self):
        agent = make_risk_agent(regime=REGIME_TREND_DOWN)
        agent.grid_restart_times = [datetime.now(), datetime.now()]

        allowed, reason = agent._check_restart_allowed("SHIFT_DOWN", 2_000.0)

        self.assertFalse(allowed)
        self.assertIn("재시작", reason)


# ─── 청산 검증 ──────────────────────────────────────────

class VerifyingStopController(GridController):
    """stopType 버그 시나리오 재현: 봇 정지 후 기초자산이 남아 있는 컨트롤러."""

    def __init__(self, leftover_qty=5.0):
        self.bot_id = "bot"
        self.leftover_qty = leftover_qty
        self.market_sells = []

    def stop_grid(self, sell_remaining=False):
        self.bot_id = None
        return {"code": "0"}

    def get_last_price(self):
        return 2_000.0

    def get_base_balance(self):
        return self.leftover_qty

    def spot_market_sell(self, qty):
        self.market_sells.append(qty)
        self.leftover_qty = 0.0
        return {"code": "0"}

    def _log(self, msg, level="INFO"):
        pass


class EmergencyStopVerificationTests(unittest.TestCase):
    def test_leftover_inventory_is_flattened(self):
        controller = VerifyingStopController(leftover_qty=5.0)

        with patch("grid_controller.time.sleep"):
            result = controller.emergency_stop(verify=True)

        self.assertEqual(controller.market_sells, [5.0])
        self.assertTrue(result["verification"]["flat"])

    def test_already_flat_skips_market_sell(self):
        controller = VerifyingStopController(leftover_qty=0.0)

        with patch("grid_controller.time.sleep"):
            result = controller.emergency_stop(verify=True)

        self.assertEqual(controller.market_sells, [])
        self.assertTrue(result["verification"]["flat"])


if __name__ == "__main__":
    unittest.main()
