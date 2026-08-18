"""Central configuration. All secrets and tunables come from environment variables.

Nothing here is hardcoded, so the same code runs locally (via a .env file) and on
GitHub Actions (via repository secrets). Missing required values fail fast with a
clear message instead of blowing up deep inside an API call.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional; env vars can be set directly.
    def load_dotenv(*_args, **_kwargs) -> bool:  # type: ignore[misc]
        return False

# Load a local .env if present. On CI there is no .env; the vars come from the
# environment directly, so this is a no-op there.
load_dotenv()


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


def _optional(name: str, default: str) -> str:
    """Env var value, treating an empty/whitespace value as unset.

    CI passes unset repository variables through as empty strings (e.g.
    `TARGET_PORTIONS: ${{ vars.TARGET_PORTIONS }}`), so falling back on emptiness — not
    just absence — keeps those workflows working.
    """
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value or not value.strip():
        raise ConfigError(
            f"Missing required environment variable: {name}. "
            f"Set it in your .env file (local) or repository secrets (CI)."
        )
    return value.strip()


def _split_recipients(raw: str) -> List[str]:
    # Accept comma- or semicolon-separated lists.
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    return [p for p in parts if p]


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings. Build with `Settings.load()`."""

    # API keys
    google_api_key: str
    deepl_api_key: str
    usda_fdc_api_key: str

    # Email
    gmail_app_password: str
    sender_email: str
    recipients: List[str]

    # Scraping / behavior
    crisp_box: str = "vegan"
    target_lang: str = "EN-GB"
    # How many portions the output should be written for. crisp.nl publishes amounts
    # for 2; the scraper asks the site for this many and falls back to scaling.
    target_portions: int = 4

    # Email SMTP (Gmail defaults; rarely changed)
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            google_api_key=_require("GOOGLE_API_KEY"),
            deepl_api_key=_require("DEEPL_API_KEY"),
            usda_fdc_api_key=_require("USDA_FDC_API_KEY"),
            gmail_app_password=_require("GMAIL_APP_PASSWORD"),
            sender_email=_require("SENDER_EMAIL"),
            recipients=_split_recipients(_require("RECIPIENTS")),
            crisp_box=_optional("CRISP_BOX", "vegan"),
            target_lang=_optional("TARGET_LANG", "EN-GB"),
            target_portions=int(_optional("TARGET_PORTIONS", "4")),
        )
