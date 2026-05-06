"""IGMS client extensions for pricing — uses set_calendar_batch (calendar-control scope)."""

from __future__ import annotations

from typing import Any

# Re-use config from igms-api-wrapper if available
try:
    from igms_wrapper.client import IGMSClient
    from igms_wrapper.client import APIResponse as _APIResponse
except ImportError:

    class IGMSClient:  # type: ignore[no-redef]
        pass

    class _APIResponse:  # type: ignore[no-redef]
        pass


class PricingClient(IGMSClient):
    """Extended IGMS client with pricing management capabilities.

    Write endpoint (confirmed working with calendar-control scope):
        POST /api/v1/set-calendar-batch

    This calls the parent IGMSClient.set_calendar_batch() which writes prices
    directly without requiring the pricing-management scope.
    """

    def update_calendar_price(
        self,
        listing_uid: str,
        property_uid: str,
        date: str,
        price: float,
        currency: str = "USD",
        min_stay: int | None = None,
    ) -> _APIResponse:
        """Update the nightly price for a specific date on a listing.

        Uses set_calendar_batch (calendar-control scope) — no pricing-management
        scope required.

        Args:
            listing_uid:   The listing UID (e.g. "645841896772032198_airbnb_209713065").
            property_uid:  The parent property UID (e.g. "6925833560458409984").
            date:          Date string "YYYY-MM-DD".
            price:         New nightly price in USD.
            currency:      Currency code (default USD).
            min_stay:      Optional minimum stay requirement.

        Returns:
            APIResponse. On success: ``{"data": {"request_uids": [n]}}``.
            On auth error: ``{"error": {"code": 13, "message": "Property merged"}}``.
        """
        return self.set_calendar_batch(
            property_uid=property_uid,
            days=[{
                "date": date,
                "price": price,
                "currency": currency,
                "min_stay": min_stay,
            }],
        )

    def bulk_update_prices(
        self,
        updates: list[dict[str, Any]],
    ) -> list[_APIResponse]:
        """Bulk update prices for multiple dates.

        Each entry in updates should have:
            listing_uid, property_uid, date, price, currency?, min_stay?

        Groups by listing_uid and calls set_calendar_batch once per listing.
        """
        by_listing: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for u in updates:
            lst = u.get("listing_uid", "")
            pid = u.get("property_uid", "")
            if lst:
                by_listing.setdefault((lst, pid), []).append(u)

        results: list[_APIResponse] = []
        for (listing_uid, property_uid), items in by_listing.items():
            del listing_uid  # unused; property_uid drives the API call
            days = [
                {
                    "date": item["date"],
                    "price": item["price"],
                    "currency": item.get("currency", "USD"),
                    "min_stay": item.get("min_stay"),
                }
                for item in items
            ]
            result = self.set_calendar_batch(property_uid=property_uid, days=days)
            results.append(result)
        return results

    def set_pricing_minimum_stay(
        self,
        listing_uid: str,
        property_uid: str,
        date: str,
        min_stay: int,
    ) -> _APIResponse:
        """Set minimum stay for a specific date on a listing."""
        del listing_uid  # unused; property_uid drives the API call
        return self.set_calendar_batch(
            property_uid=property_uid,
            days=[{"date": date, "min_stay": min_stay, "currency": "USD"}],
        )