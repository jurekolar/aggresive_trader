from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import product
from typing import Iterable


DEFAULT_EQUITY_SYMBOLS = [
    "AMD",
    "MSFT",
]

RESEARCH_EQUITY_SYMBOLS = [
    "META",
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
    min_opening_range_pct: float = 0.008
    min_relative_volume: float = 1.5
    fib_lower: float = 0.50
    fib_upper: float = 0.618
    vwap_tolerance_pct: float = 0.0015
    max_extension_from_vwap_pct: float = 0.01
    stop_buffer_pct: float = 0.001
    risk_per_trade_pct: float = 0.01
    daily_loss_limit_pct: float = 0.02
    target_r_multiple: float = 2.5
    partial_exit_r_multiple: float = 1.0
    trailing_exit_on_vwap_loss: bool = True
    min_vwap_slope_pct: float = 0.002
    min_opening_range_close_location: float = 0.65
    min_regime_strength_pct: float = 0.0075
    max_positions: int = 1
    allow_short: bool = False
    slippage_bps: float = 5.0
    commission_per_order: float = 0.0


@dataclass(frozen=True)
class UniverseConfig:
    min_price: float = 5.0
    min_avg_dollar_volume: float = 20_000_000.0
    liquidity_lookback_days: int = 10
    equities: list[str] = field(default_factory=lambda: DEFAULT_EQUITY_SYMBOLS.copy())
    research_equities: list[str] = field(default_factory=lambda: RESEARCH_EQUITY_SYMBOLS.copy())
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
        "stop_buffer_pct": [0.0010, 0.0015, 0.0020, 0.0030],
        "vwap_tolerance_pct": [0.0015, 0.0025, 0.0035, 0.0050],
        "target_r_multiple": [1.25, 1.5, 2.0, 2.5],
    }
    keys = list(grid.keys())
    for values in product(*(grid[key] for key in keys)):
        payload = dict(zip(keys, values))
        yield replace(base, **payload)
