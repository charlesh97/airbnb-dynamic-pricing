/* ── properties_modal.js — Add Property discovery modal ───── */

import { api } from "./api.js";

let selectedUids = new Set();
let availableProperties = [];

function openModal() {
  const modal = document.getElementById("add-property-modal");
  if (!modal) return;
  modal.classList.remove("hidden");
  selectedUids.clear();
  loadDiscovery();
}

function closeModal() {
  const modal = document.getElementById("add-property-modal");
  if (modal) modal.classList.add("hidden");
}

async function loadDiscovery() {
  const content = document.getElementById("add-property-content");
  if (!content) return;
  content.innerHTML = '<div class="text-center py-12 text-on-surface-variant">Loading...</div>';
  try {
    const props = await api.get("/api/properties/discover");
    availableProperties = props || [];
    renderPropertyList(availableProperties);
  } catch (e) {
    content.innerHTML = '<div class="text-center py-12 text-secondary">Failed to load properties.</div>';
    console.error(e);
  }
}

function renderPropertyList(props) {
  const content = document.getElementById("add-property-content");
  if (!content) return;
  if (props.length === 0) {
    content.innerHTML = '<div class="text-center py-12 text-on-surface-variant">No properties found in iGMS.</div>';
    return;
  }
  const added = props.filter(p => p.has_local_config);
  const available = props.filter(p => !p.has_local_config);

  let html = `<div class="mb-3 flex items-center justify-between">
    <span class="text-label-sm text-on-surface-variant">${props.length} properties found</span>
    <span class="text-label-sm text-on-surface-variant">${added.length} already added</span>
  </div>
  <div class="overflow-auto max-h-80 border border-surface-container rounded-xl">
    <table class="w-full text-sm">
      <thead class="sticky top-0 bg-surface-container-lowest">
        <tr class="border-b border-surface-container">
          <th class="text-left py-2 px-3 text-label-sm text-on-surface-variant"></th>
          <th class="text-left py-2 px-3 text-label-sm text-on-surface-variant">Name</th>
          <th class="text-left py-2 px-3 text-label-sm text-on-surface-variant">UID</th>
          <th class="text-left py-2 px-3 text-label-sm text-on-surface-variant">State</th>
        </tr>
      </thead>
      <tbody>`;

  for (const p of props) {
    const isAdded = p.has_local_config;
    const rowClass = isAdded ? "opacity-50" : "hover:bg-surface-container-low cursor-pointer";
    html += `<tr class="property-row ${rowClass} border-b border-surface-container-low" data-uid="${p.property_uid}">
      <td class="py-2 px-3">${isAdded
        ? '<span class="text-label-sm text-on-surface-variant">Added</span>'
        : `<input type="checkbox" class="property-checkbox w-4 h-4 rounded border-outline text-primary" value="${p.property_uid}">`
      }</td>
      <td class="py-2 px-3 text-on-surface font-medium">${p.name}</td>
      <td class="py-2 px-3 text-on-surface-variant text-xs font-mono">${p.property_uid}</td>
      <td class="py-2 px-3 text-on-surface-variant">${p.state}</td>
    </tr>`;
  }
  html += `</tbody></table></div>`;

  html += `<div class="mt-4 flex items-center justify-end gap-3">
    <span id="add-property-selected-count" class="text-label-sm text-on-surface-variant"></span>
    <button id="add-property-confirm-btn" class="bg-primary text-on-primary px-6 py-2.5 rounded-full text-label-sm font-bold shadow-md hover:opacity-90 transition-opacity" disabled>
      Add Selected
    </button>
  </div>`;

  content.innerHTML = html;

  content.querySelectorAll(".property-checkbox").forEach(cb => {
    cb.addEventListener("change", () => {
      const uid = cb.value;
      if (cb.checked) {
        selectedUids.add(uid);
      } else {
        selectedUids.delete(uid);
      }
      updateConfirmBtn();
    });
  });

  const confirmBtn = document.getElementById("add-property-confirm-btn");
  if (confirmBtn) {
    confirmBtn.addEventListener("click", addSelectedProperties);
  }
}

function updateConfirmBtn() {
  const btn = document.getElementById("add-property-confirm-btn");
  const count = document.getElementById("add-property-selected-count");
  if (!btn) return;
  const countVal = selectedUids.size;
  btn.disabled = countVal === 0;
  if (count) count.textContent = countVal > 0 ? `${countVal} selected` : "";
}

async function addSelectedProperties() {
  const btn = document.getElementById("add-property-confirm-btn");
  if (btn) { btn.disabled = true; btn.textContent = "Adding..."; }

  const toAdd = [...selectedUids];
  for (const uid of toAdd) {
    try {
      await api.post("/api/properties/add", { property_uid: uid });
    } catch (e) {
      console.error("Failed to add property:", uid, e);
    }
  }

  // Refresh the property dropdown
  try {
    const updated = await api.get("/api/properties");
    const switcher = document.getElementById("property-switcher");
    if (switcher && updated) {
      const current = localStorage.getItem("atlas_property_uid") || switcher.value;
      switcher.innerHTML = updated.map(p =>
        `<option value="${p.property_uid}">${p.name}</option>`
      ).join("");

      const hasCurrent = updated.some(p => p.property_uid === current);
      if (hasCurrent) {
        switcher.value = current;
        localStorage.setItem("atlas_property_uid", current);
      } else if (updated.length > 0) {
        switcher.value = updated[0].property_uid;
        localStorage.setItem("atlas_property_uid", updated[0].property_uid);
      }
    }
  } catch (e) {
    console.error("Failed to refresh property list:", e);
  }

  setTimeout(closeModal, 800);
}

// Wire up button and modal close
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("add-property-btn")?.addEventListener("click", openModal);
  document.getElementById("add-property-close")?.addEventListener("click", closeModal);
  document.getElementById("add-property-backdrop")?.addEventListener("click", closeModal);
});
