# Instructions

## Overview

This project exposes one CLI entrypoint:

```bash
python main.py <command> [options]
```

Commands:

- `trade`: run the Alpaca trading loop
- `backtest`: run the strategy on historical Alpaca data

The app automatically loads a local `.env` file from the project root before parsing CLI arguments.

## Install

Install dependencies:

```bash
pip install -r requirements.txt
```

## `.env` Setup

Create your local env file:

```bash
cp .env.example .env
```

Then replace the placeholder values with your real Alpaca keys.

Recommended `.env`:

```bash
ALPACA_DEFAULT_MODE=paper

ALPACA_PAPER_API_KEY=your_paper_key
ALPACA_PAPER_SECRET_KEY=your_paper_secret

ALPACA_LIVE_API_KEY=your_live_key
ALPACA_LIVE_SECRET_KEY=your_live_secret
```

### Recommended switching model

Use both:

- keep both paper and live credentials in `.env`
- set `ALPACA_DEFAULT_MODE` to the environment you use most often
- override with `--mode paper` or `--mode live` when needed

That is better than editing `.env` each time. It keeps secrets stable and makes one-off runs explicit.

### Variable precedence

Resolution order is:

1. CLI `--mode` if provided
2. `.env` `ALPACA_DEFAULT_MODE`
3. fallback default: `paper`

Credential lookup for the chosen mode:

- `paper` uses `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_SECRET_KEY`
- `live` uses `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY`

If the mode-specific variables are missing, the app falls back to:

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`

If the same variable already exists in your shell, the shell value wins over `.env`.

### Supported env variables

Preferred:

- `ALPACA_DEFAULT_MODE`
- `ALPACA_PAPER_API_KEY`
- `ALPACA_PAPER_SECRET_KEY`
- `ALPACA_LIVE_API_KEY`
- `ALPACA_LIVE_SECRET_KEY`

Also accepted:

- `ALPACA_ENV`
- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`

Notes:

- `ALPACA_ENV` is treated as an alias for `ALPACA_DEFAULT_MODE`
- `.env` is git-ignored, so your local secrets should not be committed

## Commands

## `trade`

Usage:

```bash
python main.py trade [options]
```

Example:

```bash
python main.py trade --mode paper --asset-class equities --symbols SPY QQQ --cash 5000 --max-positions 2 --poll-seconds 30 --dry-run
```

Parameters:

- `--mode {paper,live}`
  - Optional
  - Overrides `.env` mode selection for this run

- `--asset-class {all,equities,crypto}`
  - Default: `all`
  - Scans equities, crypto, or both

- `--symbols SYMBOL [SYMBOL ...]`
  - Optional
  - Explicit symbol list
  - If omitted, the app uses the built-in universe from `config.py`

- `--cash FLOAT`
  - Default: `1000.0`
  - Capital base used for sizing and risk calculations

- `--max-positions INT`
  - Default: `2`
  - Maximum number of simultaneous open positions

- `--poll-seconds INT`
  - Default: `20`
  - Delay between loop iterations

- `--allow-short`
  - Optional flag
  - Enables short-side signals

- `--dry-run`
  - Optional flag
  - Builds signals without placing orders

Output:

```text
results/trade_signals.csv
```

Behavior notes:

- the app exits if the market is closed
- the app closes all positions when fewer than 5 minutes remain before close
- the app closes all positions if the daily loss limit is hit

## `backtest`

Usage:

```bash
python main.py backtest --start YYYY-MM-DD --end YYYY-MM-DD [options]
```

Example:

```bash
python main.py backtest --mode paper --start 2026-01-01 --end 2026-01-31 --asset-class equities --symbols SPY QQQ --cash 10000 --max-positions 2
```

Parameters:

- `--start DATE`
  - Required
  - Historical start date

- `--end DATE`
  - Required
  - Historical end date

- `--mode {paper,live}`
  - Optional
  - Uses the same mode resolution as `trade`

- `--asset-class {all,equities,crypto}`
  - Default: `all`
  - Backtest equities, crypto, or both

- `--symbols SYMBOL [SYMBOL ...]`
  - Optional
  - Explicit symbol list
  - If omitted, the app uses the built-in universe from `config.py`

- `--cash FLOAT`
  - Default: `1000.0`
  - Starting capital for sizing and performance summary

- `--max-positions INT`
  - Default: `2`
  - Passed into strategy configuration

- `--allow-short`
  - Optional flag
  - Enables short signals in the backtest

- `--optimize`
  - Optional flag
  - Runs the parameter sweep defined in `config.py`

Output without `--optimize`:

```text
results/backtest_trades.csv
results/backtest_summary.csv
```

Output with `--optimize`:

```text
results/optimization.csv
results/best_config.csv
```

## Default Symbol Universe

If `--symbols` is not provided, the app uses these built-in defaults from `config.py`.

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

## Strategy Defaults

These are not CLI flags today. They are hardcoded in `config.py`.

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

## Examples

Use your `.env` default mode:

```bash
python main.py trade --asset-class equities
```

Force paper mode:

```bash
python main.py trade --mode paper --asset-class equities
```

Force live mode:

```bash
python main.py trade --mode live --asset-class equities --symbols SPY NVDA
```

Crypto dry run:

```bash
python main.py trade --mode paper --asset-class crypto --symbols BTC/USD ETH/USD --dry-run
```

Backtest with explicit mode:

```bash
python main.py backtest --mode paper --start 2026-01-01 --end 2026-01-31 --asset-class equities --symbols SPY QQQ AAPL
```

Run optimization:

```bash
python main.py backtest --mode paper --start 2026-01-01 --end 2026-01-31 --asset-class equities --optimize
```
