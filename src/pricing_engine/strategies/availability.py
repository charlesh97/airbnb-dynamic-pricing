"""Availability rules strategy — readable, ordered rule checks."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .base import PricingStrategy


class AvailabilityResult:
    """Output from availability rules check."""

    def __init__(
        self,
        is_available: bool,
        blocked_reason: str | None = None,
        factors: dict[str, Any] | None = None,
    ):
        self.is_available = is_available
        self.blocked_reason = blocked_reason
        self.factors = factors or {}


class AvailabilityStrategy(PricingStrategy):
    """Evaluate date availability using explicit ordered rules."""

    name = "availability"

    def compute(
        self,
        *,
        property_uid: str,
        date: str,
        calendar_entry: dict[str, Any] | None,
        bookings_in_window: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> AvailabilityResult:
        avail_cfg = _get_avail_config(config, property_uid)
        target = datetime.strptime(date, "%Y-%m-%d")
        dow = target.strftime("%a").lower()

        return _evaluate_base_rules(
            target=target,
            dow=dow,
            calendar_entry=calendar_entry,
            bookings_in_window=bookings_in_window,
            avail_cfg=avail_cfg,
        )


def _blocked(reason: str, factors: dict[str, Any]) -> AvailabilityResult:
    return AvailabilityResult(
        is_available=False,
        blocked_reason=reason,
        factors=factors,
    )


def _evaluate_base_rules(
    *,
    target: datetime,
    dow: str,
    calendar_entry: dict[str, Any] | None,
    bookings_in_window: list[dict[str, Any]],
    avail_cfg: dict[str, Any],
) -> AvailabilityResult:
    # 1) Blocked check-in weekdays.
    blocked_checkin = set((avail_cfg.get("checkin_days", {}) or {}).get("blocked", []) or [])
    if dow in blocked_checkin:
        return _blocked(f"checkin blocked on {dow}", {"blocked_checkin": True, "dow": dow})

    # 2) Blocked check-out weekdays.
    blocked_checkout = set((avail_cfg.get("checkout_days", {}) or {}).get("blocked", []) or [])
    if dow in blocked_checkout:
        return _blocked(f"checkout blocked on {dow}", {"blocked_checkout": True, "dow": dow})

    # 3) Same-day checkin rule.
    same_day_cfg = avail_cfg.get("same_day_checkin", {}) or {}
    if not bool(same_day_cfg.get("allowed", False)):
        if calendar_entry and calendar_entry.get("status") == "same_day":
            exception = same_day_cfg.get("exception", {}) or {}
            allowed_dow = set(exception.get("dow", []) or [])
            if dow not in allowed_dow:
                return _blocked("same_day_checkin not allowed", {"same_day_checkin_blocked": True})

    # 4) Gap handling.
    gap_cfg = avail_cfg.get("gap_handling", {}) or {}
    if bool(gap_cfg.get("auto_block_gaps", False)):
        gap = _check_gap(target, bookings_in_window, int(gap_cfg.get("min_gap_nights", 1) or 1))
        if gap.is_blocked:
            return _blocked(gap.reason or "isolated_gap", gap.factors)

    # 5) Block day before / after bookings.
    block_before = bool(avail_cfg.get("block_day_before", False))
    block_after = bool(avail_cfg.get("block_day_after", False))
    if block_before or block_after:
        for booking in bookings_in_window:
            checkin_raw = booking.get("checkin") or str(booking.get("local_checkin_dttm", ""))[:10]
            checkout_raw = booking.get("checkout") or str(booking.get("local_checkout_dttm", ""))[:10]
            checkin = _parse_date(checkin_raw)
            checkout = _parse_date(checkout_raw)

            if block_before and checkin:
                blocked_day = checkin - timedelta(days=1)
                if target == blocked_day:
                    code = booking.get("reservation_code", "?")
                    return _blocked(
                        f"day_before_checkin_blocked ({code})",
                        {
                            "day_before_checkin_blocked": True,
                            "checkin": checkin.isoformat(),
                        },
                    )

            if block_after and checkout and target == checkout:
                code = booking.get("reservation_code", "?")
                return _blocked(
                    f"day_after_checkout_blocked ({code})",
                    {
                        "day_after_checkout_blocked": True,
                        "checkout": checkout.isoformat(),
                    },
                )

    # 6) Booking window.
    booking_window = avail_cfg.get("booking_window_days")
    if booking_window is not None:
        today = datetime.now().date()
        days_out = (target.date() - today).days
        if days_out < 0 or days_out > int(booking_window):
            return _blocked(
                "booking_window_closed",
                {
                    "booking_window_days": int(booking_window),
                    "days_out": days_out,
                },
            )

    return AvailabilityResult(
        is_available=True,
        blocked_reason=None,
        factors={
            "dow": dow,
        },
    )


def _get_avail_config(config: dict[str, Any], property_uid: str) -> dict[str, Any]:
    """Pull availability config for a property, with global defaults."""
    props = config.get("property_overrides", {}).get(property_uid, {})
    if isinstance(props.get("availability"), dict):
        return props.get("availability", {})
    return config.get("availability", {}) or {}


class GapCheckResult:
    """Result of gap checking."""

    def __init__(
        self,
        is_blocked: bool,
        reason: str | None = None,
        factors: dict[str, Any] | None = None,
    ):
        self.is_blocked = is_blocked
        self.reason = reason
        self.factors = factors or {}


def _check_gap(
    target_date: datetime,
    bookings_in_window: list[dict[str, Any]],
    min_gap_nights: int = 1,
) -> GapCheckResult:
    """Check whether target_date is an isolated gap night between bookings."""
    for booking in bookings_in_window:
        checkout = _parse_date(booking.get("checkout", ""))
        if not checkout:
            continue

        if (target_date - checkout).days != 1:
            continue

        next_checkin = _next_booking_checkin(bookings_in_window, checkout, target_date)
        if next_checkin and (next_checkin - target_date).days <= min_gap_nights + 1:
            return GapCheckResult(
                is_blocked=True,
                reason=f"isolated gap night ({min_gap_nights} night gap)",
                factors={"gap_night": True, "prev_checkout": checkout.isoformat()},
            )

    return GapCheckResult(is_blocked=False)


def _next_booking_checkin(
    bookings: list[dict[str, Any]],
    after_date: datetime,
    before_date: datetime,
) -> datetime | None:
    """Find the next booking checkin after after_date and before before_date."""
    candidates: list[datetime] = []
    for booking in bookings:
        checkin = _parse_date(booking.get("checkin", ""))
        if checkin and after_date < checkin <= before_date + timedelta(days=1):
            candidates.append(checkin)
    return min(candidates) if candidates else None


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            pass
    return None


def _build_booked_nights_set(bookings: list[dict[str, Any]]) -> set[str]:
    nights: set[str] = set()
    for booking in bookings:
        status = str(booking.get("booking_status", "") or "").lower()
        if status and status not in {"accepted", "confirmed"}:
            continue
        checkin = _parse_date(booking.get("checkin", ""))
        checkout = _parse_date(booking.get("checkout", ""))
        if not checkin or not checkout:
            continue
        cursor = checkin.date()
        last = checkout.date()
        while cursor < last:
            nights.add(cursor.isoformat())
            cursor += timedelta(days=1)
    return nights
