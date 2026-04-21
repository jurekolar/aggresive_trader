from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from config import StrategyConfig


@dataclass
class Signal:
    symbol: str
    side: str
    timestamp: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    partial_price: float
    reason: str
    metadata: dict[str, Any]


@dataclass
class BacktestTrade:
    symbol: str
    day_type: str
    bias: str
    session_date: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    gross_pnl: float
    fees: float
    pnl_pct: float
    exit_reason: str
    r_multiple: float
    risk_amount: float
    capital_committed: float
    partial_exit_taken: bool
    partial_exit_price: float | None
    partial_exit_qty: float
    partial_realized_pnl: float
    holding_minutes: float
    mfe_r: float
    mae_r: float


def prepare_intraday_frame(bars: pd.DataFrame) -> pd.DataFrame:
    frame = bars.copy()
    frame = frame.sort_index()
    typical_price = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    cumulative_volume = frame["volume"].cumsum()
    cumulative_turnover = (typical_price * frame["volume"]).cumsum()
    frame["vwap"] = cumulative_turnover / cumulative_volume.replace(0, pd.NA)
    frame["session_high"] = frame["high"].cummax()
    frame["session_low"] = frame["low"].cummin()
    frame["cum_volume"] = cumulative_volume
    frame["avg_volume"] = frame["volume"].expanding().mean()
    frame["relative_volume"] = frame["volume"] / frame["avg_volume"].replace(0, pd.NA)
    return frame


def opening_range(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    return frame.iloc[:minutes]


def opening_range_close_location(frame: pd.DataFrame) -> float:
    high = float(frame["high"].max())
    low = float(frame["low"].min())
    close = float(frame["close"].iloc[-1])
    spread = high - low
    if spread <= 0:
        return 0.5
    return (close - low) / spread


def regime_metrics(frame: pd.DataFrame, cfg: StrategyConfig) -> dict[str, float | str]:
    early = opening_range(frame, cfg.opening_range_minutes)
    if len(early) < max(3, cfg.opening_range_minutes // 2):
        return {
            "bias": "neutral",
            "day_type": "insufficient_data",
            "opening_move_pct": 0.0,
            "opening_range_pct": 0.0,
            "vwap_slope_pct": 0.0,
            "close_location": 0.5,
            "regime_strength_pct": 0.0,
        }
    start_price = float(early["open"].iloc[0])
    end_price = float(early["close"].iloc[-1])
    high = float(early["high"].max())
    low = float(early["low"].min())
    vwap_slope = float(early["vwap"].iloc[-1] - early["vwap"].iloc[0])
    impulse_pct = (end_price - start_price) / start_price
    opening_range_pct = (high - low) / start_price if start_price else 0.0
    vwap_slope_pct = vwap_slope / start_price if start_price else 0.0
    close_location = opening_range_close_location(early)
    regime_strength_pct = abs(impulse_pct) + abs(vwap_slope_pct)
    bias = "neutral"
    if impulse_pct >= cfg.min_opening_move_pct and vwap_slope_pct >= cfg.min_vwap_slope_pct:
        bias = "long"
    elif cfg.allow_short and impulse_pct <= -cfg.min_opening_move_pct and vwap_slope_pct <= -cfg.min_vwap_slope_pct:
        bias = "short"

    day_type = "weak_trend"
    if bias == "long" and close_location >= cfg.min_opening_range_close_location:
        day_type = "trend_up"
    elif bias == "short" and close_location <= (1.0 - cfg.min_opening_range_close_location):
        day_type = "trend_down"
    elif bias != "neutral":
        day_type = "mixed_trend"

    return {
        "bias": bias,
        "day_type": day_type,
        "opening_move_pct": impulse_pct,
        "opening_range_pct": opening_range_pct,
        "vwap_slope_pct": vwap_slope_pct,
        "close_location": close_location,
        "regime_strength_pct": regime_strength_pct,
    }


def regime_allows_entry(metrics: dict[str, float | str], cfg: StrategyConfig) -> bool:
    bias = str(metrics["bias"])
    day_type = str(metrics["day_type"])
    opening_range_pct = float(metrics["opening_range_pct"])
    regime_strength_pct = float(metrics["regime_strength_pct"])
    if bias == "neutral":
        return False
    if day_type not in {"trend_up", "trend_down"}:
        return False
    if opening_range_pct < cfg.min_opening_range_pct:
        return False
    if regime_strength_pct < cfg.min_regime_strength_pct:
        return False
    return True


def session_bias(frame: pd.DataFrame, cfg: StrategyConfig) -> str:
    return str(regime_metrics(frame, cfg)["bias"])


def impulse_leg(frame: pd.DataFrame, bias: str, cfg: StrategyConfig) -> tuple[float, float] | None:
    early = opening_range(frame, cfg.opening_range_minutes)
    if early.empty:
        return None
    if bias == "long":
        return float(early["low"].min()), float(early["high"].max())
    if bias == "short":
        return float(early["high"].max()), float(early["low"].min())
    return None


def fib_zone(leg_start: float, leg_end: float, lower: float, upper: float) -> tuple[float, float]:
    move = leg_end - leg_start
    if move == 0:
        return (leg_start, leg_end)
    if move > 0:
        upper_price = leg_end - move * lower
        lower_price = leg_end - move * upper
        return (lower_price, upper_price)
    upper_price = leg_end - move * upper
    lower_price = leg_end - move * lower
    return (lower_price, upper_price)


def price_in_zone(price: float, zone: tuple[float, float]) -> bool:
    lower, upper = sorted(zone)
    return lower <= price <= upper


def calculate_position_size(
    cash: float,
    entry_price: float,
    stop_price: float,
    risk_per_trade_pct: float,
) -> float:
    risk_budget = cash * risk_per_trade_pct
    risk_per_share = abs(entry_price - stop_price)
    if risk_budget <= 0 or risk_per_share <= 0:
        return 0.0
    qty = risk_budget / risk_per_share
    max_affordable = cash / entry_price
    return max(0.0, min(qty, max_affordable))


def hit_daily_loss_limit(starting_cash: float, realized_pnl: float, cfg: StrategyConfig) -> bool:
    return realized_pnl <= -(starting_cash * cfg.daily_loss_limit_pct)


def build_signal(symbol: str, frame: pd.DataFrame, cfg: StrategyConfig) -> Signal | None:
    prepared = prepare_intraday_frame(frame)
    metrics = regime_metrics(prepared, cfg)
    bias = str(metrics["bias"])
    if not regime_allows_entry(metrics, cfg):
        return None

    leg = impulse_leg(prepared, bias, cfg)
    if leg is None:
        return None

    zone = fib_zone(leg[0], leg[1], cfg.fib_lower, cfg.fib_upper)
    open_range = opening_range(prepared, cfg.opening_range_minutes)
    search_frame = prepared.iloc[len(open_range) :]
    if search_frame.empty:
        return None

    for timestamp, row in search_frame.iterrows():
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        vwap = float(row["vwap"])
        relative_volume = float(row["relative_volume"]) if pd.notna(row["relative_volume"]) else 0.0
        extension = abs((close - vwap) / vwap) if vwap else 0.0
        if extension > cfg.max_extension_from_vwap_pct:
            continue
        if relative_volume < cfg.min_relative_volume:
            continue

        if bias == "long":
            if not price_in_zone(low, zone) and not price_in_zone(close, zone):
                continue
            if abs((close - vwap) / vwap) > cfg.vwap_tolerance_pct and close < vwap:
                continue
            if close <= float(row["open"]):
                continue
            stop = low * (1.0 - cfg.stop_buffer_pct)
            risk = close - stop
            if risk <= 0:
                continue
            return Signal(
                symbol=symbol,
                side="buy",
                timestamp=timestamp,
                entry_price=close,
                stop_price=stop,
                target_price=close + risk * cfg.target_r_multiple,
                partial_price=close + risk * cfg.partial_exit_r_multiple,
                reason="VWAP pullback long",
                metadata={
                    "bias": bias,
                    "fib_zone": zone,
                    "vwap": vwap,
                    "day_type": metrics["day_type"],
                    "session_date": str(prepared.index[0].tz_convert("America/New_York").date()),
                    "opening_move_pct": metrics["opening_move_pct"],
                    "opening_range_pct": metrics["opening_range_pct"],
                    "vwap_slope_pct": metrics["vwap_slope_pct"],
                    "regime_strength_pct": metrics["regime_strength_pct"],
                },
            )

        if bias == "short":
            if not price_in_zone(high, zone) and not price_in_zone(close, zone):
                continue
            if abs((close - vwap) / vwap) > cfg.vwap_tolerance_pct and close > vwap:
                continue
            if close >= float(row["open"]):
                continue
            stop = high * (1.0 + cfg.stop_buffer_pct)
            risk = stop - close
            if risk <= 0:
                continue
            return Signal(
                symbol=symbol,
                side="sell",
                timestamp=timestamp,
                entry_price=close,
                stop_price=stop,
                target_price=close - risk * cfg.target_r_multiple,
                partial_price=close - risk * cfg.partial_exit_r_multiple,
                reason="VWAP pullback short",
                metadata={
                    "bias": bias,
                    "fib_zone": zone,
                    "vwap": vwap,
                    "day_type": metrics["day_type"],
                    "session_date": str(prepared.index[0].tz_convert("America/New_York").date()),
                    "opening_move_pct": metrics["opening_move_pct"],
                    "opening_range_pct": metrics["opening_range_pct"],
                    "vwap_slope_pct": metrics["vwap_slope_pct"],
                    "regime_strength_pct": metrics["regime_strength_pct"],
                },
            )
    return None


def simulate_trade_path(
    signal: Signal,
    future_bars: pd.DataFrame,
    qty: float,
    cfg: StrategyConfig,
) -> BacktestTrade:
    partial_qty = qty * 0.5
    remaining_qty = qty
    partial_taken = False
    trailing_active = False
    exit_price = signal.entry_price
    exit_time = signal.timestamp
    exit_reason = "end_of_data"
    partial_exit_price: float | None = None
    partial_realized_pnl = 0.0
    risk = abs(signal.entry_price - signal.stop_price)
    slippage_factor = cfg.slippage_bps / 10_000.0
    side_mult = 1 if signal.side == "buy" else -1
    mfe_r = 0.0
    mae_r = 0.0

    for timestamp, row in future_bars.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        vwap = float(row.get("vwap", close))
        if risk > 0:
            if signal.side == "buy":
                mfe_r = max(mfe_r, (high - signal.entry_price) / risk)
                mae_r = min(mae_r, (low - signal.entry_price) / risk)
            else:
                mfe_r = max(mfe_r, (signal.entry_price - low) / risk)
                mae_r = min(mae_r, (signal.entry_price - high) / risk)
        if signal.side == "buy":
            if low <= signal.stop_price:
                exit_price = signal.stop_price * (1.0 - slippage_factor)
                exit_time = timestamp
                exit_reason = "stop"
                break
            if high >= signal.target_price:
                exit_price = signal.target_price * (1.0 - slippage_factor)
                exit_time = timestamp
                exit_reason = "target"
                break
            if not partial_taken and high >= signal.partial_price:
                partial_taken = True
                partial_exit_price = signal.partial_price * (1.0 - slippage_factor)
                partial_realized_pnl = (partial_exit_price - signal.entry_price) * partial_qty * side_mult
                remaining_qty -= partial_qty
                trailing_active = cfg.trailing_exit_on_vwap_loss
            if trailing_active and close < vwap:
                exit_price = close * (1.0 - slippage_factor)
                exit_time = timestamp
                exit_reason = "vwap_loss"
                break
        else:
            if high >= signal.stop_price:
                exit_price = signal.stop_price * (1.0 + slippage_factor)
                exit_time = timestamp
                exit_reason = "stop"
                break
            if low <= signal.target_price:
                exit_price = signal.target_price * (1.0 + slippage_factor)
                exit_time = timestamp
                exit_reason = "target"
                break
            if not partial_taken and low <= signal.partial_price:
                partial_taken = True
                partial_exit_price = signal.partial_price * (1.0 + slippage_factor)
                partial_realized_pnl = (partial_exit_price - signal.entry_price) * partial_qty * side_mult
                remaining_qty -= partial_qty
                trailing_active = cfg.trailing_exit_on_vwap_loss
            if trailing_active and close > vwap:
                exit_price = close * (1.0 + slippage_factor)
                exit_time = timestamp
                exit_reason = "vwap_loss"
                break
        exit_price = close
        exit_time = timestamp
        exit_reason = "session_close"

    gross_pnl = partial_realized_pnl + ((exit_price - signal.entry_price) * remaining_qty * side_mult)
    fees = cfg.commission_per_order * (2 + int(partial_taken))
    pnl = gross_pnl - fees
    pnl_pct = pnl / (signal.entry_price * qty) if qty else 0.0
    r_multiple = pnl / (risk * qty) if risk > 0 and qty > 0 else 0.0
    holding_minutes = max(0.0, (exit_time - signal.timestamp).total_seconds() / 60.0)
    return BacktestTrade(
        symbol=signal.symbol,
        day_type=str(signal.metadata.get("day_type", "unknown")),
        bias=str(signal.metadata.get("bias", "neutral")),
        session_date=str(signal.metadata.get("session_date", signal.timestamp.date())),
        side=signal.side,
        entry_time=signal.timestamp,
        exit_time=exit_time,
        entry_price=signal.entry_price,
        exit_price=exit_price,
        qty=qty,
        pnl=pnl,
        gross_pnl=gross_pnl,
        fees=fees,
        pnl_pct=pnl_pct,
        exit_reason=exit_reason,
        r_multiple=r_multiple,
        risk_amount=risk * qty,
        capital_committed=signal.entry_price * qty,
        partial_exit_taken=partial_taken,
        partial_exit_price=partial_exit_price,
        partial_exit_qty=partial_qty if partial_taken else 0.0,
        partial_realized_pnl=partial_realized_pnl,
        holding_minutes=holding_minutes,
        mfe_r=mfe_r,
        mae_r=mae_r,
    )


def summarize_trades(
    trades: list[BacktestTrade],
    starting_cash: float,
    signals_considered: int = 0,
    skipped_max_positions: int = 0,
    skipped_no_cash: int = 0,
) -> dict[str, float]:
    if not trades:
        return {
            "trades": 0,
            "signals_considered": signals_considered,
            "signals_executed_pct": 0.0,
            "wins": 0,
            "losses": 0,
            "net_profit": 0.0,
            "return_pct": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "avg_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy_r": 0.0,
            "avg_r_multiple": 0.0,
            "median_r_multiple": 0.0,
            "avg_mfe_r": 0.0,
            "avg_mae_r": 0.0,
            "ending_cash": starting_cash,
            "exposure": 0.0,
            "avg_hold_minutes": 0.0,
            "median_hold_minutes": 0.0,
            "stop_exits": 0,
            "target_exits": 0,
            "vwap_loss_exits": 0,
            "session_close_exits": 0,
            "partial_exits": 0,
            "skipped_max_positions": skipped_max_positions,
            "skipped_no_cash": skipped_no_cash,
        }

    equity = starting_cash
    peak_equity = starting_cash
    worst_drawdown = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    exposure_minutes = 0.0
    hold_minutes = [trade.holding_minutes for trade in trades]
    r_values = [trade.r_multiple for trade in trades]
    mfe_values = [trade.mfe_r for trade in trades]
    mae_values = [trade.mae_r for trade in trades]
    stop_exits = 0
    target_exits = 0
    vwap_loss_exits = 0
    session_close_exits = 0
    partial_exits = 0
    realized_sequence = sorted(trades, key=lambda trade: (trade.exit_time, trade.entry_time))
    for trade in realized_sequence:
        equity += trade.pnl
        peak_equity = max(peak_equity, equity)
        worst_drawdown = max(worst_drawdown, peak_equity - equity)
        if trade.pnl > 0:
            wins += 1
            gross_profit += trade.pnl
        elif trade.pnl < 0:
            gross_loss += abs(trade.pnl)
        exposure_minutes += trade.holding_minutes
        if trade.exit_reason == "stop":
            stop_exits += 1
        elif trade.exit_reason == "target":
            target_exits += 1
        elif trade.exit_reason == "vwap_loss":
            vwap_loss_exits += 1
        elif trade.exit_reason == "session_close":
            session_close_exits += 1
        if trade.partial_exit_taken:
            partial_exits += 1
    losses = len(trades) - wins
    return {
        "trades": len(trades),
        "signals_considered": signals_considered,
        "signals_executed_pct": len(trades) / signals_considered if signals_considered else 0.0,
        "wins": wins,
        "losses": losses,
        "net_profit": equity - starting_cash,
        "return_pct": (equity - starting_cash) / starting_cash if starting_cash else 0.0,
        "win_rate": wins / len(trades) if trades else 0.0,
        "max_drawdown": worst_drawdown,
        "max_drawdown_pct": worst_drawdown / peak_equity if peak_equity else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else float("inf"),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "avg_pnl": sum(trade.pnl for trade in trades) / len(trades),
        "avg_win": gross_profit / wins if wins else 0.0,
        "avg_loss": -(gross_loss / losses) if losses else 0.0,
        "expectancy_r": sum(r_values) / len(r_values),
        "avg_r_multiple": sum(r_values) / len(r_values),
        "median_r_multiple": float(pd.Series(r_values).median()),
        "avg_mfe_r": sum(mfe_values) / len(mfe_values),
        "avg_mae_r": sum(mae_values) / len(mae_values),
        "ending_cash": equity,
        "exposure": exposure_minutes,
        "avg_hold_minutes": sum(hold_minutes) / len(hold_minutes),
        "median_hold_minutes": float(pd.Series(hold_minutes).median()),
        "stop_exits": stop_exits,
        "target_exits": target_exits,
        "vwap_loss_exits": vwap_loss_exits,
        "session_close_exits": session_close_exits,
        "partial_exits": partial_exits,
        "skipped_max_positions": skipped_max_positions,
        "skipped_no_cash": skipped_no_cash,
    }


def summarize_trades_by_symbol(trades: list[BacktestTrade]) -> list[dict[str, float | int | str]]:
    if not trades:
        return []
    rows: list[dict[str, float | int | str]] = []
    grouped = {}
    for trade in trades:
        grouped.setdefault(trade.symbol, []).append(trade)
    for symbol, symbol_trades in sorted(grouped.items()):
        wins = sum(1 for trade in symbol_trades if trade.pnl > 0)
        gross_profit = sum(trade.pnl for trade in symbol_trades if trade.pnl > 0)
        gross_loss = abs(sum(trade.pnl for trade in symbol_trades if trade.pnl < 0))
        rows.append(
            {
                "symbol": symbol,
                "trades": len(symbol_trades),
                "net_profit": sum(trade.pnl for trade in symbol_trades),
                "win_rate": wins / len(symbol_trades),
                "avg_pnl": sum(trade.pnl for trade in symbol_trades) / len(symbol_trades),
                "avg_r_multiple": sum(trade.r_multiple for trade in symbol_trades) / len(symbol_trades),
                "profit_factor": gross_profit / gross_loss if gross_loss else float("inf"),
                "partial_exits": sum(1 for trade in symbol_trades if trade.partial_exit_taken),
                "stop_exits": sum(1 for trade in symbol_trades if trade.exit_reason == "stop"),
                "target_exits": sum(1 for trade in symbol_trades if trade.exit_reason == "target"),
            }
        )
    rows.sort(key=lambda row: float(row["net_profit"]), reverse=True)
    return rows


def summarize_trades_by_day_type(trades: list[BacktestTrade]) -> list[dict[str, float | int | str]]:
    if not trades:
        return []
    grouped: dict[str, list[BacktestTrade]] = {}
    for trade in trades:
        grouped.setdefault(trade.day_type, []).append(trade)
    rows: list[dict[str, float | int | str]] = []
    for day_type, grouped_trades in sorted(grouped.items()):
        wins = sum(1 for trade in grouped_trades if trade.pnl > 0)
        gross_profit = sum(trade.pnl for trade in grouped_trades if trade.pnl > 0)
        gross_loss = abs(sum(trade.pnl for trade in grouped_trades if trade.pnl < 0))
        rows.append(
            {
                "day_type": day_type,
                "trades": len(grouped_trades),
                "net_profit": sum(trade.pnl for trade in grouped_trades),
                "win_rate": wins / len(grouped_trades),
                "avg_pnl": sum(trade.pnl for trade in grouped_trades) / len(grouped_trades),
                "avg_r_multiple": sum(trade.r_multiple for trade in grouped_trades) / len(grouped_trades),
                "profit_factor": gross_profit / gross_loss if gross_loss else float("inf"),
            }
        )
    rows.sort(key=lambda row: float(row["net_profit"]), reverse=True)
    return rows


def summarize_exit_reasons(trades: list[BacktestTrade]) -> list[dict[str, float | int | str]]:
    counts: dict[str, int] = {}
    pnl_by_reason: dict[str, float] = {}
    for trade in trades:
        counts[trade.exit_reason] = counts.get(trade.exit_reason, 0) + 1
        pnl_by_reason[trade.exit_reason] = pnl_by_reason.get(trade.exit_reason, 0.0) + trade.pnl
    rows = []
    for reason in sorted(counts):
        rows.append(
            {
                "exit_reason": reason,
                "trades": counts[reason],
                "net_profit": pnl_by_reason[reason],
            }
        )
    return rows
