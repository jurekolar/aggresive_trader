from __future__ import annotations

import argparse
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pandas as pd

from alpaca_io import AlpacaIO, write_csv, signal_to_row
from config import AppConfig, StrategyConfig, optimization_grid
from env_config import load_dotenv, resolve_mode
from strategy import (
    BacktestTrade,
    Signal,
    build_signal,
    calculate_position_size,
    hit_daily_loss_limit,
    prepare_intraday_frame,
    simulate_trade_path,
    summarize_trades_by_day_type,
    summarize_exit_reasons,
    summarize_trades,
    summarize_trades_by_symbol,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple Alpaca VWAP trader and backtester.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    trade = subparsers.add_parser("trade", help="Run live or paper trading loop.")
    trade.add_argument("--mode", choices=["paper", "live"], default=None)
    trade.add_argument("--asset-class", choices=["all", "equities", "crypto"], default="all")
    trade.add_argument("--symbols", nargs="*", default=None)
    trade.add_argument("--cash", type=float, default=1_000.0)
    trade.add_argument("--max-positions", type=int, default=2)
    trade.add_argument("--poll-seconds", type=int, default=20)
    trade.add_argument("--allow-short", action="store_true")
    trade.add_argument("--dry-run", action="store_true")

    backtest = subparsers.add_parser("backtest", help="Backtest strategy on Alpaca historical data.")
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--mode", choices=["paper", "live"], default=None)
    backtest.add_argument("--asset-class", choices=["all", "equities", "crypto"], default="all")
    backtest.add_argument("--symbols", nargs="*", default=None)
    backtest.add_argument("--cash", type=float, default=1_000.0)
    backtest.add_argument("--max-positions", type=int, default=2)
    backtest.add_argument("--allow-short", action="store_true")
    backtest.add_argument("--optimize", action="store_true")
    backtest.add_argument("--walk-forward", action="store_true")
    backtest.add_argument("--research-universe", action="store_true")
    backtest.add_argument("--train-days", type=int, default=60)
    backtest.add_argument("--validation-days", type=int, default=20)
    return parser.parse_args()


def app_config_from_args(args: argparse.Namespace) -> AppConfig:
    strategy = replace(
        StrategyConfig(),
        max_positions=args.max_positions,
        allow_short=getattr(args, "allow_short", False),
    )
    return replace(
        AppConfig(),
        cash=args.cash,
        poll_seconds=getattr(args, "poll_seconds", AppConfig().poll_seconds),
        strategy=strategy,
    )


def resolved_asset_classes(asset_class: str) -> list[str]:
    return ["equities", "crypto"] if asset_class == "all" else [asset_class]


def resolve_symbol_override(
    asset_class: str,
    explicit_symbols: list[str] | None,
    config: AppConfig,
    use_research_universe: bool = False,
) -> list[str] | None:
    if explicit_symbols is not None:
        return explicit_symbols
    if use_research_universe and asset_class == "equities":
        return config.universe.research_equities
    return explicit_symbols


def load_symbols(
    io: AlpacaIO,
    asset_class: str,
    explicit_symbols: list[str] | None,
    config: AppConfig,
    use_research_universe: bool = False,
) -> list[str]:
    return io.select_universe(
        asset_class=asset_class,
        symbols=resolve_symbol_override(asset_class, explicit_symbols, config, use_research_universe),
        universe_cfg=config.universe,
    )


def result_prefix(args: argparse.Namespace) -> str:
    return "research_" if getattr(args, "research_universe", False) else ""


def regular_session_bars(bars: pd.DataFrame, timezone: str) -> pd.DataFrame:
    if bars.empty:
        return bars
    localized = bars.copy()
    local_index = localized.index.tz_convert(timezone)
    localized["_session_date"] = local_index.date
    localized["_session_time"] = local_index.time
    localized = localized[
        (localized["_session_time"] >= pd.Timestamp("09:30").time())
        & (localized["_session_time"] <= pd.Timestamp("16:00").time())
    ]
    return localized.drop(columns=["_session_time"])


def session_slices(bars: pd.DataFrame, timezone: str) -> list[pd.DataFrame]:
    filtered = regular_session_bars(bars, timezone)
    if filtered.empty:
        return []
    sessions: list[pd.DataFrame] = []
    for _, session in filtered.groupby("_session_date"):
        sessions.append(session.drop(columns=["_session_date"]))
    return sessions


@dataclass
class BacktestCandidate:
    asset_class: str
    signal: Signal
    future_bars: pd.DataFrame


@dataclass(frozen=True)
class WalkForwardSplit:
    fold: int
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str


def trade_command(args: argparse.Namespace) -> None:
    config = app_config_from_args(args)
    mode = resolve_mode(args.mode)
    io = AlpacaIO(mode=mode, config=config)
    clock = io.market_clock()
    if not clock.is_open:
        raise SystemExit(f"Market is closed. Next open: {clock.next_open}")

    asset_classes = resolved_asset_classes(args.asset_class)
    signal_rows: list[dict] = []
    open_symbols: set[str] = set()
    realized_pnl = 0.0
    starting_cash = config.cash

    while True:
        clock = io.market_clock()
        minutes_to_close = (pd.Timestamp(clock.next_close) - pd.Timestamp(clock.timestamp)).total_seconds() / 60.0
        if minutes_to_close <= 5:
            io.close_all_positions(cancel_orders=True)
            break
        if hit_daily_loss_limit(starting_cash, realized_pnl, config.strategy):
            io.close_all_positions(cancel_orders=True)
            break

        positions = io.open_positions()
        open_symbols = {position.symbol for position in positions}
        if len(positions) >= config.strategy.max_positions:
            time.sleep(config.poll_seconds)
            continue

        for asset_class in asset_classes:
            symbols = load_symbols(io, asset_class, args.symbols, config)
            for symbol in symbols:
                if symbol in open_symbols:
                    continue
                bars = io.latest_intraday_bars(symbol, asset_class)
                if bars.empty:
                    continue
                signal = build_signal(symbol, bars, config.strategy)
                if signal is None:
                    continue
                qty = calculate_position_size(
                    cash=config.cash,
                    entry_price=signal.entry_price,
                    stop_price=signal.stop_price,
                    risk_per_trade_pct=config.strategy.risk_per_trade_pct,
                )
                if qty <= 0:
                    continue
                signal_rows.append(signal_to_row(signal, qty, asset_class))
                if not args.dry_run:
                    io.place_signal_order(signal, qty, asset_class)
                open_symbols.add(symbol)
                if len(open_symbols) >= config.strategy.max_positions:
                    break
            if len(open_symbols) >= config.strategy.max_positions:
                break
        time.sleep(config.poll_seconds)

    write_csv(Path(config.results_dir) / "trade_signals.csv", signal_rows)


def run_backtest_for_symbol(
    symbol: str,
    asset_class: str,
    bars: pd.DataFrame,
    config: AppConfig,
) -> list[BacktestCandidate]:
    candidates: list[BacktestCandidate] = []
    for session in session_slices(bars, config.session_timezone):
        prepared = prepare_intraday_frame(session)
        if prepared.empty:
            continue
        signal = build_signal(symbol, prepared, config.strategy)
        if signal is None:
            continue
        future = prepared.loc[prepared.index > signal.timestamp]
        if future.empty:
            continue
        candidates.append(
            BacktestCandidate(
                asset_class=asset_class,
                signal=signal,
                future_bars=future,
            )
        )
    return candidates


def execute_backtest_candidates(
    candidates: list[BacktestCandidate],
    config: AppConfig,
) -> tuple[list[BacktestTrade], dict[str, int]]:
    trades: list[BacktestTrade] = []
    active_positions: list[tuple[pd.Timestamp, float, float]] = []
    available_cash = config.cash
    skipped_max_positions = 0
    skipped_no_cash = 0

    for candidate in sorted(candidates, key=lambda item: item.signal.timestamp):
        entry_time = candidate.signal.timestamp
        remaining_positions: list[tuple[pd.Timestamp, float, float]] = []
        for exit_time, reserved_cash, proceeds in active_positions:
            if exit_time <= entry_time:
                available_cash += proceeds
            else:
                remaining_positions.append((exit_time, reserved_cash, proceeds))
        active_positions = remaining_positions

        if len(active_positions) >= config.strategy.max_positions:
            skipped_max_positions += 1
            continue

        qty = calculate_position_size(
            cash=available_cash,
            entry_price=candidate.signal.entry_price,
            stop_price=candidate.signal.stop_price,
            risk_per_trade_pct=config.strategy.risk_per_trade_pct,
        )
        if qty <= 0:
            skipped_no_cash += 1
            continue

        capital_required = candidate.signal.entry_price * qty
        if capital_required > available_cash:
            qty = available_cash / candidate.signal.entry_price if candidate.signal.entry_price else 0.0
            capital_required = candidate.signal.entry_price * qty
        if qty <= 0 or capital_required <= 0:
            skipped_no_cash += 1
            continue

        available_cash -= capital_required
        trade = simulate_trade_path(candidate.signal, candidate.future_bars, qty, config.strategy)
        proceeds = capital_required + trade.pnl
        active_positions.append((trade.exit_time, capital_required, proceeds))
        trades.append(trade)

    trades.sort(key=lambda trade: (trade.entry_time, trade.exit_time, trade.symbol))
    stats = {
        "signals_considered": len(candidates),
        "skipped_max_positions": skipped_max_positions,
        "skipped_no_cash": skipped_no_cash,
    }
    return trades, stats


def backtest_once(io: AlpacaIO, args: argparse.Namespace, config: AppConfig) -> tuple[list[BacktestTrade], dict[str, float]]:
    return backtest_from_inputs(load_backtest_inputs(io, args, config), config)


def backtest_from_candidates(
    candidates: list[BacktestCandidate],
    config: AppConfig,
) -> tuple[list[BacktestTrade], dict[str, float]]:
    trades, stats = execute_backtest_candidates(candidates, config)
    summary = summarize_trades(
        trades,
        config.cash,
        signals_considered=stats["signals_considered"],
        skipped_max_positions=stats["skipped_max_positions"],
        skipped_no_cash=stats["skipped_no_cash"],
    )
    return trades, summary


def load_backtest_inputs(
    io: AlpacaIO,
    args: argparse.Namespace,
    config: AppConfig,
) -> list[tuple[str, str, pd.DataFrame]]:
    datasets: list[tuple[str, str, pd.DataFrame]] = []
    for asset_class in resolved_asset_classes(args.asset_class):
        symbols = load_symbols(
            io,
            asset_class,
            args.symbols,
            config,
            use_research_universe=getattr(args, "research_universe", False),
        )
        for symbol in symbols:
            bars = io.fetch_bars(symbol, asset_class, args.start, args.end)
            datasets.append((asset_class, symbol, bars))
    return datasets


def backtest_from_inputs(
    datasets: list[tuple[str, str, pd.DataFrame]],
    config: AppConfig,
) -> tuple[list[BacktestTrade], dict[str, float]]:
    candidates: list[BacktestCandidate] = []
    for asset_class, symbol, bars in datasets:
        candidates.extend(run_backtest_for_symbol(symbol, asset_class, bars, config))
    return backtest_from_candidates(candidates, config)


def slice_bars_by_date_range(
    bars: pd.DataFrame,
    timezone: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    if bars.empty:
        return bars
    localized_dates = pd.Index(bars.index.tz_convert(timezone).date)
    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    return bars[(localized_dates >= start) & (localized_dates <= end)]


def slice_inputs_by_date_range(
    datasets: list[tuple[str, str, pd.DataFrame]],
    timezone: str,
    start_date: str,
    end_date: str,
) -> list[tuple[str, str, pd.DataFrame]]:
    sliced: list[tuple[str, str, pd.DataFrame]] = []
    for asset_class, symbol, bars in datasets:
        window = slice_bars_by_date_range(bars, timezone, start_date, end_date)
        if not window.empty:
            sliced.append((asset_class, symbol, window))
    return sliced


def generate_walk_forward_splits(
    start_date: str,
    end_date: str,
    train_days: int,
    validation_days: int,
) -> list[WalkForwardSplit]:
    splits: list[WalkForwardSplit] = []
    overall_start = pd.Timestamp(start_date)
    overall_end = pd.Timestamp(end_date)
    fold = 1
    train_start = overall_start
    while True:
        train_end = train_start + pd.Timedelta(days=train_days - 1)
        validation_start = train_end + pd.Timedelta(days=1)
        validation_end = validation_start + pd.Timedelta(days=validation_days - 1)
        if validation_end > overall_end:
            break
        splits.append(
            WalkForwardSplit(
                fold=fold,
                train_start=train_start.date().isoformat(),
                train_end=train_end.date().isoformat(),
                validation_start=validation_start.date().isoformat(),
                validation_end=validation_end.date().isoformat(),
            )
        )
        fold += 1
        train_start = validation_start
    return splits


def safe_profit_factor(summary: dict[str, float]) -> float:
    value = float(summary["profit_factor"])
    return value if value != float("inf") else float("nan")


def finite_profit_factor_stats(values: list[float]) -> tuple[float, float]:
    finite_values = [value for value in values if pd.notna(value)]
    if not finite_values:
        return (float("inf"), float("inf"))
    series = pd.Series(finite_values, dtype="float64")
    return (float(series.mean()), float(series.median()))


def evaluate_walk_forward(
    datasets: list[tuple[str, str, pd.DataFrame]],
    splits: list[WalkForwardSplit],
    base_config: AppConfig,
) -> tuple[list[dict], dict | None, list[dict], list[BacktestTrade]]:
    ranking_rows: list[dict] = []
    best_row: dict | None = None
    best_fold_rows: list[dict] = []
    best_validation_trades: list[BacktestTrade] = []
    for candidate in optimization_grid(base_config.strategy):
        config = replace(base_config, strategy=candidate)
        fold_rows: list[dict] = []
        validation_trades: list[BacktestTrade] = []
        validation_profit_factors: list[float] = []
        validation_net_profits: list[float] = []
        validation_win_rates: list[float] = []
        train_profit_factors: list[float] = []
        active_validation_folds = 0
        for split in splits:
            train_inputs = slice_inputs_by_date_range(
                datasets,
                base_config.session_timezone,
                split.train_start,
                split.train_end,
            )
            validation_inputs = slice_inputs_by_date_range(
                datasets,
                base_config.session_timezone,
                split.validation_start,
                split.validation_end,
            )
            _, train_summary = backtest_from_inputs(train_inputs, config)
            fold_validation_trades, validation_summary = backtest_from_inputs(validation_inputs, config)
            validation_trades.extend(fold_validation_trades)
            validation_profit_factors.append(safe_profit_factor(validation_summary))
            validation_net_profits.append(float(validation_summary["net_profit"]))
            validation_win_rates.append(float(validation_summary["win_rate"]))
            train_profit_factors.append(safe_profit_factor(train_summary))
            if float(validation_summary["trades"]) > 0:
                active_validation_folds += 1
            fold_rows.append(
                {
                    "fold": split.fold,
                    "train_start": split.train_start,
                    "train_end": split.train_end,
                    "validation_start": split.validation_start,
                    "validation_end": split.validation_end,
                    "train_profit_factor": train_summary["profit_factor"],
                    "train_net_profit": train_summary["net_profit"],
                    "train_trades": train_summary["trades"],
                    "validation_profit_factor": validation_summary["profit_factor"],
                    "validation_net_profit": validation_summary["net_profit"],
                    "validation_win_rate": validation_summary["win_rate"],
                    "validation_trades": validation_summary["trades"],
                }
            )
        if not fold_rows:
            continue
        ranking_row = {
            **asdict(candidate),
            "folds": len(fold_rows),
            "avg_train_profit_factor": sum(train_profit_factors) / len(train_profit_factors),
            "avg_validation_profit_factor": finite_profit_factor_stats(validation_profit_factors)[0],
            "median_validation_profit_factor": finite_profit_factor_stats(validation_profit_factors)[1],
            "avg_validation_net_profit": sum(validation_net_profits) / len(validation_net_profits),
            "total_validation_net_profit": sum(validation_net_profits),
            "avg_validation_win_rate": sum(validation_win_rates) / len(validation_win_rates),
            "active_validation_folds": active_validation_folds,
            "active_fold_ratio": active_validation_folds / len(fold_rows),
        }
        ranking_rows.append(ranking_row)
        if best_row is None or (
            ranking_row["active_fold_ratio"],
            ranking_row["median_validation_profit_factor"],
            ranking_row["avg_validation_profit_factor"],
            ranking_row["avg_validation_net_profit"],
        ) > (
            best_row["active_fold_ratio"],
            best_row["median_validation_profit_factor"],
            best_row["avg_validation_profit_factor"],
            best_row["avg_validation_net_profit"],
        ):
            best_row = ranking_row
            best_fold_rows = [
                {
                    **fold_row,
                    "stop_buffer_pct": candidate.stop_buffer_pct,
                    "vwap_tolerance_pct": candidate.vwap_tolerance_pct,
                    "target_r_multiple": candidate.target_r_multiple,
                }
                for fold_row in fold_rows
            ]
            best_validation_trades = validation_trades
    ranking_rows.sort(
        key=lambda row: (
            row["active_fold_ratio"],
            row["median_validation_profit_factor"],
            row["avg_validation_profit_factor"],
            row["avg_validation_net_profit"],
        ),
        reverse=True,
    )
    return ranking_rows, best_row, best_fold_rows, best_validation_trades


def build_walk_forward_report(
    args: argparse.Namespace,
    best_row: dict,
    fold_rows: list[dict],
    validation_trades: list[BacktestTrade],
) -> str:
    lines = [
        "Walk-Forward Report",
        f"Window: {args.start} to {args.end}",
        f"Train days: {args.train_days}",
        f"Validation days: {args.validation_days}",
        f"Best stop_buffer_pct: {best_row['stop_buffer_pct']}",
        f"Best vwap_tolerance_pct: {best_row['vwap_tolerance_pct']}",
        f"Best target_r_multiple: {best_row['target_r_multiple']}",
        f"Active fold ratio: {best_row['active_fold_ratio'] * 100:.2f}%",
        f"Average finite validation profit factor: {best_row['avg_validation_profit_factor']:.2f}",
        f"Median finite validation profit factor: {best_row['median_validation_profit_factor']:.2f}",
        f"Average validation net profit: {best_row['avg_validation_net_profit']:.2f}",
        f"Average validation win rate: {best_row['avg_validation_win_rate'] * 100:.2f}%",
        "",
        "Validation breakdown by symbol:",
    ]
    for row in summarize_trades_by_symbol(validation_trades)[:5]:
        lines.append(
            f"{row['symbol']}: pnl={float(row['net_profit']):.2f}, pf={float(row['profit_factor']):.2f}, trades={int(row['trades'])}"
        )
    lines.extend(["", "Validation breakdown by day type:"])
    for row in summarize_trades_by_day_type(validation_trades):
        lines.append(
            f"{row['day_type']}: pnl={float(row['net_profit']):.2f}, pf={float(row['profit_factor']):.2f}, trades={int(row['trades'])}"
        )
    lines.extend(["", "Fold summary:"])
    for row in fold_rows:
        lines.append(
            f"Fold {row['fold']}: valid {row['validation_start']} to {row['validation_end']}, pf={float(row['validation_profit_factor']):.2f}, pnl={float(row['validation_net_profit']):.2f}"
        )
    return "\n".join(lines) + "\n"


def build_backtest_report(
    trades: list[BacktestTrade],
    summary: dict[str, float],
    config: AppConfig,
    args: argparse.Namespace,
) -> str:
    lines = [
        "Backtest Report",
        f"Window: {args.start} to {args.end}",
        f"Asset class: {args.asset_class}",
        f"Starting cash: {config.cash:.2f}",
        f"Ending cash: {summary['ending_cash']:.2f}",
        f"Net profit: {summary['net_profit']:.2f} ({summary['return_pct'] * 100:.2f}%)",
        f"Trades: {int(summary['trades'])} from {int(summary['signals_considered'])} signals",
        f"Win rate: {summary['win_rate'] * 100:.2f}%",
        f"Profit factor: {summary['profit_factor']:.2f}",
        f"Average R: {summary['avg_r_multiple']:.2f}",
        f"Max drawdown: {summary['max_drawdown']:.2f} ({summary['max_drawdown_pct'] * 100:.2f}%)",
        f"Average hold: {summary['avg_hold_minutes']:.2f} minutes",
        f"Stops/targets/VWAP/session close: {int(summary['stop_exits'])}/{int(summary['target_exits'])}/{int(summary['vwap_loss_exits'])}/{int(summary['session_close_exits'])}",
        f"Partial exits taken: {int(summary['partial_exits'])}",
        f"Skipped signals - max positions: {int(summary['skipped_max_positions'])}, no cash: {int(summary['skipped_no_cash'])}",
        "",
        "Top symbols by net profit:",
    ]
    for row in summarize_trades_by_symbol(trades)[:5]:
        lines.append(
            f"{row['symbol']}: pnl={float(row['net_profit']):.2f}, trades={int(row['trades'])}, win_rate={float(row['win_rate']) * 100:.2f}%"
        )
    return "\n".join(lines) + "\n"


def optimize_backtest(io: AlpacaIO, args: argparse.Namespace, base_config: AppConfig) -> tuple[list[dict], dict | None]:
    rows: list[dict] = []
    best_row: dict | None = None
    datasets = load_backtest_inputs(io, args, base_config)
    for candidate in optimization_grid(base_config.strategy):
        config = replace(base_config, strategy=candidate)
        trades, summary = backtest_from_inputs(datasets, config)
        row = {
            **asdict(candidate),
            **summary,
            "trade_count": len(trades),
        }
        rows.append(row)
        if summary["max_drawdown"] > base_config.cash * base_config.strategy.daily_loss_limit_pct * 3:
            continue
        if best_row is None or row["net_profit"] > best_row["net_profit"]:
            best_row = row
    rows.sort(key=lambda item: item["net_profit"], reverse=True)
    return rows, best_row


def backtest_command(args: argparse.Namespace) -> None:
    config = app_config_from_args(args)
    io = AlpacaIO(mode=resolve_mode(args.mode), config=config)
    output_dir = Path(config.results_dir)
    prefix = result_prefix(args)
    if args.walk_forward:
        datasets = load_backtest_inputs(io, args, config)
        splits = generate_walk_forward_splits(args.start, args.end, args.train_days, args.validation_days)
        ranking_rows, best_row, fold_rows, validation_trades = evaluate_walk_forward(datasets, splits, config)
        write_csv(output_dir / f"{prefix}walkforward_rankings.csv", ranking_rows)
        write_csv(output_dir / f"{prefix}walkforward_folds.csv", fold_rows)
        write_csv(output_dir / f"{prefix}walkforward_validation_by_symbol.csv", summarize_trades_by_symbol(validation_trades))
        write_csv(output_dir / f"{prefix}walkforward_validation_by_day_type.csv", summarize_trades_by_day_type(validation_trades))
        if best_row is not None:
            pd.DataFrame([best_row]).to_csv(output_dir / f"{prefix}walkforward_best_config.csv", index=False)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{prefix}walkforward_report.txt").write_text(
                build_walk_forward_report(args, best_row, fold_rows, validation_trades)
            )
        return
    if args.optimize:
        rows, best = optimize_backtest(io, args, config)
        write_csv(output_dir / f"{prefix}optimization.csv", rows)
        if best is not None:
            pd.DataFrame([best]).to_csv(output_dir / f"{prefix}best_config.csv", index=False)
        return

    trades, summary = backtest_once(io, args, config)
    trade_rows = [
        {
            "symbol": trade.symbol,
            "day_type": trade.day_type,
            "bias": trade.bias,
            "session_date": trade.session_date,
            "side": trade.side,
            "entry_time": trade.entry_time.isoformat(),
            "exit_time": trade.exit_time.isoformat(),
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "qty": trade.qty,
            "pnl": trade.pnl,
            "gross_pnl": trade.gross_pnl,
            "fees": trade.fees,
            "pnl_pct": trade.pnl_pct,
            "exit_reason": trade.exit_reason,
            "r_multiple": trade.r_multiple,
            "risk_amount": trade.risk_amount,
            "capital_committed": trade.capital_committed,
            "partial_exit_taken": trade.partial_exit_taken,
            "partial_exit_price": trade.partial_exit_price,
            "partial_exit_qty": trade.partial_exit_qty,
            "partial_realized_pnl": trade.partial_realized_pnl,
            "holding_minutes": trade.holding_minutes,
            "mfe_r": trade.mfe_r,
            "mae_r": trade.mae_r,
        }
        for trade in trades
    ]
    write_csv(output_dir / f"{prefix}backtest_trades.csv", trade_rows)
    pd.DataFrame([summary]).to_csv(output_dir / f"{prefix}backtest_summary.csv", index=False)
    write_csv(output_dir / f"{prefix}backtest_symbol_summary.csv", summarize_trades_by_symbol(trades))
    write_csv(output_dir / f"{prefix}backtest_day_type_summary.csv", summarize_trades_by_day_type(trades))
    write_csv(output_dir / f"{prefix}backtest_exit_summary.csv", summarize_exit_reasons(trades))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{prefix}backtest_report.txt").write_text(build_backtest_report(trades, summary, config, args))


def main() -> None:
    load_dotenv()
    args = parse_args()
    if args.command == "trade":
        trade_command(args)
    elif args.command == "backtest":
        backtest_command(args)
    else:  # pragma: no cover
        raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
