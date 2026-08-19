import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from flask import Flask, abort, jsonify, render_template, request

from audit_library import (
    FILE_COLUMNS,
    TOOL_COLUMNS,
    TOOLPATH_COLUMNS,
    build_thresholds,
    scan_library,
    write_csv,
)
from collision import (
    PlacedPart, blank_rect, check_placement, slot_label, rail_geom, slot_mark_y,
)
from config import (
    load_config, save_config,
    _sanitize_path_str, normalize_library_paths, resolve_library_root,
)
from gcode_generator import IdentityMap, block_tool_sequence, generate_master_gcode
from gcode_parser import GcodePart, parse_vcarve_text
from gcode_validator import format_findings, validate_gcode
from pdf_report import generate_layout_pdf, palette_color as pdf_palette_color
from pocket_map import STAGED, build_changer_state
from runtime_estimator import (
    DEFAULT_TOOL_CHANGE_SECONDS, estimate_lines_runtime, format_duration,
)
from tool_library import (
    FLUTE_DIRECTIONS, GEOMETRY_CLASSES, LibraryTool, ToolLibrary, ToolLibraryError,
    normalize_code, resolve_part,
)

app = Flask(__name__, template_folder="templates", static_folder="static")

config = load_config()

# Operator data with its own lifecycle — kept out of config.json deliberately, so a
# growing library never lands in the file the settings panel writes (spec §3.5).
tool_library = ToolLibrary.load()

# In-memory session state — single user, local app
_loaded: Dict[str, GcodePart] = {}      # library-relative path → GcodePart
_placements: Dict[str, PlacedPart] = {} # instance_id → PlacedPart
_placement_paths: Dict[str, str] = {}   # instance_id → library-relative path
_instance_counts: Dict[str, int] = {}   # filename stem → counter for unique IDs

# Job-scoped and deliberately never persisted (spec §3.2). Save/load job is sunset, so
# the whole class of "stale override in a saved job" problems does not exist.
_tool_binds: Dict[str, Dict[str, str]] = {}   # library-relative path → {T#: code}
_pocket_overrides: Dict[str, int] = {}        # code → pocket; the operator's drags

VALID_EXT = {".nc", ".mmg"}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB

_PITCH_13 = {0, 13, 26, 39, 52, 65, 78, 91, 104, 117}
_PITCH_195 = {0, 19.5, 39, 58.5, 78, 97.5, 117}


# ── private helpers ───────────────────────────────────────────────────────────

def _library_root() -> str:
    return str(resolve_library_root(config["library_path"]))


def _resolve_library_path(rel: str) -> str:
    """Resolve a library-relative path and abort 400 on any traversal attempt."""
    root = resolve_library_root(config["library_path"])
    full = (root / rel).resolve()
    if root != full and root not in full.parents:
        abort(400, description="Invalid path")
    return str(full)


def _bed_x() -> float:
    return float(config["advanced"]["bed_x_mm"])


def _bed_y() -> float:
    return float(config["advanced"]["bed_y_mm"])


def _rails() -> dict:
    """Per-rail geometry overrides from config; None falls back to RAIL_DEFAULTS."""
    return config["advanced"].get("rails")


def _tool_capacity() -> int:
    """How many tools the Smartshop 2 tool changer holds (default 8)."""
    return int(config["advanced"].get("tool_capacity", 8))


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
    rails: dict = None,
) -> list:
    """
    Convert file-coordinate segments to machine coordinates for canvas rendering.

    Uses the same per-rail datum/direction as the generator and collision
    detection (collision.rail_geom / slot_mark_y):
        machine X = x_mm      + x_dir    * file_Y
        machine Y = slot_mark + slot_dir * file_X
    """
    g = rail_geom(rail, rails)
    x0, xd = float(g["x_mm"]), float(g["x_dir"])
    slot_mark, sd = slot_mark_y(rail, slot_inches, rails), float(g["slot_dir"])
    result = []
    for s in segs:
        x1 = x0 + xd * s["y1"]
        y1 = slot_mark + sd * s["x1"]
        x2 = x0 + xd * s["y2"]
        y2 = slot_mark + sd * s["x2"]
        result.append({
            "x1": round(x1, 3), "y1": round(y1, 3),
            "x2": round(x2, 3), "y2": round(y2, 3),
            "cutting": s["cutting"],
        })
    return result


def _placement_dict(instance_id: str, placed: PlacedPart) -> dict:
    br = blank_rect(placed, _rails())
    rel = _placement_paths.get(instance_id, placed.part.filename)
    segments = _transform_segments(
        placed.part.segments,
        placed.rail,
        placed.slot_inches,
        _rails(),
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
        # Full blank extent in machine coords so the canvas never re-derives it —
        # blank_rect is the single source of truth for where a part sits.
        "blank": [br.min_x, br.max_x, br.min_y, br.max_y],
        "vcarve_x_span": placed.part.vcarve_x_span,
        "vcarve_y_span": placed.part.vcarve_y_span,
        # Optional by nature — some library files declare no material size — and the
        # canvas must render that as visibly *unknown* rather than as a thin part
        # (issue #28). None is the honest answer, so it is passed through as None.
        "material_thickness": placed.part.material_thickness,
        "tools": tools_list,
        "tool_sequence": [gp.tool_number for gp in placed.part.passes],
        "segments": segments,
        "runtime_seconds": placed.part.runtime_seconds,
    }


def _compute_job_safe_z() -> dict:
    """The highest clearance any placed part needs (#22).

    Two candidates, and the job takes whichever is higher:

    - **thickest material + configured clearance** — clears the stock, which is all
      a flat 2D job needs;
    - **the highest retract any source file asks for itself** (`part.safe_z`, the
      max `G43 Z` in that file) — Fusion sets this per operation, and a part with a
      feature standing above the stock top retracts *above* thickness + clearance.

    Taking only the first is the bug: a rapid that cleared the part inside its own
    file can clip it once the part is one of several in a merged job. Higher is
    always safe here — the cost of an over-high retract is rapid seconds, and
    `gcode_validator` still checks what is emitted.
    """
    if not _placements:
        return {"value": None, "driven_by": None}

    clearance = float(config["advanced"]["safe_z_clearance_mm"])
    best = None
    driver = None

    for placed in _placements.values():
        part = placed.part
        candidates = []
        if part.material_thickness is not None:
            candidates.append((part.material_thickness + clearance, "stock"))
        if part.safe_z is not None:
            candidates.append((part.safe_z, "retract"))
        for value, basis in candidates:
            if best is None or value > best:
                best = value
                driver = f"{part.filename} ({basis})"

    if best is None:
        return {"value": None, "driven_by": None}
    return {"value": round(best, 4), "driven_by": driver}


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

    # Utilization is measured against the real table, not the drawn extent. The
    # canvas rectangle runs from machine 0 to the far table corner, so it includes
    # a sliver (X < 61.49, Y < 24.99 mm) that is off the table and that
    # check_envelope already refuses to place on.
    travel = config["advanced"].get("machine_travel") or {}
    try:
        bed_area = (float(travel["x_max"]) - float(travel["x_min"])) * (
            float(travel["y_max"]) - float(travel["y_min"])
        )
    except (KeyError, TypeError, ValueError):
        bed_area = bed_x * bed_y
    used_area = sum(
        p.part.vcarve_x_span * p.part.vcarve_y_span for p in _placements.values()
    )
    utilization = round(used_area / bed_area * 100, 1) if bed_area else 0.0

    # The tool of every block the generator will emit, in emitted order. Its
    # length is the tool-change count: a tool that recurs at a later pass index
    # gets changed back to, so this is longer than `ordered_tools` whenever the
    # parts disagree about tool order. Counting distinct tools instead understated
    # both the change count and the run time (issue #7).
    block_tools = block_tool_sequence(list(_placements.values()))

    # Per-part runtimes deliberately exclude tool-change time (the generator
    # merges same-tool passes across parts, so a part's own change count means
    # nothing in a merged job). Charge it once per emitted block instead — per
    # block, not per distinct tool, because the 30 s touch-off inside
    # DEFAULT_TOOL_CHANGE_SECONDS is paid again every time a tool is called back
    # (issue #6). The report runs the estimator over the actual merged G-code for
    # the precise number; this is the live approximation.
    runtime_seconds = (
        sum(p.part.runtime_seconds for p in _placements.values())
        + len(block_tools) * DEFAULT_TOOL_CHANGE_SECONDS
    )

    capacity = _tool_capacity()
    return {
        "tool_sequence": ordered_tools,
        "tool_changes": len(block_tools),
        "tool_count": len(ordered_tools),
        "tool_capacity": capacity,
        "tools_over_capacity": len(ordered_tools) > capacity,
        "utilization": utilization,
        "runtime_seconds": round(runtime_seconds, 2),
    }


def _resolve(rel: str, part: GcodePart):
    """Resolve one file's tools against the identity library, with this run's binds."""
    return resolve_part(tool_library, part, _tool_binds.get(rel))


def _changer_state() -> dict:
    """The tool-changer map and the §3.4 validity gate, recomputed from scratch.

    Nothing here is cached, and that is the point: the map is a *pure function* of
    (resolved tools, declared default slots) plus the operator's drags, so recomputing
    it can never disagree with itself. See `pocket_map` for why determinism is a
    consequence rather than a rule.

    This replaces `_tool_compatibility`, which the changer dock retires. That function
    was sound in the safe direction and blind in the dangerous one: `conflict` fired
    only when one `T#` carried *differing* description strings, so two genuinely
    different cutters sharing a stale identical string sailed through (the library has
    exactly that case — `T2` and `T9` post `End Mill {0.5 inch}` byte-for-byte in one
    file). The gate it fed is unchanged; its *signal* is what was unsound (§1.1, §3.4).
    """
    entries = []
    for instance_id, placed in _placements.items():
        rel = _placement_paths.get(instance_id, placed.part.filename)
        entries.append({
            "instance_id": instance_id,
            "filename": placed.part.filename,
            # The library-relative path, because a manual bind is scoped to
            # (path, T#) — the dock has to be able to bind an orphan it surfaces,
            # and the filename alone is not enough to find the file again.
            "path": rel,
            "slot": slot_label(placed.rail, placed.slot_inches),
            "resolution": _resolve(rel, placed.part),
        })
    return build_changer_state(
        tool_library, entries, _pocket_overrides, _tool_capacity(),
    )


def _identity_map(state: dict) -> IdentityMap:
    """Turn the changer state into the generator's identity/pocket map.

    Built off the changer state, which is built off library codes and declared default
    slots — **never off the remapped `T` numbers** (§3.2.1, §4.2). Reading post-remap
    numbers here would make the emitted output depend on an assignment that depends on
    the output.
    """
    codes: Dict[str, Dict[str, str]] = {}
    for instance_id, placed in _placements.items():
        rel = _placement_paths.get(instance_id, placed.part.filename)
        res = _resolve(rel, placed.part)
        codes[instance_id] = {
            tn: b.library_code for tn, b in res.bindings.items() if b.library_code
        }
    pockets = {c: p for c, p in state["assignment"].items() if p != STAGED}
    return IdentityMap(codes=codes, pockets=pockets)


def _learn_descriptions(res) -> None:
    """Adopt the first description a code is ever seen to post.

    An empty `cam_descriptions` set has nothing to disagree with, so there is no prompt
    to show — the seal is a *change* detector, not an approval queue (§3.5.3).
    """
    if not res.learned:
        return
    for code, description in res.learned:
        tool_library.learn_description(code, description)
    tool_library.save()


def _resolution_block(rel: str, res) -> Optional[tuple]:
    """The 422 payload for a file whose tools do not resolve, or None if it is clean.

    Resolution is **strict and happens before placement** (§3.5.3): an unresolved tool
    has no radius, the library is its only source, and the app must not invent one.
    Resolve or do not place.
    """
    if res.duplicate_codes:
        pairs = "; ".join(
            f"{' and '.join(nums)} both resolve to {code}"
            for code, nums in res.duplicate_codes
        )
        return ({
            "ok": False,
            "error": "duplicate_tool_code",
            "message": (
                f"{res.filename} uses one tool code for two different tools ({pairs}). "
                "The CAM file already says these are different cutters by giving them "
                "different tool numbers, so this has to be fixed in Fusion or VCarve — "
                "the nest tool will not merge them."
            ),
            "duplicates": [{"library_code": c, "tool_numbers": n}
                           for c, n in res.duplicate_codes],
        }, 422)

    if res.seal_prompts:
        return ({
            "ok": False,
            "error": "description_changed",
            "message": (
                "A tool in this file posts a description it has never posted before. "
                "Either it was renamed in CAM, or two different cutters have been given "
                "the same tool code — which would cut one of them with the wrong tool."
            ),
            "path": rel,
            "prompts": [{
                "library_code": p.library_code,
                "tool_number": p.tool_number,
                "known": p.known,
                "posted": p.posted,
                "name": (tool_library.get(p.library_code).name
                         if tool_library.get(p.library_code) else ""),
            } for p in res.seal_prompts],
        }, 422)

    unresolved = res.unresolved
    if unresolved:
        return ({
            "ok": False,
            "error": "unresolved_tools",
            "message": (
                "This file uses tools the nest tool cannot identify. Add each one to "
                "your tool library, or say which library tool it is for this job."
            ),
            "path": rel,
            "tools": [{
                "tool_number": b.tool_number,
                "status": b.status,
                "code": b.code,
                # `""` = the file has a TOOLID comment with CODE= left blank, so the
                # Fusion tool's Product ID is empty and one edit fixes it for every
                # future post; null = the file carries no code at all. The resolver
                # says which, because only the first is worth going back to CAM for.
                "code_field": b.code_field,
                "description": b.description,
                "cam_description": b.cam_description,
                # Display only, and never compared against the declaration — it is the
                # nominal size, not the widest cutting point (§3.5.2, §3.5.3).
                "posted_diameter_inches": b.posted_diameter_inches,
            } for b in unresolved],
        }, 422)

    return None


def _setup_rows(state: dict) -> list:
    """The operator setup sheet, one row per loaded pocket (issue #13).

    Keyed on **pocket**, not on any file's `T#` — under identity merging those are
    different things, and the pocket is what the operator physically loads and what the
    emitted `T# M06` calls.
    """
    return sorted(
        [{
            "pocket": t["pocket"],
            "code": t["code"],
            "name": t["name"],
            "diameter_inches": t["diameter_inches"],
            "geometry_class": t["geometry_class"],
            "flute_display": t["flute_display"],
            "cutting_length_in": t["cutting_length_in"],
            "default_slot": t["default_slot"],
            "off_home": t["off_home"],
            "tool_numbers": t["tool_numbers"],
            "parts": sorted({u["filename"] for u in t["usages"]}),
        } for t in state.get("tools", []) if t.get("pocket")],
        key=lambda r: r["pocket"],
    )


def _setup_sheet_text(job_name: str, state: dict, safe_z: dict) -> str:
    """The same sheet as plain text, written beside the `.nc`.

    Generation is deliberately **not** gated on confirming the changer is loaded
    (operator's call, 2026-08-17) — the sheet informs, it does not interrogate.
    """
    rows = _setup_rows(state)
    out = [
        f"TOOL SETUP — {job_name}",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "Load the changer to match this before running. The pocket number is the",
        "tool number the program calls, and the height offset follows the pocket.",
        "",
    ]
    width = max([len(r["name"]) for r in rows] + [4])
    for r in rows:
        called = ", ".join(r["tool_numbers"])
        out.append(
            f"  Pocket {r['pocket']}  {r['name']:<{width}}  {r['diameter_inches']:g}\" "
            f"{r['geometry_class']}, {r['flute_display']}"
            + (f", {r['cutting_length_in']}\" cutting length" if r["cutting_length_in"] else "")
        )
        out.append(f"{'':>12}{'':<{width}}  code {r['code']}   files call it {called}")
        out.append(f"{'':>12}{'':<{width}}  needed by {', '.join(r['parts'])}")
        if r["off_home"]:
            # A deviation is a TEMPORARY instruction, because the declared slot is the
            # standard the operator is being trained toward (§3.2.1).
            out.append(f"{'':>12}{'':<{width}}  *** moved for this job only — "
                       f"normally lives in pocket {r['default_slot']}, put it back after ***")
        out.append("")

    empty = [p for p in range(1, state.get("capacity", 8) + 1)
             if p not in {r["pocket"] for r in rows}]
    if empty:
        out.append(f"  Pockets not used by this job: {', '.join(str(p) for p in empty)}")
        out.append("")
    if safe_z.get("value"):
        out.append(f"  Safe Z for the whole job: {safe_z['value'] / 25.4:.3f}\" "
                   f"(driven by {safe_z.get('driven_by')})")
    return "\n".join(out) + "\n"


def _build_pdf_model(job_name: str, settings: dict, gcode: str = "",
                     state: Optional[dict] = None) -> tuple:
    """Assemble (meta, parts, geom) for pdf_report.generate_layout_pdf.

    Parts are emitted in placement order with blanks and toolpaths already in
    machine coordinates (via blank_rect / _transform_segments), and a stable
    per-filename color matching the on-screen canvas palette.
    """
    bed_x, bed_y, rails = _bed_x(), _bed_y(), _rails()
    state = state if state is not None else _changer_state()
    # instance_id → {T#: pocket}, so the placement table can show the file's own number
    # beside the pocket it will actually run from.
    pockets_by_instance: Dict[str, Dict[str, int]] = {}
    for t in state.get("tools", []):
        for u in t["usages"]:
            if t.get("pocket"):
                pockets_by_instance.setdefault(u["instance_id"], {})[u["tool_number"]] = t["pocket"]

    # Stable color per unique filename, assigned in first-seen order (bed.js).
    color_idx: Dict[str, int] = {}

    parts = []
    tools_seen: Dict[str, bool] = {}
    for i, placed in enumerate(_placements.values(), start=1):
        fn = placed.part.filename
        if fn not in color_idx:
            color_idx[fn] = len(color_idx)
        br = blank_rect(placed, rails)
        for num in (gp.tool_number for gp in placed.part.passes):
            tools_seen[num] = True
        parts.append({
            "index": i,
            "label": slot_label(placed.rail, placed.slot_inches),
            "name": fn,
            "rail": placed.rail,
            "slot_inches": placed.slot_inches,
            "size_mm": (placed.part.vcarve_x_span, placed.part.vcarve_y_span),
            "material_thickness": placed.part.material_thickness,
            "blank": (br.min_x, br.max_x, br.min_y, br.max_y),
            "segments": _transform_segments(
                placed.part.segments, placed.rail, placed.slot_inches, rails,
            ),
            "tools": [
                {"tool_number": num,
                 "description": info.get("description", ""),
                 "diameter_inches": info.get("diameter_inches"),
                 "pocket": pockets_by_instance.get(placed.instance_id, {}).get(num)}
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
        # Counted off the emitted file's own T# M06 lines rather than derived
        # from the tool list — the file is the thing the machine runs, and a
        # recurring tool is a real change back (issue #7).
        "tool_changes": runtime["tool_change_count"] if runtime else 0,
        "parts_count": len(parts),
        "runtime": format_duration(runtime["seconds"]) if runtime else None,
        "setup": _setup_rows(state),
    }
    geom = {
        "bed_x_mm": bed_x,
        "bed_y_mm": float(settings["advanced"]["bed_y_mm"]),
        "slots": settings["advanced"].get("slots", []),
        "rails": {r: rail_geom(r, rails) for r in ("A", "B")},
    }
    return meta, parts, geom


def _output_dir() -> Path:
    d = Path(_sanitize_path_str(config["output_path"])).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _job_name(data: dict) -> str:
    return data.get("job_name") or config["job_name_format"].replace("{timestamp}", _timestamp())


# ── routes ────────────────────────────────────────────────────────────────────

def _asset(rel: str) -> str:
    """`/static/foo.js?v=<mtime>` — cache-bust every asset on edit.

    A tab left open across a server restart is the normal case here, not an edge one:
    this is a dev server the operator reloads a lot, and `git pull` + restart changes
    the API underneath a page that is still holding the old JS. That mismatch is
    silent and reads as a nonsense error — an old client that did not recognise a new
    error slug printed the slug itself into the status bar, with no dialog and no clue
    that the page was stale.

    Stamping the URL means a reload can never serve yesterday's module against today's
    routes. It does not help a page that is never reloaded at all, which nothing can.
    """
    try:
        stamp = int((Path(app.static_folder) / rel).stat().st_mtime)
    except OSError:
        stamp = 0
    return f"/static/{rel}?v={stamp}"


app.jinja_env.globals["asset"] = _asset


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
    if "library_path" in data:
        config["library_path"] = normalize_library_paths(data["library_path"])
    if "output_path" in data:
        config["output_path"] = _sanitize_path_str(data["output_path"])
    if "job_name_format" in data:
        config["job_name_format"] = data["job_name_format"]
    # `tools` is deliberately not accepted here any more. The pocket-keyed map is gone
    # from config.json entirely (spec §8) — it was junk in its entirety, and its `T4`
    # "Table Stiff" declared 0.75" for a cutter that is 2.38", an X-envelope
    # under-inflation of 1.6" on a real tool. Tool data lives in tool_library.json,
    # which the settings panel does not write.
    if "advanced" in data:
        adv_in = dict(data["advanced"])
        # `rails` is nested, so a shallow update would drop the rail the caller
        # didn't send and silently wipe slot_dir/x_dir. Merge it per rail instead.
        rails_in = adv_in.pop("rails", None)
        config["advanced"].update(adv_in)
        if rails_in:
            current = config["advanced"].setdefault("rails", {})
            for rail, values in rails_in.items():
                if rail not in ("A", "B") or not isinstance(values, dict):
                    continue
                merged = {**rail_geom(rail, config["advanced"].get("rails")), **values}
                current[rail] = merged
    save_config(config)
    return jsonify(config)


@app.route("/api/slots")
def api_slots():
    rails = _rails()
    result = []
    for s in config["advanced"]["slots"]:
        s = float(s)
        pitches = []
        if s in _PITCH_13:
            pitches.append("13")
        if s in _PITCH_195:
            pitches.append("19.5")
        label = int(s) if s == int(s) else s
        y_a = round(slot_mark_y("A", s, rails), 4)
        y_b = round(slot_mark_y("B", s, rails), 4)
        result.append({
            "inches": s,
            "label_a": f"A{label}",
            "label_b": f"B{label}",
            # The rails run in opposite directions, so a slot number is at a
            # different machine Y on each. machine_y is kept as an alias for
            # machine_y_a for older callers.
            "machine_y_a": y_a,
            "machine_y_b": y_b,
            "machine_y": y_a,
            "pitch": pitches,
        })
    return jsonify({"slots": result, "rails": {r: rail_geom(r, rails) for r in ("A", "B")}})


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
        "changer": _changer_state(),
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

    res = _resolve(rel, part)
    blocked = _resolution_block(rel, res)
    if blocked:
        payload, status = blocked
        return jsonify(payload), status
    _learn_descriptions(res)

    instance_id = _make_instance_id(part.filename)
    new_placed = PlacedPart(
        part=part, rail=rail, slot_inches=slot_inches, instance_id=instance_id,
        # The declared diameters travel with the placement, so the collision and
        # envelope checks read the library rather than the file (§3.5.2).
        tool_diameters=res.diameters_by_tool_number(tool_library),
    )

    result = check_placement(new_placed, list(_placements.values()),
                             _rails(), config["advanced"])
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
        "changer": _changer_state(),
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
        "changer": _changer_state(),
        "job_safe_z": _compute_job_safe_z(),
    })


# ── the tool changer (issue #11) ──────────────────────────────────────────────

@app.route("/api/changer")
def api_changer():
    return jsonify(_changer_state())


@app.route("/api/changer/assign", methods=["POST"])
def api_changer_assign():
    """Move a tool to a pocket, or back to staging with pocket 0.

    **Never refused and never a swap** (§3.2.1). Dropping onto an occupied pocket makes
    the two coexist — visibly and invalidly — because swapping two tools is impossible
    without transiting a double-occupied pocket, so refusing the drop would deadlock
    the swap. Only *generation* is gated.

    The override is job-scoped and **never written back to the library**: the declared
    default slot is prescriptive, and the app re-proposing it next job is the intended
    nag, not churn.
    """
    data = request.get_json(force=True) or {}
    code = normalize_code(data.get("code"))
    try:
        pocket = int(data.get("pocket"))
    except (TypeError, ValueError):
        return jsonify({"error": "pocket must be a number (0 stages the tool)"}), 400

    if not code:
        return jsonify({"error": "code required"}), 400
    capacity = _tool_capacity()
    if pocket != STAGED and not (1 <= pocket <= capacity):
        return jsonify({"error": f"Pocket must be between 1 and {capacity}, or 0 to stage"}), 400

    _pocket_overrides[code] = pocket
    return jsonify({"ok": True, "changer": _changer_state()})


@app.route("/api/changer/reset", methods=["POST"])
def api_changer_reset():
    """Drop every drag and re-seed from the library's declared default slots."""
    _pocket_overrides.clear()
    return jsonify({"ok": True, "changer": _changer_state()})


@app.route("/api/bind-tool", methods=["POST"])
def api_bind_tool():
    """Bind an orphaned toolpath to a library tool, **for this run only**.

    Nothing is remembered: a bind is job-scoped, which is how pocket assignment already
    works. All 26 files in the library predate the shop code, so every one of them
    orphans until re-posted or renamed — that is the safe default working, not a
    failure, which is why this path has to be pleasant rather than punitive (§3.5.3).
    """
    data = request.get_json(force=True) or {}
    rel = (data.get("path") or "").strip()
    tool_number = (data.get("tool_number") or "").strip().upper()
    code = normalize_code(data.get("code"))

    if not rel or not tool_number:
        return jsonify({"error": "path and tool_number required"}), 400
    if code and code not in tool_library:
        return jsonify({"error": f"No tool in your library has the code {code}"}), 404

    binds = _tool_binds.setdefault(rel, {})
    if code:
        binds[tool_number] = code
    else:
        binds.pop(tool_number, None)

    part = _loaded.get(rel)
    payload = {"ok": True, "changer": _changer_state()}
    if part is not None:
        res = _resolve(rel, part)
        payload["resolution"] = {
            "blocked": res.blocked,
            "unresolved": [b.tool_number for b in res.unresolved],
        }
        # A bind is the operator identifying a file that carries no code, so its
        # description says nothing about what that code posts. The seal deliberately
        # does not learn from it (§3.5.3) — `_learn_descriptions` is not called here.
    _refresh_placed_diameters()
    return jsonify(payload)


# ── the tool library (issue #24) ──────────────────────────────────────────────

def _library_payload() -> dict:
    used = _codes_in_use()
    return {
        "tools": [{
            **t.to_dict(),
            "display": t.display,
            "in_use_by": used.get(t.code, []),
        } for t in tool_library.sorted_tools()],
        "geometry_classes": list(GEOMETRY_CLASSES),
        "flute_directions": list(FLUTE_DIRECTIONS),
        "capacity": _tool_capacity(),
    }


def _codes_in_use() -> Dict[str, list]:
    """code → the placed instances that resolve to it. Powers delete-in-use refusal
    and the dock's "removing bracket-L frees pocket 7" attribution (§3.4.1, §3.5.4)."""
    used: Dict[str, list] = {}
    for instance_id, placed in _placements.items():
        rel = _placement_paths.get(instance_id, placed.part.filename)
        for binding in _resolve(rel, placed.part).bindings.values():
            if binding.library_code:
                entry = {"instance_id": instance_id, "filename": placed.part.filename,
                         "slot": slot_label(placed.rail, placed.slot_inches)}
                bucket = used.setdefault(binding.library_code, [])
                if entry not in bucket:
                    bucket.append(entry)
    return used


def _refresh_placed_diameters() -> list:
    """Re-resolve every placement against the current library and re-run the checks.

    **Newly-colliding placements are DELETED, not flagged** (§3.5.4). A library edit
    exists *because* the old data was wrong, so the corrected data is authoritative and
    a placement derived from the error is invalid rather than merely suspect. This is
    the only path in the app by which an already-validated placement can become wrong
    retroactively — everything else is checked once, at drop time.
    """
    for instance_id, placed in _placements.items():
        rel = _placement_paths.get(instance_id, placed.part.filename)
        placed.tool_diameters = _resolve(rel, placed.part).diameters_by_tool_number(
            tool_library)

    removed = []
    # Re-check in placement order, so an earlier part keeps its spot and the later one
    # that now overlaps it is the one dropped — the same order the operator built in.
    kept: list = []
    for instance_id, placed in list(_placements.items()):
        result = check_placement(placed, kept, _rails(), config["advanced"])
        if result.collides:
            removed.append({
                "instance_id": instance_id,
                "filename": placed.part.filename,
                "slot": slot_label(placed.rail, placed.slot_inches),
                "message": result.message,
            })
            del _placements[instance_id]
            _placement_paths.pop(instance_id, None)
        else:
            kept.append(placed)
    return removed


@app.route("/api/tool-library")
def api_tool_library():
    return jsonify(_library_payload())


@app.route("/api/tool-library", methods=["POST"])
def api_tool_library_upsert():
    """Create or edit a tool.

    Two rules that are easy to get backwards:

    - **Duplicate default slots must be permitted, never refused** (§3.5.6). The
      declared library puts two tools in slot 2 and two in slot 4 on purpose — that
      collision *is* the motivating case, and refusing it would make the case
      undeclarable.
    - **`diameter_inches` is the widest point, not the nominal size** (§3.5.2).
      `.25 Bowl Bit` is 0.75 and `1/8 Roundover` is 0.3. Over-declaring costs a
      placement that would have fit; under-declaring puts the cutting edge somewhere
      the check called clear.
    """
    data = request.get_json(force=True) or {}
    original = normalize_code(data.get("original_code") or data.get("code"))
    existing = tool_library.get(original)

    slot = data.get("default_slot")
    if slot in ("", None):
        slot = None
    else:
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return jsonify({"error": "Default slot must be a pocket number, or blank"}), 400
        if not (1 <= slot <= _tool_capacity()):
            return jsonify({
                "error": f"Default slot must be between 1 and {_tool_capacity()}, or blank"
            }), 400

    payload = dict(data)
    payload["default_slot"] = slot
    # cam_descriptions is the seal's memory and is never editable from this form —
    # it grows only through a confirmed rename (§3.5.3).
    payload["cam_descriptions"] = (existing.cam_descriptions if existing else [])

    try:
        tool = LibraryTool.from_dict(payload)
        geometry_changed = bool(
            existing and (existing.diameter_inches != tool.diameter_inches
                          or existing.geometry_class != tool.geometry_class))
        if existing and existing.code != tool.code:
            tool_library.delete(existing.code)
        tool_library.upsert(tool)
    except ToolLibraryError as e:
        return jsonify({"error": str(e)}), 400
    tool_library.save()

    removed = _refresh_placed_diameters() if geometry_changed or existing is None else []
    return jsonify({
        "ok": True,
        "tool": tool.to_dict(),
        "library": _library_payload(),
        "changer": _changer_state(),
        "removed_placements": removed,
    })


@app.route("/api/tool-library/<code>", methods=["DELETE"])
def api_tool_library_delete(code: str):
    """Deleting a tool that is in use is refused, and the parts are named (§3.5.4)."""
    code = normalize_code(code)
    if code not in tool_library:
        return jsonify({"error": f"No tool with the code {code}"}), 404
    in_use = _codes_in_use().get(code, [])
    if in_use:
        names = ", ".join(f"{u['filename']} ({u['slot']})" for u in in_use)
        return jsonify({
            "error": "tool_in_use",
            "message": (f"{code} is used by {names}. Take those parts off the bed "
                        "first, or this job would have no way to cut them."),
            "in_use_by": in_use,
        }), 409
    tool_library.delete(code)
    tool_library.save()
    _pocket_overrides.pop(code, None)
    return jsonify({"ok": True, "library": _library_payload(), "changer": _changer_state()})


@app.route("/api/tool-library/merge", methods=["POST"])
def api_tool_library_merge():
    """Fold one entry into another. The loser's code is dropped, not kept as a second
    key — that would reintroduce many-keys-to-one-tool (§3.5.4)."""
    data = request.get_json(force=True) or {}
    try:
        survivor = tool_library.merge(data.get("survivor"), data.get("loser"))
    except ToolLibraryError as e:
        return jsonify({"error": str(e)}), 400
    tool_library.save()
    _pocket_overrides.pop(normalize_code(data.get("loser")), None)
    removed = _refresh_placed_diameters()
    return jsonify({"ok": True, "tool": survivor.to_dict(),
                    "library": _library_payload(), "changer": _changer_state(),
                    "removed_placements": removed})


@app.route("/api/tool-library/seal", methods=["POST"])
def api_tool_library_seal():
    """Answer the description seal (§3.5.3).

    *"Same tool, I renamed it"* adds the string to the set — a **set**, never a
    replacement, because replace-on-confirm thrashes forever after a rename and trains
    click-through. *"Different tool"* means one code is on two physical cutters and has
    to be fixed in CAM; the app records nothing and keeps blocking.
    """
    data = request.get_json(force=True) or {}
    code = normalize_code(data.get("library_code"))
    posted = str(data.get("posted") or "").strip()
    answer = (data.get("answer") or "").strip().lower()

    if code not in tool_library:
        return jsonify({"error": f"No tool with the code {code}"}), 404
    if answer != "rename":
        return jsonify({
            "ok": False,
            "message": (
                f"Two different cutters are sharing the code {code}. Fix it in Fusion or "
                "VCarve by giving one of them its own code, then re-post the file — the "
                "nest tool cannot tell them apart from here, and merging them would cut "
                "one with the wrong tool."
            ),
        }), 409
    if not posted:
        return jsonify({"error": "posted description required"}), 400

    tool_library.learn_description(code, posted)
    tool_library.save()
    return jsonify({"ok": True, "library": _library_payload(), "changer": _changer_state()})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    if not _placements:
        return jsonify({"error": "No parts placed"}), 400

    # The §3.4 validity gate. This is the *same* gate that has always been here
    # (`app.py`'s 422 and `static/job.js`'s disabled button) pointed at a sound signal:
    # every file tool resolved, every resolved tool in exactly one pocket, no pocket
    # holding two. Capacity is not a fourth rule — with eight pockets, rules 2 and 3
    # make a ninth tool unsatisfiable on their own.
    state = _changer_state()
    if not state["valid"]:
        return jsonify({
            "error": "changer_map_invalid",
            "message": " ".join(m["text"] for m in state["messages"]),
            "changer": state,
        }), 422

    data = request.get_json(force=True) or {}
    job_name = _job_name(data)
    safe_z = _compute_job_safe_z()
    settings = {**config, "job_name": job_name, "job_safe_z": safe_z}

    try:
        gcode = generate_master_gcode(
            list(_placements.values()), settings, _identity_map(state))
    except Exception as e:
        return jsonify({"error": f"Generation failed: {e}"}), 500

    # Validate before anything reaches the disk. The validator re-derives the
    # file's modal state from the emitted text rather than reusing the
    # generator's, so it can catch the generator being wrong. A file with a hard
    # finding is never written — the operator cannot run what does not exist.
    findings = validate_gcode(gcode, config["advanced"])
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    if errors:
        return jsonify({
            "error": "validation_failed",
            "message": (
                f"Generated G-code failed {len(errors)} safety check(s) and was not "
                "written. This is a defect in the nest tool, not in your layout — "
                "send the details below to whoever maintains it."
            ),
            "findings": [str(f) for f in errors],
        }), 422

    out = _output_dir()
    nc_path = out / f"{job_name}.nc"
    pdf_path = out / f"{job_name}.pdf"

    nc_path.write_text(gcode, encoding="utf-8")
    if findings:
        (out / f"{job_name}_validation.txt").write_text(
            format_findings(findings), encoding="utf-8")
    setup_path = out / f"{job_name}_setup.txt"
    setup_path.write_text(_setup_sheet_text(job_name, state, safe_z), encoding="utf-8")

    try:
        meta, parts, geom = _build_pdf_model(job_name, settings, gcode, state)
        generate_layout_pdf(pdf_path, meta, parts, geom)
    except Exception as e:
        return jsonify({"error": f"PDF generation failed: {e}"}), 500

    return jsonify({"ok": True, "job_name": job_name,
                    "nc_path": str(nc_path), "pdf_path": str(pdf_path),
                    "setup_path": str(setup_path),
                    "warnings": [str(f) for f in warnings]})


@app.route("/api/audit")
def api_audit():
    """Audit every library file against best practices, write CSVs, and return the rows."""
    root = _library_root()
    if not os.path.isdir(root):
        return jsonify({"error": f"Library path does not exist: {root}"}), 400

    thresholds = build_thresholds(config)
    file_rows, tool_rows, toolpath_rows = scan_library(root, tool_library, thresholds)

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


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    # Loopback only. CNC Nest is a single-user desktop tool whose whole state is
    # per-process in-memory globals; it has no reason to accept a connection from
    # another machine, and binding wide is what raised the Windows Firewall prompt
    # the operator was being trained to click through (issue #2).
    app.run(host="127.0.0.1", port=5001, debug=debug)
