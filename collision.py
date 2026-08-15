from dataclasses import dataclass, field
from typing import List, NamedTuple, Optional

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
# Programmed coordinates are TOOL CENTRE with no cutter compensation — the posts
# emit G40 and computer-compensated toolpaths — so the cutting edge stands one
# tool radius outside every number in the file, and the gantry and dust boot are
# wider still. The envelope check therefore compares the toolpath footprint
# INFLATED by the part's largest tool radius against the travel limit pulled in
# by an edge margin, and rejects at placement time rather than at the control.
#
# These are NOT bed_x_mm / bed_y_mm. Those drive canvas and PDF extents and the
# utilisation figure only; these are the axis travel limits in machine
# coordinates and they gate the cut path.
#
# NONE OF THESE LIMITS ARE MEASURED, so every one is None and the check is
# dormant — an axis with None limits is skipped. See issue #19.
#
# The park block ends `G00 G53 X0 Y3048`, and the two numbers in it are the only
# machine-frame coordinates anywhere in the output. Both were read as limits
# once. Both readings were wrong:
#
#   Y 3048 is a position the machine REACHES, which makes it a lower bound on Y
#   travel, not the limit. It is also exactly 120.000" — the nominal bed length,
#   a design round number rather than an axis end. Real Y travel runs well past
#   it, because the tool changer sits beyond that end of the rail. Asserting
#   3048 as the limit put A rail slot 0 (datum Y 3034.700) inside the edge
#   margin and made the slot unusable.
#
#   X 0 → 1524 (60") contradicts the measured B rail corner at X 1534.160, which
#   every B-rail part cuts inboard from. The machine cannot both reach that
#   corner and stop at 1524.
#
# Guessing is harmful in both directions: too tight rejects placements that cut
# fine today, too loose licenses a real overtravel. Fill in
# advanced.machine_travel once the limits are read off the machine and each axis
# starts checking itself.
TRAVEL_DEFAULTS = {
    "x_min": None, "x_max": None,
    "y_min": None, "y_max": None,
    "z_min": None, "z_max": None,
}

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


def _max_tool_radius(placed: PlacedPart) -> float:
    """Largest tool radius in mm across all tools defined in the part."""
    best = 0.0
    for info in placed.part.tools.values():
        r = (info.get("diameter_inches") or 0) * 25.4 / 2
        if r > best:
            best = r
    return best


def _largest_tool_str(placed: PlacedPart) -> str:
    """'T2 (0.5\" dia) ' label for the largest-diameter tool, or '' if none defined."""
    best_num, best_dia = "", 0.0
    for num, info in placed.part.tools.items():
        dia = info.get("diameter_inches") or 0
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
    Check one placement against the machine travel limits, less the edge margin.

    The footprint measured is the toolpath inflated by the part's largest tool
    radius, not the tool-centre extents: the file's numbers are tool centre, so
    the cutting edge already stands a radius outside them.
    """
    limits = travel_limits(advanced)
    margin = edge_margin_mm(advanced)
    r = toolpath_rect(placed, rails, _max_tool_radius(placed))
    slot = slot_label(placed.rail, placed.slot_inches)
    tool = _largest_tool_str(placed) or "cutter "

    for axis, lo_key, hi_key, reach_lo, reach_hi in (
        ("X", "x_min", "x_max", r.min_x, r.max_x),
        ("Y", "y_min", "y_max", r.min_y, r.max_y),
    ):
        lo, hi = limits[lo_key], limits[hi_key]
        if lo is not None and reach_lo < lo + margin:
            return _envelope_result(placed, slot, tool, axis, reach_lo, lo + margin, lo, margin)
        if hi is not None and reach_hi > hi - margin:
            return _envelope_result(placed, slot, tool, axis, reach_hi, hi - margin, hi, margin)

    return CollisionResult(collides=False)


def _envelope_result(placed: PlacedPart, slot: str, tool: str, axis: str,
                     reach: float, keepout: float, limit: float,
                     margin: float) -> CollisionResult:
    return CollisionResult(
        collides=True,
        message=(
            f"Cannot place {placed.part.filename} at slot {slot}: its {tool}"
            f"would reach machine {axis} {reach:.1f} mm, past the safe limit of "
            f"{keepout:.1f} mm. The machine stops at {axis} {limit:.1f} mm and this "
            f"job keeps a {margin / MM_PER_IN:.2g}\" margin. "
            "Move the part to a slot further from the end of the bed."
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
