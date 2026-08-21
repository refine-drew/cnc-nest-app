"""Tests for runtime_estimator."""
import json
import math
import pathlib
import re

import pytest

from gcode_parser import plane_mode
from runtime_estimator import (
    DEFAULT_ACCEL_MM_S2,
    DEFAULT_RAPID_MM_PER_MIN,
    DEFAULT_TOOL_CHANGE_SECONDS,
    MM_PER_INCH,
    TOOL_SWAP_SECONDS,
    TOUCH_OFF_SECONDS,
    SPINDLE_START_SECONDS,
    MotionLimits,
    estimate_lines_runtime,
    format_duration,
    tool_change_seconds_for,
)

# **The trapezoid model contains the arithmetic it replaced.** With acceleration
# unbounded and every junction unlimited, each move cruises at its nominal rate from
# the first instant and the estimator reduces to `distance / feedrate`. Every test in
# this file that asserts an exact number was written against that arithmetic and is
# about *geometry* — arc sweep, unit words, modal motion, bucket sums — not about
# dynamics. Running them under `_INSTANT` keeps them pinning exactly what they were
# written to pin, and means recalibrating `DEFAULT_ACCEL_MM_S2` cannot make a
# geometry test fail. The physics gets its own tests further down, written so that
# they hold for *any* acceleration.
_INSTANT = MotionLimits(accel_mm_s2=1e12, junction_deviation_mm=1e6)


def _seconds(lines, limits=_INSTANT):
    return estimate_lines_runtime(lines, limits=limits)["seconds"]


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
    result = estimate_lines_runtime(["G00 X100 Y0"], limits=_INSTANT)
    assert result["rapid"] == pytest.approx(100 / DEFAULT_RAPID_MM_PER_MIN * 60, rel=1e-6)
    assert result["cutting"] == 0
    assert result["tool_changes"] == 0


def test_rapid_move_inches_after_g70():
    # G70 → inches. 100 in = 2540 mm at 1800 in/min = 100/1800 min = 3.333... s
    result = estimate_lines_runtime(["G70", "G00 X100 Y0"], limits=_INSTANT)
    assert result["rapid"] == pytest.approx(100 / 1800 * 60, rel=1e-6)


def test_rapid_move_metric_after_g21():
    # 25.4 mm = 1 inch. Rapid = 25.4 mm / (1800 * 25.4 mm/min) * 60 = 1/1800 * 60 = 0.0333... s
    result = estimate_lines_runtime(["G21", "G00 X25.4 Y0"], limits=_INSTANT)
    assert result["rapid"] == pytest.approx(60 / 1800, rel=1e-6)


def test_rapid_move_metric_after_g71():
    # G71 is the controller's metric code (Fanuc/ISO dialect) — same effect as G21.
    result = estimate_lines_runtime(["G71", "G00 X25.4 Y0"], limits=_INSTANT)
    assert result["rapid"] == pytest.approx(60 / 1800, rel=1e-6)


# ── G1 cutting moves ──────────────────────────────────────────────────────────

def test_cutting_move_uses_modal_feedrate():
    # 10" at F60 in/min = 10/60 min = 10 s
    result = estimate_lines_runtime(["G01 X10 F60"], limits=_INSTANT)
    assert result["cutting"] == pytest.approx(10, rel=1e-6)


def test_feedrate_is_sticky_across_lines():
    # F set on first line should apply to subsequent moves
    lines = ["G01 X10 F60", "G01 X20"]   # 10" then another 10" at same F
    result = estimate_lines_runtime(lines, limits=_INSTANT)
    assert result["cutting"] == pytest.approx(20, rel=1e-6)


def test_motion_mode_is_modal_across_lines():
    """Fusion writes `G01` once and then bare coordinate lines.

    Requiring the G-word skipped those *and* left the position stale, so the next
    explicit block was timed as a chord across the part. 18G5.nc, a Fusion file
    whose moves are almost all modal, estimated at 75 s.
    """
    # A 10" + 10" L under one G01, written the way Fusion posts it.
    result = estimate_lines_runtime(["G01 X10 Y0 F60", "X10 Y10"], limits=_INSTANT)
    assert result["cutting"] == pytest.approx(20, rel=1e-6)


def test_g28_return_is_not_a_modal_move():
    """`G28 G91 X0. Y0.` carries coordinates but routes via reference, not a cut.

    Under an inherited G01 it would be timed as a feed move all the way back to
    the origin.
    """
    result = estimate_lines_runtime(["G01 X10 Y0 F60", "G28 G91 X0. Y0."], limits=_INSTANT)
    assert result["cutting"] == pytest.approx(10, rel=1e-6)


def test_z_only_g1_plunge_is_counted():
    """A pure plunge is real cutting time, and used to be thrown away.

    "Z-only G1 moves are ignored" was a project convention with nothing behind it.
    A 19 mm plunge at F100 is 11 seconds and there are dozens per part; they were
    all free. `F` is the resultant feed along the programmed path on a Fanuc/Syntec,
    so a plunge is timed on its depth exactly as a lateral move is timed on its
    length — which is what the `_INSTANT` figure below is.
    """
    lines = ["G01 X0 Y0 F100", "G01 Z-1"]
    assert estimate_lines_runtime(lines, limits=_INSTANT)["cutting"] == pytest.approx(
        1 / 100 * 60, rel=1e-6,
    )
    assert estimate_lines_runtime(lines)["cutting"] > 0


# ── Arcs (G2/G3) ──────────────────────────────────────────────────────────────

def test_quarter_circle_arc_length():
    # G03 (CCW) from (1,0) → (0,1) with center at (0,0): I=-1, J=0, r=1.
    # CCW takes the short path → quarter circle = pi/2 inches at F60 = pi/2 s.
    lines = ["G01 X1 Y0 F60", "G03 X0 Y1 I-1 J0"]
    result = estimate_lines_runtime(lines, limits=_INSTANT)
    # The G01 covers 1" at F60 = 1s. The arc adds pi/2 s.
    assert result["cutting"] == pytest.approx(1 + math.pi / 2, rel=1e-4)


def test_clockwise_arc_takes_long_way():
    # G02 (CW) over the same endpoints traces 3/4 of the circle = 3pi/2 inches.
    lines = ["G01 X1 Y0 F60", "G02 X0 Y1 I-1 J0"]
    result = estimate_lines_runtime(lines, limits=_INSTANT)
    assert result["cutting"] == pytest.approx(1 + 3 * math.pi / 2, rel=1e-4)


def test_full_circle_when_endpoints_equal():
    # Endpoints equal with non-zero IJ → full circle (not zero motion)
    lines = ["G01 X1 Y0 F60", "G02 X1 Y0 I-1 J0"]
    result = estimate_lines_runtime(lines, limits=_INSTANT)
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
    ], limits=_INSTANT)
    assert result["rapid"] == pytest.approx(10 / DEFAULT_RAPID_MM_PER_MIN * 60, rel=1e-6)


def test_blank_and_misc_lines_skipped():
    result = estimate_lines_runtime(["", "M03 S18000", "G54", "G00 X10"], limits=_INSTANT)
    assert result["rapid"] == pytest.approx(10 / DEFAULT_RAPID_MM_PER_MIN * 60, rel=1e-6)
    # The `S` is not free, though — it is its own bucket, not skipped motion.
    assert result["spindle"] == pytest.approx(SPINDLE_START_SECONDS)


# ── Composition ───────────────────────────────────────────────────────────────

def test_combined_estimate_sums_buckets():
    lines = [
        "T1 M06",         # one tool change
        "G00 X10",        # 10" rapid
        "G01 X20 F60",    # 10" cut at 60 in/min = 10s
        "T2 M06",         # one tool change
    ]
    r = estimate_lines_runtime(lines, limits=_INSTANT)
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
    """End-to-end: parse_vcarve_text really routes through the estimator.

    Asserted relationally rather than as a number, so recalibrating the machine
    model cannot break it — what is being pinned is the wiring, not the physics.
    """
    from gcode_parser import parse_vcarve_text
    from runtime_estimator import estimate_passes_runtime
    part = parse_vcarve_text(_ROUND_TRIP_NC, filename="test.nc")
    assert part.runtime_seconds == pytest.approx(
        # Both event costs zeroed, matching the parse call — a per-part runtime
        # carries neither, because neither survives a merge.
        estimate_passes_runtime(
            part.passes, tool_change_seconds=0.0, spindle_start_seconds=0.0,
        )["seconds"],
        rel=1e-9,
    )
    assert part.runtime_seconds > 0


def test_the_parser_carries_the_caller_s_motion_model():
    """`limits` is a courier: the parser never reads it, but it must reach the
    estimator. If it did not, per-part runtimes in the tray would be computed under
    the module defaults while the job total used config — one job, two machines."""
    from gcode_parser import parse_vcarve_text
    slow = parse_vcarve_text(_ROUND_TRIP_NC, "t.nc", limits=MotionLimits(accel_mm_s2=50))
    fast = parse_vcarve_text(_ROUND_TRIP_NC, "t.nc", limits=MotionLimits(accel_mm_s2=5000))
    assert slow.runtime_seconds > fast.runtime_seconds


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


# ── Spindle starts ────────────────────────────────────────────────────────────
#
# Observed on the SS2 2026-08-21: the control holds ~5 s on every `S` word. Measured,
# not fitted, which is what lets it sit beside the one fitted parameter — and charged
# per `S`, which is per emitted tool block, so it does not behave like a per-job
# constant that a job of another length would expose.

def test_every_s_word_is_charged_a_spindle_start():
    result = estimate_lines_runtime(
        ["M03 S18000", "G01 X10 F600", "M03 S12000", "G01 X20 F600"],
    )
    assert result["spindle_start_count"] == 2
    assert result["spindle"] == pytest.approx(2 * SPINDLE_START_SECONDS)


def test_a_repeated_speed_is_charged_again():
    """Every time the controller *sees* an `S`, not every time the speed changes: it
    holds either way. This is why `gcode_generator._dedup_spindle` is worth 5 s per
    line it drops rather than being tidiness."""
    twice = estimate_lines_runtime(["M03 S18000", "M03 S18000"])
    assert twice["spindle_start_count"] == 2


def test_spindle_start_count_survives_zero_cost():
    """The count is invariant, the seconds are not — the same split as the tool-change
    count, and for the same reason: a per-part runtime is parsed at zero cost because
    a part's own starts do not survive the generator merging its passes with another
    part's."""
    result = estimate_lines_runtime(
        ["M03 S18000", "M03 S18000"], spindle_start_seconds=0.0,
    )
    assert result["spindle"] == 0.0
    assert result["spindle_start_count"] == 2


def test_a_spindle_start_stops_the_look_ahead():
    """The machine is standing still while the spindle comes up, so carrying speed
    across it would be momentum through a full stop — the same argument as a tool
    change, and the same zero-length barrier."""
    lim = MotionLimits(accel_mm_s2=900.0, junction_deviation_mm=0.05)
    across = estimate_lines_runtime(
        ["G01 X10 F600", "M03 S18000", "G01 X20 F600"],
        limits=lim, spindle_start_seconds=0.0,
    )["seconds"]
    alone = estimate_lines_runtime(
        ["G01 X10 F600"], limits=lim, spindle_start_seconds=0.0,
    )["seconds"]
    assert across == pytest.approx(2 * alone, rel=1e-9)


def test_the_total_is_the_sum_of_all_four_buckets():
    """Adding a bucket is where a total quietly stops adding up."""
    result = estimate_lines_runtime([
        "T1 M06", "M03 S18000", "G00 X10", "G01 X20 F60",
    ], limits=_INSTANT)
    assert result["seconds"] == pytest.approx(
        result["cutting"] + result["rapid"] + result["tool_changes"]
        + result["spindle"], rel=1e-12,
    )
    assert result["spindle"] == pytest.approx(SPINDLE_START_SECONDS)


# ── The acceleration model ────────────────────────────────────────────────────
#
# These pin the *physics*, not the fitted constant. Every assertion below holds for
# any acceleration and any junction deviation, so re-fitting `DEFAULT_ACCEL_MM_S2`
# against more timed jobs cannot make one of them fail. Exactly one test in this file
# is deliberately coupled to the shipped number, and it says so.

def _t(lines, accel=900.0, jd=0.05):
    return estimate_lines_runtime(
        lines, limits=MotionLimits(accel_mm_s2=accel, junction_deviation_mm=jd),
    )["seconds"]


def test_an_isolated_move_costs_exactly_one_ramp_up_and_one_ramp_down():
    """For a move long enough to reach its feed, `t = L/v + v/a` exactly.

    This is the strongest test here: it pins the trapezoid arithmetic in closed form
    without pinning any particular acceleration. The cruise phase covers whatever the
    two ramps do not, so the only cost of acceleration is the time spent below `v` —
    which integrates to exactly `v/a` however long the move is.
    """
    v = 600.0 / 60.0                       # F600 mm/min -> 10 mm/s
    for accel in (200.0, 900.0, 4000.0):
        length = 5000.0                    # >> v^2/a for all three
        expected = length / v + v / accel
        assert _t(["G01 X5000 F600"], accel=accel) == pytest.approx(expected, rel=1e-9)


def test_a_move_too_short_to_reach_its_feed_is_a_triangle():
    """Below `L = v^2/a` the machine never cruises: it accelerates to the midpoint
    and decelerates from it, taking `2*sqrt(L/a)` whatever the programmed feed says.
    This is the regime the shop's files actually live in — the median G1 XY move in
    the reference file is 0.419 mm."""
    for accel in (300.0, 900.0):
        for length in (0.5, 2.0):
            expected = 2 * math.sqrt(length / accel)
            got = _t([f"G01 X{length} F100000"], accel=accel)
            assert got == pytest.approx(expected, rel=1e-9)


def test_a_long_move_approaches_distance_over_feedrate_from_above():
    """The model's asymptote is the arithmetic it replaced, and it is never faster."""
    v = 600.0 / 60.0
    ideal = 100000.0 / v
    got = _t(["G01 X100000 F600"])
    assert got > ideal
    assert got == pytest.approx(ideal, rel=0.005)


def test_a_reversal_costs_the_same_as_two_separate_moves():
    """A 180-degree corner must bring the machine to a dead stop — there is no
    junction deviation large enough to carry speed through a direction reversal."""
    out_and_back = _t(["G01 X10 F600", "G01 X0"])
    twice_isolated = 2 * _t(["G01 X10 F600"])
    assert out_and_back == pytest.approx(twice_isolated, rel=1e-9)


def test_a_collinear_continuation_carries_full_speed_through_the_join():
    """Momentum crosses a straight join, which is what the backward pass delivers.
    Two 10 mm moves in a line cost exactly one 20 mm move, and strictly less than
    two isolated ones."""
    joined = _t(["G01 X10 F600", "G01 X20"])
    assert joined == pytest.approx(_t(["G01 X20 F600"]), rel=1e-9)
    assert joined < 2 * _t(["G01 X10 F600"])


def test_zero_junction_deviation_stops_at_every_corner():
    """`jd` is the only thing that lets speed through a corner. At zero, an N-segment
    zigzag costs exactly N isolated moves."""
    zigzag = ["G01 X10 Y0 F600", "G01 X20 Y10", "G01 X30 Y0"]
    isolated = 3 * _t([f"G01 X{math.hypot(10, 0)} F600"], jd=0.0)
    # Each leg of the zigzag after the first is hypot(10,10); compare like with like.
    legs = _t(["G01 X10 F600"], jd=0.0) + 2 * _t([f"G01 X{math.hypot(10, 10)} F600"], jd=0.0)
    assert _t(zigzag, jd=0.0) == pytest.approx(legs, rel=1e-9)
    assert isolated > 0


def test_a_sharper_corner_is_slower():
    """Pins the sign of the junction formula, which is the easiest thing to invert.
    Same path length in every case; only the corner angle changes."""
    def corner(angle_deg):
        rad = math.radians(angle_deg)
        x2 = 100 + 100 * math.cos(rad)
        y2 = 100 * math.sin(rad)
        # The feed has to be well above every junction speed or all three corners
        # clamp to `v_max` and the test compares three identical numbers.
        return _t([f"G01 X100 Y0 F60000", f"G01 X{x2} Y{y2}"])
    gentle, right, sharp = corner(10), corner(90), corner(170)
    assert gentle < right < sharp


def test_halving_the_acceleration_increases_every_estimate():
    """Monotonicity. `calibrate_runtime.py` bisects on this property, so it is pinned
    here rather than assumed there."""
    lines = ["G01 X10 Y0 F2540", "G01 X20 Y10", "G03 X30 Y0 I5 J-5", "G00 X0 Y0"]
    times = [_t(lines, accel=a) for a in (2000.0, 1000.0, 500.0, 250.0)]
    assert times == sorted(times)
    assert times[0] < times[-1]


def test_rapids_are_accelerated_too():
    """The reference file's rapid total went 17.6 s -> 48.8 s on identical geometry.
    A rapid is a move like any other; it just has a higher ceiling."""
    ideal = 100.0 / DEFAULT_RAPID_MM_PER_MIN * 60
    assert estimate_lines_runtime(["G00 X100"])["rapid"] > ideal
    assert estimate_lines_runtime(["G00 X100"], limits=_INSTANT)["rapid"] == pytest.approx(
        ideal, rel=1e-6,
    )


def test_a_tighter_arc_is_slower_at_the_same_feed():
    """The centripetal cap: the machine cannot hold `feed` around a radius tighter
    than `feed^2 / accel`, however short the arc is."""
    feed = 100000.0                       # far above any centripetal limit here
    wide = _t([f"G03 X20 Y0 I10 J0 F{feed}"], accel=900.0)       # r=10, half circle
    tight = _t([f"G03 X5 Y0 I2.5 J0 F{feed}"], accel=900.0)      # r=2.5, half circle
    # Per unit length the tight arc must be slower.
    assert tight / (math.pi * 2.5) > wide / (math.pi * 10)


# ── Arc planes ────────────────────────────────────────────────────────────────

def test_the_same_arc_times_the_same_in_every_plane():
    """A G19 arc's centre offsets are J/K and a G18's are I/K. Reading them as I/J —
    which the pre-2026-08-20 estimator did, since it never tracked the plane — timed
    every vertical lead-in ramp at zero.
    Pinned as an invariance rather than a magic number so it cannot pass by accident.
    """
    xy = _t(["G17 G01 X1 Y0 F60", "G03 X0 Y1 I-1 J0"])
    xz = _t(["G18 G01 X1 Z0 F60", "G03 X0 Z1 I-1 K0"])
    yz = _t(["G19 G01 Y1 Z0 F60", "G03 Y0 Z1 J-1 K0"])
    assert xz == pytest.approx(xy, rel=1e-9)
    assert yz == pytest.approx(xy, rel=1e-9)


def test_a_g19_ramp_arc_is_not_free():
    """The direct regression. 45 of the 162 arcs in the reference file are G19
    lead-in/lead-out ramps; before the plane fix they cost nothing at all."""
    assert estimate_lines_runtime(
        ["G19", "G01 Y10 Z10 F600", "G03 Y13.175 Z13.175 K3.175"],
    )["cutting"] > _t(["G01 Y10 Z10 F600"])


def test_the_plane_word_is_modal_across_lines():
    """VCarve writes `G19` on a line of its own, exactly as it does `G17`. A walker
    that reads the plane off the arc block misses it — the same lesson as modal
    motion, one level down."""
    inline = _t(["G01 Y1 Z0 F60", "G19 G03 Y0 Z1 J-1 K0"])
    modal = _t(["G19", "G01 Y1 Z0 F60", "G03 Y0 Z1 J-1 K0"])
    assert modal == pytest.approx(inline, rel=1e-9)


# ── Look-ahead barriers ───────────────────────────────────────────────────────

def test_a_tool_change_stops_the_look_ahead():
    """Without a barrier the planner carries speed from the last cut of one tool into
    the first move of the next — momentum across an event where the machine is
    stationary for most of a minute."""
    lim = MotionLimits(accel_mm_s2=900.0, junction_deviation_mm=0.05)
    across = estimate_lines_runtime(
        ["G01 X10 F600", "T2 M06", "G01 X20 F600"],
        limits=lim, tool_change_seconds=0.0,
    )["seconds"]
    assert across == pytest.approx(2 * _t(["G01 X10 F600"]), rel=1e-9)


@pytest.mark.parametrize("stop", ["M05", "M30"])
def test_a_spindle_stop_stops_the_look_ahead(stop):
    across = _t(["G01 X10 F600", stop, "G01 X20 F600"])
    assert across == pytest.approx(2 * _t(["G01 X10 F600"]), rel=1e-9)


# ── The posture toggle ────────────────────────────────────────────────────────

def test_tool_change_seconds_for_prices_both_postures():
    assert tool_change_seconds_for(True) == DEFAULT_TOOL_CHANGE_SECONDS
    assert tool_change_seconds_for(False) == TOOL_SWAP_SECONDS
    assert tool_change_seconds_for(True) - tool_change_seconds_for(False) == TOUCH_OFF_SECONDS


def test_motion_limits_read_config_and_fall_back():
    """Every read site supplies its own fallback — `config.py` has no defaults dict —
    so a config predating these keys must still produce the shipped machine model."""
    assert MotionLimits.from_config({}) == MotionLimits()
    assert MotionLimits.from_config(None) == MotionLimits()
    got = MotionLimits.from_config(
        {"rapid_ipm": 900.0, "accel_mm_s2": 500.0, "junction_deviation_mm": 0.02}
    )
    assert got.rapid_mm_per_min == pytest.approx(900.0 * MM_PER_INCH)
    assert got.accel_mm_s2 == 500.0
    assert got.junction_deviation_mm == 0.02


# ── The timed jobs ────────────────────────────────────────────────────────────
#
# Two jobs have been run against a clock. Only one of them can be fitted to, and the
# difference is not the geometry — it is that somebody wrote down which posture the
# control was on. See `DEFAULT_ACCEL_MM_S2`.

_FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "18g-test.nc"
_OBSERVED_SECONDS = 697.0        # 11m37s on the SS2, 2026-08-20, posture unrecorded

_T24_FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "t24-test.nc"
_T24_OBSERVED_SECONDS = 653.0    # 10m53s on the SS2, 2026-08-21, touch-off OFF


def _fitted_limits() -> MotionLimits:
    """The shipped machine model — it is that model that has to predict the shipped
    observation, not the module defaults."""
    advanced = json.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "config.json").read_text()
    )["advanced"]
    return MotionLimits.from_config(advanced)


def _up_to_the_end_of_job_change(lines):
    """The lines the T24 observation actually covers.

    The operator timed from cycle start to the final tool change — the one that leaves
    pocket 2 in the spindle for the next job — so the park block is outside the
    window. Cutting the stream at the park comment is how the window is expressed;
    `test_the_park_block_costs_only_its_tool_change` is what says that is the same as
    subtracting one change from the whole file.
    """
    at = next(i for i, ln in enumerate(lines) if ln.strip().startswith("( ---- park"))
    return lines[:at]


def test_the_timed_job_predicts_its_observed_cycle():
    """`t24-test.nc` ran 10m53s (653 s) on the SS2, 2026-08-21, auto tool touch-off OFF.

    **This is the fit.** One observation and one free parameter, so the agreement is
    exact by construction and proves nothing on its own; the test is the *record of
    the fit*, in the same spirit as
    `test_rail_defaults_match_measured_machine_positions` and
    `test_measured_tool_change_components`. Re-fitting means changing `config.json`,
    `DEFAULT_ACCEL_MM_S2` and CLAUDE.md together.

    The posture is hard-coded rather than read from the config, because **the posture
    is part of the observation**: 27 s per change is what the machine was doing while
    the stopwatch ran, and an operator later flipping the Settings checkbox must not
    silently re-price a measurement. That the earlier job has no posture recorded
    beside it is exactly why it is not the fit.

    Run `calibrate_runtime.py FILE=10:53 --no-auto-tool --to-end-of-job-change` to
    re-fit. **Do not widen the tolerance to make this pass** — at 5% it is loose
    enough to survive an honest re-fit across two or three more jobs, and tight enough
    that a structural regression (the G19 arcs going back to zero, the look-ahead
    barriers dropped, the junction formula inverted) fails it.
    """
    got = estimate_lines_runtime(
        _up_to_the_end_of_job_change(_T24_FIXTURE.read_text().splitlines()),
        limits=_fitted_limits(),
        tool_change_seconds=tool_change_seconds_for(False),
    )
    assert got["seconds"] == pytest.approx(_T24_OBSERVED_SECONDS, rel=0.05)


def test_the_park_block_costs_only_its_tool_change():
    """Everything after the last cut is one carousel change and two G53 traverses.

    Which is what makes the observation window above subtractable: the estimator does
    not time a G53 move (it reads work coordinates, and `NON_MODAL_COORD_PATTERN`
    rules the block out), so the whole file and the window differ by exactly one tool
    change. That is a **known approximation** in the optimistic direction — the
    machine really does traverse to the park, and those seconds are outside both the
    model and the observation, which is the honest place for them to be while nothing
    measures them.
    """
    lines = _T24_FIXTURE.read_text().splitlines()
    whole = estimate_lines_runtime(
        lines, limits=_fitted_limits(), tool_change_seconds=27.0)
    window = estimate_lines_runtime(
        _up_to_the_end_of_job_change(lines),
        limits=_fitted_limits(), tool_change_seconds=27.0)
    assert whole["tool_change_count"] == window["tool_change_count"] + 1
    assert whole["seconds"] - window["seconds"] == pytest.approx(27.0, abs=1e-6)


def test_the_earlier_timed_job_agrees_only_with_touch_off_off():
    """`18g-test.nc` ran 11m37s (697 s) on the SS2, 2026-08-20 — and nobody recorded
    which posture the control was on.

    That omission cost the first calibration. The fit charged this job 2 x 57 s on the
    strength of issue #8's *decision* rather than an observation, and if the control
    was actually on 27 s the same 697 s buys 60 s more motion — 9% of the job, which
    went into acceleration instead and made it 240 mm/s^2. **A timed job with no
    posture beside it is worth roughly nothing.**

    So this is a cross-check and not a fit, and it is asserted at the OFF posture:
    under that reading the job predicts within 5% of what it ran, which is the whole
    reason to believe the reading. The ON reading is 6% high — outside the same
    tolerance, and the two cannot both be true. Held at 27 s explicitly, like the fit
    above, so a Settings flip cannot re-price a measurement after the fact.
    """
    got = estimate_lines_runtime(
        _FIXTURE.read_text().splitlines(),
        limits=_fitted_limits(),
        tool_change_seconds=tool_change_seconds_for(False),
    )
    assert got["seconds"] == pytest.approx(_OBSERVED_SECONDS, rel=0.05)

    # The evidence for the reading, not decoration: the same file under the posture
    # the first fit assumed lands further from what it ran. Asserted relationally
    # rather than as "the ON reading misses by more than 5%" — that was true by 1.75 s
    # and would fail on an honest re-fit for no reason worth failing on.
    on = estimate_lines_runtime(
        _FIXTURE.read_text().splitlines(),
        limits=_fitted_limits(),
        tool_change_seconds=tool_change_seconds_for(True),
    )
    assert abs(on["seconds"] - _OBSERVED_SECONDS) > \
        abs(got["seconds"] - _OBSERVED_SECONDS)


def test_the_reference_file_times_its_vertical_ramp_arcs():
    """45 of this file's arcs are G19 lead-in/lead-out ramps.

    Before the plane fix they contributed **zero** — the arc length was computed from
    I/J only, and a G19 arc's centre offsets are J/K — so the file's cutting time was
    short by every ramp in it. Asserted against the file's own geometry rather than against the
    fitted constant, so this stays caught even after a recalibration.
    """
    lines = _FIXTURE.read_text().splitlines()
    g19 = sum(
        1 for i, ln in enumerate(lines)
        if re.search(r"\bG0?[23]\b", ln)
        and _plane_in_force(lines, i) == 19
    )
    assert g19 == 45

    # Forcing every arc into G17 changes the total: the ramps are really being timed.
    flattened = [ln.replace("G19", "G17") for ln in lines]
    assert estimate_lines_runtime(flattened)["cutting"] != pytest.approx(
        estimate_lines_runtime(lines)["cutting"], rel=1e-6,
    )


def _plane_in_force(lines, index):
    plane = 17
    for ln in lines[: index + 1]:
        if not ln.lstrip().startswith("("):
            plane = plane_mode(ln, plane)
    return plane
