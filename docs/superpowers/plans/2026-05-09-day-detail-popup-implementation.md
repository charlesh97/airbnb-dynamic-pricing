# Day Detail Popup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the full-screen blurred day detail modal with an anchored side popup (desktop) / centered card (mobile), and redesign content into a stacked monetary breakdown showing how each pricing factor contributes to the final recommendation.

**Architecture:** The popup is a fixed full-viewport layer (`pointer-events:none`) containing an absolutely-positioned card (`pointer-events:auto`). On desktop, the card anchors to the right or left of the clicked day cell using `getBoundingClientRect()`. On mobile (`<768px`), the card centers in the viewport via flexbox. The API is extended with an ordered `adjustment_ladder` array so the UI renders exact dollar deltas rather than recomputing from multipliers.

**Tech Stack:** Python (FastAPI routes, engine proxy, pydantic models), JavaScript (calendar.js positioning + rendering), CSS (dashboard.css popup styles).

---

## File Structure

| File | Role |
|------|------|
| `dashboard/templates/base.html` | HTML shell: `#day-popup-layer` + `#day-popup-card` replaces `#day-modal` |
| `dashboard/static/js/calendar.js` | Rename `openDayModal` → `openDayPopup`, anchor positioning, scroll/resize handlers, outside-click close, `renderDayDetailPopup` |
| `dashboard/static/css/dashboard.css` | New CSS classes for popup card, breakdown rows, delta chips, mobile override |
| `dashboard/models.py` | Add `adjustment_ladder`, `subtotal_before_blend`, `blend_adjustment_amount`, `final_recommended`, `current_igms_price` to `DayDetailResponse` |
| `dashboard/engine_proxy.py` | Build `adjustment_ladder` in `_date_price_to_detail`; add new fields to returned dict |
| `dashboard/routes/day_detail.py` | No structural change; data flows through from proxy unchanged |

---

## Tasks

### Task 1: Update `DayDetailResponse` model (API contract)

**Files:**
- Modify: `dashboard/models.py:59-78`

- [ ] **Step 1: Edit `dashboard/models.py` — add new fields to `DayDetailResponse`**

Replace `DayDetailResponse` class with:

```python
class AdjustmentItem(BaseModel):
    key: str
    label: str
    amount: float
    running_total_after: float


class DayDetailResponse(BaseModel):
    date: str
    property_uid: str
    final_price: float
    current_airbnb_price: Optional[float]
    confidence: float
    is_available: bool
    min_stay: int
    blocked_reason: Optional[str]
    booking_window_days: int
    match_status: Optional[str]
    base_rate: float
    seasonal: dict
    demand: dict
    event: dict
    yield_: dict = Field(alias="yield")
    competitor: dict
    strategy_weights: dict
    strategy_prices: dict
    raw_factors: dict
    # New fields
    adjustment_ladder: list[AdjustmentItem] = Field(default_factory=list)
    subtotal_before_blend: float = 0.0
    blend_adjustment_amount: float = 0.0
    final_recommended: float = 0.0
    current_igms_price: Optional[float] = None
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/models.py
git commit -m "feat: extend DayDetailResponse with adjustment_ladder and monetary breakdown fields"
```

---

### Task 2: Build `adjustment_ladder` in `engine_proxy.py`

**Files:**
- Modify: `dashboard/engine_proxy.py:356-480` (`_date_price_to_detail` function)

- [ ] **Step 1: Add `_build_adjustment_ladder` helper function before `_date_price_to_detail`**

Add at line ~355 (before `_date_price_to_detail`):

```python
def _build_adjustment_ladder(af: dict, base_price: float) -> list[dict]:
    """Build adjustment ladder from all_factors dict.

    Each entry: {key, label, amount (signed delta), running_total_after}
    Ordered as applied: seasonality → dow → demand → yield → competitor.
    """
    ladder = []
    running = base_price

    ev = af.get("event", {})
    dm = af.get("demand", {})
    yt = af.get("yield", {})
    co = af.get("competitor", {})

    seasonal_mult = ev.get("seasonal_multiplier", 1.0)
    seasonal_amt = (seasonal_mult - 1.0) * base_price
    if abs(seasonal_amt) >= 0.01:
        running += seasonal_amt
        ladder.append({
            "key": "seasonality",
            "label": "Seasonality",
            "amount": round(seasonal_amt, 2),
            "running_total_after": round(running, 2),
        })

    dow_mult = ev.get("dow_multiplier", 1.0)
    dow_amt = (dow_mult - 1.0) * base_price
    if abs(dow_amt) >= 0.01:
        running += dow_amt
        ladder.append({
            "key": "dow",
            "label": "Day-of-week",
            "amount": round(dow_amt, 2),
            "running_total_after": round(running, 2),
        })

    demand_mult = dm.get("demand_multiplier", 1.0)
    demand_amt = (demand_mult - 1.0) * base_price
    if abs(demand_amt) >= 0.01:
        running += demand_amt
        ladder.append({
            "key": "demand",
            "label": "Demand",
            "amount": round(demand_amt, 2),
            "running_total_after": round(running, 2),
        })

    yt_mult = yt.get("final_multiplier", yt.get("lead_factor", 1.0))
    yield_amt = (yt_mult - 1.0) * base_price
    if abs(yield_amt) >= 0.01:
        running += yield_amt
        ladder.append({
            "key": "yield",
            "label": "Yield",
            "amount": round(yield_amt, 2),
            "running_total_after": round(running, 2),
        })

    co_mult = 1.0 if co.get("status") == "disabled" else co.get("adjustment_factor", 1.0)
    competitor_amt = (co_mult - 1.0) * base_price
    if abs(competitor_amt) >= 0.01:
        running += competitor_amt
        ladder.append({
            "key": "competitor",
            "label": "Competitor",
            "amount": round(competitor_amt, 2),
            "running_total_after": round(running, 2),
        })

    return ladder
```

- [ ] **Step 2: Modify `_date_price_to_detail` to call `_build_adjustment_ladder` and add new fields**

Replace the return dict in `_date_price_to_detail` (starting around line 404) to include new fields:

```python
    # Build adjustment ladder
    base_price_val = config.get("base_price", 200.0)
    ladder = _build_adjustment_ladder(af, base_price_val)

    subtotal_before_blend = ladder[-1]["running_total_after"] if ladder else base_price_val
    blend_adjustment_amount = round(dp.final_price - subtotal_before_blend, 2)

    return {
        "date": dp.date,
        "property_uid": dp.property_uid,
        "final_price": dp.final_price,
        "current_airbnb_price": current,
        "confidence": dp.confidence,
        "is_available": avail.is_available,
        "min_stay": avail.min_stay,
        "blocked_reason": avail.blocked_reason,
        "booking_window_days": bwd,
        "match_status": match,
        "base_rate": base_price_val,
        "seasonal": {
            "rule": seasonal_rule,
            "detail": seasonal_detail,
            "multiplier": ev.get("seasonal_multiplier", 1.0),
            "dow": ev.get("dow", ""),
            "dow_multiplier": ev.get("dow_multiplier", 1.0),
            "raw_seasonal_multiplier": ev.get("seasonal_multiplier", 1.0),
            "effective_seasonal": round(seasonal_mult * dow_mult, 3),
        },
        "demand": {
            "multiplier": dm.get("demand_multiplier", 1.0),
            "occupancy": {
                "value": dm.get("occupancy_rate", 0.0),
                "window_days": config.get("demand_config", {}).get("demand_window_days", 14),
                "factor": config.get("demand_config", {}).get("occupancy_factor", 0.3),
                "contribution": f"Occupancy {dm.get('occupancy_rate', 0):.0%}",
            },
            "velocity": {
                "value": dm.get("bookings_per_day", 0.0),
                "window_days": config.get("demand_config", {}).get("velocity_window_days", 7),
                "factor": config.get("demand_config", {}).get("velocity_factor", 0.15),
                "contribution": f"Velocity {dm.get('bookings_per_day', 0):.2f}/day",
            },
            "far_future": {
                "discount": config.get("demand_config", {}).get("far_future", {}).get("discount", 0.9),
                "window_days": config.get("demand_config", {}).get("far_future", {}).get("window_days", 60),
                "active": dm.get("far_future_discount_applied", False),
            },
            "last_minute": {
                "discount": config.get("demand_config", {}).get("last_minute", {}).get("discount", 0.92),
                "window_days": config.get("demand_config", {}).get("last_minute", {}).get("window_days", 7),
                "threshold_occupancy": config.get("demand_config", {}).get("last_minute", {}).get("threshold_occupancy", 0.5),
                "active": dm.get("last_minute_applied", False),
            },
        },
        "event": {
            "suggested_price": dp.strategy_prices.get("event"),
            "factors": {
                "local_event": ev.get("local_event_applied"),
                "event_factor": ev.get("local_event_applied"),
                "holiday_proximity": ev.get("holiday_proximity"),
            },
        },
        "yield": {
            "suggested_price": dp.strategy_prices.get("yield"),
            "factors": {
                "yield_score": af.get("yield", {}).get("yield_score", None),
                "recent_booking_value": af.get("yield", {}).get("recent_bookings_avg", None),
            },
        },
        "competitor": {
            "suggested_price": dp.strategy_prices.get("competitor"),
            "factors": af.get("competitor", {}),
            "note": af.get("competitor", {}).get("note", ""),
        },
        "strategy_weights": {
            "demand": weights.get("demand", 0),
            "event": weights.get("event", 0),
            "competitor": weights.get("competitor", 0),
            "yield": weights.get("yield", 0),
        },
        "strategy_prices": dp.strategy_prices,
        "raw_factors": af,
        # New monetary breakdown fields
        "adjustment_ladder": ladder,
        "subtotal_before_blend": round(subtotal_before_blend, 2),
        "blend_adjustment_amount": blend_adjustment_amount,
        "final_recommended": round(dp.final_price, 2),
        "current_igms_price": current,
    }
```

- [ ] **Step 3: Run tests to verify nothing is broken**

Run: `pytest tests/ -v --tb=short -q`
Expected: All pass (or pre-existing failures unchanged)

- [ ] **Step 4: Commit**

```bash
git add dashboard/engine_proxy.py
git commit -m "feat: build adjustment_ladder in day detail with dollar breakdowns"
```

---

### Task 3: Replace `#day-modal` with `#day-popup-layer` in `base.html`

**Files:**
- Modify: `dashboard/templates/base.html:122-137`

- [ ] **Step 1: Replace the `#day-modal` block with `#day-popup-layer`**

Replace lines 122-137:
```html
  <!-- DAY MODAL — full screen overlay -->
  <div id="day-modal" class="fixed inset-0 z-50 hidden flex items-center justify-center" aria-modal="true">
    <div class="absolute inset-0 bg-black/30 backdrop-blur-sm" onclick="closeDayModal()"></div>
    <div class="relative bg-surface-container-lowest rounded-[24px] shadow-[0_30px_60px_rgba(33,150,243,0.15)]" style="max-width:360px;width:90%;padding:24px;">
      <div class="flex justify-between items-start mb-6">
        <div>
          <h2 id="modal-date" class="font-display-lg text-headline-lg text-on-surface"></h2>
          <p id="modal-property" class="text-label-sm text-on-surface-variant mt-1"></p>
        </div>
        <button onclick="closeDayModal()" class="w-10 h-10 rounded-full bg-surface-container-high flex items-center justify-center hover:bg-surface-container-highest transition-colors">
          <span class="material-symbols-outlined text-on-surface-variant">close</span>
        </button>
      </div>
      <div id="modal-content" class="space-y-4"></div>
    </div>
  </div>
```

With:
```html
  <!-- DAY POPUP — anchored side card (desktop) / centered card (mobile) -->
  <div id="day-popup-layer" class="fixed inset-0 z-50 hidden pointer-events-none" aria-hidden="true">
    <div id="day-popup-card" class="day-popup-card">
      <div class="popup-header">
        <div>
          <h2 id="popup-date" class="popup-date-text"></h2>
          <p id="popup-property" class="popup-sub-text"></p>
        </div>
        <button id="popup-close-btn" class="popup-close-btn">
          <span class="material-symbols-outlined">close</span>
        </button>
      </div>
      <div id="popup-content" class="popup-content"></div>
      <div id="popup-footer" class="popup-footer"></div>
    </div>
  </div>
```

- [ ] **Step 2: Update the inline `<script>` block to wire up popup close**

Replace lines 140-149:
```html
  <script>
    // Property switcher
    document.getElementById('property-switcher')?.addEventListener('change', function() {
      localStorage.setItem('atlas_property_uid', this.value);
      const url = new URL(window.location);
      url.searchParams.set('property_uid', this.value);
      window.location.href = window.location.pathname + '?property_uid=' + this.value;
    });
    function closeDayModal() { document.getElementById('day-modal').classList.add('hidden'); }
  </script>
```

With:
```html
  <script>
    // Property switcher
    document.getElementById('property-switcher')?.addEventListener('change', function() {
      localStorage.setItem('atlas_property_uid', this.value);
      const url = new URL(window.location);
      url.searchParams.set('property_uid', this.value);
      window.location.href = window.location.pathname + '?property_uid=' + this.value;
    });
    // Popup close via button — wired by calendar.js
  </script>
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/templates/base.html
git commit -m "feat: replace day-modal with anchored day-popup-layer"
```

---

### Task 4: Add popup CSS to `dashboard.css`

**Files:**
- Modify: `dashboard/static/css/dashboard.css` (append new styles)

- [ ] **Step 1: Add popup CSS after existing styles**

Append at end of `dashboard/static/css/dashboard.css`:

```css
/* ── Day Popup — Anchored Side Card ─────────────────────────────────────────── */
#day-popup-layer {
  z-index: 50;
}
#day-popup-layer.open {
  pointer-events: auto;
}

/* ── Popup Card (desktop absolute) ─────────────────────────────────────────── */
.day-popup-card {
  position: absolute;
  width: min(400px, 90vw);
  background: #ffffff;
  border-radius: 24px;
  box-shadow: 0 30px 60px rgba(33,150,243,0.15), 0 4px 16px rgba(0,0,0,0.08);
  padding: 24px;
  pointer-events: auto;
  z-index: 51;
  font-family: 'Plus Jakarta Sans', sans-serif;
  max-height: 85vh;
  overflow-y: auto;
}

/* Mobile centered (< 768px) */
@media (max-width: 767px) {
  #day-popup-layer {
    display: flex;
    align-items: center;
    justify-content: center;
  }
  #day-popup-layer:not(.hidden) {
    display: flex;
  }
  .day-popup-card {
    position: static;
    width: min(92vw, 420px);
    margin: auto;
    box-shadow: 0 20px 40px rgba(33,150,243,0.12), 0 4px 16px rgba(0,0,0,0.08);
  }
}

/* ── Popup Header ───────────────────────────────────────────────────────────── */
.popup-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.popup-date-text {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 22px;
  font-weight: 600;
  color: #191c1e;
  letter-spacing: -0.01em;
  line-height: 28px;
}
.popup-sub-text {
  font-size: 12px;
  font-weight: 600;
  color: #707883;
  margin-top: 2px;
}
.popup-close-btn {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #e6e8ea;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border: none;
  transition: background 0.2s;
  flex-shrink: 0;
}
.popup-close-btn:hover { background: #d8dadc; }
.popup-close-btn .material-symbols-outlined { font-size: 18px; color: #404752; }

/* ── Final Price in Header ─────────────────────────────────────────────────── */
.popup-final-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 16px;
}
.popup-final-price {
  font-family: 'Hanken Grotesk', sans-serif;
  font-size: 28px;
  font-weight: 700;
  color: #191c1e;
  letter-spacing: 0.02em;
}
.delta-chip {
  display: inline-block;
  font-size: 12px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 9999px;
}
.delta-chip.positive { background: #ffdad5; color: #930005; }
.delta-chip.negative { background: #d1e4ff; color: #00497d; }

/* ── Breakdown Block ────────────────────────────────────────────────────────── */
.breakdown-block { margin-top: 4px; }
.breakdown-block-title {
  font-size: 10px;
  font-weight: 700;
  color: #707883;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 6px;
}
.breakdown-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 5px 0;
  font-size: 13px;
  border-bottom: 1px dashed #eceef0;
  font-variant-numeric: tabular-nums;
}
.breakdown-row:last-of-type { border-bottom: none; }
.breakdown-row .row-label { color: #404752; font-weight: 400; }
.breakdown-row .row-amount { font-weight: 600; color: #191c1e; }
.breakdown-row.positive .row-amount { color: #b81311; }
.breakdown-row.negative .row-amount { color: #0061a4; }
.subtotal-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0 4px;
  font-size: 13px;
  font-weight: 700;
  border-top: 2px solid #191c1e;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}
.subtotal-row .row-label { color: #191c1e; }
.subtotal-row .row-amount { color: #191c1e; }

/* ── Blend Block ────────────────────────────────────────────────────────────── */
.blend-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0 2px;
  font-size: 12px;
  color: #707883;
  font-variant-numeric: tabular-nums;
}
.blend-row.positive .row-amount { color: #b81311; }
.blend-row.negative .row-amount { color: #0061a4; }

/* ── Final Recommended Row ──────────────────────────────────────────────────── */
.final-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 8px 0 4px;
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.final-row .row-label { color: #191c1e; }
.final-row .row-amount {
  color: #0061a4;
  font-family: 'Hanken Grotesk', sans-serif;
  font-size: 22px;
  font-weight: 700;
}

/* ── iGMS Line ──────────────────────────────────────────────────────────────── */
.igms-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-top: 1px solid #e0e3e5;
  margin-top: 8px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.igms-line .igms-label { color: #707883; }
.igms-line .igms-value { font-weight: 600; color: #191c1e; }
.igms-change { font-weight: 600; }
.igms-change.positive { color: #b81311; }
.igms-change.negative { color: #0061a4; }

/* ── Popup Footer / Close ──────────────────────────────────────────────────── */
.popup-footer {
  margin-top: 16px;
  display: flex;
  justify-content: center;
}
.popup-close-action {
  padding: 10px 28px;
  border-radius: 9999px;
  background: #e6e8ea;
  color: #404752;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: none;
  font-family: 'Plus Jakarta Sans', sans-serif;
  transition: background 0.2s;
}
.popup-close-action:hover { background: #d8dadc; }

/* ── Loading State ─────────────────────────────────────────────────────────── */
.popup-loading {
  text-align: center;
  padding: 24px 0;
  color: #707883;
  font-size: 14px;
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/css/dashboard.css
git commit -m "feat: add popup CSS for anchored day detail card"
```

---

### Task 5: Rewrite `calendar.js` — popup positioning and rendering

**Files:**
- Modify: `dashboard/static/js/calendar.js`

- [ ] **Step 1: Replace `openDayModal` function with `openDayPopup`**

Replace lines 205-231 (`async function openDayModal(...)`) with:

```javascript
/* ── Popup state ──────────────────────────────────────────────── */
let popupState = { open: false, activeDate: null, activeCell: null };

function repositionOnScroll() {
  if (popupState.open && popupState.activeCell) {
    positionPopup(popupState.activeCell);
  }
}
function repositionOnResize() {
  if (popupState.open && popupState.activeCell) {
    positionPopup(popupState.activeCell);
  }
}

function positionPopup(cellElement) {
  const layer = document.getElementById('day-popup-layer');
  const card = document.getElementById('day-popup-card');
  if (!layer || !card) return;

  const GAP = 12;
  const MARGIN = 16;
  const viewportW = window.innerWidth;
  const viewportH = window.innerHeight;

  if (viewportW < 768) return; // Mobile: CSS handles centering

  const cellRect = cellElement.getBoundingClientRect();

  // Temporarily position to measure
  card.style.visibility = 'hidden';
  card.style.position = 'absolute';
  card.style.left = '0';
  card.style.top = '0';
  card.style.width = 'min(400px, 90vw)';
  card.classList.remove('popup-mobile');
  document.body.appendChild(card);
  const cardH = card.offsetHeight;
  const cardW = card.offsetWidth;
  layer.appendChild(card);

  const placeRight = cellRect.right + GAP + cardW <= viewportW - MARGIN;
  let left, top;

  if (placeRight) {
    left = cellRect.right + GAP;
  } else {
    left = cellRect.left - GAP - cardW;
  }

  top = Math.max(MARGIN, Math.min(cellRect.top, viewportH - cardH - MARGIN));

  card.style.visibility = 'visible';
  card.style.left = left + 'px';
  card.style.top = top + 'px';
}

async function openDayPopup(date, propertyUid, currentPrice, cellElement) {
  // Store cell reference for repositioning
  popupState = { open: true, activeDate: date, activeCell: cellElement };

  const layer = document.getElementById('day-popup-layer');
  const popupDate = document.getElementById('popup-date');
  const popupProp = document.getElementById('popup-property');
  const popupContent = document.getElementById('popup-content');
  const popupFooter = document.getElementById('popup-footer');

  if (!layer) return;

  // Show layer
  layer.classList.remove('hidden');
  layer.classList.add('open');

  // Loading state
  if (popupDate) popupDate.textContent = date;
  if (popupProp) popupProp.textContent = 'Loading…';
  if (popupContent) popupContent.innerHTML = '<div class="popup-loading">Loading…</div>';
  if (popupFooter) popupFooter.innerHTML = '';

  // Wire close button
  const closeBtn = document.getElementById('popup-close-btn');
  if (closeBtn) {
    closeBtn.onclick = closeDayPopup;
  }

  // Add scroll/resize listeners
  window.addEventListener('scroll', repositionOnScroll, { passive: true });
  window.addEventListener('resize', repositionOnResize, { passive: true });

  // Position immediately (before content loads)
  positionPopup(cellElement);

  // Fetch detail
  try {
    const detail = await api.get(`/api/days/${date}?property_uid=${propertyUid}`);
    renderDayDetailPopup(detail, currentPrice);
    // Reposition after content renders (height may have changed)
    positionPopup(cellElement);
  } catch (e) {
    if (popupContent) {
      popupContent.innerHTML = '<div class="popup-loading" style="color:#ba1a1a;">Failed to load day details.</div>';
    }
    console.error("Failed to load day detail:", e);
  }
}

function closeDayPopup() {
  const layer = document.getElementById('day-popup-layer');
  if (layer) {
    layer.classList.add('hidden');
    layer.classList.remove('open');
  }
  popupState = { open: false, activeDate: null, activeCell: null };
  window.removeEventListener('scroll', repositionOnScroll);
  window.removeEventListener('resize', repositionOnResize);
}
```

- [ ] **Step 2: Replace `renderDayDetail` function with `renderDayDetailPopup`**

Replace lines 233-273 (`function renderDayDetail(...)`) with:

```javascript
function renderDayDetailPopup(detail, currentPrice) {
  const popupDate = document.getElementById('popup-date');
  const popupProp = document.getElementById('popup-property');
  const popupContent = document.getElementById('popup-content');
  const popupFooter = document.getElementById('popup-footer');
  if (!popupContent || !popupDate || !popupProp || !popupFooter) return;

  const finalPrice = detail.final_recommended || detail.final_price || 0;
  const igmsPrice = detail.current_igms_price || currentPrice || null;
  const baseRate = detail.base_rate || 0;

  // Header
  if (popupDate) popupDate.textContent = detail.date;

  let deltaBadge = '';
  if (igmsPrice != null && igmsPrice > 0) {
    const delta = finalPrice - igmsPrice;
    const sign = delta >= 0 ? '+' : '';
    const cls = delta >= 0 ? 'positive' : 'negative';
    deltaBadge = `<span class="delta-chip ${cls}">${sign}$${Math.abs(delta).toFixed(0)} vs iGMS</span>`;
  }
  if (popupProp) {
    popupProp.innerHTML = `<span class="popup-final-price">$${finalPrice.toFixed(0)}</span> ${deltaBadge}`;
  }

  // Build breakdown HTML
  const ladder = detail.adjustment_ladder || [];
  let breakdownRows = '';

  // Use new ladder if available
  if (ladder.length > 0) {
    for (const item of ladder) {
      const isPos = item.amount >= 0;
      const cls = isPos ? 'positive' : 'negative';
      const sign = isPos ? '+' : '-';
      breakdownRows += `
        <div class="breakdown-row ${cls}">
          <span class="row-label">${item.label}</span>
          <span class="row-amount">${sign}$${Math.abs(item.amount).toFixed(2)}</span>
        </div>`;
    }
    breakdownRows += `
        <div class="subtotal-row">
          <span class="row-label">Subtotal</span>
          <span class="row-amount">$${detail.subtotal_before_blend?.toFixed(2) || finalPrice.toFixed(2)}</span>
        </div>`;
  } else {
    // Fallback: compute from raw_factors (backward compat)
    const s = detail.seasonal || {};
    const d = detail.demand || {};
    const base = baseRate;
    let running = base;
    const addRow = (label, mult) => {
      const amt = (mult - 1) * base;
      if (Math.abs(amt) < 0.01) return;
      running += amt;
      const isPos = amt >= 0;
      const cls = isPos ? 'positive' : 'negative';
      const sign = isPos ? '+' : '-';
      breakdownRows += `
        <div class="breakdown-row ${cls}">
          <span class="row-label">${label}</span>
          <span class="row-amount">${sign}$${Math.abs(amt).toFixed(2)}</span>
        </div>`;
    };
    addRow('Seasonality', s.effective_seasonal || 1.0);
    addRow('Day-of-week', s.dow_multiplier || 1.0);
    addRow('Demand', d.multiplier || 1.0);
    // Yield and competitor omitted in fallback (complex to reconstruct)
    breakdownRows += `
        <div class="subtotal-row">
          <span class="row-label">Subtotal</span>
          <span class="row-amount">$${running.toFixed(2)}</span>
        </div>`;
  }

  // Blend adjustment
  let blendHtml = '';
  const blendAmt = detail.blend_adjustment_amount || 0;
  if (Math.abs(blendAmt) >= 0.01) {
    const isPos = blendAmt >= 0;
    const cls = isPos ? 'positive' : 'negative';
    const sign = isPos ? '+' : '-';
    blendHtml = `
      <div class="blend-row ${cls}">
        <span class="row-label">Blend adjustment</span>
        <span class="row-amount">${sign}$${Math.abs(blendAmt).toFixed(2)}</span>
      </div>`;
  }

  // iGMS line
  let igmsHtml = '';
  if (igmsPrice != null && igmsPrice > 0) {
    const change = finalPrice - igmsPrice;
    const sign = change >= 0 ? '+' : '-';
    const cls = change >= 0 ? 'positive' : 'negative';
    igmsHtml = `
      <div class="igms-line">
        <span class="igms-label">Current iGMS</span>
        <span>
          <span class="igms-value">$${igmsPrice.toFixed(0)}</span>
          <span class="igms-change ${cls}">${sign}$${Math.abs(change).toFixed(0)} to push</span>
        </span>
      </div>`;
  }

  popupContent.innerHTML = `
    ${breakdownRows}
    ${blendHtml}
    <div class="final-row">
      <span class="row-label">Final recommended</span>
      <span class="row-amount">$${finalPrice.toFixed(2)}</span>
    </div>
    ${igmsHtml}`;

  popupFooter.innerHTML = `
    <button class="popup-close-action" onclick="closeDayPopup()">Close</button>`;
}
```

- [ ] **Step 3: Update `buildCell` to call `openDayPopup` instead of `openDayModal`**

Replace line 200:
```javascript
  cell.addEventListener("click", () => openDayModal(day.date, propertyUid, day.current_airbnb_price));
```
With:
```javascript
  cell.addEventListener("click", () => openDayPopup(day.date, propertyUid, day.current_airbnb_price, cell));
```

- [ ] **Step 4: Add outside-click handler on document**

Add after `closeDayPopup` definition:
```javascript
// Close on outside click (but not on calendar cell clicks)
document.addEventListener('click', (e) => {
  if (!popupState.open) return;
  const card = document.getElementById('day-popup-card');
  const closeBtn = document.getElementById('popup-close-btn');
  if (card && !card.contains(e.target) && !e.target.closest('.day-cell') && e.target !== closeBtn) {
    closeDayPopup();
  }
});

// Close on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && popupState.open) {
    closeDayPopup();
  }
});
```

- [ ] **Step 5: Update bottom-of-file window aliases (remove `closeDayModal` alias, keep `refreshCalendar`)**

Replace lines 309-313:
```javascript
// Wire up global nav functions (used by inline onclick in HTML)
window.prevMonth = prevMonth;
window.nextMonth = nextMonth;
window.closeDayModal = closeDayModal;
window.refreshCalendar = refreshCalendar;
```

With:
```javascript
// Wire up global nav functions (used by inline onclick in HTML)
window.prevMonth = prevMonth;
window.nextMonth = nextMonth;
window.refreshCalendar = refreshCalendar;
window.closeDayPopup = closeDayPopup;
window.openDayPopup = openDayPopup;
```

Also remove the duplicate `closeDayModal` function at lines 318-324.

- [ ] **Step 6: Commit**

```bash
git add dashboard/static/js/calendar.js
git commit -m "feat: implement anchored day popup with monetary breakdown rendering"
```

---

### Task 6: Verification

**Files:**
- No file changes; run verification commands.

- [ ] **Step 1: Type-check and lint**

Run: `python -m py_compile dashboard/models.py dashboard/engine_proxy.py dashboard/routes/day_detail.py`
Expected: No output (no errors)

- [ ] **Step 2: Run tests**

Run: `pytest tests/ -v --tb=short -q 2>&1 | head -50`
Expected: Pre-existing tests pass (no new regressions)

- [ ] **Step 3: Manual verification checklist**

- [ ] Load calendar page — ensure no console errors on init
- [ ] Click any day cell — popup appears anchored to cell
- [ ] Click day near left viewport edge — popup opens to the right of cell
- [ ] Click day near right viewport edge — popup opens to the left of cell
- [ ] Resize window — popup repositions correctly
- [ ] Click outside popup (not on a day cell) — popup closes
- [ ] Press Escape — popup closes
- [ ] Open popup, then click a different day cell — popup repositions and shows new content
- [ ] On mobile viewport (< 768px wide) — popup appears centered, not anchored
- [ ] Verify breakdown shows `Base`, signed adjustment rows, `Subtotal`, `Final recommended`
- [ ] Verify delta chip shows correct sign/color vs iGMS price

---

## Spec Coverage Check

| Spec Section | Task |
|-------------|------|
| Popup container + behavior (desktop anchored, mobile centered) | Task 3, 5 |
| No backdrop blur / full-screen dimmer | Task 3, 4 |
| Close on outside-click / Esc / button | Task 5 |
| Positioning logic (right/left, vertical clamp) | Task 5 |
| Scroll + resize reposition | Task 5 |
| Readability redesign (stacked breakdown, signed adjustments) | Task 5 |
| Base → adjustments → Subtotal → Blend → Final → iGMS line | Task 5 |
| API `adjustment_ladder`, `subtotal_before_blend`, etc. | Task 1, 2 |
| CSS visual system (tabular nums, positive/negative colors) | Task 4 |
| Mobile behavior (< 768px centered card) | Task 3, 4, 5 |
| Regression: calendar nav, push button, status pills | Verified in Task 6 |