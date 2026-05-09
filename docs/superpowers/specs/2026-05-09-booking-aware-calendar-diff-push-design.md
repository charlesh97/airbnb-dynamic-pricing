# Booking-Aware Calendar + Diff-Only Push

## Context

This spec extends the calendar and push system to:
1. Display imported bookings as green stay bars on the month grid
2. Fix `block_day_after` semantics (blocks checkout night, not checkout + 1 day)
3. Push prices and availability as **diffs only** — never rewrite state that already matches desired state
4. Skip booked nights entirely on push; push price diffs for unavailable non-booked nights without flipping availability

---

## 1. `block_day_after` Semantic Fix

**File:** `src/pricing_engine/strategies/availability.py:121-129`

**Change:** The rule currently blocks `checkout + 1 day`. Change to block the **checkout date itself**.

```python
# OLD (line 122):
blocked_day_after = checkout + timedelta(days=1)
if target == blocked_day_after:

# NEW:
if block_after and checkout:
    if target == checkout:  # block the checkout night, not checkout + 1
        return AvailabilityResult(
            is_available=False,
            min_stay=min_stay,
            blocked_reason=f"day_after_checkout_blocked ({b.get('reservation_code', '?')})",
            factors={"day_after_checkout_blocked": True, "checkout": checkout.isoformat()},
        )
```

`block_day_before` remains unchanged: `checkin - 1 day` is blocked.

---

## 2. Calendar API — `bookings` Array in Response

**Files:**
- `dashboard/routes/calendar.py` — build and attach booking spans
- `dashboard/engine_proxy.py` — `_fetch_bookings_for_window` returns bookings; a new helper builds spans
- `dashboard/models.py` — `CalendarResponse` gets `bookings: list[BookingSpan]`

### 2.1 New Pydantic Models

```python
class BookingSpan(BaseModel):
    booking_id: str
    reservation_code: Optional[str]
    guest_name: Optional[str]
    checkin: str          # "YYYY-MM-DD"
    checkout: str         # "YYYY-MM-DD"
    checkin_display: str  # human-friendly e.g. "May 9"
    checkout_display: str # human-friendly e.g. "May 11"
    nights: int           # computed as date diff

class CalendarResponse(BaseModel):
    # ... existing fields ...
    bookings: list[BookingSpan] = Field(default_factory=list)
```

### 2.2 Span Building Logic

In `calendar.py` (or `engine_proxy.py`), build spans from `bookings_in_window`:

```python
def _build_booking_spans(bookings: list[dict]) -> list[dict]:
    """Build calendar-displayable spans from raw booking records.

    A booked night is any date d where checkin <= d < checkout.
    The checkout night (checkin of the next day after last checkin night) is NOT blocked.
    """
    spans = []
    for b in bookings:
        if b.get("booking_status") not in ("accepted", "confirmed"):
            continue
        checkin_raw = b.get("checkin") or b.get("local_checkin_dttm", "")[:10]
        checkout_raw = b.get("checkout") or b.get("local_checkout_dttm", "")[:10]
        if not checkin_raw or not checkout_raw:
            continue

        checkin = datetime.strptime(checkin_raw[:10], "%Y-%m-%d").date()
        checkout = datetime.strptime(checkout_raw[:10], "%Y-%m-%d").date()

        # Determine display label: guest name > reservation_code > booking_id
        label = b.get("guest_name") or b.get("reservation_code") or b.get("booking_id", "?")[:20]

        spans.append({
            "booking_id": b.get("booking_id", ""),
            "reservation_code": b.get("reservation_code"),
            "guest_name": b.get("guest_name"),
            "checkin": checkin.isoformat(),
            "checkout": checkout.isoformat(),
            "checkin_display": checkin.strftime("%b %-d"),
            "checkout_display": checkout.strftime("%b %-d"),
            "nights": (checkout - checkin).days,
            "label": label,
        })
    return spans
```

Attach spans to the `CalendarResponse`:
```python
bookings_in_window = [b for b in bookings_in_window if b.get("booking_status") in ("accepted", "confirmed")]
spans = _build_booking_spans(bookings_in_window)
# ...
return CalendarResponse(
    # ... existing fields ...,
    bookings=spans,
)
```

---

## 3. Calendar UI — Booking Bars on Grid

**Files:**
- `dashboard/static/js/calendar.js` — `buildCell` renders green booking bars
- `dashboard/static/css/dashboard.css` — `.booking-stay-bar`, `.booking-label`, new unavailable styles

### 3.1 Cell Rendering — Booking State

In `buildCell`, after the `isUnavailable` check, add:

```javascript
// Check if this date is part of a booking (checkin <= date < checkout)
const booking = day.booking;  // attached by the API or computed from spans
if (booking) {
  cell.classList.add("has-booking");
  // Render stay bar
  const stayBar = document.createElement("div");
  stayBar.className = "booking-stay-bar";
  // Show on all nights of the booking span
  // Style differently for checkin night vs middle vs checkout-1
  const isCheckin = booking.checkin === day.date;
  const isCheckoutEve = booking.checkout === _next_day(day.date);
  if (isCheckin) stayBar.classList.add("bar-checkin");
  else if (isCheckoutEve) stayBar.classList.add("bar-checkout");
  stayBar.textContent = booking.label || "";
  cell.appendChild(stayBar);
}
```

**Booking lookup:** The API attaches a `booking` object to each day that falls within a span:
```python
# In _date_price_to_dict or a post-processing step:
for day in days:
    day["booking"] = None
for span in spans:
    for day in days:
        if span["checkin"] <= day["date"] < span["checkout"]:
            day["booking"] = span
```

### 3.2 CSS — Booking Stay Bar

```css
/* Booking stay bar inside day cell */
.booking-stay-bar {
  position: absolute;
  left: 4px;
  right: 4px;
  bottom: 28px;  /* above the price line */
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
.booking-stay-bar.bar-checkin {
  border-radius: 4px 0 0 4px;
}
.booking-stay-bar.bar-checkout {
  border-radius: 0 4px 4px 0;
}
```

### 3.3 Unavailable Cell Behavior (unchanged, confirmed)

- Unavailable cells: grey background, "Unavailable" label, popup allowed if inside booking window
- `booking_window_closed` cells: grey, "Outside booking window", no popup
- No `NEW`/`Proposed` badges on unavailable cells (already implemented)
- The `day.booking` overlay can show even on unavailable cells — the booking bar renders above the grey bg

---

## 4. Push Route — Diff-Only with Booked-Night Skip

**Files:**
- `dashboard/routes/push.py` — rewrite with diff-only logic + set-calendar-batch

### 4.1 Live State Map

At the start of `push_prices`, build a `live_day_map`:

```python
live_day_map: dict[str, dict] = {}  # date -> {price, min_stay, is_available}
for entry in live_entries_in_window:
    date_str = entry.get("date", "")
    if not date_str:
        continue
    live_day_map[date_str] = {
        "price": entry.get("price"),
        "min_stay": entry.get("min_stay"),
        "is_available": _coerce_is_available(entry),
    }
```

### 4.2 Booked-Night Set

```python
# Build the full booked-night set across the entire push window
booked_nights: set[str] = set()
for y, m in _iter_months_in_range(from_date, to_date):
    month_bookings = _fetch_bookings_for_window(property_uid, f"{y:04d}-{m:02d}-01", ...)
    for b in month_bookings:
        checkin = _parse_date(b.get("checkin") or b.get("local_checkin_dttm", "")[:10])
        checkout = _parse_date(b.get("checkout") or b.get("local_checkout_dttm", "")[:10])
        if checkin and checkout:
            cur = checkin
            while cur < checkout:
                booked_nights.add(cur.isoformat())
                cur += timedelta(days=1)
```

### 4.3 Desired State Map

```python
desired_day_map: dict[str, dict] = {}  # date -> {price, min_stay, is_available}
for date_str, day in days_map.items():
    desired_day_map[date_str] = {
        "price": day.get("final_price"),
        "min_stay": day.get("min_stay"),
        "is_available": day.get("is_available", True),
    }
```

### 4.4 Price + Min_Stay Diff (skip booked nights)

```python
calendar_batch: list[dict] = []

for date_str in sorted(desired_day_map.keys()):
    if date_str in booked_nights:
        continue  # Never push pricing for booked nights

    desired = desired_day_map[date_str]
    live = live_day_map.get(date_str, {})

    price_changed = (
        desired["price"] is not None
        and desired["price"] > 0
        and abs((desired["price"] or 0) - (live.get("price") or 0)) >= 0.01
    )
    min_stay_changed = (
        desired.get("min_stay") is not None
        and desired.get("min_stay") != live.get("min_stay")
    )

    if price_changed or min_stay_changed:
        # For unavailable non-booked nights: push price diff but do NOT flip availability
        if not desired["is_available"]:
            # Only push if there's actually a price/min_stay diff
            pass  # already filtered by price_changed/min_stay_changed above

        calendar_batch.append({
            "date": date_str,
            "price": round(desired["price"], 2) if desired["price"] else None,
            "currency": "USD",
            "min_stay": desired.get("min_stay"),
        })
```

### 4.5 Availability Diff (never change booked nights)

```python
availability_updates: list[tuple[str, bool]] = []

for date_str in sorted(desired_day_map.keys()):
    desired_avail = desired_day_map[date_str]["is_available"]
    live_avail = live_day_map.get(date_str, {}).get("is_available")

    # Never change availability for booked nights
    if date_str in booked_nights:
        continue

    # Only send when desired differs from live
    if desired_avail != live_avail:
        availability_updates.append((date_str, desired_avail))
```

### 4.6 Write via `set-calendar-batch` + Availability

```python
if calendar_batch:
    result = client.set_calendar_batch(property_uid=property_uid, days=calendar_batch)
    # handle errors

if availability_updates:
    # Group consecutive dates for range writes
    grouped = _group_consecutive_dates(availability_updates)
    for start_date, end_date, is_available in grouped:
        client.set_property_availability(
            property_uid=property_uid,
            start_date=start_date,
            end_date=end_date,
            is_available=is_available,
        )
```

### 4.7 No-Op Behavior

If `calendar_batch` is empty and `availability_updates` is empty:
- Return `PushResponse(success=True, price_updates_sent=0, ...)` with no API calls

---

## 5. Endpoint / Scope Standardization

**Primary write endpoint: `POST /api/v1/set-calendar-batch` (calendar-control scope)**

- Use `client.set_calendar_batch()` for all price + min_stay writes
- Use `client.set_property_availability()` for availability writes only
- Do NOT use propose endpoints

**Breakdown of write operations:**

| Operation | Endpoint |
|-----------|----------|
| Price + min_stay diff | `set-calendar-batch` |
| Availability range | `set_property_availability` |
| Block beyond window | `set_property_availability` |

---

## 6. Summary of Changes

| File | Change |
|------|--------|
| `src/pricing_engine/strategies/availability.py` | Fix `block_day_after` to block checkout date, not checkout + 1 |
| `dashboard/models.py` | Add `BookingSpan` model; add `bookings` to `CalendarResponse` |
| `dashboard/engine_proxy.py` | Add `_build_booking_spans` helper; attach booking to each day |
| `dashboard/routes/calendar.py` | Attach booking spans to calendar response |
| `dashboard/static/js/calendar.js` | In `buildCell`: render green booking bar; check `booking` on day |
| `dashboard/static/css/dashboard.css` | Add `.booking-stay-bar`, `.bar-checkin`, `.bar-checkout` styles |
| `dashboard/routes/push.py` | Complete rewrite: diff-only, booked-night skip, `set-calendar-batch` |

---

## 7. Test Coverage

### Availability Rules
- `block_day_before`: May 9 checkin → May 8 blocked
- `block_day_after`: May 10 checkout → **May 10** blocked (not May 11)
- Booking window closed: no popup, greyed

### Calendar API
- Returns booking spans for `[checkin, checkout)` range
- Checkout night (last night of stay) correctly NOT in booked set
- Correct `nights` count = checkout - checkin

### Push Diff Logic
- No API calls when nothing differs
- Only sends changed nights
- Booked nights skipped even if price differs
- Unavailable non-booked nights: price diff pushed, availability unchanged
- Availability writes only on state transition
- Booked nights never have availability changed

### UI
- Green bar shows on all nights `checkin <= d < checkout`
- Bar label shows guest name / reservation code / booking_id
- Rounded caps: left cap on checkin night, right cap on checkout-1 night
- Unavailable cells still grey but show booking bar overlay