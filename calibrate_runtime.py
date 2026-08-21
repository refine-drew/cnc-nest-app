#!/usr/bin/env python3
"""
Runtime calibration — standalone CLI.

Fits `advanced.accel_mm_s2` against jobs you have actually timed on the machine.

The runtime estimator models acceleration with a trapezoidal velocity profile and
look-ahead (see `runtime_estimator`). Its one free parameter is an **effective
acceleration**: not the machine's acceleration parameter, but a single number that
also absorbs jerk limiting, the control's block processing rate, and a look-ahead
buffer shorter than the whole file. It can only be fitted from wall-clock
observations, which is what this script does.

Usage:
    python calibrate_runtime.py FILE=DURATION [FILE=DURATION ...]
    python calibrate_runtime.py --observations timed.csv
    python calibrate_runtime.py --check FILE [FILE ...]

DURATION accepts `697`, `697s`, `11:37`, `1:02:15` or `11m37s`. `11:37` is what the
control displays, and making the operator convert to seconds is exactly where a
transcription error enters a calibration that may rest on two or three numbers.

Point it at the files the machine actually ran — usually generated masters in the
output directory, not library parts. Tool changes are charged at the current posture;
pass `--no-auto-tool` if the timed jobs ran with auto tool touch-off off.

**Record the posture with the stopwatch.** It is the one input the file cannot tell
you, it moves the answer by 30 s per tool change, and a fit that guesses it wrong
buries the guess in the acceleration — which is exactly what happened to the first
calibration (2026-08-20, corrected 2026-08-21; see `DEFAULT_ACCEL_MM_S2`).

`--to-end-of-job-change` matches the window an operator can actually watch: the cut
finishing, before the carousel loads the next job's first tool and the gantry parks.
It cuts each file at the park block, so the end-of-job change and the two G53
traverses are outside both the observation and the prediction.

**It never writes config.json.** It prints the number and you paste it. A fit over
two or three observations is a judgement, and a script that silently rewrites the
machine model is how a bad fit becomes invisible — the same reasoning that keeps
`pocket_map` from writing back a default slot.

What to read in the output:

  - **The per-bucket columns.** A miss that lives entirely in the rapid column means
    the rapid *rate* is wrong, not the acceleration; fitting accel to absorb it would
    corrupt the cutting estimate on every other job. `changes` and `spindle` are
    **measured costs, not fitted ones** (27/57 s per tool change, 5 s per `S` word) —
    if the miss is the size of one of those columns, check its count in the file
    before touching the acceleration.
  - **The implied fixed overhead.** With two or more observations this also fits
    `(accel, constant)` and reports the constant. A systematic +40 s on every job is
    program load and the operator's start delay — a fixed cost, not an acceleration
    error. It is printed as a diagnostic and deliberately not adopted. Spindle
    spin-up used to be the first suspect here and is now modelled explicitly, per
    `S` word rather than per job.
  - **`--jd-sweep`.** Acceleration and junction deviation trade off against each
    other. Fitting one while holding the other at an assumed value is only honest if
    the coupling is visible.
"""
import argparse
import csv
import math
import re
import sys
from pathlib import Path

from config import load_config
from runtime_estimator import (
    DEFAULT_JUNCTION_DEVIATION_MM, MM_PER_INCH, MotionLimits,
    estimate_lines_runtime, format_duration, tool_change_seconds_for,
)

# Search bracket for the fit, in mm/s^2. Wide enough to contain both a real gantry
# acceleration and the much lower effective figure a dense toolpath produces.
ACCEL_MIN, ACCEL_MAX = 20.0, 20000.0
SCAN_STEPS = 60

_DURATION_PATTERNS = (
    re.compile(r"^(?P<s>\d+(?:\.\d+)?)s?$"),
    re.compile(r"^(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)$"),
    re.compile(r"^(?:(?P<h>\d+)h)?\s*(?:(?P<m>\d+)m)?\s*(?:(?P<s>\d+(?:\.\d+)?)s)?$"),
)


def parse_duration(text: str) -> float:
    """Seconds from `697`, `697s`, `11:37`, `1:02:15` or `11m37s`."""
    t = text.strip().lower()
    if not t:
        raise ValueError("empty duration")
    for pattern in _DURATION_PATTERNS:
        m = pattern.match(t)
        if not m:
            continue
        parts = m.groupdict()
        if not any(parts.values()):
            continue
        return (
            float(parts.get("h") or 0) * 3600
            + float(parts.get("m") or 0) * 60
            + float(parts.get("s") or 0)
        )
    raise ValueError(f"cannot read {text!r} as a duration (try 11:37, 11m37s or 697)")


def _predict(lines, accel, jd, rapid_mm_per_min, change_seconds) -> dict:
    return estimate_lines_runtime(
        lines,
        limits=MotionLimits(
            rapid_mm_per_min=rapid_mm_per_min,
            accel_mm_s2=accel,
            junction_deviation_mm=jd,
        ),
        tool_change_seconds=change_seconds,
    )


def _residual_cost(observations, accel, jd, rapid, change_seconds) -> float:
    """Sum of squared *relative* residuals. Relative so a 12-minute job and a
    3-minute job weigh equally rather than the long one dominating."""
    total = 0.0
    for obs in observations:
        got = _predict(obs["lines"], accel, jd, rapid, change_seconds)["seconds"]
        total += ((got - obs["observed"]) / obs["observed"]) ** 2
    return total


def fit_accel(observations, jd, rapid, change_seconds) -> float:
    """The acceleration that best explains the observations.

    Predicted time is strictly monotonically decreasing in acceleration — pinned by
    `test_halving_the_acceleration_increases_every_estimate` — so a single observation
    is a clean bisection. Several observations are not provably unimodal across files,
    which is why a coarse log scan brackets the minimum before refinement.
    """
    if len(observations) == 1:
        lines, target = observations[0]["lines"], observations[0]["observed"]
        lo, hi = ACCEL_MIN, ACCEL_MAX
        for _ in range(80):
            mid = math.sqrt(lo * hi)
            if _predict(lines, mid, jd, rapid, change_seconds)["seconds"] > target:
                lo = mid
            else:
                hi = mid
        return math.sqrt(lo * hi)

    ratio = (ACCEL_MAX / ACCEL_MIN) ** (1.0 / (SCAN_STEPS - 1))
    grid = [ACCEL_MIN * ratio ** i for i in range(SCAN_STEPS)]
    costs = [_residual_cost(observations, a, jd, rapid, change_seconds) for a in grid]
    best = min(range(len(grid)), key=lambda i: costs[i])
    lo = grid[max(best - 1, 0)]
    hi = grid[min(best + 1, len(grid) - 1)]

    # Golden-section refinement inside the bracketing triple.
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    for _ in range(60):
        c = hi - phi * (hi - lo)
        d = lo + phi * (hi - lo)
        if _residual_cost(observations, c, jd, rapid, change_seconds) < \
           _residual_cost(observations, d, jd, rapid, change_seconds):
            hi = d
        else:
            lo = c
    return (lo + hi) / 2.0


def fit_accel_and_overhead(observations, jd, rapid, change_seconds):
    """Also fit a per-job constant, as a diagnostic only.

    A systematic offset on every job is program load and the operator's start delay —
    a fixed cost. Letting acceleration absorb it would corrupt the estimate on jobs of
    a different length. (Spindle spin-up used to be on that list; it is modelled
    explicitly now, per `S` word, which is not per job and so does not fit here.)
    """
    if len(observations) < 2:
        return None, None
    best = (float("inf"), None, None)
    ratio = (ACCEL_MAX / ACCEL_MIN) ** (1.0 / (SCAN_STEPS - 1))
    for i in range(SCAN_STEPS):
        accel = ACCEL_MIN * ratio ** i
        preds = [_predict(o["lines"], accel, jd, rapid, change_seconds)["seconds"]
                 for o in observations]
        # Least squares for the constant at this accel.
        offset = sum(o["observed"] - p for o, p in zip(observations, preds)) / len(preds)
        cost = sum(((p + offset - o["observed"]) / o["observed"]) ** 2
                   for o, p in zip(observations, preds))
        if cost < best[0]:
            best = (cost, accel, offset)
    return best[1], best[2]


_PARK_MARKER = "( ---- park"


def _load(path: Path, to_end_of_job_change: bool = False) -> list:
    """The file's lines, optionally cut to the window the operator timed.

    The park block is found by its comment rather than by counting from the end: it is
    the generator that writes that marker, and it is the generator's output being
    timed. A file without one (a library part, a hand-written program) is used whole —
    there is no end-of-job change in it to stop before.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not to_end_of_job_change:
        return lines
    for i, ln in enumerate(lines):
        if ln.strip().startswith(_PARK_MARKER):
            return lines[:i]
    return lines


def _collect(args) -> list:
    """Build the observation list from positionals and/or a CSV."""
    raw = []
    for item in args.observations_inline:
        if "=" not in item:
            raise SystemExit(
                f"'{item}' is not FILE=DURATION. Example: "
                f'"nest.nc=11:37"'
            )
        path, _, duration = item.rpartition("=")
        raw.append((path, duration))
    if args.observations:
        with open(args.observations, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                raw.append((row["path"], row["observed"]))

    out = []
    for path, duration in raw:
        p = Path(path).expanduser()
        if not p.is_file():
            raise SystemExit(f"not a file: {p}")
        out.append({
            "path": p,
            "lines": _load(p, args.to_end_of_job_change),
            "observed": parse_duration(duration),
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Fit the runtime estimator's effective acceleration to timed jobs.",
    )
    ap.add_argument("observations_inline", nargs="*", metavar="FILE=DURATION",
                    help="a timed job, e.g. 'nest.nc=11:37'")
    ap.add_argument("--observations", metavar="CSV",
                    help="CSV with columns path,observed")
    ap.add_argument("--check", nargs="*", metavar="FILE", default=[],
                    help="predict these files at the current config; no fitting")
    ap.add_argument("--jd", type=float, default=None, metavar="MM",
                    help="junction deviation to hold fixed (default: config value)")
    ap.add_argument("--jd-sweep", action="store_true",
                    help="re-fit at jd = 0.01 / 0.05 / 0.10 and print all three")
    ap.add_argument("--rapid-ipm", type=float, default=None,
                    help="rapid rate override (default: config value)")
    ap.add_argument("--no-auto-tool", action="store_true",
                    help="the timed jobs ran with auto tool touch-off OFF")
    ap.add_argument("--to-end-of-job-change", action="store_true",
                    help="the observations stopped when the cut finished, before the "
                         "end-of-job tool change and the park")
    ap.add_argument("--tolerance", type=float, default=10.0, metavar="PCT",
                    help="exit 1 if any residual exceeds this (default 10)")
    args = ap.parse_args(argv)

    advanced = load_config().get("advanced", {})
    base = MotionLimits.from_config(advanced)
    jd = args.jd if args.jd is not None else base.junction_deviation_mm
    rapid = (args.rapid_ipm * MM_PER_INCH) if args.rapid_ipm else base.rapid_mm_per_min
    auto_tool = (not args.no_auto_tool) and bool(
        advanced.get("auto_tool_touch_off", True)
    )
    change_seconds = tool_change_seconds_for(auto_tool)

    if args.check:
        print(f"Predicting at the current config: accel {base.accel_mm_s2:g} mm/s^2, "
              f"jd {jd:g} mm, rapid {rapid / MM_PER_INCH:g} ipm, "
              f"tool change {change_seconds:g} s.\n")
        print(f"  {'file':30} {'predicted':>10} {'cut':>9} {'rapid':>9} "
              f"{'changes':>9} {'spindle':>9}")
        for path in args.check:
            p = Path(path).expanduser()
            r = _predict(_load(p, args.to_end_of_job_change),
                         base.accel_mm_s2, jd, rapid, change_seconds)
            print(f"  {p.name[:30]:30} {format_duration(r['seconds']):>10} "
                  f"{format_duration(r['cutting']):>9} {format_duration(r['rapid']):>9} "
                  f"{format_duration(r['tool_changes']):>9} "
                  f"{format_duration(r['spindle']):>9}")
        return 0

    observations = _collect(args)
    if not observations:
        ap.error("give at least one FILE=DURATION, or --observations CSV, or --check")

    print(f"Fitting acceleration. Junction deviation {jd:g} mm, "
          f"rapid {rapid / MM_PER_INCH:g} ipm, tool change {change_seconds:g} s "
          f"(auto tool touch-off {'ON' if auto_tool else 'OFF'}).")
    if args.to_end_of_job_change:
        print("Window: cycle start to the end-of-job tool change; the park is excluded.")
    if len(observations) == 1:
        print("ONE observation and ONE free parameter: the fit below is exact by "
              "construction and\nproves nothing on its own. Time another job.")
    print()

    accel = fit_accel(observations, jd, rapid, change_seconds)
    print(f"  accel = {accel:.0f} mm/s^2")
    print()
    header = (f"  {'file':28} {'observed':>10} {'predicted':>10} {'resid':>8} "
              f"{'cut':>9} {'rapid':>9} {'changes':>9} {'spindle':>9}")
    print(header)
    worst = (0.0, None)
    for obs in observations:
        r = _predict(obs["lines"], accel, jd, rapid, change_seconds)
        resid = (r["seconds"] - obs["observed"]) / obs["observed"] * 100
        if abs(resid) > abs(worst[0]):
            worst = (resid, obs["path"].name)
        print(f"  {obs['path'].name[:28]:28} "
              f"{format_duration(obs['observed']):>10} "
              f"{format_duration(r['seconds']):>10} "
              f"{resid:+7.1f}% "
              f"{format_duration(r['cutting']):>9} "
              f"{format_duration(r['rapid']):>9} "
              f"{format_duration(r['tool_changes']):>9} "
              f"{format_duration(r['spindle']):>9}")

    rms = math.sqrt(
        sum(((_predict(o["lines"], accel, jd, rapid, change_seconds)["seconds"]
              - o["observed"]) / o["observed"]) ** 2 for o in observations)
        / len(observations)
    ) * 100
    print()
    print(f"  RMS relative residual {rms:.1f}%"
          + (f"   worst: {worst[1]} {worst[0]:+.1f}%" if worst[1] else ""))

    alt_accel, overhead = fit_accel_and_overhead(observations, jd, rapid, change_seconds)
    if alt_accel is not None:
        print(f"  Diagnostic — fitted with a per-job constant instead: "
              f"{overhead:+.0f} s/job, accel would move to {alt_accel:.0f}.")
        print("    A systematic offset on every job is program load and the "
              "start delay, not acceleration.")

    if args.jd_sweep:
        print()
        print("  Acceleration and junction deviation trade off; this is the coupling:")
        for sweep_jd in (0.01, DEFAULT_JUNCTION_DEVIATION_MM, 0.10):
            a = fit_accel(observations, sweep_jd, rapid, change_seconds)
            c = _residual_cost(observations, a, sweep_jd, rapid, change_seconds)
            print(f"    jd {sweep_jd:5.2f} mm  ->  accel {a:8.0f} mm/s^2   "
                  f"cost {c:.5f}")

    print()
    print(f'  Set  "accel_mm_s2": {accel:.0f}  under `advanced` in config.json to adopt this.')

    over = [o["path"].name for o in observations
            if abs((_predict(o["lines"], accel, jd, rapid, change_seconds)["seconds"]
                    - o["observed"]) / o["observed"]) * 100 > args.tolerance]
    if over:
        print(f"  {len(over)} file(s) over the {args.tolerance:g}% tolerance: "
              f"{', '.join(over)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
