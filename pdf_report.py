"""Operator layout PDF.

Replaces the old plain-text sidecar report. The centerpiece is a scale,
top-down picture of the CNC bed showing every placed part as a labeled,
color-coded blank at its exact location (with a faint toolpath preview inside
each blank to confirm orientation). A placement table + job summary carry over
every field the old .txt report contained.

Orientation matches the on-screen canvas (static/bed.js): the operator stands at
the right (machine Y = 0), the A rail runs along the bottom (machine X = 0), the
B rail along the top. Machine→page mapping (PDF origin is lower-left):

    page_x = ox + (BED_Y - machine_y) * s     # operator (Y=0) at right
    page_y = oy + machine_x * s               # A rail (X=0) at bottom

This module does no coordinate math beyond that final mapping — callers pass part
blanks and toolpath segments already in machine coordinates.
"""

from pathlib import Path

from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

PAGE = landscape(letter)          # (792, 612) pt
MARGIN = 36                       # 0.5"

_LOGO_PATH = Path(__file__).parent / "static" / "logo.png"
_LOGO_H = 32    # pt
_LOGO_W = 80    # pt (~2.5:1 aspect ratio)

# Same palette as static/bed.js so PDF colors match the screen.
PALETTE = [
    "#4dabf7", "#69db7c", "#ffd43b", "#f783ac", "#a9e34b",
    "#74c0fc", "#63e6be", "#ffa94d", "#da77f2", "#66d9e8",
]

# ATC graphic: 8-position tool holder carousel
_ATC_SLOTS    = 8
_ATC_SLOT_W   = 86          # width of each holder slot (pt)
_ATC_SLOT_GAP = 4           # gap between slots (pt)
_ATC_TITLE_H  = 16          # height reserved for section title
_ATC_RACK_H   = 10          # horizontal mounting rail bar
_ATC_COLLAR_H = 14          # flange collar at top of each holder
_ATC_BODY_H   = 58          # main holder body
_ATC_TIP_H    = 9           # shank tip line below body
_ATC_PAD      = 5           # bottom padding below tip

_ROW_H = 14                 # table row height


def palette_color(index: int) -> str:
    """Hex color for the Nth distinct part (cycles), matching bed.js."""
    return PALETTE[index % len(PALETTE)]


def _strip_ext(name: str) -> str:
    return name.rsplit(".", 1)[0] if "." in name else name


def _tools_str(tools: list) -> str:
    """`T2→T8 0.5"` — the number the file posts and the pocket it will run from.

    Both are shown because the operator reads this beside a CAM file that still says
    `T2`, and the machine will call `T8`. Hiding either half is how the two meanings
    of `T#` get confused again, which is what §1 exists to stop.
    """
    parts = []
    for t in tools or []:
        dia = t.get("diameter_inches")
        num = t.get("tool_number", "")
        pocket = t.get("pocket")
        word = f'{num}→T{pocket}' if pocket and f"T{pocket}" != num else num
        parts.append(f'{word} ({dia:g}")' if dia else word)
    return ", ".join(p for p in parts if p) or "—"


def _atc_section_height() -> float:
    return _ATC_TITLE_H + _ATC_RACK_H + _ATC_COLLAR_H + _ATC_BODY_H + _ATC_TIP_H + _ATC_PAD


def generate_layout_pdf(out_path, meta: dict, parts: list, geom: dict) -> None:
    """Render the layout PDF to out_path.

    meta:  job_name, date, bed_x_mm, bed_y_mm, safe_z {value, driven_by},
           rail_note, tool_sequence (list), tool_changes, parts_count, runtime (str|None),
           setup (list) — the operator setup sheet, one entry per loaded pocket:
           {pocket, code, name, diameter_inches, geometry_class, flute_display,
            default_slot, off_home, parts:[filename]}
    parts: per-part dicts in machine coords —
           {index, label, name, rail, slot_inches, size_mm:(x,y),
            blank:(min_x,max_x,min_y,max_y), segments:[{x1,y1,x2,y2,cutting}],
            tools:[{tool_number,description,diameter_inches}], color}
    geom:  bed_x_mm, bed_y_mm, slots (inches), rails {A,B: x_mm/slot0_y_mm/slot_dir/x_dir}
    """
    c = pdfcanvas.Canvas(str(out_path), pagesize=PAGE)
    pw, ph = PAGE

    # ── Page 1: condensed header + maximised bed diagram + ATC graphic ──────
    header_bottom = _draw_header(c, meta, pw, ph)

    atc_h = _atc_section_height()
    diagram_bottom = MARGIN + atc_h + 6       # 6 pt gap between diagram and ATC

    _draw_diagram(
        c, MARGIN, diagram_bottom,
        pw - 2 * MARGIN, header_bottom - diagram_bottom - 8,
        parts, geom,
    )
    _draw_atc(c, meta.get("setup") or [], pw, MARGIN + atc_h)

    # ── Page 2: full placement table ────────────────────────────────────────
    c.showPage()
    _draw_table(c, meta, parts, pw, ph, ph - MARGIN)
    c.save()


# ── header ────────────────────────────────────────────────────────────────────

def _draw_header(c, meta: dict, pw: float, ph: float) -> float:
    top = ph - MARGIN
    if _LOGO_PATH.exists():
        c.drawImage(ImageReader(str(_LOGO_PATH)),
                    pw - MARGIN - _LOGO_W, top - _LOGO_H,
                    width=_LOGO_W, height=_LOGO_H,
                    preserveAspectRatio=True, mask="auto")
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(MARGIN, top - 14, "LAGUNA SMARTSHOP 2 LAYOUT TOOL — LAYOUT SHEET")

    by, bx = meta["bed_y_mm"], meta["bed_x_mm"]
    sz = meta.get("safe_z") or {}
    safe_z_str = (
        f'{sz.get("value") / 25.4:.2f}"'
        if sz.get("value") is not None else "—"
    )
    fields = [
        f'Job: {meta["job_name"]}',
        f'Date: {meta["date"]}',
        f'Bed: {by/25.4:.0f}" × {bx/25.4:.0f}"',
        f'Safe Z: {safe_z_str}',
        f'Parts: {meta.get("parts_count", 0)}',
    ]
    if meta.get("runtime"):
        fields.append(f'Est. runtime: {meta["runtime"]}')

    c.setFont("Helvetica", 9)
    y = top - 30
    c.drawString(MARGIN, y, "    |    ".join(fields))
    y -= 12
    c.setLineWidth(0.5)
    c.line(MARGIN, y - 2, pw - MARGIN, y - 2)
    return y - 8


# ── bed diagram ───────────────────────────────────────────────────────────────

def _draw_diagram(c, x0, y0, w, h, parts, geom) -> None:
    BED_Y = float(geom["bed_y_mm"])
    BED_X = float(geom["bed_x_mm"])
    rails = geom.get("rails") or {}
    geom_a = rails.get("A") or {}
    geom_b = rails.get("B") or {}
    # Rail band thickness: distance from the rail datum to the near bed edge.
    rail_a_w = abs(float(geom_a.get("x_mm", 0.0)))
    rail_b_w = abs(BED_X - float(geom_b.get("x_mm", BED_X)))

    def _slot_y(rg, slot):
        return float(rg.get("slot0_y_mm", 0.0)) + float(rg.get("slot_dir", -1)) * slot * 25.4

    ruler_room = 30
    s = min(w / BED_Y, (h - ruler_room) / BED_X)
    draw_w, draw_h = BED_Y * s, BED_X * s
    ox = x0 + (w - draw_w) / 2
    oy = y0 + ruler_room + ((h - ruler_room) - draw_h) / 2

    def P(mach_x, mach_y):
        return (ox + (BED_Y - mach_y) * s, oy + mach_x * s)

    # Rail zones.
    c.saveState()
    c.setFillColor(HexColor("#1e50b4"))
    c.setFillAlpha(0.16)
    c.rect(ox, oy, draw_w, rail_a_w * s, fill=1, stroke=0)
    c.setFillColor(HexColor("#1ea03c"))
    c.setFillAlpha(0.15)
    c.rect(ox, oy + draw_h - rail_b_w * s, draw_w, rail_b_w * s, fill=1, stroke=0)
    c.restoreState()

    c.setFillColor(HexColor("#1e50b4"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(ox + 3, oy + 3, "A")
    c.setFillColor(HexColor("#1ea03c"))
    c.drawString(ox + 3, oy + draw_h - 13, "B")

    c.setStrokeColor(black)
    c.setLineWidth(1.0)
    c.rect(ox, oy, draw_w, draw_h, fill=0, stroke=1)

    # Slot rulers — one per rail. The rails run in OPPOSITE directions, so a slot
    # number sits at a different machine Y on each and needs its own scale:
    # A below the diagram, B above it.
    c.setFont("Helvetica", 6)
    c.setStrokeColor(HexColor("#888888"))
    c.setLineWidth(0.4)
    c.setFillColor(HexColor("#555555"))
    for slot in geom.get("slots", []):
        slot = float(slot)
        px = ox + (BED_Y - _slot_y(geom_a, slot)) * s
        c.line(px, oy - 3, px, oy - 9)
        c.drawCentredString(px, oy - 17, f"{slot:g}")
    c.drawRightString(ox + draw_w, oy - 26, "A rail slot inches")

    top = oy + draw_h
    for slot in geom.get("slots", []):
        slot = float(slot)
        px = ox + (BED_Y - _slot_y(geom_b, slot)) * s
        c.line(px, top + 3, px, top + 9)
        c.drawCentredString(px, top + 12, f"{slot:g}")
    c.drawRightString(ox + draw_w, top + 21, "B rail slot inches")

    for part in parts:
        _draw_part(c, P, part)


def _draw_part(c, P, part) -> None:
    min_x, max_x, min_y, max_y = part["blank"]
    color = HexColor(part["color"])

    ax, ay = P(min_x, max_y)
    bx, by = P(max_x, min_y)
    rx, ry = min(ax, bx), min(ay, by)
    rw, rh = abs(bx - ax), abs(by - ay)

    c.saveState()
    c.setFillColor(color)
    c.setFillAlpha(0.18)
    c.rect(rx, ry, rw, rh, fill=1, stroke=0)
    c.restoreState()
    c.setStrokeColor(color)
    c.setLineWidth(1.2)
    c.rect(rx, ry, rw, rh, fill=0, stroke=1)

    c.saveState()
    c.setStrokeColor(color)
    c.setStrokeAlpha(0.5)
    c.setLineWidth(0.4)
    path = c.beginPath()
    for seg in part.get("segments", []):
        if not seg.get("cutting"):
            continue
        sx, sy = P(seg["x1"], seg["y1"])
        ex, ey = P(seg["x2"], seg["y2"])
        path.moveTo(sx, sy)
        path.lineTo(ex, ey)
    c.drawPath(path, stroke=1, fill=0)
    c.restoreState()

    cx_p, cy_p = rx + rw / 2, ry + rh / 2
    r = 7
    c.setFillColor(color)
    c.circle(cx_p, cy_p, r, fill=1, stroke=0)
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(cx_p, cy_p - 3, str(part["index"]))

    if rw > 36:
        c.setFillColor(black)
        c.setFont("Helvetica", 6.5)
        label = f'{_strip_ext(part["name"])} · {part["label"]}'
        c.drawString(rx + 2, ry + rh - 8, label[:int(rw / 3.4)])


# ── ATC tool holder graphic ───────────────────────────────────────────────────

def _draw_atc(c, setup: list, pw: float, y_top: float) -> None:
    """The operator setup sheet (issue #13). y_top is the top edge of the section.

    Because pocket assignment is job-scoped and the app deliberately holds **no model
    of the physical changer contents** (spec §3.2), the operator has to load the machine
    to match a map the app invented. If that map is not communicated clearly, the
    feature manufactures exactly the failure it exists to prevent — so this graphic is
    the feature's other half, not decoration.

    It is keyed on **pocket**, not on any file's `T#`. Under identity merging those are
    different things: one cutter can be `T2` in one file and `T4` in another, and both
    run from whichever pocket the map assigned.

    A tool away from its declared home is called out as a **temporary** instruction —
    "return to 4 after this job" — because the declared slot is what the operator is
    being trained toward (§3.2.1). Note what "what changed" can and cannot mean here:
    nothing persists a previous job's map (save/load is sunset), so the only comparison
    available is against the *declared* home, which is also the more useful one.
    """
    by_pocket = {int(t["pocket"]): t for t in setup if t.get("pocket")}

    # Section title
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN, y_top - 12, "Tool Setup — Load into ATC before running")

    moved = [t for t in setup if t.get("off_home")]
    if moved:
        c.setFont("Helvetica-Oblique", 8)
        c.setFillColor(HexColor("#8a5a00"))
        note = "; ".join(
            f'{t["name"]} → pocket {t["pocket"]} for this job only (returns to {t["default_slot"]})'
            for t in moved[:3])
        c.drawString(MARGIN + 250, y_top - 12, note[:150])

    total_w = _ATC_SLOTS * _ATC_SLOT_W + (_ATC_SLOTS - 1) * _ATC_SLOT_GAP
    gx = MARGIN + (pw - 2 * MARGIN - total_w) / 2   # centre the graphic

    # Y positions for each graphic element (ReportLab: y increases upward)
    rack_bottom   = y_top - _ATC_TITLE_H - _ATC_RACK_H
    collar_bottom = rack_bottom - _ATC_COLLAR_H
    body_bottom   = collar_bottom - _ATC_BODY_H
    tip_bottom    = body_bottom - _ATC_TIP_H

    # Mounting rack bar spans all slots with a small overhang
    overhang = 14
    c.setFillColor(HexColor("#1e1e1e"))
    c.rect(gx - overhang, rack_bottom,
           total_w + 2 * overhang, _ATC_RACK_H, fill=1, stroke=0)
    # Subtle highlight stripe on rack
    c.saveState()
    c.setFillColor(HexColor("#ffffff"))
    c.setFillAlpha(0.12)
    c.rect(gx - overhang, rack_bottom + _ATC_RACK_H - 3,
           total_w + 2 * overhang, 3, fill=1, stroke=0)
    c.restoreState()

    for i in range(_ATC_SLOTS):
        slot_key = f"T{i + 1}"
        t = by_pocket.get(i + 1)
        sx = gx + i * (_ATC_SLOT_W + _ATC_SLOT_GAP)
        cx = sx + _ATC_SLOT_W / 2
        loaded = t is not None

        # ── Collar (flange that locks into carousel) ─────────────────────
        collar_color = HexColor("#14408a") if loaded else HexColor("#5a5a5a")
        c.setFillColor(collar_color)
        c.rect(sx, collar_bottom, _ATC_SLOT_W, _ATC_COLLAR_H, fill=1, stroke=0)
        # Thin highlight at top of collar
        c.saveState()
        c.setFillColor(HexColor("#ffffff"))
        c.setFillAlpha(0.20)
        c.rect(sx, collar_bottom + _ATC_COLLAR_H - 2, _ATC_SLOT_W, 2, fill=1, stroke=0)
        c.restoreState()

        # Position label centred in collar. The pocket number IS the tool word the
        # program calls, because H follows the pocket (§4.1) — so this reads the same
        # as the `T# M06` lines in the file.
        c.setFillColor(HexColor("#d8e8ff") if loaded else HexColor("#aaaaaa"))
        c.setFont("Helvetica-Bold", 9)
        label = slot_key + (" *" if loaded and t.get("off_home") else "")
        c.drawCentredString(cx, collar_bottom + 4, label)

        # ── Body (holder tube) ───────────────────────────────────────────
        bx = sx + 5
        bw = _ATC_SLOT_W - 10
        body_color = HexColor("#2662b8") if loaded else HexColor("#c0c0c0")
        c.setFillColor(body_color)
        c.roundRect(bx, body_bottom, bw, _ATC_BODY_H, 3, fill=1, stroke=0)
        # Subtle left-edge highlight for 3-D feel
        c.saveState()
        c.setFillColor(HexColor("#ffffff"))
        c.setFillAlpha(0.14)
        c.roundRect(bx, body_bottom, 6, _ATC_BODY_H, 3, fill=1, stroke=0)
        c.restoreState()

        # ── Shank / tool tip ─────────────────────────────────────────────
        shank_color = HexColor("#0d0d0d") if loaded else HexColor("#aaaaaa")
        c.setStrokeColor(shank_color)
        c.setLineWidth(2.2 if loaded else 0.8)
        c.line(cx, body_bottom, cx, tip_bottom)
        if loaded:
            # Cutting-edge indicator (horizontal line at tip)
            c.setLineWidth(1.5)
            c.line(cx - 5, tip_bottom, cx + 5, tip_bottom)

        # ── Tool info inside body ────────────────────────────────────────
        if loaded:
            dia = t.get("diameter_inches")
            dia_str = f'{dia:g}"' if dia else "—"
            # The library name, which is the operator's own word for the cutter — not a
            # post-processor's free text. That is the whole point of the library.
            desc = t.get("name") or "—"
            max_chars = int(bw / 3.6)

            # Word-wrap description into up to 2 lines
            words = desc.split()
            line1, line2 = "", ""
            for w in words:
                candidate = (line1 + " " + w).strip()
                if len(candidate) <= max_chars:
                    line1 = candidate
                elif not line2:
                    candidate2 = (line2 + " " + w).strip()
                    if len(candidate2) <= max_chars:
                        line2 = candidate2
                    else:
                        break
                else:
                    break

            c.setFillColor(HexColor("#b8d0f8"))
            c.setFont("Helvetica", 7)
            desc_y = collar_bottom - 14 if not line2 else collar_bottom - 11
            c.drawCentredString(cx, desc_y, line1)
            if line2:
                c.drawCentredString(cx, desc_y - 9, line2)

            # Diameter at bottom of body, with the parts that need it beneath — the
            # operator can see at a glance what a missing tool would cost.
            c.setFillColor(HexColor("#ffffff"))
            c.setFont("Helvetica-Bold", 9)
            c.drawCentredString(cx, body_bottom + 15, dia_str)
            c.setFillColor(HexColor("#b8d0f8"))
            c.setFont("Helvetica", 6.5)
            names = ", ".join(sorted({_strip_ext(u) for u in (t.get("parts") or [])}))
            limit = int(bw / 3.2)
            c.drawCentredString(cx, body_bottom + 5,
                                names if len(names) <= limit else names[:limit - 1] + "…")
        else:
            c.setFillColor(HexColor("#999999"))
            c.setFont("Helvetica", 8)
            c.drawCentredString(cx, body_bottom + _ATC_BODY_H / 2 - 4, "empty")


# ── placement table ───────────────────────────────────────────────────────────

_COLS = [
    ("#",          24),
    ("Slot",       46),
    ("Part",      210),
    ("Blank (in)", 90),
    ("Pos",        64),
    ("Tools",     186),
]


def _table_header(c, x, y, pw) -> float:
    if _LOGO_PATH.exists():
        logo_h = 20
        logo_w = logo_h * 2.49
        c.drawImage(ImageReader(str(_LOGO_PATH)),
                    pw - MARGIN - logo_w, y - logo_h,
                    width=logo_w, height=logo_h,
                    preserveAspectRatio=True, mask="auto")
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y, "Placement table")
    y -= 16
    cx = x
    c.setFont("Helvetica-Bold", 8)
    for title, width in _COLS:
        c.drawString(cx, y, title)
        cx += width
    y -= 4
    c.setStrokeColor(HexColor("#999999"))
    c.setLineWidth(0.5)
    c.line(x, y, pw - MARGIN, y)
    return y - _ROW_H + 4


def _draw_table(c, meta, parts, pw, ph, top_y) -> None:
    x = MARGIN
    y = _table_header(c, x, top_y, pw)

    for part in parts:
        if y < MARGIN + 40:
            c.showPage()
            y = _table_header(c, x, ph - MARGIN, pw)

        c.setFillColor(HexColor(part["color"]))
        c.rect(x, y - 1, 7, 7, fill=1, stroke=0)

        sx, sy = part["size_mm"]
        tlist = _tools_str(part.get("tools"))
        cells = [
            str(part["index"]),
            part["label"],
            _strip_ext(part["name"]),
            f'{sx/25.4:.2f}×{sy/25.4:.2f}"',
            f'{part["slot_inches"]:.1f}"',
            tlist,
        ]
        c.setFillColor(black)
        c.setFont("Helvetica", 8)
        cx = x
        for (title, width), text in zip(_COLS, cells):
            avail = int(width / 4.2)
            c.drawString(cx + (10 if title == "#" else 0), y,
                         text if len(text) <= avail else text[: avail - 1] + "…")
            cx += width
        y -= _ROW_H

    # Summary footer
    y -= 8
    seq = " → ".join(meta.get("tool_sequence") or []) or "—"
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(HexColor("#333333"))
    summary = (
        f"Tool sequence: {seq}    Tool changes: {meta.get('tool_changes', 0)}"
        f"    Parts placed: {meta.get('parts_count', 0)}"
    )
    if meta.get("runtime"):
        summary += f"    Estimated runtime: {meta['runtime']}"
    c.drawString(x, y, summary)
