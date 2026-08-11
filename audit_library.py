#!/usr/bin/env python3
"""
CNC library audit — standalone CLI.

Scans every VCarve-exported .nc/.mmg file in the library, extracts everything derivable
from the G-code WITHOUT opening VCarve, runs best-practice checks, and writes three CSVs:

  library_audit_<timestamp>.csv            one row per file  (the main auditing view)
  library_audit_tools_<timestamp>.csv      one row per (file x tool)  (feed/speed detail)
  library_audit_toolpaths_<timestamp>.csv  one row per (file x toolpath)  (per-path feeds)

The per-toolpath CSV is the authoritative record of which feed rate belongs to which
toolpath — this VCarve post emits one tool-change block per toolpath, so a part that
reuses a tool across several toolpaths at different feeds shows each one separately.
The file-level row carries a `toolpath_feeds` summary and a `feed_varies` flag so the
variation is visible without opening the detail CSV.

Almost all parsing is reused from the app:
  - gcode_parser.parse_vcarve_text  → dimensions, thickness, tools, Z depths, safe Z, z_validation
  - audit_metrics.extract_pass_metrics → per-toolpath feed / plunge / spindle (the one new piece)
  - audit_metrics.extract_tool_metrics → the same rolled up per tool number
  - tool_library.ToolLibrary → diameter resolution + unknown-tool detection
  - runtime_estimator.format_duration → human-readable runtime

Usage:
    python audit_library.py [--library PATH] [--out DIR]

Best-practice thresholds default to the constants below and can be overridden by adding an
optional "audit" block to config.json, e.g.:

    "audit": {
        "spindle_min_rpm": 10000, "spindle_max_rpm": 24000,
        "cut_feed_min_ipm": 50, "cut_feed_max_ipm": 400,
        "plunge_feed_max_ipm": 150
    }
"""
import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

import config as config_module
from gcode_parser import parse_vcarve_text
from audit_metrics import extract_pass_metrics, extract_tool_metrics
from tool_library import ToolLibrary
from runtime_estimator import format_duration

MM_PER_INCH = 25.4
VALID_EXT = {".nc", ".mmg"}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB, matching app.py

# Best-practice thresholds (overridable via config["audit"]).
DEFAULT_THRESHOLDS = {
    "spindle_min_rpm": 10000.0,
    "spindle_max_rpm": 24000.0,
    "cut_feed_min_ipm": 50.0,
    "cut_feed_max_ipm": 400.0,
    "plunge_feed_max_ipm": 150.0,
}

# Severity ordering for rolling up the overall file status.
_SEVERITY_RANK = {"ok": 0, "warning": 1, "blocked": 2}


def mm_to_in(value):
    return None if value is None else value / MM_PER_INCH


def fmt(value, places=3):
    """Round a number for CSV; blank for None."""
    return "" if value is None else round(value, places)


def _feed_phrase(min_ipm, max_ipm):
    """'150' for a single feed, '100–150' for a range, or None when no feed."""
    if min_ipm is None:
        return None
    if max_ipm is None or abs(max_ipm - min_ipm) < 0.1:
        return f"{min_ipm:g}"
    return f"{min_ipm:g}–{max_ipm:g}"


def summarize_toolpath_feeds(pass_metrics):
    """Build the file-level 'which feed for which toolpath' summary.

    Returns (summary_text, feed_varies) where summary_text reads e.g.
    "1.T2 cut 150 plunge 50; 2.T2 cut 100 plunge 50" (ipm) and feed_varies is
    "yes" when the part uses more than one distinct cutting feed across its
    toolpaths (rounded to the nearest ipm), else "".
    """
    parts = []
    cut_feeds_seen = set()
    for idx, pm in enumerate(pass_metrics):
        cut_min = mm_to_in(pm.get("cut_feed_min"))
        cut_max = mm_to_in(pm.get("cut_feed_max"))
        plunge_min = mm_to_in(pm.get("plunge_feed_min"))
        plunge_max = mm_to_in(pm.get("plunge_feed_max"))

        seg = f"{idx + 1}.{pm['tool']}"
        cut_phrase = _feed_phrase(cut_min and round(cut_min, 1), cut_max and round(cut_max, 1))
        if cut_phrase is not None:
            seg += f" cut {cut_phrase}"
        plunge_phrase = _feed_phrase(plunge_min and round(plunge_min, 1), plunge_max and round(plunge_max, 1))
        if plunge_phrase is not None:
            seg += f" plunge {plunge_phrase}"
        if cut_phrase is None and plunge_phrase is None:
            seg += " (no feed)"
        parts.append(seg)

        for v in (cut_min, cut_max):
            if v is not None:
                cut_feeds_seen.add(round(v))

    feed_varies = "yes" if len(cut_feeds_seen) > 1 else ""
    return "; ".join(parts), feed_varies


def detect_units(raw_lines):
    """Return 'inch', 'mm', or 'mm?' based on the last unit directive seen.

    VCarve output for this shop should be metric (G71). Anything inch (G20/G70) is flagged.
    'mm?' means no explicit directive — the app defaults to mm.
    """
    unit = None
    for line in raw_lines:
        s = line.strip()
        if s.startswith("("):
            continue
        if "G20" in s or "G70" in s:
            unit = "inch"
        elif "G21" in s or "G71" in s:
            unit = "mm"
    return unit if unit else "mm?"


def worse(current, candidate):
    """Return the higher-severity of two status strings."""
    return candidate if _SEVERITY_RANK[candidate] > _SEVERITY_RANK[current] else current


def rollup(metrics, key):
    """Min or max across all tools for a metric key. key ends in _min → min, _max → max."""
    values = [m[key] for m in metrics.values() if m.get(key) is not None]
    if not values:
        return None
    return min(values) if key.endswith("_min") else max(values)


def audit_part(part, rel_path, tool_lib, thresholds):
    """Build the file-level row, per-tool rows, and per-toolpath rows for one part."""
    pass_metrics = extract_pass_metrics(part)
    metrics = extract_tool_metrics(part)
    unknown = tool_lib.find_unknown_tools(part)
    unknown_nums = [u["tool_number"] for u in unknown]
    tools_used = [gp.tool_number for gp in part.passes]
    tools_unique = sorted(set(tools_used), key=lambda t: tools_used.index(t))
    units = detect_units(part.raw_lines)

    # --- Roll feeds/spindle up across tools (mm/min → in/min) ---
    cut_feed_min = mm_to_in(rollup(metrics, "cut_feed_min"))
    cut_feed_max = mm_to_in(rollup(metrics, "cut_feed_max"))
    plunge_feed_max = mm_to_in(rollup(metrics, "plunge_feed_max"))
    spindle_min = rollup(metrics, "spindle_min")
    spindle_max = rollup(metrics, "spindle_max")
    pass_count = sum(m["pass_count"] for m in metrics.values())
    cut_move_count = sum(m["cut_move_count"] for m in metrics.values())

    # --- Best-practice flags: list of (severity, message) ---
    flags = []

    # Fold in the existing Z validation (missing header / Z ref / overtravel / shallow / safe-Z).
    if part.z_validation.status != "ok":
        for msg in (part.z_validation.messages or [part.z_validation.status]):
            flags.append((part.z_validation.status, msg))

    if unknown_nums:
        flags.append(("blocked", f"Unknown tool diameter: {', '.join(unknown_nums)}"))

    # Header diameter disagrees with the shop tool library — a silent mislabel.
    for tn in tools_unique:
        header_dia = part.tools.get(tn, {}).get("diameter_inches")
        lib_dia = tool_lib.resolve_diameter(tn)
        if header_dia is not None and lib_dia is not None and abs(header_dia - lib_dia) > 0.001:
            flags.append(("warning", f"{tn} header diameter {header_dia}\" ≠ library {lib_dia}\""))

    if not part.tools and not metrics:
        flags.append(("blocked", "No tools found — file may be empty or malformed"))
    if cut_move_count == 0:
        flags.append(("warning", "No cutting moves detected"))

    if units == "inch":
        flags.append(("warning", "File uses inch units (G20/G70); VCarve output should be metric (G71)"))

    # Spindle range / presence.
    if spindle_max is None:
        flags.append(("warning", "No spindle speed (S) found"))
    else:
        if spindle_min is not None and spindle_min < thresholds["spindle_min_rpm"]:
            flags.append(("warning", f"Spindle {spindle_min:.0f} RPM below {thresholds['spindle_min_rpm']:.0f}"))
        if spindle_max > thresholds["spindle_max_rpm"]:
            flags.append(("warning", f"Spindle {spindle_max:.0f} RPM above {thresholds['spindle_max_rpm']:.0f}"))

    # Cut feed range.
    if cut_feed_min is not None and cut_feed_min < thresholds["cut_feed_min_ipm"]:
        flags.append(("warning", f"Cut feed {cut_feed_min:.0f} ipm below {thresholds['cut_feed_min_ipm']:.0f}"))
    if cut_feed_max is not None and cut_feed_max > thresholds["cut_feed_max_ipm"]:
        flags.append(("warning", f"Cut feed {cut_feed_max:.0f} ipm above {thresholds['cut_feed_max_ipm']:.0f}"))

    # Plunge feed.
    if plunge_feed_max is not None and plunge_feed_max > thresholds["plunge_feed_max_ipm"]:
        flags.append(("warning", f"Plunge feed {plunge_feed_max:.0f} ipm above {thresholds['plunge_feed_max_ipm']:.0f}"))

    # Per-toolpath feed breakdown. When a part uses more than one cutting feed
    # across its toolpaths, note it (informational — different feeds for roughing
    # vs finishing are legitimate, so this does NOT escalate the file status).
    toolpath_feeds, feed_varies = summarize_toolpath_feeds(pass_metrics)
    if feed_varies:
        flags.append(("ok", f"Feed rates vary by toolpath: {toolpath_feeds} (ipm)"))

    # Bed fit (vcarve_x = along rail = bed Y; vcarve_y = across bed = bed X).
    # Raw part dimensions are in the CSV for manual judgment; only true bed overflow is flagged.
    bed_x = thresholds["bed_x_mm"]
    bed_y = thresholds["bed_y_mm"]
    if part.vcarve_x_span and part.vcarve_x_span > bed_y:
        flags.append(("blocked", f"Part length {part.vcarve_x_span / MM_PER_INCH:.1f}\" exceeds bed length {bed_y / MM_PER_INCH:.1f}\""))
    if part.vcarve_y_span and part.vcarve_y_span > bed_x:
        flags.append(("blocked", f"Part width {part.vcarve_y_span / MM_PER_INCH:.1f}\" exceeds bed width {bed_x / MM_PER_INCH:.1f}\""))

    status = "ok"
    for sev, _ in flags:
        status = worse(status, sev)

    flag_text = "; ".join(msg for _, msg in flags)
    z_msg_text = "; ".join(part.z_validation.messages or [])

    max_cut_depth = None
    if part.material_thickness is not None and part.min_z is not None:
        max_cut_depth = part.material_thickness - part.min_z  # depth from material top, mm

    folder = os.path.dirname(rel_path)

    file_row = {
        "path": rel_path,
        "filename": part.filename or os.path.basename(rel_path),
        "folder": folder,
        "status": status,
        "flags": flag_text,
        "vcarve_x_in": fmt(mm_to_in(part.vcarve_x_span), 3),
        "vcarve_y_in": fmt(mm_to_in(part.vcarve_y_span), 3),
        "vcarve_x_mm": fmt(part.vcarve_x_span, 2),
        "vcarve_y_mm": fmt(part.vcarve_y_span, 2),
        "cut_footprint_x_in": fmt(mm_to_in(part.max_vx - part.min_vx), 3),
        "cut_footprint_y_in": fmt(mm_to_in(part.max_vy - part.min_vy), 3),
        "material_thickness_in": fmt(mm_to_in(part.material_thickness), 4),
        "units": units,
        "min_z_in": fmt(mm_to_in(part.min_z), 4),
        "max_z_in": fmt(mm_to_in(part.max_z), 4),
        "safe_z_in": fmt(mm_to_in(part.safe_z), 4),
        "max_cut_depth_in": fmt(mm_to_in(max_cut_depth), 4),
        "tool_count": len(part.tools),
        "tools": ",".join(tools_unique),
        "unknown_tools": ",".join(unknown_nums),
        "cut_feed_min_ipm": fmt(cut_feed_min, 1),
        "cut_feed_max_ipm": fmt(cut_feed_max, 1),
        "plunge_feed_max_ipm": fmt(plunge_feed_max, 1),
        "spindle_min_rpm": fmt(spindle_min, 0),
        "spindle_max_rpm": fmt(spindle_max, 0),
        "toolpath_count": len(pass_metrics),
        "feed_varies": feed_varies,
        "toolpath_feeds": toolpath_feeds,
        "pass_count": pass_count,
        "cut_move_count": cut_move_count,
        "runtime": format_duration(part.runtime_seconds),
        "z_status": part.z_validation.status,
        "z_messages": z_msg_text,
        "error": "",
    }

    # --- Per-tool detail rows ---
    tool_rows = []
    for tn in tools_unique:
        m = metrics.get(tn, {})
        dia = tool_lib.resolve_for_part(part, tn)
        if part.tools.get(tn, {}).get("diameter_inches") is not None:
            dia_source = "header"
        elif dia is not None:
            dia_source = "library"
        else:
            dia_source = "unknown"
        tool_rows.append({
            "path": rel_path,
            "tool": tn,
            "description": part.tools.get(tn, {}).get("description", ""),
            "diameter_in": fmt(dia, 4),
            "diameter_source": dia_source,
            "header_diameter_in": fmt(part.tools.get(tn, {}).get("diameter_inches"), 4),
            "library_diameter_in": fmt(tool_lib.resolve_diameter(tn), 4),
            "pass_count": m.get("pass_count", 0),
            "cut_feed_min_ipm": fmt(mm_to_in(m.get("cut_feed_min")), 1),
            "cut_feed_max_ipm": fmt(mm_to_in(m.get("cut_feed_max")), 1),
            "plunge_feed_max_ipm": fmt(mm_to_in(m.get("plunge_feed_max")), 1),
            "spindle_min_rpm": fmt(m.get("spindle_min"), 0),
            "spindle_max_rpm": fmt(m.get("spindle_max"), 0),
            "min_z_in": fmt(mm_to_in(m.get("min_z")), 4),
            "max_z_in": fmt(mm_to_in(m.get("max_z")), 4),
        })

    # --- Per-toolpath detail rows (one per T# M06 block, in file order) ---
    toolpath_rows = []
    for idx, pm in enumerate(pass_metrics):
        tn = pm["tool"]
        toolpath_rows.append({
            "path": rel_path,
            "toolpath": idx + 1,
            "tool": tn,
            "description": part.tools.get(tn, {}).get("description", ""),
            "diameter_in": fmt(tool_lib.resolve_for_part(part, tn), 4),
            "cut_feed_min_ipm": fmt(mm_to_in(pm.get("cut_feed_min")), 1),
            "cut_feed_max_ipm": fmt(mm_to_in(pm.get("cut_feed_max")), 1),
            "plunge_feed_min_ipm": fmt(mm_to_in(pm.get("plunge_feed_min")), 1),
            "plunge_feed_max_ipm": fmt(mm_to_in(pm.get("plunge_feed_max")), 1),
            "spindle_min_rpm": fmt(pm.get("spindle_min"), 0),
            "spindle_max_rpm": fmt(pm.get("spindle_max"), 0),
            "min_z_in": fmt(mm_to_in(pm.get("min_z")), 4),
            "max_z_in": fmt(mm_to_in(pm.get("max_z")), 4),
            "cut_move_count": pm.get("cut_move_count", 0),
        })

    return file_row, tool_rows, toolpath_rows


def error_row(rel_path, message):
    return {
        "path": rel_path,
        "filename": os.path.basename(rel_path),
        "folder": os.path.dirname(rel_path),
        "status": "blocked",
        "flags": f"Parse error: {message}",
        "error": message,
    }


def scan_library(root, tool_lib, thresholds):
    """Walk the library recursively; return (file_rows, tool_rows, toolpath_rows)."""
    file_rows, tool_rows, toolpath_rows = [], [], []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() not in VALID_EXT:
                continue
            abs_path = os.path.join(dirpath, name)
            rel_path = os.path.relpath(abs_path, root)
            try:
                if os.path.getsize(abs_path) > MAX_FILE_BYTES:
                    file_rows.append(error_row(rel_path, "File too large (>10 MB)"))
                    continue
                text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
                part = parse_vcarve_text(text, filename=name)
                f_row, t_rows, tp_rows = audit_part(part, rel_path, tool_lib, thresholds)
                file_rows.append(f_row)
                tool_rows.extend(t_rows)
                toolpath_rows.extend(tp_rows)
            except Exception as exc:  # one bad file must not abort the run
                file_rows.append(error_row(rel_path, str(exc)))

    file_rows.sort(key=lambda r: r["path"])
    return file_rows, tool_rows, toolpath_rows


FILE_COLUMNS = [
    "path", "filename", "folder", "status", "flags",
    "vcarve_x_in", "vcarve_y_in", "vcarve_x_mm", "vcarve_y_mm",
    "cut_footprint_x_in", "cut_footprint_y_in", "material_thickness_in", "units",
    "min_z_in", "max_z_in", "safe_z_in", "max_cut_depth_in",
    "tool_count", "tools", "unknown_tools",
    "cut_feed_min_ipm", "cut_feed_max_ipm", "plunge_feed_max_ipm",
    "spindle_min_rpm", "spindle_max_rpm",
    "toolpath_count", "feed_varies", "toolpath_feeds",
    "pass_count", "cut_move_count", "runtime",
    "z_status", "z_messages", "error",
]

TOOL_COLUMNS = [
    "path", "tool", "description", "diameter_in", "diameter_source",
    "header_diameter_in", "library_diameter_in", "pass_count",
    "cut_feed_min_ipm", "cut_feed_max_ipm", "plunge_feed_max_ipm",
    "spindle_min_rpm", "spindle_max_rpm", "min_z_in", "max_z_in",
]

TOOLPATH_COLUMNS = [
    "path", "toolpath", "tool", "description", "diameter_in",
    "cut_feed_min_ipm", "cut_feed_max_ipm",
    "plunge_feed_min_ipm", "plunge_feed_max_ipm",
    "spindle_min_rpm", "spindle_max_rpm",
    "min_z_in", "max_z_in", "cut_move_count",
]


def write_csv(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_thresholds(cfg):
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(cfg.get("audit", {}) or {})
    advanced = cfg.get("advanced", {}) or {}
    thresholds["bed_x_mm"] = advanced.get("bed_x_mm", 1524.0)
    thresholds["bed_y_mm"] = advanced.get("bed_y_mm", 3048.0)
    return thresholds


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit the CNC library for best-practice issues.")
    parser.add_argument("--library", help="Override the library path from config.json")
    parser.add_argument("--out", help="Override the output directory from config.json")
    args = parser.parse_args(argv)

    cfg = config_module.load_config()
    tool_lib = ToolLibrary(cfg.get("tools", {}))
    thresholds = build_thresholds(cfg)

    library_path = args.library or cfg.get("library_path") or config_module.get_default_library_path()
    out_dir = args.out or cfg.get("output_path") or config_module.get_default_output_path()
    root = str(config_module.resolve_path(library_path))
    out_path = config_module.resolve_path(out_dir)

    if not os.path.isdir(root):
        print(f"Library path does not exist: {root}", file=sys.stderr)
        return 1

    print(f"Scanning {root} ...")
    file_rows, tool_rows, toolpath_rows = scan_library(root, tool_lib, thresholds)

    out_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    file_csv = out_path / f"library_audit_{stamp}.csv"
    tool_csv = out_path / f"library_audit_tools_{stamp}.csv"
    toolpath_csv = out_path / f"library_audit_toolpaths_{stamp}.csv"
    write_csv(file_csv, FILE_COLUMNS, file_rows)
    write_csv(tool_csv, TOOL_COLUMNS, tool_rows)
    write_csv(toolpath_csv, TOOLPATH_COLUMNS, toolpath_rows)

    counts = {"ok": 0, "warning": 0, "blocked": 0}
    for row in file_rows:
        counts[row.get("status", "ok")] = counts.get(row.get("status", "ok"), 0) + 1
    total = len(file_rows)
    varies = sum(1 for r in file_rows if r.get("feed_varies"))
    print(f"{total} files: {counts['ok']} ok, {counts['warning']} warning, {counts['blocked']} blocked")
    print(f"  {varies} file(s) use more than one cutting feed across toolpaths")
    print(f"  {file_csv}")
    print(f"  {tool_csv}")
    print(f"  {toolpath_csv}")

    # Echo the files that need attention so issues are visible without opening the CSV.
    flagged = [r for r in file_rows if r.get("status") != "ok"]
    if flagged:
        print("\nNeeds attention:")
        for r in flagged:
            print(f"  [{r['status'].upper()}] {r['path']} — {r.get('flags', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
