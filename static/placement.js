/**
 * placement.js — Placement API calls, drag/drop wiring, job panel updates
 */

var Placement = (() => {
  // A placement held back until its tools resolve. Resolution is strict and happens
  // before the bed (spec §3.5.3): an unresolved tool has no declared radius, the
  // library is its only source, and the app must not invent one.
  let _pending  = null;   // {path, rail, slot_inches}
  let _queue    = [];     // unresolved tools still needing an answer
  let _lastPath = "";

  // ── place via API ─────────────────────────────────────────────────────────
  async function place(path, rail, slot_inches) {
    const r = await fetch("/api/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, rail, slot_inches }),
    });
    const data = await r.json();

    if (r.status === 422 && data.error === "unresolved_tools") {
      _pending = { path, rail, slot_inches };
      openResolver(data.tools.map(t => ({ ...t, path })), path);
      return;
    }

    if (r.status === 422 && data.error === "description_changed") {
      // Blocks rather than warns: one code on two physical cutters is a wrong-tool cut,
      // and it is the only thing that can detect it across files.
      _pending = { path, rail, slot_inches };
      ToolLib.openSeal(
        data.prompts.map(p => ({ ...p, filename: path })),
        () => _retryPending());
      return;
    }

    if (r.status === 422 && data.error === "duplicate_tool_code") {
      App.setMessage(data.message, true);
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
      App.setMessage(data.message || data.error || "Placement failed", true);
      return;
    }

    await refresh();
  }

  async function remove(instance_id) {
    await fetch(`/api/place/${instance_id}`, { method: "DELETE" });
    await refresh();
  }

  async function refresh() {
    const r = await fetch("/api/placements").then(res => res.json());
    App.placements        = r.placements || [];
    App.changer           = r.changer || {};
    App.jobSafeZ          = r.job_safe_z || {};
    App.toolSequence      = r.tool_sequence || [];
    App.toolChanges       = r.tool_changes ?? 0;
    App.utilization       = r.utilization ?? 0;
    App.runtimeSeconds    = r.runtime_seconds ?? 0;
    App.onPlacementsChanged();
  }

  function _retryPending() {
    if (!_pending) { refresh(); return; }
    const { path, rail, slot_inches } = _pending;
    _pending = null;
    place(path, rail, slot_inches);
  }

  // ── the resolver: identify a tool the library does not know ───────────────
  //
  // The orphan path is **the floor, not a failure mode** — every file in the library
  // predates the shop code, so all of them land here until they are re-posted. It is
  // built to be pleasant rather than punitive: for a one-off with one tool it is two
  // clicks.

  function openResolver(unresolved, path) {
    _queue = unresolved.map(t => ({ ...t, path: t.path || path || _lastPath }));
    if (path) _lastPath = path;
    _renderResolver();
  }

  function _renderResolver() {
    const overlay = document.getElementById("resolve-overlay");
    if (!_queue.length) {
      overlay.classList.remove("open");
      _retryPending();
      return;
    }
    const t = _queue[0];
    document.getElementById("resolve-tool").textContent = t.tool_number;
    document.getElementById("resolve-file").textContent = t.path || _lastPath;
    document.getElementById("resolve-why").textContent = t.status === "unknown_code"
      ? `The file names tool code ${t.code}, which is not in your library yet.`
      : "The file carries no tool code, so there is nothing to match it against.";
    document.getElementById("resolve-posted").textContent =
      t.cam_description || t.description || "(no description in the file)";

    // The file's posted diameter, shown beside the candidates. This is what replaced
    // the retired guard (b): a display, so a gross mismatch is visible, rather than a
    // rule that would refuse correctly declared profile bits (§3.5.3).
    const posted = document.getElementById("resolve-dia");
    posted.textContent = t.posted_diameter_inches
      ? `The file says this cutter is ${t.posted_diameter_inches.toFixed(3)}" across. ` +
        `Your library's number is what actually gets used — it should be the widest point.`
      : "The file gives no usable diameter, which is normal.";

    const select = document.getElementById("resolve-pick");
    select.innerHTML = '<option value="">— choose a tool —</option>' +
      ToolLib.tools().map(x =>
        `<option value="${x.code}">${x.code} — ${x.name} · ${x.display}</option>`).join("");

    document.getElementById("resolve-error").textContent = "";
    document.getElementById("resolve-count").textContent =
      _queue.length > 1 ? `${_queue.length} tools to identify` : "";
    overlay.classList.add("open");
  }

  async function _bindResolver() {
    const t = _queue[0];
    const code = document.getElementById("resolve-pick").value;
    if (!code) {
      document.getElementById("resolve-error").textContent = "Pick a tool, or add a new one.";
      return;
    }
    const r = await fetch("/api/bind-tool", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: t.path || _lastPath, tool_number: t.tool_number, code }),
    });
    const data = await r.json();
    if (!data.ok) {
      document.getElementById("resolve-error").textContent = data.error || "Could not bind that tool.";
      return;
    }
    _queue.shift();
    _renderResolver();
  }

  async function _createFromResolver() {
    const t = _queue[0];
    document.getElementById("resolve-overlay").classList.remove("open");
    await ToolLib.open("");
    const code = document.getElementById("tl-code");
    const name = document.getElementById("tl-name");
    if (code && t.code) code.value = t.code;
    if (name && !name.value) name.value = t.cam_description || t.description || "";
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
    const { instance_id, rail: origRail, slot_inches: origSlot, path } = _canvasDragOrigin;
    _canvasDragOrigin = null;

    if (targetSlot.rail && targetSlot.slot_inches !== undefined) {
      await remove(instance_id);
      const r = await fetch("/api/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, rail: targetSlot.rail, slot_inches: targetSlot.slot_inches }),
      });
      const data = await r.json();

      if (r.status === 409) {
        App.setMessage("Collision: " + data.message, true);
        // Snap back to original position
        await fetch("/api/place", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, rail: origRail, slot_inches: origSlot }),
        });
      } else if (!data.ok) {
        App.setMessage(data.error || "Placement failed", true);
        await fetch("/api/place", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, rail: origRail, slot_inches: origSlot }),
        });
      }

      await refresh();
    }
  }

  // ── drop from sidebar ─────────────────────────────────────────────────────
  async function placeFromDrop(path, rail, slot_inches) {
    await place(path, rail, slot_inches);
  }

  // ── init ──────────────────────────────────────────────────────────────────
  async function init() {
    document.getElementById("resolve-cancel").addEventListener("click", () => {
      document.getElementById("resolve-overlay").classList.remove("open");
      _pending = null;
      _queue = [];
    });
    document.getElementById("resolve-bind").addEventListener("click", _bindResolver);
    document.getElementById("resolve-create").addEventListener("click", _createFromResolver);

    // Placement remove buttons (delegated from job panel)
    document.getElementById("placements-list").addEventListener("click", e => {
      const btn = e.target.closest(".remove-btn");
      if (btn) remove(btn.dataset.id);
    });

    await ToolLib.load();
    ToolLib.init();
    Changer.init();
    await refresh();
  }

  return { init, place, remove, refresh, placeFromDrop, openResolver,
           beginCanvasDrag, endCanvasDrag };
})();


var JobPanel = (() => {
  function refresh() {
    _renderPlacements();
    _renderSafeZ();
  }

  function _renderPlacements() {
    const list = document.getElementById("placements-list");
    if (!list) return;
    list.innerHTML = "";
    const frees = (App.changer && App.changer.frees_by_instance) || {};
    const stuck = App.changer && App.changer.needs_removal;
    for (const p of App.placements) {
      const row = document.createElement("div");
      row.className = "placement-row";
      const color = window.BedCanvas ? BedCanvas.getColor(p.filename) : "#4dabf7";
      // "Removing bracket-L frees pocket 7" — shown only when a pocket is what the
      // operator is actually short of, so it is advice rather than decoration.
      const n = frees[p.instance_id] || 0;
      row.innerHTML = `
        <span class="slot-label" style="color:${color}">${p.slot}</span>
        <span class="pname" title="${p.filename}">${p.filename}</span>
        ${stuck && n ? `<span class="frees" title="No other placed part needs ${n === 1 ? "one of its tools" : "these tools"}">frees ${n}</span>` : ""}
        <button class="remove-btn" data-id="${p.instance_id}" title="Remove">×</button>
      `;
      list.appendChild(row);
    }
    if (!App.placements.length) {
      list.innerHTML = '<div style="padding:8px 10px;color:#555;font-size:12px">No parts placed</div>';
    }
  }

  function _renderSafeZ() {
    const el = document.getElementById("safe-z-info");
    if (!el) return;
    const z = App.jobSafeZ;
    const mmToIn = mm => Math.round(mm / 25.4 * 100) / 100;
    el.textContent = z && z.value
      ? `Safe Z: ${mmToIn(z.value)}" (${z.driven_by})`
      : "Safe Z: —";
  }

  return { refresh };
})();


document.addEventListener("DOMContentLoaded", () => Placement.init());
