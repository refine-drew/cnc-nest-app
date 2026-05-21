import pytest
from gcode_parser import GcodePart, ZValidation
from collision import (
    PlacedPart, Rect,
    blank_rect, toolpath_rect, rects_overlap, check_placement, slot_label,
)

RAIL_W = 82.55
BED_X = 1524.0


def make_part(blank_w, blank_h, min_x, max_x, min_y, max_y, filename="part.nc"):
    """Minimal GcodePart with known blank and toolpath extents."""
    return GcodePart(
        filename=filename,
        blank_width=blank_w,
        blank_height=blank_h,
        material_thickness=19.05,
        tools={},
        min_x=min_x,
        max_x=max_x,
        min_y=min_y,
        max_y=max_y,
        raw_lines=[],
        z_validation=ZValidation(status="ok"),
    )


def placed(part, rail, slot_inches, instance_id="i1"):
    return PlacedPart(part=part, rail=rail, slot_inches=slot_inches, instance_id=instance_id)


# --- slot_label ---

def test_slot_label_integer():
    assert slot_label("A", 39.0) == "A39"

def test_slot_label_fractional():
    assert slot_label("B", 19.5) == "B19.5"


# --- blank_rect ---

def test_blank_rect_a_rail():
    part = make_part(100, 200, 0, 100, 0, 200)
    p = placed(part, "A", 39)
    r = blank_rect(p, RAIL_W, BED_X)

    machine_y = (120 - 39) * 25.4  # 2057.4
    assert r.min_x == pytest.approx(RAIL_W)
    assert r.max_x == pytest.approx(RAIL_W + 100)
    assert r.min_y == pytest.approx(machine_y)
    assert r.max_y == pytest.approx(machine_y + 200)


def test_blank_rect_b_rail():
    part = make_part(100, 200, 0, 100, 0, 200)
    p = placed(part, "B", 39)
    r = blank_rect(p, RAIL_W, BED_X)

    machine_y = (120 - 39) * 25.4
    expected_min_x = BED_X - RAIL_W - 100
    assert r.min_x == pytest.approx(expected_min_x)
    assert r.max_x == pytest.approx(expected_min_x + 100)
    assert r.min_y == pytest.approx(machine_y)
    assert r.max_y == pytest.approx(machine_y + 200)


# --- toolpath_rect ---

def test_toolpath_rect_a_rail_same_as_blank_when_extents_equal():
    part = make_part(100, 100, 0, 100, 0, 100)
    p = placed(part, "A", 39)

    br = blank_rect(p, RAIL_W, BED_X)
    tr = toolpath_rect(p, RAIL_W, BED_X)
    assert tr == br


def test_toolpath_rect_a_rail_extents_beyond_blank():
    # Toolpath extends 10mm beyond blank on all sides
    part = make_part(100, 100, -10, 110, -10, 110)
    p = placed(part, "A", 39)
    tr = toolpath_rect(p, RAIL_W, BED_X)

    machine_y = (120 - 39) * 25.4
    assert tr.min_x == pytest.approx(RAIL_W - 10)
    assert tr.max_x == pytest.approx(RAIL_W + 110)
    assert tr.min_y == pytest.approx(machine_y - 10)
    assert tr.max_y == pytest.approx(machine_y + 110)


def test_toolpath_rect_b_rail_rotation():
    # B rail: 180° rotation mirrors the toolpath extents
    # Part: blank 100x100, toolpath extends 10mm at high-Y (file) edge
    part = make_part(100, 100, 0, 100, 0, 110)
    p = placed(part, "B", 39)
    tr = toolpath_rect(p, RAIL_W, BED_X)

    machine_y = (120 - 39) * 25.4
    machine_x = BED_X - RAIL_W - 100
    # After 180° rotation, high-Y extension in file coords → low-Y extension in machine coords
    assert tr.min_x == pytest.approx(machine_x)
    assert tr.max_x == pytest.approx(machine_x + 100)
    assert tr.min_y == pytest.approx(machine_y - 10)   # extension now at low Y
    assert tr.max_y == pytest.approx(machine_y + 100)


def test_toolpath_rect_b_rail_x_rotation():
    # B rail X: high-X in file → low-X in machine
    part = make_part(100, 100, 0, 110, 0, 100)
    p = placed(part, "B", 39)
    tr = toolpath_rect(p, RAIL_W, BED_X)

    machine_x = BED_X - RAIL_W - 100
    assert tr.min_x == pytest.approx(machine_x - 10)  # extension flipped to low X
    assert tr.max_x == pytest.approx(machine_x + 100)


# --- verified spec example: asymmetric 300×400mm part, notch at file X=20 Y=380 ---

def test_spec_example_a_rail_notch_position():
    """
    Spec: A rail at A36 (machine_x=82.55, machine_y=2133.6)
    Notch in machine coords: X=102.55, Y=2513.6
    """
    slot_inches = 120 - 2133.6 / 25.4  # ≈ 36.0
    part = make_part(300, 400, 0, 300, 0, 400)
    p = placed(part, "A", slot_inches)
    tr = toolpath_rect(p, RAIL_W, BED_X)

    # notch at file (20, 380) maps to machine coords via direct offset
    notch_machine_x = RAIL_W + 20
    notch_machine_y = _machine_y(slot_inches) + 380
    assert notch_machine_x == pytest.approx(102.55, abs=0.01)
    assert notch_machine_y == pytest.approx(2513.6, abs=0.1)


def test_spec_example_b_rail_notch_position():
    """
    Spec: B rail at B36 (machine_x=1141.45, machine_y=2133.6)
    Rotation: new_X = 1441.45 - file_x,  new_Y = 2533.6 - file_y
    Notch in machine coords: X=1421.45, Y=2153.6
    """
    slot_inches = 120 - 2133.6 / 25.4  # ≈ 36.0
    part = make_part(300, 400, 0, 300, 0, 400)
    p = placed(part, "B", slot_inches)

    my = _machine_y(slot_inches)        # 2133.6
    machine_x = BED_X - RAIL_W - 300   # 1141.45

    # Transform notch at file (20, 380)
    notch_machine_x = machine_x + 300 - 20   # 1421.45
    notch_machine_y = my + 400 - 380          # 2153.6

    assert notch_machine_x == pytest.approx(1421.45, abs=0.01)
    assert notch_machine_y == pytest.approx(2153.6, abs=0.1)


def _machine_y(slot_inches):
    return (120.0 - slot_inches) * 25.4


# --- rects_overlap ---

def test_rects_overlap_clear():
    a = Rect(0, 10, 0, 10)
    b = Rect(20, 30, 20, 30)
    assert not rects_overlap(a, b)


def test_rects_overlap_touching_x_not_collision():
    a = Rect(0, 10, 0, 10)
    b = Rect(10, 20, 0, 10)
    assert not rects_overlap(a, b)


def test_rects_overlap_touching_y_not_collision():
    a = Rect(0, 10, 0, 10)
    b = Rect(0, 10, 10, 20)
    assert not rects_overlap(a, b)


def test_rects_overlap_partial_overlap():
    a = Rect(0, 15, 0, 15)
    b = Rect(10, 20, 10, 20)
    assert rects_overlap(a, b)


def test_rects_overlap_contained():
    a = Rect(0, 100, 0, 100)
    b = Rect(10, 90, 10, 90)
    assert rects_overlap(a, b)


# --- check_placement ---

def test_no_collision_when_no_existing_parts():
    part = make_part(100, 100, 0, 100, 0, 100)
    p = placed(part, "A", 39)
    result = check_placement(p, [], RAIL_W, BED_X)
    assert not result.collides


def test_no_collision_parts_far_apart():
    part_a = make_part(100, 100, 0, 100, 0, 100, "a.nc")
    part_b = make_part(100, 100, 0, 100, 0, 100, "b.nc")
    existing = [placed(part_a, "A", 0, "i1")]
    new = placed(part_b, "A", 117, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X)
    assert not result.collides


def test_collision_new_toolpath_into_existing_blank():
    # Part A at slot 39: blank Y = [2057.4, 2157.4]
    # Part B at slot 52: machine_y = (120-52)*25.4 = 1727.2, blank Y = [1727.2, 1827.2]
    # Give part B a toolpath that extends well past its blank into part A's blank
    part_a = make_part(200, 100, 0, 200, 0, 100, "a.nc")
    # Part B toolpath extends 500mm at high-Y — into A's blank territory
    part_b = make_part(200, 100, 0, 200, 0, 600, "b.nc")

    existing = [placed(part_a, "A", 39, "i1")]
    new = placed(part_b, "A", 52, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X)
    assert result.collides
    assert result.conflicting_instance_id == "i1"
    assert "b.nc" in result.message
    assert "a.nc" in result.message


def test_collision_existing_toolpath_into_new_blank():
    # Existing part has an oversized toolpath that reaches into where we're trying to place
    part_existing = make_part(200, 100, 0, 200, 0, 600, "existing.nc")
    part_new = make_part(200, 100, 0, 200, 0, 100, "new.nc")

    existing = [placed(part_existing, "A", 52, "i1")]
    new = placed(part_new, "A", 39, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X)
    assert result.collides
    assert "existing.nc" in result.message


def test_no_collision_toolpath_vs_toolpath():
    """
    Two parts whose toolpath extents overlap each other but do NOT reach each
    other's blank boundary must NOT be flagged as a collision.
    """
    # Both parts at same X strip, but their blanks are 200mm apart
    # Each has toolpath that extends 50mm beyond blank — not enough to reach the other's blank
    part_a = make_part(100, 100, 0, 100, 0, 150, "a.nc")  # toolpath extends 50mm high
    part_b = make_part(100, 100, 0, 100, 0, 100, "b.nc")

    # Slot 39: machine_y = 2057.4, blank Y = [2057.4, 2157.4], toolpath Y top = 2207.4
    # Slot 26: machine_y = (120-26)*25.4 = 2387.6, blank Y = [2387.6, 2487.6]
    # Toolpath of A (top=2207.4) does NOT reach blank of B (bottom=2387.6)
    existing = [placed(part_a, "A", 39, "i1")]
    new = placed(part_b, "A", 26, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X)
    assert not result.collides


def test_no_collision_a_and_b_rail_same_slot():
    """Parts on opposite rails at the same slot don't share X space."""
    part = make_part(100, 100, 0, 100, 0, 100)
    existing = [placed(part, "A", 39, "i1")]
    new = placed(part, "B", 39, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X)
    assert not result.collides


def test_collision_message_contains_slot_labels():
    part_a = make_part(200, 100, 0, 200, 0, 600, "a.nc")
    part_b = make_part(200, 100, 0, 200, 0, 100, "b.nc")

    existing = [placed(part_a, "A", 52, "i1")]
    new = placed(part_b, "A", 39, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X)
    assert "A39" in result.message
    assert "A52" in result.message
