/* ── calendar.js — Month grid rendering, prev/next nav, modal day detail ───── */

import { api } from "./api.js";

const DEFAULT_PROPERTY = "731418607849470882";
const MONTH_NAMES = ["","January","February","March","April","May","June","July","August","September","October","November","December"];

let currentYear = new Date().getFullYear();
let currentMonth = new Date().getMonth() + 1;
let currentUid = DEFAULT_PROPERTY;
let lastFetchTime = null;
let lastFetchSuccess = false;
let popupState = { open: false, activeDate: null, activeCell: null };
let scrollHandler = null;
let resizeHandler = null;

function updateFetchStatus(success, meta) {
  const el = document.getElementById("fetch-status");
  const dot = document.getElementById("fetch-dot");
  const timeEl = document.getElementById("fetch-time");
  if (!el || !dot || !timeEl) return;
  if (success && meta) {
    const t = meta.pulled_at ? new Date(meta.pulled_at) : new Date();
    dot.className = "w-2 h-2 rounded-full bg-green-500 flex-shrink-0";
    timeEl.textContent = t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } else if (success) {
    const t = new Date();
    dot.className = "w-2 h-2 rounded-full bg-green-500 flex-shrink-0";
    timeEl.textContent = t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } else {
    dot.className = "w-2 h-2 rounded-full bg-red-500 flex-shrink-0";
    timeEl.textContent = "Failed";
  }
  el.style.opacity = "1";
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
    updateFetchStatus(true, data.sync || null);
    renderGrid(data.days, propertyUid);
  } catch (e) {
    updateFetchStatus(false);
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

  sessionStorage.setItem('atlas_calendar_view', JSON.stringify({ year: currentYear, month: currentMonth }));

  const ts = parseInt(sessionStorage.getItem('old_prices_timestamp') || '0');
  if (Date.now() - ts > 5 * 60 * 1000) {
    sessionStorage.removeItem('old_prices');
    sessionStorage.removeItem('old_prices_timestamp');
  }

  const firstDay = new Date(currentYear, currentMonth - 1, 1).getDay();
  for (let i = 0; i < firstDay; i++) {
    const empty = document.createElement("div");
    empty.className = "day-cell empty";
    grid.appendChild(empty);
  }

  days.forEach(day => {
    const cell = buildCell(day, propertyUid);
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

function buildCell(day, propertyUid) {
  const cell = document.createElement("div");
  cell.className = "day-cell";

  // ── Old price check ──
  const oldPricesRaw = sessionStorage.getItem('old_prices');
  const oldPrices = oldPricesRaw ? JSON.parse(oldPricesRaw) : {};
  const oldPrice = oldPrices[day.date];
  const hasChanged = oldPrice !== undefined && Math.abs(day.final_price - oldPrice) > 0.01;
  if (hasChanged) cell.classList.add("changed");

  const dayNum = day.date.split("-")[2].replace(/^0/, "");

  // Top row: day number + holiday/demand dot
  const topRow = document.createElement("div");
  topRow.className = "flex justify-between items-start";

  const numSpan = document.createElement("span");
  numSpan.className = "day-cell-num";
  numSpan.textContent = dayNum;
  topRow.appendChild(numSpan);

  // Old price — strikethrough top-right (only when price changed)
  if (hasChanged) {
    const oldEl = document.createElement("span");
    oldEl.className = "old-price";
    oldEl.textContent = "$" + oldPrice.toFixed(0);
    topRow.appendChild(oldEl);
  }

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

  // Bottom: price — red bold for changed/new prices
  const priceDiv = document.createElement("div");
  priceDiv.className = "day-cell-price" + (day.match_status === "oversell" ? " high-demand" : "");
  if (hasChanged) priceDiv.classList.add("new-price");
  priceDiv.textContent = "$" + day.final_price.toFixed(0);
  cell.appendChild(priceDiv);

  // Holiday label below day number
  if (day.is_holiday) {
    const holidayLabel = document.createElement("span");
    holidayLabel.className = "holiday-label";
    holidayLabel.textContent = day.holiday_name || "Holiday";
    cell.appendChild(holidayLabel);
  }

  cell.addEventListener("click", () => openDayPopup(day.date, propertyUid, day.current_airbnb_price, cell));

  return cell;
}

async function openDayPopup(date, propertyUid, currentPrice, cellElement) {
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
  layer.classList.add("hidden");

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
  const igmsPrice = detail.current_igms_price || currentPrice || null;

  if (popupDate) popupDate.textContent = detail.date;

  let deltaBadge = "";
  if (igmsPrice != null && igmsPrice > 0) {
    const delta = finalPrice - igmsPrice;
    const sign = delta >= 0 ? "+" : "";
    const cls = delta >= 0 ? "positive" : "negative";
    deltaBadge = `<span class="delta-chip ${cls}">${sign}$${Math.abs(delta).toFixed(0)} vs iGMS</span>`;
  }
  if (popupProp) {
    popupProp.innerHTML = `<span class="popup-final-price">$${finalPrice.toFixed(0)}</span> ${deltaBadge}`;
  }

  const ladder = detail.adjustment_ladder || [];
  let breakdownRows = "";

  if (ladder.length > 0) {
    for (const item of ladder) {
      const isPos = item.amount >= 0;
      const cls = isPos ? "positive" : "negative";
      const sign = isPos ? "+" : "-";
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
    const s = detail.seasonal || {};
    const d = detail.demand || {};
    const base = detail.base_rate || 0;
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
          <span class="row-amount">${sign}$${Math.abs(amt).toFixed(2)}</span>
        </div>`;
    };
    addRow("Seasonality", s.effective_seasonal || 1.0);
    addRow("Day-of-week", s.dow_multiplier || 1.0);
    addRow("Demand", d.multiplier || 1.0);
    breakdownRows += `
        <div class="subtotal-row">
          <span class="row-label">Subtotal</span>
          <span class="row-amount">$${running.toFixed(2)}</span>
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
        <span class="row-amount">${sign}$${Math.abs(blendAmt).toFixed(2)}</span>
      </div>`;
  }

  let igmsHtml = "";
  if (igmsPrice != null && igmsPrice > 0) {
    const change = finalPrice - igmsPrice;
    const sign = change >= 0 ? "+" : "-";
    const cls = change >= 0 ? "positive" : "negative";
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
