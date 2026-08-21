/**
 * config.js — Settings panel
 */

document.addEventListener("DOMContentLoaded", () => {
  const panel = document.getElementById("settings-panel");

  document.getElementById("btn-settings").addEventListener("click", async () => {
    const cfg = await fetch("/api/config").then(r => r.json());
    // library_path may be a single string (older config) or a list of candidates.
    const libs = Array.isArray(cfg.library_path) ? cfg.library_path : [cfg.library_path || ""];
    document.getElementById("cfg-library-path").value = libs.filter(Boolean).join("\n");
    document.getElementById("cfg-output-path").value  = cfg.output_path  || "";
    const mmToIn = mm => Math.round(mm / 25.4 * 100) / 100;
    document.getElementById("cfg-safe-z").value       = cfg.advanced?.safe_z_clearance_mm ? mmToIn(cfg.advanced.safe_z_clearance_mm) : "";
    // Per-rail geometry, in raw mm — these are measured off the machine, so no
    // unit conversion, and each rail is edited independently of the other.
    const rails = cfg.advanced?.rails || {};
    document.getElementById("cfg-rail-a-x").value = rails.A?.x_mm ?? "";
    document.getElementById("cfg-rail-a-y").value = rails.A?.slot0_y_mm ?? "";
    document.getElementById("cfg-rail-b-x").value = rails.B?.x_mm ?? "";
    document.getElementById("cfg-rail-b-y").value = rails.B?.slot0_y_mm ?? "";
    document.getElementById("cfg-bed-x").value = cfg.advanced?.bed_x_mm ?? "";
    document.getElementById("cfg-bed-y").value = cfg.advanced?.bed_y_mm ?? "";
    // The park is a G53 move, so these are raw machine mm like the rail datums —
    // the fence/work origin does not shift them.
    document.getElementById("cfg-park-x").value = cfg.advanced?.park_x ?? "";
    document.getElementById("cfg-park-y").value = cfg.advanced?.park_y ?? "";
    // `!== false` rather than `?? true`: a config predating this key must read as the
    // shipped posture (auto tool on), not as off.
    document.getElementById("cfg-auto-tool").checked = cfg.advanced?.auto_tool_touch_off !== false;
    document.getElementById("cfg-rapid-ipm").value = cfg.advanced?.rapid_ipm ?? "";
    document.getElementById("cfg-accel").value     = cfg.advanced?.accel_mm_s2 ?? "";
    document.getElementById("cfg-jd").value        = cfg.advanced?.junction_deviation_mm ?? "";
    panel.classList.add("open");
  });

  document.getElementById("cfg-cancel").addEventListener("click", () => {
    panel.classList.remove("open");
  });

  panel.addEventListener("click", e => {
    if (e.target === panel) panel.classList.remove("open");
  });

  // Pasting a shell-quoted path keeps the quotes, which breaks resolution.
  const stripQuotes = s => {
    s = (s || "").trim();
    while (s.length >= 2 && s[0] === s[s.length - 1] && (s[0] === "'" || s[0] === '"')) {
      s = s.slice(1, -1).trim();
    }
    return s;
  };

  // Blank field -> undefined (leave unchanged) rather than 0.
  const num = id => {
    const v = (document.getElementById(id).value || "").trim();
    if (!v) return undefined;
    const f = parseFloat(v);
    return Number.isFinite(f) ? f : undefined;
  };
  const pruned = obj => {
    const out = {};
    for (const [k, v] of Object.entries(obj)) {
      if (v === undefined) continue;
      if (v && typeof v === "object" && !Object.keys(v).length) continue;
      out[k] = v;
    }
    return Object.keys(out).length ? out : undefined;
  };

  document.getElementById("cfg-save").addEventListener("click", async () => {
    const body = {
      // One path per line; each machine uses whichever exists locally.
      library_path: document.getElementById("cfg-library-path").value
        .split("\n").map(stripQuotes).filter(Boolean),
      output_path:  stripQuotes(document.getElementById("cfg-output-path").value),
      advanced: {
        safe_z_clearance_mm: parseFloat(document.getElementById("cfg-safe-z").value)
          ? parseFloat(document.getElementById("cfg-safe-z").value) * 25.4 : undefined,
        bed_x_mm: num("cfg-bed-x"),
        bed_y_mm: num("cfg-bed-y"),
        park_x: num("cfg-park-x"),
        park_y: num("cfg-park-y"),
        // A checkbox always has an opinion. `num()` returns undefined for blank so the
        // key is omitted and the backend leaves it alone — right for a number field,
        // wrong for a boolean, where `false` is a value the operator chose.
        auto_tool_touch_off: document.getElementById("cfg-auto-tool").checked,
        rapid_ipm: num("cfg-rapid-ipm"),
        accel_mm_s2: num("cfg-accel"),
        junction_deviation_mm: num("cfg-jd"),
        // Only send rail keys the user actually filled in; the backend merges
        // them over the current values so directions are preserved.
        rails: pruned({
          A: pruned({ x_mm: num("cfg-rail-a-x"), slot0_y_mm: num("cfg-rail-a-y") }),
          B: pruned({ x_mm: num("cfg-rail-b-x"), slot0_y_mm: num("cfg-rail-b-y") }),
        }),
      },
    };
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) {
      panel.classList.remove("open");
      App.setMessage("Settings saved", false);
      // Reload library if path changed
      if (window.LibraryPanel) LibraryPanel.load();
      // The motion model and the touch-off posture both move every displayed runtime,
      // and the server has already re-priced the loaded parts. Without this the panel
      // keeps showing the old estimate until the next drag.
      if (window.Placement) Placement.refresh();
    } else {
      // The park fields are validated server-side; without this the panel closed
      // silently on a rejected value and the old park stayed in force unseen.
      const err = await r.json().catch(() => ({}));
      App.setMessage(err.error || "Settings could not be saved", true);
    }
  });
});
