from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional

from gcode_parser import GcodePart


class Rect(NamedTuple):
    min_x: float
    max_x: float
    min_y: float
    max_y: float


@dataclass
class PlacedPart:
    part: GcodePart
    rail: str           # 'A' or 'B'
    slot_inches: float
    instance_id: str
    # `T#` → declared diameter in inches, resolved through the identity library. The
    # library is the **sole** diameter authority (spec §3.5.2): a Fusion header's `D=`
    # is the tool's nominal size, not its widest cutting point, and three library files
    # yield no parseable diameter at all. When this is set it is used and the parsed
    # file is not consulted; None means nothing resolved it, which only happens off the
    # placement path (the audit sweep), never for a part on the bed.
    tool_diameters: Optional[Dict[str, float]] = None


@dataclass
class CollisionResult:
    collides: bool
    message: str = ""
    conflicting_instance_id: str = ""


def slot_label(rail: str, slot_inches: float) -> str:
    n = int(slot_inches) if slot_inches == int(slot_inches) else slot_inches
    return f"{rail}{n}"


MM_PER_IN = 25.4

# ── per-rail geometry ─────────────────────────────────────────────────────────
#
# The two rails are independent fixturing systems at opposite ends of the
# machine, loaded separately by the operator. They are NOT two views of one
# formula: measured on the SS2, slot numbers run toward machine Y max on one
# rail and toward Y min on the other, so each rail carries its own datum and
# direction and either can be re-measured without disturbing the other.
#
#   x_mm       machine X of the rail corner the blank registers against
#   slot0_y_mm machine Y of the slot-0 edge (the blank's datum edge)
#   slot_dir   +1 if increasing slot number moves toward machine Y max, else -1
#   x_dir      +1 if the blank extends from x_mm toward machine X max, else -1
#
# Every value below is measured from the machine — X to the rail corner, Y to the
# slot edge — at two slots per rail, so both the datum and the direction are
# observed rather than assumed:
#
#     A0 (134.628, 3034.700)    A13 (134.628, 2704.500)   -> -330.200 mm/13"
#     B0 (1534.160,   88.300)   B13 (1534.160,  418.500)  -> +330.200 mm/13"
#
# tests/test_collision.py::test_rail_defaults_match_measured_machine_positions
# pins these four readouts; re-measure and update both together.
RAIL_DEFAULTS = {
    "A": {"x_mm":  134.628, "slot0_y_mm": 3034.700, "slot_dir": -1, "x_dir":  1},
    "B": {"x_mm": 1534.160, "slot0_y_mm":   88.300, "slot_dir":  1, "x_dir": -1},
}


# ── machine envelope ──────────────────────────────────────────────────────────
#
# The outer extents of the MACHINABLE SURFACE, measured off the machine layout
# drawing on 2026-08-17 and read in inches:
#
#     X 2.421 → 63.248     (60.827" of usable width)
#     Y 0.984 → 121.969    (120.985" of usable length)
#
# These are NOT bed_x_mm / bed_y_mm. Those drive canvas and PDF extents and the
# utilisation figure only; these gate the cut path.
#
# Programmed coordinates are TOOL CENTRE with no cutter compensation — the posts
# emit G40 and computer-compensated toolpaths — so the cutting edge stands one
# tool radius outside every number in the file. What that implies differs per
# axis, because the two axes fail differently:
#
#   X is an OBSTRUCTION axis. There is a hard stop just outside each end of X
#   travel, so the cutter itself must stay inside the surface: the footprint is
#   INFLATED by the part's largest tool radius and then held inside the limit
#   less the edge margin. Overrunning X is a crash.
#
#   Y is an OPEN axis. Nothing obstructs either end, so the tool is allowed to
#   hang over the edge of the surface — y_max + radius and y_min - radius are
#   both acceptable. Y is therefore checked on TOOL CENTRE with no edge margin,
#   which is what keeps A rail slot 0 (datum Y 3034.700) usable.
#
#   Y_FLOOR_MM is the one hard bound on the open axis: the machine does not go
#   below Y 0 at all, so the cutter EDGE has to stay above it. With y_min at
#   24.99 this only bites for a cutter over ~2" in diameter — it is a backstop,
#   not the working limit.
#
# Z is still unmeasured and ships as None; an axis with None limits is skipped.
#
# Superseded reading, kept so it is not re-derived: the park block's
# `G00 G53 X0 Y3048` is the only machine-frame coordinate in the output, and both
# its numbers were once read as limits. Y 3048 is a position the machine REACHES
# (a lower bound on travel, and exactly 120.000" — a design round number); the
# real Y surface runs to 3098.013 and travel runs past that to the tool changer.
# X 0 → 1524 contradicted the measured B rail corner at 1534.160 that every
# B-rail part cuts inboard from; the real X surface reaches 1606.499.
#
# tests/test_collision.py::test_travel_defaults_match_the_measured_machine_surface
# pins the readouts. Re-measure and update the test, this comment and config.json
# together.
TRAVEL_DEFAULTS = {
    "x_min":   61.4934, "x_max": 1606.4992,   # 2.421" → 63.248"
    "y_min":   24.9936, "y_max": 3098.0126,   # 0.984" → 121.969"
    "z_min": None,      "z_max": None,        # unmeasured — issue #19
}

# The machine does not travel below Y 0. Unlike the surface bounds above this is
# an axis end, so the cutting edge — not just the tool centre — must clear it.
Y_FLOOR_MM = 0.0

EDGE_MARGIN_DEFAULT_IN = 0.5


def travel_limits(advanced: Optional[dict] = None) -> dict:
    """Axis travel limits in machine mm, with config overrides. None = unchecked."""
    limits = dict(TRAVEL_DEFAULTS)
    for key, val in ((advanced or {}).get("machine_travel") or {}).items():
        if key in limits:
            limits[key] = None if val is None else float(val)
    return limits


def edge_margin_mm(advanced: Optional[dict] = None) -> float:
    """Keep-out from each travel limit, in mm."""
    val = (advanced or {}).get("edge_margin_in")
    return float(EDGE_MARGIN_DEFAULT_IN if val is None else val) * MM_PER_IN


def rail_geom(rail: str, rails: Optional[dict] = None) -> dict:
    """Geometry for one rail, with any config overrides layered over the defaults."""
    if rail not in RAIL_DEFAULTS:
        raise ValueError(f"Unknown rail {rail!r}; expected one of {sorted(RAIL_DEFAULTS)}")
    geom = dict(RAIL_DEFAULTS[rail])
    if rails:
        geom.update(rails.get(rail) or {})
    return geom


def slot_mark_y(rail: str, slot_inches: float, rails: Optional[dict] = None) -> float:
    """Machine Y of a slot's datum edge. Single source of truth for slot position.

    collision.py, gcode_generator.py, app.py and static/bed.js must all derive slot
    positions from this, or a part will be simulated somewhere it does not cut.
    """
    g = rail_geom(rail, rails)
    return float(g["slot0_y_mm"]) + float(g["slot_dir"]) * float(slot_inches) * MM_PER_IN


def _span_from(datum: float, direction: float, span: float) -> tuple:
    """Low/high edge of an extent starting at datum and running in `direction`."""
    far = datum + direction * span
    return (min(datum, far), max(datum, far))


def blank_rect(placed: PlacedPart, rails: Optional[dict] = None) -> Rect:
    """Blank boundary in machine coordinates.

    vcarve_y_span = dim across the bed  = machine X extent
    vcarve_x_span = dim along the rail  = machine Y extent
    """
    p = placed.part
    g = rail_geom(placed.rail, rails)
    min_x, max_x = _span_from(float(g["x_mm"]), float(g["x_dir"]), p.vcarve_y_span)
    my = slot_mark_y(placed.rail, placed.slot_inches, rails)
    min_y, max_y = _span_from(my, float(g["slot_dir"]), p.vcarve_x_span)
    return Rect(min_x=min_x, max_x=max_x, min_y=min_y, max_y=max_y)


def toolpath_rect(placed: PlacedPart, rails: Optional[dict] = None,
                  tool_radius_mm: float = 0.0) -> Rect:
    """
    Toolpath extents in machine coordinates, optionally expanded by tool_radius_mm
    on all four sides to account for the physical width of the cutter.

    VCarve X → machine Y,  VCarve Y → machine X:
        machine X = x_mm      + x_dir    * vcarve_Y
        machine Y = slot_mark + slot_dir * vcarve_X
    """
    p = placed.part
    g = rail_geom(placed.rail, rails)
    x0, xd = float(g["x_mm"]), float(g["x_dir"])
    my, sd = slot_mark_y(placed.rail, placed.slot_inches, rails), float(g["slot_dir"])

    xs = (x0 + xd * p.min_vy, x0 + xd * p.max_vy)
    ys = (my + sd * p.min_vx, my + sd * p.max_vx)
    r = Rect(min_x=min(xs), max_x=max(xs), min_y=min(ys), max_y=max(ys))
    if tool_radius_mm:
        r = Rect(
            min_x=r.min_x - tool_radius_mm,
            max_x=r.max_x + tool_radius_mm,
            min_y=r.min_y - tool_radius_mm,
            max_y=r.max_y + tool_radius_mm,
        )
    return r


def _tool_diameters(placed: PlacedPart) -> dict:
    """`T#` → diameter in inches, from the identity library when it resolved this part.

    The library is authoritative and the parsed header is not consulted at all when it
    is present — mixing the two would silently prefer whichever number happened to be
    larger, and the parsed one is wrong in the crash direction on every profile bit
    (`.25 Bowl Bit` posts a nominal 0.25 and is 0.75 across).
    """
    if placed.tool_diameters is not None:
        return dict(placed.tool_diameters)
    return {num: (info.get("diameter_inches") or 0.0)
            for num, info in placed.part.tools.items()}


def _max_tool_radius(placed: PlacedPart) -> float:
    """Largest tool radius in mm across all tools this part cuts with."""
    best = 0.0
    for dia in _tool_diameters(placed).values():
        r = (dia or 0) * 25.4 / 2
        if r > best:
            best = r
    return best


def _largest_tool_str(placed: PlacedPart) -> str:
    """'T2 (0.5\" dia) ' label for the largest-diameter tool, or '' if none defined."""
    best_num, best_dia = "", 0.0
    for num, dia in _tool_diameters(placed).items():
        dia = dia or 0
        if dia > best_dia:
            best_dia, best_num = dia, num
    if not best_dia:
        return ""
    return f"{best_num} ({best_dia:.3g}\" dia) "


def check_envelope(
    placed: PlacedPart,
    rails: Optional[dict] = None,
    advanced: Optional[dict] = None,
) -> CollisionResult:
    """
    Check one placement against the machinable surface.

    The two axes are checked differently, because they fail differently — see
    the TRAVEL_DEFAULTS comment:

      X  cutter edge (toolpath inflated by the largest tool radius) inside the
         limit less the edge margin. There is a hard stop out there.
      Y  tool centre inside the limit, no margin. Nothing obstructs either end,
         so overhang is fine — except that the edge must clear Y_FLOOR_MM.
    """
    limits = travel_limits(advanced)
    margin = edge_margin_mm(advanced)
    radius = _max_tool_radius(placed)
    centre = toolpath_rect(placed, rails)
    edge = toolpath_rect(placed, rails, radius)
    slot = slot_label(placed.rail, placed.slot_inches)
    tool = _largest_tool_str(placed) or "cutter "
    # Across the bed is fixed by the rail and the part, so no slot helps.
    hard_stop = ("There is a hard stop at each end of X travel, and this job "
                 f"keeps a {margin / MM_PER_IN:.2g}\" margin from it. Moving the "
                 "part along the rail will not help — this is how far it "
                 "reaches across the bed. Use a narrower part, the other rail, "
                 "or check that the file's origin is its bottom-left corner.")

    # X — the cutter itself has to fit, with the margin held back from the stop.
    lo, hi = limits["x_min"], limits["x_max"]
    if lo is not None and edge.min_x < lo + margin:
        return _envelope_result(placed, slot, tool, "X", edge.min_x, lo + margin, hard_stop)
    if hi is not None and edge.max_x > hi - margin:
        return _envelope_result(placed, slot, tool, "X", edge.max_x, hi - margin, hard_stop)

    # Y — tool centre against the surface; the tool may hang off either end.
    # This one IS a slot problem: the slot is what sets where the part sits.
    move = " Move the part to a slot further from the end of the bed."
    surface = ("The tool may hang over the end of the surface on Y, but its "
               "centre has to stay on it." + move)
    lo, hi = limits["y_min"], limits["y_max"]
    if lo is not None and centre.min_y < lo:
        return _envelope_result(placed, slot, tool, "Y", centre.min_y, lo, surface)
    if hi is not None and centre.max_y > hi:
        return _envelope_result(placed, slot, tool, "Y", centre.max_y, hi, surface)
    if edge.min_y < Y_FLOOR_MM:
        return _envelope_result(
            placed, slot, tool, "Y", edge.min_y, Y_FLOOR_MM,
            f"The machine does not travel below Y {Y_FLOOR_MM:.0f}." + move)

    return CollisionResult(collides=False)


def _envelope_result(placed: PlacedPart, slot: str, tool: str, axis: str,
                     reach: float, bound: float, why: str) -> CollisionResult:
    return CollisionResult(
        collides=True,
        message=(
            f"Cannot place {placed.part.filename} at slot {slot}: its {tool}"
            f"would reach machine {axis} {reach:.1f} mm, past the safe limit of "
            f"{bound:.1f} mm. {why}"
        ),
    )


def rects_overlap(a: Rect, b: Rect) -> bool:
    """True when two rects share interior area. Touching edges are not a collision."""
    return not (
        a.max_x <= b.min_x or a.min_x >= b.max_x or
        a.max_y <= b.min_y or a.min_y >= b.max_y
    )


def check_placement(
    new_placed: PlacedPart,
    existing: List[PlacedPart],
    rails: Optional[dict] = None,
    advanced: Optional[dict] = None,
) -> CollisionResult:
    """
    Check whether new_placed is legal: inside the machine envelope, and clear of
    every already-placed part.

    The envelope is checked first — a placement the machine cannot reach is not
    made acceptable by having the bed to itself.

    A collision occurs when:
      - new part's toolpath extents (expanded by its largest tool radius) overlap
        an existing part's blank boundary, OR
      - an existing part's toolpath extents (expanded by its largest tool radius)
        overlap the new part's blank boundary.

    Toolpath extents overlapping each other is NOT a collision — the cutter can
    swing freely in clearance zones between blanks.

    Both rails are checked against each other, not just parts sharing a rail:
    everything is compared in machine coordinates, so an A-rail part and a
    B-rail part that would physically interfere are caught.
    """
    envelope = check_envelope(new_placed, rails, advanced)
    if envelope.collides:
        return envelope

    new_radius = _max_tool_radius(new_placed)
    new_tool_str = _largest_tool_str(new_placed)
    new_tp = toolpath_rect(new_placed, rails, new_radius)
    new_blank = blank_rect(new_placed, rails)
    new_slot = slot_label(new_placed.rail, new_placed.slot_inches)

    for placed in existing:
        ex_radius = _max_tool_radius(placed)
        ex_tool_str = _largest_tool_str(placed)
        ex_tp = toolpath_rect(placed, rails, ex_radius)
        ex_blank = blank_rect(placed, rails)
        ex_slot = slot_label(placed.rail, placed.slot_inches)

        if rects_overlap(new_tp, ex_blank):
            return CollisionResult(
                collides=True,
                message=(
                    f"Cannot place {new_placed.part.filename} at slot {new_slot}: "
                    f"its {new_tool_str}toolpath would extend into the blank area of "
                    f"{placed.part.filename} at slot {ex_slot}. "
                    "Move one of the parts to a slot with more clearance."
                ),
                conflicting_instance_id=placed.instance_id,
            )

        if rects_overlap(ex_tp, new_blank):
            return CollisionResult(
                collides=True,
                message=(
                    f"Cannot place {new_placed.part.filename} at slot {new_slot}: "
                    f"the {ex_tool_str}toolpath of {placed.part.filename} at slot {ex_slot} "
                    f"would extend into the new part's blank area. "
                    "Move one of the parts to a slot with more clearance."
                ),
                conflicting_instance_id=placed.instance_id,
            )

    return CollisionResult(collides=False)
