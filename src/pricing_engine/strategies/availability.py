"""Availability rules strategy — min stay, blocked days, gap detection."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .base import PricingStrategy


class AvailabilityResult:
    """Output from availability rules check."""

    def __init__(
        self,
        is_available: bool,
        min_stay: int,
        blocked_reason: str | None = None,
        factors: dict[str, Any] | None = None,
    ):
        self.is_available = is_available
        self.min_stay = min_stay
        self.blocked_reason = blocked_reason
        self.factors = factors or {}


class AvailabilityStrategy(PricingStrategy):
    """Check availability rules for a given date."""

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
        """Return availability for a single date, including min_stay and block reason."""
        # Load availability config for this property
        avail_cfg = _get_avail_config(config, property_uid)

        target = datetime.strptime(date, "%Y-%m-%d")
        dow = target.strftime("%a").lower()  # 'mon', 'tue', etc.

        # 1. Check blocked checkin/checkout days
        blocked_checkin = avail_cfg.get("checkin_days", {}).get("blocked", [])
        blocked_checkout = avail_cfg.get("checkout_days", {}).get("blocked", [])

        if dow in blocked_checkin:
            return AvailabilityResult(
                is_available=False,
                min_stay=avail_cfg.get("min_stay", {}).get("default", 2),
                blocked_reason=f"checkin blocked on {dow}",
                factors={"blocked_checkin": True, "dow": dow},
            )

        if dow in blocked_checkout:
            return AvailabilityResult(
                is_available=False,
                min_stay=avail_cfg.get("min_stay", {}).get("default", 2),
                blocked_reason=f"checkout blocked on {dow}",
                factors={"blocked_checkout": True, "dow": dow},
            )

        # 2. Same-day checkin/checkout rules
        same_day_cfg = avail_cfg.get("same_day_checkin", {})
        if not same_day_cfg.get("allowed", False):
            if calendar_entry and calendar_entry.get("status") == "same_day":
                exception = same_day_cfg.get("exception", {})
                if dow not in exception.get("dow", []):
                    return AvailabilityResult(
                        is_available=False,
                        min_stay=avail_cfg.get("min_stay", {}).get("default", 2),
                        blocked_reason="same_day_checkin not allowed",
                        factors={"same_day_checkin_blocked": True},
                    )

        # 3. Compute min stay for this date
        min_stay = _compute_min_stay(avail_cfg, target)

        # 4. Check for gap (isolated night)
        gap_cfg = avail_cfg.get("gap_handling", {})
        if gap_cfg.get("auto_block_gaps", False):
            gap_result = _check_gap(target, bookings_in_window, gap_cfg.get("min_gap_nights", 1))
            if gap_result.is_blocked:
                return AvailabilityResult(
                    is_available=False,
                    min_stay=min_stay,
                    blocked_reason=gap_result.reason,
                    factors=gap_result.factors,
                )

        return AvailabilityResult(
            is_available=True,
            min_stay=min_stay,
            blocked_reason=None,
            factors={"dow": dow, "min_stay": min_stay},
        )


def _get_avail_config(config: dict[str, Any], property_uid: str) -> dict[str, Any]:
    """Pull availability config for a property, with global defaults."""
    props = config.get("property_overrides", {}).get(property_uid, {})
    avail = (
        props.get("availability", {})
        if isinstance(props.get("availability"), dict)
        else config.get("availability", {})
    )
    return avail


def _compute_min_stay(avail_cfg: dict[str, Any], target: datetime) -> int:
    """Compute min stay for a given date, checking overrides in order."""
    min_stay_cfg = avail_cfg.get("min_stay", {})
    default = min_stay_cfg.get("default", 2)
    overrides = min_stay_cfg.get("overrides", [])
    dow = target.strftime("%a").lower()
    month = target.month

    for override in overrides:
        when = override.get("when", {})
        conditions_met = True
        if "dow" in when and dow not in when["dow"]:
            conditions_met = False
        if "months" in when and month not in when["months"]:
            conditions_met = False
        if conditions_met:
            return override.get("min_nights", default)

    return default


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
    """Check if target_date is an isolated gap night between two bookings."""
    for b in bookings_in_window:
        checkout = _parse_date(b.get("checkout", ""))
        if not checkout:
            continue
        # Gap night is day after checkout
        if (target_date - checkout).days == 1:
            next_checkin_date = _next_booking_checkin(bookings_in_window, checkout, target_date)
            if next_checkin_date and (next_checkin_date - target_date).days <= min_gap_nights + 1:
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
    candidates = []
    for b in bookings:
        checkin = _parse_date(b.get("checkin", ""))
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