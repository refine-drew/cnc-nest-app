"""Tests for runtime_estimator."""
import math

import pytest

from runtime_estimator import (
    DEFAULT_RAPID_MM_PER_MIN,
    DEFAULT_TOOL_CHANGE_SECONDS,
    MM_PER_INCH,
    TOOL_SWAP_SECONDS,
    TOUCH_OFF_SECONDS,
    estimate_lines_runtime,
    format_duration,
)


def _seconds(lines):
    return estimate_lines_runtime(lines)["seconds"]


# ── format_duration ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"),
    (45, "45s"),
    (60, "1m 0s"),
    (90, "1m 30s"),
    (3599, "59m 59s"),
    (3600, "1h 0m"),
    (5025, "1h 23m"),
    (None, "—"),
    (-1, "—"),
])
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


# ── G0 rapid moves ────────────────────────────────────────────────────────────

def test_rapid_move_metric_default_units():
    # No unit code → default mm. 100 mm at DEFAULT_RAPID_MM_PER_MIN.
    result = estimate_lines_runtime(["G00 X100 Y0"])
    assert result["rapid"] == pytest.approx(100 / DEFAULT_RAPID_MM_PER_MIN * 60, rel=1e-6)
    assert result["cutting"] == 0
    assert result["tool_changes"] == 0


def test_rapid_move_inches_after_g70():
    # G70 → inches. 100 in = 2540 mm at 1800 in/min = 100/1800 min = 3.333... s
    result = estimate_lines_runtime(["G70", "G00 X100 Y0"])
    assert result["rapid"] == pytest.approx(100 / 1800 * 60, rel=1e-6)


def test_rapid_move_metric_after_g21():
    # 25.4 mm = 1 inch. Rapid = 25.4 mm / (1800 * 25.4 mm/min) * 60 = 1/1800 * 60 = 0.0333... s
    result = estimate_lines_runtime(["G21", "G00 X25.4 Y0"])
    assert result["rapid"] == pytest.approx(60 / 1800, rel=1e-6)


def test_rapid_move_metric_after_g71():
    # G71 is the controller's metric code (Fanuc/ISO dialect) — same effect as G21.
    result = estimate_lines_runtime(["G71", "G00 X25.4 Y0"])
    assert result["rapid"] == pytest.approx(60 / 1800, rel=1e-6)


# ── G1 cutting moves ──────────────────────────────────────────────────────────

def test_cutting_move_uses_modal_feedrate():
    # 10" at F60 in/min = 10/60 min = 10 s
    result = estimate_lines_runtime(["G01 X10 F60"])
    assert result["cutting"] == pytest.approx(10, rel=1e-6)


def test_feedrate_is_sticky_across_lines():
    # F set on first line should apply to subsequent moves
    lines = ["G01 X10 F60", "G01 X20"]   # 10" then another 10" at same F
    result = estimate_lines_runtime(lines)
    assert result["cutting"] == pytest.approx(20, rel=1e-6)


def test_z_only_g1_plunge_is_skipped():
    # A pure plunge contributes zero cutting time per project convention
    lines = ["G01 X0 Y0 F100", "G01 Z-1"]
    result = estimate_lines_runtime(lines)
    assert result["cutting"] == 0


# ── Arcs (G2/G3) ──────────────────────────────────────────────────────────────

def test_quarter_circle_arc_length():
    # G03 (CCW) from (1,0) → (0,1) with center at (0,0): I=-1, J=0, r=1.
    # CCW takes the short path → quarter circle = pi/2 inches at F60 = pi/2 s.
    lines = ["G01 X1 Y0 F60", "G03 X0 Y1 I-1 J0"]
    result = estimate_lines_runtime(lines)
    # The G01 covers 1" at F60 = 1s. The arc adds pi/2 s.
    assert result["cutting"] == pytest.approx(1 + math.pi / 2, rel=1e-4)


def test_clockwise_arc_takes_long_way():
    # G02 (CW) over the same endpoints traces 3/4 of the circle = 3pi/2 inches.
    lines = ["G01 X1 Y0 F60", "G02 X0 Y1 I-1 J0"]
    result = estimate_lines_runtime(lines)
    assert result["cutting"] == pytest.approx(1 + 3 * math.pi / 2, rel=1e-4)


def test_full_circle_when_endpoints_equal():
    # Endpoints equal with non-zero IJ → full circle (not zero motion)
    lines = ["G01 X1 Y0 F60", "G02 X1 Y0 I-1 J0"]
    result = estimate_lines_runtime(lines)
    # 1" linear at F60 = 1s; full circle r=1 → 2π in at F60 = 2π s
    assert result["cutting"] == pytest.approx(1 + 2 * math.pi, rel=1e-4)


# ── Tool changes ──────────────────────────────────────────────────────────────

def test_measured_tool_change_components():
    """
    Timed on the SS2, 2026-08-17 (issue #6): swap 27 s, swap + touch-off 57 s.

    Pinned as literals because they are machine readouts, not derivations —
    re-time the cycle and update this test, the comment in `runtime_estimator`,
    and CLAUDE.md together. The sum is asserted separately so the default cannot
    drift away from its own components.
    """
    assert TOOL_SWAP_SECONDS == 27.0
    assert TOUCH_OFF_SECONDS == 30.0
    assert DEFAULT_TOOL_CHANGE_SECONDS == 57.0
    assert DEFAULT_TOOL_CHANGE_SECONDS == TOOL_SWAP_SECONDS + TOUCH_OFF_SECONDS


def test_tool_change_charges_swap_plus_touch_off():
    result = estimate_lines_runtime(["T1 M06"])
    assert result["tool_changes"] == DEFAULT_TOOL_CHANGE_SECONDS
    assert result["seconds"] == DEFAULT_TOOL_CHANGE_SECONDS


def test_swap_only_posture_costs_the_touch_off_per_change():
    """
    Touching off once at load (auto tool off) is priced by passing the swap alone.

    The gap between the postures is `TOUCH_OFF_SECONDS` times the *change* count,
    not the distinct-tool count — T1 recalled after T2 is measured a second time.
    This is the arithmetic #8 turns on, so it is pinned rather than left implied.
    """
    lines = ["T1 M06", "T2 M06", "T1 M06", "T2 M06"]
    always_on = estimate_lines_runtime(lines)
    at_load = estimate_lines_runtime(lines, tool_change_seconds=TOOL_SWAP_SECONDS)

    assert at_load["tool_change_count"] == always_on["tool_change_count"] == 4
    assert at_load["tool_changes"] == 4 * TOOL_SWAP_SECONDS
    assert always_on["tool_changes"] - at_load["tool_changes"] == pytest.approx(
        4 * TOUCH_OFF_SECONDS,
    )


def test_multiple_tool_changes():
    result = estimate_lines_runtime(["T1 M06", "T2 M06", "T3 M06"])
    assert result["tool_changes"] == 3 * DEFAULT_TOOL_CHANGE_SECONDS


# ── Comments and non-motion lines ────────────────────────────────────────────

def test_comments_ignored():
    result = estimate_lines_runtime([
        "(this is a comment)",
        "(another)",
        "G00 X10",
    ])
    assert result["rapid"] == pytest.approx(10 / DEFAULT_RAPID_MM_PER_MIN * 60, rel=1e-6)


def test_blank_and_misc_lines_skipped():
    result = estimate_lines_runtime(["", "M03 S18000", "G54", "G00 X10"])
    assert result["rapid"] == pytest.approx(10 / DEFAULT_RAPID_MM_PER_MIN * 60, rel=1e-6)


# ── Composition ───────────────────────────────────────────────────────────────

def test_combined_estimate_sums_buckets():
    lines = [
        "T1 M06",         # one tool change
        "G00 X10",        # 10" rapid
        "G01 X20 F60",    # 10" cut at 60 in/min = 10s
        "T2 M06",         # one tool change
    ]
    r = estimate_lines_runtime(lines)
    expected = (
        2 * DEFAULT_TOOL_CHANGE_SECONDS + 10 + 10 / DEFAULT_RAPID_MM_PER_MIN * 60
    )
    assert r["seconds"] == pytest.approx(expected, rel=1e-6)
    assert r["seconds"] == pytest.approx(
        r["cutting"] + r["rapid"] + r["tool_changes"], rel=1e-9
    )


_ROUND_TRIP_NC = """( Material Size)
( X= 457.200, Y= 304.800, Z= 19.050)
(T2 = End Mill {0.5 inches})
G43 H2 Z44.4754
T2 M06
G00 X0 Y0
G01 X10 Y0 Z-1 F60
G53 G49 Z0
M05
M30
"""


def test_part_runtime_round_trip_via_parser():
    """End-to-end: parse_vcarve_text populates runtime_seconds."""
    from gcode_parser import parse_vcarve_text
    part = parse_vcarve_text(_ROUND_TRIP_NC, filename="test.nc")
    # 10 mm cut at F60 mm/min = 10s. The G00 X0 Y0 is a no-op from the origin.
    assert part.runtime_seconds == pytest.approx(10, rel=1e-6)


def test_part_runtime_excludes_tool_change_time():
    """
    A part's own T# M06 costs nothing in `part.runtime_seconds`.

    The generator merges same-tool passes across every placed part, so a part's
    change count in isolation is not a count of anything the machine will do.
    Job-level runtime charges once per emitted block instead — see
    `app._compute_job_stats`, where the honest figure lives (issue #7).
    """
    from gcode_parser import parse_vcarve_text
    part = parse_vcarve_text(_ROUND_TRIP_NC, filename="test.nc")
    with_change = estimate_lines_runtime(
        part.passes[0].lines, tool_change_seconds=DEFAULT_TOOL_CHANGE_SECONDS,
    )
    # The pass really does contain the tool change the part runtime omits.
    assert with_change["tool_change_count"] == 1
    assert part.runtime_seconds == pytest.approx(
        with_change["seconds"] - DEFAULT_TOOL_CHANGE_SECONDS, rel=1e-6,
    )


# ── tool_change_count ─────────────────────────────────────────────────────────

def test_tool_change_count_survives_zero_cost():
    result = estimate_lines_runtime(
        ["T1 M06", "T2 M06", "T1 M06"], tool_change_seconds=0.0,
    )
    assert result["tool_changes"] == 0
    assert result["tool_change_count"] == 3


def test_tool_change_count_counts_recurrences_not_distinct_tools():
    # T1 twice is two changes: the machine really does put T1 back on.
    result = estimate_lines_runtime(["T1 M06", "T2 M06", "T1 M06", "T2 M06"])
    assert result["tool_change_count"] == 4
    assert result["tool_changes"] == 4 * DEFAULT_TOOL_CHANGE_SECONDS
