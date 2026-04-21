from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env", override: bool = False) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value


def resolve_mode(cli_mode: str | None = None) -> str:
    if cli_mode:
        return cli_mode

    env_mode = os.getenv("ALPACA_DEFAULT_MODE") or os.getenv("ALPACA_ENV") or "paper"
    env_mode = env_mode.strip().lower()
    if env_mode not in {"paper", "live"}:
        raise RuntimeError("ALPACA_DEFAULT_MODE must be 'paper' or 'live'.")
    return env_mode


def resolve_alpaca_credentials(mode: str) -> tuple[str, str]:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"paper", "live"}:
        raise RuntimeError("Alpaca mode must be 'paper' or 'live'.")

    if normalized_mode == "paper":
        key = os.getenv("ALPACA_PAPER_API_KEY")
        secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    else:
        key = os.getenv("ALPACA_LIVE_API_KEY")
        secret = os.getenv("ALPACA_LIVE_SECRET_KEY")

    # Backward-compatible fallback for older single-pair setups.
    if not key:
        key = os.getenv("ALPACA_API_KEY")
    if not secret:
        secret = os.getenv("ALPACA_SECRET_KEY")

    if not key or not secret:
        prefix = normalized_mode.upper()
        raise RuntimeError(
            f"Missing Alpaca credentials for {normalized_mode} mode. "
            f"Set ALPACA_{prefix}_API_KEY and ALPACA_{prefix}_SECRET_KEY "
            "in .env or the shell."
        )
    return key, secret
