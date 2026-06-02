"""
Per-toolpath and per-tool feed rate, plunge rate, and spindle speed extraction for
the library audit.

This is the one piece of data the audit needs that `gcode_parser.GcodePart` does not
already store. It walks each tool pass the same way `runtime_estimator.estimate_lines_runtime`
does — tracking modal units (G20/G70 inch, G21/G71 mm), modal feedrate (F-words), modal
spindle speed (S-words), and current XYZ.

This VCarve post-processor emits one `T# M06` block per toolpath, so each parsed
`GcodePass` IS one toolpath. `extract_pass_metrics` returns one metric dict per
toolpath (preserving file order) — this is what lets the audit report which feed
rate belongs to which toolpath, and flag when they differ across the part.
`extract_tool_metrics` rolls those per-toolpath metrics up by tool number for the
file-level summary (a tool reused by several toolpaths accumulates into one range).

Conventions match the rest of the app:
  - Default to mm; flip to inches on a G20/G70 line.
  - Cutting feed = F active on a G01 lateral move (XY changes) or any G02/G03 arc.
  - Plunge feed = F active on a G01 Z-only move (no XY change).
  - All feeds are returned in mm/min; Z values in mm. The audit converts to in/min for display.
  - A single toolpath may itself use more than one cutting feed (e.g. an arc vs a
    straight lerp, or a ramp) — `cut_feed_min` != `cut_feed_max` captures that.
"""
import re
from typing import Dict, Optional

from gcode_parser import COORD_PATTERN, TOOL_CHANGE_PATTERN
from runtime_estimator import MM_PER_INCH

_F_PATTERN = re.compile(r"\bF\s*([0-9.]+)")
_S_PATTERN = re.compile(r"\bS\s*([0-9.]+)")
_G0_PATTERN = re.compile(r"\bG0?0\b")
_G1_PATTERN = re.compile(r"\bG0?1\b")
_G2_PATTERN = re.compile(r"\bG0?2\b")
_G3_PATTERN = re.compile(r"\bG0?3\b")
_G20_PATTERN = re.compile(r"\bG20\b")
_G21_PATTERN = re.compile(r"\bG21\b")
_G70_PATTERN = re.compile(r"\bG70\b")
_G71_PATTERN = re.compile(r"\bG71\b")


def _new_metric() -> dict:
    return {
        "cut_feed_min": None,
        "cut_feed_max": None,
        "plunge_feed_min": None,
        "plunge_feed_max": None,
        "spindle_min": None,
        "spindle_max": None,
        "pass_count": 0,
        "cut_move_count": 0,
        "min_z": None,
        "max_z": None,
    }


def _accumulate_min_max(metric: dict, key_min: str, key_max: str, value: float) -> None:
    if metric[key_min] is None or value < metric[key_min]:
        metric[key_min] = value
    if metric[key_max] is None or value > metric[key_max]:
        metric[key_max] = value


def _initial_unit_scale(raw_lines) -> float:
    """File-level units. VCarve emits the G20/G70/G21/G71 directive in the header,
    BEFORE the first T# M06 — so it lives outside any pass. Detect it from the full
    line list and seed each pass's starting scale with it. Defaults to mm."""
    scale = 1.0
    for raw in raw_lines:
        s = raw.strip()
        if not s or s.startswith("("):
            continue
        if _G20_PATTERN.search(s) or _G70_PATTERN.search(s):
            scale = MM_PER_INCH
        elif _G21_PATTERN.search(s) or _G71_PATTERN.search(s):
            scale = 1.0
    return scale


def _walk_pass(gp, base_unit_scale: float) -> dict:
    """Walk a single tool pass (one toolpath) and return its metric dict.

    Feeds are mm/min, Z is mm; values stay None when a category never appears.
    `base_unit_scale` seeds the modal units from the file header; a mid-pass
    G20/G70/G21/G71 still overrides it.
    """
    metric = _new_metric()
    metric["pass_count"] = 1

    unit_scale = base_unit_scale
    cur_x = cur_y = cur_z = 0.0
    cur_f: Optional[float] = None

    for raw in gp.lines:
        s = raw.strip()
        if not s or s.startswith("("):
            continue

        if _G20_PATTERN.search(s) or _G70_PATTERN.search(s):
            unit_scale = MM_PER_INCH
        elif _G21_PATTERN.search(s) or _G71_PATTERN.search(s):
            unit_scale = 1.0

        # Spindle speed (S-word) — RPM, unit-independent.
        s_match = _S_PATTERN.search(s)
        if s_match:
            rpm = float(s_match.group(1))
            if rpm > 0:
                _accumulate_min_max(metric, "spindle_min", "spindle_max", rpm)

        if TOOL_CHANGE_PATTERN.search(s):
            # T# M06 lines carry no motion or feed; skip coordinate reading.
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

        xy_changed = new_x != cur_x or new_y != cur_y

        # Track cutting-depth Z on feed moves (not rapids), mirroring scan_z_values.
        if (is_g1 or is_g2 or is_g3):
            _accumulate_min_max(metric, "min_z", "max_z", new_z)

        f_mm = cur_f * unit_scale if cur_f is not None else None
        if f_mm is not None and f_mm > 0:
            if is_g1 and xy_changed:
                metric["cut_move_count"] += 1
                _accumulate_min_max(metric, "cut_feed_min", "cut_feed_max", f_mm)
            elif is_g1 and not xy_changed:
                # Z-only G01 → plunge.
                _accumulate_min_max(metric, "plunge_feed_min", "plunge_feed_max", f_mm)
            elif is_g2 or is_g3:
                metric["cut_move_count"] += 1
                _accumulate_min_max(metric, "cut_feed_min", "cut_feed_max", f_mm)

        cur_x, cur_y, cur_z = new_x, new_y, new_z

    return metric


# Metric keys that hold a (min, max) range and so must be merged from both ends
# when rolling per-toolpath metrics up into a per-tool summary.
_RANGE_KEYS = (
    ("cut_feed_min", "cut_feed_max"),
    ("plunge_feed_min", "plunge_feed_max"),
    ("spindle_min", "spindle_max"),
    ("min_z", "max_z"),
)


def extract_pass_metrics(part) -> list:
    """
    Walk each toolpath (one per `T# M06` block) and return its metrics in file order.

    Returns a list of metric dicts (the keys from `_new_metric()`) each extended with:
      - "pass_index": the GcodePass index (0-based, file order)
      - "tool": the tool number, upper-cased (e.g. "T2")

    This is the per-toolpath view: when a part runs the same tool through several
    toolpaths at different feeds, each appears as its own entry here.
    """
    base_unit_scale = _initial_unit_scale(part.raw_lines)
    out = []
    for gp in part.passes:
        metric = _walk_pass(gp, base_unit_scale)
        metric["pass_index"] = gp.pass_index
        metric["tool"] = gp.tool_number.upper()
        out.append(metric)
    return out


def extract_tool_metrics(part) -> Dict[str, dict]:
    """
    Walk a GcodePart's tool passes and return per-tool feed/spindle/Z metrics.

    Returns: {tool_number -> metric dict}, where each metric dict has the keys
    created by `_new_metric()`. Feeds are mm/min, Z is mm. Values stay None when
    a category never appears (e.g. spindle_min is None if the file has no S-words).
    A tool used by multiple toolpaths accumulates across all of them.
    """
    metrics: Dict[str, dict] = {}

    for pm in extract_pass_metrics(part):
        agg = metrics.setdefault(pm["tool"], _new_metric())
        agg["pass_count"] += pm["pass_count"]
        agg["cut_move_count"] += pm["cut_move_count"]
        for key_min, key_max in _RANGE_KEYS:
            # Fold the toolpath's own min and max into the per-tool range.
            if pm[key_min] is not None:
                _accumulate_min_max(agg, key_min, key_max, pm[key_min])
            if pm[key_max] is not None:
                _accumulate_min_max(agg, key_min, key_max, pm[key_max])

    return metrics
