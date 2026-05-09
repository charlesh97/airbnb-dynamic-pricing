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
        <div class="panel-row"><span class="label">Min Stay</span><span class="value">${d.min_stay} nights</span></div>
        <div class="panel-row"><span class="label">Booking Window</span><span class="value">${d.booking_window_days} days</span></div>
      </div>

      <div class="panel-section">
        <h3>Base Rate</h3>
        <div class="panel-row"><span class="label">Base</span><span class="value">$${d.base_rate.toFixed(2)}</span></div>
      </div>

      <div class="panel-section">
        <h3>Seasonal &amp; DOW</h3>
        <div class="panel-row"><span class="label">Rule</span><span class="value">${d.seasonal.rule}</span></div>
        ${d.seasonal.detail ? `<div class="panel-row"><span class="label">Detail</span><span class="value">${d.seasonal.detail}</span></div>` : ""}
        <div class="panel-row"><span class="label">Seasonal Mult</span><span class="value">${d.seasonal.raw_seasonal_multiplier.toFixed(3)}</span></div>
        <div class="panel-row"><span class="label">DOW (${d.seasonal.dow})</span><span class="value">×${d.seasonal.dow_multiplier.toFixed(3)}</span></div>
        <div class="panel-row"><span class="label">Effective</span><span class="value">×${d.seasonal.effective_seasonal.toFixed(3)}</span></div>
      </div>

      <div class="panel-section">
        <h3>Demand</h3>
        <div class="panel-row"><span class="label">Multiplier</span><span class="value">×${d.demand.multiplier.toFixed(3)}</span></div>
        <div class="panel-factor-grid">
          <div class="panel-factor-item">
            <div class="label">Occupancy</div>
            <div class="value">${(d.demand.occupancy.value * 100).toFixed(0)}%</div>
            <div class="label" style="font-size:10px">${d.demand.occupancy.window_days}d window · factor ${d.demand.occupancy.factor}</div>
          </div>
          <div class="panel-factor-item">
            <div class="label">Velocity</div>
            <div class="value">${d.demand.velocity.value.toFixed(2)}/day</div>
            <div class="label" style="font-size:10px">${d.demand.velocity.window_days}d window · factor ${d.demand.velocity.factor}</div>
          </div>
        </div>
        ${d.demand.far_future.active ? `<div class="panel-row"><span class="label">Far Future</span><span class="value">${d.demand.far_future.discount} (${d.demand.far_future.window_days}d out)</span></div>` : ""}
        ${d.demand.last_minute.active ? `<div class="panel-row"><span class="label">Last Minute</span><span class="value">${d.demand.last_minute.discount}</span></div>` : ""}
      </div>

      <div class="panel-section">
        <h3>Strategy Prices</h3>
        <div class="panel-factor-grid">
          ${Object.entries(d.strategy_prices).map(([k, v]) => `
            <div class="panel-factor-item">
              <div class="label">${k}</div>
              <div class="value">${v != null ? "$" + v.toFixed(2) : "—"}</div>
              <div class="label" style="font-size:10px">weight ${d.strategy_weights[k]}</div>
            </div>
          `).join("")}
        </div>
      </div>

      <div class="panel-section">
        <h3>Strategy Weights</h3>
        ${Object.entries(d.strategy_weights).filter(([k]) => k !== "weather").map(([k, v]) => `
          <div class="panel-row"><span class="label">${k}</span><span class="value">${(v * 100).toFixed(1)}%</span></div>
        `).join("")}
      </div>

      ${d.event.suggested_price != null ? `
      <div class="panel-section">
        <h3>Event</h3>
        <div class="panel-row"><span class="label">Suggested</span><span class="value">$${d.event.suggested_price.toFixed(2)}</span></div>
        ${d.event.factors.local_event ? `<div class="panel-row"><span class="label">Local Event</span><span class="value">${d.event.factors.local_event}</span></div>` : ""}
      </div>
      ` : ""}

      ${d.yield.suggested_price != null ? `
      <div class="panel-section">
        <h3>Yield</h3>
        <div class="panel-row"><span class="label">Suggested</span><span class="value">$${d.yield.suggested_price.toFixed(2)}</span></div>
        ${d.yield.factors.yield_score != null ? `<div class="panel-row"><span class="label">Yield Score</span><span class="value">${d.yield.factors.yield_score}</span></div>` : ""}
      </div>
      ` : ""}
    `;
  }

  // Public API
  return { open };
})();
