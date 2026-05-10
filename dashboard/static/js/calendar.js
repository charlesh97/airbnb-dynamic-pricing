/* ── calendar.js — Month grid rendering, prev/next nav, modal day detail ───── */

import { api } from "./api.js";

const DEFAULT_PROPERTY = "731418607849470882";
const MONTH_NAMES = ["","January","February","March","April","May","June","July","August","September","October","November","December"];

let currentYear = new Date().getFullYear();
let currentMonth = new Date().getMonth() + 1;
let currentUid = DEFAULT_PROPERTY;
let popupState = { open: false, activeDate: null, activeCell: null };
let scrollHandler = null;
let resizeHandler = null;

function fmtUsd(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "$0";
  const abs = Math.round(Math.abs(n));
  return n < 0 ? `-$${abs}` : `$${abs}`;
}

function fmtPacificTime(value) {
  const t = value ? new Date(value) : new Date();
  return t.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/Los_Angeles",
    timeZoneName: "short",
  });
}

function updateFetchStatus(state, meta) {
  const el = document.getElementById("fetch-status");
  const dot = document.getElementById("fetch-dot");
  const timeEl = document.getElementById("fetch-time");
  if (!el || !dot || !timeEl) return;
  if (state === "ok" && meta) {
    dot.className = "w-2 h-2 rounded-full bg-green-500 flex-shrink-0";
    timeEl.textContent = fmtPacificTime(meta.pulled_at);
  } else if (state === "ok") {
    dot.className = "w-2 h-2 rounded-full bg-green-500 flex-shrink-0";
    timeEl.textContent = fmtPacificTime();
  } else if (state === "partial") {
    dot.className = "w-2 h-2 rounded-full bg-amber-500 flex-shrink-0";
    timeEl.textContent = `Partial ${fmtPacificTime(meta?.pulled_at)}`;
  } else {
    dot.className = "w-2 h-2 rounded-full bg-red-500 flex-shrink-0";
    timeEl.textContent = "Failed";
  }
  el.style.opacity = "1";
}

function renderSyncBanner(sync, days) {
  const banner = document.getElementById("igms-sync-banner");
  if (!banner) return;

  banner.classList.add("hidden");
  banner.className = "mb-4 hidden rounded-xl border px-4 py-3 text-sm font-medium";
  banner.textContent = "";

  if (!sync) return;

  if (!sync.igms_pull_success) {
    banner.classList.remove("hidden");
    banner.classList.add("bg-red-50", "border-red-300", "text-red-800");
    banner.textContent = sync.igms_error
      ? `iGMS sync failed: ${sync.igms_error}`
      : "iGMS sync failed. Live prices may be unavailable.";
    return;
  }

  const totalDays = Array.isArray(days) ? days.length : 0;
  const nonClosedDays = Array.isArray(days)
    ? days.filter((d) => d.live_price_status !== "closed").length
    : totalDays;
  if (nonClosedDays > 0 && sync.igms_price_count < nonClosedDays) {
    banner.classList.remove("hidden");
    banner.classList.add("bg-amber-50", "border-amber-300", "text-amber-800");
    banner.textContent = "Partial iGMS data; some days may be unavailable.";
  }
}

function refreshCalendar() {
  loadMonth(currentYear, currentMonth);
}

function getPropertyUid() {
  return localStorage.getItem("atlas_property_uid") || DEFAULT_PROPERTY;
}

function initCalendar() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("year")) currentYear = parseInt(params.get("year"));
  if (params.has("month")) currentMonth = parseInt(params.get("month"));
  if (params.has("property_uid")) {
    currentUid = params.get("property_uid");
    localStorage.setItem("atlas_property_uid", currentUid);
  } else {
    currentUid = getPropertyUid();
  }

  const switcher = document.getElementById("property-switcher");
  if (switcher) switcher.value = currentUid;

  if (params.get('refreshed') === '1') {
    const banner = document.getElementById('config-updated-banner');
    if (banner) {
      banner.style.display = 'flex';
      setTimeout(() => banner.remove(), 3500);
    }
    const newUrl = new URL(window.location);
    newUrl.searchParams.delete('refreshed');
    window.history.replaceState(null, '', newUrl);
  }

  loadMonth(currentYear, currentMonth);
}

async function loadMonth(year, month) {
  currentYear = year;
  currentMonth = month;
  const propertyUid = currentUid || getPropertyUid();

  const url = new URL(window.location);
  url.searchParams.set("year", year);
  url.searchParams.set("month", month);
  url.searchParams.set("property_uid", propertyUid);
  window.history.replaceState(null, "", url);

  const monthName = document.getElementById("month-name");
  const yearLabel = document.getElementById("year-label");
  if (monthName) monthName.textContent = MONTH_NAMES[month];
  if (yearLabel) yearLabel.textContent = year + " Overview";

  try {
    const data = await api.get(`/api/calendar/${year}/${month}?property_uid=${propertyUid}`);
    const sync = data.sync || null;
    const days = data.days || [];
    const missingLiveDays = days.filter((d) => d.live_price_status === "missing" || d.live_price_status === "error").length;
    const isPartial = Boolean(sync?.igms_pull_success) && missingLiveDays > 0;
    const statusState = !sync || sync.igms_pull_success
      ? (isPartial ? "partial" : "ok")
      : "error";
    updateFetchStatus(statusState, sync);
    renderSyncBanner(sync, data.days || []);
    window._calendarBookings = data.bookings || [];
    renderGrid(days, propertyUid);
  } catch (e) {
    updateFetchStatus("error");
    renderSyncBanner(
      {
        igms_pull_success: false,
        igms_error: "Calendar API request failed.",
      },
      [],
    );
    console.error("Failed to load calendar:", e);
  }
}

function prevMonth() {
  currentMonth--;
  if (currentMonth < 1) { currentMonth = 12; currentYear--; }
  loadMonth(currentYear, currentMonth);
}

function nextMonth() {
  currentMonth++;
  if (currentMonth > 12) { currentMonth = 1; currentYear++; }
  loadMonth(currentYear, currentMonth);
}

function renderGrid(days, propertyUid) {
  const grid = document.getElementById("days-grid");
  if (!grid) return;
  grid.innerHTML = "";

  // Precompute booking span map for fast lookup
  const bookingSpanMap = {};
  if (window._calendarBookings) {
    for (const span of window._calendarBookings) {
      const checkin = span.checkin;
      const checkout = span.checkout;
      const checkinDate = new Date(checkin + 'T00:00:00');
      const checkoutDate = new Date(checkout + 'T00:00:00');
      const current = new Date(checkinDate);
      while (current < checkoutDate) {
        const dateStr = current.toISOString().split('T')[0];
        bookingSpanMap[dateStr] = span;
        current.setDate(current.getDate() + 1);
      }
    }
  }

  sessionStorage.setItem('atlas_calendar_view', JSON.stringify({ year: currentYear, month: currentMonth }));

  const firstDay = new Date(currentYear, currentMonth - 1, 1).getDay();
  for (let i = 0; i < firstDay; i++) {
    const empty = document.createElement("div");
    empty.className = "day-cell empty";
    grid.appendChild(empty);
  }

  days.forEach(day => {
    const cell = buildCell(day, propertyUid, bookingSpanMap);
    grid.appendChild(cell);
  });

  const totalCells = firstDay + days.length;
  const remaining = (7 - (totalCells % 7)) % 7;
  for (let i = 0; i < remaining; i++) {
    const empty = document.createElement("div");
    empty.className = "day-cell empty";
    grid.appendChild(empty);
  }
}

function buildCell(day, propertyUid, bookingSpanMap = {}) {
  const cell = document.createElement("div");
  cell.className = "day-cell";
  const isBookingWindowClosed = day.blocked_reason === "booking_window_closed";
  const isUnavailable = day.is_available === false;
  if (isUnavailable) {
    cell.classList.add("unavailable");
  }
  if (isBookingWindowClosed) {
    cell.classList.add("booking-window-closed");
  }

  // Check for booking membership from precomputed map
  const cellBooking = bookingSpanMap[day.date] || null;

  const dayNum = day.date.split("-")[2].replace(/^0/, "");

  // Top row: day number + holiday/demand dot
  const topRow = document.createElement("div");
  topRow.className = "flex justify-between items-start";

  const numSpan = document.createElement("span");
  numSpan.className = "day-cell-num";
  numSpan.textContent = dayNum;
  topRow.appendChild(numSpan);

  // Dot indicator (holiday = red, high-demand = blue)
  if (day.is_holiday) {
    const dot = document.createElement("span");
    dot.className = "holiday-dot";
    topRow.appendChild(dot);
  } else if (day.match_status === "oversell") {
    const dot = document.createElement("span");
    dot.className = "demand-dot";
    topRow.appendChild(dot);
  }

  cell.appendChild(topRow);

  const livePriceRaw = Number(day.current_airbnb_price);
  const livePrice = Number.isFinite(livePriceRaw) && livePriceRaw > 0
    ? livePriceRaw
    : null;
  const proposedPriceRaw = Number(day.final_price);
  const proposedPrice = Number.isFinite(proposedPriceRaw) ? proposedPriceRaw : null;
  const proposedDelta = livePrice != null && proposedPrice != null ? proposedPrice - livePrice : null;
  const hasProposedChange = !isUnavailable && (
    Boolean(day.has_proposed_change) || (proposedDelta != null && Math.abs(proposedDelta) >= 0.01)
  );

  // Bottom: primary live iGMS price
  const priceDiv = document.createElement("div");
  priceDiv.className = "day-cell-price";
  priceDiv.textContent = livePrice != null ? fmtUsd(livePrice) : "—";
  cell.appendChild(priceDiv);

  if (isUnavailable) {
    const unavailable = document.createElement("div");
    unavailable.className = "igms-missing-label";
    unavailable.textContent = isBookingWindowClosed ? "Outside booking window" : "Unavailable";
    cell.appendChild(unavailable);
  } else if (livePrice == null) {
    const missing = document.createElement("div");
    missing.className = "igms-missing-label";
    missing.textContent = "iGMS missing";
    cell.appendChild(missing);
  }

    if (!isUnavailable && hasProposedChange && proposedPrice != null) {
    const proposedRow = document.createElement("div");
    proposedRow.className = "proposed-row";

    const badge = document.createElement("span");
    badge.className = "new-badge";
    badge.textContent = "NEW";
    proposedRow.appendChild(badge);

    const proposedText = document.createElement("span");
    proposedText.className = "proposed-text";
    proposedText.textContent = `Proposed ${fmtUsd(proposedPrice)}`;
    proposedRow.appendChild(proposedText);

    if (proposedDelta != null) {
      const delta = document.createElement("span");
      delta.className = "proposed-delta";
      delta.textContent = `${proposedDelta >= 0 ? "+" : "-"}${fmtUsd(Math.abs(proposedDelta))}`;
      proposedRow.appendChild(delta);
    }
    cell.appendChild(proposedRow);
  } else if (!isUnavailable && !isBookingWindowClosed && livePrice == null && proposedPrice != null) {
    const proposedOnly = document.createElement("div");
    proposedOnly.className = "proposed-only-label";
    proposedOnly.textContent = `Proposed ${fmtUsd(proposedPrice)} (advisory)`;
    cell.appendChild(proposedOnly);
  }

  // Holiday label below day number
  if (day.is_holiday) {
    const holidayLabel = document.createElement("span");
    holidayLabel.className = "holiday-label";
    holidayLabel.textContent = day.holiday_name || "Holiday";
    cell.appendChild(holidayLabel);
  }

  // Render booking bar if this date is within a booking span
  if (cellBooking) {
    const stayBar = document.createElement("div");
    stayBar.className = "booking-stay-bar";
    stayBar.textContent = cellBooking.label || "";
    cell.appendChild(stayBar);
  }

  if (!isBookingWindowClosed) {
    cell.addEventListener("click", () => openDayPopup(day.date, propertyUid, day.current_airbnb_price, cell, day.blocked_reason));
  }

  return cell;
}

async function openDayPopup(date, propertyUid, currentPrice, cellElement, blockedReason = null) {
  if (blockedReason === "booking_window_closed") return;
  const layer = document.getElementById("day-popup-layer");
  const card = document.getElementById("day-popup-card");
  if (!layer || !card) return;

  if (scrollHandler) {
    window.removeEventListener("scroll", scrollHandler);
    scrollHandler = null;
  }
  if (resizeHandler) {
    window.removeEventListener("resize", resizeHandler);
    resizeHandler = null;
  }

  popupState = { open: true, activeDate: date, activeCell: cellElement };

  layer.classList.remove("hidden");
  layer.classList.add("open");

  const popupDate = document.getElementById("popup-date");
  const popupProp = document.getElementById("popup-property");
  const popupContent = document.getElementById("popup-content");
  const popupFooter = document.getElementById("popup-footer");

  if (popupDate) popupDate.textContent = date;
  if (popupProp) popupProp.textContent = "Loading…";
  if (popupContent) popupContent.innerHTML = '<div class="text-center py-12 text-on-surface-variant font-body-md">Loading…</div>';
  if (popupFooter) popupFooter.innerHTML = "";

  const closeBtn = document.getElementById("popup-close-btn");
  if (closeBtn) closeBtn.onclick = closeDayPopup;

  scrollHandler = repositionOnScroll;
  resizeHandler = repositionOnResize;
  window.addEventListener("scroll", scrollHandler, { passive: true });
  window.addEventListener("resize", resizeHandler);

  positionPopup(cellElement);

  try {
    const detail = await api.get(`/api/days/${date}?property_uid=${propertyUid}`);
    if (detail?.blocked_reason === "booking_window_closed") {
      closeDayPopup();
      return;
    }
    renderDayDetailPopup(detail, currentPrice);
    positionPopup(cellElement);
  } catch (e) {
    const popupContent = document.getElementById("popup-content");
    if (popupContent) popupContent.innerHTML = '<div class="text-center py-12 text-secondary font-body-md">Failed to load day details.</div>';
    console.error("Failed to load day detail:", e);
  }
}

function positionPopup(cellElement) {
  const card = document.getElementById("day-popup-card");
  const layer = document.getElementById("day-popup-layer");
  if (!card || !layer) return;

  if (window.innerWidth < 768) return;

  const GAP = 12;
  const MARGIN = 16;
  const viewportW = window.innerWidth;
  const viewportH = window.innerHeight;

  const cellRect = cellElement.getBoundingClientRect();

  card.style.visibility = "hidden";
  card.style.left = "0";
  card.style.top = "0";
  card.style.right = "auto";
  card.style.bottom = "auto";
  layer.classList.remove("hidden");
  const cardH = card.offsetHeight;
  const cardW = card.offsetWidth;

  const placeRight = cellRect.right + GAP + cardW <= viewportW - MARGIN;
  let left, top;
  if (placeRight) {
    left = cellRect.right + GAP;
  } else {
    left = cellRect.left - GAP - cardW;
  }
  top = Math.max(MARGIN, Math.min(cellRect.top, viewportH - cardH - MARGIN));

  card.style.left = left + "px";
  card.style.top = top + "px";
  card.style.visibility = "visible";
}

function closeDayPopup() {
  if (scrollHandler) {
    window.removeEventListener("scroll", scrollHandler);
    scrollHandler = null;
  }
  if (resizeHandler) {
    window.removeEventListener("resize", resizeHandler);
    resizeHandler = null;
  }
  popupState = { open: false, activeDate: null, activeCell: null };

  const layer = document.getElementById("day-popup-layer");
  if (layer) {
    layer.classList.add("hidden");
    layer.classList.remove("open");
  }
}

function repositionOnScroll() {
  if (!popupState.activeCell) return;
  if (popupState.open && popupState.activeCell) positionPopup(popupState.activeCell);
}

function repositionOnResize() {
  if (!popupState.activeCell) return;
  if (popupState.open && popupState.activeCell) positionPopup(popupState.activeCell);
}

document.addEventListener('click', (e) => {
  if (!popupState.open) return;
  const card = document.getElementById('day-popup-card');
  const closeBtn = document.getElementById('popup-close-btn');
  if (card && !card.contains(e.target) && !e.target.closest('.day-cell') && e.target !== closeBtn) {
    closeDayPopup();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && popupState.open) {
    closeDayPopup();
  }
});

function renderDayDetailPopup(detail, currentPrice) {
  const popupDate = document.getElementById("popup-date");
  const popupProp = document.getElementById("popup-property");
  const popupContent = document.getElementById("popup-content");
  const popupFooter = document.getElementById("popup-footer");

  const finalPrice = detail.final_recommended || detail.final_price || 0;
  const hasLive = detail.live_price_status === "ok" && typeof (detail.current_igms_price ?? currentPrice) === "number" && (detail.current_igms_price ?? currentPrice) > 0;
  const igmsPrice = hasLive ? (detail.current_igms_price ?? currentPrice) : null;

  if (popupDate) popupDate.textContent = detail.date;

  const hasProposedChange = detail.is_available !== false && (
    Boolean(detail.has_proposed_change) || (igmsPrice != null && Math.abs(finalPrice - igmsPrice) >= 0.01)
  );
  if (popupProp) popupProp.textContent = "";

  const ladder = detail.adjustment_ladder || [];
  const baseRate = detail.base_rate || 0;
  let breakdownRows = `
    <div class="breakdown-row base-row">
      <span class="row-label">Base</span>
      <span class="row-amount">${fmtUsd(baseRate)}</span>
    </div>`;

  if (ladder.length > 0) {
    for (const item of ladder) {
      const isPos = item.amount >= 0;
      const cls = isPos ? "positive" : "negative";
      const sign = isPos ? "+" : "-";
      breakdownRows += `
        <div class="breakdown-row ${cls}">
          <span class="row-label">${item.label}</span>
          <span class="row-amount">${sign}${fmtUsd(Math.abs(item.amount))}</span>
        </div>`;
    }
    breakdownRows += `
        <div class="subtotal-row">
          <span class="row-label">Subtotal</span>
          <span class="row-amount">${fmtUsd(detail.subtotal_before_blend ?? finalPrice)}</span>
        </div>`;
  } else {
    const s = detail.seasonal || {};
    const d = detail.demand || {};
    const base = baseRate;
    let running = base;
    const addRow = (label, mult) => {
      const amt = (mult - 1) * base;
      if (Math.abs(amt) < 0.01) return;
      running += amt;
      const isPos = amt >= 0;
      const cls = isPos ? "positive" : "negative";
      const sign = isPos ? "+" : "-";
      breakdownRows += `
        <div class="breakdown-row ${cls}">
          <span class="row-label">${label}</span>
          <span class="row-amount">${sign}${fmtUsd(Math.abs(amt))}</span>
        </div>`;
    };
    addRow("Seasonality", s.effective_seasonal || 1.0);
    addRow("Day-of-week", s.dow_multiplier || 1.0);
    addRow("Demand", d.multiplier || 1.0);
    breakdownRows += `
        <div class="subtotal-row">
          <span class="row-label">Subtotal</span>
          <span class="row-amount">${fmtUsd(running)}</span>
        </div>`;
  }

  let blendHtml = "";
  const blendAmt = detail.blend_adjustment_amount || 0;
  if (Math.abs(blendAmt) >= 0.01) {
    const isPos = blendAmt >= 0;
    const cls = isPos ? "positive" : "negative";
    const sign = isPos ? "+" : "-";
    blendHtml = `
      <div class="blend-row ${cls}">
        <span class="row-label">Blend adjustment</span>
        <span class="row-amount">${sign}${fmtUsd(Math.abs(blendAmt))}</span>
      </div>`;
  }

  let igmsHtml = "";
  if (igmsPrice != null) {
    const change = finalPrice - igmsPrice;
    const sign = change >= 0 ? "+" : "-";
    const cls = change >= 0 ? "positive" : "negative";
    igmsHtml = `
      <div class="igms-line">
        <span class="igms-label">Current iGMS</span>
        <span>
          <span class="igms-value">${fmtUsd(igmsPrice)}</span>
          <span class="igms-change ${cls}">${sign}${fmtUsd(Math.abs(change))} to push</span>
        </span>
      </div>`;
  } else {
    const unavailableLabel = detail.live_price_status === "closed" ? "Outside booking window" : "Unavailable";
    igmsHtml = `
      <div class="igms-line igms-line-error">
        <span class="igms-label">Current iGMS</span>
        <span class="igms-value">${unavailableLabel}</span>
      </div>`;
  }

  const isBookingWindowClosed = detail.blocked_reason === "booking_window_closed";
  const proposedHeader = detail.is_available === false
    ? `<div class="popup-proposed-header"><span class="popup-proposed-price">Unavailable</span></div>`
    : isBookingWindowClosed
    ? `<div class="popup-proposed-header"><span class="popup-proposed-price">Outside booking window</span></div>`
    : hasProposedChange
      ? `<div class="popup-proposed-header"><span class="new-badge">NEW</span><span class="popup-proposed-price">Proposed ${fmtUsd(finalPrice)}</span></div>`
      : `<div class="popup-proposed-header"><span class="popup-proposed-price">Proposed ${fmtUsd(finalPrice)}</span></div>`;

  popupContent.innerHTML = `
    ${proposedHeader}
    ${breakdownRows}
    ${blendHtml}
    <div class="final-row">
      <span class="row-label">Final recommended</span>
      <span class="row-amount">${fmtUsd(finalPrice)}</span>
    </div>
    ${igmsHtml}`;

  popupFooter.innerHTML = `
    <button class="popup-close-action" onclick="closeDayPopup()">Close</button>`;
}

window.pushMonthToIGMS = async function() {
  const btn = document.getElementById("push-igms-btn");
  if (!btn) return;

  btn.disabled = true;
  btn.textContent = "Pushing…";

  try {
    const resp = await api.post(`/api/calendar/push`, {
      property_uid: currentUid || getPropertyUid(),
      year: currentYear,
      month: currentMonth,
    });
    const statusEl = document.getElementById("push-status");
    if (resp.success) {
      const count = resp.price_updates_sent || resp.pushed || 0;
      btn.textContent = `✓ Sent ${count}`;
      if (statusEl) statusEl.textContent = `Sent ${count} price updates`;
      setTimeout(() => { btn.textContent = "Push to iGMS"; btn.disabled = false; if (statusEl) statusEl.textContent = ""; }, 3000);
    } else {
      btn.textContent = "Failed";
      const errCount = resp.errors?.length || 0;
      if (statusEl) statusEl.textContent = `${errCount} error(s)`;
      console.error("Push errors:", resp.errors);
      setTimeout(() => { btn.textContent = "Push to iGMS"; btn.disabled = false; }, 3000);
    }
  } catch (e) {
    btn.textContent = "Error";
    console.error(e);
    setTimeout(() => { btn.textContent = "Push to iGMS"; btn.disabled = false; }, 3000);
  }
};

// Wire up global nav functions (used by inline onclick in HTML)
window.prevMonth = prevMonth;
window.nextMonth = nextMonth;
window.refreshCalendar = refreshCalendar;
window.closeDayPopup = closeDayPopup;
window.openDayPopup = openDayPopup;

// Auto-init when calendar page loads
initCalendar();
