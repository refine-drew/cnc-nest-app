"""
Runtime estimator for VCarve- and Fusion-posted G-code.

Walks a line stream tracking modal units (G20/G70 inch, G21/G71 mm), modal motion
mode, modal plane, modal feedrate and current XYZ; builds a segment list; plans it
with look-ahead; and integrates a trapezoidal velocity profile over it. Returns
cutting / rapid / tool-change seconds plus `tool_change_count`.

**The machine accelerates, and pretending otherwise was not a small error.**
`distance / feedrate` prices a machine that reaches full speed instantly and turns
corners without slowing. On `T24 Test.nc` — three parts, five tool blocks, timed at
10m53s (653 s) on the SS2, 2026-08-21 — that model says 7m20s. The median G1 XY move
in that file is **0.495 mm**; at F2540 it lasts 12 ms, which is not long enough to
reach F2540. The *rapids alone* go from 25 s to 2m52s once acceleration is modelled,
on identical geometry: a short rapid is all ramp and no cruise.

The correction is not a constant and cannot be replaced by one: measured across this
shop's library before the model was fitted, it ranged from 1.11x to 2.11x depending on
the file. It falls out of segment length and corner angle, so it has to be the physics
— a single multiplier would fit one job and be wrong on the next.

Three stages, one pass each:

  1. `_scan_segments`  — knows G-code, knows no physics. Emits `_Segment`s.
  2. `_plan_velocities` — knows physics, knows no G-code. Junction speeds, then a
     backward pass so every junction is one the *next* segment can decelerate from.
  3. `_integrate`      — ramp-up / cruise / ramp-down per segment.

Stages 2 and 3 take only `List[_Segment]` and `MotionLimits`, which is what makes the
physics testable without writing G-code. If a second module ever needs them, that is
when they move out; today the only caller is this file.

Conventions:
  - `F` is the resultant feed along the programmed **3D** path, per Fanuc/Syntec. A
    ramp that moves X and Z together is timed on its true path length, and a Z-only
    plunge is timed rather than skipped (it used to be "ignored by convention" — a
    convention with nothing behind it: a 19 mm plunge at 100 mm/min is 11 s, and
    there are dozens per part).
  - The plane word is modal and is tracked, because a G19 arc's centre offsets are
    J/K. Reading them as I/J timed every vertical lead-in ramp at zero.
  - Default rapid rate 1800 in/min; default tool change 57 s (27 s swap + 30 s
    touch-off) — see the constants below.
  - Coordinates default to mm if no units word is seen, matching `gcode_parser`.
"""
from dataclasses import dataclass
from math import atan2, cos, hypot, pi, sin, sqrt
from typing import Iterable, List, Optional, Tuple

# Pre-compiled patterns. Re-use the parser's for X/Y/Z extraction and for the modal
# motion and plane rules so we stay in sync with what the rest of the app treats as
# a coordinate, a move, and a work plane.
import re
from gcode_parser import (
    COORD_PATTERN, TOOL_CHANGE_PATTERN, is_modal_move, motion_mode, plane_mode,
)

_F_PATTERN = re.compile(r"\bF\s*([0-9.]+)")
_IJK_PATTERN = re.compile(r"([IJK])\s*([+-]?\d*\.?\d+)")
_SPINDLE_STOP_PATTERN = re.compile(r"\bM0?5\b|\bM30\b", re.IGNORECASE)
_SPINDLE_SPEED_PATTERN = re.compile(r"\bS\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_G20_PATTERN = re.compile(r"\bG20\b")
_G21_PATTERN = re.compile(r"\bG21\b")
_G70_PATTERN = re.compile(r"\bG70\b")
_G71_PATTERN = re.compile(r"\bG71\b")

MM_PER_INCH = 25.4

DEFAULT_RAPID_IPM = 1800.0                                # Smartshop 2
DEFAULT_RAPID_MM_PER_MIN = DEFAULT_RAPID_IPM * MM_PER_INCH

# **This is an EFFECTIVE acceleration, not the machine's acceleration parameter, and
# the difference matters.** It is one number fitted to timed jobs:
#
#   T24 Test.nc     653 s   2026-08-21, auto tool touch-off OFF   <- the fit
#   (9) 18G Test.nc 697 s   2026-08-20, posture NOT RECORDED      <- cross-check only
#
# 189 mm/s^2 is about 0.019 g, which is far too low to be a 5x10 gantry's real
# acceleration. It is low because it is absorbing everything else that slows the
# machine on a dense toolpath and is not modelled here: jerk limiting (the Syntec
# ramps its ramps; a pure trapezoid is optimistic), the control's finite block
# processing rate, and a look-ahead buffer that is a few hundred blocks rather than
# the whole file. **So do not "correct" this against a parameter read off the
# control — a disagreement there is expected, not a bug.**
#
# **It moved 240 -> 189 on 2026-08-21, and both halves of that move were costs the
# model was charging to the wrong thing.**
#
#   -60 s  the posture. The 240 fit assumed `(9) 18G Test.nc` ran with auto tool
#          touch-off ON and charged it 2 x 57 s. Nobody wrote down what the control
#          was set to, and if it was OFF the same 697 s buys 60 s more *motion* — 9%
#          of the job, absorbed into acceleration. **A timed job with no posture
#          recorded beside it is worth roughly nothing.**
#   -20 s  spindle spin-up, which nothing was charging at all. 5 s per `S` word, four
#          of them in the fitted job (see `SPINDLE_START_SECONDS`).
#
# `T24 Test.nc` is the fit now because its posture is known (OFF) and its window is
# known, and the earlier job survives as a cross-check: at 189 it predicts 674 s
# against its observed 697 s, 3.4% low, where the ON reading is 5.2% high. Both
# corrections moved the fit in the same direction for the same reason — a cost the
# model does not name is a cost acceleration ends up wearing.
#
# The lumping is a deliberate choice, not laziness. Priced as a plausible 1000
# mm/s^2 plus a per-block floor, the reference file needs 29 ms/block (34 blocks/s),
# and across the whole library that two-parameter model and this one-parameter model
# disagree by at most 9% — indistinguishable from two timed jobs. A second
# parameter that cannot be fitted is worse than none.
#
# **What the two observations still cannot separate: acceleration from a fixed
# per-job overhead.** Both jobs are ~11 minutes, so "motion is N% slower than
# modelled" and "every job costs a constant the model never sees" still fit about
# equally well — program load and the operator's start delay are real and unmodelled.
# Naming the spindle spin-up took the largest identifiable piece out of that pool
# (it scales per tool block, not per job, so it is *not* the same shape), but the
# remainder is not resolved. **One timed job of a different length settles it**: on a
# 3-minute job the first story predicts a ~20 s miss and the second a ~55 s one. Time
# a short job next, not another 11-minute one. Until then this stays one free
# parameter, and `calibrate_runtime.py` prints the constant as a diagnostic it
# deliberately does not adopt.
#
# Acceleration is the *single* sensitive parameter; junction deviation is nearly
# inert (0.01 -> 0.1 mm moves the total by 1%), which is what makes fitting one
# number against a couple of jobs meaningful at all. Re-fit with
# `calibrate_runtime.py`, and **time the most acceleration-sensitive job you have**:
# a job whose prediction barely moves with accel cannot tell you what accel is; a job
# that swings is where a wrong *model* shows up rather than a wrong number.
# `calibrate_runtime.py --check` over the library ranks them.
#
# Changing this means changing `config.json`,
# `test_the_timed_job_predicts_its_observed_cycle` and CLAUDE.md together.
DEFAULT_ACCEL_MM_S2 = 189.0
DEFAULT_JUNCTION_DEVIATION_MM = 0.05

# Timed on the SS2, 2026-08-17 (issue #6). Two constants, not one, because the
# safety posture (#8) switches exactly one of them on and off:
#
#   swap alone ........ 27 s   carousel move, spindle release/clamp
#   swap + touch-off .. 57 s   -> touch-off costs 30 s
#
# Touch-off is charged on **every** `T# M06`, not once per distinct tool, and
# including the first: with "auto tool" left on the control measures at every call,
# so a tool the pass-index walk returns to is measured again. That is why the change
# *count* (issue #7) had to be honest before this number meant anything.
#
# The other posture — touch off once as each tool is loaded, auto tool off — moves
# the 30 s out of the cut cycle into setup. `tool_change_seconds_for(False)` prices
# it; the difference between the postures is exactly
# `TOUCH_OFF_SECONDS * tool_change_count`.
TOOL_SWAP_SECONDS = 27.0
TOUCH_OFF_SECONDS = 30.0
DEFAULT_TOOL_CHANGE_SECONDS = TOOL_SWAP_SECONDS + TOUCH_OFF_SECONDS   # 57.0

# Spindle spin-up, observed on the SS2 2026-08-21 (operator): the control holds for
# about 5 s **every time it sees an `S` word**, not only when the speed changes and
# not only from a stop.
#
# **This is a measured constant, like the two above, and that is what lets it exist
# alongside the fitted acceleration.** `DEFAULT_ACCEL_MM_S2` is the model's one *free*
# parameter and the rule against a second one is about free parameters — a cost that
# was timed with a stopwatch is an observation, and folding an observation into the
# acceleration is exactly the mistake the posture confound already made once. What it
# is not is a per-job constant: it scales with the number of emitted tool blocks, so it
# does not fit a job of a different length the way a program-load offset would.
#
# Charged per `S` word in the emitted stream, which is per **tool block** — the
# generator's `_dedup_spindle` leaves exactly one per block. That makes that function
# worth 5 s per line it drops, where before it was tidiness.
SPINDLE_START_SECONDS = 5.0

# Which two of I/J/K carry the centre offset, and which two axes the arc sweeps in,
# for each modal plane. `gcode_validator` tabulates the same thing for its own
# purposes; it deliberately shares no state with the code it checks, so this is a
# restatement rather than an import.
_PLANE_GEOMETRY = {
    17: ("I", "J", 0, 1, 2),   # XY plane, normal Z
    18: ("I", "K", 0, 2, 1),   # XZ plane, normal Y
    19: ("J", "K", 1, 2, 0),   # YZ plane, normal X
}

_ZERO3 = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class MotionLimits:
    """The machine's motion model: how fast it rapids, and how hard it can change.

    Frozen because these are read on every parse of every library file and nothing
    should be able to mutate the machine model mid-walk. `from_config` is the one
    place `rapid_ipm` (inches per minute, the unit the machine quotes and is
    re-measured in) becomes mm/min.
    """
    rapid_mm_per_min: float = DEFAULT_RAPID_MM_PER_MIN
    accel_mm_s2: float = DEFAULT_ACCEL_MM_S2
    junction_deviation_mm: float = DEFAULT_JUNCTION_DEVIATION_MM

    @classmethod
    def from_config(cls, advanced: Optional[dict]) -> "MotionLimits":
        adv = advanced or {}
        return cls(
            rapid_mm_per_min=float(adv.get("rapid_ipm", DEFAULT_RAPID_IPM)) * MM_PER_INCH,
            accel_mm_s2=float(adv.get("accel_mm_s2", DEFAULT_ACCEL_MM_S2)),
            junction_deviation_mm=float(
                adv.get("junction_deviation_mm", DEFAULT_JUNCTION_DEVIATION_MM)
            ),
        )


def tool_change_seconds_for(auto_tool_touch_off: bool = True) -> float:
    """Seconds per `T# M06` under the given control posture.

    With "auto tool" touch-off on (the shop's chosen posture, issue #8) the control
    measures at every tool call, so the full 57 s lands inside the cut cycle. With it
    off the operator touches each tool off as it is loaded and only the 27 s carousel
    swap is left in the cycle — the 30 s does not vanish, it moves to setup.
    """
    return DEFAULT_TOOL_CHANGE_SECONDS if auto_tool_touch_off else TOOL_SWAP_SECONDS


@dataclass
class _Segment:
    """One planned move. `dir_out` differs from `dir_in` only for arcs, which is what
    lets the junction planner treat an arc and a line identically."""
    length: float                          # mm along the path; true arc length for G2/G3
    v_max: float                           # mm/s ceiling: feed, rapid rate, or centripetal cap
    dir_in: Tuple[float, float, float]     # unit tangent entering
    dir_out: Tuple[float, float, float]    # unit tangent leaving
    kind: str                              # "cut" | "rapid" | "stop"
    v_start: float = 0.0                   # rewritten by the planner
    v_end: float = 0.0


def estimate_lines_runtime(
    lines: Iterable[str],
    *,
    limits: Optional[MotionLimits] = None,
    tool_change_seconds: float = DEFAULT_TOOL_CHANGE_SECONDS,
    spindle_start_seconds: float = SPINDLE_START_SECONDS,
) -> dict:
    """
    Estimate runtime for a raw G-code line stream.

    Returns a dict: {seconds, cutting, rapid, tool_changes, spindle} — each in
    seconds — plus `tool_change_count` and `spindle_start_count`, the number of
    `T# M06` and `S` events seen. The counts are reported separately because they are
    invariant while the seconds are not: they survive the `0.0` costs that per-part
    runtimes are parsed with, and they are what reprices a job when the posture
    changes which of `TOOL_SWAP_SECONDS` / `TOUCH_OFF_SECONDS` applies.

    **Both event costs default to their measured value and both are passed as `0.0`
    for a per-part runtime**, for the same reason: the generator merges same-tool
    passes across parts, so neither a part's own change count nor its own spindle
    starts survive into the merged job. They are charged once per emitted block at
    job level instead.

    Arguments after `lines` are keyword-only on purpose: the old third positional
    argument was `tool_change_seconds` and the new one is `limits`, so a stale
    positional call must fail loudly rather than silently time a job with a float
    as its machine model.
    """
    lim = limits or MotionLimits()
    segments, change_count, spindle_count = _scan_segments(lines, lim)
    _plan_velocities(segments, lim)
    cutting_s, rapid_s = _integrate(segments, lim)
    change_s = change_count * tool_change_seconds
    spindle_s = spindle_count * spindle_start_seconds

    return {
        "seconds": cutting_s + rapid_s + change_s + spindle_s,
        "cutting": cutting_s,
        "rapid": rapid_s,
        "tool_changes": change_s,
        "tool_change_count": change_count,
        "spindle": spindle_s,
        "spindle_start_count": spindle_count,
    }


def estimate_passes_runtime(
    passes: List,
    *,
    limits: Optional[MotionLimits] = None,
    tool_change_seconds: float = DEFAULT_TOOL_CHANGE_SECONDS,
    spindle_start_seconds: float = SPINDLE_START_SECONDS,
    unit_scale: float = 1.0,
) -> dict:
    """Convenience wrapper that flattens a list of GcodePass objects.

    `unit_scale` seeds the units, because both posts write their `G70`/`G71` in the
    file header — *before* the first `T# M06` — which puts it outside every pass.
    Walking the passes alone therefore never sees a units word and would default to
    mm for an inch-posted file, a 25.4x error. Nothing in this library posts inches
    today, so pass the file's scale in rather than trusting the default.
    See `gcode_parser.file_unit_scale`.
    """
    all_lines: List[str] = []
    for p in passes:
        all_lines.extend(p.lines)
    # Prepend a synthetic units word rather than threading scale through the walk:
    # one line, and it makes the pass stream self-describing the way the file is.
    if unit_scale != 1.0:
        all_lines = ["G70"] + all_lines
    return estimate_lines_runtime(
        all_lines, limits=limits, tool_change_seconds=tool_change_seconds,
        spindle_start_seconds=spindle_start_seconds,
    )


# ── stage 1: the walk ─────────────────────────────────────────────────────────
# Knows G-code. Knows no physics.

def _scan_segments(
    lines: Iterable[str], limits: MotionLimits,
) -> Tuple[List[_Segment], int, int]:
    """Walk the stream and emit one `_Segment` per move, plus the event counts.

    The events — tool changes and spindle speed commands — are counted here rather
    than priced here: this stage knows G-code and no physics, and what a swap or a
    spin-up *costs* is a machine measurement the caller supplies.
    """
    segments: List[_Segment] = []
    change_count = 0
    spindle_count = 0

    # Default to mm — matches gcode_parser (mm everywhere) and VCarve G71 output.
    unit_scale = 1.0
    # Position starts at the origin. This is a known approximation: the machine is at
    # the tool changer when a program starts, not at the work origin, so the first
    # rapid of a file is fictional and acceleration has just made it more expensive
    # (two rapids over 500 mm in the reference file, ~8.7 s of its 48.8 s rapid
    # total). `extract_file_segments` makes the opposite choice — it starts *unknown*
    # — because a fictional line in a preview is a visible defect while a fictional
    # rapid in a total is a small one, and in a merged master the first move after a
    # tool change is a real machine move. Both alternatives here are wrong in a
    # different direction; this one is at least stable.
    cur_x = cur_y = cur_z = 0.0
    cur_f = 0.0        # in the file's units; scaled at use
    mode: Optional[int] = None
    plane = 17

    for raw in lines:
        if not raw:
            continue
        s = raw.strip()
        if not s or s.startswith("("):
            continue

        if _G20_PATTERN.search(s) or _G70_PATTERN.search(s):
            unit_scale = MM_PER_INCH
        elif _G21_PATTERN.search(s) or _G71_PATTERN.search(s):
            unit_scale = 1.0

        if TOOL_CHANGE_PATTERN.search(s):
            change_count += 1
            # A carousel swap is a full stop. Without the barrier the planner carries
            # speed from the last cut of one tool into the first move of the next —
            # momentum across an event where the machine is stationary for most of a
            # minute. The barrier is a zero-length segment rather than a flag so
            # neither planning pass needs a special case.
            segments.append(_barrier())
            continue
        if _SPINDLE_STOP_PATTERN.search(s):
            segments.append(_barrier())
            # M05/M30 may share a line with a move (the park block does); fall
            # through rather than `continue` so that move is still counted.

        if _SPINDLE_SPEED_PATTERN.search(s):
            # Every `S` the control sees, not every *change* of speed: it holds for
            # about 5 s either way (observed 2026-08-21). Counted before the move on
            # the same line, and a barrier for the same reason a tool change is —
            # the machine is standing still while the spindle comes up, so carrying
            # look-ahead speed across it would be momentum through a full stop.
            spindle_count += 1
            segments.append(_barrier())

        f_match = _F_PATTERN.search(s)
        if f_match:
            cur_f = float(f_match.group(1))

        plane = plane_mode(s, plane)
        mode = motion_mode(s, mode)
        if not is_modal_move(s, mode):
            continue

        new_x, new_y, new_z = cur_x, cur_y, cur_z
        for axis, val in COORD_PATTERN.findall(s):
            v = float(val) * unit_scale
            a = axis.upper()
            if a == "X":
                new_x = v
            elif a == "Y":
                new_y = v
            elif a == "Z":
                new_z = v

        if mode == 0:
            seg = _line_segment(
                cur_x, cur_y, cur_z, new_x, new_y, new_z,
                limits.rapid_mm_per_min / 60.0, "rapid",
            )
        elif mode == 1:
            seg = _line_segment(
                cur_x, cur_y, cur_z, new_x, new_y, new_z,
                cur_f * unit_scale / 60.0, "cut",
            )
        else:
            offsets = {}
            for axis, val in _IJK_PATTERN.findall(s):
                offsets[axis.upper()] = float(val) * unit_scale
            seg = _arc_segment(
                cur_x, cur_y, cur_z, new_x, new_y, new_z,
                offsets, plane, cur_f * unit_scale / 60.0,
                clockwise=(mode == 2), limits=limits,
            )

        if seg is not None:
            segments.append(seg)
        elif mode != 0:
            # A move we could not price — a G1 with no modal feed, or a degenerate
            # arc. The position still advances, so break the look-ahead chain rather
            # than joining two segments the machine never ran consecutively.
            segments.append(_barrier())

        cur_x, cur_y, cur_z = new_x, new_y, new_z

    return segments, change_count, spindle_count


def _barrier() -> _Segment:
    return _Segment(0.0, 0.0, _ZERO3, _ZERO3, "stop")


def _line_segment(x0, y0, z0, x1, y1, z1, v_max: float, kind: str) -> Optional[_Segment]:
    """A straight move. `F` is the resultant feed along the 3D path (Fanuc/Syntec),
    so a ramp and a plunge are both timed on their true length."""
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    length = sqrt(dx * dx + dy * dy + dz * dz)
    if length <= 0.0 or v_max <= 0.0:
        return None
    u = (dx / length, dy / length, dz / length)
    return _Segment(length, v_max, u, u, kind)


def _arc_segment(
    x0, y0, z0, x1, y1, z1,
    offsets: dict, plane: int, feed_mm_s: float,
    clockwise: bool, limits: MotionLimits,
) -> Optional[_Segment]:
    """A G2/G3 move, in whichever plane is modal.

    The plane decides which two of I/J/K carry the centre offset and which two axes
    sweep; the third axis is the helical component and is included in both the length
    and the tangents, so the junction into a following plunge comes out right.
    """
    if feed_mm_s <= 0.0:
        return None
    key_u, key_v, ax_u, ax_v, ax_n = _PLANE_GEOMETRY.get(plane, _PLANE_GEOMETRY[17])
    i = offsets.get(key_u, 0.0)
    j = offsets.get(key_v, 0.0)
    radius = hypot(i, j)
    if radius == 0.0:
        return None

    start = (x0, y0, z0)
    end = (x1, y1, z1)
    u0, v0 = start[ax_u], start[ax_v]
    u1, v1 = end[ax_u], end[ax_v]
    cu, cv = u0 + i, v0 + j

    a0 = atan2(v0 - cv, u0 - cu)
    a1 = atan2(v1 - cv, u1 - cu)
    sweep = (a0 - a1) if clockwise else (a1 - a0)
    # A near-zero sweep for matching endpoints is a full circle, not a no-op.
    while sweep <= 1e-9:
        sweep += 2 * pi

    normal_travel = end[ax_n] - start[ax_n]
    arc_len = radius * sweep
    length = hypot(arc_len, normal_travel)
    if length <= 0.0:
        return None

    # Tangent in the sweep plane: perpendicular to the radius, sign by direction.
    # `sign` is +1 for CCW (angle increasing) and -1 for CW.
    sign = -1.0 if clockwise else 1.0
    helix = normal_travel / length          # component along the plane normal
    planar = arc_len / length               # component in the sweep plane

    def tangent_at(angle: float) -> Tuple[float, float, float]:
        # d/dtheta (cos, sin) = (-sin, cos), times the sweep direction. Scaled by
        # `planar` and combined with `helix` so the result is a unit 3-vector.
        out = [0.0, 0.0, 0.0]
        out[ax_u] = -sign * sin(angle) * planar
        out[ax_v] = sign * cos(angle) * planar
        out[ax_n] = helix
        return (out[0], out[1], out[2])

    dir_in = tangent_at(a0)
    dir_out = tangent_at(a1)

    # Centripetal limit: the machine cannot hold `feed` around a radius tighter than
    # feed^2 / accel. Rarely binding at this shop's feeds (R=3.175 mm allows
    # 3390 mm/min against F2540) but it is three lines and it is right.
    v_max = min(feed_mm_s, sqrt(limits.accel_mm_s2 * radius))
    return _Segment(length, v_max, dir_in, dir_out, "cut")


# ── stage 2: look-ahead ───────────────────────────────────────────────────────
# Knows physics. Knows no G-code.

def _junction_velocity(a: _Segment, b: _Segment, limits: MotionLimits) -> float:
    """Speed the machine may carry through the corner between `a` and `b`.

    The GRBL junction-deviation model: fit a circle of the given deviation into the
    corner and take the speed that circle's centripetal limit allows. A gentler
    corner fits a bigger circle and passes more speed.
    """
    if a.kind == "stop" or b.kind == "stop":
        # Explicit, because a zero direction vector dots to cos=0, which would read
        # as a 90-degree corner rather than a full stop.
        return 0.0
    dot = a.dir_out[0] * b.dir_in[0] + a.dir_out[1] * b.dir_in[1] + a.dir_out[2] * b.dir_in[2]
    dot = max(-1.0, min(1.0, dot))
    # GRBL negates the dot product before halving, and the negation is the whole
    # formula: `theta` is the angle between the *reversed* incoming direction and the
    # outgoing one, so it is pi for a straight join and 0 for a reversal. Using the
    # un-negated dot inverts the model — gentle corners get penalised harder than
    # right angles — which passes every collinear and reversal test, because both of
    # those are special-cased below, and fails only in between.
    sin_half_theta = sqrt((1.0 + dot) / 2.0)
    if sin_half_theta >= 1.0 - 1e-9:           # collinear: no corner to slow for
        return min(a.v_max, b.v_max)
    if sin_half_theta <= 1e-9:                 # full reversal: dead stop
        return 0.0
    radius = limits.junction_deviation_mm * sin_half_theta / (1.0 - sin_half_theta)
    return min(sqrt(limits.accel_mm_s2 * radius), a.v_max, b.v_max)


def _plan_velocities(segments: List[_Segment], limits: MotionLimits) -> None:
    """Set every segment's entry and exit speed, in place.

    Junction speeds first, then a backward pass so each junction is one the machine
    can actually decelerate *from* — a corner that allows 300 mm/s is no use if the
    0.4 mm segment after it has to stop.
    """
    n = len(segments)
    if n == 0:
        return

    for i in range(n):
        segments[i].v_start = segments[i].v_max
        segments[i].v_end = segments[i].v_max
    # The file starts and ends at rest.
    segments[0].v_start = 0.0
    segments[-1].v_end = 0.0

    for i in range(1, n):
        vj = _junction_velocity(segments[i - 1], segments[i], limits)
        segments[i - 1].v_end = min(segments[i - 1].v_end, vj)
        segments[i].v_start = min(segments[i].v_start, vj)

    accel = limits.accel_mm_s2
    for i in range(n - 1, -1, -1):
        seg = segments[i]
        reachable = sqrt(seg.v_end * seg.v_end + 2.0 * accel * seg.length)
        seg.v_start = min(seg.v_start, reachable)
        if i > 0:
            segments[i - 1].v_end = min(segments[i - 1].v_end, seg.v_start)


# ── stage 3: integration ──────────────────────────────────────────────────────

def _integrate(segments: List[_Segment], limits: MotionLimits) -> Tuple[float, float]:
    """Sum ramp-up / cruise / ramp-down seconds into (cutting, rapid)."""
    cutting_s = 0.0
    rapid_s = 0.0
    accel = limits.accel_mm_s2
    # Forward feasibility: the backward pass guarantees every entry speed can be shed
    # in time, but not that it can be *reached*. A 0.4 mm segment entering at rest
    # cannot leave at 300 mm/s however permissive the corner after it is, so the exit
    # is clamped to what this segment can accelerate to and that becomes the next
    # segment's entry.
    v_in = 0.0

    for seg in segments:
        if seg.kind == "stop" or seg.length <= 0.0:
            v_in = 0.0
            continue
        v_in = min(v_in, seg.v_start, seg.v_max)
        v_out = min(seg.v_end, seg.v_max, sqrt(v_in * v_in + 2.0 * accel * seg.length))
        t = _segment_seconds(seg.length, seg.v_max, accel, v_in, v_out)
        if seg.kind == "rapid":
            rapid_s += t
        else:
            cutting_s += t
        v_in = v_out

    return cutting_s, rapid_s


def _segment_seconds(length: float, v_max: float, accel: float,
                     v_in: float, v_out: float) -> float:
    """Time along `length`, entering at `v_in`, leaving at `v_out`, capped at `v_max`.

    With `accel = inf` this reduces exactly to `length / v_max` — the arithmetic this
    model replaced — which is what the geometry tests rely on.
    """
    if length <= 0.0 or v_max <= 0.0:
        return 0.0
    v_peak = min(sqrt(max((2.0 * accel * length + v_in * v_in + v_out * v_out) / 2.0, 0.0)), v_max)
    d_acc = (v_peak * v_peak - v_in * v_in) / (2.0 * accel) if v_peak > v_in else 0.0
    d_dec = (v_peak * v_peak - v_out * v_out) / (2.0 * accel) if v_peak > v_out else 0.0
    seconds = (max(v_peak - v_in, 0.0) + max(v_peak - v_out, 0.0)) / accel
    cruise = length - d_acc - d_dec
    if cruise > 0.0:
        seconds += cruise / v_peak
    return seconds


def format_duration(seconds: float) -> str:
    """`45s`, `12m 30s`, or `1h 23m` — matches the frontend formatter."""
    if seconds is None or seconds < 0:
        return "—"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"
