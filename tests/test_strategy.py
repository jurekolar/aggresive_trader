from __future__ import annotations

import unittest

import pandas as pd

from config import StrategyConfig
from config import AppConfig
from main import (
    BacktestCandidate,
    execute_backtest_candidates,
    finite_profit_factor_stats,
    generate_walk_forward_splits,
    result_prefix,
    resolve_symbol_override,
)
from strategy import (
    Signal,
    build_signal,
    calculate_position_size,
    fib_zone,
    hit_daily_loss_limit,
    prepare_intraday_frame,
    regime_allows_entry,
    regime_metrics,
    simulate_trade_path,
    summarize_trades,
    summarize_trades_by_day_type,
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


def weak_intraday_bars() -> pd.DataFrame:
    index = pd.date_range("2026-04-21 13:30:00+00:00", periods=25, freq="1min")
    rows = [
        (100.0, 100.15, 99.95, 100.05, 10_000),
        (100.05, 100.18, 100.0, 100.08, 10_500),
        (100.08, 100.2, 100.02, 100.1, 10_800),
        (100.1, 100.22, 100.04, 100.12, 11_000),
        (100.12, 100.24, 100.05, 100.14, 11_200),
        (100.14, 100.25, 100.06, 100.12, 11_100),
        (100.12, 100.21, 100.04, 100.1, 10_900),
        (100.1, 100.2, 100.03, 100.11, 10_700),
        (100.11, 100.23, 100.06, 100.13, 10_600),
        (100.13, 100.24, 100.07, 100.12, 10_500),
        (100.12, 100.22, 100.05, 100.1, 10_400),
        (100.1, 100.21, 100.04, 100.09, 10_300),
        (100.09, 100.2, 100.03, 100.08, 10_200),
        (100.08, 100.19, 100.02, 100.1, 10_100),
        (100.1, 100.2, 100.04, 100.09, 10_000),
        (100.09, 100.18, 100.03, 100.08, 9_900),
        (100.08, 100.17, 100.02, 100.07, 9_800),
        (100.07, 100.16, 100.01, 100.06, 9_700),
        (100.06, 100.15, 100.0, 100.05, 9_600),
        (100.05, 100.14, 99.99, 100.04, 9_500),
        (100.04, 100.13, 99.98, 100.03, 9_400),
        (100.03, 100.12, 99.97, 100.02, 9_300),
        (100.02, 100.11, 99.96, 100.01, 9_200),
        (100.01, 100.1, 99.95, 100.0, 9_100),
        (100.0, 100.09, 99.94, 99.99, 9_000),
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
        self.assertEqual(signal.metadata["day_type"], "trend_up")

    def test_regime_filter_rejects_weak_trend_session(self) -> None:
        prepared = prepare_intraday_frame(weak_intraday_bars())
        metrics = regime_metrics(prepared, StrategyConfig())
        self.assertFalse(regime_allows_entry(metrics, StrategyConfig()))
        self.assertIsNone(build_signal("SPY", weak_intraday_bars(), StrategyConfig()))

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

    def test_simulate_trade_path_records_partial_exit(self) -> None:
        index = pd.date_range("2026-04-20 14:00:00+00:00", periods=3, freq="1min")
        future = pd.DataFrame(
            [
                (100.0, 101.2, 99.9, 100.8, 100.7),
                (100.8, 100.9, 99.8, 99.9, 100.3),
                (99.9, 100.1, 99.7, 99.8, 100.1),
            ],
            index=index,
            columns=["open", "high", "low", "close", "vwap"],
        )
        signal = Signal(
            symbol="SPY",
            side="buy",
            timestamp=index[0] - pd.Timedelta(minutes=1),
            entry_price=100.0,
            stop_price=99.0,
            target_price=102.0,
            partial_price=101.0,
            reason="test",
            metadata={},
        )
        trade = simulate_trade_path(signal, future, qty=10.0, cfg=StrategyConfig())
        self.assertTrue(trade.partial_exit_taken)
        self.assertEqual(trade.exit_reason, "vwap_loss")
        self.assertAlmostEqual(trade.partial_exit_qty, 5.0)
        self.assertGreater(trade.partial_realized_pnl, 0.0)

    def test_summarize_trades_includes_partial_exits(self) -> None:
        bars = sample_intraday_bars()
        signal = build_signal("SPY", bars, StrategyConfig())
        assert signal is not None
        prepared = prepare_intraday_frame(bars)
        future = prepared.loc[prepared.index > signal.timestamp]
        trade = simulate_trade_path(signal, future, qty=5.0, cfg=StrategyConfig())
        summary = summarize_trades([trade], 1_000.0, signals_considered=1)
        self.assertEqual(summary["trades"], 1)
        self.assertEqual(summary["signals_considered"], 1)
        self.assertIn("avg_mfe_r", summary)

    def test_execute_backtest_candidates_enforces_max_positions(self) -> None:
        index = pd.date_range("2026-04-20 14:00:00+00:00", periods=2, freq="1min")
        future = pd.DataFrame(
            [
                (100.0, 100.2, 99.9, 100.1, 100.0),
                (100.1, 100.3, 100.0, 100.2, 100.1),
            ],
            index=index,
            columns=["open", "high", "low", "close", "vwap"],
        )
        signal_a = Signal(
            symbol="AAA",
            side="buy",
            timestamp=index[0] - pd.Timedelta(minutes=1),
            entry_price=100.0,
            stop_price=99.0,
            target_price=105.0,
            partial_price=101.0,
            reason="test",
            metadata={},
        )
        signal_b = Signal(
            symbol="BBB",
            side="buy",
            timestamp=index[0] - pd.Timedelta(minutes=1),
            entry_price=100.0,
            stop_price=99.0,
            target_price=105.0,
            partial_price=101.0,
            reason="test",
            metadata={},
        )
        class Config:
            cash = 1_000.0
            strategy = StrategyConfig(max_positions=1)

        trades, stats = execute_backtest_candidates(
            [
                BacktestCandidate(asset_class="equities", signal=signal_a, future_bars=future),
                BacktestCandidate(asset_class="equities", signal=signal_b, future_bars=future),
            ],
            Config(),
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(stats["skipped_max_positions"], 1)

    def test_summarize_trades_by_day_type_groups_results(self) -> None:
        bars = sample_intraday_bars()
        signal = build_signal("SPY", bars, StrategyConfig())
        assert signal is not None
        prepared = prepare_intraday_frame(bars)
        future = prepared.loc[prepared.index > signal.timestamp]
        trade = simulate_trade_path(signal, future, qty=5.0, cfg=StrategyConfig())
        rows = summarize_trades_by_day_type([trade])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["day_type"], "trend_up")

    def test_generate_walk_forward_splits_produces_rolling_windows(self) -> None:
        splits = generate_walk_forward_splits("2026-01-01", "2026-03-31", train_days=30, validation_days=15)
        self.assertGreaterEqual(len(splits), 2)
        self.assertEqual(splits[0].train_start, "2026-01-01")
        self.assertEqual(splits[0].validation_start, "2026-01-31")

    def test_finite_profit_factor_stats_ignores_infinite_values(self) -> None:
        avg_pf, median_pf = finite_profit_factor_stats([float("nan"), 1.5, 2.5])
        self.assertAlmostEqual(avg_pf, 2.0)
        self.assertAlmostEqual(median_pf, 2.0)

    def test_research_universe_uses_research_symbols_when_no_explicit_symbols(self) -> None:
        config = AppConfig()
        symbols = resolve_symbol_override("equities", None, config, use_research_universe=True)
        self.assertEqual(symbols, config.universe.research_equities)
        self.assertEqual(resolve_symbol_override("equities", ["AMD"], config, use_research_universe=True), ["AMD"])

    def test_result_prefix_uses_research_prefix(self) -> None:
        class Args:
            research_universe = True

        self.assertEqual(result_prefix(Args()), "research_")


if __name__ == "__main__":
    unittest.main()
