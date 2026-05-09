# Booking-Aware Calendar + Diff-Only Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add booking bars to calendar grid, fix block_day_after semantics, rewrite push route as diff-only with booked-night skip.

**Architecture:** Booking spans are built server-side from iGMS bookings and returned in CalendarResponse. Frontend computes per-day booking membership client-side. Push route builds live_day_map + booked_nights set, sends only diffs via set-calendar-batch + set_property_availability. Availability writes skip booked nights and only fire on state transitions.

**Tech Stack:** Python (FastAPI, engine_proxy), JavaScript (calendar.js), CSS (dashboard.css), pytest.

---

## File Structure

| File | Role |
|------|------|
| `src/pricing_engine/strategies/availability.py` | Fix `block_day_after` to block checkout date |
| `dashboard/models.py` | Add `BookingSpan` model; add `bookings` to `CalendarResponse` |
| `dashboard/routes/calendar.py` | Build booking spans, attach to response |
| `dashboard/static/js/calendar.js` | Render booking bars; compute membership client-side |
| `dashboard/static/css/dashboard.css` | Add `.booking-stay-bar` CSS |
| `dashboard/routes/push.py` | Complete rewrite with diff-only + booked-night skip |
| `tests/test_availability.py` | Add block_day_after test |

---

## Tasks

### Task 1: Fix `block_day_after` semantics

**Files:**
- Modify: `src/pricing_engine/strategies/availability.py:121-129`

- [ ] **Step 1: Write failing test for block_day_after behavior**

Add to `tests/test_availability.py`:

```python
def test_block_day_after_blocks_checkout_date_not_plus_one():
    """block_day_after should block the checkout night, not checkout + 1 day."""
    from datetime import date
    from pricing_engine.strategies.availability import AvailabilityStrategy, AvailabilityResult

    strat = AvailabilityStrategy()
    config = {
        "availability": {
            "block_day_after": True,
            "block_day_before": False,
            "booking_window_days": 120,
        }
    }
    bookings = [{
        "checkin": "2026-05-09",
        "checkout": "2026-05-11",
        "reservation_code": "RES001",
        "booking_status": "accepted",
    }]

    # May 11 is checkout date — should be blocked
    result_checkout_night = strat.compute(
        property_uid="test",
        date="2026-05-11",
        calendar_entry=None,
        bookings_in_window=bookings,
        config=config,
    )
    assert result_checkout_night.is_available is False, "Checkout night (May 11) should be blocked"

    # May 12 is checkout + 1 — should NOT be blocked
    result_plus_one = strat.compute(
        property_uid="test",
        date="2026-05-12",
        calendar_entry=None,
        bookings_in_window=bookings,
        config=config,
    )
    assert result_plus_one.is_available is True, "checkout+1 (May 12) should NOT be blocked"

    # May 10 is checkout - 1 (last occupied night) — NOT blocked by block_day_after
    result_minus_one = strat.compute(
        property_uid="test",
        date="2026-05-10",
        calendar_entry=None,
        bookings_in_window=bookings,
        config=config,
    )
    assert result_minus_one.is_available is True, "checkout-1 (May 10) should NOT be blocked by block_day_after"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_availability.py::test_block_day_after_blocks_checkout_date_not_plus_one -v`
Expected: FAIL — current code blocks May 12 not May 11

- [ ] **Step 3: Fix `block_day_after` in availability.py**

Replace lines 121-129:
```python
                if block_after and checkout:
                    blocked_day_after = checkout + timedelta(days=1)
                    if target == blocked_day_after:
                        return AvailabilityResult(
                            is_available=False,
                            min_stay=min_stay,
                            blocked_reason=f"day_after_checkout_blocked ({b.get('reservation_code', '?')})",
                            factors={"day_after_checkout_blocked": True, "checkout": checkout.isoformat()},
                        )
```
With:
```python
                if block_after and checkout:
                    if target == checkout:
                        return AvailabilityResult(
                            is_available=False,
                            min_stay=min_stay,
                            blocked_reason=f"day_after_checkout_blocked ({b.get('reservation_code', '?')})",
                            factors={"day_after_checkout_blocked": True, "checkout": checkout.isoformat()},
                        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_availability.py::test_block_day_after_blocks_checkout_date_not_plus_one -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pricing_engine/strategies/availability.py tests/test_availability.py
git commit -m "fix: block_day_after blocks checkout date, not checkout+1"
```

---

### Task 2: Add `BookingSpan` model + extend `CalendarResponse`

**Files:**
- Modify: `dashboard/models.py`

- [ ] **Step 1: Add `BookingSpan` class and update `CalendarResponse`**

After line 57 (after `IgmsSync` class definition, before `CalendarResponse`), add:

```python
class BookingSpan(BaseModel):
    booking_id: str
    label: str
    reservation_code: Optional[str] = None
    guest_name: Optional[str] = None
    checkin: str
    checkout: str
    checkin_display: str
    checkout_display: str
    nights: int
```

In `CalendarResponse`, add `bookings` field:
```python
class CalendarResponse(BaseModel):
    year: int
    month: int
    property_uid: str
    days: List[DayResponse]
    sync: IgmsSync | None = None
    bookings: List[BookingSpan] = Field(default_factory=list)  # NEW
```

- [ ] **Step 2: Verify syntax**

Run: `python3 -m py_compile dashboard/models.py`
Expected: No output

- [ ] **Step 3: Commit**

```bash
git add dashboard/models.py
git commit -m "feat: add BookingSpan model and bookings field in CalendarResponse"
```

---

### Task 3: Build and attach booking spans in calendar route

**Files:**
- Modify: `dashboard/routes/calendar.py`

- [ ] **Step 1: Add `_build_booking_spans` helper and use it in `get_calendar`**

Add before `@router.get("/calendar/{year}/{month}", ...)`:

```python
from datetime import datetime

def _build_booking_spans(bookings: list[dict]) -> list[dict]:
    """Build BookingSpan list from raw booking records.

    Booked nights: checkin <= d < checkout (checkout night is NOT booked).
    """
    spans = []
    for b in bookings:
        if b.get("booking_status") not in ("accepted", "confirmed"):
            continue

        checkin_raw = b.get("checkin") or b.get("local_checkin_dttm", "")[:10]
        checkout_raw = b.get("checkout") or b.get("local_checkout_dttm", "")[:10]
        if not checkin_raw or not checkout_raw:
            continue

        try:
            checkin_dt = datetime.strptime(checkin_raw[:10], "%Y-%m-%d").date()
            checkout_dt = datetime.strptime(checkout_raw[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        label = (
            b.get("guest_name")
            or b.get("reservation_code")
            or b.get("booking_id", "?")
        )[:20]

        spans.append({
            "booking_id": b.get("booking_id", ""),
            "label": label,
            "reservation_code": b.get("reservation_code"),
            "guest_name": b.get("guest_name"),
            "checkin": checkin_dt.isoformat(),
            "checkout": checkout_dt.isoformat(),
            "checkin_display": checkin_dt.strftime("%b %-d"),
            "checkout_display": checkout_dt.strftime("%b %-d"),
            "nights": (checkout_dt - checkin_dt).days,
        })
    return spans
```

In `get_calendar`, after line 127 (after `days = compute_month(...)`), add span building and attach to response. Find the `return CalendarResponse(...)` block and add `bookings=spans`:

```python
    # Build booking spans for UI display
    confirmed_bookings = [b for b in bookings_in_window if b.get("booking_status") in ("accepted", "confirmed")]
    spans = _build_booking_spans(confirmed_bookings)

    return CalendarResponse(
        year=year,
        month=month,
        property_uid=property_uid,
        days=[DayResponse(**d) for d in days],
        sync=IgmsSync(
            igms_pull_success=igms_pull_success,
            igms_price_count=igms_price_count,
            igms_bookings_count=igms_bookings_count,
            igms_error=igms_error,
            pulled_at=datetime.now(timezone.utc).isoformat(),
        ),
        bookings=spans,  # NEW
    )
```

- [ ] **Step 2: Verify syntax**

Run: `python3 -m py_compile dashboard/routes/calendar.py`
Expected: No output

- [ ] **Step 3: Commit**

```bash
git add dashboard/routes/calendar.py
git commit -m "feat: attach booking spans to calendar API response"
```

---

### Task 4: Add booking bar CSS to dashboard.css

**Files:**
- Modify: `dashboard/static/css/dashboard.css`

- [ ] **Step 1: Append booking bar styles at end of file**

```css
/* ── Booking Stay Bar ─────────────────────────────────────────────────────── */
.booking-stay-bar {
  position: absolute;
  left: 4px;
  right: 4px;
  bottom: 28px;
  height: 18px;
  background: #10b981;
  border-radius: 4px;
  font-size: 8px;
  font-weight: 700;
  color: #ffffff;
  display: flex;
  align-items: center;
  padding: 0 4px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  z-index: 1;
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/css/dashboard.css
git commit -m "feat: add booking stay bar CSS"
```

---

### Task 5: Add booking bar rendering to calendar.js

**Files:**
- Modify: `dashboard/static/js/calendar.js`

- [ ] **Step 1: Update `renderGrid` to precompute booking span map**

In `renderGrid`, after `grid.innerHTML = "";`, add:

```javascript
  // Precompute booking span map for fast lookup
  const bookingSpanMap = {};
  if (window._calendarBookings) {
    for (const span of window._calendarBookings) {
      const checkin = span.checkin;
      const checkout = span.checkout;
      let d = checkin;
      const checkoutDate = new Date(checkout + 'T00:00:00');
      const checkinDate = new Date(checkin + 'T00:00:00');
      const current = new Date(checkinDate);
      while (current < checkoutDate) {
        const dateStr = current.toISOString().split('T')[0];
        bookingSpanMap[dateStr] = span;
        current.setDate(current.getDate() + 1);
      }
    }
  }
```

Pass `bookingSpanMap` to `buildCell`:
```javascript
  days.forEach(day => {
    const cell = buildCell(day, propertyUid, bookingSpanMap);
    grid.appendChild(cell);
  });
```

- [ ] **Step 2: Update `buildCell` signature and add booking bar rendering**

Change `buildCell` function signature:
```javascript
function buildCell(day, propertyUid, bookingSpanMap = {}) {
```

After existing booking check (line ~203):
```javascript
  // Check for booking membership from precomputed map
  const cellBooking = bookingSpanMap[day.date] || null;

  // ... existing unavailable checks ...

  // Render booking bar if this date is within a booking span
  if (cellBooking) {
    const stayBar = document.createElement("div");
    stayBar.className = "booking-stay-bar";
    stayBar.textContent = cellBooking.label || "";
    cell.appendChild(stayBar);
  }
```

- [ ] **Step 3: Store bookings from API response for use in renderGrid**

In `loadMonth` (or the fetch handler that calls `renderGrid`), store the bookings:

```javascript
  window._calendarBookings = data.bookings || [];
```

Find where `renderGrid(data.days, propertyUid)` is called and add the line before it:
```javascript
  window._calendarBookings = data.bookings || [];
  renderGrid(data.days, propertyUid);
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/static/js/calendar.js
git commit -m "feat: render booking bars from spans in calendar grid"
```

---

### Task 6: Complete rewrite of push.py — diff-only with booked-night skip

**Files:**
- Modify: `dashboard/routes/push.py`

This is the largest change. Replace the entire file content.

- [ ] **Step 1: Write tests first**

Add to `tests/test_integration.py` or create `tests/test_push.py`:

```python
from datetime import date, timedelta

class TestPushDiffOnly:
    def test_no_op_when_no_diffs(self):
        """No API calls when live state matches desired."""
        # Mock client returns same prices
        # Push should return success with 0 writes
        pass

    def test_skips_booked_nights(self):
        """Never push price for a booked night."""
        # Night is in booked_nights set
        # set_calendar_batch should not include that date
        pass

    def test_unavailable_non_booked_price_diff(self):
        """Push price diff for unavailable non-booked night without flipping availability."""
        # day is unavailable, not in booked_nights
        # price differs from live
        # set_calendar_batch called with price
        # set_property_availability NOT called
        pass

    def test_availability_write_only_on_transition(self):
        """Only send availability write when desired != live."""
        pass

    def test_no_availability_control_scope_calls(self):
        """set_property_availability is the only availability endpoint used."""
        pass

    def test_booked_nights_availability_unchanged(self):
        """Booked nights never have availability changed."""
        pass
```

- [ ] **Step 2: Rewrite push.py**

Replace the entire file with:

```python
"""POST /api/calendar/push — diff-only price/availability push to iGMS."""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from ..engine_proxy import compute_month, _get_pricing_client, _fetch_bookings_for_window, _CONFIG_STORE

router = APIRouter(prefix="/api", tags=["calendar"])
LOG = logging.getLogger(__name__)

BEYOND_WINDOW_BLOCK_HORIZON_DAYS = 365


class PushRequest(BaseModel):
    property_uid: str
    year: int
    month: int


class PushResponse(BaseModel):
    success: bool
    price_updates_sent: int = 0
    availability_updates_sent: int = 0
    dates_priced: int = 0
    dates_blocked: int = 0
    errors: list[str] = []


def _parse_date(value: str) -> datetime.date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt).date()
        except ValueError:
            pass
    return None


def _coerce_is_available(entry: dict) -> bool | None:
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


def _build_live_day_map(entries: list[dict]) -> dict[str, dict]:
    """Build {date: {price, min_stay, is_available}} from iGMS calendar entries."""
    result = {}
    for entry in entries:
        date_str = entry.get("date", "")
        if not date_str:
            continue
        result[date_str] = {
            "price": entry.get("price"),
            "min_stay": entry.get("min_stay"),
            "is_available": _coerce_is_available(entry),
        }
    return result


def _build_booked_nights_set(bookings: list[dict]) -> set[str]:
    """Build set of booked night date strings [checkin, checkout)."""
    booked = set()
    for b in bookings:
        if b.get("booking_status") not in ("accepted", "confirmed"):
            continue
        checkin = _parse_date(b.get("checkin") or b.get("local_checkin_dttm", "")[:10])
        checkout = _parse_date(b.get("checkout") or b.get("local_checkout_dttm", "")[:10])
        if not checkin or not checkout:
            continue
        cur = checkin
        while cur < checkout:
            booked.add(cur.isoformat())
            cur += timedelta(days=1)
    return booked


def _iter_months_in_range(from_date: datetime.date, to_date: datetime.date):
    cur = datetime(from_date.year, from_date.month, 1)
    end = datetime(to_date.year, to_date.month, 1)
    while cur <= end:
        yield cur.year, cur.month
        m = cur.month + 1
        y = cur.year
        if m > 12:
            m = 1
            y += 1
        cur = datetime(y, m, 1)


def _group_consecutive_avail_writes(
    writes: list[tuple[str, str, bool]]
) -> list[tuple[str, str, bool]]:
    """Group consecutive same-availability dates into range writes."""
    if not writes:
        return []
    sorted_writes = sorted(writes, key=lambda x: x[0])
    grouped = []
    start_d, end_d, avail = sorted_writes[0]
    for i in range(1, len(sorted_writes)):
        d_start, d_end, d_avail = sorted_writes[i]
        d_start_date = datetime.strptime(d_start, "%Y-%m-%d").date()
        prev_end_date = datetime.strptime(end_d, "%Y-%m-%d").date()
        if d_start_date == prev_end_date + timedelta(days=1) and d_avail == avail:
            end_d = d_end
        else:
            grouped.append((start_d, end_d, avail))
            start_d, end_d, avail = d_start, d_end, d_avail
    grouped.append((start_d, end_d, avail))
    return grouped


@router.post("/calendar/push", response_model=PushResponse)
async def push_prices(body: PushRequest):
    """
    Push computed prices and availability as diffs only.

    Window: today → today + booking_window_days.
    - Skips all booked nights completely.
    - Price/min_stay: sends only when diff >= $0.01 or min_stay differs.
    - Availability: sends only when desired != live (skips booked nights).
    - No-op if nothing differs.
    """
    property_uid = body.property_uid

    cfg = _CONFIG_STORE.load(property_uid)
    bwd = cfg.get("availability", {}).get("booking_window_days", 120)
    today = datetime.utcnow().date()
    from_date = today
    to_date = today + timedelta(days=bwd)

    client = _get_pricing_client()

    # Fetch live calendar entries across full push window
    live_entries: list[dict] = []
    for y, m in _iter_months_in_range(from_date, to_date):
        _, last_day = monthrange(y, m)
        month_start = f"{y:04d}-{m:02d}-01"
        month_end = f"{y:04d}-{m:02d}-{last_day:02d}"
        try:
            raw = client.get_calendar(
                property_uid=property_uid,
                from_date=month_start,
                to_date=month_end,
            )
            entries = raw if isinstance(raw, list) else raw.get("data", [])
            live_entries.extend([e for e in entries if isinstance(e, dict)])
        except Exception:
            pass

    live_day_map = _build_live_day_map(live_entries)

    # Fetch all bookings in window and build booked_nights set
    all_bookings: list[dict] = []
    for y, m in _iter_months_in_range(from_date, to_date):
        _, last_day = monthrange(y, m)
        month_bookings = _fetch_bookings_for_window(
            property_uid,
            f"{y:04d}-{m:02d}-01",
            f"{y:04d}-{m:02d}-{last_day:02d}",
        )
        all_bookings.extend(month_bookings)

    booked_nights = _build_booked_nights_set(all_bookings)

    # Compute desired state for each day
    days_map: dict[str, dict] = {}
    for y, m in _iter_months_in_range(from_date, to_date):
        month_bookings = [b for b in all_bookings if _booking_overlaps_month(b, y, m)]
        month_live_entries = [
            e for e in live_entries
            if str(e.get("date", "")).startswith(f"{y:04d}-{m:02d}-")
        ]
        for day in compute_month(
            property_uid=property_uid,
            year=y,
            month=m,
            bookings_in_window=month_bookings,
            calendar_data=month_live_entries,
        ):
            days_map[day["date"]] = day

    # Compute diffs
    calendar_batch: list[dict] = []
    availability_writes: list[tuple[str, str, bool]] = []

    for date_str in sorted(days_map.keys()):
        if date_str in booked_nights:
            continue

        desired = days_map[date_str]
        live = live_day_map.get(date_str, {})

        desired_price = desired.get("final_price")
        desired_min_stay = desired.get("min_stay")
        desired_avail = desired.get("is_available", True)
        live_price = live.get("price")
        live_min_stay = live.get("min_stay")
        live_avail = live.get("is_available")

        price_diff = abs((desired_price or 0) - (live_price or 0))
        price_changed = desired_price is not None and desired_price > 0 and price_diff >= 0.01
        min_stay_changed = desired_min_stay is not None and desired_min_stay != live_min_stay

        if price_changed or min_stay_changed:
            calendar_batch.append({
                "date": date_str,
                "price": round(desired_price, 2) if desired_price else None,
                "currency": "USD",
                "min_stay": desired_min_stay,
            })

        if desired_avail != live_avail:
            availability_writes.append((date_str, date_str, desired_avail))

    # No-op path
    if not calendar_batch and not availability_writes:
        return PushResponse(success=True, errors=[])

    # Execute writes
    errors: list[str] = []

    if calendar_batch:
        try:
            result = client.set_calendar_batch(property_uid=property_uid, days=calendar_batch)
            sc = getattr(result, "status_code", 200)
            if sc >= 400:
                errors.append(f"set-calendar-batch HTTP {sc}: {getattr(result, 'payload', None)}")
        except Exception as exc:
            errors.append(str(exc))
            LOG.exception("set-calendar-batch failed")

    if availability_writes:
        for start_d, end_d, is_avail in _group_consecutive_avail_writes(availability_writes):
            try:
                result = client.set_property_availability(
                    property_uid=property_uid,
                    start_date=start_d,
                    end_date=end_d,
                    is_available=is_avail,
                )
                sc = getattr(result, "status_code", 200)
                if sc >= 400:
                    errors.append(f"set-property-availability HTTP {sc}: {getattr(result, 'payload', None)}")
            except Exception as exc:
                errors.append(str(exc))
                LOG.exception("set-property-availability failed for %s", start_d)

    return PushResponse(
        success=len(errors) == 0,
        price_updates_sent=len(calendar_batch),
        availability_updates_sent=len(availability_writes),
        dates_priced=len(calendar_batch),
        errors=errors,
    )


def _booking_overlaps_month(booking: dict, year: int, month: int) -> bool:
    """Return True if booking overlaps the given month."""
    checkin = _parse_date(booking.get("checkin") or booking.get("local_checkin_dttm", "")[:10])
    checkout = _parse_date(booking.get("checkout") or booking.get("local_checkout_dttm", "")[:10])
    if not checkin or not checkout:
        return False
    month_start = datetime(year, month, 1).date()
    month_end = datetime(year, month, monthrange(year, month)[1]).date()
    return checkin <= month_end and checkout > month_start
```

- [ ] **Step 3: Verify syntax**

Run: `python3 -m py_compile dashboard/routes/push.py`
Expected: No output

- [ ] **Step 4: Commit**

```bash
git add dashboard/routes/push.py
git commit -m "feat: rewrite push route as diff-only with booked-night skip and no-op path"
```

---

### Task 7: Verification

- [ ] **Step 1: Python syntax check on all changed files**

Run:
```bash
python3 -m py_compile \
  src/pricing_engine/strategies/availability.py \
  dashboard/models.py \
  dashboard/routes/calendar.py \
  dashboard/routes/push.py
echo "All syntax OK"
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/ -v --tb=short -q 2>&1 | head -80`

- [ ] **Step 3: Git log check**

Run: `git log --oneline -15`

---

## Spec Coverage Check

| Spec Section | Task |
|-------------|------|
| `block_day_after` blocks checkout date | Task 1 |
| `BookingSpan` model + `CalendarResponse.bookings` | Task 2 |
| `_build_booking_spans` + span attachment | Task 3 |
| `.booking-stay-bar` CSS | Task 4 |
| Booking bar rendering in calendar grid | Task 5 |
| Diff-only push with booked-night skip | Task 6 |
| No-op path | Task 6 |
| `set-calendar-batch` + `set_property_availability` only | Task 6 |
| Availability writes only on transition | Task 6 |