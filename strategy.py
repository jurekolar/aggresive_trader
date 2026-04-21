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
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    r_multiple: float


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


def session_bias(frame: pd.DataFrame, cfg: StrategyConfig) -> str:
    early = opening_range(frame, cfg.opening_range_minutes)
    if len(early) < max(3, cfg.opening_range_minutes // 2):
        return "neutral"
    start_price = float(early["open"].iloc[0])
    end_price = float(early["close"].iloc[-1])
    vwap_slope = float(early["vwap"].iloc[-1] - early["vwap"].iloc[0])
    impulse_pct = (end_price - start_price) / start_price
    if impulse_pct >= cfg.min_opening_move_pct and vwap_slope > 0:
        return "long"
    if cfg.allow_short and impulse_pct <= -cfg.min_opening_move_pct and vwap_slope < 0:
        return "short"
    return "neutral"


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
    bias = session_bias(prepared, cfg)
    if bias == "neutral":
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
                metadata={"bias": bias, "fib_zone": zone, "vwap": vwap},
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
                metadata={"bias": bias, "fib_zone": zone, "vwap": vwap},
            )
    return None


def simulate_trade_path(
    signal: Signal,
    future_bars: pd.DataFrame,
    qty: float,
    cfg: StrategyConfig,
) -> BacktestTrade:
    partial_taken = False
    trailing_active = False
    exit_price = signal.entry_price
    exit_time = signal.timestamp
    exit_reason = "end_of_data"
    risk = abs(signal.entry_price - signal.stop_price)
    slippage_factor = cfg.slippage_bps / 10_000.0
    side_mult = 1 if signal.side == "buy" else -1

    for timestamp, row in future_bars.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        vwap = float(row.get("vwap", close))
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
                trailing_active = cfg.trailing_exit_on_vwap_loss
            if trailing_active and close > vwap:
                exit_price = close * (1.0 + slippage_factor)
                exit_time = timestamp
                exit_reason = "vwap_loss"
                break
        exit_price = close
        exit_time = timestamp
        exit_reason = "session_close"

    gross_pnl = (exit_price - signal.entry_price) * qty * side_mult
    fees = cfg.commission_per_order * 2
    pnl = gross_pnl - fees
    pnl_pct = pnl / (signal.entry_price * qty) if qty else 0.0
    r_multiple = pnl / (risk * qty) if risk > 0 and qty > 0 else 0.0
    return BacktestTrade(
        symbol=signal.symbol,
        side=signal.side,
        entry_time=signal.timestamp,
        exit_time=exit_time,
        entry_price=signal.entry_price,
        exit_price=exit_price,
        qty=qty,
        pnl=pnl,
        pnl_pct=pnl_pct,
        exit_reason=exit_reason,
        r_multiple=r_multiple,
    )


def summarize_trades(trades: list[BacktestTrade], starting_cash: float) -> dict[str, float]:
    if not trades:
        return {
            "trades": 0,
            "net_profit": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": 0.0,
            "ending_cash": starting_cash,
            "exposure": 0.0,
        }

    equity = starting_cash
    peak = starting_cash
    worst_drawdown = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    exposure_minutes = 0.0
    for trade in trades:
        equity += trade.pnl
        peak = max(peak, equity)
        worst_drawdown = max(worst_drawdown, peak - equity)
        if trade.pnl > 0:
            wins += 1
            gross_profit += trade.pnl
        elif trade.pnl < 0:
            gross_loss += abs(trade.pnl)
        exposure_minutes += max(0.0, (trade.exit_time - trade.entry_time).total_seconds() / 60.0)
    max_drawdown = max(0.0, peak - min(starting_cash + sum(t.pnl for t in trades[: i + 1]) for i in range(len(trades))))
    return {
        "trades": float(len(trades)),
        "net_profit": equity - starting_cash,
        "win_rate": wins / len(trades),
        "max_drawdown": worst_drawdown,
        "profit_factor": gross_profit / gross_loss if gross_loss else float("inf"),
        "ending_cash": equity,
        "exposure": exposure_minutes,
    }
