/**
 * placement.js — Placement API calls, drag/drop wiring, job panel updates
 */

var Placement = (() => {
  // Pending placement that was blocked by unknown_tools
  let _pendingUnknown = null; // {path, rail, slot_inches, tools: [...]}
  let _unknownQueue   = [];   // tools still needing resolution

  // ── place via API ─────────────────────────────────────────────────────────
  async function place(path, rail, slot_inches) {
    // (b) before API call
    console.log("[drop] (b) before /api/place", { path, rail, slot_inches });
    const r = await fetch("/api/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, rail, slot_inches }),
    });
    const data = await r.json();
    // (c) response received
    console.log("[drop] (c) /api/place response", { status: r.status, data });

    if (r.status === 422 && data.error === "unknown_tools") {
      _pendingUnknown = { path, rail, slot_inches };
      _unknownQueue = [...data.tools];
      _promptNextUnknown();
      return;
    }

    if (r.status === 422 && data.error === "z_blocked") {
      App.setMessage("Blocked: " + data.message, true);
      return;
    }

    if (r.status === 409) {
      App.setMessage("Collision: " + data.message, true);
      return;
    }

    if (!data.ok) {
      App.setMessage(data.error || "Placement failed", true);
      return;
    }

    await _refreshPlacements();
  }

  async function remove(instance_id) {
    await fetch(`/api/place/${instance_id}`, { method: "DELETE" });
    await _refreshPlacements();
  }

  async function _refreshPlacements() {
    const r = await fetch("/api/placements").then(res => res.json());
    App.placements        = r.placements || [];
    App.compatibility     = r.compatibility || {};
    App.jobSafeZ          = r.job_safe_z || {};
    // (d) placements state updated
    console.log("[drop] (d) placements state updated", App.placements);
    App.onPlacementsChanged();
  }

  // ── unknown tool modal ────────────────────────────────────────────────────
  function _promptNextUnknown() {
    if (_unknownQueue.length === 0) {
      // All resolved — retry the placement
      if (_pendingUnknown) {
        const { path, rail, slot_inches } = _pendingUnknown;
        _pendingUnknown = null;
        place(path, rail, slot_inches);
      }
      return;
    }

    const tool = _unknownQueue[0];
    document.getElementById("modal-tool-num").textContent = tool.tool_number;
    document.getElementById("modal-tool-dia").value = "";
    document.getElementById("modal-tool-save").checked = true;
    const overlay = document.getElementById("modal-overlay");
    overlay.classList.add("open");
    document.getElementById("modal-tool-dia").focus();
  }

  function _closeModal() {
    document.getElementById("modal-overlay").classList.remove("open");
  }

  async function _confirmTool() {
    const tool    = _unknownQueue[0];
    const dia     = parseFloat(document.getElementById("modal-tool-dia").value);
    const save    = document.getElementById("modal-tool-save").checked;

    if (!dia || dia <= 0) {
      document.getElementById("modal-tool-dia").style.borderColor = "#ff453a";
      return;
    }
    document.getElementById("modal-tool-dia").style.borderColor = "";

    await fetch("/api/resolve-tool", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tool_number: tool.tool_number,
        description: tool.description,
        diameter_inches: dia,
        save_to_library: save,
      }),
    });

    _unknownQueue.shift();
    _closeModal();
    _promptNextUnknown();
  }

  // ── canvas drag (move placed part) ────────────────────────────────────────
  let _canvasDragOrigin = null; // {instance_id, original_slot}

  function beginCanvasDrag(instance_id, cx, cy) {
    const p = App.placements.find(pl => pl.instance_id === instance_id);
    if (!p) return;
    _canvasDragOrigin = { instance_id, rail: p.rail, slot_inches: p.slot_inches, path: p.path };
    BedCanvas.beginDrag({ part: p, instanceId: instance_id });
  }

  async function endCanvasDrag(targetSlot, cx, cy) {
    if (!_canvasDragOrigin || !targetSlot) {
      _canvasDragOrigin = null;
      return;
    }
    const { instance_id, path } = _canvasDragOrigin;
    _canvasDragOrigin = null;

    if (targetSlot.rail && targetSlot.slot_inches !== undefined) {
      await remove(instance_id);
      await place(path, targetSlot.rail, targetSlot.slot_inches);
    }
  }

  // ── drop from sidebar ─────────────────────────────────────────────────────
  async function placeFromDrop(path, rail, slot_inches) {
    await place(path, rail, slot_inches);
  }

  // ── init ──────────────────────────────────────────────────────────────────
  function init() {
    // Modal buttons
    document.getElementById("modal-tool-cancel").addEventListener("click", () => {
      _closeModal();
      _pendingUnknown = null;
      _unknownQueue   = [];
    });
    document.getElementById("modal-tool-confirm").addEventListener("click", _confirmTool);
    document.getElementById("modal-tool-dia").addEventListener("keydown", e => {
      if (e.key === "Enter") _confirmTool();
    });

    // Placement remove buttons (delegated from job panel)
    document.getElementById("placements-list").addEventListener("click", e => {
      const btn = e.target.closest(".remove-btn");
      if (btn) remove(btn.dataset.id);
    });

    // Load initial placements
    _refreshPlacements();
  }

  return { init, place, remove, placeFromDrop, beginCanvasDrag, endCanvasDrag };
})();


var JobPanel = (() => {
  function refresh() {
    _renderPlacements();
    _renderCompat();
    _renderSafeZ();
  }

  function _renderPlacements() {
    const list = document.getElementById("placements-list");
    if (!list) return;
    list.innerHTML = "";
    for (const p of App.placements) {
      const row = document.createElement("div");
      row.className = "placement-row";
      const color = window.BedCanvas ? BedCanvas.getColor(p.filename) : "#4dabf7";
      row.innerHTML = `
        <span class="slot-label" style="color:${color}">${p.slot}</span>
        <span class="pname" title="${p.filename}">${p.filename}</span>
        <button class="remove-btn" data-id="${p.instance_id}" title="Remove">×</button>
      `;
      list.appendChild(row);
    }
    if (!App.placements.length) {
      list.innerHTML = '<div style="padding:8px 10px;color:#555;font-size:12px">No parts placed</div>';
    }
  }

  function _renderCompat() {
    const sec = document.getElementById("compat-section");
    if (!sec) return;
    const matrix = (App.compatibility && App.compatibility.matrix) || [];
    if (!matrix.length) { sec.innerHTML = '<div style="padding:6px 10px;color:#555;font-size:12px">—</div>'; return; }

    sec.innerHTML = matrix.map(t => {
      const icon = t.conflict ? '<span class="compat-conflict">⚠</span>' : '<span class="compat-ok">✓</span>';
      const desc = t.usages.map(u => u.description || "—").join(" / ");
      return `<div class="compat-row">${icon} <strong>${t.tool_number}</strong> <span style="color:#888;font-size:11px">${desc}</span></div>`;
    }).join("");
  }

  function _renderSafeZ() {
    const el = document.getElementById("safe-z-info");
    if (!el) return;
    const z = App.jobSafeZ;
    el.textContent = z && z.value
      ? `Safe Z: ${z.value}mm (${z.driven_by})`
      : "Safe Z: —";
  }

  return { refresh };
})();


document.addEventListener("DOMContentLoaded", () => Placement.init());
