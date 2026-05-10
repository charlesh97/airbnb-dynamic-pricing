"""IGMS client extensions for pricing — uses set_calendar_batch (calendar-control scope)."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from igms_wrapper.client import APIResponse as _APIResponse
from igms_wrapper.client import IGMSClient


_DictStrAnyList = Dict[str, List[Dict[str, Any]]]
_DictStrAny = Dict[str, Any]
_ListDictStrAny = List[Dict[str, Any]]
_APIResponseList = List[_APIResponse]


class PricingClient(IGMSClient):
    """Extended IGMS client with pricing management capabilities.

    Write endpoints (confirmed working with calendar-control scope):
        POST /api/v1/set-calendar-batch  — set prices + min_stay for dates
        POST /api/v2/set-property-calendar-availability  — set date ranges available/unavailable
    """

    def set_calendar_batch(
        self,
        property_uid: str,
        days: _ListDictStrAny,
    ) -> _APIResponse:
        """Set calendar prices/min-stay for a batch of dates.

        Each day dict: {date: "YYYY-MM-DD", price?: float, currency?: str, min_stay?: int}
        """
        payload = {
            "property_uid": property_uid,
            "days": days,
        }
        return self.request(
            "/api/v1/set-calendar-batch",
            method="POST",
            json_body=payload,
        )

    def set_property_availability(
        self,
        property_uid: str,
        start_date: str,
        end_date: str,
        is_available: bool = False,
    ) -> _APIResponse:
        """Mark a date range as available or unavailable."""
        payload = {
            "property_uid": property_uid,
            "start_date": start_date,
            "end_date": end_date,
            "is_available": is_available,
        }
        return self.request(
            "/api/v2/set-property-calendar-availability",
            method="POST",
            json_body=payload,
        )

    def update_calendar_price(
        self,
        listing_uid: str,
        property_uid: str,
        date: str,
        price: float,
        currency: str = "USD",
        min_stay: int | None = None,
    ) -> _APIResponse:
        """Update the nightly price for a specific date on a listing."""
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
        updates: _ListDictStrAny,
    ) -> _APIResponseList:
        """Bulk update prices for multiple dates.

        Each entry in updates should have:
            property_uid, date, price, currency?, min_stay?

        Groups by property_uid and calls set_calendar_batch once per property.
        """
        by_prop: _DictStrAnyList = {}
        for u in updates:
            pid = u.get("property_uid", "")
            if pid:
                by_prop.setdefault(pid, []).append(u)

        results: _APIResponseList = []
        for pid, items in by_prop.items():
            days = [
                {
                    "date": item["date"],
                    "price": item["price"],
                    "currency": item.get("currency", "USD"),
                    "min_stay": item.get("min_stay"),
                }
                for item in items
            ]
            result = self.set_calendar_batch(property_uid=pid, days=days)
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
        return self.set_calendar_batch(
            property_uid=property_uid,
            days=[{"date": date, "min_stay": min_stay, "currency": "USD"}],
        )

    # Compatibility wrappers: some runtime environments ship older/missing
    # igms_wrapper methods, so we expose these here unconditionally.
    def get_calendar(self, property_uid: str, from_date: str, to_date: str) -> Any:
        params = {
            "property_uid": property_uid,
            "from_date": from_date,
            "to_date": to_date,
        }
        return self.request("/api/v1/get-calendar-data", params=params).payload

    def get_bookings(self, page: int = 1, **filters: Any) -> Any:
        params = {"page": page, **filters}
        return self.request("/api/v1/bookings", params=params).payload
