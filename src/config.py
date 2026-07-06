"""Load and validate Odoo connection settings from the environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load variables from a .env file in the current working directory (if present).
# Real OS environment variables take precedence over .env values.
load_dotenv()

_REQUIRED_VARS = ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY")


class ConfigError(RuntimeError):
    """Raised when required Odoo configuration is missing."""


@dataclass(frozen=True)
class OdooConfig:
    url: str
    db: str
    username: str
    api_key: str


def load_config() -> OdooConfig:
    """Read Odoo settings from the environment, raising a clear error if any are missing."""
    values = {var: os.environ.get(var, "").strip() for var in _REQUIRED_VARS}
    missing = [var for var, value in values.items() if not value]
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in your Odoo credentials."
        )

    return OdooConfig(
        url=values["ODOO_URL"].rstrip("/"),
        db=values["ODOO_DB"],
        username=values["ODOO_USERNAME"],
        api_key=values["ODOO_API_KEY"],
    )
