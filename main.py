from __future__ import annotations

import argparse
import time
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from alpaca_io import AlpacaIO, write_csv, signal_to_row
from config import AppConfig, StrategyConfig, optimization_grid
from strategy import (
    BacktestTrade,
    build_signal,
    calculate_position_size,
    hit_daily_loss_limit,
    prepare_intraday_frame,
    simulate_trade_path,
    summarize_trades,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple Alpaca VWAP trader and backtester.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    trade = subparsers.add_parser("trade", help="Run live or paper trading loop.")
    trade.add_argument("--mode", choices=["paper", "live"], default="paper")
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
    backtest.add_argument("--asset-class", choices=["all", "equities", "crypto"], default="all")
    backtest.add_argument("--symbols", nargs="*", default=None)
    backtest.add_argument("--cash", type=float, default=1_000.0)
    backtest.add_argument("--max-positions", type=int, default=2)
    backtest.add_argument("--allow-short", action="store_true")
    backtest.add_argument("--optimize", action="store_true")
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


def load_symbols(io: AlpacaIO, asset_class: str, explicit_symbols: list[str] | None, config: AppConfig) -> list[str]:
    return io.select_universe(asset_class=asset_class, symbols=explicit_symbols, universe_cfg=config.universe)


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


def trade_command(args: argparse.Namespace) -> None:
    config = app_config_from_args(args)
    io = AlpacaIO(paper=args.mode == "paper", config=config)
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
) -> list[BacktestTrade]:
    trades: list[BacktestTrade] = []
    for session in session_slices(bars, config.session_timezone):
        prepared = prepare_intraday_frame(session)
        if prepared.empty:
            continue
        signal = build_signal(symbol, prepared, config.strategy)
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
        future = prepared.loc[prepared.index > signal.timestamp]
        if future.empty:
            continue
        trades.append(simulate_trade_path(signal, future, qty, config.strategy))
    return trades


def backtest_once(io: AlpacaIO, args: argparse.Namespace, config: AppConfig) -> tuple[list[BacktestTrade], dict[str, float]]:
    all_trades: list[BacktestTrade] = []
    for asset_class in resolved_asset_classes(args.asset_class):
        symbols = load_symbols(io, asset_class, args.symbols, config)
        for symbol in symbols:
            bars = io.fetch_bars(symbol, asset_class, args.start, args.end)
            all_trades.extend(run_backtest_for_symbol(symbol, asset_class, bars, config))
    summary = summarize_trades(all_trades, config.cash)
    return all_trades, summary


def optimize_backtest(io: AlpacaIO, args: argparse.Namespace, base_config: AppConfig) -> tuple[list[dict], dict | None]:
    rows: list[dict] = []
    best_row: dict | None = None
    for candidate in optimization_grid(base_config.strategy):
        config = replace(base_config, strategy=candidate)
        trades, summary = backtest_once(io, args, config)
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
    io = AlpacaIO(paper=True, config=config)
    output_dir = Path(config.results_dir)
    if args.optimize:
        rows, best = optimize_backtest(io, args, config)
        write_csv(output_dir / "optimization.csv", rows)
        if best is not None:
            pd.DataFrame([best]).to_csv(output_dir / "best_config.csv", index=False)
        return

    trades, summary = backtest_once(io, args, config)
    trade_rows = [
        {
            "symbol": trade.symbol,
            "side": trade.side,
            "entry_time": trade.entry_time.isoformat(),
            "exit_time": trade.exit_time.isoformat(),
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "qty": trade.qty,
            "pnl": trade.pnl,
            "pnl_pct": trade.pnl_pct,
            "exit_reason": trade.exit_reason,
            "r_multiple": trade.r_multiple,
        }
        for trade in trades
    ]
    write_csv(output_dir / "backtest_trades.csv", trade_rows)
    pd.DataFrame([summary]).to_csv(output_dir / "backtest_summary.csv", index=False)


def main() -> None:
    args = parse_args()
    if args.command == "trade":
        trade_command(args)
    elif args.command == "backtest":
        backtest_command(args)
    else:  # pragma: no cover
        raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
