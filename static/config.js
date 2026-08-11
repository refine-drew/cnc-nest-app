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
    document.getElementById("cfg-rail-width").value   = cfg.advanced?.rail_width_mm ? mmToIn(cfg.advanced.rail_width_mm) : "";
    document.getElementById("cfg-safe-z").value       = cfg.advanced?.safe_z_clearance_mm ? mmToIn(cfg.advanced.safe_z_clearance_mm) : "";
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

  document.getElementById("cfg-save").addEventListener("click", async () => {
    const body = {
      // One path per line; each machine uses whichever exists locally.
      library_path: document.getElementById("cfg-library-path").value
        .split("\n").map(stripQuotes).filter(Boolean),
      output_path:  stripQuotes(document.getElementById("cfg-output-path").value),
      advanced: {
        rail_width_mm:       parseFloat(document.getElementById("cfg-rail-width").value)
          ? parseFloat(document.getElementById("cfg-rail-width").value) * 25.4 : undefined,
        safe_z_clearance_mm: parseFloat(document.getElementById("cfg-safe-z").value)
          ? parseFloat(document.getElementById("cfg-safe-z").value) * 25.4 : undefined,
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
    }
  });
});
