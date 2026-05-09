"""POST /api/calendar/push — push computed prices to iGMS for a month."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..engine_proxy import compute_month, _get_pricing_client

router = APIRouter(prefix="/api", tags=["calendar"])


class PushRequest(BaseModel):
    property_uid: str
    year: int
    month: int


class PushResponse(BaseModel):
    success: bool
    pushed: int
    errors: list[str]


@router.post("/calendar/push", response_model=PushResponse)
async def push_prices(
    property_uid: str = Query(...),
    year: int = Query(...),
    month: int = Query(...),
):
    """
    Push computed prices for a full month to iGMS.

    Computes prices using the pricing engine (same as GET /calendar/{year}/{month}),
    then pushes each day's final_price to iGMS via bulk_update_prices.
    """
    from calendar import monthrange

    _, n_days = monthrange(year, month)

    # Compute the month
    days = compute_month(
        property_uid=property_uid,
        year=year,
        month=month,
    )

    # Build updates for bulk_update_prices
    updates = []
    for day in days:
        date_str = day["date"]
        final_price = day.get("final_price")
        if final_price is None or final_price <= 0:
            continue
        updates.append({
            "property_uid": property_uid,
            "date": date_str,
            "price": round(final_price, 2),
            "currency": "USD",
        })

    # Push to iGMS
    errors: list[str] = []
    pushed = 0
    if updates:
        try:
            client = _get_pricing_client()
            results = client.bulk_update_prices(updates)
            for res in results:
                # _APIResponse: success → {"data": {"request_uids": [...]}}
                # error → {"error": {"code": N, "message": "..."}}
                data = getattr(res, "_data", None) or {}
                if "error" in data:
                    err = data["error"]
                    errors.append(str(err.get("message", err)))
                else:
                    pushed += 1
        except Exception as exc:
            errors.append(str(exc))

    return PushResponse(
        success=len(errors) == 0,
        pushed=pushed,
        errors=errors,
    )