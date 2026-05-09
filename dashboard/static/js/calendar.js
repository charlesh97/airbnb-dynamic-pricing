/* ── calendar.js — Month grid rendering, prev/next nav, modal day detail ───── */

import { api } from "./api.js";

const DEFAULT_PROPERTY = "731418607849470882";
const MONTH_NAMES = ["","January","February","March","April","May","June","July","August","September","October","November","December"];

let currentYear = new Date().getFullYear();
let currentMonth = new Date().getMonth() + 1;
let currentUid = DEFAULT_PROPERTY;
let lastFetchTime = null;
let lastFetchSuccess = false;

function updateFetchStatus(success) {
  lastFetchSuccess = success;
  const el = document.getElementById("fetch-status");
  const dot = document.getElementById("fetch-dot");
  const timeEl = document.getElementById("fetch-time");
  if (!el || !dot || !timeEl) return;
  if (success) {
    lastFetchTime = new Date();
    dot.className = "w-2 h-2 rounded-full bg-green-500 flex-shrink-0";
    const t = lastFetchTime;
    timeEl.textContent = t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    sessionStorage.setItem('last_fetch_time', lastFetchTime.getTime().toString());
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
  // Restore from URL params
  const params = new URLSearchParams(window.location.search);
  if (params.has("year")) currentYear = parseInt(params.get("year"));
  if (params.has("month")) currentMonth = parseInt(params.get("month"));
  if (params.has("property_uid")) {
    currentUid = params.get("property_uid");
    localStorage.setItem("atlas_property_uid", currentUid);
  } else {
    currentUid = getPropertyUid();
  }

  // Sync switcher
  const switcher = document.getElementById("property-switcher");
  if (switcher) switcher.value = currentUid;

  // Restore fetch status if we have a cached time
  const cached = sessionStorage.getItem('last_fetch_time');
  if (cached) {
    const t = new Date(parseInt(cached));
    const dot = document.getElementById("fetch-dot");
    const timeEl = document.getElementById("fetch-time");
    if (dot) dot.className = "w-2 h-2 rounded-full bg-green-500 flex-shrink-0";
    if (timeEl) timeEl.textContent = t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const el = document.getElementById("fetch-status");
    if (el) el.style.opacity = "1";
  }

  loadMonth(currentYear, currentMonth);
}

async function loadMonth(year, month) {
  currentYear = year;
  currentMonth = month;
  const propertyUid = currentUid || getPropertyUid();

  // Update URL
  const url = new URL(window.location);
  url.searchParams.set("year", year);
  url.searchParams.set("month", month);
  url.searchParams.set("property_uid", propertyUid);
  window.history.replaceState(null, "", url);

  // Update giant header
  const monthName = document.getElementById("month-name");
  const yearLabel = document.getElementById("year-label");
  if (monthName) monthName.textContent = MONTH_NAMES[month];
  if (yearLabel) yearLabel.textContent = year + " Overview";

  try {
    const data = await api.get(`/api/calendar/${year}/${month}?property_uid=${propertyUid}`);
    updateFetchStatus(true);
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

  // ── Clear stale old prices (older than 5 min) ──
  const ts = parseInt(sessionStorage.getItem('old_prices_timestamp') || '0');
  if (Date.now() - ts > 5 * 60 * 1000) {
    sessionStorage.removeItem('old_prices');
    sessionStorage.removeItem('old_prices_timestamp');
  }

  // ── Show refreshed banner and force reload if redirected from config save ──
  const params = new URLSearchParams(window.location.search);
  if (params.get('refreshed') === '1') {
    const banner = document.getElementById('config-updated-banner');
    if (banner) {
      banner.style.display = 'flex';
      setTimeout(() => banner.remove(), 3500);
    }
    // Clear param from URL bar and force a fresh fetch
    const newUrl = new URL(window.location);
    newUrl.searchParams.delete('refreshed');
    window.history.replaceState(null, '', newUrl);
    // Force fresh data by resetting any stale session cache
    sessionStorage.removeItem('last_fetch_time');
    loadMonth(currentYear, currentMonth);
  }

  // Figure out first day offset (Sun = 0)
  const firstDay = new Date(currentYear, currentMonth - 1, 1).getDay();
  // Offset cells before day 1
  for (let i = 0; i < firstDay; i++) {
    const empty = document.createElement("div");
    empty.className = "day-cell empty";
    grid.appendChild(empty);
  }

  days.forEach(day => {
    const cell = buildCell(day, propertyUid);
    grid.appendChild(cell);
  });

  // Pad to complete last row
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

  cell.addEventListener("click", () => openDayModal(day.date, propertyUid, day.current_airbnb_price));

  return cell;
}

async function openDayModal(date, propertyUid, currentPrice) {
  const modal = document.getElementById("day-modal");
  const modalContent = document.getElementById("modal-content");
  const modalDate = document.getElementById("modal-date");
  const modalProp = document.getElementById("modal-property");

  if (!modal || !modalContent) return;

  // Show modal frame
  modal.classList.remove("hidden");
  modal.classList.add("open");

  // Loading state
  if (modalDate) modalDate.textContent = date;
  if (modalProp) modalProp.textContent = "Loading…";
  modalContent.innerHTML = '<div class="text-center py-12 text-on-surface-variant font-body-md">Loading…</div>';

  try {
    const detail = await api.get(`/api/days/${date}?property_uid=${propertyUid}`);
    renderDayDetail(detail, currentPrice);
    if (modalDate) modalDate.textContent = detail.date;
    if (modalProp) modalProp.textContent = `Base: $${detail.base_rate?.toFixed(0)} | Final: $${detail.final_price?.toFixed(0)}`;
  } catch (e) {
    modalContent.innerHTML = '<div class="text-center py-12 text-secondary font-body-md">Failed to load day details.</div>';
    console.error("Failed to load day detail:", e);
  }
}

function renderDayDetail(detail, currentPrice) {
  const modalContent = document.getElementById("modal-content");
  if (!modalContent) return;

  const baseRate = detail.base_rate || 0;
  const finalPrice = detail.final_price || 0;
  const s = detail.seasonal || {};
  const d = detail.demand || {};
  
  // Get the key factors
  const seasonalMult = s.effective_seasonal || 1.0;
  const dowMult = s.dow_multiplier || 1.0;
  const demandMult = d.multiplier || 1.0;
  
  // Build modifier line
  const mods = [];
  if (seasonalMult !== 1.0) mods.push(`${seasonalMult.toFixed(2)}× season`);
  if (dowMult !== 1.0) mods.push(`${dowMult.toFixed(2)}× DOW`);
  if (demandMult !== 1.0) mods.push(`${demandMult.toFixed(2)}× demand`);
  if (d.last_minute?.active) mods.push(`last-min ${d.last_minute.discount}`);
  if (d.far_future?.active) mods.push(`far-future ${d.far_future.discount}`);
  const modLine = mods.length > 0 ? mods.join(" · ") : "no adjustments";

  modalContent.innerHTML = `
    <div class="text-center">
      <p class="text-label-sm text-on-surface-variant mb-1">${detail.date}</p>
      <p class="text-display-sm font-extrabold text-on-surface">$${finalPrice.toFixed(0)}</p>
      <p class="text-label-sm text-on-surface-variant mt-1">Base $${baseRate.toFixed(0)} · ${modLine}</p>
    </div>
    ${currentPrice != null ? `
    <div class="mt-3 text-center text-sm">
      <span class="text-on-surface-variant">Airbnb: </span>
      <span class="font-semibold text-on-surface">$${currentPrice.toFixed(0)}</span>
      <span class="ml-2 text-on-surface-variant">Delta: </span>
      <span class="font-semibold ${finalPrice > currentPrice ? "text-secondary" : "text-green-600"}">${finalPrice >= currentPrice ? "+" : ""}$${(finalPrice - currentPrice).toFixed(0)}</span>
    </div>` : ""}
    <div class="mt-4 flex justify-center gap-3">
      <button onclick="closeDayModal()" class="px-4 py-2 rounded-full bg-surface-container-high text-on-surface text-label-sm font-semibold hover:bg-surface-container-low transition-colors">Close</button>
    </div>
  `;
}

// Push to iGMS
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
    if (resp.success) {
      btn.textContent = `✓ Pushed ${resp.pushed}`;
      setTimeout(() => { btn.textContent = "Push to iGMS"; btn.disabled = false; }, 2000);
    } else {
      btn.textContent = "Failed";
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
window.closeDayModal = closeDayModal;

// Auto-init when calendar page loads
initCalendar();

function closeDayModal() {
  const modal = document.getElementById("day-modal");
  if (modal) {
    modal.classList.add("hidden");
    modal.classList.remove("open");
  }
}
