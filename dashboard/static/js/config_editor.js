/* ── config_editor.js — Config field bindings, seasonal chart, save logic ────── */

import { api } from "./api.js";

const DEFAULT_PROPERTY = "731418607849470882";
const MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const DOW_KEYS = ["mon","tue","wed","thu","fri","sat","sun"];
const CONFIG_SCHEMA_VERSION_CANONICAL = 4;
const LEGACY_YIELD_ONLY_KEYS = [
  "advance_lead_factor",
  "mid_lead_factor",
  "short_lead_factor",
  "last_minute_lead_factor",
  "base_churn_probability",
  "opportunity_threshold_nights",
  "low_opportunity_factor",
  "high_opportunity_factor",
];

function fmtPct(v, digits = 1) {
  const value = Number.parseFloat(v);
  if (!Number.isFinite(value)) return "0%";
  const fixed = value.toFixed(digits).replace(/\\.0+$/, "").replace(/(\\.\\d*[1-9])0+$/, "$1");
  if (value > 0) return `+${fixed}%`;
  if (value < 0) return `${fixed}%`;
  return "0%";
}

function parsePct(raw, fallback = 0) {
  if (raw == null) return fallback;
  const text = String(raw).trim().replace(/%/g, "");
  if (!text) return fallback;
  const parsed = Number.parseFloat(text);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function setPctInput(id, value, digits = 1) {
  const el = document.getElementById(id);
  if (el) el.value = fmtPct(value, digits);
}

function getPctInput(id, fallback = 0) {
  const el = document.getElementById(id);
  return parsePct(el?.value, fallback);
}

let currentPropertyUid = localStorage.getItem("atlas_property_uid") || DEFAULT_PROPERTY;

function getPropertyUid() {
  return currentPropertyUid;
}

let config = null;
let dirty = false;
let seasonalMonths = {};
let dragIndex = -1;
let hoveredIndex = -1;

// ── Init ────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  const switcher = document.getElementById("property-switcher");
  if (switcher) {
    switcher.value = getPropertyUid();
    switcher.addEventListener("change", async () => {
      localStorage.setItem("atlas_property_uid", switcher.value);
      currentPropertyUid = switcher.value;
      dirty = false;
      setDirty(false);
      await loadConfig(switcher.value);
    });
  }

  document.getElementById("save-config-btn")?.addEventListener("click", saveConfig);
  document.getElementById("add-event-btn")?.addEventListener("click", addEventRow);
  document.getElementById("occ-explain-btn")?.addEventListener("click", () => explainAdjustment("occupancy"));
  document.getElementById("vel-explain-btn")?.addEventListener("click", () => explainAdjustment("velocity"));
  document.getElementById("comp-enabled")?.addEventListener("change", () => {
    updateCompetitorPill();
    markDirty();
  });

  // Preset buttons
  document.querySelectorAll(".preset-btn").forEach(btn => {
    btn.addEventListener("click", () => applyPreset(btn.dataset.preset));
  });



  await loadConfig(getPropertyUid());
});

async function loadConfig(uid) {
  currentPropertyUid = uid;
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

  const paCfg = cfg.pricing_adjustments || {};

  // DOW percentages
  const dow = paCfg.dow_pct || {};
  DOW_KEYS.forEach(k => {
    setPctInput("dow-" + k, dow[k] ?? 0, 0);
  });

  // Pricing adjustments (canonical percent-point schema)
  const occ = {
    enabled: paCfg.occupancy_pacing_enabled ?? true,
    window_days: paCfg.occupancy_pacing_window_days ?? 14,
    target_occupancy_pct: paCfg.occupancy_pacing_target_occupancy_pct ?? 25,
    sensitivity_pct: paCfg.occupancy_pacing_sensitivity_pct ?? 20,
    max_discount_pct: paCfg.occupancy_pacing_max_discount_pct ?? 10,
    max_increase_pct: paCfg.occupancy_pacing_max_increase_pct ?? 10,
    min_available_nights: paCfg.occupancy_pacing_min_available_nights ?? 5,
  };
  const vel = {
    enabled: paCfg.booking_velocity_enabled ?? true,
    recent_window_days: paCfg.booking_velocity_recent_window_days ?? 7,
    baseline_window_days: paCfg.booking_velocity_baseline_window_days ?? 60,
    sensitivity_pct: paCfg.booking_velocity_sensitivity_pct ?? 8,
    max_discount_pct: paCfg.booking_velocity_max_discount_pct ?? 0,
    max_increase_pct: paCfg.booking_velocity_max_increase_pct ?? 15,
    min_recent_bookings: paCfg.booking_velocity_min_recent_bookings ?? 2,
    min_baseline_bookings: paCfg.booking_velocity_min_baseline_bookings ?? 3,
  };

  const occEnabled = document.getElementById("occ-enabled");
  if (occEnabled) occEnabled.checked = occ.enabled ?? true;
  setVal("occ-window-days", occ.window_days ?? 14);
  setPctInput("occ-target-occupancy", occ.target_occupancy_pct ?? 25, 1);
  setPctInput("occ-sensitivity", occ.sensitivity_pct ?? 20, 1);
  setPctInput("occ-max-discount", occ.max_discount_pct ?? 10, 1);
  setPctInput("occ-max-increase", occ.max_increase_pct ?? 10, 1);
  setVal("occ-min-available-nights", occ.min_available_nights ?? 5);

  const velEnabled = document.getElementById("vel-enabled");
  if (velEnabled) velEnabled.checked = vel.enabled ?? true;
  setVal("vel-recent-window-days", vel.recent_window_days ?? 7);
  setVal("vel-baseline-window-days", vel.baseline_window_days ?? 60);
  setPctInput("vel-sensitivity", vel.sensitivity_pct ?? 8, 1);
  setPctInput("vel-max-discount", vel.max_discount_pct ?? 0, 1);
  setPctInput("vel-max-increase", vel.max_increase_pct ?? 15, 1);
  setVal("vel-min-recent-bookings", vel.min_recent_bookings ?? 2);
  setVal("vel-min-baseline-bookings", vel.min_baseline_bookings ?? 3);

  // Competitor analysis
  const compEnabled = document.getElementById("comp-enabled");
  if (compEnabled) compEnabled.checked = Boolean(cfg.external_market_data?.enabled ?? false);
  updateCompetitorPill();

  // Availability
  const av = cfg.availability || {};
  setVal("booking_window_days", av.booking_window_days ?? 120);
  setVal("ff-window", paCfg.far_future_window_days ?? 60);
  setPctInput("ff-discount", paCfg.far_future_discount_pct ?? -10, 1);
  setVal("lm-window", paCfg.last_minute_window_days ?? 7);
  setPctInput("lm-discount", paCfg.last_minute_discount_pct ?? -8, 1);
  setPctInput("lm-threshold-occupancy", paCfg.last_minute_threshold_occupancy_pct ?? 50, 1);
  const blockBefore = document.getElementById("block_day_before");
  const blockAfter = document.getElementById("block_day_after");
  if (blockBefore) blockBefore.checked = av.block_day_before ?? false;
  if (blockAfter) blockAfter.checked = av.block_day_after ?? false;

  // Price adjustment
  const pa = document.getElementById("price_adjust");
  const paVal = document.getElementById("price-adjust-val");
  if (pa && paCfg.price_adjust_pct !== undefined) {
    pa.value = paCfg.price_adjust_pct;
    if (paVal) paVal.textContent = fmtPct(paCfg.price_adjust_pct, 0);
  }
  if (pa && !pa.dataset.boundInput) {
    pa.dataset.boundInput = "1";
    pa.addEventListener("input", () => {
      if (paVal) paVal.textContent = fmtPct(parseFloat(pa.value), 0);
      markDirty();
    });
  }

  // Seasonal months
  seasonalMonths = {};
  MONTH_NAMES.forEach((_, i) => {
    const key = String(i + 1).padStart(2, "0");
    seasonalMonths[key] = paCfg.seasonal_months_pct?.[key] ?? 0.0;
  });

  // Holiday buffer days
  const hbDays = document.getElementById("holiday_buffer_days");
  const hbSlope = document.getElementById("holiday_buffer_slope");
  const hbDaysVal = document.getElementById("buffer-days-val");
  const hbSlopeVal = document.getElementById("buffer-slope-val");
  if (hbDays && paCfg.holiday_buffer_days !== undefined) {
    hbDays.value = paCfg.holiday_buffer_days;
    if (hbDaysVal) hbDaysVal.textContent = paCfg.holiday_buffer_days + " days";
  }
  if (hbSlope && paCfg.holiday_buffer_slope_pct !== undefined) {
    hbSlope.value = paCfg.holiday_buffer_slope_pct;
    if (hbSlopeVal) {
      hbSlopeVal.textContent = fmtPct(paCfg.holiday_buffer_slope_pct, 0);
    }
  }

  // Local events
  populateEventsTable(paCfg.local_events || []);

  dirty = false;
  setDirty(false);
  document.getElementById("save-config-btn").disabled = true;
}

function setVal(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value;
}

function updateCompetitorPill() {
  const enabled = document.getElementById("comp-enabled")?.checked ?? false;
  const pill = document.getElementById("comp-enabled-pill");
  if (!pill) return;
  pill.textContent = enabled ? "ON" : "OFF";
  if (enabled) {
    pill.className = "inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold bg-green-100 text-green-800";
  } else {
    pill.className = "inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold bg-surface-container-low text-on-surface-variant";
  }
}

function buildDraftConfigFromForm() {
  const cfg = JSON.parse(JSON.stringify(config || {}));

  cfg.base_price = parseFloat(document.getElementById("base_price")?.value) || 200;
  cfg.min_price = parseFloat(document.getElementById("min_price")?.value) || 100;
  cfg.max_price = parseFloat(document.getElementById("max_price")?.value) || 800;
  cfg.config_schema_version = CONFIG_SCHEMA_VERSION_CANONICAL;

  cfg.pricing_adjustments = cfg.pricing_adjustments || {};
  cfg.pricing_adjustments.price_adjust_pct = parseFloat(document.getElementById("price_adjust")?.value) || 0;
  cfg.pricing_adjustments.seasonal_months_pct = getSeasonalMonths();
  cfg.pricing_adjustments.dow_pct = {};
  DOW_KEYS.forEach(k => {
    cfg.pricing_adjustments.dow_pct[k] = getPctInput("dow-" + k, 0);
  });
  cfg.pricing_adjustments.holiday_buffer_days = parseInt(document.getElementById("holiday_buffer_days")?.value) || 3;
  cfg.pricing_adjustments.holiday_buffer_slope_pct = parseFloat(document.getElementById("holiday_buffer_slope")?.value) || 5;
  cfg.pricing_adjustments.far_future_window_days = parseInt(document.getElementById("ff-window")?.value) || 60;
  cfg.pricing_adjustments.far_future_discount_pct = getPctInput("ff-discount", -10);
  cfg.pricing_adjustments.last_minute_window_days = parseInt(document.getElementById("lm-window")?.value) || 7;
  cfg.pricing_adjustments.last_minute_discount_pct = getPctInput("lm-discount", -8);
  cfg.pricing_adjustments.last_minute_threshold_occupancy_pct = getPctInput("lm-threshold-occupancy", 50);

  cfg.pricing_adjustments.occupancy_pacing_enabled = document.getElementById("occ-enabled")?.checked ?? true;
  cfg.pricing_adjustments.occupancy_pacing_window_days = parseInt(document.getElementById("occ-window-days")?.value) || 14;
  cfg.pricing_adjustments.occupancy_pacing_target_occupancy_pct = getPctInput("occ-target-occupancy", 25);
  cfg.pricing_adjustments.occupancy_pacing_sensitivity_pct = getPctInput("occ-sensitivity", 20);
  cfg.pricing_adjustments.occupancy_pacing_max_discount_pct = getPctInput("occ-max-discount", 10);
  cfg.pricing_adjustments.occupancy_pacing_max_increase_pct = getPctInput("occ-max-increase", 10);
  cfg.pricing_adjustments.occupancy_pacing_min_available_nights = parseInt(document.getElementById("occ-min-available-nights")?.value) || 5;

  cfg.pricing_adjustments.booking_velocity_enabled = document.getElementById("vel-enabled")?.checked ?? true;
  cfg.pricing_adjustments.booking_velocity_recent_window_days = parseInt(document.getElementById("vel-recent-window-days")?.value) || 7;
  cfg.pricing_adjustments.booking_velocity_baseline_window_days = parseInt(document.getElementById("vel-baseline-window-days")?.value) || 60;
  cfg.pricing_adjustments.booking_velocity_sensitivity_pct = getPctInput("vel-sensitivity", 8);
  cfg.pricing_adjustments.booking_velocity_max_discount_pct = getPctInput("vel-max-discount", 0);
  cfg.pricing_adjustments.booking_velocity_max_increase_pct = getPctInput("vel-max-increase", 15);
  cfg.pricing_adjustments.booking_velocity_min_recent_bookings = parseInt(document.getElementById("vel-min-recent-bookings")?.value) || 2;
  cfg.pricing_adjustments.booking_velocity_min_baseline_bookings = parseInt(document.getElementById("vel-min-baseline-bookings")?.value) || 3;

  cfg.external_market_data = cfg.external_market_data || {};
  cfg.external_market_data.enabled = document.getElementById("comp-enabled")?.checked ?? false;

  cfg.availability = cfg.availability || {};
  cfg.availability.booking_window_days = parseInt(document.getElementById("booking_window_days")?.value) || 120;
  cfg.availability.block_day_before = document.getElementById("block_day_before")?.checked ?? false;
  cfg.availability.block_day_after = document.getElementById("block_day_after")?.checked ?? false;
  delete cfg.availability.far_future;
  delete cfg.availability.last_minute;

  delete cfg.demand_config;
  delete cfg.seasonal_months;
  delete cfg.dow_multipliers;
  delete cfg.price_adjust;
  delete cfg.holiday_buffer_slope;
  delete cfg.holiday_multipliers;
  delete cfg.holiday_default_multiplier;
  delete cfg.seasonal_months_pct;
  delete cfg.dow_pct;
  delete cfg.price_adjust_pct;
  delete cfg.holiday_buffer_days;
  delete cfg.holiday_buffer_slope_pct;
  delete cfg.holiday_multipliers_pct;
  delete cfg.holiday_default_pct;
  delete cfg.local_events;
  delete cfg.local_events_config;
  delete cfg.pricing_adjustments.occupancy_pacing;
  delete cfg.pricing_adjustments.booking_velocity;

  // Remove legacy yield-only knobs so config mirrors active UI controls.
  for (const key of LEGACY_YIELD_ONLY_KEYS) {
    delete cfg[key];
  }
  return cfg;
}

async function explainAdjustment(kind) {
  const outId = kind === "occupancy" ? "occ-explain-output" : "vel-explain-output";
  const key = kind === "occupancy" ? "occupancy_pacing" : "booking_velocity";
  const output = document.getElementById(outId);
  if (!output) return;

  output.classList.remove("hidden");
  output.textContent = "Generating explanation using current values…";

  try {
    const draft = buildDraftConfigFromForm();
    const res = await api.post("/api/config/explain-adjustments", { config: draft, property_uid: getPropertyUid() });
    const text = res?.[key]?.example_text || "No explanation generated.";
    output.textContent = text;
  } catch (e) {
    output.textContent = `Failed to generate explanation: ${e.message}`;
  }
}

// ── Presets ──────────────────────────────────────────────────────────────────
const PRESETS = {
  flat: { "01":0,"02":0,"03":0,"04":0,"05":0,"06":0,"07":0,"08":0,"09":0,"10":0,"11":0,"12":0 },
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

  const PCT_MIN = -50;
  const PCT_MAX = 100;
  const PCT_SPAN = PCT_MAX - PCT_MIN;

  // Grid lines
  ctx.strokeStyle = "#e2e8f0";
  ctx.lineWidth = 1;
  for (let pct = PCT_MIN; pct <= PCT_MAX; pct += 25) {
    const py = PAD.top + chartH - ((pct - PCT_MIN) / PCT_SPAN) * chartH;
    ctx.beginPath();
    ctx.moveTo(PAD.left, py);
    ctx.lineTo(PAD.left + chartW, py);
    ctx.stroke();
    ctx.fillStyle = "#718096";
    ctx.font = "11px system-ui";
    ctx.textAlign = "right";
    ctx.fillText(`${pct}%`, PAD.left - 6, py + 4);
  }

  // Baseline 0%
  const baselineY = PAD.top + chartH - ((0 - PCT_MIN) / PCT_SPAN) * chartH;
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
      y: PAD.top + chartH - ((seasonalMonths[key] - PCT_MIN) / PCT_SPAN) * chartH,
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
    ctx.fillText(fmtPct(val, 0), p.x, p.y + handleRadius + 12);
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
    tip.textContent = `${MONTH_NAMES[idx]}  ${fmtPct(val, 1)}  $${(baseP * (1 + (val / 100))).toFixed(0)}/nt`;
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
      const newVal = Math.round((PCT_MIN + (((PAD.top + chartH - clampedY) / chartH) * PCT_SPAN)) * 10) / 10;
      const key = String(dragIndex + 1).padStart(2, "0");
      seasonalMonths[key] = Math.max(PCT_MIN, Math.min(PCT_MAX, newVal));
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
  const val = seasonalMonths[key] ?? 0.0;
  const monthSpan = document.getElementById("seasonal-selected-month");
  const input = document.getElementById("seasonal-value-input");
  const pctEl = document.getElementById("seasonal-value-pct");
  if (monthSpan) monthSpan.textContent = MONTH_NAMES[index] + ": ";
  if (input) input.value = fmtPct(val, 1);
  if (pctEl) pctEl.textContent = fmtPct(val);
  dragIndex = index;
}

// Wire seasonal value input
document.getElementById("seasonal-value-input")?.addEventListener("change", () => {
  const input = document.getElementById("seasonal-value-input");
  const val = parsePct(input?.value, 0);
  if (dragIndex >= 0 && dragIndex < 12) {
    const key = String(dragIndex + 1).padStart(2, "0");
    seasonalMonths[key] = Math.max(-50, Math.min(100, val));
    const pctEl = document.getElementById("seasonal-value-pct");
    if (pctEl) pctEl.textContent = fmtPct(seasonalMonths[key]);
    if (input) input.value = fmtPct(seasonalMonths[key], 1);
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
    <td class="py-3 pr-4"><input type="text" class="e-factor w-20 bg-surface-container-low border-none rounded-full py-1.5 px-3 text-on-surface text-sm text-center outline-none focus:ring-2 focus:ring-primary/20" value="${fmtPct(ev.factor_pct ?? 0, 1)}" placeholder="+10%"></td>
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
  if (indicator) {
    if (isDirty) {
      indicator.style.visibility = "visible";
      indicator.style.opacity = "1";
    } else {
      indicator.style.visibility = "hidden";
      indicator.style.opacity = "0";
    }
  }
}

// Wire all inputs to markDirty
document.querySelectorAll("input").forEach(el => {
  el.addEventListener("change", markDirty);
});

// Wire DOW inputs to mark dirty
DOW_KEYS.forEach(k => {
  document.getElementById("dow-" + k)?.addEventListener("input", markDirty);
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
    hbSlopeVal.textContent = fmtPct(parseFloat(hbSlope.value), 0);
  }
  markDirty();
});

// ── Collect & save ────────────────────────────────────────────────────────────
async function saveConfig() {
  const btn = document.getElementById("save-config-btn");
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }

  const keyFields = ["config_schema_version", "base_price", "min_price", "max_price", "pricing_adjustments", "availability", "external_market_data"];
  let beforeSnapshot = null;
  try {
    const before = await api.get(`/api/config/${getPropertyUid()}`);
    beforeSnapshot = {};
    for (const k of keyFields) { beforeSnapshot[k] = before[k]; }
  } catch (e) {
    console.warn("Could not capture before config snapshot:", e);
  }

  const cfg = buildDraftConfigFromForm();
  cfg.seasonal_base_prices = cfg.seasonal_base_prices ?? {};

  // Local events
  cfg.pricing_adjustments = cfg.pricing_adjustments || {};
  cfg.pricing_adjustments.local_events = [];
  document.querySelectorAll("#events-tbody tr").forEach(tr => {
    const name = tr.querySelector(".e-name")?.value.trim() || "";
    const date = tr.querySelector(".e-date")?.value.trim() || "";
    const factorPct = parsePct(tr.querySelector(".e-factor")?.value, 0);
    if (name && date) cfg.pricing_adjustments.local_events.push({ name, date, factor_pct: factorPct });
  });

  try {
    const saved = await api.put(`/api/config/${getPropertyUid()}`, cfg);

    try {
      const verify = await api.get(`/api/config/${getPropertyUid()}`);
      const afterSnapshot = {};
      for (const k of keyFields) { afterSnapshot[k] = verify[k]; }
      const debug = { before: beforeSnapshot, after: afterSnapshot, saved_cfg: saved };
      console.log("saved_config_debug", debug);
      if (beforeSnapshot) {
        const mismatch = keyFields.find(k => JSON.stringify(beforeSnapshot[k]) !== JSON.stringify(afterSnapshot[k]));
        if (mismatch) {
          console.error("Save verify mismatch on field:", mismatch, "before:", beforeSnapshot[mismatch], "after:", afterSnapshot[mismatch]);
        }
      }
    } catch (ve) {
      console.error("Save verify GET failed:", ve);
    }

    config = saved;
    dirty = false;
    setDirty(false);
    window.location.href = '/calendar?property_uid=' + currentPropertyUid + '&refreshed=1';
  } catch (e) {
    console.error("Save failed:", e);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-outlined text-sm">save</span> Save Config';
    }
  }
}
