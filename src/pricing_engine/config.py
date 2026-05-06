"""Configuration loader — env vars + .env file support."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class PropertyOverride(BaseModel):
    """Per-property pricing parameters."""

    property_uid: str
    base_price: float = 100.0
    min_price: float = 50.0
    max_price: float = 2000.0
    quality_score: float = 0.85
    strategy_weights: dict[str, float] = Field(default_factory=dict)


class EngineConfig(BaseModel):
    """Top-level pricing engine configuration."""

    igms_client_id: str = Field(default="")
    igms_client_secret: str = Field(default="")
    igms_redirect_uri: str = Field(default="")
    igms_scope: str = Field(default="listings,pricing-management")
    igms_access_token: str = Field(default="")

    pricing_window_days: int = Field(default=90)
    schedule_interval_minutes: int = Field(default=60)

    default_base_price: float = Field(default=100.0)
    default_min_price: float = Field(default=50.0)
    default_max_price: float = Field(default=2000.0)
    default_quality_score: float = Field(default=0.85)

    # Default strategy weights
    default_strategy_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "demand": 0.40,
            "event": 0.30,
            "competitor": 0.20,
            "yield": 0.10,
        }
    )

    property_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    competitor_api_key: str = Field(default="")
    log_level: str = Field(default="INFO")

    @classmethod
    def from_env(cls, env_path: str | Path = ".env") -> "EngineConfig":
        """Load config from environment variables, optionally reading a .env file."""
        path = Path(env_path)
        if path.exists():
            _load_dotenv(path)

        return cls(
            igms_client_id=os.getenv("IGMS_CLIENT_ID", ""),
            igms_client_secret=os.getenv("IGMS_CLIENT_SECRET", ""),
            igms_redirect_uri=os.getenv("IGMS_REDIRECT_URI", ""),
            igms_scope=os.getenv("IGMS_SCOPE", "listings,pricing-management"),
            igms_access_token=os.getenv("IGMS_ACCESS_TOKEN", ""),
            pricing_window_days=int(
                os.getenv("PRICING_WINDOW_DAYS", "90")
            ),
            schedule_interval_minutes=int(
                os.getenv("SCHEDULE_INTERVAL_MINUTES", "60")
            ),
            default_base_price=float(
                os.getenv("DEFAULT_BASE_PRICE", "100.0")
            ),
            default_min_price=float(
                os.getenv("DEFAULT_MIN_PRICE", "50.0")
            ),
            default_max_price=float(
                os.getenv("DEFAULT_MAX_PRICE", "2000.0")
            ),
            default_quality_score=float(
                os.getenv("DEFAULT_QUALITY_SCORE", "0.85")
            ),
            competitor_api_key=os.getenv("COMPETITOR_API_KEY", ""),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


def _load_dotenv(path: Path) -> None:
    """Simple .env loader — sets vars only if not already in environ."""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)
