import math

import pytest

from gcode_parser import parse_vcarve_text
from audit_metrics import extract_pass_metrics, extract_tool_metrics
from audit_library import audit_part, build_thresholds, scan_library, summarize_toolpath_feeds
from tool_library import ToolLibrary

MM_PER_INCH = 25.4

# Well-formed spoilboard-referenced file: 0.75" stock (19.05 mm), cuts reach the
# spoilboard (Z≈0.5), material top at +19.05. Feeds in mm/min: plunge 1524 (=60 ipm),
# cut 5080 (=200 ipm). Spindle 18000 RPM.
SAMPLE_GOOD = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(T2 = End Mill {0.5 inches})
G71
G43 H2 Z44.4754
T2 M06
M03 S18000
G00 X0 Y0
G01 Z0.5 F1524
G01 X50 Y50 F5080
G01 X100 Y100
G53 G49 Z0
M05
M30
"""

# Legacy top-of-material convention (large negative Z) — must be blocked by validate_z.
SAMPLE_LEGACY = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(T2 = End Mill {0.5 inches})
G71
G43 H2 Z25.4254
T2 M06
M03 S18000
G00 X0 Y0
G01 Z-19.304 F1524
G01 X50 Y50 F5080
G53 G49 Z0
M05
M30
"""

# Out-of-range feed (12700 mm/min = 500 ipm) and spindle (30000 RPM).
SAMPLE_OUT_OF_RANGE = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(T2 = End Mill {0.5 inches})
G71
G43 H2 Z44.4754
T2 M06
M03 S30000
G00 X0 Y0
G01 Z0.5 F1524
G01 X50 Y50 F12700
G53 G49 Z0
M05
M30
"""

# References a tool with no diameter in the header and not in our library.
SAMPLE_UNKNOWN_TOOL = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(T7 = Mystery Bit)
G71
G43 H2 Z44.4754
T7 M06
M03 S18000
G00 X0 Y0
G01 Z0.5 F1524
G01 X50 Y50 F5080
G53 G49 Z0
M05
M30
"""

# Two toolpaths, same tool (T2), different cutting feeds: first 200 ipm (5080 mm/min),
# second 100 ipm (2540 mm/min). Mirrors real library files (e.g. Airstream/603060-A)
# where one tool runs several toolpaths at different feeds.
SAMPLE_MULTI_FEED = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(T2 = End Mill {0.5 inches})
G71
G43 H2 Z44.4754
T2 M06
M03 S18000
G00 X0 Y0
G01 Z0.5 F1524
G01 X50 Y50 F5080
G00 Z44.4754
T2 M06
M03 S18000
G00 X0 Y0
G01 Z0.5 F1524
G01 X90 Y90 F2540
G53 G49 Z0
M05
M30
"""

LIBRARY_TOOLS = {
    "T2": {"name": "1/2\" end mill", "diameter_inches": 0.5},
}


@pytest.fixture()
def tool_lib():
    return ToolLibrary(LIBRARY_TOOLS)


@pytest.fixture()
def thresholds():
    return build_thresholds({"tools": LIBRARY_TOOLS})


def _approx_ipm(value_ipm, expected):
    return value_ipm == pytest.approx(expected, abs=0.5)


# --- extract_tool_metrics -------------------------------------------------

def test_metrics_split_cut_and_plunge():
    part = parse_vcarve_text(SAMPLE_GOOD)
    metrics = extract_tool_metrics(part)
    assert "T2" in metrics
    m = metrics["T2"]
    # Cut feed = 5080 mm/min, plunge = 1524 mm/min.
    assert m["cut_feed_min"] == pytest.approx(5080.0)
    assert m["cut_feed_max"] == pytest.approx(5080.0)
    assert m["plunge_feed_min"] == pytest.approx(1524.0)
    assert m["plunge_feed_max"] == pytest.approx(1524.0)
    assert m["spindle_min"] == pytest.approx(18000.0)
    assert m["spindle_max"] == pytest.approx(18000.0)
    assert m["cut_move_count"] == 2  # the two lateral G01 moves
    assert m["pass_count"] == 1


def test_metrics_handle_inch_units():
    # Same geometry but declared in inches: feeds should scale to mm internally.
    text = SAMPLE_GOOD.replace("G71", "G20").replace("F1524", "F60").replace("F5080", "F200")
    part = parse_vcarve_text(text)
    m = extract_tool_metrics(part)["T2"]
    assert m["cut_feed_max"] == pytest.approx(200 * MM_PER_INCH)
    assert m["plunge_feed_max"] == pytest.approx(60 * MM_PER_INCH)


# --- per-toolpath feed extraction -----------------------------------------

def test_pass_metrics_separate_toolpaths_sharing_a_tool():
    # Same tool (T2) runs two toolpaths at 5080 and 2540 mm/min.
    part = parse_vcarve_text(SAMPLE_MULTI_FEED)
    passes = extract_pass_metrics(part)
    assert len(passes) == 2
    assert [p["tool"] for p in passes] == ["T2", "T2"]
    assert passes[0]["cut_feed_max"] == pytest.approx(5080.0)
    assert passes[1]["cut_feed_max"] == pytest.approx(2540.0)
    # Rolled up per tool, the two feeds collapse into one min/max range.
    tool = extract_tool_metrics(part)["T2"]
    assert tool["cut_feed_min"] == pytest.approx(2540.0)
    assert tool["cut_feed_max"] == pytest.approx(5080.0)
    assert tool["pass_count"] == 2


def test_summarize_toolpath_feeds_flags_variation():
    part = parse_vcarve_text(SAMPLE_MULTI_FEED)
    summary, feed_varies = summarize_toolpath_feeds(extract_pass_metrics(part))
    assert feed_varies == "yes"
    # 5080 mm/min = 200 ipm, 2540 mm/min = 100 ipm, plunge 1524 = 60 ipm.
    assert "1.T2 cut 200" in summary
    assert "2.T2 cut 100" in summary
    assert "plunge 60" in summary


def test_audit_part_reports_per_toolpath_feeds(tool_lib, thresholds):
    part = parse_vcarve_text(SAMPLE_MULTI_FEED, filename="multi.nc")
    row, _, toolpath_rows = audit_part(part, "multi.nc", tool_lib, thresholds)
    assert row["feed_varies"] == "yes"
    assert row["toolpath_count"] == 2
    assert "Feed rates vary by toolpath" in row["flags"]
    # Differing feeds alone must not escalate an otherwise-clean file.
    assert row["status"] == "ok"
    assert len(toolpath_rows) == 2
    assert _approx_ipm(toolpath_rows[0]["cut_feed_max_ipm"], 200.0)
    assert _approx_ipm(toolpath_rows[1]["cut_feed_max_ipm"], 100.0)


# --- audit_part flags -----------------------------------------------------

def test_good_file_is_ok(tool_lib, thresholds):
    part = parse_vcarve_text(SAMPLE_GOOD, filename="good.nc")
    row, tool_rows, toolpath_rows = audit_part(part, "good.nc", tool_lib, thresholds)
    assert row["status"] == "ok"
    assert row["flags"] == ""
    assert _approx_ipm(row["cut_feed_max_ipm"], 200.0)
    assert _approx_ipm(row["plunge_feed_max_ipm"], 60.0)
    assert row["spindle_max_rpm"] == 18000
    assert row["tools"] == "T2"
    assert len(tool_rows) == 1
    assert tool_rows[0]["diameter_source"] == "header"
    # One toolpath, single feed → not flagged as varying.
    assert row["toolpath_count"] == 1
    assert row["feed_varies"] == ""
    assert len(toolpath_rows) == 1
    assert toolpath_rows[0]["toolpath"] == 1
    assert toolpath_rows[0]["tool"] == "T2"
    assert _approx_ipm(toolpath_rows[0]["cut_feed_max_ipm"], 200.0)


def test_legacy_top_of_material_blocked(tool_lib, thresholds):
    part = parse_vcarve_text(SAMPLE_LEGACY, filename="legacy.nc")
    row, _, _ = audit_part(part, "legacy.nc", tool_lib, thresholds)
    assert row["status"] == "blocked"
    assert "top-of-material" in row["flags"].lower() or "spoilboard" in row["flags"].lower()


def test_out_of_range_feed_and_spindle_warn(tool_lib, thresholds):
    part = parse_vcarve_text(SAMPLE_OUT_OF_RANGE, filename="hot.nc")
    row, _, _ = audit_part(part, "hot.nc", tool_lib, thresholds)
    assert row["status"] == "warning"
    assert "Cut feed" in row["flags"]
    assert "Spindle" in row["flags"]


def test_header_library_diameter_mismatch_warns(tool_lib, thresholds):
    # Header declares T2 = 0.25", but the library says T2 = 0.5".
    text = SAMPLE_GOOD.replace("(T2 = End Mill {0.5 inches})", "(T2 = End Mill {0.25 inches})")
    part = parse_vcarve_text(text, filename="mismatch.nc")
    row, tool_rows, _ = audit_part(part, "mismatch.nc", tool_lib, thresholds)
    assert row["status"] == "warning"
    assert "library" in row["flags"]
    assert tool_rows[0]["header_diameter_in"] == 0.25
    assert tool_rows[0]["library_diameter_in"] == 0.5


def test_unknown_tool_blocked(tool_lib, thresholds):
    part = parse_vcarve_text(SAMPLE_UNKNOWN_TOOL, filename="unknown.nc")
    row, tool_rows, _ = audit_part(part, "unknown.nc", tool_lib, thresholds)
    assert row["status"] == "blocked"
    assert "Unknown tool" in row["flags"]
    assert "T7" in row["unknown_tools"]
    assert tool_rows[0]["diameter_source"] == "unknown"


# --- scan_library end to end ----------------------------------------------

def test_scan_library_walks_files_and_recovers_from_errors(tmp_path, tool_lib, thresholds):
    (tmp_path / "good.nc").write_text(SAMPLE_GOOD)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "legacy.nc").write_text(SAMPLE_LEGACY)
    (tmp_path / "ignore.txt").write_text("not gcode")

    file_rows, tool_rows, toolpath_rows = scan_library(str(tmp_path), tool_lib, thresholds)
    paths = {r["path"] for r in file_rows}
    assert "good.nc" in paths
    assert ("sub/legacy.nc" in paths) or ("sub\\legacy.nc" in paths)
    assert not any(r["path"].endswith(".txt") for r in file_rows)
    assert len(tool_rows) >= 2
    assert len(toolpath_rows) >= 2
    # Every toolpath row is tagged with its source file.
    assert all(r["path"] in paths for r in toolpath_rows)
