"""
Tests for gcode_validator.py.

Two layers. The unit tests below build the smallest program that exhibits one
defect, so a failure names the check. The fixture tests at the bottom run the
validator against the three real files from the 2026-08-15 output review and
require it to find, unaided, every defect a human found by reading them.
"""
import json
import pathlib

import pytest

from gcode_validator import (
    ERROR, WARNING, validate_gcode, has_errors, format_findings,
)

ADVANCED = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "config.json").read_text()
)["advanced"]


def checks(text, severity=None, advanced=None):
    """Set of check names fired, optionally filtered by severity."""
    fs = validate_gcode(text, advanced if advanced is not None else ADVANCED)
    return {f.check for f in fs if severity is None or f.severity == severity}


# A minimal well-formed program. Every unit test below is this with one thing
# broken, so anything the tests report is the break and not the scaffolding.
CLEAN = """%
(TEST)
N10 G00 G17 G71 G40 G49 G80 G90
N20 G54
N30 G00 G53 Z0
N40 T2 M06
N50 G00 G43 H2 Z25.4000
N60 M03 S18000
N70 G00 X100.0000 Y100.0000
N80 G01 Z-1.0000 F1270.
N90 G01 X120.0000 Y100.0000 F2540.
N100 G02 X130.0000 Y110.0000 I0. J10.0000
N110 G00 Z25.4000
N120 G00 G53 G49 Z0
N130 M05
N140 G00 G53 Z0
N150 G00 G53 X0.0000 Y3048.0000 M05
N160 M30
%
"""


def test_clean_program_produces_no_errors():
    fs = validate_gcode(CLEAN, ADVANCED)
    assert not has_errors(fs), format_findings(fs)


def test_g71_is_not_flagged():
    # Syntec's one documented deviation from Fanuc 0M. Flagging it would push
    # someone to "fix" it to G21, which the control does not accept.
    assert "unknown-code" not in checks(CLEAN)


# ── structure ────────────────────────────────────────────────────────────────

def test_duplicate_sequence_number_is_an_error():
    assert "sequence" in checks(CLEAN.replace("N90 G01", "N80 G01"), ERROR)


def test_non_increasing_sequence_number_is_an_error():
    # N75 follows N80 — lower, so block search would jump backwards.
    assert "sequence" in checks(CLEAN.replace("N90 G01", "N75 G01"), ERROR)


def test_nested_comment_is_an_error():
    # "(Job: (9) 18G Test)" — the header of a real 2026-08-20 job. The comment ends
    # at the first ')', so the control reads ") 18G Test)" as code and alarms, at
    # the line the comment sits on rather than at the tool that was mis-described.
    assert "comment-syntax" in checks(
        CLEAN.replace("(TEST)", "(Job: (9) 18G Test)"), ERROR)


def test_stray_close_paren_is_an_error():
    assert "comment-syntax" in checks(CLEAN.replace("(TEST)", "TEST)"), ERROR)


def test_unclosed_comment_is_an_error():
    assert "comment-syntax" in checks(CLEAN.replace("(TEST)", "(TEST"), ERROR)


def test_two_comments_on_one_line_are_fine():
    # Balanced and sequential, not nested — the control reads two comments and no
    # code. Flagging this would fire on ordinary posted output.
    assert "comment-syntax" not in checks(CLEAN.replace("(TEST)", "(A) (B)"))


def test_a_stranded_decimal_point_is_an_error():
    # `Y2727.7000.` — the 2026-08-21 output. A substitution rewrote the number
    # in front of a trailing decimal point the pattern never matched, leaving
    # two points in one word. The control alarms on the block, mid-cut.
    assert "word-syntax" in checks(
        CLEAN.replace("Y100.0000 F2540.", "Y100.0000. F2540."), ERROR)


def test_a_trailing_decimal_point_is_legal_on_its_own():
    # `Z24.` and `F1270.` are how Fusion writes whole numbers; CLEAN already
    # carries `F1270.` and `I0.`. Flagging those would fire on every job.
    assert "word-syntax" not in checks(CLEAN)
    assert "word-syntax" not in checks(
        CLEAN.replace("G01 Z-1.0000 F1270.", "G01 Z-1. F1270."))
    assert "word-syntax" not in checks(
        CLEAN.replace("X100.0000 Y100.0000", "X.5 Y-.25"))


def test_a_broken_comment_is_reported_once_as_a_comment():
    # Stripping comments from `(TEST` leaves `(TEST` as code, which the word
    # tokeniser also cannot read. One defect, one report — the comment check
    # names the actual problem, so word-syntax stays quiet.
    assert "word-syntax" not in checks(CLEAN.replace("(TEST)", "(TEST"))


def test_lone_tape_mark_is_an_error():
    assert "tape-marks" in checks(CLEAN[1:], ERROR)      # trailing % only
    assert "tape-marks" in checks(CLEAN[:-2], ERROR)     # leading % only


def test_neither_tape_mark_is_accepted():
    assert "tape-marks" not in checks(CLEAN.replace("%\n", ""))


def test_stray_program_stop_is_an_error():
    # An M01 inherited from a source file halts a merged job part-way with no
    # prompt explaining why.
    assert "stray-stop" in checks(CLEAN.replace("N130 M05", "N130 M01"), ERROR)


def test_second_m30_is_an_error():
    assert "program-end" in checks(CLEAN.replace("N130 M05", "N130 M30"), ERROR)


def test_missing_m30_is_an_error():
    assert "program-end" in checks(CLEAN.replace("N160 M30", "N160 M05"), ERROR)


def test_h_must_match_t():
    assert "g43-offset" in checks(CLEAN.replace("G43 H2", "G43 H4"), ERROR)


def test_unknown_code_is_a_warning_not_a_gate():
    # This list is more likely incomplete than the file is wrong, so an
    # unrecognised code must not block a job.
    broken = CLEAN.replace("N20 G54", "N20 G54 G123")
    assert "unknown-code" in checks(broken, WARNING)
    assert "unknown-code" not in checks(broken, ERROR)


# ── geometry ─────────────────────────────────────────────────────────────────

def test_offset_word_outside_the_active_plane_is_an_error():
    # The 2026-08-14 alarm: a file G19 emitted next to a swapped X word, leaving
    # an I offset in a plane that takes J/K.
    broken = CLEAN.replace("N100 G02 X130.0000 Y110.0000 I0. J10.0000",
                           "N100 G19 G03 X130.0000 Z1.27 I-1.2700")
    assert "arc-plane" in checks(broken, ERROR)


def test_plane_is_tracked_modally():
    # The plane word arrives on its own block; the arc that follows carries none
    # and still belongs to the vertical plane.
    broken = CLEAN.replace(
        "N100 G02 X130.0000 Y110.0000 I0. J10.0000",
        "N95 G19\nN100 G03 X130.0000 Z1.27 I-1.2700")
    assert "arc-plane" in checks(broken, ERROR)


def test_arc_centre_must_be_equidistant_from_both_ends():
    # Same start and offsets, end point moved off the circle.
    broken = CLEAN.replace("N100 G02 X130.0000 Y110.0000 I0. J10.0000",
                           "N100 G02 X130.0000 Y140.0000 I0. J10.0000")
    assert "arc-radius" in checks(broken, ERROR)


def test_arc_within_tolerance_is_accepted():
    # 0.01 mm out — inside the 0.02 mm tolerance, which real posts need.
    ok = CLEAN.replace("N100 G02 X130.0000 Y110.0000 I0. J10.0000",
                       "N100 G02 X130.0100 Y110.0000 I0. J10.0000")
    assert "arc-radius" not in checks(ok)


def test_r_format_arc_is_not_offset_checked():
    ok = CLEAN.replace("N100 G02 X130.0000 Y110.0000 I0. J10.0000",
                       "N100 G02 X130.0000 Y110.0000 R10.0000")
    assert "arc-plane" not in checks(ok) and "arc-radius" not in checks(ok)


def test_program_ending_in_a_vertical_plane_is_an_error():
    broken = CLEAN.replace("N110 G00 Z25.4000", "N110 G19\nN115 G00 Z25.4000")
    assert "plane-restore" in checks(broken, ERROR)


# ── motion safety ────────────────────────────────────────────────────────────

def test_work_frame_z_move_under_g49_is_an_error():
    # Issue #15: with G49 active the control positions the spindle gauge line,
    # so the head sits roughly a tool length below what the number reads.
    broken = CLEAN.replace("N140 G00 G53 Z0", "N140 G00 Z25.4000")
    assert "g49-z-move" in checks(broken, ERROR)


def test_machine_frame_retract_under_g49_is_accepted():
    # The fix for the above: G53 is unambiguous whether or not comp is active.
    assert "g49-z-move" not in checks(CLEAN)


def test_cutting_move_with_the_spindle_stopped_is_an_error():
    assert "spindle-off" in checks(CLEAN.replace("N60 M03 S18000", "N60 M05"), ERROR)


def test_feed_move_without_a_feed_rate_is_an_error():
    assert "no-feed" in checks(CLEAN.replace("N80 G01 Z-1.0000 F1270.",
                                             "N80 G01 Z-1.0000"), ERROR)


def test_zero_feed_is_an_error():
    assert "no-feed" in checks(CLEAN.replace("F1270.", "F0"), ERROR)


def test_deep_fast_plunge_is_a_warning():
    broken = CLEAN.replace("N80 G01 Z-1.0000 F1270.", "N80 G01 Z-19.0500 F7620.")
    assert "plunge" in checks(broken, WARNING)
    assert "plunge" not in checks(broken, ERROR)


def test_rapid_below_the_retract_plane_is_a_warning_not_a_gate():
    # The job's retract plane is the maximum across every part in the nest, so
    # any part with a lower clearance plane trips this on every rapid it makes.
    # Real and worth surfacing; not provable enough to refuse to write the file.
    broken = CLEAN.replace("N70 G00 X100.0000 Y100.0000",
                           "N70 G00 Z5.0000\nN75 G00 X100.0000 Y100.0000")
    assert "rapid-below-retract" in checks(broken, WARNING)
    assert "rapid-below-retract" not in checks(broken, ERROR)


# ── modal hygiene ────────────────────────────────────────────────────────────

def test_g43_approach_without_g00_is_an_error():
    assert "modal-hygiene" in checks(CLEAN.replace("N50 G00 G43", "N50 G43"), ERROR)


def test_g53_retract_without_g00_is_an_error():
    assert "modal-hygiene" in checks(
        CLEAN.replace("N120 G00 G53 G49 Z0", "N120 G53 G49 Z0"), ERROR)


# ── envelope ─────────────────────────────────────────────────────────────────

# This mirrors collision.check_envelope, which checks the two axes differently:
# X keeps the edge margin because a hard stop sits outside it, Y takes no margin
# because the tool is free to hang off the end of the surface. The validator
# reads tool-centre coordinates out of the finished file with no radius to
# inflate them by, so on X it is the more permissive of the two — a backstop
# behind the placement gate, not the primary guard.
#
# The shipped surface bounds are X 61.493 → 1606.499, Y 24.994 → 3098.013.

WITH_LIMITS = {**ADVANCED,
               "machine_travel": {"x_min": 0.0, "x_max": 1524.0,
                                  "y_min": 0.0, "y_max": 3048.0}}


def test_an_unmeasured_axis_is_not_checked():
    # Z ships as null (issue #19) and is skipped rather than guessed at.
    assert "envelope" not in checks(CLEAN.replace("Z-5.0000", "Z-9999.0000"))


def test_move_past_a_configured_travel_limit_is_an_error():
    broken = CLEAN.replace("Y100.0000", "Y3100.0000")
    assert "envelope" in checks(broken, ERROR, WITH_LIMITS)


def test_y_takes_no_edge_margin_because_overhang_is_allowed():
    # 3044.023 mm — the actual max Y from nest_20260815_103400.nc. It sits
    # 3.98 mm short of the old 3048 bound, which the margin once made a
    # warning. Nothing obstructs that end of Y, so a tool centre still on the
    # surface is simply legal.
    inside = CLEAN.replace("Y100.0000", "Y3044.0230")
    assert "envelope" not in checks(inside, advanced=WITH_LIMITS)
    # Past the bound it is still an error — the centre may not leave the surface.
    assert "envelope" in checks(CLEAN.replace("Y100.0000", "Y3050.0000"),
                                ERROR, WITH_LIMITS)


def test_x_keeps_its_edge_margin_because_a_hard_stop_sits_outside_it():
    inside = CLEAN.replace("X100.0000", "X1520.0000")   # 4 mm short of 1524
    assert "envelope" in checks(inside, WARNING, WITH_LIMITS)
    assert "envelope" not in checks(inside, ERROR, WITH_LIMITS)


def test_x_is_checked_once_its_travel_is_configured():
    assert "envelope" in checks(CLEAN.replace("X100.0000", "X1600.0000"),
                                ERROR, WITH_LIMITS)


def test_machine_frame_moves_are_exempt_from_the_envelope():
    # The park traverse goes to G53 Y3048 by design, which is the limit itself.
    assert "envelope" not in checks(CLEAN, None, WITH_LIMITS)


# ── the reviewed files ───────────────────────────────────────────────────────
#
# Every defect below was found by hand, reading these three files line by line
# on 2026-08-15. The validator has to find each one on its own.

REVIEWED = pathlib.Path.home() / "Downloads"
FILES = {
    "2026-08-14": REVIEWED / "nest_20260814_090009.nc",
    "103400": REVIEWED / "nest_20260815_103400.nc",
    "105420": REVIEWED / "nest_20260815_105420.nc",
}

missing = pytest.mark.skipif(
    not all(p.exists() for p in FILES.values()),
    reason="reviewed output files not present in ~/Downloads",
)


def _checks(key):
    return checks(FILES[key].read_text(errors="replace"))


@missing
def test_reviewed_file_g19_plane_bug_is_caught():
    # N186510: "G19 G03 X1539.8750 Z0. I-1.2700" — an I offset in a YZ plane,
    # next to a swapped X word. This is the block that alarmed the control.
    found = _checks("2026-08-14")
    assert "arc-plane" in found
    assert "arc-radius" in found


@missing
@pytest.mark.parametrize("key", list(FILES))
def test_reviewed_files_all_have_the_g49_park_move(key):
    # N186630 / N41160 / N186630 — "G00 Z<safeZ>" in G54 with G49 active.
    assert "g49-z-move" in _checks(key)


@missing
def test_reviewed_file_tool_change_inherits_g01():
    # N40980 and N41010 in the two-tool job: neither the G53 retract nor the
    # next G43 approach asserts G00.
    assert "modal-hygiene" in _checks("103400")


@missing
@pytest.mark.parametrize("key", list(FILES))
def test_every_reviewed_file_would_have_been_blocked(key):
    fs = validate_gcode(FILES[key].read_text(errors="replace"), ADVANCED)
    assert has_errors(fs), f"{key} produced no hard finding"


@missing
@pytest.mark.parametrize("key", list(FILES))
def test_reviewed_files_clear_the_measured_envelope(key):
    # The 2026-08-15 review flagged 3044.023 and 3041.365 as crowding the end of
    # Y travel. That was measured against 3048, which is the park position and
    # not the end of anything. The surface actually runs to 3098.013, and Y is
    # open at that end besides — so all three files are clean on the envelope,
    # which matches the fact that every one of them ran without alarming.
    # Their other defects are asserted above; this pins that the envelope no
    # longer manufactures a finding on top of them.
    assert "envelope" not in checks(FILES[key].read_text(errors="replace"))


def test_format_findings_reports_counts():
    fs = validate_gcode(CLEAN.replace("N50 G00 G43", "N50 G43"), ADVANCED)
    text = format_findings(fs)
    assert "1 error(s)" in text and "modal-hygiene" in text


def test_format_findings_on_a_clean_file():
    assert format_findings([]) == "G-code validation: no findings."
