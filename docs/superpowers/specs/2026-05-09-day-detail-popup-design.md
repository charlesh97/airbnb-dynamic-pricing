# Day Detail Popup — Readability + Anchored Side Popup

## Context

The current calendar day detail modal is a full-screen blurred overlay. Users cannot see the pricing math behind a recommendation at a glance. This spec replaces it with an anchored side popup on desktop (centered card on mobile) and restructures the content into a scannable monetary breakdown.

---

## 1. Popup Shell — Desktop

**File: `dashboard/templates/base.html`**

Replace the `#day-modal` full-screen overlay with two elements:

```html
<!-- Layer: pointer-events:none, fixed, full viewport, sits above grid -->
<div id="day-popup-layer" class="fixed inset-0 z-50 hidden pointer-events-none" aria-hidden="true">
  <!-- Card: pointer-events:auto, absolutely positioned -->
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

**Behavior:**
- `pointer-events:none` on layer; `pointer-events:auto` on card.
- No backdrop, no blur overlay.
- Outside-click on document closes popup.
- `Escape` key closes popup.
- `popup-close-btn` in header closes popup.

**Mobile (< 768px):**
- Card is centered via flexbox, `width: min(92vw, 420px)`.
- No backdrop, no scroll lock (optional: `position:fixed; overscroll-behavior:none`).

---

## 2. Popup Shell — Mobile Override

On `< 768px`, `#day-popup-layer` uses `display: flex; align-items: center; justify-content: center`. The card is centered and NOT absolutely positioned — it flows normally within a centered flex container.

---

## 3. Positioning Logic — Desktop

**File: `dashboard/static/js/calendar.js`**

Rename `openDayModal` → `openDayPopup`.

```javascript
let popupState = { open: false, activeDate: null, activeCell: null };

async function openDayPopup(date, propertyUid, currentPrice, cellElement) {
  // Store reference to clicked cell
  popupState = { open: true, activeDate: date, activeCell: cellElement };

  // Show layer
  document.getElementById('day-popup-layer').classList.remove('hidden');

  // Render loading state immediately
  const popupDate = document.getElementById('popup-date');
  const popupProp = document.getElementById('popup-property');
  const popupContent = document.getElementById('popup-content');
  if (popupDate) popupDate.textContent = date;
  if (popupProp) popupProp.textContent = 'Loading…';
  if (popupContent) popupContent.innerHTML = '<div class="popup-loading">Loading…</div>';

  // Fetch detail
  const detail = await api.get(`/api/days/${date}?property_uid=${propertyUid}`);
  renderDayDetailPopup(detail, currentPrice);

  // Position card
  positionPopup(cellElement);

  // Store cell for reposition on scroll/resize
  window.addEventListener('scroll', repositionOnScroll, { passive: true });
  window.addEventListener('resize', repositionOnResize, { passive: true });
}

function positionPopup(cellElement) {
  const layer = document.getElementById('day-popup-layer');
  const card = document.getElementById('day-popup-card');
  if (!layer || !card) return;

  const GAP = 12;
  const MARGIN = 16;
  const viewportW = window.innerWidth;
  const viewportH = window.innerHeight;

  const cellRect = cellElement.getBoundingClientRect();

  // Measure card
  card.style.visibility = 'hidden';
  card.style.position = 'absolute';
  card.style.left = '0';
  card.style.top = '0';
  card.style.width = 'min(400px, 90vw)';
  card.classList.remove('popup-mobile');
  document.body.appendChild(card);
  const cardH = card.offsetHeight;
  const cardW = card.offsetWidth;
  document.body.appendChild(layer);
  layer.appendChild(card);
  card.style.visibility = 'visible';

  const placeRight = cellRect.right + GAP + cardW <= viewportW - MARGIN;
  let left, top;

  if (placeRight) {
    left = cellRect.right + GAP;
  } else {
    left = cellRect.left - GAP - cardW;
  }

  top = Math.max(MARGIN, Math.min(cellRect.top, viewportH - cardH - MARGIN));

  card.style.left = left + 'px';
  card.style.top = top + 'px';
}
```

**Scroll/resize reposition:**

```javascript
function repositionOnScroll() { if (popupState.open && popupState.activeCell) positionPopup(popupState.activeCell); }
function repositionOnResize() { if (popupState.open && popupState.activeCell) positionPopup(popupState.activeCell); }
```

**Close:**

```javascript
function closeDayPopup() {
  document.getElementById('day-popup-layer').classList.add('hidden');
  popupState = { open: false, activeDate: null, activeCell: null };
  window.removeEventListener('scroll', repositionOnScroll);
  window.removeEventListener('resize', repositionOnResize);
}
```

**Outside-click handler on `document`:**

```javascript
document.addEventListener('click', (e) => {
  if (!popupState.open) return;
  const card = document.getElementById('day-popup-card');
  const layer = document.getElementById('day-popup-layer');
  if (card && !card.contains(e.target) && !e.target.closest('.day-cell')) {
    closeDayPopup();
  }
});
```

**Mobile breakpoint:** below `768px`, `positionPopup` centers the card via CSS class `popup-mobile` instead of absolute positioning.

---

## 4. Content Structure — Stacked Audit Layout

**File: `dashboard/static/js/calendar.js` — `renderDayDetailPopup(detail, currentPrice)`**

```
┌──────────────────────────────────────┐
│ [Header]                             │
│  May 25, 2026                  [X]   │
│  Final: $248  [+16 vs iGMS]          │
├──────────────────────────────────────┤
│ Pricing Breakdown                     │
│ Base:        $200                    │
│ Seasonality: +$12                    │
│ Day-of-week: +$8                     │
│ Demand:      +$15                    │
│ Event:        $0                     │
│ Yield:       +$10                    │
│ Competitor:   $0                     │
│ ─────────────────────────────────── │
│ Subtotal:    $245                    │
├──────────────────────────────────────┤
│ (Blend adjustment +$3)               │
│ Final recommended: $248              │
├──────────────────────────────────────┤
│ Current iGMS: $232                   │
│ Change to push: +$16                 │
├──────────────────────────────────────┤
│          [Close]                     │
└──────────────────────────────────────┘
```

**Section 1 — Header:**
- Large date, single line (not duplicated).
- Final recommended price in large bold text.
- Optional delta chip: `+$N` green / `-$N` red, vs current iGMS price.

**Section 2 — Breakdown block:**
- Left-aligned `label: $amount` rows.
- Positive adjustments show `+$N` in red/secondary.
- Negative adjustments show `-$N` in green.
- Zero/inactive rows omitted.
- After last adjustment: `─────` divider, then `Subtotal: $Z`.

**Section 3 — Blend block:**
- If `blend_adjustment_amount !== 0`: show `Blend adjustment: +$N` or `-$N`.
- Final recommended emphasized (largest weight, bold).

**Section 4 — Current iGMS line:**
- `Current iGMS: $C`
- `Change to push: +$D` or `-$D`.
- If `current_igms_price` unavailable, hide this block.

**Section 5 — Close button** (secondary prominence, bottom).

---

## 5. API Contract Extension

**File: `dashboard/routes/day_detail.py`**

The API payload from `GET /api/days/{date}` adds:

```json
{
  "adjustment_ladder": [
    { "key": "seasonality", "label": "Seasonality", "amount": 12.0, "running_total_after": 212.0 },
    { "key": "dow", "label": "Day-of-week", "amount": 8.0, "running_total_after": 220.0 }
  ],
  "subtotal_before_blend": 245.0,
  "blend_adjustment_amount": 3.0,
  "final_recommended": 248.0,
  "current_igms_price": 232.0
}
```

`adjustment_ladder` is ordered exactly as applied by the pricing engine.

**Backward compatibility:**
- If `adjustment_ladder` is absent, `renderDayDetailPopup` falls back to computing from `raw_factors` + existing multipliers (old payload).
- `final_recommended` maps to existing `final_price` if absent.

**Files to update:**
- `dashboard/routes/day_detail.py` — pass new fields through
- `dashboard/engine_proxy.py` — build `adjustment_ladder` in `_date_price_to_detail`
- `dashboard/models.py` — add new fields to `DayDetailResponse`

---

## 6. Data Calculation — `_date_price_to_detail`

Build `adjustment_ladder` from `all_factors` + `_explain` output:

```python
def _build_adjustment_ladder(af, base_price):
    ladder = []
    running = base_price

    ev = af.get("event", {})
    dm = af.get("demand", {})
    yt = af.get("yield", {})
    co = af.get("competitor", {})

    seasonal_amt = (ev.get("seasonal_multiplier", 1.0) - 1.0) * base_price
    if abs(seasonal_amt) >= 0.01:
        running += seasonal_amt
        ladder.append({ "key": "seasonality", "label": "Seasonality",
                        "amount": round(seasonal_amt, 2), "running_total_after": round(running, 2) })

    dow_amt = (ev.get("dow_multiplier", 1.0) - 1.0) * base_price
    if abs(dow_amt) >= 0.01:
        running += dow_amt
        ladder.append({ "key": "dow", "label": "Day-of-week",
                        "amount": round(dow_amt, 2), "running_total_after": round(running, 2) })

    demand_amt = (dm.get("demand_multiplier", 1.0) - 1.0) * base_price
    if abs(demand_amt) >= 0.01:
        running += demand_amt
        ladder.append({ "key": "demand", "label": "Demand",
                        "amount": round(demand_amt, 2), "running_total_after": round(running, 2) })

    yield_amt = (yt.get("final_multiplier", 1.0) - 1.0) * base_price
    if abs(yield_amt) >= 0.01:
        running += yield_amt
        ladder.append({ "key": "yield", "label": "Yield",
                        "amount": round(yield_amt, 2), "running_total_after": round(running, 2) })

    competitor_amt = (co.get("adjustment_factor", 1.0) - 1.0) * base_price
    if abs(competitor_amt) >= 0.01:
        running += competitor_amt
        ladder.append({ "key": "competitor", "label": "Competitor",
                        "amount": round(competitor_amt, 2), "running_total_after": round(running, 2) })

    return ladder
```

`subtotal_before_blend` = last `running_total_after` in ladder (or `base_price` if no adjustments).
`blend_adjustment_amount` = `final_price - subtotal_before_blend`.
`final_recommended` = `final_price`.

---

## 7. CSS Visual System

**File: `dashboard/static/css/dashboard.css`**

```css
/* ── Popup Layer ─────────────────────────────────────── */
#day-popup-layer {
  pointer-events: none;
}
#day-popup-layer.open {
  pointer-events: auto;
}

/* ── Popup Card ──────────────────────────────────────── */
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

/* Mobile centered */
@media (max-width: 767px) {
  #day-popup-layer {
    display: flex;
    align-items: center;
    justify-content: center;
  }
  #day-popup-layer.open { display: flex; }
  .day-popup-card {
    position: static;
    width: min(92vw, 420px);
    margin: auto;
  }
}

/* ── Popup Header ───────────────────────────────────── */
.popup-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
}
.popup-date-text {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 24px;
  font-weight: 600;
  color: #191c1e;
  letter-spacing: -0.01em;
  line-height: 32px;
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

/* ── Final Price Header ──────────────────────────────── */
.popup-final-price {
  font-family: 'Hanken Grotesk', sans-serif;
  font-size: 28px;
  font-weight: 700;
  color: #191c1e;
  letter-spacing: 0.02em;
}
.delta-chip {
  display: inline-block;
  font-size: 13px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 9999px;
  margin-left: 8px;
}
.delta-chip.positive {
  background: #ffdad5;
  color: #930005;
}
.delta-chip.negative {
  background: #d1e4ff;
  color: #00497d;
}

/* ── Breakdown Block ────────────────────────────────── */
.breakdown-block {
  margin-top: 16px;
}
.breakdown-block-title {
  font-size: 11px;
  font-weight: 700;
  color: #707883;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 8px;
}
.breakdown-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  font-size: 14px;
  border-bottom: 1px dashed #eceef0;
  font-variant-numeric: tabular-nums;
}
.breakdown-row:last-child { border-bottom: none; }
.breakdown-row .row-label {
  color: #404752;
  font-weight: 400;
}
.breakdown-row .row-amount {
  font-weight: 600;
  color: #191c1e;
}
.breakdown-row.positive .row-amount { color: #b81311; }
.breakdown-row.negative .row-amount { color: #0061a4; }
.subtotal-row {
  display: flex;
  justify-content: space-between;
  padding: 10px 0 4px;
  font-size: 14px;
  font-weight: 700;
  border-top: 2px solid #191c1e;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}
.final-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 12px 0 8px;
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.final-row .row-label { color: #191c1e; }
.final-row .row-amount {
  color: #0061a4;
  font-family: 'Hanken Grotesk', sans-serif;
  font-size: 24px;
  font-weight: 700;
}

/* ── Blend row ──────────────────────────────────────── */
.blend-row {
  font-size: 13px;
  color: #707883;
  padding: 4px 0;
  font-variant-numeric: tabular-nums;
}
.blend-row.positive .row-amount { color: #b81311; }
.blend-row.negative .row-amount { color: #0061a4; }

/* ── Current iGMS line ──────────────────────────────── */
.igms-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  border-top: 1px solid #e0e3e5;
  margin-top: 8px;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.igms-line .igms-label { color: #707883; }
.igms-line .igms-value { font-weight: 600; color: #191c1e; }
.igms-change {
  font-weight: 600;
  font-size: 12px;
}
.igms-change.positive { color: #b81311; }
.igms-change.negative { color: #0061a4; }

/* ── Footer / Close Button ──────────────────────────── */
.popup-footer {
  margin-top: 16px;
  display: flex;
  justify-content: center;
}
.popup-close-action {
  padding: 10px 24px;
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

/* ── Loading ────────────────────────────────────────── */
.popup-loading {
  text-align: center;
  padding: 24px 0;
  color: #707883;
  font-size: 14px;
}
```

---

## 8. Files Changed

| File | Change |
|------|--------|
| `dashboard/templates/base.html` | Replace `#day-modal` with `#day-popup-layer` + `#day-popup-card` |
| `dashboard/static/js/calendar.js` | Rename fns, implement anchor positioning, full popup renderer |
| `dashboard/static/css/dashboard.css` | Add popup CSS classes |
| `dashboard/routes/day_detail.py` | No structural change, data flows from proxy |
| `dashboard/engine_proxy.py` | Build `adjustment_ladder` + new fields in `_date_price_to_detail` |
| `dashboard/models.py` | Add new fields to `DayDetailResponse` |

---

## 9. Regression Scope

- Calendar month navigation (prev/next) — unchanged.
- Push to iGMS button — unchanged.
- Status pills / fetch status — unchanged.
- Property switcher — unchanged.
- Config editor — unchanged.
- No console errors on open/close/reposition.
- No layout shift on calendar grid when popup opens/closes.