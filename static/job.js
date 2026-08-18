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
        const warned = (data.warnings || []).length;
        App.setMessage(
          `Saved: ${data.nc_path} (+ layout PDF)` +
            (warned ? ` — ${warned} check(s) flagged for review, see ${data.job_name}_validation.txt` : ""),
          false,
        );
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
