"""
Runtime estimator for VCarve-style G-code.

Walks a line stream tracking modal units (G20/G21), modal feedrate (F-words),
and current XYZ. Sums cutting time (distance / feedrate) and rapid time
(distance / rapid rate), plus a fixed seconds-per-tool-change cost.

Per project convention:
  - Default rapid rate: 1800 in/min (Smartshop 2).
  - Default tool change: 30 s per T# M06.
  - Z-only G1 plunges are ignored (treated as zero time).
  - F-words and coordinates are interpreted under the currently-modal units;
    G20 lines switch to inches, G21 lines switch to mm.
  - VCarve default is inches (G20), so estimator defaults to inches if no
    units directive is seen.
"""
from math import atan2, hypot, pi
from typing import Iterable, List

# Pre-compiled patterns. Re-use the parser's for X/Y/Z extraction so we stay
# in sync with what the rest of the app treats as a coordinate.
import re
from gcode_parser import COORD_PATTERN, TOOL_CHANGE_PATTERN

_F_PATTERN = re.compile(r"\bF\s*([0-9.]+)")
_IJ_PATTERN = re.compile(r"([IJ])\s*([+-]?\d*\.?\d+)")
_G0_PATTERN = re.compile(r"\bG0?0\b")
_G1_PATTERN = re.compile(r"\bG0?1\b")
_G2_PATTERN = re.compile(r"\bG0?2\b")
_G3_PATTERN = re.compile(r"\bG0?3\b")
_G20_PATTERN = re.compile(r"\bG20\b")
_G21_PATTERN = re.compile(r"\bG21\b")

MM_PER_INCH = 25.4
DEFAULT_RAPID_MM_PER_MIN = 1800.0 * MM_PER_INCH   # 1800 in/min
DEFAULT_TOOL_CHANGE_SECONDS = 30.0


def estimate_lines_runtime(
    lines: Iterable[str],
    rapid_mm_per_min: float = DEFAULT_RAPID_MM_PER_MIN,
    tool_change_seconds: float = DEFAULT_TOOL_CHANGE_SECONDS,
) -> dict:
    """
    Estimate runtime for a raw G-code line stream.

    Returns a dict: {seconds, cutting, rapid, tool_changes}, each in seconds.
    """
    cutting_s = 0.0
    rapid_s = 0.0
    change_s = 0.0

    # Default to inches — VCarve standard. Flip to mm if we see G21.
    unit_scale = MM_PER_INCH
    cur_x = cur_y = cur_z = 0.0
    cur_f = 0.0  # mm/min when used; converted on the fly via unit_scale

    for raw in lines:
        if not raw:
            continue
        s = raw.strip()
        if not s or s.startswith("("):
            continue

        if _G20_PATTERN.search(s):
            unit_scale = MM_PER_INCH
        elif _G21_PATTERN.search(s):
            unit_scale = 1.0

        if TOOL_CHANGE_PATTERN.search(s):
            change_s += tool_change_seconds
            # T# M06 lines don't move; continue so we don't try to read coords.
            continue

        f_match = _F_PATTERN.search(s)
        if f_match:
            cur_f = float(f_match.group(1))

        is_g0 = bool(_G0_PATTERN.search(s))
        is_g1 = bool(_G1_PATTERN.search(s))
        is_g2 = bool(_G2_PATTERN.search(s))
        is_g3 = bool(_G3_PATTERN.search(s))

        if not (is_g0 or is_g1 or is_g2 or is_g3):
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

        if is_g0:
            d = _dist3(cur_x, cur_y, cur_z, new_x, new_y, new_z)
            if rapid_mm_per_min > 0:
                rapid_s += d / rapid_mm_per_min * 60.0
        elif is_g1:
            xy_changed = new_x != cur_x or new_y != cur_y
            if xy_changed:
                d = _dist2(cur_x, cur_y, new_x, new_y)
                f_mm = cur_f * unit_scale
                if f_mm > 0:
                    cutting_s += d / f_mm * 60.0
            # Z-only G1 (plunge) → skip per project convention.
        elif is_g2 or is_g3:
            i_off = j_off = 0.0
            for axis, val in _IJ_PATTERN.findall(s):
                v = float(val) * unit_scale
                if axis.upper() == "I":
                    i_off = v
                else:
                    j_off = v
            arc_len = _arc_length(cur_x, cur_y, new_x, new_y, i_off, j_off, clockwise=is_g2)
            f_mm = cur_f * unit_scale
            if f_mm > 0 and arc_len > 0:
                cutting_s += arc_len / f_mm * 60.0

        cur_x, cur_y, cur_z = new_x, new_y, new_z

    return {
        "seconds": cutting_s + rapid_s + change_s,
        "cutting": cutting_s,
        "rapid": rapid_s,
        "tool_changes": change_s,
    }


def estimate_passes_runtime(
    passes: List,
    rapid_mm_per_min: float = DEFAULT_RAPID_MM_PER_MIN,
    tool_change_seconds: float = DEFAULT_TOOL_CHANGE_SECONDS,
) -> dict:
    """Convenience wrapper that flattens a list of GcodePass objects."""
    all_lines: List[str] = []
    for p in passes:
        all_lines.extend(p.lines)
    return estimate_lines_runtime(all_lines, rapid_mm_per_min, tool_change_seconds)


def _dist2(x0: float, y0: float, x1: float, y1: float) -> float:
    return hypot(x1 - x0, y1 - y0)


def _dist3(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> float:
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2) ** 0.5


def _arc_length(
    x0: float, y0: float, x1: float, y1: float,
    i: float, j: float, clockwise: bool,
) -> float:
    cx, cy = x0 + i, y0 + j
    r = hypot(i, j)
    if r == 0:
        return 0.0
    a0 = atan2(y0 - cy, x0 - cx)
    a1 = atan2(y1 - cy, x1 - cx)
    if clockwise:
        sweep = a0 - a1
    else:
        sweep = a1 - a0
    # A near-zero sweep for matching endpoints is a full circle, not a no-op.
    while sweep <= 1e-9:
        sweep += 2 * pi
    return r * sweep


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
