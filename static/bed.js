/**
 * bed.js — CNC Nest Tool canvas renderer
 *
 * Coordinate system (from spec):
 *   canvas_x = (BED_Y_MM - machine_y) * scale   ← Y=0 (operator) at right
 *   canvas_y = (BED_X_MM - machine_x) * scale   ← X=0 (A rail)   at bottom
 *
 * Zoom/pan state is kept in this module. Other modules call
 *   BedCanvas.render() after updating App.placements.
 */

var BedCanvas = (() => {
  // ── machine constants (overwritten from /api/config on init) ──────────────
  let BED_X_MM = 1668.788;
  let BED_Y_MM = 3123.0;

  // Per-rail geometry — the two rails are independent fixturing systems at
  // opposite ends of the machine and run in OPPOSITE slot directions, so each
  // carries its own datum. Overwritten from /api/config on init; must stay in
  // step with collision.RAIL_DEFAULTS on the backend.
  let RAILS = {
    A: { x_mm:  134.628, slot0_y_mm: 3034.700, slot_dir: -1, x_dir:  1 },
    B: { x_mm: 1534.160, slot0_y_mm:   88.300, slot_dir:  1, x_dir: -1 },
  };

  const railX    = rail => RAILS[rail].x_mm;
  const slotMarkY = (rail, slotInches) =>
    RAILS[rail].slot0_y_mm + RAILS[rail].slot_dir * slotInches * 25.4;

  // slot data loaded from /api/slots
  let SLOTS = [];

  // corporate logo watermark
  let _logoImg = null;
  const _logoEl = new Image();
  _logoEl.src = '/static/logo.png';
  _logoEl.onload = () => { _logoImg = _logoEl; render(); };

  // ── canvas state ──────────────────────────────────────────────────────────
  let canvas, ctx, area;
  let baseScale = 1;        // px/mm at zoom=1 (fit-to-window)
  let zoom      = 1.0;
  let panX      = 0;        // CSS-pixel pan offset
  let panY      = 0;
  const MIN_ZOOM = 0.5;     // allow slightly smaller to handle tall windows
  const MAX_ZOOM = 10;

  // ── interaction state ─────────────────────────────────────────────────────
  let isPanning   = false;
  let panStart    = {x: 0, y: 0};
  let panOrigin   = {x: 0, y: 0};
  let dragState   = null;   // set by placement.js via BedCanvas.beginDrag()
  let hoverSlot   = null;   // {rail, slot_inches} during drag
  let hoverPart   = null;   // instance_id under cursor
  let viewMode    = "all"; // "bounds" | "cuts" | "all"

  // ── part colors ───────────────────────────────────────────────────────────
  const PALETTE = [
    "#4dabf7","#69db7c","#ffd43b","#f783ac","#a9e34b",
    "#74c0fc","#63e6be","#ffa94d","#da77f2","#4dabf7",
  ];
  const partColors = new Map(); // filename → color
  let colorIdx = 0;
  function colorForPart(filename) {
    if (!partColors.has(filename)) {
      partColors.set(filename, PALETTE[colorIdx % PALETTE.length]);
      colorIdx++;
    }
    return partColors.get(filename);
  }

  // ── coordinate transforms ─────────────────────────────────────────────────
  function toCanvas(machX, machY) {
    const s = baseScale * zoom;
    return {
      x: panX + (BED_Y_MM - machY) * s,
      y: panY + (BED_X_MM - machX) * s,
    };
  }

  function toMachine(cx, cy) {
    const s = baseScale * zoom;
    return {
      x: BED_X_MM - (cy - panY) / s,
      y: BED_Y_MM - (cx - panX) / s,
    };
  }

  function mmToPx(mm) {
    return mm * baseScale * zoom;
  }

  // ── fit-to-window ─────────────────────────────────────────────────────────
  function fitToWindow() {
    const w = area.clientWidth;
    const h = area.clientHeight;
    const MARGIN = 24;
    baseScale = Math.min((w - MARGIN * 2) / BED_Y_MM, (h - MARGIN * 2) / BED_X_MM);
    zoom = 1.0;
    // Center the bed in the canvas area
    const bedW = BED_Y_MM * baseScale;
    const bedH = BED_X_MM * baseScale;
    panX = (w - bedW) / 2;
    panY = (h - bedH) / 2;
  }

  // ── canvas resize ─────────────────────────────────────────────────────────
  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const w   = area.clientWidth;
    const h   = area.clientHeight;
    canvas.width  = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width  = w + "px";
    canvas.style.height = h + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    fitToWindow();
    render();
  }

  // ── main render ───────────────────────────────────────────────────────────
  function render() {
    if (!ctx) return;
    const w = area.clientWidth;
    const h = area.clientHeight;
    ctx.clearRect(0, 0, w, h);

    _drawBed(w, h);
    _drawLogo();
    _drawSlotMarks();
    _drawRuler();
    _drawParts();
    _drawDragFeedback();
    _drawOriginLabel(w, h);
    _updateZoomIndicator();
  }

  // ── bed background ────────────────────────────────────────────────────────
  function _drawBed(w, h) {
    const tl = toCanvas(BED_X_MM, 0);
    const br = toCanvas(0, BED_Y_MM);
    const bw = br.x - tl.x;
    const bh = br.y - tl.y;

    // Bed fill
    ctx.fillStyle = "#1a1a1a";
    ctx.fillRect(tl.x, tl.y, bw, bh);

    // A rail zone (bottom) — blue tint, machine X 0..railX("A")
    const aTop = toCanvas(railX("A"), 0);
    const aBot = toCanvas(0, BED_Y_MM);
    ctx.fillStyle = "rgba(30, 80, 180, 0.18)";
    ctx.fillRect(tl.x, aTop.y, bw, aBot.y - aTop.y);

    // B rail zone (top) — green tint, machine X railX("B")..BED_X
    const bTop = toCanvas(BED_X_MM, 0);
    const bBot = toCanvas(railX("B"), BED_Y_MM);
    ctx.fillStyle = "rgba(30, 160, 60, 0.15)";
    ctx.fillRect(tl.x, bTop.y, bw, bBot.y - bTop.y);

    // Rail face lines — the machine-X datum each rail's blanks register against
    ctx.strokeStyle = "rgba(60, 120, 255, 0.5)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    const aLine = toCanvas(railX("A"), 0);
    const aLine2 = toCanvas(railX("A"), BED_Y_MM);
    ctx.moveTo(aLine.x, aLine.y);
    ctx.lineTo(aLine2.x, aLine2.y);
    ctx.stroke();

    ctx.strokeStyle = "rgba(50, 200, 80, 0.5)";
    ctx.beginPath();
    const bLine = toCanvas(railX("B"), 0);
    const bLine2 = toCanvas(railX("B"), BED_Y_MM);
    ctx.moveTo(bLine.x, bLine.y);
    ctx.lineTo(bLine2.x, bLine2.y);
    ctx.stroke();

    // Bed border
    ctx.strokeStyle = "#444";
    ctx.lineWidth = 1;
    ctx.strokeRect(tl.x, tl.y, bw, bh);

    // Slot grid lines (faint) — drawn per rail across that rail's half of the
    // bed only, because a slot number sits at a different Y on each rail.
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    const midX = BED_X_MM / 2;
    for (const slot of SLOTS) {
      for (const rail of ["A", "B"]) {
        const machY = slotMarkY(rail, slot.inches);
        const p1 = toCanvas(rail === "A" ? 0 : midX, machY);
        const p2 = toCanvas(rail === "A" ? midX : BED_X_MM, machY);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
      }
    }
  }

  // ── logo watermark ────────────────────────────────────────────────────────
  function _drawLogo() {
    if (!_logoImg) return;
    const tl = toCanvas(BED_X_MM, 0);
    const br = toCanvas(0, BED_Y_MM);
    const bw = br.x - tl.x;
    const bh = br.y - tl.y;
    const logoW = bw * 0.18;
    const logoH = logoW / 2.49;
    const cx = tl.x + bw / 2;
    const cy = tl.y + bh / 2;
    ctx.save();
    ctx.filter = 'invert(1) brightness(1.8)';
    ctx.globalAlpha = 0.22;
    ctx.drawImage(_logoImg, cx - logoW / 2, cy - logoH / 2, logoW, logoH);
    ctx.restore();
  }

  // ── slot marks ────────────────────────────────────────────────────────────
  function _drawSlotMarks() {
    const s = baseScale * zoom;
    if (s < 0.04) return; // too small to see
    const size = Math.min(Math.max(4, s * 8), 12);

    for (const slot of SLOTS) {
      const aPos = toCanvas(railX("A"), slotMarkY("A", slot.inches));
      const bPos = toCanvas(railX("B"), slotMarkY("B", slot.inches));

      if (slot.pitch.includes("13")) {
        _drawTriangle(ctx, aPos.x, aPos.y, size, "up",   "#4dabf7");
        _drawTriangle(ctx, bPos.x, bPos.y, size, "down", "#4dabf7");
      }
      if (slot.pitch.includes("19.5")) {
        _drawCircle(ctx, aPos.x, aPos.y + (slot.pitch.includes("13") ? size + 2 : 0), size * 0.55, "#30d158");
        _drawCircle(ctx, bPos.x, bPos.y - (slot.pitch.includes("13") ? size + 2 : 0), size * 0.55, "#30d158");
      }

      // Slot labels (hide when very small)
      if (s > 0.08) {
        const fontSize = Math.min(Math.max(9, s * 14), 12);
        ctx.font = `${fontSize}px system-ui`;
        ctx.fillStyle = "rgba(180,180,180,0.55)";
        ctx.textAlign = "center";
        const label = slot.label_a.replace("A", "");
        // A rail label below mark
        ctx.fillText(slot.label_a, aPos.x, aPos.y + size + fontSize + 2);
        ctx.fillText(slot.label_b, bPos.x, bPos.y - size - 4);
      }
    }
  }

  function _drawTriangle(ctx, cx, cy, size, dir, color) {
    const h = size;
    ctx.beginPath();
    if (dir === "up") {
      ctx.moveTo(cx, cy - h);
      ctx.lineTo(cx + h * 0.6, cy + h * 0.4);
      ctx.lineTo(cx - h * 0.6, cy + h * 0.4);
    } else {
      ctx.moveTo(cx, cy + h);
      ctx.lineTo(cx + h * 0.6, cy - h * 0.4);
      ctx.lineTo(cx - h * 0.6, cy - h * 0.4);
    }
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
  }

  function _drawCircle(ctx, cx, cy, r, color) {
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }

  // ── ruler ─────────────────────────────────────────────────────────────────
  function _drawRuler() {
    // Operator inches ruler along the bottom (A rail side)
    // Operator inches: 0 at operator (Y=0, right), increases left
    const s = baseScale * zoom;
    if (s < 0.03) return;

    const aRailY = toCanvas(0, 0).y + 2; // canvas Y of machine X=0 (A rail bottom)
    const RULER_H = 16;

    ctx.fillStyle = "rgba(0,0,0,0.5)";
    ctx.fillRect(toCanvas(0, BED_Y_MM).x, aRailY, mmToPx(BED_Y_MM), RULER_H);

    // Tick every 10", label every 20"
    const tickPx = s * 25.4 * 10;
    if (tickPx < 6) return;
    ctx.fillStyle = "#888";
    ctx.strokeStyle = "#888";
    ctx.lineWidth = 0.5;
    ctx.font = "9px system-ui";
    ctx.textAlign = "center";

    const bedInches = BED_Y_MM / 25.4;
    for (let op = 0; op <= bedInches; op += 10) {
      const machY = BED_Y_MM - op * 25.4;
      const cx = toCanvas(0, machY).x;
      const isMajor = op % 20 === 0;
      const tickH = isMajor ? 8 : 4;
      ctx.beginPath();
      ctx.moveTo(cx, aRailY);
      ctx.lineTo(cx, aRailY + tickH);
      ctx.stroke();
      if (isMajor && tickPx > 20) {
        ctx.fillText(op + '"', cx, aRailY + RULER_H - 1);
      }
    }
  }

  // ── placed parts ──────────────────────────────────────────────────────────
  function _drawParts() {
    const placements = App?.placements ?? [];
    console.log("[render] _drawParts called, placements.length =", placements.length);
    for (const p of placements) {
      _drawOnePart(p);
    }
  }

  function _drawOnePart(p) {
    console.log("[render] _drawOnePart", {
      filename: p.filename, slot: p.slot,
      machine_x: p.machine_x, machine_y: p.machine_y,
      vcarve_x_span: p.vcarve_x_span, vcarve_y_span: p.vcarve_y_span,
    });
    const color = colorForPart(p.filename);
    const s = baseScale * zoom;

    // Blank extent comes straight from the backend (collision.blank_rect) so the
    // canvas cannot disagree with collision detection or the generator.
    const [machX0, machX1, machY0, machY1] = p.blank;

    const tl = toCanvas(machX1, machY1);  // true top-left: high machX (up), high machY (left)
    const br = toCanvas(machX0, machY0);  // true bottom-right: low machX (down), low machY (right)
    const rw = br.x - tl.x;
    const rh = br.y - tl.y;

    // Solid blank boundary
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.fillStyle = hexToRgba(color, 0.08);
    ctx.beginPath();
    ctx.rect(tl.x, tl.y, rw, rh);
    ctx.fill();
    ctx.stroke();

    // Toolpath extents (dashed, lighter)
    if (p.tp_min_x !== undefined && p.tp_max_x !== undefined) {
      const tpTL = toCanvas(p.tp_max_x, p.tp_max_y);
      const tpBR = toCanvas(p.tp_min_x, p.tp_min_y);
      ctx.strokeStyle = hexToRgba(color, 0.5);
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.rect(tpTL.x, tpTL.y, tpBR.x - tpTL.x, tpBR.y - tpTL.y);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Collision highlight
    if (p.collision) {
      ctx.fillStyle = "rgba(255, 60, 60, 0.25)";
      ctx.fillRect(tl.x, tl.y, rw, rh);
      ctx.strokeStyle = "#ff3c3c";
      ctx.lineWidth = 2;
      ctx.strokeRect(tl.x, tl.y, rw, rh);
    }

    // Part label (hide when tiny)
    if (s > 0.05) {
      const fontSize = Math.min(Math.max(9, s * 10), 13);
      ctx.font = `${fontSize}px system-ui`;
      ctx.fillStyle = color;
      ctx.textAlign = "left";
      const label = p.filename.replace(/\.[^.]+$/, "") + " · " + p.slot;
      ctx.fillText(label, tl.x + 3, tl.y + fontSize + 2);
    }

    // Cut-move toolpaths (segments pre-transformed to machine coords by backend)
    if (viewMode !== "bounds" && p.segments) {
      _drawSegments(p, color);
    }
  }

  function _drawSegments(p, color) {
    if (!p.segments || !p.segments.length) return;
    const showAll = viewMode === "all";
    ctx.lineWidth = Math.min(Math.max(0.5, baseScale * zoom * 0.3), 2);

    // Cutting moves — solid, part color
    ctx.strokeStyle = color;
    ctx.setLineDash([]);
    ctx.beginPath();
    for (const seg of p.segments) {
      if (!seg.cutting) continue;
      const from = toCanvas(seg.x1, seg.y1);
      const to   = toCanvas(seg.x2, seg.y2);
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x,   to.y);
    }
    ctx.stroke();

    // Rapid moves — dashed gray, only in "all" mode
    if (showAll) {
      ctx.strokeStyle = "rgba(150,150,150,0.4)";
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      for (const seg of p.segments) {
        if (seg.cutting) continue;
        const from = toCanvas(seg.x1, seg.y1);
        const to   = toCanvas(seg.x2, seg.y2);
        ctx.moveTo(from.x, from.y);
        ctx.lineTo(to.x,   to.y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  // ── drag feedback ─────────────────────────────────────────────────────────
  function _drawDragFeedback() {
    if (!dragState) return;

    // Glow on rail face during drag
    const rail = dragState.targetRail || dragState.nearestRail;
    if (rail) {
      const machX = railX(rail);
      const p1 = toCanvas(machX, 0);
      const p2 = toCanvas(machX, BED_Y_MM);
      ctx.shadowColor = rail === "A" ? "#4dabf7" : "#30d158";
      ctx.shadowBlur = 10;
      ctx.strokeStyle = rail === "A" ? "#4dabf7" : "#30d158";
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    // Highlight target slot
    if (hoverSlot) {
      const { rail: hr, slot_inches } = hoverSlot;
      const machY = slotMarkY(hr, slot_inches);
      const pos = toCanvas(railX(hr), machY);
      ctx.strokeStyle = hr === "A" ? "#4dabf7" : "#30d158";
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(pos.x - 8, pos.y);
      ctx.lineTo(pos.x + 8, pos.y);
      ctx.stroke();
      ctx.setLineDash([]);

      // Ghost part outline
      if (dragState.part) {
        const color = colorForPart(dragState.part.filename);
        const bw = dragState.part.vcarve_x_span;
        const bh = dragState.part.vcarve_y_span;
        // Mirrors collision.blank_rect: the blank runs from each rail's datum in
        // that rail's own direction (vcarve_y_span across X, vcarve_x_span along Y).
        const g = RAILS[hr];
        const xA = g.x_mm, xB = g.x_mm + g.x_dir * bh;
        const yA = machY,  yB = machY + g.slot_dir * bw;
        const gTL = toCanvas(Math.max(xA, xB), Math.max(yA, yB));
        const gBR = toCanvas(Math.min(xA, xB), Math.min(yA, yB));
        ctx.strokeStyle = hexToRgba(color, 0.6);
        ctx.lineWidth = 1.5;
        ctx.setLineDash([5, 4]);
        ctx.strokeRect(gTL.x, gTL.y, gBR.x - gTL.x, gBR.y - gTL.y);
        ctx.setLineDash([]);
      }
    }
  }

  // ── origin label ──────────────────────────────────────────────────────────
  function _drawOriginLabel(w, h) {
    const pos = toCanvas(0, 0);
    ctx.font = "10px system-ui";
    ctx.fillStyle = "rgba(180,180,180,0.5)";
    ctx.textAlign = "right";
    ctx.fillText("0,0 operator ▶", pos.x - 4, pos.y + 12);
  }

  function _updateZoomIndicator() {
    const el = document.getElementById("zoom-indicator");
    if (el) el.textContent = Math.round(zoom * 100) + "%";
  }

  // ── zoom/pan event handlers ───────────────────────────────────────────────
  function _onWheel(e) {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
    const newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom * factor));
    if (newZoom === zoom) return;

    // Zoom centered on cursor
    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    panX = cx - (cx - panX) * (newZoom / zoom);
    panY = cy - (cy - panY) * (newZoom / zoom);
    zoom = newZoom;
    render();
  }

  function _onMouseDown(e) {
    if (e.button !== 0) return;
    const pos = _clientPos(e);

    // Check if clicking on a placed part for drag
    const hit = _hitTestPart(pos.x, pos.y);
    if (hit && window.Placement) {
      Placement.beginCanvasDrag(hit, pos.x, pos.y);
      return;
    }

    isPanning = true;
    panStart  = pos;
    panOrigin = { x: panX, y: panY };
    canvas.style.cursor = "grabbing";
  }

  function _onMouseMove(e) {
    const pos = _clientPos(e);

    if (dragState) {
      dragState.curX = pos.x;
      dragState.curY = pos.y;
      hoverSlot = _findNearestSlot(pos.x, pos.y);
      render();
      return;
    }

    if (isPanning) {
      panX = panOrigin.x + (pos.x - panStart.x);
      panY = panOrigin.y + (pos.y - panStart.y);
      render();
      return;
    }

    // Hover cursor
    const hit = _hitTestPart(pos.x, pos.y);
    canvas.style.cursor = hit ? "grab" : "crosshair";
    if (hit !== hoverPart) { hoverPart = hit; render(); }
  }

  function _onMouseUp(e) {
    const pos = _clientPos(e);

    if (dragState && window.Placement) {
      Placement.endCanvasDrag(hoverSlot, pos.x, pos.y);
      dragState = null;
      hoverSlot = null;
      render();
      return;
    }

    isPanning = false;
    canvas.style.cursor = "crosshair";
  }

  function _onDblClick() {
    fitToWindow();
    render();
  }

  function _clientPos(e) {
    const rect = canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  // ── hit testing ───────────────────────────────────────────────────────────
  function _hitTestPart(cx, cy) {
    const placements = App?.placements ?? [];
    for (const p of [...placements].reverse()) {
      const [machX0, machX1, machY0, machY1] = p.blank;
      const tl = toCanvas(machX1, machY1);
      const br = toCanvas(machX0, machY0);
      if (cx >= tl.x && cx <= br.x && cy >= tl.y && cy <= br.y) {
        return p.instance_id;
      }
    }
    return null;
  }

  // ── slot snap helper ──────────────────────────────────────────────────────
  function _findNearestSlot(cx, cy) {
    const mach = toMachine(cx, cy);
    // Determine rail from machine X position
    const midX = BED_X_MM / 2;
    const rail = mach.x < midX ? "A" : "B";

    // Measure against the slot positions of the rail we're actually over — the
    // two rails put the same slot number at different machine Y.
    let best = null, bestD = Infinity;
    for (const slot of SLOTS) {
      const d = Math.abs(mach.y - slotMarkY(rail, slot.inches));
      if (d < bestD) { bestD = d; best = slot; }
    }
    if (!best) return null;
    return { rail, slot_inches: best.inches };
  }

  // ── drag API (called by placement.js / sidebar.js) ────────────────────────
  function beginDrag(state) {
    dragState = state;
    hoverSlot = null;
    canvas.style.cursor = "grabbing";
  }

  function endDrag() {
    const result = hoverSlot;
    dragState = null;
    hoverSlot = null;
    canvas.style.cursor = "crosshair";
    render();
    return result;
  }

  // ── colour utils ──────────────────────────────────────────────────────────
  function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  function getColor(filename) { return colorForPart(filename); }

  // ── drop target (sidebar drag) ────────────────────────────────────────────
  function _onDragOver(e) {
    e.preventDefault();
    const pos  = _clientPos(e);
    const mach = toMachine(pos.x, pos.y);
    const rail = mach.x < BED_X_MM / 2 ? "A" : "B";
    hoverSlot  = _findNearestSlot(pos.x, pos.y);
    if (hoverSlot) hoverSlot.rail = rail;

    // dataTransfer.getData() returns "" during dragover (browser security);
    // sidebar.js stores the part in window._cncDragPart on dragstart instead.
    if (!dragState) dragState = {};
    dragState.nearestRail = rail;
    if (!dragState.part && window._cncDragPart) {
      dragState.part = window._cncDragPart;
    }
    render();
  }

  async function _onDrop(e) {
    try {
      e.preventDefault();
      const path = e.dataTransfer.getData("text/plain");
      if (path && hoverSlot && window.Placement) {
        await Placement.placeFromDrop(path, hoverSlot.rail, hoverSlot.slot_inches);
      }
      dragState = null;
      hoverSlot = null;
      window._cncDragPart = null;
      render();
    } catch (err) {
      console.error("[drop] EXCEPTION in _onDrop:", err);
    }
  }

  function _onDragLeave(e) {
    // Only clear when leaving the canvas entirely, not entering a child element
    if (e.relatedTarget && canvas.contains(e.relatedTarget)) return;
    dragState = null;
    hoverSlot = null;
    render();
  }

  // ── init ──────────────────────────────────────────────────────────────────
  function init() {
    canvas = document.getElementById("bed-canvas");
    ctx    = canvas.getContext("2d");
    area   = document.getElementById("canvas-area");

    canvas.addEventListener("wheel",     _onWheel,     { passive: false });
    canvas.addEventListener("mousedown", _onMouseDown);
    canvas.addEventListener("mousemove", _onMouseMove);
    canvas.addEventListener("mouseup",   _onMouseUp);
    canvas.addEventListener("dblclick",  _onDblClick);
    canvas.addEventListener("dragover",  _onDragOver);
    canvas.addEventListener("drop",      _onDrop);
    canvas.addEventListener("dragleave", _onDragLeave);

    // View toggle buttons
    document.querySelectorAll("#view-toggle button").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("#view-toggle button").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        viewMode = btn.dataset.view;
        render();
      });
    });

    // Load config and slot data, then size canvas
    Promise.all([
      fetch("/api/config").then(r => r.json()),
      fetch("/api/slots").then(r => r.json()),
    ]).then(([cfg, slotData]) => {
      BED_X_MM = parseFloat(cfg.advanced.bed_x_mm);
      BED_Y_MM = parseFloat(cfg.advanced.bed_y_mm);
      // Prefer the resolved geometry /api/slots returns (defaults already merged
      // in on the backend) so the canvas can't drift from collision detection.
      const resolved = slotData.rails || cfg.advanced.rails;
      if (resolved) {
        for (const rail of ["A", "B"]) {
          if (resolved[rail]) RAILS[rail] = { ...RAILS[rail], ...resolved[rail] };
        }
      }
      SLOTS    = slotData.slots;
      resize();
    }).catch(() => resize());

    new ResizeObserver(resize).observe(area);
  }

  // ── public API ────────────────────────────────────────────────────────────
  return { init, render, beginDrag, endDrag, getColor, fitToWindow };
})();

document.addEventListener("DOMContentLoaded", () => BedCanvas.init());
