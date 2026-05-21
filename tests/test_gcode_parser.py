from gcode_parser import parse_vcarve_text

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


def test_parse_vcarve_text_extracts_blank_and_material():
    part = parse_vcarve_text(SAMPLE_VCARVE, filename="sample.nc")

    assert part.filename == "sample.nc"
    assert part.blank_width == 1676.4
    assert part.blank_height == 3200.4
    assert part.material_thickness == 31.877


def test_parse_vcarve_text_extracts_tool_from_header():
    part = parse_vcarve_text(SAMPLE_VCARVE)

    assert "T2" in part.tools
    assert part.tools["T2"]["diameter_inches"] == 0.5


def test_parse_vcarve_text_scans_coordinates():
    part = parse_vcarve_text(SAMPLE_VCARVE)

    assert part.min_x == 0.0
    assert part.max_x == 200.0
    assert part.min_y == 0.0
    assert part.max_y == 300.0
