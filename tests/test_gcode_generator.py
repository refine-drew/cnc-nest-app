"""
Tests for gcode_generator.py — master G-code builder.

Fixtures use parse_vcarve_text so the passes list is populated exactly as it
would be in production.
"""
import re
import pytest
from gcode_parser import parse_vcarve_text
from collision import PlacedPart, RAIL_DEFAULTS, slot_mark_y
from gcode_validator import validate_gcode
from gcode_generator import (
    comment,
    comment_is_wellformed,
    generate_master_gcode,
    block_tool_sequence,
    _build_blocks,
    _dedup_spindle,
    _extract_body,
    _transform_body,
    _transform_line,
    _transform_params,
    _nearest_neighbor_sort,
    _first_xy,
    _last_xy,
)

# ── test config ───────────────────────────────────────────────────────────────

BED_X = 1668.788
BED_Y = 3123.0

# Measured SS2 rail geometry (collision.RAIL_DEFAULTS)
A_X  = RAIL_DEFAULTS["A"]["x_mm"]
A_Y0 = RAIL_DEFAULTS["A"]["slot0_y_mm"]
B_X  = RAIL_DEFAULTS["B"]["x_mm"]
B_Y0 = RAIL_DEFAULTS["B"]["slot0_y_mm"]

# _transform_line is pure line-rewriting math driven by a params dict. These
# constants are arbitrary inputs for those unit tests and deliberately unrelated
# to the measured rail geometry above.
LINE_X_CONST = 82.55
LINE_FAR_X = 1441.45
LINE_Y_CONST = 2057.4

SETTINGS = {
    "job_name": "test_job",
    "job_safe_z": {"value": 25.4, "driven_by": "part.nc"},
    "advanced": {
        "bed_x_mm": BED_X,
        "bed_y_mm": BED_Y,
        "safe_z_clearance_mm": 6.35,
        "park_x": 0.0,
        "park_y": 3048.0,
        "slots": [0, 13, 19.5, 26, 39, 52, 58.5, 65, 78, 91, 97.5, 104, 117],
    },
}

# ── G-code fixtures ───────────────────────────────────────────────────────────

def _nc(tools_passes):
    """
    Build a minimal VCarve-style G-code string.
    tools_passes: list of (tool_num, tool_name, diameter) for each pass.
    """
    header = "( Material Size)\n( X= 200.0, Y= 100.0, Z= 19.05)\n"
    for tn, name, dia in tools_passes:
        header += f"({tn} = {name} {{{dia} inches}})\n"

    body = ""
    for tn, name, dia in tools_passes:
        body += (
            f"{tn} M06\n"
            f"(Tool: {name} {{{dia} inches}})\n"
            f"G43 H{tn[1:]} Z44.4754\n"
            f"M03 S18000\n"
            f"G00 X0 Y0\n"
            f"G01 X50 Y50 Z-0.254\n"
            f"G01 X100 Y50 Z-0.254\n"
            f"G53 G49 Z0\n"
            f"M05\n"
        )
    body += "M30\n%\n"
    return header + body


SINGLE_T2 = _nc([("T2", "End Mill", 0.5)])
TWO_PASS_T2_T4 = _nc([("T2", "End Mill", 0.5), ("T4", "Table Stiff", 0.75)])
THREE_PASS_T2_T4_T2 = _nc([
    ("T2", "End Mill", 0.5),
    ("T4", "Table Stiff", 0.75),
    ("T2", "End Mill", 0.5),
])

ARC_NC = (
    "( Material Size)\n( X= 200.0, Y= 100.0, Z= 19.05)\n"
    "(T2 = End Mill {0.5 inches})\n"
    "T2 M06\n(Tool: End Mill {0.5 inches})\nG43 H2 Z44.4754\nM03 S18000\n"
    "G00 X10 Y10\n"
    "G02 X50 Y50 I20 J0 Z-0.254\n"  # CW arc with I offset
    "G03 X80 Y80 I-10 J5 Z-0.254\n"  # CCW arc
    "G53 G49 Z0\nM05\nM30\n%\n"
)


# A multi-region pass: VCarve re-issues M03 S18000 before each cutting region
# (after a clearance retract) even though the spindle never stops. The repeated
# command is redundant and should be dropped from the master output.
MULTI_REGION_REDUNDANT = (
    "( Material Size)\n( X= 200.0, Y= 100.0, Z= 19.05)\n"
    "(T2 = End Mill {0.5 inches})\n"
    "T2 M06\n(Tool: End Mill {0.5 inches})\nG43 H2 Z44.4754\nM03 S18000\n"
    "G00 X0 Y0\n"
    "G01 X50 Y50 Z-0.254\n"
    "G00 Z38.1\n"          # clearance retract between regions
    "M03 S18000\n"         # redundant — spindle already at 18000
    "G00 X100 Y100\n"
    "G01 X120 Y120 Z-0.254\n"
    "G53 G49 Z0\nM05\nM30\n%\n"
)

# A pass that genuinely changes the spindle speed mid-pass — the change must
# be preserved in the output.
MULTI_REGION_REAL_CHANGE = (
    "( Material Size)\n( X= 200.0, Y= 100.0, Z= 19.05)\n"
    "(T2 = End Mill {0.5 inches})\n"
    "T2 M06\n(Tool: End Mill {0.5 inches})\nG43 H2 Z44.4754\nM03 S18000\n"
    "G00 X0 Y0\n"
    "G01 X50 Y50 Z-0.254\n"
    "G00 Z38.1\n"
    "M03 S12000\n"         # genuine speed change — keep it
    "G00 X100 Y100\n"
    "G01 X120 Y120 Z-0.254\n"
    "G53 G49 Z0\nM05\nM30\n%\n"
)


def _placed(nc_text, rail, slot_inches, instance_id="i1"):
    part = parse_vcarve_text(nc_text, filename="part.nc")
    return PlacedPart(part=part, rail=rail, slot_inches=slot_inches, instance_id=instance_id)


# ── _extract_body ─────────────────────────────────────────────────────────────

def test_extract_body_returns_moves_only():
    lines = [
        "T2 M06",
        "(Tool: End Mill {0.5 inches})",
        "G43 H2 Z44.4754",
        "M03 S18000",
        "G00 X0 Y0",
        "G01 X100 Y100 Z-0.254",
        "G53 G49 Z0",
        "M05",
    ]
    body = _extract_body(lines)
    assert body == ["G00 X0 Y0", "G01 X100 Y100 Z-0.254"]


def test_extract_body_strips_existing_n_codes():
    lines = [
        "T2 M06", "G43 H2 Z44", "M03 S18000",
        "N10 G00 X0 Y0",
        "N20 G01 X100 Y100",
        "G53 G49 Z0", "M05",
    ]
    body = _extract_body(lines)
    assert body == ["G00 X0 Y0", "G01 X100 Y100"]


def test_extract_body_empty_when_no_m03():
    lines = ["T2 M06", "G00 X0 Y0", "G01 X100 Y100", "M05"]
    body = _extract_body(lines)
    assert body == []


def test_extract_body_strips_g43_within_body():
    """G43 appearing after M03 (e.g. multi-region VCarve files) must be stripped."""
    lines = [
        "T2 M06", "G43 H2 Z44.4754", "M03 S18000",
        "G00 X0 Y0",
        "G01 X100 Y100 Z-0.254",
        "G43 H2 Z44.4754",  # mid-body G43 — must be filtered out
        "G00 X200 Y200",
        "G53 G49 Z0", "M05",
    ]
    body = _extract_body(lines)
    assert not any("G43" in ln.upper() for ln in body)
    assert "G00 X200 Y200" in body


def test_extract_body_strips_trailing_z_retract():
    """Trailing G00 Z retract is stripped so the generator controls the retract."""
    lines = [
        "T2 M06", "G43 H2 Z44", "M03 S18000",
        "G00 X0 Y0",
        "G01 X100 Y100 Z-0.254",
        "G00 Z25.4",  # trailing retract — must be stripped
        "G53 G49 Z0", "M05",
    ]
    body = _extract_body(lines)
    assert body == ["G00 X0 Y0", "G01 X100 Y100 Z-0.254"]


# ── _dedup_spindle ────────────────────────────────────────────────────────────

def test_dedup_spindle_drops_redundant_standalone():
    # spindle already at 18000; a bare M03 S18000 is redundant → whole line dropped
    assert _dedup_spindle("M03 S18000", 18000) == (None, 18000)


def test_dedup_spindle_keeps_genuine_change():
    # different speed → keep the line and update the tracked speed
    assert _dedup_spindle("M03 S12000", 18000) == ("M03 S12000", 12000)


def test_dedup_spindle_passes_through_non_spindle_lines():
    assert _dedup_spindle("G01 X50 Y50 Z-0.254", 18000) == ("G01 X50 Y50 Z-0.254", 18000)


def test_dedup_spindle_strips_redundant_s_on_motion_line():
    # redundant S on a motion line → strip just the S-word, keep the motion
    assert _dedup_spindle("G01 X50 S18000", 18000) == ("G01 X50", 18000)


def test_dedup_spindle_initial_current_none_keeps_line():
    # no prior speed → cannot be redundant
    assert _dedup_spindle("M03 S18000", None) == ("M03 S18000", 18000)


# ── _transform_line ───────────────────────────────────────────────────────────

def test_transform_a_rail_adds_offset():
    # A rail: b_x=True, b_y=False
    # file X (VCarve X) → machine Y: slot_mark - vx → output Y word
    # file Y (VCarve Y) → machine X: rail_w + vy  → output X word
    # X50 → Y(2057.4 - 50) = Y2007.4;  Y100 → X(82.55 + 100) = X182.55
    params = {"b_x": True, "x": LINE_Y_CONST, "b_y": False, "y": LINE_X_CONST}
    result = _transform_line("G01 X50 Y100 Z-0.254", params)
    assert "Y2007.4000" in result   # file X=50 → machine Y
    assert "X182.5500" in result    # file Y=100 → machine X
    assert "Z-0.254" in result      # Z unchanged


def test_transform_b_rail_one_axis_mirrored():
    # B rail (true 180° rotation): b_x=False, b_y=True
    # file X → machine Y: x_const + vx → output Y word (additive)
    # file Y → machine X: y_const - vy → output X word (mirrored)
    params = {"b_x": False, "x": LINE_Y_CONST, "b_y": True, "y": LINE_FAR_X}
    result = _transform_line("G01 X50 Y30 Z-0.254", params)
    assert "Y2107.4000" in result  # file X=50 → machine Y = 2057.4 + 50
    assert "X1411.4500" in result  # file Y=30 → machine X = LINE_FAR_X - 30


def test_transform_b_rail_no_arc_swap():
    # B rail (b_x=False, b_y=True): one axis mirrored + axis-swap = even flips →
    # proper rotation → arc direction preserved (no G02/G03 swap).
    params = {"b_x": False, "x": LINE_Y_CONST, "b_y": True, "y": LINE_FAR_X}
    result_cw = _transform_line("G02 X50 Y50 I10 J0 Z-0.254", params)
    result_ccw = _transform_line("G03 X50 Y50 I-10 J5 Z-0.254", params)
    assert "G02" in result_cw and "G03" not in result_cw
    assert "G03" in result_ccw and "G02" not in result_ccw


def test_transform_a_rail_no_arc_swap():
    # A rail: one axis mirrored + axis-swap = 2 total flips (even) → orientation preserved → no swap
    params = {"b_x": True, "x": LINE_Y_CONST, "b_y": False, "y": LINE_X_CONST}
    result_cw = _transform_line("G02 X50 Y50 I10 J0 Z-0.254", params)
    result_ccw = _transform_line("G03 X50 Y50 I-10 J5 Z-0.254", params)
    assert "G02" in result_cw and "G03" not in result_cw
    assert "G03" in result_ccw and "G02" not in result_ccw


def test_transform_b_rail_negates_ij():
    # B rail: b_x=False (no negate on file-I), b_y=True (negate file-J).
    # file I20 (VCarve-X direction) → machine-Y direction → output J, not negated: J20
    # file J-5 (VCarve-Y direction) → machine-X direction → output I, negated: I5
    params = {"b_x": False, "x": LINE_Y_CONST, "b_y": True, "y": LINE_FAR_X}
    result = _transform_line("G02 X50 Y50 I20 J-5 Z-0.254", params)
    assert "J20.0000" in result   # file I20, no x_mirror → output J=20
    assert "I5.0000" in result    # file J-5, y_mirror → output I=5


def test_transform_comment_unchanged():
    params = {"b_x": False, "x": LINE_X_CONST, "b_y": False, "y": 2000.0}
    line = "(Tool: End Mill {0.5 inches})"
    assert _transform_line(line, params) == line


def test_transform_g53_unchanged():
    params = {"b_x": False, "x": LINE_X_CONST, "b_y": False, "y": 2000.0}
    line = "G53 G49 Z0"
    assert _transform_line(line, params) == line


def test_transform_a_rail_z_unchanged():
    params = {"b_x": True, "x": LINE_Y_CONST, "b_y": False, "y": LINE_X_CONST}
    result = _transform_line("G01 X10 Y10 Z18.796", params)
    assert "Z18.796" in result


# ── _transform_params ─────────────────────────────────────────────────────────

def test_transform_params_a_rail():
    p = _placed(SINGLE_T2, "A", 39)
    params = _transform_params(p)
    assert params["b_x"] is True      # A slot_dir = -1 → file X mirrored
    assert params["b_y"] is False     # A x_dir    = +1 → file Y additive
    assert params["x"] == pytest.approx(A_Y0 - 39 * 25.4)
    assert params["y"] == pytest.approx(A_X)


def test_transform_params_b_rail():
    p = _placed(SINGLE_T2, "B", 39)
    params = _transform_params(p)
    assert params["b_x"] is False     # B slot_dir = +1 → file X additive
    assert params["b_y"] is True      # B x_dir    = -1 → file Y mirrored
    assert params["x"] == pytest.approx(B_Y0 + 39 * 25.4)
    assert params["y"] == pytest.approx(B_X)


def test_transform_params_matches_collision_slot_mark():
    """The generator and collision detection must agree on every slot datum.

    If these drift, a part is cut somewhere the collision check never looked.
    """
    for rail in ("A", "B"):
        for slot in (0, 13, 19.5, 39, 78, 117):
            p = _placed(SINGLE_T2, rail, slot)
            assert _transform_params(p)["x"] == pytest.approx(slot_mark_y(rail, slot))


def test_transform_params_honors_rail_override():
    """Re-measuring one rail must move only that rail."""
    override = {"A": {"slot0_y_mm": 2000.0}}
    a = _transform_params(_placed(SINGLE_T2, "A", 39), override)
    b = _transform_params(_placed(SINGLE_T2, "B", 39), override)
    assert a["x"] == pytest.approx(2000.0 - 39 * 25.4)
    assert b["x"] == pytest.approx(B_Y0 + 39 * 25.4)   # untouched


def test_generate_honors_rail_override():
    """End-to-end: moving a rail datum must shift the emitted cut coordinates."""
    p = _placed(SINGLE_T2, "A", 39)
    moved = {
        **SETTINGS,
        "advanced": {**SETTINGS["advanced"],
                     "rails": {"A": {**RAIL_DEFAULTS["A"], "slot0_y_mm": 2000.0}}},
    }
    assert generate_master_gcode([p], SETTINGS) != generate_master_gcode([p], moved)


# ── fence-origin offset ───────────────────────────────────────────────────────

def _fence(x_in, y_in):
    return {**SETTINGS, "advanced": {
        **SETTINGS["advanced"],
        "fence_offset_x_in": x_in,
        "fence_offset_y_in": y_in,
    }}


def test_fence_offset_absent_leaves_output_unchanged():
    """Missing keys must behave exactly like no offset."""
    p = _placed(SINGLE_T2, "A", 39)
    adv = {k: v for k, v in SETTINGS["advanced"].items()
           if not k.startswith("fence_offset_")}
    bare = generate_master_gcode([p], {**SETTINGS, "advanced": adv})
    zeroed = generate_master_gcode([p], _fence(0.0, 0.0))
    assert bare == zeroed


def test_fence_offset_zero_is_identity():
    p = _placed(SINGLE_T2, "B", 26)
    assert generate_master_gcode([p], SETTINGS) == generate_master_gcode([p], _fence(0.0, 0.0))


@pytest.mark.parametrize("rail", ["A", "B"])
def test_fence_offset_shifts_transform_constants_on_both_rails(rail):
    p = _placed(SINGLE_T2, rail, 39)
    base = _transform_params(p)
    off = _transform_params(p, None, x_off_mm=2.0, y_off_mm=3.0)
    # 'x'/'y' are named for the VCarve axis, not the output word: the machine-Y
    # offset folds into 'x' (slot_mark) and the machine-X offset into 'y' (rail).
    assert off["x"] == pytest.approx(base["x"] + 3.0)
    assert off["y"] == pytest.approx(base["y"] + 2.0)
    # Mirror flags must be untouched — the offset is a translation, not a flip.
    assert off["b_x"] == base["b_x"] and off["b_y"] == base["b_y"]


def test_fence_offset_shifts_cut_coordinates():
    """Each offset must move its own machine axis in the matching output word."""
    p = _placed(SINGLE_T2, "A", 39)
    # X word = machine X = A_X + vcarve_Y + x_off; at vcarve (0,0)
    assert f"X{A_X + 0.1 * 25.4:.4f}" in generate_master_gcode([p], _fence(0.1, 0.0))
    # Y word = machine Y = A slot datum + y_off
    expected_y = A_Y0 - 39 * 25.4 + 0.1 * 25.4
    assert f"Y{expected_y:.4f}" in generate_master_gcode([p], _fence(0.0, 0.1))


def test_fence_offset_does_not_move_the_g53_park():
    """The park is G53 (absolute machine coords) — the fence origin cannot shift it."""
    p = _placed(SINGLE_T2, "A", 39)
    park_line = f"G00 G53 X{SETTINGS['advanced']['park_x']:.4f} " \
                f"Y{SETTINGS['advanced']['park_y']:.4f} M05"
    for x_in, y_in in [(0.0, 0.0), (0.1, 0.2), (-0.059, 0.026)]:
        out = generate_master_gcode([p], _fence(x_in, y_in))
        assert park_line in out, f"park line moved with offset ({x_in}, {y_in})"


# ── tool capacity ─────────────────────────────────────────────────────────────

NINE_TOOL_NC = _nc([(f"T{n}", f"Tool {n}", 0.25) for n in range(1, 10)])


def test_generate_raises_when_tools_exceed_capacity():
    """9 distinct tools on an 8-position changer cannot be loaded — fail loudly."""
    p = _placed(NINE_TOOL_NC, "A", 39)
    settings = {**SETTINGS, "advanced": {**SETTINGS["advanced"], "tool_capacity": 8}}
    with pytest.raises(ValueError, match="holds only 8"):
        generate_master_gcode([p], settings)


def test_generate_allows_tools_exactly_at_capacity():
    p = _placed(_nc([(f"T{n}", f"Tool {n}", 0.25) for n in range(1, 9)]), "A", 39)
    settings = {**SETTINGS, "advanced": {**SETTINGS["advanced"], "tool_capacity": 8}}
    result = generate_master_gcode([p], settings)
    assert "T8 M06" in result


def test_tool_capacity_defaults_to_eight_when_absent():
    """An older config.json without the key must still be gated at 8."""
    adv = {k: v for k, v in SETTINGS["advanced"].items() if k != "tool_capacity"}
    p = _placed(NINE_TOOL_NC, "A", 39)
    with pytest.raises(ValueError, match="holds only 8"):
        generate_master_gcode([p], {**SETTINGS, "advanced": adv})


# ── _nearest_neighbor_sort ────────────────────────────────────────────────────

def test_nearest_neighbor_single_segment():
    segs = [["G01 X10 Y10", "G01 X20 Y20"]]
    assert _nearest_neighbor_sort(segs) == segs


def test_nearest_neighbor_picks_closest_first():
    # Seg A starts at (0, 0); seg B starts at (1000, 1000); seg C starts at (5, 0)
    seg_a = ["G01 X0 Y0", "G01 X10 Y0"]
    seg_b = ["G01 X1000 Y1000", "G01 X1010 Y1000"]
    seg_c = ["G01 X5 Y0", "G01 X15 Y0"]
    result = _nearest_neighbor_sort([seg_a, seg_b, seg_c])
    # A ends at (10, 0); nearest to (10,0) is C (starts at 5,0, dist=5) not B (dist~1404)
    assert result[0] == seg_a
    assert result[1] == seg_c
    assert result[2] == seg_b


# ── _first_xy / _last_xy ──────────────────────────────────────────────────────

def test_first_xy():
    lines = ["G00 X10 Y20", "G01 X30 Y40"]
    assert _first_xy(lines) == (10.0, 20.0)


def test_last_xy():
    lines = ["G00 X10 Y20", "G01 X30 Y40"]
    assert _last_xy(lines) == (30.0, 40.0)


# ── generate_master_gcode — structure ────────────────────────────────────────

def test_output_has_required_header_lines():
    p = _placed(SINGLE_T2, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    assert "(MASTER JOB - Generated by CNC Nest Tool)" in result
    assert "(Job: test_job)" in result
    assert "(Parts: part.nc)" in result
    assert "G00 G17 G71 G40 G49 G80 G90" in result
    # The controller rejects G21; metric is set by G71 in the safety line above.
    assert "G21" not in result
    assert "G54" in result


def test_output_ends_with_m30_and_percent():
    p = _placed(SINGLE_T2, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    lines = result.strip().splitlines()
    assert lines[-1] == "%"
    assert "M30" in lines[-2]


def test_output_has_park_line():
    p = _placed(SINGLE_T2, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    assert "G00 G53 X0.0000 Y3048.0000" in result


def test_output_has_line_numbers():
    p = _placed(SINGLE_T2, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    numbered = [l for l in result.splitlines() if re.match(r"N\d+\s", l)]
    assert len(numbered) >= 6  # at least header setup + tool block lines


def test_output_line_numbers_increment_by_10():
    p = _placed(SINGLE_T2, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    nums = [int(re.match(r"N(\d+)\s", l).group(1))
            for l in result.splitlines() if re.match(r"N\d+\s", l)]
    diffs = [nums[i + 1] - nums[i] for i in range(len(nums) - 1)]
    assert all(d == 10 for d in diffs)


def test_output_uses_job_safe_z_in_g43():
    p = _placed(SINGLE_T2, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    assert "G43 H2 Z25.4000" in result


def test_output_single_tool_block_for_single_part():
    p = _placed(SINGLE_T2, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    assert result.count("T2 M06") == 1
    assert result.count("M05") == 2  # one in tool block, one in park


def test_output_drops_redundant_mid_pass_spindle():
    # The block emits one M03 S18000 header; the source's redundant mid-region
    # M03 S18000 must NOT survive into the master output.
    p = _placed(MULTI_REGION_REDUNDANT, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    assert result.count("M03 S18000") == 1
    # both cutting regions survive (each plunges to Z-0.254)
    assert result.count("Z-0.254") == 2


def test_output_preserves_genuine_spindle_change():
    # A real mid-pass change to S12000 must be kept (in addition to the header).
    p = _placed(MULTI_REGION_REAL_CHANGE, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    assert result.count("M03 S18000") == 1   # header
    assert result.count("M03 S12000") == 1   # genuine change preserved


# ── coordinate transformation in output ──────────────────────────────────────

def test_a_rail_coordinates_offset_in_output():
    # A rail slot 39. G00 X0 Y0 in file:
    #   file X=0 → machine Y = slot datum - 0  → output Y word
    #   file Y=0 → machine X = A_X + 0         → output X word
    p = _placed(SINGLE_T2, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    assert f"Y{A_Y0 - 39 * 25.4:.4f}" in result   # file X=0 → machine Y
    assert f"X{A_X:.4f}" in result                # file Y=0 → machine X


def test_b_rail_coordinates_rotated_in_output():
    # B rail slot 39 — the 180° rotation: file X additive, file Y mirrored.
    # G00 X0 Y0 in file:
    #   file X=0 → machine Y = B slot datum + 0 → output Y word
    #   file Y=0 → machine X = B_X - 0          → output X word
    p = _placed(SINGLE_T2, "B", 39)
    result = generate_master_gcode([p], SETTINGS)
    assert f"Y{B_Y0 + 39 * 25.4:.4f}" in result   # file X=0 → machine Y
    assert f"X{B_X:.4f}" in result                # file Y=0 → machine X


def test_b_rail_arcs_not_swapped_in_output():
    # A true 180° rotation preserves arc handedness — G02/G03 are NOT swapped.
    p = _placed(ARC_NC, "B", 39)
    result = generate_master_gcode([p], SETTINGS)
    tool_block = result[result.index("T2 M06"):]
    # The source G02 (CW, I20 J0) → J20 (file-I, not negated) and stays G02.
    g02_line = next(ln for ln in tool_block.splitlines() if "J20.0000" in ln)
    assert "G02" in g02_line and "G03" not in g02_line
    # The source G03 (CCW) stays G03.
    assert "G03" in tool_block


# ── pass merging (order of operations) ───────────────────────────────────────

def test_two_parts_same_tool_merged():
    """Two parts both using T2 → one T2 M06 in output."""
    p1 = _placed(SINGLE_T2, "A", 39, "i1")
    p2 = _placed(SINGLE_T2, "A", 26, "i2")
    result = generate_master_gcode([p1, p2], SETTINGS)
    assert result.count("T2 M06") == 1


def test_two_parts_same_tool_has_retract_between_segments():
    """Within a tool block, a G00 Z[safe_z] separates part segments."""
    p1 = _placed(SINGLE_T2, "A", 39, "i1")
    p2 = _placed(SINGLE_T2, "A", 26, "i2")
    result = generate_master_gcode([p1, p2], SETTINGS)
    # job_safe_z = 25.4, one retract between the two segments. The park block
    # deliberately has none — it retracts in machine coordinates instead.
    assert result.count("G00 Z25.4000") == 1


def test_g43_appears_only_once_per_tool_block():
    """G43 must appear exactly once per tool block, never between part segments."""
    p1 = _placed(SINGLE_T2, "A", 39, "i1")
    p2 = _placed(SINGLE_T2, "A", 26, "i2")
    result = generate_master_gcode([p1, p2], SETTINGS)
    assert result.count("G43") == 1


def test_two_parts_two_passes_merged():
    """Both parts T2→T4 → output: T2(A+B) → T4(A+B) — 2 tool blocks."""
    p1 = _placed(TWO_PASS_T2_T4, "A", 39, "i1")
    p2 = _placed(TWO_PASS_T2_T4, "A", 26, "i2")
    result = generate_master_gcode([p1, p2], SETTINGS)
    assert result.count("T2 M06") == 1
    assert result.count("T4 M06") == 1
    # T2 must come before T4
    assert result.index("T2 M06") < result.index("T4 M06")


def test_spec_example_t2_t4_t2_merged():
    """
    Spec example: both parts T2→T4→T2.
    Output: T2(A+B) → T4(A+B) → T2(A+B) — saves 2 tool changes.
    """
    p1 = _placed(THREE_PASS_T2_T4_T2, "A", 39, "i1")
    p2 = _placed(THREE_PASS_T2_T4_T2, "A", 26, "i2")
    result = generate_master_gcode([p1, p2], SETTINGS)
    assert result.count("T2 M06") == 2   # pass 1 and pass 3
    assert result.count("T4 M06") == 1   # pass 2
    # Order: T2 → T4 → T2
    t2_first = result.index("T2 M06")
    t4_pos = result.index("T4 M06")
    t2_second = result.index("T2 M06", t4_pos)
    assert t2_first < t4_pos < t2_second


def test_spec_example_different_tools_not_merged():
    """
    Spec example: A: T2→T4→T2, B: T2→T5.
    Output: T2(A+B) → T4(A) → T5(B) → T2(A) — 4 tool blocks.
    """
    three_pass = _nc([
        ("T2", "End Mill", 0.5),
        ("T4", "Table Stiff", 0.75),
        ("T2", "End Mill", 0.5),
    ])
    two_pass_t5 = _nc([
        ("T2", "End Mill", 0.5),
        ("T5", "V-Bit", 0.25),
    ])
    p1 = _placed(three_pass, "A", 39, "i1")
    p2 = _placed(two_pass_t5, "A", 26, "i2")
    result = generate_master_gcode([p1, p2], SETTINGS)
    assert result.count("T2 M06") == 2
    assert result.count("T4 M06") == 1
    assert result.count("T5 M06") == 1
    # Order: T2 → T4 → T5 → T2
    t2_a = result.index("T2 M06")
    t4_pos = result.index("T4 M06")
    t5_pos = result.index("T5 M06")
    t2_b = result.index("T2 M06", t4_pos)
    assert t2_a < t4_pos < t5_pos < t2_b


# ── operation labelling and tape marks ───────────────────────────────────────

NAMED_OPS_NC = (
    "( Material Size)\n( X= 200.0, Y= 100.0, Z= 19.05)\n"
    "(T2 = End Mill {0.5 inches})\n"
    "N25 G90\n"
    "\n"
    "(TABLE OUTSIDE PROFILE ADAPTIVE)\n"
    "N30 T2 M06\n"
    "(Tool: End Mill {0.5 inches})\n"
    "N35 G43 H2 Z44.4754\nN40 M03 S18000\n"
    "N50 G00 X0 Y0\nN55 G01 X50 Y50 Z-0.254\n"
    "(OUTSIDE FINISH PASS)\n"
    "N60 G01 X100 Y50 Z-0.254\n"
    "N65 G53 G49 Z0\nN70 M05\nN75 M30\n%\n"
)


def test_first_operation_is_named_in_output():
    # The later operation's name survives on its own because it sits inside the
    # body; the first one has to be carried across from above the tool change.
    result = generate_master_gcode([_placed(NAMED_OPS_NC, "A", 39)], SETTINGS)
    assert "(TABLE OUTSIDE PROFILE ADAPTIVE)" in result
    assert "(OUTSIDE FINISH PASS)" in result


def test_operation_name_precedes_its_own_geometry():
    # The travel sort reorders segments, so the name must ride with the segment
    # it labels rather than being emitted once at the top of the tool block.
    result = generate_master_gcode(
        [_placed(NAMED_OPS_NC, "A", 39, "i1"), _placed(NAMED_OPS_NC, "B", 26, "i2")],
        SETTINGS)
    lines = result.splitlines()
    names = [i for i, l in enumerate(lines) if "TABLE OUTSIDE PROFILE ADAPTIVE" in l]
    assert len(names) == 2   # one per placed instance
    for i in names:
        assert any(re.search(r"\bG0?[01]\b", l) for l in lines[i + 1:i + 4])


def test_operation_name_does_not_steer_the_travel_sort():
    # A toolpath name containing something like "X2" must not be read as an
    # X word when the sort picks each segment's start point.
    assert _first_xy(["(POCKET X2 Y9)", "G00 X40 Y70"]) == (40.0, 70.0)


def test_percent_present_at_both_ends():
    result = generate_master_gcode([_placed(SINGLE_T2, "A", 39)], SETTINGS)
    lines = [l for l in result.splitlines() if l.strip()]
    assert lines[0] == "%"
    assert lines[-1] == "%"


# ── modal hygiene ─────────────────────────────────────────────────────────────
#
# Motion mode is modal, so a block that omits G00/G01 runs at whatever the last
# motion command left active. Two defects came out of that: a park retract that
# fed instead of rapiding, and — worse — a G54 Z move issued while G49 was
# active, which positions the spindle gauge line rather than the tool tip.

def _park_block(result: str) -> list:
    """Lines from the park comment through M30."""
    lines = result.splitlines()
    start = next(i for i, l in enumerate(lines) if "---- park ----" in l)
    end = next(i for i, l in enumerate(lines) if "M30" in l)
    return lines[start:end + 1]


def test_park_block_retracts_in_machine_coordinates():
    # The per-tool retract leaves G49 active. A G54 Z here would target the
    # gauge line, dropping the head about one tool length below the number, and
    # the next line traverses the full length of the bed at that height.
    p = _placed(TWO_PASS_T2_T4, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    park = _park_block(result)
    assert any(re.search(r"\bG0?0\b.*\bG53\b.*\bZ0\b", l) for l in park)
    for line in park:
        if re.search(r"Z[-+.\d]", line):
            assert re.search(r"\bG53\b", line), f"G54 Z move in park block: {line}"


def test_park_block_does_not_use_job_safe_z():
    # Regression on the specific number: safe Z is a G54 height and has no
    # meaning once tool length comp is cancelled.
    p = _placed(TWO_PASS_T2_T4, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    assert not any("25.4000" in l for l in _park_block(result))


def test_park_block_safe_with_no_placements():
    # With no tool blocks there is no preceding retract at all, so the park
    # block has to be self-sufficient.
    park = _park_block(generate_master_gcode([], SETTINGS))
    assert any(re.search(r"\bG0?0\b.*\bG53\b.*\bZ0\b", l) for l in park)


def test_every_g43_approach_asserts_g00():
    # On a multi-tool job the previous pass ends in G01, so an unqualified G43
    # approach feeds down to safe Z at cutting rate instead of rapiding.
    p1 = _placed(TWO_PASS_T2_T4, "A", 39, "i1")
    p2 = _placed(TWO_PASS_T2_T4, "A", 26, "i2")
    result = generate_master_gcode([p1, p2], SETTINGS)
    g43 = [l for l in result.splitlines() if re.search(r"\bG43\b", l)]
    assert len(g43) == 2
    for line in g43:
        assert re.search(r"\bG0?0\b", line), f"G43 approach without G00: {line}"


def test_every_g53_z_retract_asserts_g00():
    p1 = _placed(TWO_PASS_T2_T4, "A", 39, "i1")
    p2 = _placed(TWO_PASS_T2_T4, "A", 26, "i2")
    result = generate_master_gcode([p1, p2], SETTINGS)
    retracts = [l for l in result.splitlines()
                if re.search(r"\bG53\b", l) and re.search(r"\bZ", l)]
    assert len(retracts) == 4   # header, two per-tool retracts, park
    for line in retracts:
        assert re.search(r"\bG0?0\b", line), f"G53 retract without G00: {line}"


def test_no_placements_produces_header_and_park():
    result = generate_master_gcode([], SETTINGS)
    assert "(MASTER JOB" in result
    assert "M30" in result
    assert result.count("M06") == 0  # no tool changes


# ── _build_blocks (unit) ──────────────────────────────────────────────────────

def test_build_blocks_merges_same_tool():
    p1 = _placed(SINGLE_T2, "A", 39, "i1")
    p2 = _placed(SINGLE_T2, "A", 26, "i2")
    blocks = _build_blocks([p1, p2])
    assert len(blocks) == 1
    assert blocks[0]["tool"] == "T2"
    assert len(blocks[0]["segments"]) == 2


def test_build_blocks_two_passes():
    p1 = _placed(TWO_PASS_T2_T4, "A", 39, "i1")
    p2 = _placed(TWO_PASS_T2_T4, "A", 26, "i2")
    blocks = _build_blocks([p1, p2])
    assert len(blocks) == 2
    assert blocks[0]["tool"] == "T2"
    assert blocks[1]["tool"] == "T4"
    assert len(blocks[0]["segments"]) == 2
    assert len(blocks[1]["segments"]) == 2


# ── block_tool_sequence ───────────────────────────────────────────────────────
#
# The tool-change count comes from this list, so it has to track _build_blocks
# exactly. A count derived independently is how the undercount in issue #7 got in.

@pytest.mark.parametrize("placed_args", [
    [(SINGLE_T2, "A", 39), (SINGLE_T2, "A", 26)],
    [(TWO_PASS_T2_T4, "A", 39), (TWO_PASS_T2_T4, "A", 26)],
    [(THREE_PASS_T2_T4_T2, "A", 39), (TWO_PASS_T2_T4, "A", 26)],
    [(THREE_PASS_T2_T4_T2, "A", 39), (SINGLE_T2, "B", 26)],
    [(TWO_PASS_T2_T4, "A", 39)],
])
def test_block_tool_sequence_matches_built_blocks(placed_args):
    placements = [
        _placed(nc, rail, slot, f"i{n}")
        for n, (nc, rail, slot) in enumerate(placed_args)
    ]
    assert block_tool_sequence(placements) == [
        b["tool"] for b in _build_blocks(placements)
    ]


def test_block_tool_sequence_empty_with_no_placements():
    assert block_tool_sequence([]) == []


def test_block_tool_sequence_repeats_a_recurring_tool():
    # T2, T4, T2 — two distinct tools, three blocks, three tool changes.
    p = _placed(THREE_PASS_T2_T4_T2, "A", 39, "i1")
    assert block_tool_sequence([p]) == ["T2", "T4", "T2"]


def test_block_tool_sequence_counts_every_emitted_tool_change():
    # The count has to equal the T# M06 lines the machine will actually see.
    placements = [
        _placed(THREE_PASS_T2_T4_T2, "A", 39, "i1"),
        _placed(TWO_PASS_T2_T4, "A", 26, "i2"),
    ]
    seq = block_tool_sequence(placements)
    gcode = generate_master_gcode(placements, SETTINGS)
    assert len(re.findall(r"^N\d+\s+T\d+ M06\b", gcode, re.MULTILINE)) == len(seq)


# ── H follows T ───────────────────────────────────────────────────────────────
#
# The control is assumed to honour G43 H# as a register index rather than
# substituting a live measured length (issue #5). Under that assumption a stray H
# is a wrong Z, so every block's H has to match the tool it just changed to —
# including a tool that recurs, where the second block must re-issue its own H.

def test_every_block_asserts_the_h_of_the_tool_it_just_changed_to():
    placements = [
        _placed(THREE_PASS_T2_T4_T2, "A", 39, "i1"),
        _placed(TWO_PASS_T2_T4, "A", 26, "i2"),
    ]
    gcode = generate_master_gcode(placements, SETTINGS)

    pairs, pending = [], None
    for line in gcode.splitlines():
        t = re.search(r"\bT(\d+) M06\b", line)
        if t:
            pending = t.group(1)
            continue
        h = re.search(r"\bG43 H(\d+)\b", line)
        if h:
            pairs.append((pending, h.group(1)))
            pending = None

    assert pairs, "no G43 blocks emitted"
    assert len(pairs) == len(block_tool_sequence(placements))
    assert all(t == h for t, h in pairs), pairs


# ── vertical-plane (G18/G19) ramp arcs ────────────────────────────────────────
#
# VCarve emits lead-in/lead-out ramps as arcs in a vertical plane: G19 (file YZ)
# when the ramp runs along file Y, G18 (file XZ) when it runs along file X. The
# X↔Y axis swap moves those arcs into the other vertical plane, so the plane word
# must swap with them. Arc direction follows the plane's NORMAL axis, which is
# why the two rails disagree per plane — verified against transformed arc points
# in the geometry check that accompanies this change.

A_PARAMS = {"b_x": True,  "x": LINE_Y_CONST, "b_y": False, "y": LINE_X_CONST}
B_PARAMS = {"b_x": False, "x": LINE_Y_CONST, "b_y": True,  "y": LINE_FAR_X}


def test_g19_ramp_becomes_g18_on_both_rails():
    # File YZ arc → machine XZ arc. Emitting G19 next to a swapped X word is the
    # illegal block that alarmed the control mid-job.
    for params in (A_PARAMS, B_PARAMS):
        result = _transform_line("G19 G03 Y-5.715 Z0. J1.27 F7620.", params, "G19")
        assert "G18" in result and "G19" not in result
        assert re.search(r"X[-\d.]", result) and "Y" not in result
        assert re.search(r"I[-\d.]", result) and "J" not in result


def test_g18_ramp_becomes_g19_on_both_rails():
    for params in (A_PARAMS, B_PARAMS):
        result = _transform_line("G18 G02 X-3.203 Z12.7 I1.27 F3810.", params, "G18")
        assert "G19" in result and "G18" not in result
        assert re.search(r"Y[-\d.]", result) and "X" not in result
        assert re.search(r"J[-\d.]", result) and "I" not in result


def test_g19_ramp_direction_flips_on_a_rail_only():
    # G19's normal is file X, which maps to machine Y: reversed on A, not on B.
    assert "G02" in _transform_line("G19 G03 Y-5.715 Z0. J1.27", A_PARAMS, "G19")
    assert "G03" in _transform_line("G19 G03 Y-5.715 Z0. J1.27", B_PARAMS, "G19")


def test_g18_ramp_direction_flips_on_b_rail_only():
    # G18's normal is file Y, which maps to machine X: reversed on B, not on A.
    assert "G02" in _transform_line("G18 G02 X-3.203 Z12.7 I1.27", A_PARAMS, "G18")
    assert "G03" in _transform_line("G18 G02 X-3.203 Z12.7 I1.27", B_PARAMS, "G18")


def test_g17_arc_direction_unaffected_by_plane_argument():
    # Both rails are proper rotations, so XY arcs keep their direction. This is
    # the pre-existing rule and must not regress.
    for params in (A_PARAMS, B_PARAMS):
        assert "G02" in _transform_line("G02 X50 Y50 I10 J0", params, "G17")
        assert "G03" in _transform_line("G03 X50 Y50 I-10 J5", params, "G17")


def test_transform_body_tracks_modal_plane_across_lines():
    # The plane word is modal: a following arc with no plane word of its own
    # still belongs to the vertical plane and must be rewritten as one.
    body = [
        "G19 G03 Y-5.715 Z0. J1.27",
        "G03 Y-4.445 Z-1.27 J1.27",   # same plane, no plane word
        "G17",
        "G03 X79.375 Y-3.175 I-1.27",
    ]
    out = _transform_body(body, A_PARAMS)
    assert "G18" in out[0]
    # Still in the vertical plane: file J → output I, direction flipped as above.
    assert "G02" in out[1] and re.search(r"I[-\d.]", out[1]) and "J" not in out[1]
    assert out[2] == "G17"
    assert "G03" in out[3]  # XY arc, direction preserved


def test_transform_body_restores_g17_when_body_ends_vertical():
    # Segments get reordered by the travel sort, so each must leave the control
    # in G17 for the next one to start from a known plane.
    out = _transform_body(["G19 G03 Y-5.715 Z0. J1.27"], B_PARAMS)
    assert out[-1] == "G17"


def test_reported_alarm_line_regression():
    # The exact block that alarmed the SS2 (nest_20260814_090009.nc N186510),
    # from Vacuum Puck.nc N3350 placed on the B rail at slot 0.
    params = _transform_params(
        PlacedPart(part=parse_vcarve_text(SINGLE_T2, filename="p.nc"),
                   rail="B", slot_inches=0, instance_id="i1"))
    result = _transform_line("G19 G03 Y-5.715 Z0. I0. J1.27 F7620.", params, "G19")
    assert result.startswith("G18 G03")
    assert "X1539.8750" in result    # B_X - (-5.715)
    assert "I-1.2700" in result      # file J → output I, negated (b_y mirrored)
    assert "G19" not in result and "Y" not in result


def test_generated_output_has_no_illegal_plane_axis_pairs():
    # Invariant: a G18 block never carries a Y/J word and a G19 block never
    # carries an X/I word. That pairing is what the control rejects.
    ramp_nc = (
        "( Material Size)\n( X= 200.0, Y= 100.0, Z= 19.05)\n"
        "(T2 = End Mill {0.5 inches})\n"
        "T2 M06\n(Tool: End Mill {0.5 inches})\nG43 H2 Z44.4754\nM03 S18000\n"
        "G00 X80.645 Y-6.985\nG01 Z1.27 F2540.\n"
        "G19 G03 Y-5.715 Z0. J1.27 F7620.\n"
        "G01 Y-4.445\n"
        "G17 G03 X79.375 Y-3.175 I-1.27\n"
        "G18 G02 X-3.203 Z12.7 I1.27\n"
        "G17\n"
        "G53 G49 Z0\nM05\nM30\n%\n"
    )
    text = generate_master_gcode(
        [_placed(ramp_nc, "A", 13, "i1"), _placed(ramp_nc, "B", 26, "i2")],
        SETTINGS)
    plane = "G17"
    for line in text.splitlines():
        if line.startswith("("):
            continue
        m = re.search(r"\bG1([789])\b", line)
        if m:
            plane = "G1" + m.group(1)
        if plane == "G18":
            assert not re.search(r"[YJ][-\d.]", line), f"Y/J word in G18 block: {line}"
        elif plane == "G19":
            assert not re.search(r"[XI][-\d.]", line), f"X/I word in G19 block: {line}"


# ── comment syntax ────────────────────────────────────────────────────────────
#
# A comment ends at the first ')', with no nesting and no escape, so anything
# this file interpolates into one has to survive that rule. The strings involved
# are all written by somebody else — the operator's job name, the source file
# names, `driven_by`, the CAM tool comment — and the failure lands as an alarm
# mid-program, after the operator has already started the job.

def _comment_lines(text):
    return [l for l in text.splitlines() if l.lstrip().startswith("(")]


def _every_comment_is_readable(text):
    """The whole file, checked the way the control reads it: one pass, one depth."""
    for line in text.splitlines():
        depth = 0
        for ch in line:
            if ch == "(":
                assert depth == 0, f"nested '(' in: {line}"
                depth = 1
            elif ch == ")":
                assert depth == 1, f"stray ')' in: {line}"
                depth = 0
        assert depth == 0, f"comment never closed: {line}"


def test_comment_wraps_parens_as_brackets():
    assert comment("Job: (9) 18G Test") == "(Job: [9] 18G Test)"


def test_comment_keeps_whitespace_exactly_as_passed():
    # The header aligns its fields with runs of spaces, and the pass banner pads
    # inside the parens. Sanitising must not reflow either.
    assert comment(" ---- T2 pass 1 ---- ") == "( ---- T2 pass 1 ---- )"
    assert comment("Instances: 9  Tools: T2, T1") == "(Instances: 9  Tools: T2, T1)"


def test_comment_replaces_non_ascii_and_cannot_split_a_block():
    # The .nc is written as UTF-8: an em dash reaches the control as three bytes
    # it has no code page for, and a newline would end the block mid-comment.
    assert comment("2.250\" — driven by 18G.nc") == '(2.250"   driven by 18G.nc)'
    out = comment("first\nsecond")
    assert "\n" not in out and out == "(first second)"


def test_job_name_with_parens_stays_one_comment():
    # "(9) 18G Test" — a real job name, typed by the operator on 2026-08-20. Emitted
    # verbatim it closed the header comment after "(9" and left ") 18G Test)" as code.
    p = _placed(SINGLE_T2, "A", 39)
    text = generate_master_gcode([p], {**SETTINGS, "job_name": "(9) 18G Test"})
    assert "(Job: [9] 18G Test)" in text
    _every_comment_is_readable(text)


def test_safe_z_driver_with_parens_stays_one_comment():
    # `_compute_job_safe_z` writes its own driver as "18G.nc (retract)", so this one
    # is the app breaking its own header without any operator input at all.
    p = _placed(SINGLE_T2, "A", 39)
    text = generate_master_gcode([p], {
        **SETTINGS,
        "job_safe_z": {"value": 57.15, "driven_by": "18G.nc (retract)"},
    })
    assert "(Safe Z: 2.250\" - driven by 18G.nc [retract])" in text
    _every_comment_is_readable(text)


def test_tool_description_with_parens_stays_one_comment():
    # `_tool_comment` reads up to the first ')', so a CAM tool named
    # "End Mill (3 flute)" hands the block an unbalanced description.
    nc = SINGLE_T2.replace("(Tool: End Mill {0.5 inches})",
                           "(Tool: End Mill (3 flute) {0.5 inches})")
    text = generate_master_gcode([_placed(nc, "A", 39)], SETTINGS)
    _every_comment_is_readable(text)
    assert "3 flute" in text


def test_a_malformed_source_comment_is_repaired_rather_than_copied_through():
    # Body comments pass through byte-for-byte, which is right until the source
    # file wrote one the control cannot read. A toolpath named "Pocket (2)" in
    # VCarve is the live case; the master is the file that gets run, so it is the
    # one that has to be readable.
    params = _transform_params(
        PlacedPart(part=parse_vcarve_text(SINGLE_T2, filename="p.nc"),
                   rail="A", slot_inches=13, instance_id="i1"))
    assert _transform_line("(POCKET X2)", params) == "(POCKET X2)"
    assert _transform_line("(A) (B)", params) == "(A) (B)"          # two comments, fine
    repaired = _transform_line("(POCKET (2))", params)
    assert comment_is_wellformed(repaired) and "2" in repaired


def test_generated_output_passes_the_validator_comment_check():
    # The two layers meet here: the generator sanitises, and the validator — which
    # shares none of that code — is what says whether it did.
    text = generate_master_gcode(
        [_placed(SINGLE_T2, "A", 13, "i1"), _placed(TWO_PASS_T2_T4, "B", 26, "i2")],
        {**SETTINGS, "job_name": "(9) 18G Test",
         "job_safe_z": {"value": 57.15, "driven_by": "18G.nc (retract)"}})
    findings = validate_gcode(text, SETTINGS["advanced"])
    assert [f for f in findings if f.check == "comment-syntax"] == []
    _every_comment_is_readable(text)
    assert _comment_lines(text)   # the check above is vacuous on a file with none
