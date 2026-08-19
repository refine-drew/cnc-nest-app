/**
 * toollib.js — the tool library manager (issue #24, spec §3.5).
 *
 * This UI is load-bearing rather than convenient, for four reasons that are worth
 * keeping in view while editing it:
 *
 *   - **`flute_direction` exists nowhere else.** Nothing from either CAM names up /
 *     down / compression — VCarve writes `End Mill`, Fusion writes `FLAT END MILL`.
 *     The distinction that must never merge can only be declared here.
 *   - **The library is the sole diameter authority**, so this form is the only thing
 *     standing between a rough figure and the table-edge check. Ask for the *widest
 *     cutting point*, never "diameter" unqualified: `.25 Bowl Bit` is 0.75 and
 *     `1/8 Roundover` is 0.3.
 *   - **The default slot is declared here and nowhere else.** A drag never writes back.
 *   - **The code is declared here**, and it is what every file matches on.
 *
 * Two rules that are easy to get backwards: duplicate default slots must be *permitted*
 * (§3.5.6 — the declared library contests slots 2 and 4 on purpose, and that is the
 * motivating case), and `cam_descriptions` is the seal's memory, shown but never edited
 * by hand.
 */

var ToolLib = (() => {
  let library = { tools: [], geometry_classes: [], flute_directions: [], capacity: 8 };
  let editing = null;          // the code being edited, "" for a new tool
  let sealQueue = [];
  let onSealDone = null;

  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function load() {
    library = await fetch("/api/tool-library").then(r => r.json());
    return library;
  }

  function tools() { return library.tools || []; }
  function get(code) { return tools().find(t => t.code === code); }

  // ── the panel ─────────────────────────────────────────────────────────────

  async function open(code) {
    await load();
    document.getElementById("toollib-panel").classList.add("open");
    editing = code !== undefined ? code : (tools()[0] || {}).code || "";
    render();
  }

  function close() {
    document.getElementById("toollib-panel").classList.remove("open");
  }

  function render() {
    const list = document.getElementById("toollib-list");
    list.innerHTML = tools().map(t => {
      const flags = [];
      if (!t.default_slot) flags.push('<span class="chip home">no pocket</span>');
      if (t.cam_descriptions.length > 2)
        flags.push(`<span class="chip home" title="Three accepted descriptions on one code is worth a look">${t.cam_descriptions.length} names</span>`);
      if (t.in_use_by.length) flags.push(`<span class="chip parts">${t.in_use_by.length} placed</span>`);
      return `<div class="tl-row${t.code === editing ? " on" : ""}" data-code="${esc(t.code)}">
        <div class="tl-code">${esc(t.code)}</div>
        <div class="tl-name">${esc(t.name)}</div>
        <div class="tl-geo">${esc(t.display)}${t.default_slot ? ` · pocket ${t.default_slot}` : ""}</div>
        <div class="tl-flags">${flags.join("")}</div>
      </div>`;
    }).join("") || '<div class="tl-empty">No tools yet.</div>';

    list.querySelectorAll(".tl-row").forEach(row =>
      row.addEventListener("click", () => { editing = row.dataset.code; render(); }));

    _renderForm();
  }

  function _renderForm(prefill) {
    const form = document.getElementById("toollib-form");
    const t = prefill || get(editing) || null;
    const isNew = !get(editing);

    const classes = new Set([...(library.geometry_classes || []), ...(t ? [t.geometry_class] : [])]);
    const slotOptions = ['<option value="">— staged, no declared pocket —</option>']
      .concat(Array.from({ length: library.capacity || 8 }, (_, i) =>
        `<option value="${i + 1}"${t && t.default_slot === i + 1 ? " selected" : ""}>Pocket ${i + 1}</option>`))
      .join("");

    form.innerHTML = `
      <div class="tl-form-head">${isNew ? "New tool" : esc(t.name || t.code)}</div>
      <div class="tl-field">
        <label>Shop code <small>you type this same code into Fusion's Product ID field and the VCarve tool name</small></label>
        <input type="text" id="tl-code" value="${esc(t ? t.code : "")}" placeholder="EM-0512"/>
      </div>
      <div class="tl-field">
        <label>Name <small>what you call it — renaming is free and changes nothing else</small></label>
        <input type="text" id="tl-name" value="${esc(t ? t.name : "")}"/>
      </div>
      <div class="tl-field">
        <label>Widest cutting diameter, inches
          <small>the tool's widest point, not the size it is sold by. A ¼" bowl bit is 0.75.</small></label>
        <input type="text" id="tl-dia" value="${t && t.diameter_inches ? t.diameter_inches : ""}" placeholder="0.5"/>
      </div>
      <div class="tl-row2">
        <div class="tl-field">
          <label>Shape</label>
          <select id="tl-class">
            ${Array.from(classes).filter(Boolean).map(c =>
              `<option${t && t.geometry_class === c ? " selected" : ""}>${esc(c)}</option>`).join("")}
            <option value="__new">Add a new shape…</option>
          </select>
        </div>
        <div class="tl-field">
          <label>Flutes</label>
          <select id="tl-flute">
            ${(library.flute_directions || []).map(f =>
              `<option value="${f}"${t && t.flute_direction === f ? " selected" : ""}>${
                { up: "Upcut", down: "Downcut", compression: "Compression", straight: "Straight" }[f] || f
              }</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="tl-row2">
        <div class="tl-field">
          <label>Cutting length, inches <small>optional; tells apart two otherwise-identical cutters</small></label>
          <input type="text" id="tl-len" value="${t && t.cutting_length_in ? t.cutting_length_in : ""}"/>
        </div>
        <div class="tl-field">
          <label>Usual pocket <small>where it belongs — a drag never changes this</small></label>
          <select id="tl-slot">${slotOptions}</select>
        </div>
      </div>
      <div class="tl-row2">
        <div class="tl-field"><label>Vendor <small>for reordering; the app never reads it</small></label>
          <input type="text" id="tl-vendor" value="${esc(t ? t.vendor : "")}"/></div>
        <div class="tl-field"><label>Link</label>
          <input type="text" id="tl-link" value="${esc(t ? t.product_link : "")}"/></div>
      </div>
      ${isNew ? "" : `
      <div class="tl-field">
        <label>Names this code has posted <small>added only when you confirm a rename</small></label>
        <div class="tl-descs">${(t.cam_descriptions || []).map(d => `<code>${esc(d)}</code>`).join("") ||
          '<span style="color:#666">none seen yet</span>'}</div>
      </div>
      ${t.in_use_by.length ? `<div class="tl-inuse">Used by ${
        t.in_use_by.map(u => `${esc(u.filename)} (${esc(u.slot)})`).join(", ")}</div>` : ""}`}
      <div class="tl-actions">
        ${isNew ? "" : `<button id="tl-delete">Delete</button>
                        <button id="tl-merge">Merge into…</button>`}
        <div style="flex:1"></div>
        <button id="tl-save" class="primary">${isNew ? "Add tool" : "Save"}</button>
      </div>
      <div class="tl-error" id="tl-error"></div>`;

    document.getElementById("tl-class").addEventListener("change", e => {
      if (e.target.value !== "__new") return;
      const name = prompt("Name the new shape (e.g. Chamfer Mill):");
      const draft = _readForm();
      draft.geometry_class = (name || "").trim() || (library.geometry_classes || [])[0] || "";
      if (draft.geometry_class) library.geometry_classes.push(draft.geometry_class);
      _renderForm({ ...(get(editing) || {}), ...draft, cam_descriptions: (get(editing) || {}).cam_descriptions || [], in_use_by: (get(editing) || {}).in_use_by || [] });
    });
    document.getElementById("tl-save").addEventListener("click", save);
    const del = document.getElementById("tl-delete");
    if (del) del.addEventListener("click", remove);
    const merge = document.getElementById("tl-merge");
    if (merge) merge.addEventListener("click", mergeInto);
  }

  function _readForm() {
    const num = id => {
      const v = document.getElementById(id).value.trim();
      return v === "" ? null : parseFloat(v);
    };
    return {
      code: document.getElementById("tl-code").value.trim(),
      name: document.getElementById("tl-name").value.trim(),
      diameter_inches: num("tl-dia"),
      geometry_class: document.getElementById("tl-class").value,
      flute_direction: document.getElementById("tl-flute").value,
      cutting_length_in: num("tl-len"),
      default_slot: document.getElementById("tl-slot").value || null,
      vendor: document.getElementById("tl-vendor").value.trim(),
      product_link: document.getElementById("tl-link").value.trim(),
    };
  }

  function _error(msg) { document.getElementById("tl-error").textContent = msg || ""; }

  async function save() {
    const body = _readForm();
    if (get(editing)) body.original_code = editing;
    const r = await fetch("/api/tool-library", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!data.ok) { _error(data.error || "Could not save that tool"); return; }
    library = data.library;
    editing = data.tool.code;
    _afterLibraryChange(data);
    render();
  }

  async function remove() {
    const t = get(editing);
    if (!t || !confirm(`Delete ${t.code} — ${t.name}?`)) return;
    const r = await fetch(`/api/tool-library/${encodeURIComponent(t.code)}`, { method: "DELETE" });
    const data = await r.json();
    if (!data.ok) { _error(data.message || data.error || "Could not delete that tool"); return; }
    library = data.library;
    editing = (tools()[0] || {}).code || "";
    _afterLibraryChange(data);
    render();
  }

  async function mergeInto() {
    const loser = get(editing);
    const others = tools().filter(t => t.code !== loser.code);
    if (!others.length) { _error("There is nothing to merge into."); return; }
    const survivor = prompt(
      `Fold ${loser.code} into which tool? The names ${loser.code} has posted move across, ` +
      `and ${loser.code} itself is deleted.\n\n` + others.map(t => `${t.code} — ${t.name}`).join("\n"));
    if (!survivor) return;
    const r = await fetch("/api/tool-library/merge", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ survivor: survivor.trim(), loser: loser.code }),
    });
    const data = await r.json();
    if (!data.ok) { _error(data.error || "Could not merge those tools"); return; }
    library = data.library;
    editing = data.tool.code;
    _afterLibraryChange(data);
    render();
  }

  /**
   * A library edit can invalidate a placement retroactively — the only path in the app
   * where that happens. Newly-colliding placements are deleted rather than flagged,
   * because the edit exists *because* the old data was wrong (§3.5.4). Say so plainly.
   */
  function _afterLibraryChange(data) {
    if (data.changer) App.changer = data.changer;
    const removed = data.removed_placements || [];
    if (removed.length) {
      App.setMessage(
        `Took ${removed.map(r => `${r.filename} (${r.slot})`).join(", ")} off the bed — ` +
        `with the corrected tool they no longer fit.`, true);
    }
    if (window.Placement) Placement.refresh();
  }

  // ── the description seal (§3.5.3) ─────────────────────────────────────────

  /**
   * Two-way and it must show **both** strings, because the operator's ability to read
   * them is the whole value of the check. It blocks rather than warns: it is the only
   * cross-file detector of a wrong-tool cut, and a warning on a crash-class check is
   * one that gets clicked through.
   */
  function openSeal(prompts, done) {
    sealQueue = [...prompts];
    onSealDone = done || null;
    _nextSeal();
  }

  function _nextSeal() {
    const overlay = document.getElementById("seal-overlay");
    if (!sealQueue.length) {
      overlay.classList.remove("open");
      const done = onSealDone; onSealDone = null;
      if (done) done();
      return;
    }
    const s = sealQueue[0];
    document.getElementById("seal-code").textContent = s.library_code;
    document.getElementById("seal-name").textContent = s.name ? ` — ${s.name}` : "";
    document.getElementById("seal-known").innerHTML =
      s.known.map(k => `<code>${esc(k)}</code>`).join("<br>");
    document.getElementById("seal-posted").innerHTML = `<code>${esc(s.posted)}</code>`;
    document.getElementById("seal-file").textContent = s.filename || "";
    document.getElementById("seal-error").textContent = "";
    overlay.classList.add("open");
  }

  async function _answerSeal(answer) {
    const s = sealQueue[0];
    const r = await fetch("/api/tool-library/seal", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ library_code: s.library_code, posted: s.posted, answer }),
    });
    const data = await r.json();
    if (!data.ok) {
      // "Different tool" is not an error to dismiss — it is an instruction to go and
      // fix CAM, and nothing here can unblock it.
      document.getElementById("seal-error").textContent = data.message || data.error;
      return;
    }
    library = data.library;
    if (data.changer) App.changer = data.changer;
    sealQueue.shift();
    _nextSeal();
  }

  function init() {
    document.getElementById("btn-tools").addEventListener("click", () => open());
    document.getElementById("toollib-close").addEventListener("click", close);
    document.getElementById("toollib-new").addEventListener("click", () => {
      editing = "";
      render();
    });
    document.getElementById("seal-rename").addEventListener("click", () => _answerSeal("rename"));
    document.getElementById("seal-different").addEventListener("click", () => _answerSeal("different"));
    document.getElementById("seal-later").addEventListener("click", () => {
      document.getElementById("seal-overlay").classList.remove("open");
      sealQueue = []; onSealDone = null;
    });
  }

  return { init, open, close, load, openSeal, tools, get };
})();
