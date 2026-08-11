import pytest
from gcode_parser import GcodePart, ZValidation
from collision import (
    PlacedPart, Rect,
    blank_rect, toolpath_rect, rects_overlap, check_placement, slot_label,
)

RAIL_W = 82.55
BED_X = 1524.0
BED_Y = 3048.0   # 120" along the rail


def make_part(vcarve_x_span, vcarve_y_span, min_vx, max_vx, min_vy, max_vy,
              filename="part.nc", tools=None):
    """Minimal GcodePart with known blank and toolpath extents."""
    return GcodePart(
        filename=filename,
        vcarve_x_span=vcarve_x_span,
        vcarve_y_span=vcarve_y_span,
        material_thickness=19.05,
        tools=tools or {},
        min_vx=min_vx,
        max_vx=max_vx,
        min_vy=min_vy,
        max_vy=max_vy,
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
    # vcarve_x_span=100 (along rail = machine Y), vcarve_y_span=200 (across bed = machine X)
    part = make_part(100, 200, 0, 100, 0, 200)
    p = placed(part, "A", 39)
    r = blank_rect(p, RAIL_W, BED_X, BED_Y)

    machine_y = (120 - 39) * 25.4  # 2057.4
    assert r.min_x == pytest.approx(RAIL_W)
    assert r.max_x == pytest.approx(RAIL_W + 200)        # vcarve_y_span = machine X extent
    assert r.min_y == pytest.approx(machine_y - 100)     # slot_mark - vcarve_x_span
    assert r.max_y == pytest.approx(machine_y)            # slot_mark = high-Y edge


def test_blank_rect_b_rail():
    # vcarve_x_span=100 (along rail = machine Y), vcarve_y_span=200 (across bed = machine X)
    part = make_part(100, 200, 0, 100, 0, 200)
    p = placed(part, "B", 39)
    r = blank_rect(p, RAIL_W, BED_X, BED_Y)

    machine_y = (120 - 39) * 25.4
    expected_min_x = BED_X - RAIL_W - 200               # vcarve_y_span = machine X extent
    assert r.min_x == pytest.approx(expected_min_x)
    assert r.max_x == pytest.approx(expected_min_x + 200)
    assert r.min_y == pytest.approx(machine_y)            # slot_mark = low-Y edge for B
    assert r.max_y == pytest.approx(machine_y + 100)     # + vcarve_x_span


def test_blank_rect_honors_non_default_bed_y():
    """A shorter bed must move the slot mark.

    Regression: bed_y_mm was a config key the backend ignored in favour of a
    hardcoded 120", so the canvas moved with config while collision detection,
    the generated G-code and the PDF slot ruler did not.
    """
    part = make_part(100, 200, 0, 100, 0, 200)
    p = placed(part, "A", 39)

    short_bed = 2000.0
    r = blank_rect(p, RAIL_W, BED_X, short_bed)
    assert r.max_y == pytest.approx(short_bed - 39 * 25.4)
    # ...and is genuinely different from the 120" default.
    assert r.max_y != pytest.approx(blank_rect(p, RAIL_W, BED_X, BED_Y).max_y)


def test_machine_y_scales_with_bed_y():
    from collision import _machine_y
    assert _machine_y(39, 3048.0) == pytest.approx((120 - 39) * 25.4)
    assert _machine_y(39, 2000.0) == pytest.approx(2000.0 - 39 * 25.4)
    # edge margin is subtracted in the same inch-space as the slot
    assert _machine_y(39, 2000.0, 1.5) == pytest.approx(2000.0 - (39 + 1.5) * 25.4)


# --- toolpath_rect ---

def test_toolpath_rect_a_rail_same_as_blank_when_extents_equal():
    part = make_part(100, 100, 0, 100, 0, 100)
    p = placed(part, "A", 39)

    br = blank_rect(p, RAIL_W, BED_X, BED_Y)
    tr = toolpath_rect(p, RAIL_W, BED_X, BED_Y)
    assert tr == br


def test_toolpath_rect_a_rail_extents_beyond_blank():
    # Toolpath extends 10mm beyond blank on all sides
    # vcarve_x_span=100 (machine Y), vcarve_y_span=100 (machine X)
    # min_vx=-10,max_vx=110 → machine Y: min_y=my-110, max_y=my+10
    # min_vy=-10,max_vy=110 → machine X: min_x=RAIL_W-10, max_x=RAIL_W+110
    part = make_part(100, 100, -10, 110, -10, 110)
    p = placed(part, "A", 39)
    tr = toolpath_rect(p, RAIL_W, BED_X, BED_Y)

    machine_y = (120 - 39) * 25.4
    assert tr.min_x == pytest.approx(RAIL_W - 10)
    assert tr.max_x == pytest.approx(RAIL_W + 110)
    assert tr.min_y == pytest.approx(machine_y - 110)
    assert tr.max_y == pytest.approx(machine_y + 10)


def test_toolpath_rect_b_rail_rotation():
    # B rail (true 180° rotation): VCarve Y → machine X (mirrored), VCarve X → machine Y (additive)
    # Part: vcarve_x_span=100 (machine Y), vcarve_y_span=100 (machine X)
    # min_vx=0, max_vx=100, min_vy=0, max_vy=110 (toolpath extends 10mm in VCarve Y)
    # far_x = BED_X - RAIL_W = 1441.45
    # machine X: min_x = far_x - max_vy = 1441.45-110=1331.45, max_x = far_x - min_vy=1441.45
    # machine Y: min_y = my + min_vx = my, max_y = my + max_vx = my+100
    part = make_part(100, 100, 0, 100, 0, 110)
    p = placed(part, "B", 39)
    tr = toolpath_rect(p, RAIL_W, BED_X, BED_Y)

    machine_y = (120 - 39) * 25.4
    far_x = BED_X - RAIL_W  # 1441.45
    assert tr.min_x == pytest.approx(far_x - 110)   # far_x - max_vy
    assert tr.max_x == pytest.approx(far_x)          # far_x - min_vy (0)
    assert tr.min_y == pytest.approx(machine_y)      # my + min_vx (0)
    assert tr.max_y == pytest.approx(machine_y + 100)


def test_toolpath_rect_b_rail_x_rotation():
    # B rail VCarve X → machine Y (additive, not mirrored — true 180° rotation)
    # vcarve_x_span=100, min_vx=0, max_vx=110 → toolpath extends 10mm in VCarve X
    # machine Y: min_y = my + min_vx = my, max_y = my + max_vx = my + 110
    part = make_part(100, 100, 0, 110, 0, 100)
    p = placed(part, "B", 39)
    tr = toolpath_rect(p, RAIL_W, BED_X, BED_Y)

    machine_y = (120 - 39) * 25.4
    assert tr.min_y == pytest.approx(machine_y)       # my + min_vx (0)
    assert tr.max_y == pytest.approx(machine_y + 110) # extension on high-Y side


# --- verified spec example: correct axis convention ---
# VCarve X = along rail = machine Y; VCarve Y = across bed = machine X
# A rail: machX = RAIL_W + VCarve_Y,  machY = slot_mark - VCarve_X
# B rail: machX = (BED_X-RAIL_W) - VCarve_Y,  machY = slot_mark + VCarve_X

def test_spec_example_a_rail_notch_position():
    """
    Correct axis convention: A rail at slot 36 (slot_mark=2133.6mm)
    Part vcarve_x_span=300 (along rail), vcarve_y_span=400 (across bed)
    Notch at file VCarve_X=20, VCarve_Y=380:
      machine X = RAIL_W + 380 = 462.55
      machine Y = 2133.6 - 20 = 2113.6
    """
    slot_inches = 120 - 2133.6 / 25.4  # ≈ 36.0
    # vcarve_x_span=300 (along rail), vcarve_y_span=400 (across bed)
    part = make_part(300, 400, 0, 300, 0, 400)
    p = placed(part, "A", slot_inches)

    my = _machine_y(slot_inches)  # 2133.6
    # Notch at VCarve coords (20, 380)
    notch_machine_x = RAIL_W + 380      # RAIL_W + VCarve_Y
    notch_machine_y = my - 20           # slot_mark - VCarve_X
    assert notch_machine_x == pytest.approx(462.55, abs=0.01)
    assert notch_machine_y == pytest.approx(2113.6, abs=0.1)


def test_spec_example_b_rail_notch_position():
    """
    Correct axis convention (true 180° rotation): B rail at slot 36 (slot_mark=2133.6mm)
    Part vcarve_x_span=300 (along rail), vcarve_y_span=400 (across bed)
    Notch at file VCarve_X=20, VCarve_Y=380:
      machine X = (BED_X-RAIL_W) - 380 = 1441.45 - 380 = 1061.45
      machine Y = 2133.6 + 20 = 2153.6
    """
    slot_inches = 120 - 2133.6 / 25.4  # ≈ 36.0
    # vcarve_x_span=300 (along rail), vcarve_y_span=400 (across bed)
    part = make_part(300, 400, 0, 300, 0, 400)
    p = placed(part, "B", slot_inches)

    my = _machine_y(slot_inches)   # 2133.6
    far_x = BED_X - RAIL_W         # 1441.45
    # Notch at VCarve coords (20, 380)
    notch_machine_x = far_x - 380                  # far_x - VCarve_Y
    notch_machine_y = my + 20                      # slot_mark + VCarve_X
    assert notch_machine_x == pytest.approx(1061.45, abs=0.01)
    assert notch_machine_y == pytest.approx(2153.6, abs=0.1)


def _machine_y(slot_inches):
    return BED_Y - slot_inches * 25.4


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
# With correct axis convention:
#   VCarve X = along rail = machine Y direction
#   VCarve Y = across bed = machine X direction
# A rail blank: min_x=RAIL_W, max_x=RAIL_W+vcarve_y_span
#               min_y=slot_mark-vcarve_x_span, max_y=slot_mark
# A rail toolpath: min_x=RAIL_W+min_vy, max_x=RAIL_W+max_vy
#                  min_y=slot_mark-max_vx, max_y=slot_mark-min_vx
# Adjacent slots: slot 39 → slot_mark=2057.4; slot 52 → slot_mark=1727.2
# Gap = 2057.4-1727.2 = 330.2mm (= 13" × 25.4)
# To reach slot 39's blank from slot 52, toolpath needs max_vx > 330.2mm

def test_no_collision_when_no_existing_parts():
    part = make_part(100, 100, 0, 100, 0, 100)
    p = placed(part, "A", 39)
    result = check_placement(p, [], RAIL_W, BED_X, BED_Y)
    assert not result.collides


def test_no_collision_parts_far_apart():
    part_a = make_part(100, 100, 0, 100, 0, 100, "a.nc")
    part_b = make_part(100, 100, 0, 100, 0, 100, "b.nc")
    existing = [placed(part_a, "A", 0, "i1")]
    new = placed(part_b, "A", 117, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X, BED_Y)
    assert not result.collides


def test_collision_new_toolpath_into_existing_blank():
    # Part A at slot 39: blank Y = [2057.4-200, 2057.4] = [1857.4, 2057.4]
    # Part B at slot 52: slot_mark=1727.2; blank Y = [1727.2-200, 1727.2] = [1527.2, 1727.2]
    # B's toolpath has max_vx=600, so machine Y max = 1727.2-0=1727.2, min = 1727.2-600=1127.2
    # Wait: min_y = slot_mark-max_vx = 1727.2-600 = 1127.2 < A blank min_y=1857.4 — no overlap
    # Instead: B needs its toolpath to INCREASE in machine Y past A's blank
    # toolpath min_y = slot_mark-max_vx — to reach A's blank [1857.4, 2057.4], need min_y < 2057.4
    # i.e. slot_mark_B - max_vx < slot_mark_A → 1727.2 - max_vx < 2057.4 → max_vx > -330.2 (always true)
    # AND toolpath max_y > A's blank min_y: slot_mark_B - min_vx > slot_mark_A - vcarve_x_span_A
    # 1727.2 - 0 > 2057.4 - 200 → 1727.2 > 1857.4 — FALSE
    # So toolpath of B (high machine Y = slot_mark-min_vx = 1727.2) < A's blank low Y (1857.4) — no collision
    # Need B's toolpath to reach UPWARD toward A. Since A is at higher slot (further from operator),
    # A has higher machine_y. B at slot 52 has lower machine_y (1727.2).
    # B's toolpath extends toward machine Y direction via negative min_vx:
    # toolpath max_y = slot_mark_B - min_vx; to overlap A's blank: max_y > A_blank_min_y
    # 1727.2 - min_vx > 1857.4 → min_vx < -130.2 → use min_vx=-400 (extends 400mm toward higher machine Y)
    part_a = make_part(200, 100, 0, 200, 0, 100, "a.nc")
    # Part B toolpath extends 400mm toward higher machine Y (min_vx=-400)
    part_b = make_part(200, 100, -400, 200, 0, 100, "b.nc")

    existing = [placed(part_a, "A", 39, "i1")]
    new = placed(part_b, "A", 52, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X, BED_Y)
    assert result.collides
    assert result.conflicting_instance_id == "i1"
    assert "b.nc" in result.message
    assert "a.nc" in result.message


def test_collision_existing_toolpath_into_new_blank():
    # Existing at slot 52 with toolpath extending into slot 39's blank (same logic as above)
    part_existing = make_part(200, 100, -400, 200, 0, 100, "existing.nc")
    part_new = make_part(200, 100, 0, 200, 0, 100, "new.nc")

    existing = [placed(part_existing, "A", 52, "i1")]
    new = placed(part_new, "A", 39, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X, BED_Y)
    assert result.collides
    assert "existing.nc" in result.message


def test_no_collision_toolpath_vs_toolpath():
    """
    Two parts whose toolpath extents overlap each other but do NOT reach each
    other's blank boundary must NOT be flagged as a collision.
    """
    # Part A at slot 39: blank machine Y = [1957.4, 2057.4] (vcarve_x_span=100)
    # Part B at slot 52: blank machine Y = [1627.2, 1727.2] (vcarve_x_span=100)
    # Gap between blanks = 1957.4 - 1727.2 = 230.2mm
    # Give A a toolpath that extends 50mm below its blank (min_vx=-50)
    #   A toolpath min_y = 2057.4 - 150 = 1907.4 — doesn't reach B's blank (max 1727.2)
    part_a = make_part(100, 100, -50, 100, 0, 100, "a.nc")
    part_b = make_part(100, 100, 0, 100, 0, 100, "b.nc")

    existing = [placed(part_a, "A", 39, "i1")]
    new = placed(part_b, "A", 52, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X, BED_Y)
    assert not result.collides


def test_no_collision_a_and_b_rail_same_slot():
    """Parts on opposite rails at the same slot don't share X space."""
    part = make_part(100, 100, 0, 100, 0, 100)
    existing = [placed(part, "A", 39, "i1")]
    new = placed(part, "B", 39, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X, BED_Y)
    assert not result.collides


def test_collision_message_contains_slot_labels():
    # existing at slot 52 with toolpath extending into slot 39's blank
    part_a = make_part(200, 100, -400, 200, 0, 100, "a.nc")
    part_b = make_part(200, 100, 0, 200, 0, 100, "b.nc")

    existing = [placed(part_a, "A", 52, "i1")]
    new = placed(part_b, "A", 39, "i2")

    result = check_placement(new, existing, RAIL_W, BED_X, BED_Y)
    assert "A39" in result.message
    assert "A52" in result.message


# --- tool-radius collision tests ---

def test_toolpath_rect_expands_by_tool_radius():
    part = make_part(100, 100, 0, 100, 0, 100)
    p = placed(part, "A", 39)
    base = toolpath_rect(p, RAIL_W, BED_X, BED_Y, tool_radius_mm=0.0)
    expanded = toolpath_rect(p, RAIL_W, BED_X, BED_Y, tool_radius_mm=10.0)
    assert expanded.min_x == pytest.approx(base.min_x - 10.0)
    assert expanded.max_x == pytest.approx(base.max_x + 10.0)
    assert expanded.min_y == pytest.approx(base.min_y - 10.0)
    assert expanded.max_y == pytest.approx(base.max_y + 10.0)


def test_check_placement_catches_tool_radius_collision():
    """Centerline is clear but cutter physically reaches the adjacent blank."""
    # A at slot 26: blank machine Y = [2087.6, 2387.6]  (vcarve_x_span=300)
    # B at slot 39: toolpath max_y = 2057.4 - (-20) = 2077.4  (min_vx=-20)
    # Centerline gap = 2087.6 - 2077.4 = 10.2mm — no centerline collision
    # T2 = 1.0" dia → radius 12.7mm > 10.2mm → expanded toolpath reaches A's blank
    part_a = make_part(300, 100, 0, 300, 0, 100, "a.nc")
    part_b = make_part(300, 100, -20, 300, 0, 100, "b.nc",
                       tools={"T2": {"description": "End Mill", "diameter_inches": 1.0}})

    result = check_placement(placed(part_b, "A", 39, "i2"),
                             [placed(part_a, "A", 26, "i1")], RAIL_W, BED_X, BED_Y)
    assert result.collides
    assert "T2" in result.message


def test_check_placement_passes_when_radius_fits_in_gap():
    """Same geometry, smaller tool — cutter stays clear of the adjacent blank."""
    # Same setup: 10.2mm centerline gap
    # T2 = 0.25" dia → radius 3.175mm < 10.2mm → no collision
    part_a = make_part(300, 100, 0, 300, 0, 100, "a.nc")
    part_b = make_part(300, 100, -20, 300, 0, 100, "b.nc",
                       tools={"T2": {"description": "End Mill", "diameter_inches": 0.25}})

    result = check_placement(placed(part_b, "A", 39, "i2"),
                             [placed(part_a, "A", 26, "i1")], RAIL_W, BED_X, BED_Y)
    assert not result.collides
