/**
 * job.js — Generate G-code, save/load job file
 */

document.addEventListener("DOMContentLoaded", () => {
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
        App.setMessage(`Saved: ${data.nc_path}`, false);
      } else {
        App.setMessage(data.error || "Generation failed", true);
      }
    } catch (e) {
      App.setMessage("Generation failed: " + e.message, true);
    } finally {
      btn.textContent = "Generate G-code";
      // re-enable based on current state
      btn.disabled = !App.placements.length || (App.compatibility && App.compatibility.has_conflict);
    }
  });

  document.getElementById("btn-save").addEventListener("click", async () => {
    const jobName = document.getElementById("job-name-input").value.trim() || undefined;
    const r = await fetch("/api/save-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(jobName ? { job_name: jobName } : {}),
    });
    const data = await r.json();
    if (data.ok) {
      App.setMessage(`Job saved: ${data.path}`, false);
    } else {
      App.setMessage(data.error || "Save failed", true);
    }
  });

  document.getElementById("btn-load").addEventListener("click", async () => {
    const path = prompt("Path to .cnj job file:");
    if (!path) return;
    const r = await fetch("/api/load-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const data = await r.json();
    if (!data.ok) { App.setMessage(data.error || "Load failed", true); return; }

    App.placements    = data.placements || [];
    App.compatibility = data.compatibility || {};
    App.jobSafeZ      = data.job_safe_z || {};
    App.toolSequence  = data.tool_sequence || [];
    App.toolChanges   = data.tool_changes ?? 0;
    App.utilization   = data.utilization ?? 0;
    if (data.job_name) document.getElementById("job-name-input").value = data.job_name;
    if (data.warnings && data.warnings.length) {
      App.setMessage("Loaded with warnings: " + data.warnings.join("; "), false);
    } else {
      App.setMessage("Job loaded", false);
    }
    App.onPlacementsChanged();
  });
});
