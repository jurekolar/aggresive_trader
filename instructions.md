# Instructions

## Overview

This project exposes a single CLI entrypoint:

```bash
python main.py <command> [options]
```

Available commands:

- `trade`: runs the trading loop against Alpaca in `paper` or `live` mode.
- `backtest`: runs a historical backtest using Alpaca market data.

The project reads Alpaca credentials from environment variables:

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`

There is no built-in `.env` loader in the codebase. If you want to use a `.env` file, you must load it yourself in your shell before running the commands.

## Install

Install dependencies first:

```bash
pip install -r requirements.txt
```

If needed, the minimum runtime dependencies mentioned in the code are:

```bash
pip install alpaca-py pandas
```

## Alpaca Keys

### How the app uses credentials

The code always reads:

```bash
ALPACA_API_KEY
ALPACA_SECRET_KEY
```

The `--mode` flag on `trade` decides whether Alpaca trading is paper or live:

- `--mode paper`: uses the paper trading environment.
- `--mode live`: uses the live trading environment.

Important: the variable names do not change between paper and live. You switch environments by exporting different values before running the command.

### Paper trading keys

In your terminal:

```bash
export ALPACA_API_KEY="your_paper_key"
export ALPACA_SECRET_KEY="your_paper_secret"
python main.py trade --mode paper
```

### Live trading keys

In your terminal:

```bash
export ALPACA_API_KEY="your_live_key"
export ALPACA_SECRET_KEY="your_live_secret"
python main.py trade --mode live
```

### Backtest keys

Backtests also instantiate the Alpaca clients, so the same variables must be set even though no live order is sent:

```bash
export ALPACA_API_KEY="your_paper_or_live_key"
export ALPACA_SECRET_KEY="your_paper_or_live_secret"
python main.py backtest --start 2026-01-01 --end 2026-01-31
```

### Recommended shell setup

Because the app only reads environment variables, the cleanest setup is to keep separate shell snippets for paper and live.

Example `paper` setup:

```bash
export ALPACA_API_KEY="your_paper_key"
export ALPACA_SECRET_KEY="your_paper_secret"
```

Example `live` setup:

```bash
export ALPACA_API_KEY="your_live_key"
export ALPACA_SECRET_KEY="your_live_secret"
```

Then paste the correct pair into the terminal before running the command you want.

If you want persistent shell config, add the exports to your `~/.zshrc`, but do not keep both paper and live assigned to the same variable names at the same time. Override them intentionally before each run.

## Command Reference

## `trade`

Runs the live trading loop until the market is near close or the daily loss limit is hit.

Basic usage:

```bash
python main.py trade [options]
```

Example:

```bash
python main.py trade --mode paper --asset-class equities --symbols SPY QQQ --cash 5000 --max-positions 2 --poll-seconds 30 --dry-run
```

### Parameters

- `--mode {paper,live}`
  - Default: `paper`
  - Selects whether the trading client connects to Alpaca paper trading or live trading.

- `--asset-class {all,equities,crypto}`
  - Default: `all`
  - Controls which market universe to scan.
  - `all` means both equities and crypto.

- `--symbols SYMBOL [SYMBOL ...]`
  - Default: not set
  - Optional explicit symbol list.
  - If omitted, the app uses the built-in curated universe from `config.py`.
  - Examples:
    - equities: `SPY AAPL NVDA`
    - crypto: `BTC/USD ETH/USD`

- `--cash FLOAT`
  - Default: `1000.0`
  - Sets the capital base used for position sizing and risk calculations.

- `--max-positions INT`
  - Default: `2`
  - Maximum number of open positions allowed at once.

- `--poll-seconds INT`
  - Default: `20`
  - Delay between trading loop checks.

- `--allow-short`
  - Default: off
  - Enables short-side signals. Without this flag, the strategy only takes long setups.

- `--dry-run`
  - Default: off
  - Generates signals but does not place orders.
  - Useful for verifying the strategy flow safely.

### Trade output

When signals are generated, the app writes:

```text
results/trade_signals.csv
```

### Notes

- The trading loop checks Alpaca market hours and stops if the market is closed.
- When fewer than 5 minutes remain before close, it closes all positions and exits.
- If the configured daily loss limit is hit, it closes all positions and exits.

## `backtest`

Runs the strategy on historical Alpaca data and writes CSV outputs.

Basic usage:

```bash
python main.py backtest --start YYYY-MM-DD --end YYYY-MM-DD [options]
```

Example:

```bash
python main.py backtest --start 2026-01-01 --end 2026-01-31 --asset-class equities --symbols SPY QQQ --cash 10000 --max-positions 2
```

### Parameters

- `--start DATE`
  - Required.
  - Start date for historical data.
  - Example: `2026-01-01`

- `--end DATE`
  - Required.
  - End date for historical data.
  - Example: `2026-01-31`

- `--asset-class {all,equities,crypto}`
  - Default: `all`
  - Chooses whether to backtest equities, crypto, or both.

- `--symbols SYMBOL [SYMBOL ...]`
  - Default: not set
  - Optional explicit list of symbols to backtest.
  - If omitted, the app uses the default universe from `config.py`.

- `--cash FLOAT`
  - Default: `1000.0`
  - Starting capital used for position sizing and summary metrics.

- `--max-positions INT`
  - Default: `2`
  - Passed into strategy config for position limit behavior.

- `--allow-short`
  - Default: off
  - Enables short signals in the backtest.

- `--optimize`
  - Default: off
  - Runs a parameter sweep across a predefined optimization grid in `config.py`.
  - Writes optimization results instead of the normal trade list output.

### Backtest output

Without `--optimize`, the app writes:

```text
results/backtest_trades.csv
results/backtest_summary.csv
```

With `--optimize`, the app writes:

```text
results/optimization.csv
results/best_config.csv
```

## Default Universe

If you do not pass `--symbols`, the app uses these built-in defaults from `config.py`.

Equities:

- `SPY`
- `QQQ`
- `IWM`
- `DIA`
- `AAPL`
- `MSFT`
- `NVDA`
- `AMD`
- `TSLA`
- `META`
- `AMZN`
- `GOOGL`
- `GLD`
- `SLV`
- `USO`
- `DBA`

Crypto:

- `BTC/USD`
- `ETH/USD`
- `SOL/USD`

## Strategy Config Defaults

These are not CLI parameters today, but they affect trading and backtesting behavior because they are hardcoded in `config.py`.

- `opening_range_minutes = 15`
- `impulse_lookback_bars = 20`
- `min_opening_move_pct = 0.005`
- `min_relative_volume = 1.5`
- `fib_lower = 0.50`
- `fib_upper = 0.618`
- `vwap_tolerance_pct = 0.0025`
- `max_extension_from_vwap_pct = 0.01`
- `stop_buffer_pct = 0.001`
- `risk_per_trade_pct = 0.01`
- `daily_loss_limit_pct = 0.02`
- `target_r_multiple = 2.0`
- `partial_exit_r_multiple = 1.0`
- `trailing_exit_on_vwap_loss = true`
- `max_positions = 2`
- `allow_short = false`
- `slippage_bps = 5.0`
- `commission_per_order = 0.0`

## Common Examples

Paper trading, equities only:

```bash
export ALPACA_API_KEY="your_paper_key"
export ALPACA_SECRET_KEY="your_paper_secret"
python main.py trade --mode paper --asset-class equities
```

Paper trading, custom crypto symbols, no live orders:

```bash
export ALPACA_API_KEY="your_paper_key"
export ALPACA_SECRET_KEY="your_paper_secret"
python main.py trade --mode paper --asset-class crypto --symbols BTC/USD ETH/USD --dry-run
```

Live trading with explicit symbols:

```bash
export ALPACA_API_KEY="your_live_key"
export ALPACA_SECRET_KEY="your_live_secret"
python main.py trade --mode live --asset-class equities --symbols SPY NVDA
```

Backtest a custom equity set:

```bash
export ALPACA_API_KEY="your_paper_key"
export ALPACA_SECRET_KEY="your_paper_secret"
python main.py backtest --start 2026-01-01 --end 2026-01-31 --asset-class equities --symbols SPY QQQ AAPL
```

Run optimization:

```bash
export ALPACA_API_KEY="your_paper_key"
export ALPACA_SECRET_KEY="your_paper_secret"
python main.py backtest --start 2026-01-01 --end 2026-01-31 --asset-class equities --optimize
```

## Important Limitation

The current codebase does not support separate variable names like `ALPACA_PAPER_API_KEY` and `ALPACA_LIVE_API_KEY`.

It only supports:

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`

So for paper vs live, you must replace the exported values in your shell before running the command.
