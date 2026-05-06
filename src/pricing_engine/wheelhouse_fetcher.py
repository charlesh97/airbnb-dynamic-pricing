"""Wheelhouse Lite API client for market rate data."""

from __future__ import annotations

import os
import requests
from datetime import datetime, timedelta
from typing import Any


DEFAULT_WH_BASE = "https://app.usewheelhouse.com/api/v2/"


class WheelhouseFetcher:
    """Fetches market rate recommendations from Wheelhouse Lite API."""

    def __init__(self, api_key: str | None = None, base_url: str = DEFAULT_WH_BASE):
        self.api_key = api_key or os.getenv("WHEELHOUSE_API_KEY", "")
        self.base_url = base_url
        self.session = requests.Session()
        self.session.params["token"] = self.api_key

    def check_coverage(
        self,
        latitude: float,
        longitude: float,
        country: str = "US",
        postal_code: str = "",
    ) -> dict[str, Any]:
        """Call GET /in_market — returns {in_market: bool, market_name: str}."""
        params = {"latitude": latitude, "longitude": longitude, "country": country}
        if postal_code:
            params["postal_code"] = postal_code
        resp = self.session.get(f"{self.base_url}in_market", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def fetch_recommendations(
        self,
        latitude: float,
        longitude: float,
        bedrooms: int,
        baths: float,
        sleeps: int,
        *,
        room_type: str = "house",
        country_code: str = "US",
        cleaning_fee: float | None = None,
        security_deposit: float | None = None,
        guests_included: int | None = None,
        amenities: list[str] | None = None,
        min_price: float | None = None,
        avg_booking_price: float | None = None,
        booking_price_certainty: float = 0.5,
        no_temporality: bool = False,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        window_days: int = 90,
    ) -> list[dict[str, Any]]:
        """Fetch price recommendations for a date range."""
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "bedrooms": bedrooms,
            "baths": baths,
            "sleeps": sleeps,
            "room_type": room_type,
            "country_code": country_code,
            "booking_price_certainty": booking_price_certainty,
            "no_temporality": str(no_temporality).lower(),
        }
        if cleaning_fee is not None:
            params["cleaning_fee"] = cleaning_fee
        if security_deposit is not None:
            params["security_deposit"] = security_deposit
        if guests_included is not None:
            params["guests_included"] = guests_included
        if amenities:
            params["amenities"] = ",".join(amenities)
        if min_price is not None:
            params["min_price"] = min_price
        if avg_booking_price is not None:
            params["avg_booking_price"] = avg_booking_price

        resp = self.session.get(f"{self.base_url}recommendations", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        recommendations = data.get("daily_recommendation", [])
        if not recommendations:
            return []

        # Filter by date range if specified
        start = from_date or datetime.now()
        end = to_date or (start + timedelta(days=window_days))

        filtered = []
        for rec in recommendations:
            date_str = rec.get("date") or rec.get("date_iso", "")
            if not date_str:
                filtered.append(rec)
                continue
            # Normalize to YYYY-MM-DD
            date_normalized = date_str[:10] if "T" in date_str else date_str
            try:
                rec_date = datetime.strptime(date_normalized, "%Y-%m-%d")
                if start <= rec_date <= end:
                    filtered.append(rec)
            except ValueError:
                filtered.append(rec)  # Include if we can't parse

        return filtered

    def build_market_rates(
        self,
        recommendations: list[dict[str, Any]],
    ) -> dict[str, float]:
        """Convert Wheelhouse recommendations to {date: total_price} dict."""
        rates = {}
        for rec in recommendations:
            date_str = rec.get("date") or rec.get("date_iso", "")
            if not date_str:
                continue
            # Normalize to YYYY-MM-DD
            if "T" in date_str:
                date_str = date_str[:10]
            total = rec.get("total_price")
            if total is not None:
                rates[date_str] = float(total)
        return rates