"""
Tests for gcode_generator.py — master G-code builder.

Fixtures use parse_vcarve_text so the passes list is populated exactly as it
would be in production.
"""
import re
import pytest
from gcode_parser import parse_vcarve_text
from collision import PlacedPart
from gcode_generator import (
    generate_master_gcode,
    _build_blocks,
    _extract_body,
    _transform_line,
    _transform_params,
    _nearest_neighbor_sort,
    _first_xy,
    _last_xy,
)

# ── test config ───────────────────────────────────────────────────────────────

RAIL_W = 82.55
BED_X = 1524.0

SETTINGS = {
    "job_name": "test_job",
    "job_safe_z": {"value": 25.4, "driven_by": "part.nc"},
    "advanced": {
        "rail_width_mm": RAIL_W,
        "bed_x_mm": BED_X,
        "bed_y_mm": 3048.0,
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


# ── _transform_line ───────────────────────────────────────────────────────────

def test_transform_a_rail_adds_offset():
    # A rail: b_x=True, b_y=False
    # file X (VCarve X) → machine Y: slot_mark - vx → output Y word
    # file Y (VCarve Y) → machine X: rail_w + vy  → output X word
    # slot_mark=2057.4, rail_w=82.55
    # X50 → Y(2057.4 - 50) = Y2007.4;  Y100 → X(82.55 + 100) = X182.55
    slot_mark = (120 - 39) * 25.4  # 2057.4
    params = {"b_x": True, "x": slot_mark, "b_y": False, "y": RAIL_W}
    result = _transform_line("G01 X50 Y100 Z-0.254", params)
    assert "Y2007.4000" in result   # file X=50 → machine Y
    assert "X182.5500" in result    # file Y=100 → machine X
    assert "Z-0.254" in result      # Z unchanged


def test_transform_b_rail_mirrors():
    # B rail: b_x=True, b_y=True (both mirrored)
    # file X → machine Y: x_const - vx → output Y word
    # file Y → machine X: y_const - vy → output X word
    params = {"b_x": True, "x": BED_X - RAIL_W, "b_y": True, "y": 2100.0}
    result = _transform_line("G01 X50 Y30 Z-0.254", params)
    assert "Y1391.4500" in result  # file X=50 → machine Y = (BED_X-RAIL_W) - 50
    assert "X2070.0000" in result  # file Y=30 → machine X = 2100 - 30


def test_transform_b_rail_swaps_g02_to_g03():
    # B rail: both axes mirrored + axis-swap = 3 total flips (odd) → orientation reversed → swap
    params = {"b_x": True, "x": 1441.45, "b_y": True, "y": 2100.0}
    result = _transform_line("G02 X50 Y50 I10 J0 Z-0.254", params)
    assert "G03" in result   # G02 swapped to G03 on B rail
    assert "G02" not in result


def test_transform_b_rail_swaps_g03_to_g02():
    # B rail: orientation reversed → G03 becomes G02
    params = {"b_x": True, "x": 1441.45, "b_y": True, "y": 2100.0}
    result = _transform_line("G03 X50 Y50 I-10 J5 Z-0.254", params)
    assert "G02" in result
    assert "G03" not in result


def test_transform_a_rail_no_arc_swap():
    # A rail: one axis mirrored + axis-swap = 2 total flips (even) → orientation preserved → no swap
    slot_mark = (120 - 39) * 25.4
    params = {"b_x": True, "x": slot_mark, "b_y": False, "y": RAIL_W}
    result_cw = _transform_line("G02 X50 Y50 I10 J0 Z-0.254", params)
    result_ccw = _transform_line("G03 X50 Y50 I-10 J5 Z-0.254", params)
    assert "G02" in result_cw and "G03" not in result_cw
    assert "G03" in result_ccw and "G02" not in result_ccw


def test_transform_b_rail_negates_ij():
    # Both axes mirrored.
    # file I20 (VCarve-X direction) → machine-Y direction → output J, negated: J-20
    # file J-5 (VCarve-Y direction) → machine-X direction → output I, negated: I5
    params = {"b_x": True, "x": 1441.45, "b_y": True, "y": 2100.0}
    result = _transform_line("G02 X50 Y50 I20 J-5 Z-0.254", params)
    assert "J-20.0000" in result  # file I20, x_mirror → output J=-20
    assert "I5.0000" in result    # file J-5, y_mirror → output I=5


def test_transform_comment_unchanged():
    params = {"b_x": False, "x": RAIL_W, "b_y": False, "y": 2000.0}
    line = "(Tool: End Mill {0.5 inches})"
    assert _transform_line(line, params) == line


def test_transform_g53_unchanged():
    params = {"b_x": False, "x": RAIL_W, "b_y": False, "y": 2000.0}
    line = "G53 G49 Z0"
    assert _transform_line(line, params) == line


def test_transform_a_rail_z_unchanged():
    params = {"b_x": True, "x": 2057.4, "b_y": False, "y": RAIL_W}
    result = _transform_line("G01 X10 Y10 Z18.796", params)
    assert "Z18.796" in result


# ── _transform_params ─────────────────────────────────────────────────────────

def test_transform_params_a_rail():
    p = _placed(SINGLE_T2, "A", 39)
    params = _transform_params(p, RAIL_W, BED_X)
    slot_mark = (120 - 39) * 25.4
    assert params["b_x"] is True
    assert params["b_y"] is False
    assert params["x"] == pytest.approx(slot_mark)
    assert params["y"] == pytest.approx(RAIL_W)


def test_transform_params_b_rail():
    p = _placed(SINGLE_T2, "B", 39)
    params = _transform_params(p, RAIL_W, BED_X)
    slot_mark = (120 - 39) * 25.4
    assert params["b_x"] is True
    assert params["b_y"] is True
    assert params["x"] == pytest.approx(slot_mark + p.part.vcarve_x_span)
    assert params["y"] == pytest.approx(BED_X - RAIL_W)


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
    assert "G21 (metric)" in result
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


# ── coordinate transformation in output ──────────────────────────────────────

def test_a_rail_coordinates_offset_in_output():
    # SINGLE_T2 has vcarve_x_span=200, vcarve_y_span=100
    # A rail slot 39: slot_mark=2057.4, rail_w=82.55
    # G00 X0 Y0 in file:
    #   file X=0 (VCarve X) → machine Y = slot_mark - 0 = 2057.4 → output Y word
    #   file Y=0 (VCarve Y) → machine X = rail_w + 0 = 82.55    → output X word
    p = _placed(SINGLE_T2, "A", 39)
    result = generate_master_gcode([p], SETTINGS)
    slot_mark = (120 - 39) * 25.4  # 2057.4
    assert f"Y{slot_mark:.4f}" in result   # file X=0 → machine Y
    assert f"X{RAIL_W:.4f}" in result      # file Y=0 → machine X


def test_b_rail_coordinates_mirrored_in_output():
    # SINGLE_T2: vcarve_x_span=200, vcarve_y_span=100
    # B rail slot 39:
    #   machine-Y const = slot_mark + vcarve_x_span = 2057.4+200 = 2257.4
    #   machine-X const = BED_X - RAIL_W = 1441.45
    # G00 X0 Y0 in file:
    #   file X=0 → machine Y = 2257.4 - 0 = 2257.4 → output Y word
    #   file Y=0 → machine X = 1441.45 - 0 = 1441.45 → output X word
    p = _placed(SINGLE_T2, "B", 39)
    result = generate_master_gcode([p], SETTINGS)
    slot_mark = (120 - 39) * 25.4
    vcarve_x_span = p.part.vcarve_x_span  # 200.0
    mach_y_const = slot_mark + vcarve_x_span   # 2257.4
    mach_x_const = BED_X - RAIL_W              # 1441.45
    assert f"Y{mach_y_const:.4f}" in result    # file X=0 → machine Y
    assert f"X{mach_x_const:.4f}" in result    # file Y=0 → machine X


def test_b_rail_arcs_swapped_in_output():
    p = _placed(ARC_NC, "B", 39)
    result = generate_master_gcode([p], SETTINGS)
    # Original G02 should become G03 and vice versa in the output body
    tool_block_start = result.index("T2 M06")
    tool_block = result[tool_block_start:]
    assert "G03" in tool_block  # G02 was flipped
    assert "G02" in tool_block  # G03 was flipped


# ── pass merging (order of operations) ───────────────────────────────────────

def test_two_parts_same_tool_merged():
    """Two parts both using T2 → one T2 M06 in output."""
    p1 = _placed(SINGLE_T2, "A", 39, "i1")
    p2 = _placed(SINGLE_T2, "A", 26, "i2")
    result = generate_master_gcode([p1, p2], SETTINGS)
    assert result.count("T2 M06") == 1


def test_two_parts_same_tool_has_retract_between_segments():
    """Within a tool block, a G00 Z[safe_z] separates part segments; one also precedes park."""
    p1 = _placed(SINGLE_T2, "A", 39, "i1")
    p2 = _placed(SINGLE_T2, "A", 26, "i2")
    result = generate_master_gcode([p1, p2], SETTINGS)
    # job_safe_z = 25.4: one between segments, one before park = 2 total
    assert result.count("G00 Z25.4000") == 2


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


def test_no_placements_produces_header_and_park():
    result = generate_master_gcode([], SETTINGS)
    assert "(MASTER JOB" in result
    assert "M30" in result
    assert result.count("M06") == 0  # no tool changes


# ── _build_blocks (unit) ──────────────────────────────────────────────────────

def test_build_blocks_merges_same_tool():
    p1 = _placed(SINGLE_T2, "A", 39, "i1")
    p2 = _placed(SINGLE_T2, "A", 26, "i2")
    blocks = _build_blocks([p1, p2], RAIL_W, BED_X)
    assert len(blocks) == 1
    assert blocks[0]["tool"] == "T2"
    assert len(blocks[0]["segments"]) == 2


def test_build_blocks_two_passes():
    p1 = _placed(TWO_PASS_T2_T4, "A", 39, "i1")
    p2 = _placed(TWO_PASS_T2_T4, "A", 26, "i2")
    blocks = _build_blocks([p1, p2], RAIL_W, BED_X)
    assert len(blocks) == 2
    assert blocks[0]["tool"] == "T2"
    assert blocks[1]["tool"] == "T4"
    assert len(blocks[0]["segments"]) == 2
    assert len(blocks[1]["segments"]) == 2
