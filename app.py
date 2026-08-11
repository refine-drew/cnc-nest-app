import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict

from flask import Flask, abort, jsonify, render_template, request

from audit_library import (
    FILE_COLUMNS,
    TOOL_COLUMNS,
    TOOLPATH_COLUMNS,
    build_thresholds,
    scan_library,
    write_csv,
)
from collision import PlacedPart, blank_rect, check_placement, slot_label
from config import load_config, save_config
from gcode_generator import generate_master_gcode
from gcode_parser import GcodePart, parse_vcarve_text
from pdf_report import generate_layout_pdf, palette_color as pdf_palette_color
from runtime_estimator import estimate_lines_runtime, format_duration
from tool_library import ToolLibrary

app = Flask(__name__, template_folder="templates", static_folder="static")

config = load_config()

# In-memory session state — single user, local app
_loaded: Dict[str, GcodePart] = {}      # library-relative path → GcodePart
_placements: Dict[str, PlacedPart] = {} # instance_id → PlacedPart
_placement_paths: Dict[str, str] = {}   # instance_id → library-relative path
_instance_counts: Dict[str, int] = {}   # filename stem → counter for unique IDs

VALID_EXT = {".nc", ".mmg"}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB

_PITCH_13 = {0, 13, 26, 39, 52, 65, 78, 91, 104, 117}
_PITCH_195 = {0, 19.5, 39, 58.5, 78, 97.5, 117}


# ── private helpers ───────────────────────────────────────────────────────────

def _library_root() -> str:
    return str(Path(config["library_path"]).expanduser().resolve())


def _resolve_library_path(rel: str) -> str:
    """Resolve a library-relative path and abort 400 on any traversal attempt."""
    root = Path(config["library_path"]).expanduser().resolve()
    full = (root / rel).resolve()
    if root != full and root not in full.parents:
        abort(400, description="Invalid path")
    return str(full)


def _rail_width() -> float:
    return float(config["advanced"]["rail_width_mm"])


def _bed_x() -> float:
    return float(config["advanced"]["bed_x_mm"])


def _bed_y() -> float:
    return float(config["advanced"]["bed_y_mm"])


def _edge_margin_in() -> float:
    return float(config["advanced"].get("slot_edge_margin_in", 1.5))


def _make_instance_id(filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    _instance_counts[stem] = _instance_counts.get(stem, 0) + 1
    return f"{stem}_{_instance_counts[stem]}"


def _parse_file(abs_path: str) -> GcodePart:
    p = Path(abs_path)
    return parse_vcarve_text(p.read_text(encoding="utf-8", errors="replace"), filename=p.name)


def _part_dict(part: GcodePart, rel_path: str = "") -> dict:
    return {
        "filename": part.filename,
        "path": rel_path,
        "vcarve_x_span": part.vcarve_x_span,   # was blank_width
        "vcarve_y_span": part.vcarve_y_span,   # was blank_height
        "material_thickness": part.material_thickness,
        "tools": part.tools,
        "z_status": part.z_validation.status,
        "z_messages": part.z_validation.messages,
        "min_vx": part.min_vx,    # was min_x
        "max_vx": part.max_vx,
        "min_vy": part.min_vy,    # was min_y
        "max_vy": part.max_vy,
        "min_z": part.min_z,
        "max_z": part.max_z,
        "safe_z": part.safe_z,
        "pass_count": len(part.passes),
        "tool_sequence": [p.tool_number for p in part.passes],
        "runtime_seconds": part.runtime_seconds,
    }


def _transform_segments(
    segs: list, rail: str, slot_inches: float,
    rail_width_mm: float, bed_x_mm: float, bed_y_mm: float,
    edge_margin_in: float = 0.0,
) -> list:
    """
    Convert file-coordinate segments to machine coordinates for canvas rendering.
    Mirrors the generator transform (gcode_generator._transform_params): both rails
    are proper rotations (one file axis mirrored each).
    file_Y → machine X (vertical),  file_X → machine Y (horizontal)
    A rail:  machX = rail_w + fileY             machY = slot_mark - fileX
    B rail:  machX = (BED_X-rail_w) - fileY     machY = slot_mark + fileX
    """
    slot_mark = bed_y_mm - (slot_inches + edge_margin_in) * 25.4
    result = []
    for s in segs:
        if rail == "A":
            x1 = rail_width_mm + s["y1"]
            y1 = slot_mark - s["x1"]
            x2 = rail_width_mm + s["y2"]
            y2 = slot_mark - s["x2"]
        else:
            far_x = bed_x_mm - rail_width_mm
            x1 = far_x - s["y1"]
            y1 = slot_mark + s["x1"]
            x2 = far_x - s["y2"]
            y2 = slot_mark + s["x2"]
        result.append({
            "x1": round(x1, 3), "y1": round(y1, 3),
            "x2": round(x2, 3), "y2": round(y2, 3),
            "cutting": s["cutting"],
        })
    return result


def _placement_dict(instance_id: str, placed: PlacedPart) -> dict:
    br = blank_rect(placed, _rail_width(), _bed_x(), _bed_y(), _edge_margin_in())
    rel = _placement_paths.get(instance_id, placed.part.filename)
    segments = _transform_segments(
        placed.part.segments,
        placed.rail,
        placed.slot_inches,
        _rail_width(),
        _bed_x(),
        _bed_y(),
        _edge_margin_in(),
    )
    tools_list = [
        {
            "tool_number": num,
            "description": info.get("description", ""),
            "diameter_inches": info.get("diameter_inches"),
        }
        for num, info in placed.part.tools.items()
    ]
    return {
        "instance_id": instance_id,
        "filename": placed.part.filename,
        "path": rel,
        "rail": placed.rail,
        "slot_inches": placed.slot_inches,
        "slot": slot_label(placed.rail, placed.slot_inches),
        "machine_x": br.min_x,
        "machine_y": br.min_y,
        "vcarve_x_span": placed.part.vcarve_x_span,
        "vcarve_y_span": placed.part.vcarve_y_span,
        "tools": tools_list,
        "tool_sequence": [gp.tool_number for gp in placed.part.passes],
        "segments": segments,
        "runtime_seconds": placed.part.runtime_seconds,
    }


def _compute_job_safe_z() -> dict:
    """Highest material thickness across placements plus configured clearance."""
    if not _placements:
        return {"value": None, "driven_by": None}
    max_t = -1.0
    driver = None
    for placed in _placements.values():
        t = placed.part.material_thickness
        if t is not None and t > max_t:
            max_t = t
            driver = placed.part.filename
    if driver is None:
        return {"value": None, "driven_by": None}
    clearance = float(config["advanced"]["safe_z_clearance_mm"])
    return {"value": round(max_t + clearance, 4), "driven_by": driver}


def _compute_job_stats() -> dict:
    """Tool sequence, change count, and bed utilization across all placements."""
    bed_x = float(config["advanced"]["bed_x_mm"])
    bed_y = float(config["advanced"]["bed_y_mm"])

    # Execution-ordered unique tool list (mirrors _build_blocks pass-index walk)
    max_passes = max((len(p.part.passes) for p in _placements.values()), default=0)
    ordered_tools: list = []
    seen_tools: set = set()
    for idx in range(max_passes):
        by_tool: set = set()
        for placed in _placements.values():
            if idx < len(placed.part.passes):
                by_tool.add(placed.part.passes[idx].tool_number)
        for tn in sorted(by_tool):
            if tn not in seen_tools:
                seen_tools.add(tn)
                ordered_tools.append(tn)

    bed_area = bed_x * bed_y
    used_area = sum(
        p.part.vcarve_x_span * p.part.vcarve_y_span for p in _placements.values()
    )
    utilization = round(used_area / bed_area * 100, 1) if bed_area else 0.0

    # Per-part runtimes already include each part's internal T# M06 events.
    # Summing slightly overestimates because the generator merges consecutive
    # same-tool blocks across parts; the .txt report runs the estimator over
    # the actual merged G-code for the precise number.
    runtime_seconds = sum(p.part.runtime_seconds for p in _placements.values())

    return {
        "tool_sequence": ordered_tools,
        "tool_changes": max(0, len(ordered_tools) - 1),
        "utilization": utilization,
        "runtime_seconds": round(runtime_seconds, 2),
    }


def _tool_compatibility() -> dict:
    """Tool compatibility matrix across all placed parts, ordered by job execution sequence."""
    if not _placements:
        return {"matrix": [], "has_conflict": False}

    # Build execution-ordered unique tool list (mirrors _build_blocks pass-index walk)
    max_passes = max(len(p.part.passes) for p in _placements.values())
    ordered_tools: list = []
    seen_tools: set = set()
    for idx in range(max_passes):
        by_tool: set = set()
        for placed in _placements.values():
            if idx < len(placed.part.passes):
                by_tool.add(placed.part.passes[idx].tool_number)
        for tn in sorted(by_tool):
            if tn not in seen_tools:
                seen_tools.add(tn)
                ordered_tools.append(tn)

    # Collect one usage entry per (tool, placed-part) pair using pass membership
    usages_by_tool: Dict[str, list] = {tn: [] for tn in ordered_tools}
    for placed in _placements.values():
        pass_tools = {gp.tool_number for gp in placed.part.passes}
        for tn in ordered_tools:
            if tn not in pass_tools:
                continue
            info = placed.part.tools.get(tn, {})
            entry = {
                "filename": placed.part.filename,
                "description": info.get("description", ""),
                "diameter_inches": info.get("diameter_inches"),
            }
            if entry not in usages_by_tool[tn]:
                usages_by_tool[tn].append(entry)

    matrix = []
    has_conflict = False
    for tn in ordered_tools:
        usages = usages_by_tool[tn]
        conflict = len({u["description"] for u in usages}) > 1
        if conflict:
            has_conflict = True
        matrix.append({"tool_number": tn, "usages": usages, "conflict": conflict})

    return {"matrix": matrix, "has_conflict": has_conflict}


def _build_pdf_model(job_name: str, settings: dict, gcode: str = "") -> tuple:
    """Assemble (meta, parts, geom) for pdf_report.generate_layout_pdf.

    Parts are emitted in placement order with blanks and toolpaths already in
    machine coordinates (via blank_rect / _transform_segments), and a stable
    per-filename color matching the on-screen canvas palette.
    """
    rail_w, bed_x, bed_y, edge = _rail_width(), _bed_x(), _bed_y(), _edge_margin_in()

    # Stable color per unique filename, assigned in first-seen order (bed.js).
    color_idx: Dict[str, int] = {}

    parts = []
    tools_seen: Dict[str, bool] = {}
    for i, placed in enumerate(_placements.values(), start=1):
        fn = placed.part.filename
        if fn not in color_idx:
            color_idx[fn] = len(color_idx)
        br = blank_rect(placed, rail_w, bed_x, bed_y, edge)
        for num in (gp.tool_number for gp in placed.part.passes):
            tools_seen[num] = True
        parts.append({
            "index": i,
            "label": slot_label(placed.rail, placed.slot_inches),
            "name": fn,
            "rail": placed.rail,
            "slot_inches": placed.slot_inches,
            "size_mm": (placed.part.vcarve_x_span, placed.part.vcarve_y_span),
            "blank": (br.min_x, br.max_x, br.min_y, br.max_y),
            "segments": _transform_segments(
                placed.part.segments, placed.rail, placed.slot_inches,
                rail_w, bed_x, bed_y, edge,
            ),
            "tools": [
                {"tool_number": num,
                 "description": info.get("description", ""),
                 "diameter_inches": info.get("diameter_inches")}
                for num, info in placed.part.tools.items()
            ],
            "color": pdf_palette_color(color_idx[fn]),
        })

    runtime = estimate_lines_runtime(gcode.splitlines()) if gcode else None
    meta = {
        "job_name": job_name,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "bed_x_mm": float(settings["advanced"]["bed_x_mm"]),
        "bed_y_mm": float(settings["advanced"]["bed_y_mm"]),
        "safe_z": settings.get("job_safe_z", {}),
        "tool_sequence": list(tools_seen.keys()),
        "tool_changes": max(0, len(tools_seen) - 1),
        "parts_count": len(parts),
        "runtime": format_duration(runtime["seconds"]) if runtime else None,
    }
    geom = {
        "bed_x_mm": bed_x,
        "bed_y_mm": float(settings["advanced"]["bed_y_mm"]),
        "rail_width_mm": rail_w,
        "slots": settings["advanced"].get("slots", []),
        "edge_margin_in": edge,
    }
    return meta, parts, geom


def _output_dir() -> Path:
    d = Path(config["output_path"]).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _job_name(data: dict) -> str:
    return data.get("job_name") or config["job_name_format"].replace("{timestamp}", _timestamp())


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", config=config)


@app.route("/api/config", methods=["GET"])
def api_config_get():
    return jsonify(config)


@app.route("/api/config", methods=["POST"])
def api_config_post():
    global config
    data = request.get_json(force=True) or {}
    if not data:
        return jsonify({"error": "Empty body"}), 400
    for key in ("library_path", "output_path", "job_name_format"):
        if key in data:
            config[key] = data[key]
    if "tools" in data:
        config["tools"].update(data["tools"])
    if "advanced" in data:
        config["advanced"].update(data["advanced"])
    save_config(config)
    return jsonify(config)


@app.route("/api/slots")
def api_slots():
    edge_margin = _edge_margin_in()
    result = []
    for s in config["advanced"]["slots"]:
        s = float(s)
        pitches = []
        if s in _PITCH_13:
            pitches.append("13")
        if s in _PITCH_195:
            pitches.append("19.5")
        label = int(s) if s == int(s) else s
        result.append({
            "inches": s,
            "label_a": f"A{label}",
            "label_b": f"B{label}",
            "machine_y": round(_bed_y() - (s + edge_margin) * 25.4, 4),
            "pitch": pitches,
        })
    return jsonify({"slots": result})


@app.route("/api/library")
def api_library():
    root = _library_root()
    if not os.path.isdir(root):
        return jsonify({"library_path": root, "exists": False, "entries": []})

    def scan(abs_dir: str, rel_dir: str) -> list:
        entries = []
        try:
            names = sorted(os.listdir(abs_dir))
        except PermissionError:
            return entries
        for name in names:
            abs_path = os.path.join(abs_dir, name)
            rel_path = f"{rel_dir}/{name}" if rel_dir else name
            if os.path.isdir(abs_path):
                entries.append({
                    "type": "folder",
                    "name": name,
                    "path": rel_path,
                    "children": scan(abs_path, rel_path),
                })
            elif os.path.splitext(name)[1].lower() in VALID_EXT:
                size = os.path.getsize(abs_path)
                if size > MAX_FILE_BYTES:
                    entries.append({"type": "file", "name": name, "path": rel_path, "error": "File too large"})
                    continue
                try:
                    part = _parse_file(abs_path)
                    _loaded[rel_path] = part  # warm the cache while we're here
                    entries.append({
                        "type": "file",
                        "name": name,
                        "path": rel_path,
                        "vcarve_x_span": part.vcarve_x_span,
                        "vcarve_y_span": part.vcarve_y_span,
                        "material_thickness": part.material_thickness,
                        "tools": list(part.tools.keys()),
                        "z_status": part.z_validation.status,
                        "z_messages": part.z_validation.messages,
                        "runtime_seconds": part.runtime_seconds,
                    })
                except Exception as e:
                    entries.append({"type": "file", "name": name, "path": rel_path, "error": str(e)})
        return entries

    return jsonify({"library_path": root, "exists": True, "entries": scan(root, "")})


@app.route("/api/load-file", methods=["POST"])
def api_load_file():
    data = request.get_json(force=True) or {}
    rel = data.get("path", "").strip()
    if not rel:
        return jsonify({"error": "path required"}), 400
    abs_path = _resolve_library_path(rel)
    if not os.path.isfile(abs_path):
        return jsonify({"error": f"File not found: {rel}"}), 404
    if os.path.splitext(abs_path)[1].lower() not in VALID_EXT:
        return jsonify({"error": "Unsupported file type"}), 400
    if os.path.getsize(abs_path) > MAX_FILE_BYTES:
        return jsonify({"error": "File too large"}), 400
    try:
        part = _parse_file(abs_path)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    _loaded[rel] = part
    return jsonify(_part_dict(part, rel))


@app.route("/api/placements", methods=["GET"])
def api_placements_get():
    return jsonify({
        "placements": [_placement_dict(iid, p) for iid, p in _placements.items()],
        "compatibility": _tool_compatibility(),
        "job_safe_z": _compute_job_safe_z(),
        **_compute_job_stats(),
    })


@app.route("/api/place", methods=["POST"])
def api_place():
    data = request.get_json(force=True) or {}
    rel = data.get("path", "").strip()
    rail = data.get("rail", "").upper()
    slot_raw = data.get("slot_inches")

    if not rel or rail not in ("A", "B") or slot_raw is None:
        return jsonify({"error": "path, rail (A/B), and slot_inches required"}), 400
    try:
        slot_inches = float(slot_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "slot_inches must be a number"}), 400

    valid_slots = [float(s) for s in config["advanced"]["slots"]]
    if slot_inches not in valid_slots:
        return jsonify({"error": f"Not a valid slot: {slot_inches}"}), 400

    if rel not in _loaded:
        abs_path = _resolve_library_path(rel)
        if not os.path.isfile(abs_path):
            return jsonify({"error": f"File not found: {rel}"}), 404
        try:
            _loaded[rel] = _parse_file(abs_path)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    part = _loaded[rel]

    if part.z_validation.status == "blocked":
        msg = part.z_validation.messages[0] if part.z_validation.messages else "File failed Z validation."
        return jsonify({"ok": False, "error": "z_blocked", "message": msg}), 422

    library = ToolLibrary(config.get("tools", {}))
    unknown = library.find_unknown_tools(part)
    if unknown:
        return jsonify({"ok": False, "error": "unknown_tools", "tools": unknown}), 422

    instance_id = _make_instance_id(part.filename)
    new_placed = PlacedPart(part=part, rail=rail, slot_inches=slot_inches, instance_id=instance_id)

    result = check_placement(new_placed, list(_placements.values()), _rail_width(), _bed_x(),
                             _bed_y(), _edge_margin_in())
    if result.collides:
        # Roll back the instance counter
        stem = os.path.splitext(part.filename)[0]
        _instance_counts[stem] -= 1
        return jsonify({
            "ok": False,
            "error": "collision",
            "message": result.message,
            "conflicting_instance_id": result.conflicting_instance_id,
        }), 409

    _placements[instance_id] = new_placed
    _placement_paths[instance_id] = rel

    return jsonify({
        "ok": True,
        **_placement_dict(instance_id, new_placed),
        "compatibility": _tool_compatibility(),
        "job_safe_z": _compute_job_safe_z(),
    })


@app.route("/api/place/<instance_id>", methods=["DELETE"])
def api_remove_placement(instance_id: str):
    if instance_id not in _placements:
        return jsonify({"error": "Not found"}), 404
    del _placements[instance_id]
    _placement_paths.pop(instance_id, None)
    return jsonify({
        "ok": True,
        "compatibility": _tool_compatibility(),
        "job_safe_z": _compute_job_safe_z(),
    })


@app.route("/api/compatibility")
def api_compatibility():
    return jsonify(_tool_compatibility())


@app.route("/api/resolve-tool", methods=["POST"])
def api_resolve_tool():
    """
    Operator supplies a diameter for an unknown tool.
    Optionally persists it to the tool library in config.json.
    Updates any in-memory cached parts that reference this tool.
    """
    data = request.get_json(force=True) or {}
    tool_number = (data.get("tool_number") or "").strip().upper()
    diameter_raw = data.get("diameter_inches")
    save_to_library = bool(data.get("save_to_library", False))

    if not tool_number:
        return jsonify({"error": "tool_number required"}), 400
    try:
        diameter = float(diameter_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "diameter_inches must be a number"}), 400
    if diameter <= 0:
        return jsonify({"error": "diameter_inches must be positive"}), 400

    if save_to_library:
        config.setdefault("tools", {})[tool_number] = {
            "name": data.get("description", ""),
            "diameter_inches": diameter,
        }
        save_config(config)

    # Patch all cached parts so /api/place no longer blocks on this tool
    for part in _loaded.values():
        if tool_number in part.tools:
            part.tools[tool_number]["diameter_inches"] = diameter
        else:
            part.tools[tool_number] = {"diameter_inches": diameter, "description": ""}

    return jsonify({"ok": True, "tool_number": tool_number, "diameter_inches": diameter})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    if not _placements:
        return jsonify({"error": "No parts placed"}), 400
    compat = _tool_compatibility()
    if compat["has_conflict"]:
        return jsonify({"error": "Resolve tool compatibility conflicts before generating"}), 422

    data = request.get_json(force=True) or {}
    job_name = _job_name(data)
    safe_z = _compute_job_safe_z()
    settings = {**config, "job_name": job_name, "job_safe_z": safe_z}

    try:
        gcode = generate_master_gcode(list(_placements.values()), settings)
    except Exception as e:
        return jsonify({"error": f"Generation failed: {e}"}), 500

    out = _output_dir()
    nc_path = out / f"{job_name}.nc"
    pdf_path = out / f"{job_name}.pdf"

    nc_path.write_text(gcode, encoding="utf-8")
    try:
        meta, parts, geom = _build_pdf_model(job_name, settings, gcode)
        generate_layout_pdf(pdf_path, meta, parts, geom)
    except Exception as e:
        return jsonify({"error": f"PDF generation failed: {e}"}), 500

    return jsonify({"ok": True, "job_name": job_name,
                    "nc_path": str(nc_path), "pdf_path": str(pdf_path)})


@app.route("/api/audit")
def api_audit():
    """Audit every library file against best practices, write CSVs, and return the rows."""
    root = _library_root()
    if not os.path.isdir(root):
        return jsonify({"error": f"Library path does not exist: {root}"}), 400

    tool_lib = ToolLibrary(config.get("tools", {}))
    thresholds = build_thresholds(config)
    file_rows, tool_rows, toolpath_rows = scan_library(root, tool_lib, thresholds)

    out = _output_dir()
    stamp = _timestamp()
    file_csv = out / f"library_audit_{stamp}.csv"
    tool_csv = out / f"library_audit_tools_{stamp}.csv"
    toolpath_csv = out / f"library_audit_toolpaths_{stamp}.csv"
    write_csv(file_csv, FILE_COLUMNS, file_rows)
    write_csv(tool_csv, TOOL_COLUMNS, tool_rows)
    write_csv(toolpath_csv, TOOLPATH_COLUMNS, toolpath_rows)

    summary = {"ok": 0, "warning": 0, "blocked": 0}
    for r in file_rows:
        status = r.get("status", "ok")
        summary[status] = summary.get(status, 0) + 1
    summary["total"] = len(file_rows)
    summary["feed_varies"] = sum(1 for r in file_rows if r.get("feed_varies"))

    return jsonify({
        "ok": True,
        "file_csv": str(file_csv),
        "tool_csv": str(tool_csv),
        "toolpath_csv": str(toolpath_csv),
        "summary": summary,
        "files": file_rows,
    })


@app.route("/api/save-job", methods=["POST"])
def api_save_job():
    if not _placements:
        return jsonify({"error": "No parts placed"}), 400

    data = request.get_json(force=True) or {}
    job_name = _job_name(data)

    job = {
        "version": "1.1",
        "created": datetime.now().isoformat(),
        "job_name": job_name,
        "placements": [
            {
                "filename": p.part.filename,
                "path": _placement_paths.get(iid, p.part.filename),
                "rail": p.rail,
                "slot_inches": p.slot_inches,
                "slot": slot_label(p.rail, p.slot_inches),
                "instance_id": iid,
                "vcarve_x_span": p.part.vcarve_x_span,
                "vcarve_y_span": p.part.vcarve_y_span,
            }
            for iid, p in _placements.items()
        ],
    }
    out = _output_dir()
    cnj_path = os.path.join(out, f"{job_name}.cnj")
    with open(cnj_path, "w", encoding="utf-8") as f:
        json.dump(job, f, indent=2)

    return jsonify({"ok": True, "path": cnj_path, "job_name": job_name})


@app.route("/api/load-job", methods=["POST"])
def api_load_job():
    global _placements, _placement_paths, _instance_counts

    data = request.get_json(force=True) or {}
    path = data.get("path", "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400

    abs_path = os.path.expanduser(path)
    if not os.path.isfile(abs_path):
        return jsonify({"error": f"File not found: {path}"}), 404
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            job = json.load(f)
    except Exception as e:
        return jsonify({"error": f"Could not read job file: {e}"}), 400

    new_placements: Dict[str, PlacedPart] = {}
    new_paths: Dict[str, str] = {}
    new_counts: Dict[str, int] = {}
    warnings = []

    for entry in job.get("placements", []):
        rel = entry.get("path") or entry.get("filename", "")
        rail = entry.get("rail", "A").upper()
        slot_inches = float(entry.get("slot_inches", 0))
        instance_id = entry.get("instance_id", "")

        if rel not in _loaded:
            try:
                abs_file = _resolve_library_path(rel)
            except Exception:
                warnings.append(f"Invalid path in job file: {rel} — skipped")
                continue
            if not os.path.isfile(abs_file):
                warnings.append(f"File not found in library: {rel} — skipped")
                continue
            try:
                _loaded[rel] = _parse_file(abs_file)
            except Exception as e:
                warnings.append(f"Could not parse {rel}: {e} — skipped")
                continue

        part = _loaded[rel]
        saved_w = entry.get("vcarve_x_span") or entry.get("blank_width")   # back-compat
        saved_h = entry.get("vcarve_y_span") or entry.get("blank_height")
        if saved_w and saved_h:
            if abs(part.vcarve_x_span - saved_w) > 0.1 or abs(part.vcarve_y_span - saved_h) > 0.1:
                warnings.append(
                    f"{rel}: dimensions changed since job was saved "
                    f"({saved_w}×{saved_h} → {part.vcarve_x_span}×{part.vcarve_y_span}). "
                    "Re-placement required."
                )
                continue

        placed = PlacedPart(part=part, rail=rail, slot_inches=slot_inches, instance_id=instance_id)
        new_placements[instance_id] = placed
        new_paths[instance_id] = rel
        stem = os.path.splitext(part.filename)[0]
        new_counts[stem] = new_counts.get(stem, 0) + 1

    _placements = new_placements
    _placement_paths = new_paths
    _instance_counts = new_counts

    return jsonify({
        "ok": True,
        "job_name": job.get("job_name", ""),
        "warnings": warnings,
        "placements": [_placement_dict(iid, p) for iid, p in _placements.items()],
        "compatibility": _tool_compatibility(),
        "job_safe_z": _compute_job_safe_z(),
        **_compute_job_stats(),
    })


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5001, debug=debug)
