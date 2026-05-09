"""GET/PUT /api/config/{property_uid} — read/write property config."""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from ..engine_proxy import get_property_config, save_property_config

router = APIRouter(prefix="/api", tags=["config"])


class ConfigPutRequest(BaseModel):
    """Validated property config for PUT."""

    base_price: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    strategy_weights: Optional[dict[str, float]] = None
    seasonal_months: Optional[dict[str, float]] = None
    dow_multipliers: Optional[dict[str, float]] = None
    demand_config: Optional[dict[str, Any]] = None
    availability: Optional[dict[str, Any]] = None
    local_events: Optional[list[dict[str, Any]]] = None
    seasonal_base_prices: Optional[dict[str, float]] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("min_price", "max_price")
    @classmethod
    def price_must_be_positive(cls, v):
        if v is not None and v < 0:
            raise ValueError("price must be non-negative")
        return v

    @field_validator("strategy_weights")
    @classmethod
    def weights_non_negative(cls, v):
        if v is None:
            return v
        for k, val in v.items():
            if val < 0:
                raise ValueError(f"weight for '{k}' must be non-negative")
        return v


@router.get("/config/{property_uid}")
async def get_config(property_uid: str):
    """Return the raw property config JSON."""
    config = get_property_config(property_uid)
    if not config:
        raise HTTPException(status_code=404, detail=f"No config found for {property_uid}")
    return config


@router.put("/config/{property_uid}")
async def put_config(property_uid: str, body: ConfigPutRequest):
    """Save an edited property config after validating."""
    # Load current config to preserve structure and server-side fields
    current = get_property_config(property_uid)
    if not current:
        raise HTTPException(status_code=404, detail=f"No config found for {property_uid}")

    # Merge only fields that were explicitly sent (exclude_unset)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if field == "strategy_weights" and value is not None:
            current[field] = _normalize_weights(value)
        else:
            current[field] = value

    save_property_config(property_uid, current)
    return current


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Normalize weights to sum to 1.0."""
    total = sum(weights.values())
    if total <= 0 or total == 1.0:
        return weights
    return {k: round(v / total, 3) for k, v in weights.items()}
