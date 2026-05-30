/**
 * config.js — Settings panel
 */

document.addEventListener("DOMContentLoaded", () => {
  const panel = document.getElementById("settings-panel");

  document.getElementById("btn-settings").addEventListener("click", async () => {
    const cfg = await fetch("/api/config").then(r => r.json());
    document.getElementById("cfg-library-path").value = cfg.library_path || "";
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

  document.getElementById("cfg-save").addEventListener("click", async () => {
    const body = {
      library_path: document.getElementById("cfg-library-path").value.trim(),
      output_path:  document.getElementById("cfg-output-path").value.trim(),
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
