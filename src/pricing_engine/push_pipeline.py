"""Single push-to-iGMS pipeline  shared by FastAPI routes and CLI.

This is the **only** module allowed to decide push behaviour.  Every
code-path (dashboard route, CLI command, future scheduler) must call
``run_push_pipeline()`` rather than implementing its own push logic.
"""

from __future__ import annotations

import copy
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .booking_adapter import fetch_bookings_for_window
from .client import PricingClient
from .config import EngineConfig
from .config_store import PropertyConfigStore
from .engine import PricingEngine

logger = logging.getLogger(__name__)

_DEFAULT_ENV_PATH = str(Path(__file__).parent.parent.parent / ".env")

_SKIPPED_LIVE_BLOCKED_MAX = 200


#  request / response types 


@dataclass
class PushPipelineRequest:
    """Input for a single push run."""

    property_uid: str
    dry_run: bool = False
    env_path: str = _DEFAULT_ENV_PATH


@dataclass
class PushPipelineResult:
    """Structured result returned by every push run."""

    success: bool
    from_date: str = ""
    to_date: str = ""
    base_booking_window_days: int = 0
    effective_window_days: int = 0
    dates_evaluated: int = 0
    price_updates_sent: int = 0
    availability_updates_sent: int = 0
    dates_skipped_booked: int = 0
    dates_skipped_live_blocked: int = 0
    dates_skipped_outside_window: int = 0
    skipped_live_blocked_dates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


#  helpers 


def _parse_date(value: str) -> datetime.date | None:
    """Parse a date string into a ``datetime.date``."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt).date()
        except ValueError:
            pass
    return None


def _coerce_is_available(entry: dict[str, Any]) -> bool | None:
    """Return ``True`` / ``False`` / ``None`` from an iGMS calendar entry.

    This is the **single canonical helper**  no other module should
    duplicate this logic.
    """
    if "is_available" in entry:
        raw = entry.get("is_available")
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw != 0
        if isinstance(raw, str):
            val = raw.strip().lower()
            if val in {"1", "true", "yes", "y"}:
                return True
            if val in {"0", "false", "no", "n"}:
                return False
    status = str(entry.get("status", "")).strip().lower()
    if status in {"available", "open"}:
        return True
    if status in {"unavailable", "blocked", "booked", "closed", "reserved"}:
        return False
    return None


def _coerce_price(value: Any) -> float | None:
    """Return a finite float value for price fields, else ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _build_live_day_map(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build ``{date: {price, is_available}}`` from iGMS entries."""
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        date_str = entry.get("date", "")
        if not date_str:
            continue
        result[date_str] = {
            "price": _coerce_price(entry.get("price")),
            "is_available": _coerce_is_available(entry),
        }
    return result


def _group_contiguous_dates(dates: list[str]) -> list[tuple[str, str]]:
    """Group sorted unique date strings into contiguous [start, end] ranges."""
    parsed_set: set[datetime.date] = set()
    for date_str in dates:
        parsed = _parse_date(date_str)
        if parsed is not None:
            parsed_set.add(parsed)
    parsed_dates = sorted(parsed_set)
    if not parsed_dates:
        return []

    ranges: list[tuple[str, str]] = []
    start = parsed_dates[0]
    prev = parsed_dates[0]
    for current in parsed_dates[1:]:
        if (current - prev).days == 1:
            prev = current
            continue
        ranges.append((start.isoformat(), prev.isoformat()))
        start = current
        prev = current
    ranges.append((start.isoformat(), prev.isoformat()))
    return ranges


def _build_booked_nights_set(bookings: list[dict[str, Any]]) -> set[str]:
    """Build a set of booked night date-strings ``[checkin, checkout)``."""
    booked: set[str] = set()
    for b in bookings:
        if b.get("booking_status") not in ("accepted", "confirmed"):
            continue
        checkin = _parse_date(b.get("checkin") or "")
        checkout = _parse_date(b.get("checkout") or "")
        if not checkin or not checkout:
            continue
        cur = checkin
        while cur < checkout:
            booked.add(cur.isoformat())
            cur += timedelta(days=1)
    return booked


def _create_client(env_path: str) -> PricingClient:
    """Create an authenticated ``PricingClient`` from environment."""
    cfg = EngineConfig.from_env(env_path)
    client = PricingClient()
    if hasattr(client, "set_access_token"):
        client.set_access_token(cfg.igms_access_token)
    else:
        client.access_token = cfg.igms_access_token
    return client


#  pipeline 


def run_push_pipeline(req: PushPipelineRequest) -> PushPipelineResult:
    """Execute the full push pipeline for a single property.

    This is the **only** function allowed to implement push logistics.
    Every caller (dashboard route, CLI command, scheduler) must go
    through this entry-point.
    """

    errors: list[str] = []
    warnings: list[str] = []

    #  1. Load config 
    store = PropertyConfigStore()
    prop_config = store.load(req.property_uid)
    if not prop_config:
        return PushPipelineResult(
            success=False,
            errors=[f"Property config not found for {req.property_uid}"],
        )

    engine_config = EngineConfig.from_env(req.env_path)
    merged = store.merge_with_env_defaults(
        req.property_uid, engine_config.__dict__
    )

    logger.debug(
        "Loaded config for %s | env=%s | keys=%s",
        req.property_uid,
        req.env_path,
        sorted(merged.keys()),
    )

    #  2. Booking window 
    base_bwd: int = int(
        merged.get("availability", {}).get("booking_window_days", 120) or 120
    )
    effective_window_days = base_bwd + 60

    logger.debug(
        "Booking window: base=%sd effective=%sd",
        base_bwd,
        effective_window_days,
    )

    #  3. Date range 
    today = datetime.now().date()
    from_date = today
    to_date = today + timedelta(days=effective_window_days)
    from_date_str = from_date.isoformat()
    to_date_str = to_date.isoformat()

    logger.debug("Push range: %s  %s (%s days)", from_date_str, to_date_str, effective_window_days)

    #  4. Create client 
    try:
        client = _create_client(req.env_path)
    except Exception as exc:
        return PushPipelineResult(
            success=False,
            from_date=from_date_str,
            to_date=to_date_str,
            base_booking_window_days=base_bwd,
            effective_window_days=effective_window_days,
            errors=[f"Failed to create iGMS client: {exc}"],
        )

    #  5. Fetch live iGMS calendar 
    try:
        raw = client.get_calendar(req.property_uid, from_date_str, to_date_str)
    except Exception as exc:
        return PushPipelineResult(
            success=False,
            from_date=from_date_str,
            to_date=to_date_str,
            base_booking_window_days=base_bwd,
            effective_window_days=effective_window_days,
            errors=[f"Failed to fetch iGMS calendar: {exc}"],
        )
    entries: list[dict[str, Any]] = raw if isinstance(raw, list) else raw.get("data", [])
    live_day_map = _build_live_day_map(entries)

    logger.debug(
        "Live calendar: %s entries  %s dates with data",
        len(entries),
        len(live_day_map),
    )

    #  6. Fetch bookings 
    try:
        bookings = fetch_bookings_for_window(
            client, req.property_uid, from_date_str, to_date_str
        )
    except Exception as exc:
        logger.warning("Booking fetch failed (non-fatal): %s", exc)
        bookings = []
    booked_nights = _build_booked_nights_set(bookings)

    logger.debug("Bookings: %s records  %s booked nights", len(bookings), len(booked_nights))

    # 7. Compute recommendations
    config_copy = copy.deepcopy(merged)
    av_cfg = config_copy.setdefault("availability", {})
    av_cfg["booking_window_days"] = effective_window_days

    logger.debug(
        "Computing with effective window: base_price=%s min/max=%s/%s",
        config_copy.get("base_price", "?"),
        config_copy.get("min_price", "?"),
        config_copy.get("max_price", "?"),
    )

    engine = PricingEngine()
    try:
        results = engine.compute_range(
            property_uid=req.property_uid,
            from_date=from_date_str,
            to_date=to_date_str,
            calendar_data=entries,
            bookings_in_window=bookings,
            config=config_copy,
        )
    except Exception as exc:
        return PushPipelineResult(
            success=False,
            from_date=from_date_str,
            to_date=to_date_str,
            base_booking_window_days=base_bwd,
            effective_window_days=effective_window_days,
            errors=[f"Engine computation failed: {exc}"],
        )

    logger.debug("Engine computed %s dates", len(results))

    # 8. Diff + build push payloads
    calendar_batch: list[dict[str, Any]] = []
    price_count = 0
    availability_count = 0
    skipped_booked = 0
    skipped_live_blocked = 0
    skipped_outside = 0
    blocked_dates: list[str] = []

    for dp in results:
        date_str = dp.date

        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        if d < from_date or d > to_date:
            skipped_outside += 1
            continue

        if date_str in booked_nights:
            skipped_booked += 1
            continue

        live = live_day_map.get(date_str, {})
        live_avail = live.get("is_available")
        if live_avail is False:
            skipped_live_blocked += 1
            blocked_dates.append(date_str)
            continue

        avail = engine.compute_availability(
            property_uid=req.property_uid,
            date=date_str,
            calendar_entry=None,
            bookings_in_window=bookings,
            config=config_copy,
        )
        desired_is_available = bool(avail.is_available)

        desired_price = dp.final_price
        live_price = live.get("price")

        price_diff = abs((desired_price or 0.0) - (live_price or 0.0))
        price_changed = (
            desired_is_available
            and
            desired_price is not None
            and desired_price > 0
            and price_diff >= 0.01
        )
        should_block_availability = (not desired_is_available) and (live_avail is not False)

        if logger.isEnabledFor(logging.DEBUG):
            expl = dp.all_factors.get("explanation", {})
            components = expl.get("components", []) if isinstance(expl, dict) else []

            action_parts = []
            if price_changed:
                action_parts.append("PUSH-price")
            if should_block_availability:
                action_parts.append("PUSH-block")
            action = "+".join(action_parts) if action_parts else "SKIP"

            lines = [f"{date_str} price calc:"]
            lines.append(f"  base={float(expl.get('base_price', 0.0)):.2f}")
            for comp in components:
                if not isinstance(comp, dict):
                    continue
                label = str(comp.get("label", comp.get("key", "component")))
                pct = float(comp.get("pct", 0.0) or 0.0)
                amount = float(comp.get("amount", 0.0) or 0.0)
                running = float(comp.get("running_subtotal", 0.0) or 0.0)
                lines.append(
                    f"  {amount:+.2f} {label} ({pct:+.2f}%) -> {running:.2f}"
                )
            lines.extend(
                [
                    f"  subtotal={float(expl.get('subtotal_before_adjust', 0.0) or 0.0):.2f}",
                    (
                        f"  {float(expl.get('price_adjust_amount', 0.0) or 0.0):+.2f} "
                        f"Global Adjust ({float(expl.get('price_adjust_pct', 0.0) or 0.0):+.2f}%)"
                    ),
                    (
                        f"  clamp[min={float(expl.get('min_price', 0.0) or 0.0):.2f} "
                        f"max={float(expl.get('max_price', 0.0) or 0.0):.2f}] "
                        f"-> final={float(expl.get('final_price', 0.0) or 0.0):.2f}"
                    ),
                    (
                        f"  live=${live_price if live_price is not None else 'n/a'} "
                        f"-> delta=${round(price_diff, 2)} -> {action}"
                    ),
                ]
            )
            logger.debug("%s", "\n".join(lines))

        if price_changed:
            payload: dict[str, Any] = {
                "date": date_str,
                "currency": "USD",
            }
            if desired_price is not None:
                payload["price"] = round(desired_price, 2)
            calendar_batch.append(payload)
            price_count += 1
            logger.debug(
                "PUSH %s: price $%s->$%s (delta $%s)",
                date_str,
                live_price if live_price is not None else "n/a",
                desired_price,
                round(price_diff, 2),
            )
        if should_block_availability:
            availability_count += 1
            # Push block via set_calendar_batch (set_property_availability v2
            # endpoint requires a scope our token doesn't have — it returns
            # HTTP 200 with error code 14 silently).
            calendar_batch.append({
                "date": date_str,
                "currency": "USD",
                "is_available": False,
            })
            logger.debug(
                "BLOCK %s: live_availability=%s reason=%s",
                date_str,
                live_avail,
                avail.blocked_reason or "availability_rule",
            )

    # 9. Dry-run short-circuit
    if req.dry_run:
        logger.info(
            "DRY-RUN %s: %s dates | would-push-price=%s would-block-availability=%s | "
            "skipped-booked=%s skipped-blocked=%s skipped-outside=%s",
            req.property_uid,
            len(results),
            price_count,
            availability_count,
            skipped_booked,
            skipped_live_blocked,
            skipped_outside,
        )

        if len(blocked_dates) > _SKIPPED_LIVE_BLOCKED_MAX:
            warnings.append(
                f"Truncated skipped_live_blocked_dates from {len(blocked_dates)} "
                f"to {_SKIPPED_LIVE_BLOCKED_MAX}"
            )
            blocked_dates = blocked_dates[:_SKIPPED_LIVE_BLOCKED_MAX]

        return PushPipelineResult(
            success=True,
            from_date=from_date_str,
            to_date=to_date_str,
            base_booking_window_days=base_bwd,
            effective_window_days=effective_window_days,
            dates_evaluated=len(results),
            price_updates_sent=price_count,
            availability_updates_sent=availability_count,
            dates_skipped_booked=skipped_booked,
            dates_skipped_live_blocked=skipped_live_blocked,
            dates_skipped_outside_window=skipped_outside,
            skipped_live_blocked_dates=blocked_dates,
            warnings=warnings,
        )

    # 10. Push (calendar_batch now includes both price updates and availability blocks)
    if calendar_batch:
        price_updates = [d for d in calendar_batch if "price" in d]
        block_updates = [d for d in calendar_batch if d.get("is_available") is False]
        logger.info(
            "PUSHING %s: %s updates (%s price, %s block) to iGMS",
            req.property_uid,
            len(calendar_batch),
            len(price_updates),
            len(block_updates),
        )
        try:
            result = client.set_calendar_batch(
                property_uid=req.property_uid,
                days=calendar_batch,
            )
            sc = getattr(result, "status_code", 200)
            payload = getattr(result, "payload", None)

            # Check for error objects in response payload (iGMS returns HTTP 200
            # with {"error": {"code": N, "message": "..."}} for scope/auth failures)
            api_error = None
            if isinstance(payload, dict) and "error" in payload:
                err = payload["error"]
                api_error = (
                    f"iGMS API error (HTTP {sc}): "
                    f"code={err.get('code', '?')} "
                    f"message={err.get('message', str(err))}"
                )

            if sc >= 400 or api_error:
                errors.append(api_error or f"set-calendar-batch HTTP {sc}: {payload}")
                logger.error("PUSH FAILED %s: %s", req.property_uid, errors[-1])
            else:
                logger.info(
                    "PUSH OK %s: %s updates sent (HTTP %s)",
                    req.property_uid,
                    len(calendar_batch),
                    sc,
                )
        except Exception as exc:
            errors.append(str(exc))
            logger.exception("set-calendar-batch failed for %s", req.property_uid)

    if not calendar_batch:
        logger.info("PUSH %s: no changes - nothing to push", req.property_uid)

    # 11. Truncate blocked-date list
    if len(blocked_dates) > _SKIPPED_LIVE_BLOCKED_MAX:
        warnings.append(
            f"Truncated skipped_live_blocked_dates from {len(blocked_dates)} "
            f"to {_SKIPPED_LIVE_BLOCKED_MAX}"
        )
        blocked_dates = blocked_dates[:_SKIPPED_LIVE_BLOCKED_MAX]

    logger.info(
        "PUSH RESULT %s: success=%s | evaluated=%s pushed-price=%s pushed-availability=%s | "
        "skipped-booked=%s skipped-blocked=%s skipped-outside=%s | errors=%s",
        req.property_uid,
        len(errors) == 0,
        len(results),
        price_count,
        availability_count,
        skipped_booked,
        skipped_live_blocked,
        skipped_outside,
        len(errors),
    )

    return PushPipelineResult(
        success=len(errors) == 0,
        from_date=from_date_str,
        to_date=to_date_str,
        base_booking_window_days=base_bwd,
        effective_window_days=effective_window_days,
        dates_evaluated=len(results),
        price_updates_sent=price_count,
        availability_updates_sent=availability_count,
        dates_skipped_booked=skipped_booked,
        dates_skipped_live_blocked=skipped_live_blocked,
        dates_skipped_outside_window=skipped_outside,
        skipped_live_blocked_dates=blocked_dates,
        errors=errors,
        warnings=warnings,
    )
