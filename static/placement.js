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
      App.setMessage(_readable(data), true);
      return;
    }

    await refresh();
  }

  /**
   * Never put a raw error slug in front of an operator.
   *
   * `error` is a machine token like `unresolved_tools`; `message` is the sentence
   * written for a person. Preferring the slug is how a stale page reports a new server
   * as the nonsense string "unresolved_tools" with no other clue — which is exactly
   * what happened, and what sent this bug hunt off in the wrong direction.
   */
  function _readable(data) {
    if (data && data.message) return data.message;
    if (data && data.error) {
      console.error("[place] unhandled server error:", data.error, data);
      return "The nest tool could not place that part, and did not say why. " +
             "Reload the page — if this page has been open since before the app was " +
             "updated, it is talking to a newer version of the app than it expects.";
    }
    return "Placement failed";
  }

  async function remove(instance_id) {
    await fetch(`/api/place/${instance_id}`, { method: "DELETE" });
    await refresh();
  }

  /**
   * Take every part off the bed.
   *
   * Confirmed first, because there is no undo and no saved job to fall back on — a
   * nest is rebuilt by hand or not at all. The tray keeps its files, so rebuilding is
   * dragging from the tray rather than hunting the library again.
   */
  async function clearAll() {
    const n = App.placements.length;
    if (!n) return;
    if (!confirm(`Take all ${n} part${n === 1 ? "" : "s"} off the bed?\n\n` +
                 "The parts tray, the tool changer pockets and any tools you " +
                 "identified by hand are kept.")) return;
    await fetch("/api/placements", { method: "DELETE" });
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

  // Why this tool did not resolve, in the operator's terms. Three cases, and the
  // middle one is the one worth naming: a `CODE=` the post wrote but left empty means
  // the tool exists in Fusion with an empty Product ID, so one edit there fixes every
  // file it is ever used in. An absent code is the ordinary floor — every VCarve file
  // and everything posted before the comment existed — and a bind is the whole answer.
  function _whyUnresolved(t) {
    if (t.status === "unknown_code") {
      return `The file names tool code ${t.code}, which is not in your library yet.`;
    }
    if (t.code_field === "") {
      return "This file was posted with an empty tool code: the tool's Product ID " +
             "field is blank in Fusion. Fill it in with the shop code and re-post to " +
             "fix it for good — or say which tool it is, for this job only.";
    }
    return "The file carries no tool code, so there is nothing to match it against.";
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
    document.getElementById("resolve-why").textContent = _whyUnresolved(t);
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

      // One restore path for every failure, not one per error shape. The old code
      // enumerated 409 and "anything else", so a *new* refusal — an unresolved tool
      // after a library edit, say — printed a raw slug and, if the restore was also
      // refused, left the part off the bed entirely with no explanation.
      if (!data.ok) {
        App.setMessage(
          (r.status === 409 ? "Collision: " : "") + _readable(data), true);
        const back = await fetch("/api/place", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, rail: origRail, slot_inches: origSlot }),
        });
        if (!back.ok) {
          App.setMessage(
            `${path} could not be moved there and could not be put back, so it is no ` +
            "longer on the bed. Drag it on again from the tray.", true);
        }
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

    document.getElementById("btn-clear-placements").addEventListener("click", clearAll);

    await ToolLib.load();
    ToolLib.init();
    Changer.init();
    await refresh();
  }

  return { init, place, remove, clearAll, refresh, placeFromDrop, openResolver,
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
