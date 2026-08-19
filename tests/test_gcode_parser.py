import math
import pathlib

import pytest
from gcode_parser import (
    GcodePass, _arc_points, extract_file_segments, file_is_inch,
    operation_name_in_comment, parse_vcarve_text, validate_z,
)
from tool_library import code_in_file_tool

# --- fixtures ---

SAMPLE_VCARVE = """(VECTRIC POST REVISION)
(hash)
(filename)
(CREATED date)
( Material Size)
( X= 1676.400, Y= 3200.400, Z= 31.877)
(Tools used in this file: )
(T2 = End Mill {0.5 inches})
G00 X0 Y0
G01 X100 Y200
G02 X200 Y300
"""

# Real-file spoilboard-referenced: thin overtravel only
SAMPLE_SPOILBOARD = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(Tools used in this file: )
(T2 = End Mill {0.5 inches})
G43 H2 Z44.4754
T2 M06
G00 X0 Y0
G01 X10 Y10 Z18.796
G01 X50 Y50 Z-0.254
G01 X100 Y100 Z-0.254
G53 G49 Z0
M05
M30
"""

# Legacy top-of-material convention — should be blocked
SAMPLE_LEGACY = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(T2 = End Mill {0.5 inches})
G43 H2 Z25.4254
T2 M06
G00 X0 Y0
G01 X50 Y50 Z-19.304
G53 G49 Z0
M05
M30
"""

# Two-pass file: T2 then T4
SAMPLE_TWO_PASS = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(T2 = End Mill {0.5 inches})
(T4 = Table Stiff {0.75 inches})
G43 H2 Z44.4754
T2 M06
G00 X0 Y0
G01 X50 Y50 Z-0.254
G53 G49 Z0
M05
T4 M06
G00 X0 Y0
G01 X100 Y100 Z-0.254
G53 G49 Z0
M05
M30
"""

# Shallow-cut file (pocket only — warning, not blocked)
SAMPLE_SHALLOW = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(T2 = End Mill {0.5 inches})
G43 H2 Z44.4754
T2 M06
G00 X0 Y0
G01 X50 Y50 Z12.0
G53 G49 Z0
M05
M30
"""

# Safe Z too low — should be blocked
SAMPLE_LOW_SAFE_Z = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(T2 = End Mill {0.5 inches})
G43 H2 Z15.0
T2 M06
G00 X0 Y0
G01 X50 Y50 Z-0.254
G53 G49 Z0
M05
M30
"""

# Missing Material Size header
SAMPLE_NO_HEADER = """(T2 = End Mill {0.5 inches})
G00 X0 Y0
G01 X50 Y50 Z-0.254
M30
"""


# --- existing tests (unchanged) ---

def test_parse_vcarve_text_extracts_blank_and_material():
    part = parse_vcarve_text(SAMPLE_VCARVE, filename="sample.nc")

    assert part.filename == "sample.nc"
    assert part.vcarve_x_span == 1676.4
    assert part.vcarve_y_span == 3200.4
    assert part.material_thickness == 31.877


def test_parse_vcarve_text_extracts_tool_from_header():
    part = parse_vcarve_text(SAMPLE_VCARVE)

    assert "T2" in part.tools
    assert part.tools["T2"]["diameter_inches"] == 0.5


def test_parse_vcarve_text_scans_coordinates():
    part = parse_vcarve_text(SAMPLE_VCARVE)

    assert part.min_vx == 0.0
    assert part.max_vx == 200.0
    assert part.min_vy == 0.0
    assert part.max_vy == 300.0


# --- modal coordinate tests ---

def test_modal_coordinates_carry_y_forward():
    gcode = """( Material Size)
( X= 500, Y= 500, Z= 19.0)
G00 X0 Y10
G01 X100
G01 X200 Y50
"""
    part = parse_vcarve_text(gcode)
    # After G01 X100 (no Y), machine is at (100, 10) — must be included in bbox
    assert part.min_vx == 0.0
    assert part.max_vx == 200.0
    assert part.min_vy == 10.0
    assert part.max_vy == 50.0


def test_modal_coordinates_carry_x_forward():
    gcode = """( Material Size)
( X= 500, Y= 500, Z= 19.0)
G00 X50 Y0
G01 Y100
"""
    part = parse_vcarve_text(gcode)
    # G01 Y100 (no X) — machine moves to (50, 100)
    assert part.min_vx == 50.0
    assert part.max_vx == 50.0
    assert part.min_vy == 0.0
    assert part.max_vy == 100.0


def test_machine_coord_moves_excluded_from_bbox():
    gcode = """( Material Size)
( X= 500, Y= 500, Z= 19.0)
G00 X0 Y0
G01 X100 Y100
G53 X0 Y3048
"""
    part = parse_vcarve_text(gcode)
    # G53 line should not affect bounding box
    assert part.max_vx == 100.0
    assert part.max_vy == 100.0


# --- Z scanning tests ---

def test_z_scan_extracts_min_max_and_safe_z():
    part = parse_vcarve_text(SAMPLE_SPOILBOARD)

    assert part.safe_z == pytest.approx(44.4754)
    assert part.min_z == pytest.approx(-0.254)
    assert part.max_z == pytest.approx(18.796)


def test_z_scan_takes_the_highest_of_several_retracts():
    """Fusion writes one G43 Z per operation and they differ per tool (#22).

    Modelled on 18G.nc: T2 retracts to 34.29, T1 to 57.15 because it cuts a
    feature standing above the stock. Reading either one alone gives a clearance
    that the other tool crashes through.
    """
    gcode = """( Material Size)
( X= 300.0, Y= 450.0, Z= 19.05)
(T1 D=12.7 CR=6.35 - ZMIN=14.605 - BALL END MILL)
(T2 D=12.7 CR=0. - ZMIN=0. - FLAT END MILL)
T2 M06
G43 Z34.29 H02
G01 X10 Y10 Z-1.0
T1 M06
G43 Z57.15 H01
G01 X20 Y20 Z14.605
M30
"""
    part = parse_vcarve_text(gcode)
    assert part.safe_z == pytest.approx(57.15)


def test_z_scan_ignores_g53_lines():
    gcode = """( Material Size)
( X= 500, Y= 500, Z= 19.0)
G43 H2 Z44.0
T2 M06
G01 X10 Y10 Z-0.254
G53 Z0
M30
"""
    part = parse_vcarve_text(gcode)
    # G53 Z0 must not count as a cutting Z — min_z should be -0.254
    assert part.min_z == pytest.approx(-0.254)


def test_z_scan_none_when_no_cutting_z():
    part = parse_vcarve_text(SAMPLE_VCARVE)

    assert part.min_z is None
    assert part.max_z is None


# --- Z validation tests (real-file cases from spec) ---

def test_z_validation_passes_spoilboard_file():
    part = parse_vcarve_text(SAMPLE_SPOILBOARD)

    assert part.z_validation.status == "ok"
    assert part.z_validation.messages == []


def test_z_validation_blocks_legacy_file():
    part = parse_vcarve_text(SAMPLE_LEGACY)

    assert part.z_validation.status == "blocked"
    assert "top-of-material" in part.z_validation.messages[0]


def test_z_validation_blocks_missing_material_header():
    part = parse_vcarve_text(SAMPLE_NO_HEADER)

    assert part.z_validation.status == "blocked"
    assert "Material Size header" in part.z_validation.messages[0]


def test_z_validation_warns_shallow_cut():
    part = parse_vcarve_text(SAMPLE_SHALLOW)

    assert part.z_validation.status == "warning"
    assert "less than half" in part.z_validation.messages[0]


def test_z_validation_blocks_low_safe_z():
    part = parse_vcarve_text(SAMPLE_LOW_SAFE_Z)

    assert part.z_validation.status == "blocked"
    assert "Safe Z height" in part.z_validation.messages[0]


def test_z_validation_no_cutting_z_is_ok():
    # File with no cutting Z values (e.g. only header lines) should not be blocked
    part = parse_vcarve_text(SAMPLE_VCARVE)

    assert part.z_validation.status == "ok"


# --- validate_z unit tests for known real files from spec ---

@pytest.mark.parametrize("material_z,min_z,safe_z,expected_status", [
    (19.05,  -0.254,  44.4754, "ok"),       # 18G.NC
    (50.80,  -0.254,  76.2254, "ok"),       # 24EG.NC
    (19.05,  -0.254,  44.4754, "ok"),       # 24G.NC corrected
    (31.75,   0.000,  57.1754, "ok"),       # 603060-A.NC (no overtravel)
    (19.05, -19.304,  25.4254, "blocked"),  # 24G.NC legacy
    (31.877, -31.877, 25.4254, "blocked"),  # 969034Table.NC legacy
])
def test_z_validation_known_real_files(material_z, min_z, safe_z, expected_status):
    result = validate_z(min_z, safe_z, material_z)
    assert result.status == expected_status


# --- pass extraction tests ---

def test_extract_passes_single_tool():
    part = parse_vcarve_text(SAMPLE_SPOILBOARD)

    assert len(part.passes) == 1
    assert part.passes[0].tool_number == "T2"
    assert part.passes[0].pass_index == 0


def test_extract_passes_two_tools():
    part = parse_vcarve_text(SAMPLE_TWO_PASS)

    assert len(part.passes) == 2
    assert part.passes[0].tool_number == "T2"
    assert part.passes[0].pass_index == 0
    assert part.passes[1].tool_number == "T4"
    assert part.passes[1].pass_index == 1


def test_extract_passes_lines_assigned_correctly():
    part = parse_vcarve_text(SAMPLE_TWO_PASS)

    t2_pass = part.passes[0]
    t4_pass = part.passes[1]

    # Each pass starts with its own T# M06 line
    assert "T2 M06" in t2_pass.lines[0]
    assert "T4 M06" in t4_pass.lines[0]

    # T2 pass should not contain T4 lines
    assert all("T4" not in ln for ln in t2_pass.lines)


def test_extract_passes_empty_when_no_tool_change():
    part = parse_vcarve_text(SAMPLE_VCARVE)

    assert part.passes == []


# --- toolpath name capture ---
#
# Both posts write the toolpath name as a bare comment on the line ABOVE the
# tool change, which puts it outside the pass extract_passes builds. Operations
# after the first within a pass carry their names inline and survive on their
# own, so without this capture the first operation of every pass is the only
# one that reaches the master file unnamed.

OP_NAME_NC = (
    "( Material Size)\n( X= 200.0, Y= 100.0, Z= 19.05)\n"
    "(T2 = End Mill {0.5 inches})\n"
    "(T4 = Table Stiff {0.75 inches})\n"
    "N10 G90 G94 G17 G49 G40 G80\n"
    "N25 G90\n"
    "\n"
    "(TABLE OUTSIDE PROFILE ADAPTIVE)\n"
    "N30 T2 M06\n"
    "N35 S18000 M03\n"
    "N50 G00 X0 Y0\n"
    "N55 G01 X50 Y50 Z-0.254\n"
    "N60 M05\n"
    "N65 G90\n"
    "\n"
    "(TABLE STIFF)\n"
    "N70 M01\n"
    "N75 T4 M06\n"
    "N80 S18000 M03\n"
    "N85 G01 X10 Y10 Z-0.254\n"
    "N90 M30\n"
)


def test_operation_name_captured_from_line_above_tool_change():
    part = parse_vcarve_text(OP_NAME_NC, filename="p.nc")
    assert part.passes[0].operation_name == "TABLE OUTSIDE PROFILE ADAPTIVE"


def test_operation_name_survives_intervening_optional_stop():
    # Every tool change after the first has an M01 between the name and the
    # T# M06. It commands no motion, so it must not break the association.
    part = parse_vcarve_text(OP_NAME_NC, filename="p.nc")
    assert part.passes[1].operation_name == "TABLE STIFF"


def test_tool_comment_is_not_an_operation_name():
    # The Vectric post puts the tool comment directly above the tool change and
    # no toolpath name at all. That comment names the cutter, not the toolpath.
    src = (
        "( Material Size)\n( X= 200.0, Y= 100.0, Z= 19.05)\n"
        "(T2 = End Mill {0.5 inch})\n"
        "N11 G53 Z0\n"
        "N12 (Tool: End Mill {0.5 inch})\n"
        "N13 T2 M06\n"
        "N14 M03 S18000\nN16 G01 X1 Y1 Z-1\nN20 M30\n"
    )
    part = parse_vcarve_text(src, filename="p.nc")
    assert part.passes[0].operation_name == ""


def test_header_comments_are_not_operation_names():
    # A file whose tool change follows the header block directly must not
    # inherit a material-size or tool-table line as its operation name.
    src = (
        "( Material Size)\n( X= 200.0, Y= 100.0, Z= 19.05)\n"
        "(T2 = End Mill {0.5 inches})\n"
        "T2 M06\nM03 S18000\nG01 X1 Y1 Z-1\nM30\n"
    )
    part = parse_vcarve_text(src, filename="p.nc")
    assert part.passes[0].operation_name == ""


def test_code_line_between_name_and_tool_change_breaks_association():
    src = (
        "(SOME EARLIER LABEL)\n"
        "G00 X5 Y5\n"
        "T2 M06\nM03 S18000\nG01 X1 Y1 Z-1\nM30\n"
    )
    part = parse_vcarve_text(src, filename="p.nc")
    assert part.passes[0].operation_name == ""


def test_arc_points_flatten_r_arc_into_multiple_segments():
    points = _arc_points(1.0, 0.0, 0.0, 1.0, r=1.0, clockwise=False)

    assert len(points) >= 4
    assert points[-1] == pytest.approx((0.0, 1.0))
    assert all(math.hypot(x, y) <= 1.0 + 1e-9 for x, y in points)


def test_arc_points_negative_r_selects_major_arc():
    minor = _arc_points(1.0, 0.0, 0.0, 1.0, r=1.0, clockwise=False)
    major = _arc_points(1.0, 0.0, 0.0, 1.0, r=-1.0, clockwise=False)

    # Major arc sweeps further, so it is flattened into more points.
    assert len(major) > len(minor)
    # Minor-arc points lie on the origin circle (center (0,0)).
    assert all(math.hypot(x, y) == pytest.approx(1.0) for x, y in minor)
    # Major-arc points lie on the other radius-1 circle (center (1,1)).
    assert all(math.hypot(x - 1.0, y - 1.0) == pytest.approx(1.0) for x, y in major)


def test_arc_points_degenerate_returns_single_chord():
    pts = _arc_points(5.0, 5.0, 5.0, 5.0, r=2.0, clockwise=False)
    assert pts == [(5.0, 5.0)]


def test_extract_file_segments_flattens_arc_moves():
    passes = [GcodePass(pass_index=0, tool_number="T2", lines=[
        "G00 X0 Y0",
        "G03 X10 Y0 R5",
    ])]

    segments = extract_file_segments(passes, material_thickness=10.0)

    assert len(segments) > 1
    assert segments[0]["x1"] == pytest.approx(0.0)
    assert segments[0]["y1"] == pytest.approx(0.0)
    assert segments[-1]["x2"] == pytest.approx(10.0)
    assert segments[-1]["y2"] == pytest.approx(0.0)
    # Arc sub-segments must chain continuously: each seg's end is the next's start.
    for prev, nxt in zip(segments, segments[1:]):
        assert (prev["x2"], prev["y2"]) == pytest.approx((nxt["x1"], nxt["y1"]))


def test_extract_file_segments_linear_move_unaffected():
    passes = [GcodePass(pass_index=0, tool_number="T2", lines=[
        "G00 X0 Y0",
        "G01 X10 Y10 Z-1",
    ])]

    segments = extract_file_segments(passes, material_thickness=10.0)

    assert len(segments) == 1
    assert segments[0]["x2"] == pytest.approx(10.0)
    assert segments[0]["y2"] == pytest.approx(10.0)


def test_extract_file_segments_arc_inherits_missing_axis():
    # G03 with no Y inherits the current Y (modal), and still flattens.
    passes = [GcodePass(pass_index=0, tool_number="T2", lines=[
        "G00 X0 Y5",
        "G03 X10 R5",
    ])]

    segments = extract_file_segments(passes, material_thickness=10.0)

    # segments[0] is the rapid lead-in (0,0)->(0,5); the arc starts after it.
    assert len(segments) > 2
    assert segments[1]["x1"] == pytest.approx(0.0)
    assert segments[1]["y1"] == pytest.approx(5.0)
    assert segments[-1]["x2"] == pytest.approx(10.0)
    assert segments[-1]["y2"] == pytest.approx(5.0)


def test_extract_file_segments_full_circle_spans_diameter():
    # Two semicircles forming a full circle must not collapse to a flat line.
    passes = [GcodePass(pass_index=0, tool_number="T2", lines=[
        "G00 X0 Y0",
        "G02 X10 Y0 R5",
        "G02 X0 Y0 R5",
    ])]

    segments = extract_file_segments(passes, material_thickness=10.0)

    ys = [s["y1"] for s in segments] + [s["y2"] for s in segments]
    # The circle bulges off the chord — must span roughly the full diameter.
    assert max(ys) - min(ys) == pytest.approx(10.0, abs=0.5)


# --- tool header format tests ---

def _minimal(header_comment):
    return f"( Material Size)\n( X=100, Y=50, Z=19)\n{header_comment}\nT2 M06\nM30\n"


def test_extract_tools_plural_inches_brace():
    part = parse_vcarve_text(_minimal("(T2 = End Mill {0.5 inches})"))
    assert part.tools["T2"]["diameter_inches"] == pytest.approx(0.5)
    assert part.tools["T2"]["description"] == "End Mill {0.5 inches}"


def test_extract_tools_singular_inch_brace():
    part = parse_vcarve_text(_minimal("(T2 = End Mill {0.5 inch})"))
    assert part.tools["T2"]["diameter_inches"] == pytest.approx(0.5)
    assert part.tools["T2"]["description"] == "End Mill {0.5 inch}"


def test_extract_tools_no_brace_dia_suffix():
    part = parse_vcarve_text(_minimal("(T1 = Ball Nose .5 inches Dia)").replace("T2", "T1"))
    assert part.tools["T1"]["diameter_inches"] == pytest.approx(0.5)
    assert part.tools["T1"]["description"] == "Ball Nose .5 inches Dia"


def test_extract_tools_no_diameter_integer_only():
    part = parse_vcarve_text(_minimal("(T5 = ROUNDOVER 125)").replace("T2", "T5"))
    assert part.tools["T5"]["diameter_inches"] is None
    assert part.tools["T5"]["description"] == "ROUNDOVER 125"


def test_extract_tools_description_excludes_tool_prefix():
    part = parse_vcarve_text(_minimal("(T4 = End Mill {.75 inches})").replace("T2", "T4"))
    desc = part.tools["T4"]["description"]
    assert desc == "End Mill {.75 inches}"
    assert "T4" not in desc
    assert "=" not in desc


# ── Fusion tool headers (issue #20) ──────────────────────────────────────────
#
# Before this, `extract_tools` matched only VCarve's "(T2 = ...)" shape, so every
# Fusion-posted file in the library parsed to no tools at all — which meant
# diameter 0 feeding `collision._max_tool_radius`, and therefore an X envelope
# check and a tool-radius collision check that both under-inflated on the
# *primary* corpus. These pin the four variants the post can emit.


def _fusion(header_comment, tool="T2"):
    return f"( Material Size)\n( X=100, Y=50, Z=19)\n{header_comment}\nN15 G71\n{tool} M06\nM30\n"


def test_fusion_header_diameter_is_converted_from_mm():
    part = parse_vcarve_text(_fusion("(T2 D=12.7 CR=0. - ZMIN=0. - FLAT END MILL)"))
    # 12.7 mm is exactly 1/2". Read as inches it would inflate the envelope check
    # by 1/25.4 of what the cutter really needs.
    assert part.tools["T2"]["diameter_inches"] == pytest.approx(0.5)


def test_fusion_header_ball_mill_carries_corner_radius():
    part = parse_vcarve_text(_fusion("(T1 D=12.7 CR=6.35 - ZMIN=14.605 - BALL END MILL)", "T1"))
    info = part.tools["T1"]
    assert info["diameter_inches"] == pytest.approx(0.5)
    assert info["corner_radius_inches"] == pytest.approx(0.25)
    assert info["tool_type"] == "BALL END MILL"
    assert info["taper_degrees"] is None


def test_fusion_header_taper_is_captured():
    part = parse_vcarve_text(_fusion("(T3 D=12.7 CR=0. TAPER=45DEG - ZMIN=18.542 - CHAMFER MILL)", "T3"))
    info = part.tools["T3"]
    assert info["taper_degrees"] == pytest.approx(45.0)
    assert info["tool_type"] == "CHAMFER MILL"
    assert "TAPER=45DEG" in info["description"]


def test_fusion_header_without_zmin_still_parses():
    # ZMIN is written only when the job is 3D, so a 2D-only program omits it.
    part = parse_vcarve_text(_fusion("(T2 D=3.175 CR=3.175 - RADIUS MILL)"))
    info = part.tools["T2"]
    assert info["diameter_inches"] == pytest.approx(0.125)
    assert info["tool_type"] == "RADIUS MILL"


def test_fusion_description_excludes_zmin():
    # The regression this guards is subtle and app-breaking rather than unsafe.
    # ZMIN is the job's Z range, not a property of the cutter, so one physical tool
    # posts ZMIN=0. in one file and ZMIN=-19.05 in another. `_tool_compatibility`
    # flags a conflict when descriptions for one T# differ, so carrying ZMIN would
    # make a tool conflict with itself and block Generate on compatible files.
    a = parse_vcarve_text(_fusion("(T2 D=12.7 CR=0. - ZMIN=0. - FLAT END MILL)"))
    b = parse_vcarve_text(_fusion("(T2 D=12.7 CR=0. - ZMIN=-19.05 - FLAT END MILL)"))
    assert a.tools["T2"]["description"] == b.tools["T2"]["description"]
    assert "ZMIN" not in a.tools["T2"]["description"]


def test_fusion_description_distinguishes_diameters():
    # The opposite direction: two genuinely different cutters must not collapse to
    # one string. Fusion names both "FLAT END MILL", so geometry has to be in there.
    a = parse_vcarve_text(_fusion("(T2 D=12.7 CR=0. - FLAT END MILL)"))
    b = parse_vcarve_text(_fusion("(T2 D=6.35 CR=0. - FLAT END MILL)"))
    assert a.tools["T2"]["description"] != b.tools["T2"]["description"]


def test_fusion_header_is_not_read_as_an_operation_name():
    # A bare comment above a tool change is how a toolpath name is recognised, so
    # the tool list has to be excluded explicitly or it becomes an "operation".
    assert operation_name_in_comment("(T2 D=12.7 CR=0. - ZMIN=0. - FLAT END MILL)") == ""


def test_fusion_diameter_not_rescaled_when_file_declares_inches():
    part = parse_vcarve_text(
        "( Material Size)\n( X=100, Y=50, Z=19)\n"
        "(T2 D=0.5 CR=0. - FLAT END MILL)\nN15 G70\nT2 M06\nM30\n"
    )
    assert part.tools["T2"]["diameter_inches"] == pytest.approx(0.5)


def test_units_word_inside_a_comment_does_not_set_inches():
    # Comment lines are skipped, so free text mentioning G20 cannot rescale a tool
    # by 25.4x in the under-inflating direction.
    assert file_is_inch(["(FACE G20 PASS)", "N15 G71", "T2 M06"]) is False


def test_units_default_is_metric_when_no_units_word():
    assert file_is_inch(["T2 M06", "G00 X1 Y1"]) is False


# ── the TOOLID identity comment (spec §6.2.1) ────────────────────────────────
#
# What `writeToolIdentity` in `post/syntec_Refine.cps` emits. The strings below
# are post-filter: settings.comments uppercases and strips anything outside
# " a-z0-9.,=_-", so '/' and '"' are gone from free text by the time the app reads
# it. That only touches TOOLDESC, which is why identity lives on the other line.


def _with_toolid(*extra):
    body = "\n".join(extra)
    return (
        "( Material Size)\n( X=100, Y=50, Z=19)\n"
        "(T2 D=12.7 CR=0. - FLAT END MILL)\n"
        f"{body}\nN15 G71\nT2 M06\nM30\n"
    )


def test_toolid_identity_is_read_alongside_the_geometry_header():
    part = parse_vcarve_text(_with_toolid("(TOOLID T2 VENDOR=AMANA CODE=EM-0512 FLUTES=3)"))
    info = part.tools["T2"]
    assert info["vendor"] == "AMANA"
    assert info["code"] == "EM-0512"
    assert info["flutes"] == 3
    # The geometry from the header line survives the merge.
    assert info["diameter_inches"] == pytest.approx(0.5)
    assert info["tool_type"] == "FLAT END MILL"


def test_toolid_blank_field_is_distinct_from_a_missing_one():
    # The post emits "VENDOR=" rather than omitting it, because a blank Fusion
    # library entry is something the operator can go and fill in while an older
    # post is not. "" and None must not collapse.
    blank = parse_vcarve_text(_with_toolid("(TOOLID T2 VENDOR= CODE=EM-0512 FLUTES=)"))
    assert blank.tools["T2"]["vendor"] == ""
    assert blank.tools["T2"]["flutes"] is None

    absent = parse_vcarve_text(_with_toolid())
    assert absent.tools["T2"].get("vendor") is None


def test_toolid_blank_code_is_distinct_from_a_missing_one():
    # Same rule, on the field that now carries the identity: "CODE=" says the Fusion
    # Product Link field is empty and wants a code typed into it, while a missing CODE
    # says only that the file predates the comment. Only the first is actionable.
    blank = parse_vcarve_text(_with_toolid("(TOOLID T2 CODE= VENDOR=AMANA FLUTES=3)"))
    assert blank.tools["T2"]["code"] == ""
    assert parse_vcarve_text(_with_toolid()).tools["T2"].get("code") is None


def test_toolid_product_field_is_not_read_as_a_code():
    # Fusion's Product ID is free for the manufacturer's real part number now that the
    # code lives in Product Link, so a stray PRODUCT= must not be mistaken for identity.
    # Two shop tools ground from one catalogue item share a part number and would merge.
    part = parse_vcarve_text(_with_toolid("(TOOLID T2 CODE=EM-0512 PRODUCT=46170-K FLUTES=3)"))
    info = part.tools["T2"]
    assert info["code"] == "EM-0512"
    assert "product_id" not in info
    assert code_in_file_tool(info) == "EM-0512"


def test_a_code_containing_a_space_cannot_round_trip():
    # Pins the grammar the post's own validation relies on. TOOLID_FIELD_PATTERN reads
    # `KEY=(\S*)`, so a space ends the value: "EM 0512" comes back as "EM", silently,
    # and every "EM ...." code would collapse onto that one key. `getToolCode` in
    # post/syntec_Refine.cps therefore refuses to emit a code with interior whitespace —
    # this test is why that check has to stay.
    part = parse_vcarve_text(_with_toolid("(TOOLID T2 CODE=EM 0512 VENDOR=AMANA FLUTES=3)"))
    assert part.tools["T2"]["code"] == "EM"


def test_toolid_distinguishes_two_cutters_that_post_the_same_geometry():
    # The case the whole comment exists for: an upcut and a downcut of one diameter
    # are both "FLAT END MILL D=12.7 CR=0." byte-for-byte, so geometry cannot tell
    # them apart and merging them cuts with whatever is loaded.
    up = parse_vcarve_text(_with_toolid("(TOOLID T2 VENDOR=AMANA CODE=EM-0512 FLUTES=3)"))
    down = parse_vcarve_text(_with_toolid("(TOOLID T2 VENDOR=AMANA CODE=EM-0512D FLUTES=3)"))
    assert up.tools["T2"]["description"] == down.tools["T2"]["description"]
    assert up.tools["T2"]["code"] != down.tools["T2"]["code"]


def test_tooldesc_is_captured_separately_from_the_derived_description():
    part = parse_vcarve_text(
        _with_toolid(
            "(TOOLID T2 VENDOR=AMANA CODE=EM-0512 FLUTES=3)",
            "(TOOLDESC T2 12 DOWNCUT SPIRAL)",
        )
    )
    info = part.tools["T2"]
    assert info["cam_description"] == "12 DOWNCUT SPIRAL"
    # `description` stays the geometry string the compatibility matrix compares, so
    # adding the post's free text cannot change that signal.
    assert info["description"] == "FLAT END MILL D=12.7 CR=0."


def test_toolid_lines_are_not_read_as_operation_names():
    assert operation_name_in_comment("(TOOLID T2 VENDOR=AMANA CODE=EM-0512 FLUTES=3)") == ""
    assert operation_name_in_comment("(TOOLDESC T2 12 DOWNCUT SPIRAL)") == ""


_FORM_MILL_FILE = pathlib.Path.home() / "Documents" / "cnc_library" / "39x35.nc"


@pytest.mark.skipif(not _FORM_MILL_FILE.exists(), reason="library file not present")
def test_library_form_mill_diameter_is_read_from_the_real_file():
    # The worst case the old parser produced, pinned against the real file: a
    # 59.728 mm form mill that resolved to diameter 0, so `_max_tool_radius`
    # inflated the X envelope by nothing where it owed 1.176" per side. X is the
    # axis with a hard stop just outside each end.
    part = parse_vcarve_text(_FORM_MILL_FILE.read_text(errors="replace"), "39x35.nc")
    assert part.tools["T4"]["diameter_inches"] == pytest.approx(2.3515, abs=1e-4)
    assert part.tools["T4"]["tool_type"] == "FORM MILL"
