import pytest
from gcode_parser import GcodePart, ZValidation
from collision import (
    PlacedPart, Rect, RAIL_DEFAULTS,
    blank_rect, toolpath_rect, rects_overlap, check_placement, slot_label,
    rail_geom, slot_mark_y,
)

# Measured SS2 rail geometry (collision.RAIL_DEFAULTS) — the two rails are
# independent systems running in opposite slot directions.
A_X  = RAIL_DEFAULTS["A"]["x_mm"]        # 134.628  rail corner
A_Y0 = RAIL_DEFAULTS["A"]["slot0_y_mm"]  # 3034.700 slot-0 edge, counts down
B_X  = RAIL_DEFAULTS["B"]["x_mm"]        # 1534.160 rail corner
B_Y0 = RAIL_DEFAULTS["B"]["slot0_y_mm"]  # 88.300   slot-0 edge, counts up


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
    r = blank_rect(p)

    my = A_Y0 - 39 * 25.4                                # slot datum, A runs -Y
    assert r.min_x == pytest.approx(A_X)                 # rail corner
    assert r.max_x == pytest.approx(A_X + 200)           # extends toward +X
    assert r.min_y == pytest.approx(my - 100)            # datum - vcarve_x_span
    assert r.max_y == pytest.approx(my)                  # datum = high-Y edge


def test_blank_rect_b_rail():
    # vcarve_x_span=100 (along rail = machine Y), vcarve_y_span=200 (across bed = machine X)
    part = make_part(100, 200, 0, 100, 0, 200)
    p = placed(part, "B", 39)
    r = blank_rect(p)

    my = B_Y0 + 39 * 25.4                                # slot datum, B runs +Y
    assert r.max_x == pytest.approx(B_X)                 # rail corner
    assert r.min_x == pytest.approx(B_X - 200)           # extends toward -X
    assert r.min_y == pytest.approx(my)                  # datum = low-Y edge for B
    assert r.max_y == pytest.approx(my + 100)


# Ground truth read off the machine: X to the rail corner, Y to the slot edge.
# Two slots per rail, so direction is observed and not inferred.
MEASURED = {
    ("A", 0):  (134.628, 3034.700),
    ("A", 13): (134.628, 2704.500),
    ("B", 0):  (1534.160,  88.300),
    ("B", 13): (1534.160,  418.500),
}


def test_rail_defaults_match_measured_machine_positions():
    """The calibration anchor: RAIL_DEFAULTS must reproduce every measured point.

    If someone edits RAIL_DEFAULTS without re-measuring, this fails. Keep it in
    step with the readout table in collision.py.
    """
    for (rail, slot), (mx, my) in MEASURED.items():
        assert rail_geom(rail)["x_mm"] == pytest.approx(mx, abs=1e-3), f"{rail}{slot} X"
        assert slot_mark_y(rail, slot) == pytest.approx(my, abs=1e-3), f"{rail}{slot} Y"


def test_measured_pitch_is_exactly_13_inches_each_way():
    """Both rails step 13" per 13 slot-inches, in opposite directions."""
    a = MEASURED[("A", 13)][1] - MEASURED[("A", 0)][1]
    b = MEASURED[("B", 13)][1] - MEASURED[("B", 0)][1]
    assert a / 25.4 == pytest.approx(-13.0)
    assert b / 25.4 == pytest.approx(+13.0)
    assert a == pytest.approx(-b)


def test_rails_are_antisymmetric_about_bed_centre():
    """A_Y(slot) + B_Y(slot) is constant — the rails mirror about one Y centre.

    Verified at two independent slots, so this is a real property of the machine
    rather than an artifact of fitting a single point pair. It is what justifies
    the provisional bed_y_mm; it does NOT by itself measure the table length.
    """
    total = MEASURED[("A", 0)][1] + MEASURED[("B", 0)][1]
    assert MEASURED[("A", 13)][1] + MEASURED[("B", 13)][1] == pytest.approx(total)
    for slot in (19.5, 39, 78, 117):
        assert slot_mark_y("A", slot) + slot_mark_y("B", slot) == pytest.approx(total)


def test_rails_run_in_opposite_directions():
    """The measured SS2 geometry: slot numbers count up toward +Y on B, -Y on A.

    Regression: a single shared formula put both rails' slot 0 at the same machine
    Y, simulating B-rail parts ~2.9 m from where they actually cut and making
    cross-rail collision detection meaningless.
    """
    assert slot_mark_y("A", 0) == pytest.approx(A_Y0)
    assert slot_mark_y("B", 0) == pytest.approx(B_Y0)
    # 13" pitch, measured B0 -> B13 = 330.200 mm
    assert slot_mark_y("B", 13) - slot_mark_y("B", 0) == pytest.approx(330.2)
    assert slot_mark_y("A", 13) - slot_mark_y("A", 0) == pytest.approx(-330.2)
    # opposite signs, and never equal for any slot
    for s in (0, 13, 19.5, 39, 78, 117):
        assert slot_mark_y("A", s) != pytest.approx(slot_mark_y("B", s))


def test_slot_mark_y_honors_config_override():
    """Either rail must be re-measurable without disturbing the other."""
    override = {"A": {"slot0_y_mm": 2000.0}}
    assert slot_mark_y("A", 39, override) == pytest.approx(2000.0 - 39 * 25.4)
    # B untouched by an A-only override
    assert slot_mark_y("B", 39, override) == pytest.approx(slot_mark_y("B", 39))


def test_rail_geom_rejects_unknown_rail():
    with pytest.raises(ValueError, match="Unknown rail"):
        rail_geom("C")


# --- toolpath_rect ---

def test_toolpath_rect_a_rail_same_as_blank_when_extents_equal():
    part = make_part(100, 100, 0, 100, 0, 100)
    p = placed(part, "A", 39)

    br = blank_rect(p)
    tr = toolpath_rect(p)
    assert tr == br


def test_toolpath_rect_a_rail_extents_beyond_blank():
    # Toolpath extends 10mm beyond blank on all sides.
    # A rail: x_dir=+1 so machine X = A_X + vcarve_Y; slot_dir=-1 so machine Y = my - vcarve_X
    part = make_part(100, 100, -10, 110, -10, 110)
    p = placed(part, "A", 39)
    tr = toolpath_rect(p)

    my = A_Y0 - 39 * 25.4
    assert tr.min_x == pytest.approx(A_X - 10)
    assert tr.max_x == pytest.approx(A_X + 110)
    assert tr.min_y == pytest.approx(my - 110)
    assert tr.max_y == pytest.approx(my + 10)


def test_toolpath_rect_b_rail_rotation():
    # B rail: x_dir=-1 so machine X = B_X - vcarve_Y (mirrored across the bed)
    # min_vy=0, max_vy=110 → min_x = B_X-110, max_x = B_X
    part = make_part(100, 100, 0, 100, 0, 110)
    p = placed(part, "B", 39)
    tr = toolpath_rect(p)

    my = B_Y0 + 39 * 25.4
    assert tr.min_x == pytest.approx(B_X - 110)      # B_X - max_vy
    assert tr.max_x == pytest.approx(B_X)             # B_X - min_vy (0)
    assert tr.min_y == pytest.approx(my)              # my + min_vx (0)
    assert tr.max_y == pytest.approx(my + 100)


def test_toolpath_rect_b_rail_x_rotation():
    # B rail: slot_dir=+1 so machine Y = my + vcarve_X (extends toward +Y)
    part = make_part(100, 100, 0, 110, 0, 100)
    p = placed(part, "B", 39)
    tr = toolpath_rect(p)

    my = B_Y0 + 39 * 25.4
    assert tr.min_y == pytest.approx(my)              # my + min_vx (0)
    assert tr.max_y == pytest.approx(my + 110)        # extension on high-Y side


# --- verified spec example: correct axis convention ---
# VCarve X = along rail = machine Y; VCarve Y = across bed = machine X
# A rail: machX = RAIL_W + VCarve_Y,  machY = slot_mark - VCarve_X
# B rail: machX = (BED_X-RAIL_W) - VCarve_Y,  machY = slot_mark + VCarve_X

def _notch_at(rail, slot_inches, vx, vy):
    """Machine position of a single feature at file coords (vx, vy).

    A degenerate toolpath (min==max on both axes) makes toolpath_rect collapse to
    the point, so this asserts against the production transform rather than
    re-doing the arithmetic in the test.
    """
    part = make_part(300, 400, vx, vx, vy, vy)
    tr = toolpath_rect(placed(part, rail, slot_inches))
    return tr.min_x, tr.min_y


def test_spec_example_a_rail_notch_position():
    """A rail: machine X = A_X + VCarve_Y, machine Y = slot datum - VCarve_X."""
    nx, ny = _notch_at("A", 36, vx=20, vy=380)
    assert nx == pytest.approx(A_X + 380, abs=0.01)
    assert ny == pytest.approx(A_Y0 - 36 * 25.4 - 20, abs=0.1)


def test_spec_example_b_rail_notch_position():
    """B rail: machine X = B_X - VCarve_Y, machine Y = slot datum + VCarve_X.

    Both file axes flip relative to the A rail, which is the 180° rotation.
    """
    nx, ny = _notch_at("B", 36, vx=20, vy=380)
    assert nx == pytest.approx(B_X - 380, abs=0.01)
    assert ny == pytest.approx(B_Y0 + 36 * 25.4 + 20, abs=0.1)


# --- cross-rail collisions ---
#
# The rails are loaded independently, but the cutter is not: a part on A and a
# part on B that overlap in machine X and Y will physically interfere. The rail
# datums are 1399.5 mm (55.1") apart, so two parts whose across-bed dimensions
# sum to more than that overlap in X — a real case, not a hypothetical.

def test_collision_across_rails_wide_parts():
    """A wide A-rail part and a wide B-rail part that overlap must be refused."""
    # 800mm across the bed each: 800 + 800 = 1600 > 1399.5 → X ranges overlap.
    wide = lambda: make_part(400, 800, 0, 400, 0, 800)
    a = placed(wide(), "A", 39, "a1")     # Y 1644.1 .. 2044.1
    b = placed(wide(), "B", 65, "b1")     # Y 1739.3 .. 2139.3  → overlaps A in Y

    # Sanity: they really do overlap in both axes.
    ra, rb = blank_rect(a), blank_rect(b)
    assert ra.max_x > rb.min_x and rb.max_x > ra.min_x
    assert ra.max_y > rb.min_y and rb.max_y > ra.min_y

    result = check_placement(b, [a])
    assert result.collides
    assert result.conflicting_instance_id == "a1"


def test_no_collision_across_rails_narrow_parts():
    """Narrow parts on opposite rails leave the middle of the bed clear."""
    narrow = lambda: make_part(400, 200, 0, 400, 0, 200)
    a = placed(narrow(), "A", 39, "a1")
    b = placed(narrow(), "B", 65, "b1")
    assert not check_placement(b, [a]).collides


def test_collision_across_rails_is_symmetric():
    """Order of placement must not change the verdict."""
    wide = lambda: make_part(400, 800, 0, 400, 0, 800)
    a = placed(wide(), "A", 39, "a1")
    b = placed(wide(), "B", 65, "b1")
    assert check_placement(b, [a]).collides
    assert check_placement(a, [b]).collides


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
    result = check_placement(p, [])
    assert not result.collides


def test_no_collision_parts_far_apart():
    part_a = make_part(100, 100, 0, 100, 0, 100, "a.nc")
    part_b = make_part(100, 100, 0, 100, 0, 100, "b.nc")
    existing = [placed(part_a, "A", 0, "i1")]
    new = placed(part_b, "A", 117, "i2")

    result = check_placement(new, existing)
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

    result = check_placement(new, existing)
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

    result = check_placement(new, existing)
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

    result = check_placement(new, existing)
    assert not result.collides


def test_no_collision_a_and_b_rail_same_slot():
    """Parts on opposite rails at the same slot don't share X space."""
    part = make_part(100, 100, 0, 100, 0, 100)
    existing = [placed(part, "A", 39, "i1")]
    new = placed(part, "B", 39, "i2")

    result = check_placement(new, existing)
    assert not result.collides


def test_collision_message_contains_slot_labels():
    # existing at slot 52 with toolpath extending into slot 39's blank
    part_a = make_part(200, 100, -400, 200, 0, 100, "a.nc")
    part_b = make_part(200, 100, 0, 200, 0, 100, "b.nc")

    existing = [placed(part_a, "A", 52, "i1")]
    new = placed(part_b, "A", 39, "i2")

    result = check_placement(new, existing)
    assert "A39" in result.message
    assert "A52" in result.message


# --- tool-radius collision tests ---

def test_toolpath_rect_expands_by_tool_radius():
    part = make_part(100, 100, 0, 100, 0, 100)
    p = placed(part, "A", 39)
    base = toolpath_rect(p, tool_radius_mm=0.0)
    expanded = toolpath_rect(p, tool_radius_mm=10.0)
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
                             [placed(part_a, "A", 26, "i1")])
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
                             [placed(part_a, "A", 26, "i1")])
    assert not result.collides
