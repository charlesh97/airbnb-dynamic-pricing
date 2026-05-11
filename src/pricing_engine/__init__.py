"""iGMS Dynamic Pricing Engine.

A black-box dynamic pricing engine for short-term rental properties managed via iGMS.
"""

from .config import EngineConfig
from .engine import DatePrice, PricingEngine, PropertyConfig
try:
    from .client import PricingClient
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency
    PricingClient = None  # type: ignore[assignment]
try:
    from .push_pipeline import (
        PushPipelineRequest,
        PushPipelineResult,
        run_push_pipeline,
    )
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency
    PushPipelineRequest = None  # type: ignore[assignment]
    PushPipelineResult = None   # type: ignore[assignment]
    run_push_pipeline = None    # type: ignore[assignment]
from .strategies import (
    PriceRecommendation,
    PricingStrategy,
    DemandStrategy,
    EventStrategy,
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
    "CompetitorStrategy",
    "PushPipelineRequest",
    "PushPipelineResult",
    "run_push_pipeline",
]
