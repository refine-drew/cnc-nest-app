/**
 * layout.js — panel sizing, collapse, and remembering both (issue #29).
 *
 * Three things live here:
 *
 *   1. **Column handles**, moved out of `index.html` and generalised to take an axis.
 *      The old copy was column-only — `cursor:col-resize`, `e.clientX`, `--lw`/`--rw` —
 *      and the cheap path to in-panel dividers is one axis-aware handler rather than
 *      two near-copies of a 90-line IIFE.
 *   2. **In-panel dividers**, on the rule *between* sections rather than on the `h2`
 *      itself. Clicking a header to collapse its section is a likely next ask, and the
 *      two gestures must not fight over the same pixels.
 *   3. **Persistence**, in `localStorage` rather than `config.json`. Layout is
 *      per-workstation — the shop PC and a laptop have different screens and want
 *      different splits — while `config.json` holds shop truth (rail datums, tool
 *      capacity, paths) and is the file you would copy to a second machine. Writing
 *      panel pixels into it would mean changing a monitor edits the same file as the
 *      machine's geometry.
 *
 * Everything is **clamped on read**: a 520px sidebar saved on a wide monitor must come
 * back clamped on a laptop, not off-screen. And writes happen on mouseup, not on every
 * mousemove.
 */

var Layout = (() => {
  const KEY = "cncnest.layout.v1";

  // [min, max, default] per stored dimension.
  const BOUNDS = {
    lw:       [140, 520, 240],
    rw:       [140, 520, 260],
    libH:     [60, 900, 240],
    plH:      [60, 900, 160],
    // The dock's floor is not cosmetic. Spec §3.4.1 force-expands the dock while the
    // map is invalid so a disabled Generate always has a visible reason on screen; a
    // height drag with no floor would be a collapse by the back door. 74px is one full
    // card row plus the pocket's own header — below that a card clips and the reason
    // stops being readable.
    pocketH:  [74, 260, 92],
  };

  const main = () => document.getElementById("main");
  let saved = {};

  function _clamp(name, value) {
    const [min, max, dflt] = BOUNDS[name];
    const n = parseFloat(value);
    if (!isFinite(n)) return dflt;
    return Math.max(min, Math.min(max, n));
  }

  function load() {
    let raw = {};
    try { raw = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { raw = {}; }
    saved = {};
    for (const name of Object.keys(BOUNDS)) saved[name] = _clamp(name, raw[name]);
    saved.leftHidden     = !!raw.leftHidden;
    saved.rightHidden    = !!raw.rightHidden;
    saved.dockCollapsed  = !!raw.dockCollapsed;
    return saved;
  }

  /** Written on mouseup, never on mousemove. An adjustment that resets on reload is
   *  worse than none: the operator redoes it every launch and then stops bothering. */
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) { /* private mode */ }
  }

  function set(name, value) {
    saved[name] = _clamp(name, value);
    apply();
  }

  function get(name) { return saved[name]; }

  function setFlag(name, value) { saved[name] = !!value; save(); }

  function apply() {
    const m = main();
    if (!m) return;
    m.style.setProperty("--lw", saved.lw + "px");
    m.style.setProperty("--rw", saved.rw + "px");
    document.documentElement.style.setProperty("--lib-h", saved.libH + "px");
    document.documentElement.style.setProperty("--pl-h", saved.plH + "px");
    document.documentElement.style.setProperty("--pocket-h", saved.pocketH + "px");
    if (window.BedCanvas) BedCanvas.render();
  }

  // ── one axis-aware drag handler ───────────────────────────────────────────

  let drag = null;   // {el, name, axis, sign, start, startValue}

  function _bind(el, name, axis, sign) {
    if (!el) return;
    el.addEventListener("mousedown", e => {
      if (e.target.classList.contains("rh-tab")) return;   // collapse tab owns its click
      if (el.dataset.disabled === "1") return;
      drag = {
        el, name, axis, sign,
        start: axis === "x" ? e.clientX : e.clientY,
        startValue: saved[name],
      };
      document.body.classList.add(axis === "x" ? "resizing-x" : "resizing-y");
      el.classList.add("dragging");
      e.preventDefault();
    });
  }

  function _onMove(e) {
    if (!drag) return;
    const now = drag.axis === "x" ? e.clientX : e.clientY;
    set(drag.name, drag.startValue + drag.sign * (now - drag.start));
  }

  function _onUp() {
    if (!drag) return;
    drag.el.classList.remove("dragging");
    document.body.classList.remove("resizing-x", "resizing-y");
    drag = null;
    save();       // one write per gesture
  }

  // ── collapse ──────────────────────────────────────────────────────────────

  function _applyCollapse() {
    const m = main();
    m.classList.toggle("left-hidden", saved.leftHidden);
    m.classList.toggle("right-hidden", saved.rightHidden);
    document.getElementById("sidebar-left").classList.toggle("hidden", saved.leftHidden);
    document.getElementById("sidebar-right").classList.toggle("hidden", saved.rightHidden);
    const tl = document.getElementById("tab-left");
    const tr = document.getElementById("tab-right");
    tl.textContent = saved.leftHidden ? "▶" : "◀";
    tl.title = saved.leftHidden ? "Show library" : "Hide library";
    tr.textContent = saved.rightHidden ? "◀" : "▶";
    tr.title = saved.rightHidden ? "Show job panel" : "Hide job panel";
    document.getElementById("rh-left").dataset.disabled = saved.leftHidden ? "1" : "0";
    document.getElementById("rh-right").dataset.disabled = saved.rightHidden ? "1" : "0";
    if (window.BedCanvas) BedCanvas.render();
  }

  function toggle(side) {
    saved[side === "left" ? "leftHidden" : "rightHidden"] =
      !saved[side === "left" ? "leftHidden" : "rightHidden"];
    _applyCollapse();
    save();
  }

  function init() {
    load();
    apply();
    _applyCollapse();

    // Columns. The right handle moves the opposite way from the left, which is the
    // only thing the two ever disagreed about.
    _bind(document.getElementById("rh-left"),  "lw", "x", +1);
    _bind(document.getElementById("rh-right"), "rw", "x", -1);
    // In-panel dividers — the rule between two sections, not the header above it.
    _bind(document.getElementById("split-left"),  "libH",    "y", +1);
    _bind(document.getElementById("split-right"), "plH",     "y", +1);
    // The dock grows upward, so dragging its top edge up must make the cells taller.
    _bind(document.getElementById("dock-grip"),   "pocketH", "y", -1);

    document.getElementById("tab-left").addEventListener("click", e => {
      e.stopPropagation(); toggle("left");
    });
    document.getElementById("tab-right").addEventListener("click", e => {
      e.stopPropagation(); toggle("right");
    });

    document.addEventListener("mousemove", _onMove);
    document.addEventListener("mouseup", _onUp);
  }

  return { init, get, set, save, setFlag, apply };
})();

document.addEventListener("DOMContentLoaded", () => Layout.init());
