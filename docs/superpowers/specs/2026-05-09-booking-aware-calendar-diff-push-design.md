# Booking-Aware Calendar + Diff-Only Push

## Context

This spec extends the calendar and push system to:
1. Display imported bookings as green stay bars on the month grid
2. Fix `block_day_after` semantics (blocks checkout night, not checkout + 1 day)
3. Push prices and availability as **diffs only** — never rewrite state that already matches desired state
4. Skip booked nights entirely on push; push price diffs for unavailable non-booked nights without flipping availability
5. Standardize all writes through `calendar-control` scope endpoints only

**Active iGMS scopes:** `listings`, `messaging`, `calendar-control`, `direct-bookings`

---

## 1. `block_day_after` Semantic Fix

**File:** `src/pricing_engine/strategies/availability.py:121-129`

**Change:** The rule currently blocks `checkout + 1 day`. Change to block the **checkout date itself** (the last occupied night).

```python
# OLD (line 122):
blocked_day_after = checkout + timedelta(days=1)
if target == blocked_day_after:

# NEW:
if block_after and checkout:
    if target == checkout:  # blocks the checkout/last-occupied night
        return AvailabilityResult(
            is_available=False,
            min_stay=min_stay,
            blocked_reason=f"day_after_checkout_blocked ({b.get('reservation_code', '?')})",
            factors={"day_after_checkout_blocked": True, "checkout": checkout.isoformat()},
        )
```

`block_day_before` remains unchanged: `checkin - 1 day` is blocked (May 9 checkin → May 8 blocked).

**Test case:** May 10 checkout with `block_day_after=true` → May 10 night is blocked. May 11 is NOT blocked.

---

## 2. Calendar API — `bookings` Array in Response

**Files:**
- `dashboard/models.py` — `BookingSpan` model, add to `CalendarResponse`
- `dashboard/routes/calendar.py` — build spans from bookings, attach to response

### 2.1 Scope and Endpoint Constraints

- **Read endpoints:** `get-calendar-data` (listings scope), `bookings` (direct-bookings scope)
- **Write endpoints (calendar-control scope only):**
  - `POST /api/v1/set-calendar-batch` — price + min_stay per day
  - `POST /api/v2/set-property-calendar-availability` — availability transitions (range)
- **Do NOT use:** propose endpoints, availability-control scope, pricing-management scope

### 2.2 Pydantic Models

```python
class BookingSpan(BaseModel):
    booking_id: str
    label: str                          # guest_name > reservation_code > booking_id
    reservation_code: Optional[str]
    guest_name: Optional[str]
    checkin: str                       # "YYYY-MM-DD"
    checkout: str                      # "YYYY-MM-DD"
    checkin_display: str                # "May 9"
    checkout_display: str               # "May 11"
    nights: int                         # (checkout - checkin).days

class CalendarResponse(BaseModel):
    year: int
    month: int
    property_uid: str
    days: List[DayResponse]
    sync: IgmsSync | None = None
    bookings: List[BookingSpan] = Field(default_factory=list)
```

### 2.3 BookingSpan Building

```python
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

        checkin_dt = datetime.strptime(checkin_raw[:10], "%Y-%m-%d").date()
        checkout_dt = datetime.strptime(checkout_raw[:10], "%Y-%m-%d").date()

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

### 2.4 Frontend Booking Membership

The frontend computes per-day booking membership from `CalendarResponse.bookings`:

```javascript
// In renderGrid or buildCell:
function findBookingForDate(date, bookings) {
  for (const span of bookings) {
    if (span.checkin <= date && date < span.checkout) {
      return span;
    }
  }
  return null;
}
```

No server-side `day.booking` attachment required. The frontend owns booking display logic.

---

## 3. Calendar UI — Booking Bars

**Files:**
- `dashboard/static/js/calendar.js` — `buildCell` renders green booking bars
- `dashboard/static/css/dashboard.css` — `.booking-stay-bar`, `.bar-checkin`, `.bar-checkout`

### 3.1 Cell Rendering

```javascript
function buildCell(day, propertyUid) {
  // ... existing unavailable/booking-window-closed checks ...

  // Check for booking membership (computed client-side from spans)
  const booking = day.booking; // still supported as server-side attachment fallback
  // But primary path: look up from pre-built booking map
  const bookingMap = window._bookingSpanMap || {};
  const cellBooking = bookingMap[day.date] || null;

  // Booking bar
  if (cellBooking) {
    const stayBar = document.createElement("div");
    stayBar.className = "booking-stay-bar";
    stayBar.textContent = cellBooking.label || "";
    cell.appendChild(stayBar);
  }
}
```

### 3.2 CSS

```css
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

### 3.3 Popup Behavior (unchanged, confirmed)

- Unavailable in booking window: popup allowed, "Unavailable" label shown
- `booking_window_closed`: no popup, cell greyed
- Booking bars render over unavailable cells when date is within booking span

---

## 4. Push Route — Diff-Only with Booked-Night Skip

**Files:**
- `dashboard/routes/push.py` — complete rewrite with diff-only logic

### 4.1 Scope Constraints

- **Use:** `set-calendar-batch` (price+min_stay), `set-property-calendar-availability` (availability)
- **Do NOT use:** availability-control scope, propose endpoints

### 4.2 Step-by-Step Diff Logic

**Step 1: Build live_day_map from iGMS calendar**

```python
live_day_map: dict[str, dict] = {}
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

**Step 2: Build booked_nights set from imported bookings**

```python
booked_nights: set[str] = set()
for b in all_bookings_in_window:
    checkin = _parse_date(b.get("checkin") or b.get("local_checkin_dttm", "")[:10])
    checkout = _parse_date(b.get("checkout") or b.get("local_checkout_dttm", "")[:10])
    if checkin and checkout:
        cur = checkin
        while cur < checkout:  # [checkin, checkout) — checkout night is NOT booked
            booked_nights.add(cur.isoformat())
            cur += timedelta(days=1)
```

**Step 3: Build desired_day_map from computed days**

```python
desired_day_map: dict[str, dict] = {}
for date_str, day in days_map.items():
    desired_day_map[date_str] = {
        "price": day.get("final_price"),
        "min_stay": day.get("min_stay"),
        "is_available": day.get("is_available", True),
    }
```

**Step 4: Compute price/min_stay diffs (skip booked nights)**

```python
calendar_batch: list[dict] = []

for date_str in sorted(desired_day_map.keys()):
    if date_str in booked_nights:
        continue  # Never push pricing for booked nights

    desired = desired_day_map[date_str]
    live = live_day_map.get(date_str, {})

    price_diff = abs((desired["price"] or 0) - (live.get("price") or 0))
    price_changed = desired["price"] is not None and desired["price"] > 0 and price_diff >= 0.01
    min_stay_changed = desired.get("min_stay") is not None and desired.get("min_stay") != live.get("min_stay")

    if price_changed or min_stay_changed:
        calendar_batch.append({
            "date": date_str,
            "price": round(desired["price"], 2) if desired["price"] else None,
            "currency": "USD",
            "min_stay": desired.get("min_stay"),
        })
```

**Step 5: Compute availability diffs (skip booked nights)**

```python
availability_writes: list[tuple[str, str, bool]] = []  # (start, end, is_available)

for date_str in sorted(desired_day_map.keys()):
    if date_str in booked_nights:
        continue  # Never change availability for booked nights

    desired_avail = desired_day_map[date_str]["is_available"]
    live_avail = live_day_map.get(date_str, {}).get("is_available")

    if desired_avail != live_avail:
        availability_writes.append((date_str, date_str, desired_avail))
```

**Step 6: Group consecutive availability writes**

```python
def _group_consecutive(dates_wanted: list[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
    """Group consecutive same-availability dates into range writes."""
    if not dates_wanted:
        return []
    dates_wanted.sort(key=lambda x: x[0])
    grouped = []
    start_date, end_date, avail = dates_wanted[0]
    for d_start, d_end, d_avail in dates_wanted[1:]:
        if d_start == end_date + timedelta(days=1) and d_avail == avail:
            end_date = d_end
        else:
            grouped.append((start_date.isoformat() if isinstance(start_date, datetime.date) else start_date,
                            end_date.isoformat() if isinstance(end_date, datetime.date) else end_date,
                            avail))
            start_date, end_date, avail = d_start, d_end, d_avail
    grouped.append((start_date.isoformat() if isinstance(start_date, datetime.date) else start_date,
                    end_date.isoformat() if isinstance(end_date, datetime.date) else end_date,
                    avail))
    return grouped
```

**Step 7: No-op path — return early if nothing to write**

```python
if not calendar_batch and not availability_writes:
    return PushResponse(
        success=True,
        price_updates_sent=0,
        availability_updates_sent=0,
        dates_priced=0,
        dates_blocked=0,
        errors=[],
    )
```

**Step 8: Execute writes**

```python
if calendar_batch:
    result = client.set_calendar_batch(property_uid=property_uid, days=calendar_batch)
    # handle errors (status_code >= 400)

if availability_writes:
    for start_date, end_date, is_available in _group_consecutive(availability_writes):
        result = client.set_property_availability(
            property_uid=property_uid,
            start_date=start_date,
            end_date=end_date,
            is_available=is_available,
        )
```

---

## 5. Summary of Changes

| File | Change |
|------|--------|
| `src/pricing_engine/strategies/availability.py` | `block_day_after` blocks checkout date, not checkout+1 |
| `dashboard/models.py` | Add `BookingSpan` model; add `bookings` list to `CalendarResponse` |
| `dashboard/routes/calendar.py` | Build and attach booking spans from iGMS bookings |
| `dashboard/engine_proxy.py` | (no changes needed) |
| `dashboard/static/js/calendar.js` | Render green booking bar from `bookings` spans; compute membership client-side |
| `dashboard/static/css/dashboard.css` | Add `.booking-stay-bar` styles |
| `dashboard/routes/push.py` | Complete rewrite: live_day_map, booked_nights set, diff-only, no-op, set-calendar-batch + set_property_availability |

---

## 6. Test Coverage Requirements

| Test | Scope |
|------|-------|
| `block_day_after` blocks checkout date, not checkout+1 | availability.py |
| Booking span `[checkin, checkout)` — checkout night not booked | calendar |
| Diff-only push: no API calls when nothing differs | push.py |
| Booked-night skip: never push price for booked night | push.py |
| Unavailable non-booked: price diff pushed, availability unchanged | push.py |
| Availability writes only on state transition | push.py |
| No `availability-control` endpoint calls (scope enforcement) | push.py |
| Booking bar renders over unavailable cells | calendar.js |