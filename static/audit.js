/**
 * audit.js — Library Audit panel.
 *
 * Calls /api/audit, which scans every library file, writes two CSVs to the output
 * folder, and returns per-file rows. Renders them in a sortable table with status
 * badges so best-practice issues surface at a glance. The CSVs on disk are the
 * full report (this table shows the most useful columns).
 */
(function () {
  const SEVERITY = { blocked: 2, warning: 1, ok: 0 };

  // Columns shown in the table: [key, label, numeric?]
  const COLUMNS = [
    ["status", "Status", false],
    ["path", "File", false],
    ["material_thickness_in", "Thick \"", true],
    ["units", "Units", false],
    ["tools", "Tools", false],
    ["cut_feed_max_ipm", "Cut ipm", true],
    ["plunge_feed_max_ipm", "Plunge ipm", true],
    ["spindle_max_rpm", "Spindle", true],
    ["toolpath_feeds", "Feeds by toolpath (ipm)", false],
    ["runtime", "Runtime", false],
    ["flags", "Flags", false],
  ];

  let rows = [];
  let sortKey = "status";
  let sortDir = -1; // default: worst first

  document.addEventListener("DOMContentLoaded", () => {
    const panel = document.getElementById("audit-panel");
    const btn = document.getElementById("btn-audit");
    if (!panel || !btn) return;

    btn.addEventListener("click", () => { panel.classList.add("open"); run(); });
    document.getElementById("audit-close").addEventListener("click", () => panel.classList.remove("open"));
    document.getElementById("audit-refresh").addEventListener("click", run);
    panel.addEventListener("click", e => { if (e.target === panel) panel.classList.remove("open"); });
  });

  async function run() {
    const summaryEl = document.getElementById("audit-summary");
    const pathsEl = document.getElementById("audit-paths");
    const loadingEl = document.getElementById("audit-loading");
    const tableEl = document.getElementById("audit-table");

    summaryEl.textContent = "Scanning…";
    pathsEl.textContent = "";
    tableEl.style.display = "none";
    loadingEl.style.display = "block";
    loadingEl.textContent = "Scanning library…";

    let data;
    try {
      data = await fetch("/api/audit").then(r => r.json());
    } catch (e) {
      loadingEl.textContent = "Audit failed: " + e;
      return;
    }
    if (!data.ok) {
      loadingEl.textContent = data.error || "Audit failed";
      summaryEl.textContent = "";
      return;
    }

    const s = data.summary;
    summaryEl.innerHTML =
      `<b>${s.total}</b> files: ` +
      `<span class="ok">${s.ok} ok</span>, ` +
      `<span class="warning">${s.warning} warning</span>, ` +
      `<span class="blocked">${s.blocked} blocked</span>`;
    const varies = s.feed_varies ? `  •  ${s.feed_varies} with multiple feeds across toolpaths` : "";
    summaryEl.innerHTML += varies;
    const csvs = [data.file_csv, data.tool_csv, data.toolpath_csv].filter(Boolean).join("  •  ");
    pathsEl.textContent = `Saved: ${csvs}`;

    rows = data.files || [];
    loadingEl.style.display = "none";
    tableEl.style.display = "table";
    render();
  }

  function render() {
    const tableEl = document.getElementById("audit-table");
    const sorted = rows.slice().sort(compare);

    const head = "<thead><tr>" + COLUMNS.map(
      ([key, label]) => `<th data-key="${key}">${label}${sortKey === key ? (sortDir < 0 ? " ▾" : " ▴") : ""}</th>`
    ).join("") + "</tr></thead>";

    const body = "<tbody>" + sorted.map(r => "<tr>" + COLUMNS.map(([key]) => {
      if (key === "status") {
        const st = r.status || "ok";
        return `<td><span class="badge ${st}">${st}</span></td>`;
      }
      const val = r[key] == null || r[key] === "" ? "—" : r[key];
      let cls = key === "flags" ? ' class="flags"' : "";
      // Flag the per-toolpath feed cell when the part mixes cutting feeds.
      if (key === "toolpath_feeds" && r.feed_varies) cls = ' class="flags feed-varies"';
      return `<td${cls}>${escapeHtml(String(val))}</td>`;
    }).join("") + "</tr>").join("") + "</tbody>";

    tableEl.innerHTML = head + body;

    tableEl.querySelectorAll("th").forEach(th => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (sortKey === key) sortDir = -sortDir;
        else { sortKey = key; sortDir = (key === "status") ? -1 : 1; }
        render();
      });
    });
  }

  function compare(a, b) {
    let av = a[sortKey], bv = b[sortKey];
    if (sortKey === "status") { av = SEVERITY[a.status] ?? 0; bv = SEVERITY[b.status] ?? 0; }
    else {
      const an = parseFloat(av), bn = parseFloat(bv);
      if (!isNaN(an) && !isNaN(bn)) { av = an; bv = bn; }
      else { av = (av ?? "").toString().toLowerCase(); bv = (bv ?? "").toString().toLowerCase(); }
    }
    if (av < bv) return -sortDir;
    if (av > bv) return sortDir;
    // Tie-break by path so the order is stable.
    return (a.path || "").localeCompare(b.path || "");
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }
})();
