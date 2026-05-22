import pytest
from gcode_parser import parse_vcarve_text, validate_z, GcodePass

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
