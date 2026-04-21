from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from config import AppConfig, UniverseConfig
from env_config import resolve_alpaca_credentials
from strategy import Signal

try:
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, OrderClass, OrderSide, OrderType, TimeInForce
    from alpaca.trading.requests import GetAssetsRequest, MarketOrderRequest, TakeProfitRequest, StopLossRequest
    ALPACA_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover - covered by runtime behavior
    ALPACA_IMPORT_ERROR = exc
    TradingClient = None
    StockHistoricalDataClient = None
    CryptoHistoricalDataClient = None
    TimeFrame = None
    TimeFrameUnit = None
    GetAssetsRequest = None
    StockBarsRequest = None
    CryptoBarsRequest = None
    MarketOrderRequest = None
    TakeProfitRequest = None
    StopLossRequest = None
    AssetClass = None
    OrderSide = None
    OrderType = None
    TimeInForce = None
    OrderClass = None


def ensure_alpaca() -> None:
    if TradingClient is None:
        detail = f" Original import error: {ALPACA_IMPORT_ERROR}" if ALPACA_IMPORT_ERROR else ""
        raise RuntimeError(
            "Alpaca dependencies are not available. Install them with "
            "`pip install -r requirements.txt`." + detail
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def one_minute_timeframe():
    ensure_alpaca()
    return TimeFrame(1, TimeFrameUnit.Minute)


class AlpacaIO:
    def __init__(self, mode: str = "paper", config: AppConfig | None = None) -> None:
        ensure_alpaca()
        self.config = config or AppConfig()
        normalized_mode = mode.strip().lower()
        key, secret = resolve_alpaca_credentials(normalized_mode)
        self.paper = normalized_mode == "paper"
        self.trading_client = TradingClient(key, secret, paper=self.paper)
        self.stock_data = StockHistoricalDataClient(key, secret)
        self.crypto_data = CryptoHistoricalDataClient(key, secret)

    def market_clock(self):
        return self.trading_client.get_clock()

    def market_calendar(self, start: str, end: str):
        return self.trading_client.get_calendar(start=start, end=end)

    def account(self):
        return self.trading_client.get_account()

    def get_active_assets(self, asset_class: str) -> list:
        if asset_class == "equities":
            params = GetAssetsRequest(asset_class=AssetClass.US_EQUITY)
        elif asset_class == "crypto":
            params = GetAssetsRequest(asset_class=AssetClass.CRYPTO)
        else:
            raise ValueError(f"Unsupported asset class: {asset_class}")
        return list(self.trading_client.get_all_assets(params))

    def select_universe(
        self,
        asset_class: str,
        symbols: Iterable[str] | None = None,
        universe_cfg: UniverseConfig | None = None,
    ) -> list[str]:
        cfg = universe_cfg or self.config.universe
        requested = list(symbols) if symbols else (cfg.equities if asset_class == "equities" else cfg.crypto)
        active_map = {asset.symbol: asset for asset in self.get_active_assets(asset_class)}
        selected: list[str] = []
        for symbol in requested:
            asset = active_map.get(symbol)
            if asset_class == "crypto" and asset is None:
                # Crypto asset metadata can differ from pair symbols, so allow curated pairs through.
                selected.append(symbol)
                continue
            if asset is None or not getattr(asset, "tradable", False) or getattr(asset, "status", "") != "active":
                continue
            selected.append(symbol)
        liquid: list[str] = []
        for symbol in selected:
            try:
                frame = self.fetch_bars(
                    symbol=symbol,
                    asset_class=asset_class,
                    start=(utc_now() - timedelta(days=cfg.liquidity_lookback_days + 5)).date().isoformat(),
                    end=utc_now().date().isoformat(),
                )
            except Exception:
                continue
            if frame.empty:
                continue
            daily = frame.resample("1D").agg({"close": "last", "volume": "sum"}).dropna()
            if daily.empty:
                continue
            avg_close = float(daily["close"].tail(cfg.liquidity_lookback_days).mean())
            avg_dollar_volume = float((daily["close"] * daily["volume"]).tail(cfg.liquidity_lookback_days).mean())
            if avg_close >= cfg.min_price and avg_dollar_volume >= cfg.min_avg_dollar_volume:
                liquid.append(symbol)
        return liquid

    def fetch_bars(self, symbol: str, asset_class: str, start: str, end: str) -> pd.DataFrame:
        if asset_class == "equities":
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=one_minute_timeframe(),
                start=pd.Timestamp(start, tz="UTC").to_pydatetime(),
                end=pd.Timestamp(end, tz="UTC").to_pydatetime(),
                adjustment="raw",
                feed="iex",
            )
            bars = self.stock_data.get_stock_bars(request).df
        elif asset_class == "crypto":
            request = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=one_minute_timeframe(),
                start=pd.Timestamp(start, tz="UTC").to_pydatetime(),
                end=pd.Timestamp(end, tz="UTC").to_pydatetime(),
            )
            bars = self.crypto_data.get_crypto_bars(request).df
        else:
            raise ValueError(f"Unsupported asset class: {asset_class}")

        if bars.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        if isinstance(bars.index, pd.MultiIndex):
            bars = bars.xs(symbol)
        bars.index = pd.to_datetime(bars.index, utc=True)
        return bars[["open", "high", "low", "close", "volume"]].sort_index()

    def latest_intraday_bars(self, symbol: str, asset_class: str, minutes: int = 120) -> pd.DataFrame:
        end = utc_now()
        start = end - timedelta(minutes=minutes + 5)
        return self.fetch_bars(symbol, asset_class, start.isoformat(), end.isoformat())

    def open_positions(self):
        return list(self.trading_client.get_all_positions())

    def close_all_positions(self, cancel_orders: bool = True) -> None:
        if cancel_orders:
            self.trading_client.cancel_orders()
        self.trading_client.close_all_positions(cancel_orders=cancel_orders)

    def place_signal_order(self, signal: Signal, qty: float, asset_class: str):
        side = OrderSide.BUY if signal.side == "buy" else OrderSide.SELL
        if asset_class == "equities":
            request = MarketOrderRequest(
                symbol=signal.symbol,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=round(signal.target_price, 2)),
                stop_loss=StopLossRequest(stop_price=round(signal.stop_price, 2)),
            )
        else:
            request = MarketOrderRequest(
                symbol=signal.symbol,
                qty=qty,
                side=side,
                type=OrderType.MARKET,
                time_in_force=TimeInForce.GTC,
            )
        return self.trading_client.submit_order(request)


def write_csv(path: str | Path, rows: list[dict]) -> None:
    if not rows:
        return
    frame = pd.DataFrame(rows)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)


def signal_to_row(signal: Signal, qty: float, asset_class: str) -> dict:
    data = asdict(signal)
    data["qty"] = qty
    data["asset_class"] = asset_class
    data["timestamp"] = signal.timestamp.isoformat()
    data["metadata"] = str(signal.metadata)
    return data
