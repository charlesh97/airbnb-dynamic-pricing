# Local-Only Property Management + Manual "Add Property" Flow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dashboard dropdown show only local JSON properties, add an "Add Property" flow to discover and add iGMS properties, make CLI global commands process only local properties, fix save-button shifting in config editor, and show property UID below calendar.

**Architecture:** Local JSON files in `config/properties/` become the sole source of truth for managed properties. iGMS is only a discovery source when user explicitly clicks "Add Property". The `get_properties()` function in `engine_proxy.py` is refactored to read only local JSONs. New endpoints (`/api/properties/discover`, `/api/properties/add`) handle discovery and creation. CLI global commands (`status`, `run`, `dry-run`, `push`) switch from `client.get_all_properties()` to `PropertyConfigStore().list_properties()`.

**Tech Stack:** Python/FastAPI (dashboard), PropertyConfigStore for local JSON CRUD, existing PricingClient for iGMS discovery reads only, JavaScript modules for modal UI.

---

## File Structure

**Modified:**
- `dashboard/engine_proxy.py` — `get_properties()` becomes local-only
- `dashboard/routes/calendar.py` — add discover + add endpoints
- `src/pricing_engine/cli.py` — `cmd_status`, `cmd_run`, `cmd_dry_run`, `cmd_push` switch to local config store
- `dashboard/templates/base.html` — add "Add Property" button + modal container
- `dashboard/templates/calendar.html` — add property UID reference below legend
- `dashboard/static/js/calendar.js` — set `#property-uid-ref` on init/property change
- `dashboard/static/js/config_editor.js` — fix dirty indicator visibility (not display:none/block)
- `dashboard/templates/config_editor.html` — save bar layout fix for fixed button position

**Created:**
- `dashboard/static/js/properties_modal.js` — discovery modal logic

---

## Task 1: Make `get_properties()` local-only

**Files:**
- Modify: `dashboard/engine_proxy.py:406-445`

- [ ] **Step 1: Write the test**

```python
# tests/test_engine_proxy.py (add)
import json, tempfile, shutil
from pathlib import Path

def test_get_properties_local_only(tmp_path):
    """get_properties() returns only local JSON files, no iGMS calls."""
    # Create a temporary config dir with 2 JSON files
    config_dir = tmp_path / "properties"
    config_dir.mkdir()
    (config_dir / "uid1.json").write_text(json.dumps({
        "property_uid": "uid1", "name": "Alpha", "state": "CA"
    }))
    (config_dir / "uid2.json").write_text(json.dumps({
        "property_uid": "uid2", "name": "Beta", "state": "VA"
    }))

    # Patch config_dir to use tmp_path
    from dashboard.engine_proxy import _CONFIG_STORE
    orig_dir = _CONFIG_STORE.config_dir
    _CONFIG_STORE.config_dir = config_dir

    from dashboard.engine_proxy import get_properties
    result = get_properties()

    _CONFIG_STORE.config_dir = orig_dir  # restore

    assert len(result) == 2
    uids = {r["property_uid"] for r in result}
    assert uids == {"uid1", "uid2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine_proxy.py::test_get_properties_local_only -v`
Expected: FAIL — current `get_properties()` calls iGMS and creates configs

- [ ] **Step 3: Write the implementation**

Replace `get_properties()` (lines 406-445) with:

```python
def get_properties() -> list[dict]:
    """Return all locally-managed properties as {property_uid, name, state}.

    Reads only config/properties/*.json files. Does NOT call iGMS.
    Sorts by name then uid.
    """
    discovered: dict[str, dict[str, Any]] = {}

    for fname in sorted(Path(_CONFIG_STORE.config_dir).glob("*.json")):
        try:
            d = json.loads(fname.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Skipping unreadable property config: %s", fname)
            continue
        uid = str(d.get("property_uid") or fname.stem).strip()
        if not uid:
            continue
        discovered[uid] = {
            "property_uid": uid,
            "name": str(d.get("name") or f"Property {uid}"),
            "state": str(d.get("state") or "CA"),
        }

    return sorted(discovered.values(), key=lambda p: (p.get("name", "").lower(), p.get("property_uid", "")))
```

Also remove these functions that are no longer needed (or keep them but don't call from `get_properties`):
- `_normalize_igms_properties()` — keep for discover endpoint
- `_build_default_property_config()` — keep for add endpoint
- `_upsert_discovered_property_config()` — refactor for add endpoint use only

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_engine_proxy.py::test_get_properties_local_only -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/engine_proxy.py tests/test_engine_proxy.py
git commit -m "feat: get_properties() returns only local JSON configs"
```

---

## Task 2: Add `GET /api/properties/discover` endpoint

**Files:**
- Modify: `dashboard/routes/calendar.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_routes.py
def test_discover_endpoint_returns_igms_list(monkeypatch):
    """GET /api/properties/discover returns iGMS list with has_local_config flags."""
    # Mock iGMS response
    mock_props = [
        {"property_uid": "p1", "name": "Prop One", "state": "CA"},
        {"property_uid": "p2", "name": "Prop Two", "state": "TX"},
    ]
    monkeypatch.setattr("dashboard.engine_proxy._get_pricing_client")  # mocked client returning mock_props above
    # Also need local config for p1 to test has_local_config=true
    ...

def test_discover_no_file_writes():
    """Discover endpoint does not create any files."""
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — endpoint doesn't exist yet

- [ ] **Step 3: Write the implementation**

In `dashboard/routes/calendar.py`, add:

```python
@router.get("/properties/discover")
async def discover_properties():
    """Fetch all iGMS properties and mark which ones have local configs.

    Returns list of {property_uid, name, state, has_local_config} sorted by name+uid.
    Read-only: no file writes.
    """
    try:
        client = _get_pricing_client()
        raw = client.get_all_properties()
        igms_props = _normalize_igms_properties(raw)
    except Exception:
        logger.exception("discover: iGMS fetch failed")
        return []

    local_uids = set(Path(_CONFIG_STORE.config_dir).glob("*.json"))
    local_uids = {p.stem for p in local_uids}

    result = []
    for p in igms_props:
        uid = str(p.get("property_uid") or "").strip()
        if not uid:
            continue
        location = p.get("location") or {}
        result.append({
            "property_uid": uid,
            "name": str(p.get("name") or f"Property {uid}"),
            "state": str(p.get("state") or location.get("state") or "CA"),
            "has_local_config": uid in local_uids,
        })

    return sorted(result, key=lambda x: (x.get("name", "").lower(), x.get("property_uid", "")))
```

Import `_CONFIG_STORE` and `Path` at top of calendar.py (they're already available from `engine_proxy` imports).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_routes.py -v -k discover`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/routes/calendar.py tests/test_routes.py
git commit -m "feat: add GET /api/properties/discover read-only endpoint"
```

---

## Task 3: Add `POST /api/properties/add` endpoint

**Files:**
- Modify: `dashboard/routes/calendar.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_routes.py
def test_add_property_creates_json(tmp_path):
    """POST /api/properties/add with new uid creates config file and returns created."""
    ...

def test_add_property_existing_returns_exists(tmp_path):
    """POST /api/properties/add with existing uid returns {status: 'exists'}."""
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — endpoint doesn't exist

- [ ] **Step 3: Write the implementation**

Add to `dashboard/routes/calendar.py`:

```python
@router.post("/properties/add")
async def add_property(body: dict):
    """Add a property by creating its local config from iGMS discovery data.

    Request body: {"property_uid": "<uid>"}
    Returns: {"status": "created", "property_uid": "...", "name": "..."}
            or {"status": "exists"} if already on disk
    Does NOT overwrite existing files.
    """
    uid = str(body.get("property_uid") or "").strip()
    if not uid:
        return {"error": "property_uid is required"}

    config_path = Path(_CONFIG_STORE.config_dir) / f"{uid}.json"
    if config_path.exists():
        return {"status": "exists", "property_uid": uid}

    try:
        client = _get_pricing_client()
        raw = client.get_all_properties()
        igms_props = _normalize_igms_properties(raw)
    except Exception:
        logger.exception("add_property: iGMS fetch failed")
        return {"error": "Failed to fetch iGMS properties"}

    igms_match = None
    for p in igms_props:
        if str(p.get("property_uid") or "").strip() == uid:
            igms_match = p
            break

    if igms_match is None:
        return {"error": f"Property {uid} not found in iGMS"}

    name = str(igms_match.get("name") or f"Property {uid}").strip()
    location = igms_match.get("location") or {}
    state = str(igms_match.get("state") or location.get("state") or "CA").strip() or "CA"

    config = _build_default_property_config(uid, name, state, igms_match)
    _CONFIG_STORE.save(uid, config)

    return {"status": "created", "property_uid": uid, "name": name}
```

Note: `_build_default_property_config` and `_CONFIG_STORE` need to be imported from `engine_proxy` at the top.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_routes.py -v -k add_property`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/routes/calendar.py tests/test_routes.py
git commit -m "feat: add POST /api/properties/add endpoint"
```

---

## Task 4: Add "Add Property" UI — button + modal container in base.html

**Files:**
- Modify: `dashboard/templates/base.html:103-110`
- Create: `dashboard/static/js/properties_modal.js`

- [ ] **Step 1: Add "Add Property" button to base.html**

In `base.html`, change the property switcher section from:

```html
<select id="property-switcher" ...>
  {% for prop in properties %}...
</select>
```

To:

```html
<div class="flex items-center gap-2">
  <select id="property-switcher" ...>
    {% for prop in properties %}...
  </select>
  <button id="add-property-btn" class="flex items-center gap-1.5 px-3 py-2 rounded-full bg-surface-container-low border border-surface-variant text-on-surface-variant text-label-sm font-semibold hover:bg-surface-container-high hover:text-on-surface transition-all">
    <span class="material-symbols-outlined text-sm">add</span>
    Add Property
  </button>
</div>
```

Also add modal container markup before `{% block scripts %}`:

```html
<div id="add-property-modal" class="fixed inset-0 z-50 hidden">
  <div class="absolute inset-0 bg-black/40 backdrop-blur-sm" id="add-property-backdrop"></div>
  <div class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg bg-surface-container-lowest rounded-2xl shadow-2xl p-6 border border-surface-container">
    <div class="flex items-center justify-between mb-4">
      <h3 class="text-headline-lg-mobile text-on-surface">Add Property</h3>
      <button id="add-property-close" class="w-8 h-8 rounded-full bg-surface-container-high flex items-center justify-center text-on-surface-variant hover:bg-surface-container-highest transition-colors">
        <span class="material-symbols-outlined text-sm">close</span>
      </button>
    </div>
    <div id="add-property-content">
      <div class="text-center py-12 text-on-surface-variant">Loading...</div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Write properties_modal.js**

Create `dashboard/static/js/properties_modal.js`:

```javascript
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

  // Wire up checkbox listeners
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
  const results = [];
  for (const uid of toAdd) {
    try {
      const r = await api.post("/api/properties/add", { property_uid: uid });
      results.push({ uid, success: r.status !== "exists", result: r });
    } catch (e) {
      results.push({ uid, success: false, error: e });
    }
  }

  const created = results.filter(r => r.success).length;
  const alreadyExisted = results.filter(r => !r.success).length;

  // Refresh the property dropdown
  try {
    const updated = await api.get("/api/properties");
    const switcher = document.getElementById("property-switcher");
    if (switcher && updated) {
      switcher.innerHTML = updated.map(p =>
        `<option value="${p.property_uid}">${p.name}</option>`
      ).join("");
    }
  } catch (e) {
    console.error("Failed to refresh property list:", e);
  }

  // Close modal
  setTimeout(closeModal, 800);
}

// Wire up button and modal close
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("add-property-btn")?.addEventListener("click", openModal);
  document.getElementById("add-property-close")?.addEventListener("click", closeModal);
  document.getElementById("add-property-backdrop")?.addEventListener("click", closeModal);
});
```

- [ ] **Step 3: Include the new JS module in base.html**

Add to the scripts block in `base.html`:

```html
<script type="module" src="/static/js/properties_modal.js"></script>
```

Note: Since base.html uses `{% block scripts %}{% endblock %}` and calendar.html/config_editor.html extend it, the modal JS should be in base.html directly (not a child block). It gets included on every page.

- [ ] **Step 4: Verify the build**

Run the dashboard and check for JS syntax errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/templates/base.html dashboard/static/js/properties_modal.js
git commit -m "feat: add Add Property modal with iGMS discovery"
```

---

## Task 5: Fix save-button shifting in config editor

**Files:**
- Modify: `dashboard/templates/config_editor.html:272-280`
- Modify: `dashboard/static/js/config_editor.js:501-503`

- [ ] **Step 1: Identify the CSS issue**

The save bar in `config_editor.html` (lines 272-280):
```html
<div class="sticky bottom-6 flex items-center justify-between ...>
  <div class="text-label-sm text-on-surface-variant" id="dirty-indicator" style="display:none">⚠️ Unsaved changes</div>
  <div class="flex items-center gap-4">
    <button id="save-config-btn" ...>Save Config</button>
  </div>
</div>
```

The `dirty-indicator` div uses `display:none` when not dirty, and `display:inline` when dirty. This means the left slot changes width between states, pushing the button right when dirty state appears.

- [ ] **Step 2: Fix the HTML layout**

Change the save bar to use fixed-width slots:

```html
<div class="sticky bottom-6 flex items-center justify-between bg-surface-container-lowest rounded-full px-6 py-4 shadow-[0_10px_30px_rgba(33,150,243,0.12)] border border-surface-container-low">
  <div class="w-40 text-label-sm text-on-surface-variant" id="dirty-indicator" style="visibility:hidden;opacity:0">
    ⚠️ Unsaved changes
  </div>
  <div class="flex items-center gap-4">
    <button id="save-config-btn" class="bg-primary text-on-primary px-8 py-3 rounded-full font-label-sm font-bold shadow-md hover:opacity-90 transition-opacity flex items-center gap-2" disabled>
      <span class="material-symbols-outlined text-sm">save</span> Save Config
    </button>
  </div>
</div>
```

Key changes:
- Left slot has fixed `w-40` so it doesn't collapse
- Uses `visibility:hidden;opacity:0` instead of `display:none` so layout space is preserved
- Right slot (button) is in its own flex container

- [ ] **Step 3: Fix setDirty() in config_editor.js**

Change `setDirty()` (lines 501-503):

```javascript
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
```

- [ ] **Step 4: Verify the fix visually**

Load the config editor page, make a field change (dirty state), confirm save button does not shift position.

- [ ] **Step 5: Commit**

```bash
git add dashboard/templates/config_editor.html dashboard/static/js/config_editor.js
git commit -m "fix: prevent save button shifting with fixed-width dirty indicator slot"
```

---

## Task 6: Show property UID below calendar

**Files:**
- Modify: `dashboard/templates/calendar.html:44-47`
- Modify: `dashboard/static/js/calendar.js`

- [ ] **Step 1: Update calendar.html**

In `calendar.html`, change the legend section to include the UID reference:

```html
<div class="flex items-center gap-6 mt-6 text-label-sm text-on-surface-variant">
  <span class="flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-secondary-container shadow-[0_0_8px_rgba(220,49,40,0.6)]"></span> High-demand / Holiday</span>
  <span class="flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-surface-variant"></span> Normal</span>
  <span class="ml-auto text-on-surface-variant text-xs font-mono" id="property-uid-ref-container">Property UID: <span id="property-uid-ref"></span></span>
</div>
```

- [ ] **Step 2: Update calendar.js**

In `calendar.js`, update `initCalendar()` to set the UID reference after the switcher is synced:

Find in `initCalendar()` (around line 103-104):
```javascript
const switcher = document.getElementById("property-switcher");
if (switcher) switcher.value = currentUid;
```

Add below that:
```javascript
const uidRef = document.getElementById("property-uid-ref");
if (uidRef) uidRef.textContent = currentUid || getPropertyUid();
```

Also update the property change handler in the switcher's change listener (line 35-41 area) to update the UID ref when the user switches properties. The `change` event handler on the switcher already has `localStorage.setItem('atlas_property_uid', switcher.value)` — add:
```javascript
const uidRef = document.getElementById("property-uid-ref");
if (uidRef) uidRef.textContent = switcher.value;
```

And also in `loadMonth()` after the property uid is set (around line 122):
```javascript
const uidRef = document.getElementById("property-uid-ref");
if (uidRef) uidRef.textContent = propertyUid;
```

- [ ] **Step 3: Verify**

Load the calendar page, check that the UID appears below the legend.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/calendar.html dashboard/static/js/calendar.js
git commit -m "feat: show property UID below calendar legend"
```

---

## Task 7: Make CLI global commands local-only

**Files:**
- Modify: `src/pricing_engine/cli.py`

Focus on these commands:
- `cmd_status` (line 281-349)
- `cmd_run` (line 352-384)
- `cmd_dry_run` (line 387-389 — just calls cmd_run)
- `cmd_push` (line 392-479)

- [ ] **Step 1: Write tests**

```python
# tests/test_cli_local_only.py
def test_status_iterates_local_only_properties(tmp_path, monkeypatch):
    """cmd_status should iterate only local JSON property UIDs."""
    ...

def test_run_iterates_local_only_properties(tmp_path, monkeypatch):
    """cmd_run should iterate only local JSON property UIDs."""
    ...

def test_push_iterates_local_only_properties(tmp_path, monkeypatch):
    """cmd_push should iterate only local JSON property UIDs."""
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_local_only.py -v`
Expected: FAIL — commands still use `client.get_all_properties()`

- [ ] **Step 3: Implement the changes**

In `cmd_status`, replace:
```python
properties = client.get_all_properties()
```
with:
```python
store = PropertyConfigStore()
uids = store.list_properties()
if not uids:
    console.print("[red]No local property configs found in config/properties/[/red]")
    return
```

And update the loop to load each property config:
```python
for uid in uids:
    prop_config = store.load(uid)
    if not prop_config:
        continue
    name = prop_config.get("name", uid)
    # ... rest of the existing loop logic
```

In `cmd_run`, do the same replacement. The key change is replacing:
```python
properties = client.get_all_properties()
for prop in properties:
    uid = prop.get("property_uid")
```
with:
```python
store = PropertyConfigStore()
uids = store.list_properties()
for uid in uids:
    prop_config = store.load(uid)
    if not prop_config:
        continue
    name = prop_config.get("name", uid)
```

In `cmd_push`, same pattern.

Note: `PropertyConfigStore` is already imported at line 17. The `PricingClient` is still needed for `get_calendar()` calls (price fetching), but it's now called per-uid from the local config list rather than from an iGMS property list.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_local_only.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pricing_engine/cli.py tests/test_cli_local_only.py
git commit -m "feat: make CLI global commands process only local JSON properties"
```

---

## Verification Checklist

After all tasks, verify each requirement:

1. **Local-only dropdown**: With exactly 2 local JSON files in `config/properties/`, `GET /api/properties` returns 2 items and dropdown shows 2.

2. **Discovery endpoint**: `GET /api/properties/discover` returns iGMS list with correct `has_local_config` flags.

3. **Add flow**:
   - Adding new UID creates one JSON file in `config/properties/`
   - Adding same UID again returns `{"status": "exists"}`
   - After adding, dropdown refreshes with new property

4. **Save bar**: Button position unchanged before and after dirty state.

5. **Calendar UID**: Below legend shows the correct property UID, updates on property switch.

6. **CLI commands**: `status`, `run`, `dry-run`, `push` iterate only local JSON UIDs, not all iGMS properties.