/**
 * sidebar.js — Library browser and parts tray
 */

var LibraryPanel = (() => {
  let allEntries = []; // flat list of file entries for search

  function init() {
    document.getElementById("lib-search-input").addEventListener("input", e => {
      _filterTree(e.target.value.trim().toLowerCase());
    });
    load();
  }

  function load() {
    fetch("/api/library").then(r => r.json()).then(data => {
      allEntries = [];
      const tree = document.getElementById("lib-tree");
      tree.innerHTML = "";
      if (!data.exists) {
        tree.innerHTML = `<div style="padding:12px 10px;color:#666;font-size:12px">Library folder not found:<br><code style="color:#888">${data.library_path}</code><br><br>Set it in Settings ⚙</div>`;
        return;
      }
      _renderEntries(tree, data.entries, "");
    }).catch(() => {
      document.getElementById("lib-tree").innerHTML =
        '<div style="padding:12px 10px;color:#666">Could not load library.</div>';
    });
  }

  function _renderEntries(parent, entries, indent) {
    for (const e of entries) {
      if (e.type === "folder") {
        const div = document.createElement("div");
        div.className = "lib-folder";
        div.style.paddingLeft = (6 + indent * 10) + "px";
        div.innerHTML = `<span class="arrow">▸</span><span>${e.name}</span>`;
        const children = document.createElement("div");
        children.className = "lib-folder-children collapsed";
        div.addEventListener("click", ev => {
          ev.stopPropagation();
          const collapsed = children.classList.toggle("collapsed");
          div.querySelector(".arrow").textContent = collapsed ? "▸" : "▾";
        });
        parent.appendChild(div);
        _renderEntries(children, e.children || [], indent + 1);
        parent.appendChild(children);
      } else {
        const el = _makeFileEl(e, indent);
        allEntries.push({ el, name: e.name.toLowerCase(), path: e.path });
        parent.appendChild(el);
      }
    }
  }

  function _makeFileEl(e, indent) {
    const div = document.createElement("div");
    div.className = "lib-file";
    div.style.paddingLeft = (22 + indent * 10) + "px";
    div.draggable = true;
    div.dataset.path = e.path;

    const zIcon = e.z_status === "ok" ? "✓" : e.z_status === "warning" ? "⚠" : "✕";
    const zClass = "z-" + (e.z_status || "ok");
    const dims = e.vcarve_x_span ? `${e.vcarve_x_span}×${e.vcarve_y_span}mm · ${e.material_thickness || "?"}mm` : "";
    const runtime = e.runtime_seconds ? `~${formatDuration(e.runtime_seconds)}` : "";
    const tooltip = [e.path, dims].filter(Boolean).join(" — ");

    div.innerHTML = `
      <span class="${zClass}" title="${(e.z_messages||[]).join('; ')}">${zIcon}</span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${tooltip}">${e.name}</span>
      ${runtime ? `<span style="color:#666;font-size:10px;margin-left:6px">${runtime}</span>` : ""}
    `;

    div.addEventListener("click", () => TrayPanel.add(e));
    div.addEventListener("dragstart", ev => {
      ev.dataTransfer.setData("text/plain", e.path);
      window._cncDragPart = { ...e, filename: e.name };
      div.classList.add("dragging");
    });
    div.addEventListener("dragend", () => {
      div.classList.remove("dragging");
      window._cncDragPart = null;
    });
    return div;
  }

  function _filterTree(q) {
    if (!q) {
      allEntries.forEach(item => item.el.style.display = "");
      document.querySelectorAll(".lib-folder").forEach(f => f.style.display = "");
      return;
    }
    allEntries.forEach(item => {
      item.el.style.display = item.name.includes(q) ? "" : "none";
    });
    // Expand folders that have matching children
    document.querySelectorAll(".lib-folder").forEach(f => {
      const children = f.nextElementSibling;
      if (children && children.classList.contains("lib-folder-children")) {
        const visible = [...children.querySelectorAll(".lib-file")].some(
          el => el.style.display !== "none"
        );
        f.style.display = visible ? "" : "none";
        if (visible) children.classList.remove("collapsed");
      }
    });
  }

  return { init, load };
})();


var TrayPanel = (() => {
  // Map filename → part info (only one entry per unique file)
  const trayParts = new Map(); // path → entry

  function add(entry) {
    if (trayParts.has(entry.path)) return; // already in tray
    trayParts.set(entry.path, entry);
    _render();
  }

  function remove(path) {
    trayParts.delete(path);
    _render();
    // Also remove all placed instances of this file
    const toRemove = App.placements.filter(p => p.path === path).map(p => p.instance_id);
    Promise.all(toRemove.map(iid =>
      fetch(`/api/place/${iid}`, { method: "DELETE" }).then(r => r.json())
    )).then(() => {
      App.placements = App.placements.filter(p => p.path !== path);
      App.onPlacementsChanged();
    });
  }

  function _render() {
    const list = document.getElementById("tray-list");
    const empty = document.getElementById("tray-empty");
    list.querySelectorAll(".tray-item").forEach(el => el.remove());

    if (trayParts.size === 0) {
      if (empty) empty.style.display = "";
      return;
    }
    if (empty) empty.style.display = "none";

    for (const [path, e] of trayParts) {
      const color = window.BedCanvas ? BedCanvas.getColor(e.filename || e.name) : "#4dabf7";
      const tools = (e.tools || []);
      const compat = (App.compatibility.matrix || []);
      const conflictTools = new Set(compat.filter(t => t.conflict).map(t => t.tool_number));

      const div = document.createElement("div");
      div.className = "tray-item";
      div.draggable = true;
      div.dataset.path = path;

      const tagHtml = tools.map(t =>
        `<span class="tag${conflictTools.has(t) ? " conflict" : ""}">${t}</span>`
      ).join("");
      const dims = e.vcarve_x_span ? `${e.vcarve_x_span}×${e.vcarve_y_span}mm · ${e.material_thickness||"?"}mm` : "";
      const zStatus = e.z_status === "ok" ? "✓" : e.z_status === "warning" ? "⚠" : "✕";
      const zColor = e.z_status === "ok" ? "#30d158" : e.z_status === "warning" ? "#ffd60a" : "#ff453a";

      div.innerHTML = `
        <div class="dot" style="background:${color}"></div>
        <div class="info">
          <div class="name">${e.name || e.filename}</div>
          <div class="dims">${dims}</div>
          <div class="tags">${tagHtml}<span style="margin-left:4px;color:${zColor};font-size:10px">${zStatus}</span></div>
        </div>
        <button class="remove" title="Remove from tray">×</button>
      `;

      div.querySelector(".remove").addEventListener("click", ev => {
        ev.stopPropagation();
        remove(path);
      });

      div.addEventListener("dragstart", ev => {
        ev.dataTransfer.setData("text/plain", path);
        window._cncDragPart = { ...e, filename: e.name || e.filename };
        div.classList.add("dragging");
      });
      div.addEventListener("dragend", () => {
        div.classList.remove("dragging");
        window._cncDragPart = null;
      });

      list.appendChild(div);
    }
  }

  function refresh() { _render(); }

  function getAll() { return [...trayParts.values()]; }

  return { add, remove, refresh, getAll };
})();


document.addEventListener("DOMContentLoaded", () => {
  LibraryPanel.init();
});
