from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from env_config import load_dotenv, resolve_alpaca_credentials, resolve_mode


class EnvConfigTests(unittest.TestCase):
    def test_load_dotenv_reads_key_value_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "ALPACA_DEFAULT_MODE=live",
                        'ALPACA_PAPER_API_KEY="paper-key"',
                        "export ALPACA_PAPER_SECRET_KEY=paper-secret",
                    ]
                )
            )
            with patch.dict(os.environ, {}, clear=True):
                load_dotenv(env_path)
                self.assertEqual(os.environ["ALPACA_DEFAULT_MODE"], "live")
                self.assertEqual(os.environ["ALPACA_PAPER_API_KEY"], "paper-key")
                self.assertEqual(os.environ["ALPACA_PAPER_SECRET_KEY"], "paper-secret")

    def test_cli_mode_overrides_env_default(self) -> None:
        with patch.dict(os.environ, {"ALPACA_DEFAULT_MODE": "live"}, clear=True):
            self.assertEqual(resolve_mode("paper"), "paper")

    def test_env_default_mode_is_used_when_cli_mode_missing(self) -> None:
        with patch.dict(os.environ, {"ALPACA_DEFAULT_MODE": "live"}, clear=True):
            self.assertEqual(resolve_mode(None), "live")

    def test_mode_specific_credentials_are_selected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ALPACA_PAPER_API_KEY": "paper-key",
                "ALPACA_PAPER_SECRET_KEY": "paper-secret",
                "ALPACA_LIVE_API_KEY": "live-key",
                "ALPACA_LIVE_SECRET_KEY": "live-secret",
            },
            clear=True,
        ):
            self.assertEqual(resolve_alpaca_credentials("paper"), ("paper-key", "paper-secret"))
            self.assertEqual(resolve_alpaca_credentials("live"), ("live-key", "live-secret"))

    def test_generic_credentials_remain_supported(self) -> None:
        with patch.dict(
            os.environ,
            {"ALPACA_API_KEY": "shared-key", "ALPACA_SECRET_KEY": "shared-secret"},
            clear=True,
        ):
            self.assertEqual(resolve_alpaca_credentials("paper"), ("shared-key", "shared-secret"))


if __name__ == "__main__":
    unittest.main()
