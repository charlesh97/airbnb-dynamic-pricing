"""iGMS Dynamic Pricing Engine.

A black-box dynamic pricing engine for short-term rental properties managed via iGMS.
"""

from .config import EngineConfig
from .engine import DatePrice, PricingEngine, PropertyConfig
try:
    from .client import PricingClient
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency
    PricingClient = None  # type: ignore[assignment]
from .strategies import (
    PriceRecommendation,
    PricingStrategy,
    DemandStrategy,
    EventStrategy,
    YieldStrategy,
    CompetitorStrategy,
)

__all__ = [
    "EngineConfig",
    "PricingEngine",
    "PropertyConfig",
    "DatePrice",
    "PricingClient",
    "PriceRecommendation",
    "PricingStrategy",
    "DemandStrategy",
    "EventStrategy",
    "YieldStrategy",
    "CompetitorStrategy",
]
