/**
 * job.js — Generate G-code
 */

/**
 * The "Ends holding" selector in the changer dock's header: which pocket the program
 * loads last, so the next job starts with that tool already up. It is a per-job pick,
 * like the job name, and never written to config — the config pocket is only what
 * Auto falls back to. The value is a **pocket**, not a cutter (see
 * `gcode_generator.park_tool_word`); it sits in the dock because the pockets below it
 * are what the labels refer to.
 */
window.JobEnd = (() => {
  const SOURCE_TEXT = {
    only_tool: "the job's only tool",
    default: "shop default",
    none: "no change",
  };

  function _occupant(pocket) {
    const st = App.changer || {};
    const slot = (st.pockets || []).find(p => p.number === pocket);
    const names = (slot ? slot.codes : [])
      .map(c => (st.tools || []).find(t => t.code === c))
      .filter(Boolean)
      .map(t => t.name);
    return names.length ? names.join(" + ") : "nothing from this job";
  }

  function render() {
    const sel = document.getElementById("end-pocket-select");
    if (!sel) return;
    const ep = App.endPocket || {};
    const capacity = (App.changer && App.changer.capacity) || 8;
    const autoText = ep.auto_pocket
      ? `Auto · pocket ${ep.auto_pocket} (${SOURCE_TEXT[ep.auto_source] || ""})`
      : "Auto · no change";
    const opts = [
      { value: "", text: autoText },
      { value: "0", text: "No change (leave the last tool up)" },
    ];
    for (let n = 1; n <= capacity; n++) {
      opts.push({ value: String(n), text: `Pocket ${n} · ${_occupant(n)}` });
    }
    sel.innerHTML = "";
    for (const o of opts) {
      const el = document.createElement("option");
      el.value = o.value;
      el.textContent = o.text;
      sel.appendChild(el);
    }
    sel.value = ep.choice == null ? "" : String(ep.choice);
  }

  async function _onChange(e) {
    const v = e.target.value;
    const r = await fetch("/api/end-pocket", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pocket: v === "" ? null : Number(v) }),
    });
    const data = await r.json();
    if (!data.ok) App.setMessage(data.error || "Could not set the end-of-job tool", true);
    // One refresh re-reads the panel's change count, runtime and the dock badge off
    // the same stats call the server answered with.
    if (window.Placement) Placement.refresh();
  }

  function init() {
    const sel = document.getElementById("end-pocket-select");
    if (sel) sel.addEventListener("change", _onChange);
    render();
  }

  return { init, render };
})();

document.addEventListener("DOMContentLoaded", () => {
  JobEnd.init();
  document.getElementById("btn-generate").addEventListener("click", async () => {
    const jobName = document.getElementById("job-name-input").value.trim() || undefined;
    const btn = document.getElementById("btn-generate");
    btn.disabled = true;
    btn.textContent = "Generating…";
    try {
      const r = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(jobName ? { job_name: jobName } : {}),
      });
      const data = await r.json();
      if (data.ok) {
        App.setMessage(`Saved: ${data.nc_path} (+ layout PDF)`, false);
      } else {
        // Prefer `message`: `error` is a slug like "validation_failed" and the
        // people running this are operators, not developers.
        App.setMessage(data.message || data.error || "Generation failed", true);
        if (data.findings) console.error("Validation findings:", data.findings);
      }
    } catch (e) {
      App.setMessage("Generation failed: " + e.message, true);
    } finally {
      btn.textContent = "Generate G-code";
      // Re-enable off the §3.4 validity gate, the same signal `_updateTopButtons` uses.
      btn.disabled = !App.placements.length || !(App.changer && App.changer.valid);
    }
  });
});
