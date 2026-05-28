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


def _machine_y(slot_inches: float, edge_margin_in: float = 0.0) -> float:
    return (120.0 - slot_inches - edge_margin_in) * 25.4


def blank_rect(placed: PlacedPart, rail_width_mm: float, bed_x_mm: float,
               edge_margin_in: float = 0.0) -> Rect:
    """Blank boundary in machine coordinates."""
    p = placed.part
    my = _machine_y(placed.slot_inches, edge_margin_in)
    if placed.rail == "A":
        # vcarve_y_span = dim across bed = machine X extent
        # vcarve_x_span = dim along rail = machine Y extent
        # slot mark (my) = HIGH machine-Y edge
        return Rect(
            min_x=rail_width_mm,
            max_x=rail_width_mm + p.vcarve_y_span,
            min_y=my - p.vcarve_x_span,
            max_y=my,
        )
    else:  # B rail
        min_x = bed_x_mm - rail_width_mm - p.vcarve_y_span
        return Rect(
            min_x=min_x,
            max_x=min_x + p.vcarve_y_span,
            min_y=my,
            max_y=my + p.vcarve_x_span,
        )


def toolpath_rect(placed: PlacedPart, rail_width_mm: float, bed_x_mm: float,
                  tool_radius_mm: float = 0.0, edge_margin_in: float = 0.0) -> Rect:
    """
    Toolpath extents in machine coordinates, optionally expanded by tool_radius_mm
    on all four sides to account for the physical width of the cutter.
    VCarve X → machine Y,  VCarve Y → machine X
    A rail: machX = rail_w + vcarve_Y,   machY = slot_mark - vcarve_X
    B rail: machX = (bed_x-rail_w) - vcarve_Y,  machY = (slot_mark + vcarve_x_span) - vcarve_X
    """
    p = placed.part
    my = _machine_y(placed.slot_inches, edge_margin_in)
    if placed.rail == "A":
        r = Rect(
            min_x=rail_width_mm + p.min_vy,
            max_x=rail_width_mm + p.max_vy,
            min_y=my - p.max_vx,
            max_y=my - p.min_vx,
        )
    else:  # B rail
        far_x = bed_x_mm - rail_width_mm
        r = Rect(
            min_x=far_x - p.max_vy,
            max_x=far_x - p.min_vy,
            min_y=my + p.vcarve_x_span - p.max_vx,
            max_y=my + p.vcarve_x_span - p.min_vx,
        )
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


def rects_overlap(a: Rect, b: Rect) -> bool:
    """True when two rects share interior area. Touching edges are not a collision."""
    return not (
        a.max_x <= b.min_x or a.min_x >= b.max_x or
        a.max_y <= b.min_y or a.min_y >= b.max_y
    )


def check_placement(
    new_placed: PlacedPart,
    existing: List[PlacedPart],
    rail_width_mm: float,
    bed_x_mm: float,
    edge_margin_in: float = 0.0,
) -> CollisionResult:
    """
    Check whether new_placed collides with any already-placed part.

    A collision occurs when:
      - new part's toolpath extents (expanded by its largest tool radius) overlap
        an existing part's blank boundary, OR
      - an existing part's toolpath extents (expanded by its largest tool radius)
        overlap the new part's blank boundary.

    Toolpath extents overlapping each other is NOT a collision — the cutter can
    swing freely in clearance zones between blanks.
    """
    new_radius = _max_tool_radius(new_placed)
    new_tool_str = _largest_tool_str(new_placed)
    new_tp = toolpath_rect(new_placed, rail_width_mm, bed_x_mm, new_radius, edge_margin_in)
    new_blank = blank_rect(new_placed, rail_width_mm, bed_x_mm, edge_margin_in)
    new_slot = slot_label(new_placed.rail, new_placed.slot_inches)

    for placed in existing:
        ex_radius = _max_tool_radius(placed)
        ex_tool_str = _largest_tool_str(placed)
        ex_tp = toolpath_rect(placed, rail_width_mm, bed_x_mm, ex_radius, edge_margin_in)
        ex_blank = blank_rect(placed, rail_width_mm, bed_x_mm, edge_margin_in)
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
