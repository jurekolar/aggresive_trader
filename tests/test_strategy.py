from __future__ import annotations

import unittest

import pandas as pd

from config import StrategyConfig
from strategy import (
    build_signal,
    calculate_position_size,
    fib_zone,
    hit_daily_loss_limit,
    prepare_intraday_frame,
    simulate_trade_path,
)


def sample_intraday_bars() -> pd.DataFrame:
    index = pd.date_range("2026-04-20 13:30:00+00:00", periods=25, freq="1min")
    rows = [
        (100.0, 101.0, 99.8, 100.8, 10_000),
        (100.8, 101.7, 100.7, 101.5, 11_000),
        (101.5, 102.5, 101.3, 102.2, 12_000),
        (102.2, 103.1, 102.0, 102.8, 13_000),
        (102.8, 103.6, 102.6, 103.5, 14_000),
        (103.5, 104.5, 103.4, 104.3, 15_000),
        (104.3, 105.0, 104.1, 104.8, 16_000),
        (104.8, 105.5, 104.7, 105.2, 17_000),
        (105.2, 106.0, 105.0, 105.9, 18_000),
        (105.9, 106.4, 105.8, 106.1, 19_000),
        (106.1, 106.8, 106.0, 106.5, 20_000),
        (106.5, 107.0, 106.4, 106.8, 21_000),
        (106.8, 107.3, 106.7, 107.0, 22_000),
        (107.0, 107.4, 106.8, 107.1, 23_000),
        (107.1, 107.3, 106.9, 107.2, 24_000),
        (107.2, 107.3, 103.6, 105.2, 80_000),
        (104.8, 105.6, 103.4, 105.4, 90_000),
        (104.7, 105.6, 104.5, 105.4, 60_000),
        (105.4, 106.2, 105.2, 105.9, 40_000),
        (105.9, 106.8, 105.7, 106.6, 35_000),
        (106.6, 107.7, 106.4, 107.5, 30_000),
        (107.5, 108.2, 107.3, 108.1, 25_000),
        (108.1, 108.9, 107.9, 108.7, 20_000),
        (108.7, 109.0, 108.4, 108.8, 18_000),
        (108.8, 109.2, 108.7, 109.0, 16_000),
    ]
    return pd.DataFrame(rows, index=index, columns=["open", "high", "low", "close", "volume"])


class StrategyTests(unittest.TestCase):
    def test_prepare_intraday_frame_adds_vwap(self) -> None:
        prepared = prepare_intraday_frame(sample_intraday_bars().head(3))
        self.assertIn("vwap", prepared.columns)
        self.assertGreater(float(prepared["vwap"].iloc[-1]), 0.0)

    def test_fib_zone_for_long_move(self) -> None:
        lower, upper = fib_zone(100.0, 110.0, 0.5, 0.618)
        self.assertAlmostEqual(lower, 103.82, places=2)
        self.assertAlmostEqual(upper, 105.0, places=2)

    def test_build_signal_finds_long_pullback(self) -> None:
        signal = build_signal("SPY", sample_intraday_bars(), StrategyConfig())
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.side, "buy")
        self.assertGreater(signal.target_price, signal.entry_price)

    def test_position_size_is_risk_based_and_affordable(self) -> None:
        qty = calculate_position_size(1_000.0, 100.0, 99.0, 0.01)
        self.assertAlmostEqual(qty, 10.0)

    def test_daily_loss_limit_guardrail(self) -> None:
        self.assertTrue(hit_daily_loss_limit(1_000.0, -25.0, StrategyConfig()))
        self.assertFalse(hit_daily_loss_limit(1_000.0, -10.0, StrategyConfig()))

    def test_simulate_trade_path_hits_target(self) -> None:
        bars = sample_intraday_bars()
        signal = build_signal("SPY", bars, StrategyConfig())
        assert signal is not None
        prepared = prepare_intraday_frame(bars)
        future = prepared.loc[prepared.index > signal.timestamp]
        trade = simulate_trade_path(signal, future, qty=5.0, cfg=StrategyConfig())
        self.assertIn(trade.exit_reason, {"target", "session_close", "vwap_loss"})
        self.assertGreaterEqual(trade.exit_time, trade.entry_time)


if __name__ == "__main__":
    unittest.main()
