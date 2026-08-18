/**
 * changer.js — the tool changer dock (issue #11, spec §3.4.1).
 *
 * Eight pockets left to right, matching how the machine is read, in a full-width
 * collapsible dock above the status bar. It replaces the right sidebar's Tool
 * Compatibility panel — that panel existed to show the symptom this feature removes,
 * and it is where the screen budget comes from.
 *
 * Three behaviours are load-bearing and easy to "fix" into something worse:
 *
 *   - **Dropping onto an occupied pocket makes the two coexist.** It does not swap and
 *     it is never refused. Swapping two tools is impossible without transiting a
 *     doubled pocket, so refusing the drop would deadlock the swap (§3.2.1).
 *   - **The dock cannot be collapsed while the map is invalid**, so a disabled Generate
 *     always has a visible reason on screen.
 *   - **A drag is job-scoped.** It never writes back to the library, so the declared
 *     pocket is re-proposed on the next job. That nag is deliberate.
 */

var Changer = (() => {
  let collapsed = false;
  let dragCode = null;

  const state = () => App.changer || {};
  const tools = () => state().tools || [];
  const byCode = code => tools().find(t => t.code === code);

  // ── rendering ─────────────────────────────────────────────────────────────

  function render() {
    const st = state();
    const dock = document.getElementById("dock");
    if (!dock) return;
    if (!App.placements.length) { dock.style.display = "none"; return; }
    dock.style.display = "";

    _renderVerdict(st);
    _renderPockets(st);
    _renderStaging(st);

    // Force-expanded while invalid: a blocked Generate must never be able to hide its
    // own explanation behind a collapsed panel.
    const locked = !st.valid;
    if (locked) collapsed = false;
    dock.classList.toggle("collapsed", collapsed);
    const head = document.getElementById("dock-head");
    head.classList.toggle("locked", locked);
    head.title = locked ? "Cannot be collapsed while Generate is blocked" : "Collapse";
    document.getElementById("dock-chev").textContent = collapsed ? "▲" : "▼";
  }

  function _renderVerdict(st) {
    const el = document.getElementById("dock-verdict");
    const sym = { error: "✕", warning: "◇", ok: "✓" };
    el.innerHTML = (st.messages || [])
      .map(m => `<span class="v-${m.level}"><span class="sym">${sym[m.level] || ""}</span> ${_esc(m.text)}</span>`)
      .join('<span style="color:#555">·</span>');
  }

  function _renderPockets(st) {
    const row = document.getElementById("dock-pockets");
    row.innerHTML = "";
    row.style.gridTemplateColumns = `repeat(${st.capacity || 8}, minmax(0,1fr))`;

    for (const pocket of st.pockets || []) {
      const here = pocket.codes.map(byCode).filter(Boolean);
      const cell = document.createElement("div");
      cell.className = "pocket" + (here.length > 1 ? " dbl" : "");
      cell.innerHTML =
        `<div class="pocket-no"><span>POCKET ${pocket.number}</span>` +
        (here.length > 1 ? '<span class="free" style="color:var(--bad)">2 tools</span>' : "") +
        `</div><div class="pocket-cards"></div>`;
      const cards = cell.querySelector(".pocket-cards");
      if (!here.length) {
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "empty";
        cards.appendChild(empty);
      } else {
        here.forEach(t => cards.appendChild(_card(t)));
      }
      _dropTarget(cell, pocket.number);
      row.appendChild(cell);
    }
  }

  function _renderStaging(st) {
    const wrap = document.getElementById("dock-staging");
    wrap.innerHTML = "";
    const staged = (st.staged || []).map(byCode).filter(Boolean);
    const unresolved = st.unresolved || [];

    if (staged.length || unresolved.length) {
      const box = document.createElement("div");
      box.className = "staging";
      box.innerHTML = '<div class="st-lbl">Needs a home</div><div class="st-cards"></div>';
      const cards = box.querySelector(".st-cards");
      staged.forEach(t => cards.appendChild(_card(t)));
      // An unresolved tool is NOT a staged tool: it cannot take a pocket at all,
      // because nothing knows what it is. Different treatment, different message.
      _dedupeUnresolved(unresolved).forEach(u => cards.appendChild(_orphanCard(u)));

      const hint = document.createElement("div");
      hint.className = "st-hint";
      if (unresolved.length && !staged.length) {
        hint.textContent = "Add it to your tool library, or say which tool it is.";
      } else if (!(st.free || []).length) {
        hint.textContent = "No free pockets. " + (st.removal_advice || "");
      } else {
        const free = st.free;
        hint.textContent = `${free.length} pocket${free.length > 1 ? "s" : ""} free: ${free.join(", ")}.`;
      }
      box.appendChild(hint);
      _dropTarget(box, 0);
      wrap.appendChild(box);
    }

    // When no pocket is free there may be nothing staged, so the one actionable
    // sentence needs its own line — otherwise it has nowhere to live.
    if (st.needs_removal && !staged.length && !unresolved.length) {
      const bar = document.createElement("div");
      bar.className = "dock-advice";
      bar.innerHTML = `<span class="sym">✕</span> All ${st.capacity} pockets are taken. ${_esc(st.removal_advice || "")}`;
      wrap.appendChild(bar);
    }

    for (const dup of st.duplicate_codes || []) {
      const bar = document.createElement("div");
      bar.className = "dock-advice";
      bar.innerHTML =
        `<span class="sym">✕</span> ${_esc(dup.filename)} calls ` +
        `${dup.tool_numbers.join(" and ")} by the same tool code ` +
        `(<b>${_esc(dup.library_code)}</b>). Fix it in Fusion or VCarve — these are ` +
        `different cutters and the nest tool will not merge them.`;
      wrap.appendChild(bar);
    }

    for (const seal of st.seal_prompts || []) {
      const bar = document.createElement("div");
      bar.className = "dock-advice";
      bar.innerHTML =
        `<span class="sym">✕</span> <b>${_esc(seal.library_code)}</b> posts a description ` +
        `it has never posted before, in ${_esc(seal.filename)}. `;
      const btn = document.createElement("button");
      btn.textContent = "Sort it out…";
      btn.style.marginLeft = "4px";
      btn.addEventListener("click", () => ToolLib.openSeal([seal]));
      bar.appendChild(btn);
      wrap.appendChild(bar);
    }
  }

  /** One card per distinct unresolved tool, however many parts need it. */
  function _dedupeUnresolved(unresolved) {
    const out = [];
    for (const u of unresolved) {
      const key = `${u.code || ""}|${u.cam_description || u.description}`;
      const seen = out.find(o => o.key === key);
      if (seen) { seen.usages.push(u); continue; }
      out.push({ key, ...u, usages: [u] });
    }
    return out;
  }

  function _card(tool) {
    const el = document.createElement("div");
    el.className = "tool-card";
    el.draggable = true;
    el.tabIndex = 0;
    el.dataset.code = tool.code;
    el.style.borderLeftColor = _codeColor(tool.code);
    el.innerHTML =
      `<div class="nm" title="${_esc(tool.name)}">${_esc(tool.name)}</div>
       <div class="geo">${_esc(tool.display)}</div>
       <div class="row">
         ${_remapChip(tool)}
         <span class="chip parts">${tool.part_count} part${tool.part_count > 1 ? "s" : ""}</span>
         ${tool.off_home
           ? `<span class="chip home" title="Its declared pocket is ${tool.default_slot}">home ${tool.default_slot}</span>`
           : ""}
         ${tool.default_slot ? "" : '<span class="chip home" title="The library declares no pocket for this tool">no default</span>'}
       </div>`;

    el.addEventListener("dragstart", e => {
      dragCode = tool.code;
      e.dataTransfer.setData("text/plain", tool.code);
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("dragging");
      _hidePop();
    });
    el.addEventListener("dragend", () => { dragCode = null; el.classList.remove("dragging"); });
    el.addEventListener("mouseenter", () => _showPop(el, tool));
    el.addEventListener("focus", () => _showPop(el, tool));
    el.addEventListener("mouseleave", _hidePop);
    el.addEventListener("blur", _hidePop);
    el.addEventListener("keydown", e => {
      const max = state().capacity || 8;
      if (!/^\d$/.test(e.key)) return;
      const n = Number(e.key);
      if (n > max) return;
      e.preventDefault();
      assign(tool.code, n);
    });
    return el;
  }

  /**
   * The `T` numbers the files call this tool by, and the one it will be cut as.
   * Only a *change* earns the blue chip — an unchanged number is just a fact.
   */
  function _remapChip(tool) {
    const src = tool.tool_numbers;
    const dest = tool.pocket ? "T" + tool.pocket : null;
    if (dest && src.length === 1 && src[0] === dest) return `<span class="chip">${dest}</span>`;
    return `<span class="chip remap">${src.join(",")} → ${dest || "?"}</span>`;
  }

  function _orphanCard(u) {
    const el = document.createElement("div");
    el.className = "tool-card orphan";
    el.tabIndex = 0;
    const parts = new Set(u.usages.map(x => x.filename)).size;
    const ts = [...new Set(u.usages.map(x => x.tool_number))].sort().join(",");
    const headline = u.status === "unknown_code"
      ? `Code ${_esc(u.code)} is not in your library`
      : "Not in your library";
    el.innerHTML =
      `<div class="nm">${headline}</div>
       <div class="geo">${_esc(u.cam_description || u.description || "no description")}</div>
       <div class="row">
         <span class="chip">${ts}</span>
         <span class="chip parts">${parts} part${parts > 1 ? "s" : ""}</span>
         <span class="chip home">identify…</span>
       </div>`;
    el.addEventListener("click", () => Placement.openResolver(u.usages));
    el.addEventListener("mouseenter", () => _showPop(el, null, u));
    el.addEventListener("focus", () => _showPop(el, null, u));
    el.addEventListener("mouseleave", _hidePop);
    el.addEventListener("blur", _hidePop);
    return el;
  }

  function _dropTarget(el, pocket) {
    el.addEventListener("dragover", e => {
      if (!dragCode) return;
      e.preventDefault();
      el.classList.add("over");
    });
    el.addEventListener("dragleave", () => el.classList.remove("over"));
    el.addEventListener("drop", e => {
      el.classList.remove("over");
      const code = e.dataTransfer.getData("text/plain") || dragCode;
      if (!code) return;
      e.preventDefault();
      assign(code, pocket);   // coexist, never swap, never refuse
    });
  }

  // ── the one mutation ──────────────────────────────────────────────────────

  async function assign(code, pocket) {
    const r = await fetch("/api/changer/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, pocket }),
    });
    const data = await r.json();
    if (!data.ok) { App.setMessage(data.error || "Could not move that tool", true); return; }
    App.changer = data.changer;
    App.onPlacementsChanged();
    const again = document.querySelector(`.tool-card[data-code="${code}"]`);
    if (again) again.focus();
  }

  async function reset() {
    const data = await fetch("/api/changer/reset", { method: "POST" }).then(r => r.json());
    App.changer = data.changer;
    App.onPlacementsChanged();
  }

  // ── hover detail ──────────────────────────────────────────────────────────

  function _showPop(anchor, tool, orphan) {
    const pop = document.getElementById("dock-pop");
    const box = document.body.getBoundingClientRect();
    const r = anchor.getBoundingClientRect();

    if (tool) {
      const rows = tool.usages.map(u =>
        `<tr><td>${_esc(u.filename)} <span style="color:#666">${_esc(u.slot)}</span></td>` +
        `<td class="t">${_esc(u.tool_number)}</td></tr>`).join("");
      const extras = [tool.geometry_class, tool.flute_display,
                      tool.cutting_length_in ? `${tool.cutting_length_in}" cutting length` : null,
                      tool.vendor || null].filter(Boolean).join(" · ");
      const sole = tool.sole_instance_id
        ? `Only <b style="color:#ddd;font-weight:500">${_esc(_nameOf(tool.sole_instance_id))}</b> ` +
          `needs this. Take it off the bed and pocket ${tool.pocket || "—"} frees up.`
        : "";
      pop.innerHTML =
        `<div class="p-nm"><span>${_esc(tool.name)}</span><span class="p-code">${_esc(tool.code)}</span></div>
         <div class="p-geo">${_esc(extras)} · ${tool.diameter_inches}"</div>
         <div class="p-slot">${
            tool.default_slot
              ? (tool.off_home
                  ? `Belongs in pocket ${tool.default_slot} — <span class="off">now in ${tool.pocket}</span>`
                  : `Pocket ${tool.pocket || "—"}, its declared home`)
              : "No declared pocket in the library"}</div>
         <div class="p-h">Needed by</div><table>${rows}</table>
         ${sole ? `<div class="p-sole">${sole}</div>` : ""}`;
    } else {
      const rows = orphan.usages.map(u =>
        `<tr><td>${_esc(u.filename)} <span style="color:#666">${_esc(u.slot)}</span></td>` +
        `<td class="t">${_esc(u.tool_number)}</td></tr>`).join("");
      pop.innerHTML =
        `<div class="p-nm"><span style="color:var(--warn)">Not in your library</span></div>
         <div class="p-geo">Posted as <code>${_esc(orphan.cam_description || orphan.description)}</code>${
            orphan.posted_diameter_inches ? ` · file says ${orphan.posted_diameter_inches.toFixed(3)}"` : ""}</div>
         <div class="p-slot">No identity, so no pocket and no declared diameter for the table-edge check.</div>
         <div class="p-h">Needed by</div><table>${rows}</table>
         <div class="p-sole">Click the card to add it or bind it.</div>`;
    }

    pop.classList.add("show");
    const pr = pop.getBoundingClientRect();
    let left = r.left + r.width / 2 - pr.width / 2;
    left = Math.max(6, Math.min(left, box.width - pr.width - 6));
    pop.style.left = left + "px";
    pop.style.top = (r.top - pr.height - 8) + "px";
  }

  function _hidePop() {
    const pop = document.getElementById("dock-pop");
    if (pop) pop.classList.remove("show");
  }

  function _nameOf(instanceId) {
    const p = App.placements.find(x => x.instance_id === instanceId);
    return p ? p.filename : instanceId;
  }

  /** Card stripe colour, stable per code rather than per file. */
  function _codeColor(code) {
    const codes = tools().map(t => t.code);
    const palette = (window.BedCanvas && BedCanvas.PALETTE) ||
                    ["#4dabf7", "#69db7c", "#ffd43b", "#f783ac", "#a9e34b", "#74c0fc", "#63e6be", "#ffa94d"];
    return palette[Math.max(0, codes.indexOf(code)) % palette.length];
  }

  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function init() {
    document.getElementById("dock-head").addEventListener("click", e => {
      if (e.target.closest("button")) return;
      if (!state().valid) return;      // locked open while Generate is blocked
      collapsed = !collapsed;
      render();
    });
    document.getElementById("dock-reset").addEventListener("click", e => {
      e.stopPropagation();
      reset();
    });
    window.addEventListener("resize", _hidePop);
  }

  return { init, render, assign, reset };
})();
