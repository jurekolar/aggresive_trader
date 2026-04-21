from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import product
from typing import Iterable


DEFAULT_EQUITY_SYMBOLS = [
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMD",
    "TSLA",
    "META",
    "AMZN",
    "GOOGL",
    "GLD",
    "SLV",
    "USO",
    "DBA",
]

DEFAULT_CRYPTO_SYMBOLS = [
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
]


@dataclass(frozen=True)
class StrategyConfig:
    opening_range_minutes: int = 15
    impulse_lookback_bars: int = 20
    min_opening_move_pct: float = 0.005
    min_relative_volume: float = 1.5
    fib_lower: float = 0.50
    fib_upper: float = 0.618
    vwap_tolerance_pct: float = 0.0025
    max_extension_from_vwap_pct: float = 0.01
    stop_buffer_pct: float = 0.001
    risk_per_trade_pct: float = 0.01
    daily_loss_limit_pct: float = 0.02
    target_r_multiple: float = 2.0
    partial_exit_r_multiple: float = 1.0
    trailing_exit_on_vwap_loss: bool = True
    max_positions: int = 2
    allow_short: bool = False
    slippage_bps: float = 5.0
    commission_per_order: float = 0.0


@dataclass(frozen=True)
class UniverseConfig:
    min_price: float = 5.0
    min_avg_dollar_volume: float = 20_000_000.0
    liquidity_lookback_days: int = 10
    equities: list[str] = field(default_factory=lambda: DEFAULT_EQUITY_SYMBOLS.copy())
    crypto: list[str] = field(default_factory=lambda: DEFAULT_CRYPTO_SYMBOLS.copy())


@dataclass(frozen=True)
class AppConfig:
    cash: float = 1_000.0
    poll_seconds: int = 20
    session_timezone: str = "America/New_York"
    results_dir: str = "results"
    bars_timeframe: str = "1Min"
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)


def optimization_grid(base: StrategyConfig) -> Iterable[StrategyConfig]:
    grid = {
        "opening_range_minutes": [10, 15, 20],
        "min_relative_volume": [1.2, 1.5, 2.0],
        "fib_lower": [0.45, 0.50],
        "fib_upper": [0.618, 0.65],
        "vwap_tolerance_pct": [0.002, 0.0035],
        "target_r_multiple": [1.5, 2.0, 2.5],
    }
    keys = list(grid.keys())
    for values in product(*(grid[key] for key in keys)):
        payload = dict(zip(keys, values))
        if payload["fib_lower"] >= payload["fib_upper"]:
            continue
        yield replace(base, **payload)
