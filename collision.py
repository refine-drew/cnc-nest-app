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


def _machine_y(slot_inches: float) -> float:
    return (120.0 - slot_inches) * 25.4


def blank_rect(placed: PlacedPart, rail_width_mm: float, bed_x_mm: float) -> Rect:
    """Blank boundary in machine coordinates."""
    p = placed.part
    my = _machine_y(placed.slot_inches)
    if placed.rail == "A":
        return Rect(
            min_x=rail_width_mm,
            max_x=rail_width_mm + p.blank_width,
            min_y=my,
            max_y=my + p.blank_height,
        )
    else:  # B rail
        min_x = bed_x_mm - rail_width_mm - p.blank_width
        return Rect(
            min_x=min_x,
            max_x=min_x + p.blank_width,
            min_y=my,
            max_y=my + p.blank_height,
        )


def toolpath_rect(placed: PlacedPart, rail_width_mm: float, bed_x_mm: float) -> Rect:
    """
    Toolpath extents in machine coordinates.

    A rail: direct offset — file coords shift by (rail_width_mm, machine_y).
    B rail: 180° rotation — file coords are mirrored through blank center.
      new_X = (machine_x + blank_width) - file_x
      new_Y = (machine_y + blank_height) - file_y
    """
    p = placed.part
    my = _machine_y(placed.slot_inches)
    if placed.rail == "A":
        return Rect(
            min_x=rail_width_mm + p.min_x,
            max_x=rail_width_mm + p.max_x,
            min_y=my + p.min_y,
            max_y=my + p.max_y,
        )
    else:  # B rail
        machine_x = bed_x_mm - rail_width_mm - p.blank_width
        return Rect(
            min_x=machine_x + p.blank_width - p.max_x,
            max_x=machine_x + p.blank_width - p.min_x,
            min_y=my + p.blank_height - p.max_y,
            max_y=my + p.blank_height - p.min_y,
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
    rail_width_mm: float,
    bed_x_mm: float,
) -> CollisionResult:
    """
    Check whether new_placed collides with any already-placed part.

    A collision occurs when:
      - new part's toolpath extents overlap an existing part's blank boundary, OR
      - an existing part's toolpath extents overlap the new part's blank boundary.

    Toolpath extents overlapping each other is NOT a collision — the cutter can
    swing freely in clearance zones between blanks.
    """
    new_tp = toolpath_rect(new_placed, rail_width_mm, bed_x_mm)
    new_blank = blank_rect(new_placed, rail_width_mm, bed_x_mm)
    new_slot = slot_label(new_placed.rail, new_placed.slot_inches)

    for placed in existing:
        ex_tp = toolpath_rect(placed, rail_width_mm, bed_x_mm)
        ex_blank = blank_rect(placed, rail_width_mm, bed_x_mm)
        ex_slot = slot_label(placed.rail, placed.slot_inches)

        if rects_overlap(new_tp, ex_blank):
            return CollisionResult(
                collides=True,
                message=(
                    f"Cannot place {new_placed.part.filename} at slot {new_slot}: "
                    f"its toolpath would extend into the blank area of "
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
                    f"the toolpath of {placed.part.filename} at slot {ex_slot} "
                    f"would extend into the new part's blank area. "
                    "Move one of the parts to a slot with more clearance."
                ),
                conflicting_instance_id=placed.instance_id,
            )

    return CollisionResult(collides=False)
