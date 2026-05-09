/* ── config_editor.js — Config field bindings, seasonal chart, save logic ────── */

import { api } from "./api.js";

function updateSaveStatus(success) {
  const el = document.getElementById("save-status");
  const dot = document.getElementById("save-dot");
  const timeEl = document.getElementById("save-time");
  if (!el || !dot || !timeEl) return;
  const t = new Date();
  if (success) {
    dot.className = "w-2 h-2 rounded-full bg-green-500 flex-shrink-0";
    timeEl.textContent = t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } else {
    dot.className = "w-2 h-2 rounded-full bg-red-500 flex-shrink-0";
    timeEl.textContent = "Failed";
  }
  el.style.opacity = "1";
}

const DEFAULT_PROPERTY = "731418607849470882";
const MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const DOW_KEYS = ["mon","tue","wed","thu","fri","sat","sun"];

// Format a multiplier decimal as a signed percentage string
// e.g., 1.05 → "+5%", 0.95 → "-5%", 1.0 → "±0%"
function fmtPct(v) {
  const pct = Math.round((parseFloat(v) - 1) * 100);
  if (pct > 0) return `+${pct}%`;
  if (pct < 0) return `${pct}%`;
  return `±0%`;
}

let currentPropertyUid = localStorage.getItem("atlas_property_uid") || DEFAULT_PROPERTY;

function getPropertyUid() {
  return currentPropertyUid;
}

let config = null;
let dirty = false;
let seasonalMonths = {};
let dragIndex = -1;

// ── Init ────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  const switcher = document.getElementById("property-switcher");
  if (switcher) {
    switcher.value = getPropertyUid();
    switcher.addEventListener("change", async () => {
      localStorage.setItem("atlas_property_uid", switcher.value);
      dirty = false;
      setDirty(false);
      await loadConfig(switcher.value);
    });
  }

  document.getElementById("save-config-btn")?.addEventListener("click", saveConfig);
  document.getElementById("renorm-btn")?.addEventListener("click", renormalizeWeights);
  document.getElementById("add-event-btn")?.addEventListener("click", addEventRow);

  const renormBtn = document.getElementById("renorm-btn");
  if (renormBtn) renormBtn.style.display = "none";

  // Preset buttons
  document.querySelectorAll(".preset-btn").forEach(btn => {
    btn.addEventListener("click", () => applyPreset(btn.dataset.preset));
  });



  await loadConfig(getPropertyUid());
});

async function loadConfig(uid) {
  try {
    config = await api.get(`/api/config/${uid}`);
    const nameDisplay = document.getElementById("property-name-display");
    if (nameDisplay) nameDisplay.textContent = config.name || uid;

    // Sync switcher
    const switcher = document.getElementById("property-switcher");
    if (switcher) switcher.value = uid;

    populateAllFields(config);
    setTimeout(drawSeasonalChart, 50);
  } catch (e) {
    console.error("Failed to load config:", e);
  }
}

// ── Field population ─────────────────────────────────────────────────────────
function populateAllFields(cfg) {
  // Base
  setVal("base_price", cfg.base_price ?? 200);
  setVal("min_price", cfg.min_price ?? 100);
  setVal("max_price", cfg.max_price ?? 800);

  // Strategy weights
  const sw = cfg.strategy_weights || {};
  setVal("wt-demand", sw.demand ?? 0.5);
  setVal("wt-event", sw.event ?? 0.375);
  setVal("wt-competitor", sw.competitor ?? 0);
  setVal("wt-yield", sw.yield ?? 0.125);
  updateWeightDisplay();

  // DOW multipliers
  const dow = cfg.dow_multipliers || {};
  DOW_KEYS.forEach(k => {
    setVal("dow-" + k, Math.round((dow[k] - 1) * 100));
  });

  // Demand config
  const dc = cfg.demand_config || {};
  setVal("demand_window_days", dc.demand_window_days ?? 14);
  setVal("velocity_window_days", dc.velocity_window_days ?? 7);
  // Far Future label simplified
  const ff = dc.far_future || {};
  setVal("ff-window", ff.window_days ?? 60);
  setVal("ff-discount", ff.discount ?? 0.9);
  const lm = dc.last_minute || {};
  setVal("lm-window", lm.window_days ?? 7);
  setVal("lm-discount", lm.discount ?? 0.92);

  // Availability
  const av = cfg.availability || {};
  setVal("booking_window_days", av.booking_window_days ?? 120);
  setVal("min_stay_default", av.min_stay?.default ?? 2);
  document.getElementById("block_day_before").checked = av.block_day_before ?? false;
  document.getElementById("block_day_after").checked = av.block_day_after ?? false;

  // Price adjustment
  const pa = document.getElementById("price_adjust");
  const paVal = document.getElementById("price-adjust-val");
  if (pa && cfg.price_adjust !== undefined) {
    pa.value = cfg.price_adjust;
    const pct = Math.round(parseFloat(cfg.price_adjust) * 100);
    if (paVal) paVal.textContent = pct >= 0 ? `+${pct}%` : `${pct}%`;
  }
  if (pa) pa.addEventListener("input", () => {
    const pct = Math.round(parseFloat(pa.value) * 100);
    if (paVal) paVal.textContent = pct >= 0 ? `+${pct}%` : `${pct}%`;
    markDirty();
  });

  // Seasonal months
  seasonalMonths = {};
  MONTH_NAMES.forEach((_, i) => {
    const key = String(i + 1).padStart(2, "0");
    seasonalMonths[key] = cfg.seasonal_months?.[key] ?? 1.0;
  });

  // Holiday buffer slope
  const slopeEl = document.getElementById("holiday_buffer_slope");
  const slopeValEl = document.getElementById("holiday_buffer_slope_val");
  if (slopeEl) {
    slopeEl.value = cfg.holiday_buffer_slope ?? 0.05;
    if (slopeValEl) slopeValEl.textContent = fmtPct(cfg.holiday_buffer_slope ?? 0.05);
  }

  // Holiday buffer days
  const hbDays = document.getElementById("holiday_buffer_days");
  const hbSlope = document.getElementById("holiday_buffer_slope");
  const hbDaysVal = document.getElementById("buffer-days-val");
  const hbSlopeVal = document.getElementById("buffer-slope-val");
  if (hbDays && cfg.holiday_buffer_days !== undefined) {
    hbDays.value = cfg.holiday_buffer_days;
    if (hbDaysVal) hbDaysVal.textContent = cfg.holiday_buffer_days + " days";
  }
  if (hbSlope && cfg.holiday_buffer_slope !== undefined) {
    hbSlope.value = cfg.holiday_buffer_slope;
    if (hbSlopeVal) {
      const pct = Math.round((cfg.holiday_buffer_slope) * 100);
      hbSlopeVal.textContent = pct >= 0 ? `+${pct}%` : `${pct}%`;
    }
  }

  // Local events
  populateEventsTable(cfg.local_events || []);

  dirty = false;
  setDirty(false);
  document.getElementById("save-config-btn").disabled = true;
}

function setVal(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value;
}

// ── Weight sum tracking ──────────────────────────────────────────────────────
function updateWeightDisplay() {
  const ids = ["wt-demand","wt-event","wt-competitor","wt-yield"];
  const vals = ids.map(id => parseFloat(document.getElementById(id)?.value || "0"));
  const sum = vals.reduce((a, b) => a + b, 0);
  const sumEl = document.getElementById("weights-sum");
  if (sumEl) sumEl.textContent = Math.round(sum * 100) + "%";
  const renormBtn = document.getElementById("renorm-btn");
  if (renormBtn) renormBtn.style.display = Math.abs(sum - 1.0) < 0.001 ? "none" : "inline";
}

function renormalizeWeights() {
  const ids = ["wt-demand","wt-event","wt-competitor","wt-yield"];
  const vals = ids.map(id => parseFloat(document.getElementById(id)?.value || "0"));
  const sum = vals.reduce((a, b) => a + b, 0);
  if (sum <= 0) return;
  ids.forEach((id, i) => {
    const el = document.getElementById(id);
    if (el) el.value = (vals[i] / sum).toFixed(3);
  });
  updateWeightDisplay();
  markDirty();
}

// ── Presets ──────────────────────────────────────────────────────────────────
const PRESETS = {
  flat: { "01":1.00,"02":1.00,"03":1.00,"04":1.00,"05":1.00,"06":1.00,"07":1.00,"08":1.00,"09":1.00,"10":1.00,"11":1.00,"12":1.00 },
};

function applyPreset(name) {
  const preset = PRESETS[name];
  if (!preset) return;
  Object.assign(seasonalMonths, preset);
  drawSeasonalChart();
  markDirty();
  // Flash feedback
  const canvas = document.getElementById("seasonal-chart");
  if (canvas) {
    canvas.style.opacity = "0.4";
    setTimeout(() => canvas.style.opacity = "1", 120);
  }
}

  // Month cards and summary functions removed
// ── Seasonal draggable chart ────────────────────────────────────────────────
function getSeasonalMonths() {
  return { ...seasonalMonths };
}

function drawSeasonalChart() {
  const canvas = document.getElementById("seasonal-chart");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const dpr = window.devicePixelRatio || 1;
  const displayW = canvas.clientWidth;
  const displayH = 220;
  canvas.width = displayW * dpr;
  canvas.height = displayH * dpr;
  canvas.style.height = displayH + "px";
  ctx.scale(dpr, dpr);
  const W = displayW;
  const H = displayH;

  ctx.clearRect(0, 0, W, H);

  const PAD = { top: 20, right: 40, bottom: 30, left: 50 };
  const chartW = W - PAD.left - PAD.right;
  const chartH = H - PAD.top - PAD.bottom;
  const stepX = chartW / 11;

  // Background fill
  ctx.fillStyle = "#f7f8fa";
  ctx.fillRect(PAD.left, PAD.top, chartW, chartH);

  // Seasonal bands
  const bandDefs = [
    { months:[12,1,2],  color:"#dbeafe" },
    { months:[4,5],      color:"#fef3c7" },
    { months:[7,8],     color:"#ffedd5" },
    { months:[10,11],   color:"#f5f5f4" },
  ];
  bandDefs.forEach(band => {
    band.months.forEach(m => {
      const idx = m === 12 ? 11 : m - 1;
      const x1 = PAD.left + idx * stepX;
      ctx.fillStyle = band.color;
      ctx.fillRect(x1, PAD.top, stepX, chartH);
    });
  });

  // Grid lines
  ctx.strokeStyle = "#e2e8f0";
  ctx.lineWidth = 1;
  for (let y = 0; y <= 2.0; y += 0.5) {
    const py = PAD.top + chartH - (y / 2.0) * chartH;
    ctx.beginPath();
    ctx.moveTo(PAD.left, py);
    ctx.lineTo(PAD.left + chartW, py);
    ctx.stroke();
    ctx.fillStyle = "#718096";
    ctx.font = "11px system-ui";
    ctx.textAlign = "right";
    ctx.fillText(y.toFixed(1), PAD.left - 6, py + 4);
  }

  // Baseline 1.0
  const baselineY = PAD.top + chartH - (1.0 / 2.0) * chartH;
  ctx.strokeStyle = "#0061a4";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(PAD.left, baselineY);
  ctx.lineTo(PAD.left + chartW, baselineY);
  ctx.stroke();
  ctx.setLineDash([]);

  // Axes
  ctx.strokeStyle = "#1a202c";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(PAD.left, PAD.top);
  ctx.lineTo(PAD.left, PAD.top + chartH);
  ctx.lineTo(PAD.left + chartW, PAD.top + chartH);
  ctx.stroke();

  // Month labels on canvas
  ctx.fillStyle = "#718096";
  ctx.font = "11px system-ui";
  ctx.textAlign = "center";
  MONTH_NAMES.forEach((name, i) => {
    const px = PAD.left + stepX / 2 + i * stepX;
    ctx.fillText(name, px, PAD.top + chartH + 18);
    ctx.beginPath();
    ctx.moveTo(px, PAD.top + chartH);
    ctx.lineTo(px, PAD.top + chartH + 4);
    ctx.stroke();
  });

  // Points
  const points = MONTH_NAMES.map((_, i) => {
    const key = String(i + 1).padStart(2, "0");
    return {
      x: PAD.left + stepX / 2 + i * stepX,
      y: PAD.top + chartH - ((seasonalMonths[key] - 0) / 2.0) * chartH,
      key,
    };
  });

  // Min/max values
  const allVals = Object.values(seasonalMonths);
  const maxVal = Math.max(...allVals);
  const minVal = Math.min(...allVals);

  // Line
  ctx.strokeStyle = "#0061a4";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
  ctx.stroke();

  // Area fill
  ctx.fillStyle = "rgba(0,97,164,0.08)";
  ctx.beginPath();
  points.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
  ctx.lineTo(points[points.length - 1].x, PAD.top + chartH);
  ctx.lineTo(points[0].x, PAD.top + chartH);
  ctx.closePath();
  ctx.fill();

  // Handles
  const handleRadius = 11;
  points.forEach((p, i) => {
    const key = String(i + 1).padStart(2, "0");
    const val = seasonalMonths[key];
    const isDragging = dragIndex === i;
    const isHovered  = hoveredIndex === i;

    // Point
    ctx.beginPath();
    ctx.arc(p.x, p.y, handleRadius, 0, Math.PI * 2);
    if (isDragging) { ctx.fillStyle = "#00497d"; ctx.strokeStyle = "#fff"; }
    else if (isHovered) { ctx.fillStyle = "#3b82f6"; ctx.strokeStyle = "#fff"; }
    else { ctx.fillStyle = "#1d4ed8"; ctx.strokeStyle = "#ffffff"; }
    ctx.lineWidth = 2;
    ctx.fill();
    ctx.stroke();

    // Label BELOW the point (not above), always visible
    ctx.fillStyle = "#374151";
    ctx.font = "9px system-ui";
    ctx.textAlign = "center";
    ctx.fillText(val.toFixed(2) + "\u00d7", p.x, p.y + handleRadius + 12);
  });

  // Helpers
  function hitTest(mx, my) {
    for (let i = 0; i < points.length; i++) {
      const dx = mx - points[i].x, dy = my - points[i].y;
      if (Math.sqrt(dx*dx + dy*dy) < handleRadius * 4) return i;
    }
    return -1;
  }
  function getPos(e) {
    const rect = canvas.getBoundingClientRect();
    return { mx: (e.clientX - rect.left) * (W / rect.width), my: (e.clientY - rect.top) * (H / rect.height) };
  }
  function showTip(idx) {
    const tip = document.getElementById("seasonal-tooltip");
    if (!tip || idx < 0) { if (tip) tip.classList.add("hidden"); return; }
    const p = points[idx];
    const key = String(idx + 1).padStart(2, "0");
    const val = seasonalMonths[key];
    const baseP = parseFloat(document.getElementById("base_price")?.value) || 250;
    tip.textContent = `${MONTH_NAMES[idx]}  ${val.toFixed(2)}\u00d7  $${(baseP * val).toFixed(0)}/nt`;
    const rect = canvas.getBoundingClientRect();
    tip.style.left = (p.x * rect.width / W) + "px";
    tip.style.top  = (p.y * rect.height / H) + "px";
    tip.classList.remove("hidden");
  }

  canvas.onmousedown = e => {
    const { mx, my } = getPos(e);
    const idx = hitTest(mx, my);
    if (idx >= 0) {
      dragIndex = idx;
      updateSeasonalValueDisplay(idx);
    }
  };
  canvas.onmousemove = e => {
    const { mx, my } = getPos(e);
    if (dragIndex >= 0) {
      const clampedY = Math.max(PAD.top, Math.min(PAD.top + chartH, my));
      const newVal = Math.round(((PAD.top + chartH - clampedY) / chartH) * 2 * 100) / 100;
      const key = String(dragIndex + 1).padStart(2, "0");
      seasonalMonths[key] = Math.max(0.5, Math.min(2.0, newVal));
      updateSeasonalValueDisplay(dragIndex);
      drawSeasonalChart();
    } else {
      const prev = hoveredIndex;
      hoveredIndex = hitTest(mx, my);
      showTip(hoveredIndex);
      if (prev !== hoveredIndex) drawSeasonalChart();
    }
  };
  canvas.onmouseup = () => { if (dragIndex >= 0) markDirty(); dragIndex = -1; };
  canvas.onmouseleave = () => {
    if (dragIndex >= 0) markDirty();
    dragIndex = -1; hoveredIndex = -1;
    showTip(-1);
    drawSeasonalChart();
  };
}

function updateSeasonalValueDisplay(index) {
  const key = String(index + 1).padStart(2, "0");
  const val = seasonalMonths[key] ?? 1.0;
  const monthSpan = document.getElementById("seasonal-selected-month");
  const input = document.getElementById("seasonal-value-input");
  const pctEl = document.getElementById("seasonal-value-pct");
  if (monthSpan) monthSpan.textContent = MONTH_NAMES[index] + ": ";
  if (input) input.value = val.toFixed(2);
  if (pctEl) pctEl.textContent = fmtPct(val);
  dragIndex = index;
}

// Wire seasonal value input
document.getElementById("seasonal-value-input")?.addEventListener("change", () => {
  const input = document.getElementById("seasonal-value-input");
  const val = parseFloat(input.value);
  if (dragIndex >= 0 && dragIndex < 12) {
    const key = String(dragIndex + 1).padStart(2, "0");
    seasonalMonths[key] = val;
    const pctEl = document.getElementById("seasonal-value-pct");
    if (pctEl) pctEl.textContent = fmtPct(val);
    drawSeasonalChart();
    markDirty();
  }
});

// ── Events table ────────────────────────────────────────────────────────────
function populateEventsTable(events) {
  const tbody = document.getElementById("events-tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  events.forEach(ev => addEventRow(ev, false));
}

function addEventRow(ev = {}, markDirtyFlag = true) {
  const tbody = document.getElementById("events-tbody");
  if (!tbody) return;
  const tr = document.createElement("tr");
  tr.className = "border-b border-surface-container-low";
  tr.innerHTML = `
    <td class="py-3 pr-4"><input type="text" class="e-name w-full bg-transparent border-none text-on-surface text-sm outline-none focus:ring-0 px-0 py-1" value="${ev.name || ""}" placeholder="Event name"></td>
    <td class="py-3 pr-4"><input type="text" class="e-date w-full bg-transparent border-none text-on-surface text-sm outline-none focus:ring-0 px-0 py-1" value="${ev.date || ""}" placeholder="YYYY-MM-DD"></td>
    <td class="py-3 pr-4"><input type="number" step="0.01" min="0" class="e-factor w-20 bg-surface-container-low border-none rounded-full py-1.5 px-3 text-on-surface text-sm text-center outline-none focus:ring-2 focus:ring-primary/20" value="${ev.factor || 1.1}"></td>
    <td class="py-3"><button type="button" class="del-row-btn w-8 h-8 rounded-full bg-surface-container-high flex items-center justify-center text-on-surface-variant hover:bg-surface-container-highest transition-colors text-sm">✕</button></td>
  `;
  tr.querySelector(".del-row-btn").addEventListener("click", () => {
    tr.remove();
    if (markDirtyFlag) markDirty();
  });
  tr.querySelectorAll("input").forEach(i => i.addEventListener("change", markDirty));
  tbody.appendChild(tr);
  if (markDirtyFlag) markDirty();
}

document.getElementById("add-event-btn")?.addEventListener("click", () => addEventRow());

// ── Dirty state ──────────────────────────────────────────────────────────────
let _dirtyTimer = null;
function markDirty() {
  dirty = true;
  setDirty(true);
  const btn = document.getElementById("save-config-btn");
  if (btn) {
    btn.disabled = false;
    if (_dirtyTimer) clearTimeout(_dirtyTimer);
    _dirtyTimer = setTimeout(() => {}, 300);
  }
}

function setDirty(isDirty) {
  const indicator = document.getElementById("dirty-indicator");
  if (indicator) indicator.style.display = isDirty ? "inline" : "none";
}

// Wire all inputs to markDirty
document.querySelectorAll("input").forEach(el => {
  el.addEventListener("change", markDirty);
});
["wt-demand","wt-event","wt-competitor","wt-yield"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("input", () => {
    const lbl = document.getElementById(id + "-val");
    if (lbl) {
      const v = parseFloat(el.value);
      lbl.textContent = v >= 0 ? `+${Math.round(v * 100)}%` : `${Math.round(v * 100)}%`;
    }
    updateWeightDisplay();
    markDirty();
  });
});

// Wire DOW inputs to mark dirty
DOW_KEYS.forEach(k => {
  document.getElementById("dow-" + k)?.addEventListener("input", markDirty);
});

// Wire holiday_buffer_slope slider (old single one in Seasonality section)
document.getElementById("holiday_buffer_slope")?.addEventListener("input", (e) => {
  const valEl = document.getElementById("holiday_buffer_slope_val");
  if (valEl) valEl.textContent = fmtPct(parseFloat(e.target.value));
  markDirty();
});

// Wire new holiday buffer days slider
const hbDays = document.getElementById("holiday_buffer_days");
const hbDaysVal = document.getElementById("buffer-days-val");
if (hbDays) hbDays.addEventListener("input", () => {
  if (hbDaysVal) hbDaysVal.textContent = hbDays.value + " days";
  markDirty();
});

// Wire new holiday buffer slope slider (in Local Events section)
const hbSlope = document.getElementById("holiday_buffer_slope");
const hbSlopeVal = document.getElementById("buffer-slope-val");
if (hbSlope) hbSlope.addEventListener("input", () => {
  if (hbSlopeVal) {
    const pct = Math.round(parseFloat(hbSlope.value) * 100);
    hbSlopeVal.textContent = pct >= 0 ? `+${pct}%` : `${pct}%`;
  }
  markDirty();
});

// ── Collect & save ────────────────────────────────────────────────────────────
async function saveConfig() {
  const btn = document.getElementById("save-config-btn");
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }

  // ── Capture old prices BEFORE saving ──
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth() + 1;
  try {
    const res = await fetch(`/api/calendar/${year}/${month}?property_uid=${currentPropertyUid}`);
    const data = await res.json();
    const oldPricesByDate = {};
    for (const day of data.days) {
      oldPricesByDate[day.date] = day.final_price;
    }
    sessionStorage.setItem('old_prices', JSON.stringify(oldPricesByDate));
    sessionStorage.setItem('old_prices_timestamp', Date.now());
  } catch (e) {
    console.warn("Could not capture old prices:", e);
  }
  // ──────────────────────────────────────

  const cfg = JSON.parse(JSON.stringify(config || {}));

  cfg.base_price = parseFloat(document.getElementById("base_price")?.value) || 200;
  cfg.min_price = parseFloat(document.getElementById("min_price")?.value) || 100;
  cfg.max_price = parseFloat(document.getElementById("max_price")?.value) || 800;
  cfg.price_adjust = parseFloat(document.getElementById("price_adjust")?.value) || 0;

  cfg.strategy_weights = {
    demand: parseFloat(document.getElementById("wt-demand")?.value) || 0.5,
    event: parseFloat(document.getElementById("wt-event")?.value) || 0.375,
    competitor: parseFloat(document.getElementById("wt-competitor")?.value) || 0,
    yield: parseFloat(document.getElementById("wt-yield")?.value) || 0.125,
  };

  cfg.dow_multipliers = {};
  DOW_KEYS.forEach(k => {
    cfg.dow_multipliers[k] = 1.0 + (parseFloat(document.getElementById("dow-" + k)?.value) || 0) / 100;
  });

  cfg.demand_config = cfg.demand_config || {};
  cfg.demand_config.demand_window_days = parseInt(document.getElementById("demand_window_days")?.value) || 14;
  cfg.demand_config.velocity_window_days = parseInt(document.getElementById("velocity_window_days")?.value) || 7;
  cfg.demand_config.far_future = {
    window_days: parseInt(document.getElementById("ff-window")?.value) || 60,
    discount: parseFloat(document.getElementById("ff-discount")?.value) || 0.9,
  };
  cfg.demand_config.last_minute = {
    window_days: parseInt(document.getElementById("lm-window")?.value) || 7,
    discount: parseFloat(document.getElementById("lm-discount")?.value) || 0.92,
  };

  cfg.availability = cfg.availability || {};
  cfg.availability.booking_window_days = parseInt(document.getElementById("booking_window_days")?.value) || 120;
  cfg.availability.min_stay = { default: parseInt(document.getElementById("min_stay_default")?.value) || 2, overrides: [] };
  cfg.availability.block_day_before = document.getElementById("block_day_before")?.checked ?? false;
  cfg.availability.block_day_after = document.getElementById("block_day_after")?.checked ?? false;

  cfg.demand_config.occupancy_factor = parseFloat(document.getElementById("occupancy_factor")?.value) || 0.3;
  cfg.demand_config.velocity_factor = cfg.demand_config.velocity_factor ?? 0.5;
  cfg.seasonal_base_prices = cfg.seasonal_base_prices ?? {};

  cfg.seasonal_months = getSeasonalMonths();
  cfg.holiday_buffer_days = parseInt(document.getElementById("holiday_buffer_days")?.value) || 3;
  cfg.holiday_buffer_slope = parseFloat(document.getElementById("holiday_buffer_slope")?.value) || 0.05;

  // Local events
  cfg.local_events = [];
  document.querySelectorAll("#events-tbody tr").forEach(tr => {
    const name = tr.querySelector(".e-name")?.value.trim() || "";
    const date = tr.querySelector(".e-date")?.value.trim() || "";
    const factor = parseFloat(tr.querySelector(".e-factor")?.value) || 1.1;
    if (name && date) cfg.local_events.push({ name, date, factor });
  });

  try {
    const saved = await api.put(`/api/config/${getPropertyUid()}`, cfg);
    updateSaveStatus(true);
    config = saved;
    dirty = false;
    setDirty(false);
    // Redirect to calendar so user sees new prices immediately
    window.location.href = '/calendar?property_uid=' + currentPropertyUid + '&refreshed=1';
  } catch (e) {
    updateSaveStatus(false);
    console.error("Save failed:", e);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-outlined text-sm">save</span> Save Config';
    }
  }
}
