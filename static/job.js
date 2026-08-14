/**
 * job.js — Generate G-code
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
        App.setMessage(`Saved: ${data.nc_path} (+ layout PDF)`, false);
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
});
