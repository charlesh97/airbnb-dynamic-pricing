/* ── day_panel.js — Slide-out factor breakdown panel ─────────────── */

import { api } from "./api.js";

export const DayPanel = (() => {
  let panel, overlay, panelBody, panelTitle, closeBtn;
  let currentResolve;

  function _getEl(id) { return document.getElementById(id); }

  function init() {
    panel = _getEl("day-panel");
    overlay = _getEl("panel-overlay");
    panelBody = _getEl("panel-body");
    panelTitle = _getEl("panel-date-title");
    closeBtn = _getEl("panel-close-btn");

    closeBtn.addEventListener("click", close);
    overlay.addEventListener("click", close);

    panel.addEventListener("click", e => e.stopPropagation());
  }

  async function open(date, propertyUid, airbnbPrice) {
    if (!panel) init();
    show();
    panelBody.innerHTML = '<div class="panel-loading">Loading…</div>';

    const params = new URLSearchParams({ property_uid: propertyUid });
    if (airbnbPrice != null) params.set("airbnb_price", airbnbPrice);

    try {
      const detail = await api.get(`/api/days/${date}?${params}`);
      render(detail);
    } catch (e) {
      panelBody.innerHTML = `<div class="panel-loading" style="color:#c53030">Error: ${e.message}</div>`;
    }
  }

  function show() {
    panel.classList.add("open");
    overlay.classList.add("open");
  }

  function close() {
    panel.classList.remove("open");
    overlay.classList.remove("open");
  }

  function render(d) {
    const occ = d?.demand?.occupancy_pacing || {};
    const vel = d?.demand?.booking_velocity || {};
    const occInputs = occ.inputs || {};
    const occComputed = occ.computed || {};
    const velInputs = vel.inputs || {};
    const velComputed = vel.computed || {};
    const pct = (v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
    const multPct = (m) => `${((Number(m) - 1) * 100 >= 0 ? "+" : "")}${((Number(m) - 1) * 100).toFixed(1)}%`;
    const num = (v, fallback = "0.000") => Number.isFinite(v) ? v.toFixed(3) : fallback;
    const valOrNA = (v, digits = 3) => Number.isFinite(v) ? v.toFixed(digits) : "n/a";

    panelTitle.textContent = d.date;
    panelBody.innerHTML = `
      <div class="panel-section">
        <h3>Overview</h3>
        <div class="panel-row"><span class="label">Suggested Price</span><span class="value">$${d.final_price.toFixed(2)}</span></div>
        ${d.current_airbnb_price != null
          ? `<div class="panel-row"><span class="label">Airbnb Price</span><span class="value">$${d.current_airbnb_price.toFixed(2)}</span></div>
             <div class="panel-row"><span class="label">Delta</span><span class="value">${d.price_delta != null ? (d.price_delta >= 0 ? "+" : "") + "$" + d.price_delta.toFixed(2) : "—"} (${d.price_delta_pct != null ? (d.price_delta_pct >= 0 ? "+" : "") + (d.price_delta_pct * 100).toFixed(1) + "%" : "—"})</span></div>
             <div class="panel-row"><span class="label">Status</span><span class="value">${d.match_status || "—"}</span></div>`
          : ""}
        <div class="panel-row"><span class="label">Confidence</span><span class="value">${(d.confidence * 100).toFixed(0)}%</span></div>
        <div class="panel-row"><span class="label">Available</span><span class="value">${d.is_available ? "Yes" : "No"}</span></div>
        ${!d.is_available && d.blocked_reason ? `<div class="panel-row"><span class="label">Block Reason</span><span class="value">${d.blocked_reason}</span></div>` : ""}
        <div class="panel-row"><span class="label">Booking Window</span><span class="value">${d.booking_window_days} days</span></div>
      </div>

      <div class="panel-section">
        <h3>Base Rate</h3>
        <div class="panel-row"><span class="label">Base</span><span class="value">$${d.base_rate.toFixed(2)}</span></div>
        <div class="panel-row"><span class="label">Starting Price</span><span class="value">$${(d.starting_price ?? d.base_rate).toFixed(2)}</span></div>
      </div>

      <div class="panel-section">
        <h3>Seasonal &amp; DOW</h3>
        <div class="panel-row"><span class="label">Rule</span><span class="value">${d.seasonal.rule}</span></div>
        ${d.seasonal.detail ? `<div class="panel-row"><span class="label">Detail</span><span class="value">${d.seasonal.detail}</span></div>` : ""}
        <div class="panel-row"><span class="label">Seasonal Adj</span><span class="value">${multPct(d.seasonal.raw_seasonal_multiplier)}</span></div>
        <div class="panel-row"><span class="label">DOW (${d.seasonal.dow})</span><span class="value">${multPct(d.seasonal.dow_multiplier)}</span></div>
        <div class="panel-row"><span class="label">Effective Adj</span><span class="value">${multPct(d.seasonal.effective_seasonal)}</span></div>
      </div>

      <div class="panel-section">
        <h3>Occupancy Pacing</h3>
        <div class="panel-row"><span class="label">Reason</span><span class="value">${occ.reason || "n/a"}</span></div>
        <div class="panel-row"><span class="label">Window Days</span><span class="value">${occInputs.window_days ?? "n/a"}</span></div>
        <div class="panel-row"><span class="label">Booked / Available</span><span class="value">${occInputs.booked_nights ?? "n/a"} / ${occInputs.available_nights ?? "n/a"}</span></div>
        <div class="panel-row"><span class="label">Actual Occupancy</span><span class="value">${Number.isFinite(occComputed.actual_occupancy) ? (occComputed.actual_occupancy * 100).toFixed(1) + "%" : "n/a"}</span></div>
        <div class="panel-row"><span class="label">Target Occupancy</span><span class="value">${Number.isFinite(occInputs.target_occupancy) ? (occInputs.target_occupancy * 100).toFixed(1) + "%" : "n/a"}</span></div>
        <div class="panel-row"><span class="label">Delta</span><span class="value">${Number.isFinite(occComputed.delta) ? pct(occComputed.delta) : "n/a"}</span></div>
        <div class="panel-row"><span class="label">Raw Adjustment</span><span class="value">${Number.isFinite(occComputed.raw_adjustment) ? pct(occComputed.raw_adjustment) : "n/a"}</span></div>
        <div class="panel-row"><span class="label">Capped Adjustment</span><span class="value">${Number.isFinite(occComputed.capped_adjustment) ? pct(occComputed.capped_adjustment) : "n/a"}</span></div>
        <div class="panel-row"><span class="label">Multiplier</span><span class="value">×${num(occ.multiplier, "1.000")}</span></div>
        <div class="panel-row"><span class="label">Price After Occupancy</span><span class="value">$${valOrNA(d.demand?.price_after_occupancy ?? occ.price_after, 2)}</span></div>
      </div>

      <div class="panel-section">
        <h3>Booking Velocity</h3>
        <div class="panel-row"><span class="label">Reason</span><span class="value">${vel.reason || "n/a"}</span></div>
        <div class="panel-row"><span class="label">Recent Bookings / Window</span><span class="value">${velInputs.recent_bookings ?? "n/a"} / ${velInputs.recent_window_days ?? "n/a"}d</span></div>
        <div class="panel-row"><span class="label">Baseline Bookings / Window</span><span class="value">${velInputs.baseline_bookings ?? "n/a"} / ${velInputs.baseline_window_days ?? "n/a"}d</span></div>
        <div class="panel-row"><span class="label">Recent BPD</span><span class="value">${valOrNA(velComputed.recent_bpd)}</span></div>
        <div class="panel-row"><span class="label">Baseline BPD</span><span class="value">${valOrNA(velComputed.baseline_bpd)}</span></div>
        <div class="panel-row"><span class="label">Velocity Ratio</span><span class="value">${Number.isFinite(velComputed.velocity_ratio) ? velComputed.velocity_ratio.toFixed(2) + "x" : "n/a"}</span></div>
        <div class="panel-row"><span class="label">Velocity Delta</span><span class="value">${Number.isFinite(velComputed.velocity_delta) ? pct(velComputed.velocity_delta) : "n/a"}</span></div>
        <div class="panel-row"><span class="label">Raw Adjustment</span><span class="value">${Number.isFinite(velComputed.raw_adjustment) ? pct(velComputed.raw_adjustment) : "n/a"}</span></div>
        <div class="panel-row"><span class="label">Capped Adjustment</span><span class="value">${Number.isFinite(velComputed.capped_adjustment) ? pct(velComputed.capped_adjustment) : "n/a"}</span></div>
        <div class="panel-row"><span class="label">Multiplier</span><span class="value">×${num(vel.multiplier, "1.000")}</span></div>
        <div class="panel-row"><span class="label">Price After Velocity</span><span class="value">$${valOrNA(d.demand?.price_after_velocity ?? vel.price_after, 2)}</span></div>
      </div>

      <div class="panel-section">
        <h3>Final Clamp</h3>
        <div class="panel-row"><span class="label">Raw Adjusted</span><span class="value">$${(d.raw_adjusted_price ?? d.final_price).toFixed(2)}</span></div>
        <div class="panel-row"><span class="label">Final Recommended</span><span class="value">$${(d.final_recommended ?? d.final_price).toFixed(2)}</span></div>
      </div>

      ${d.event.suggested_price != null ? `
      <div class="panel-section">
        <h3>Event</h3>
        <div class="panel-row"><span class="label">Suggested</span><span class="value">$${d.event.suggested_price.toFixed(2)}</span></div>
        ${d.event.factors.local_event ? `<div class="panel-row"><span class="label">Local Event</span><span class="value">${d.event.factors.local_event}</span></div>` : ""}
      </div>
      ` : ""}
    `;
  }

  // Public API
  return { open };
})();
