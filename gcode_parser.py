import re
from dataclasses import dataclass, field
from math import atan2, ceil, cos, hypot, pi, radians, sin
from typing import Dict, List, Optional, Tuple

COORD_PATTERN = re.compile(r"([XYZ])\s*([+-]?\d*\.?\d+)")
ARC_PARAM_PATTERN = re.compile(r"([IJR])\s*([+-]?\d*\.?\d+)", re.IGNORECASE)
HEADER_SIZE_PATTERN = re.compile(
    r"\(\s*X\s*=\s*([0-9.+-]+)\s*,\s*Y\s*=\s*([0-9.+-]+)\s*,\s*Z\s*=\s*([0-9.+-]+)\s*\)",
    re.IGNORECASE,
)
PART_SIZE_PATTERN = re.compile(r"\(\s*PART SIZE X\s*=\s*([0-9.+-]+)\s*Y\s*=\s*([0-9.+-]+)\s*\)", re.IGNORECASE)
TOOL_HEADER_PATTERN = re.compile(r"\(\s*(T\d+)\s*=\s*(.+?)\s*\)", re.IGNORECASE)
INLINE_TOOL_PATTERN = re.compile(r"\(\s*Tool:\s*([^\{\)]+)\{([0-9.]+)\s*inches\}\)", re.IGNORECASE)
# Fusion's tool list, written by `post/syntec 4.cps` writeProgramHeader():
#   (T1 D=12.7 CR=6.35 - ZMIN=14.605 - BALL END MILL)
#   (T3 D=12.7 CR=0. TAPER=45DEG - ZMIN=18.542 - CHAMFER MILL)
# TAPER appears only for tapered tools, and ZMIN only when the job is 3D, so both
# are optional. `D`/`CR` are in the file's own units. The post writes `T#` rather
# than a name unconditionally — its `toolAsName` branch is never taken, because no
# such property is declared.
FUSION_TOOL_HEADER_PATTERN = re.compile(
    r"\(\s*(T\d+)\s+D=([0-9.+-]+)\s+CR=([0-9.+-]+)"
    r"(?:\s+TAPER=([0-9.+-]+)\s*DEG)?"
    r"(?:\s*-\s*ZMIN=([0-9.+-]+))?"
    r"\s*-\s*(.+?)\s*\)",
    re.IGNORECASE,
)
# The identity comment the REFINE post emits (spec §6.2.1), written by
# `writeToolIdentity` in `post/syntec 4.cps`:
#   (TOOLID T2 VENDOR=AMANA PRODUCT=46170-K FLUTES=3)
#   (TOOLDESC T2 12 DOWNCUT SPIRAL)
# `TOOLID` leads so the line can be found without guessing at comment shapes. The
# identity is VENDOR+PRODUCT; the `T#` only ties the line to the header above it and
# is not part of the identity.
TOOLID_PATTERN = re.compile(r"\(\s*TOOLID\s+(T\d+)\s*(.*?)\s*\)", re.IGNORECASE)
TOOLDESC_PATTERN = re.compile(r"\(\s*TOOLDESC\s+(T\d+)\s+(.+?)\s*\)", re.IGNORECASE)
TOOLID_FIELD_PATTERN = re.compile(r"\b([A-Z_]+)=(\S*)", re.IGNORECASE)

# First units word in the body wins. The rest of the app is millimetres throughout
# (see CLAUDE.md), and G71 is what the Syntec post emits, so metric is the default
# when nothing says otherwise.
UNITS_WORD_PATTERN = re.compile(r"\bG(70|71|20|21)\b")
_INCH_UNITS_WORDS = frozenset({"70", "20"})
TOOL_CHANGE_PATTERN = re.compile(r"\bT(\d+)\s+M06\b", re.IGNORECASE)
G43_Z_PATTERN = re.compile(r"\bG43\b.*\bZ([+-]?\d*\.?\d+)", re.IGNORECASE)
CUTTING_MOVE_PATTERN = re.compile(r"\bG0?[123]\b", re.IGNORECASE)
MACHINE_COORD_PATTERN = re.compile(r"\bG53\b", re.IGNORECASE)
BARE_COMMENT_PATTERN = re.compile(r"^\(([^)]*)\)$")

# Comments that are file metadata rather than a toolpath name. A toolpath name
# is recognised only by position — the bare comment above a tool change — so
# these have to be ruled out explicitly or a header line becomes an "operation".
_NON_OPERATION_COMMENTS = (
    HEADER_SIZE_PATTERN, PART_SIZE_PATTERN, TOOL_HEADER_PATTERN, INLINE_TOOL_PATTERN,
    FUSION_TOOL_HEADER_PATTERN, TOOLID_PATTERN, TOOLDESC_PATTERN,
)

OVERTRAVEL_TOLERANCE_MM = 0.762  # 0.03 inches


def operation_name_in_comment(line: str) -> str:
    """Return the toolpath name in a bare comment line, or '' if it isn't one."""
    m = BARE_COMMENT_PATTERN.match(line.strip())
    if not m or any(p.search(line) for p in _NON_OPERATION_COMMENTS):
        return ""
    return m.group(1).strip()


# A program stop sits between the toolpath name and the tool change on every
# pass after the first ("(TABLE STIFF)" / "N2530 M01" / "N2535 T4 M06"). It
# commands no motion, so it must not break the name's association with the
# tool change the way a real code line does.
_PROGRAM_STOP_ONLY = re.compile(r"^(?:N\d+\s+)?M0?[01]\s*$", re.IGNORECASE)


@dataclass
class ZValidation:
    status: str  # 'ok', 'warning', 'blocked'
    messages: List[str] = field(default_factory=list)


@dataclass
class GcodePass:
    pass_index: int
    tool_number: str
    lines: List[str] = field(default_factory=list)
    # Toolpath name, e.g. "TABLE OUTSIDE PROFILE ADAPTIVE". Both posts write it
    # as a bare comment on the line above the tool change, which puts it outside
    # the pass — see extract_passes. Later operations within a pass carry theirs
    # inline, so without this the first operation of every pass is the only one
    # that reaches the master file unnamed.
    operation_name: str = ""


@dataclass
class GcodePart:
    filename: str
    vcarve_x_span: float       # VCarve X = along rail = machine Y extent
    vcarve_y_span: float       # VCarve Y = across bed = machine X extent
    material_thickness: Optional[float]
    tools: Dict[str, Dict[str, Optional[float]]]
    min_vx: float              # VCarve X min
    max_vx: float
    min_vy: float              # VCarve Y min
    max_vy: float
    raw_lines: List[str]
    min_z: Optional[float] = None
    max_z: Optional[float] = None
    safe_z: Optional[float] = None
    z_validation: ZValidation = field(default_factory=lambda: ZValidation(status="ok"))
    passes: List[GcodePass] = field(default_factory=list)
    segments: List[dict] = field(default_factory=list)
    runtime_seconds: float = 0.0


def parse_vcarve_text(text: str, filename: str = "") -> GcodePart:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    vcarve_x_span, vcarve_y_span, material_thickness = extract_blank_and_material(lines)
    tools = extract_tools(lines)
    min_vx, max_vx, min_vy, max_vy = scan_coordinates(lines)
    min_z, max_z, safe_z = scan_z_values(lines)
    passes = extract_passes(lines)
    z_validation = validate_z(min_z, safe_z, material_thickness)

    if min_vx is None or max_vx is None or min_vy is None or max_vy is None:
        min_vx, min_vy = 0.0, 0.0
        max_vx, max_vy = vcarve_x_span, vcarve_y_span

    segments = extract_file_segments(passes, material_thickness)

    from runtime_estimator import estimate_passes_runtime
    runtime_seconds = estimate_passes_runtime(passes, tool_change_seconds=0.0)["seconds"]

    return GcodePart(
        filename=filename,
        vcarve_x_span=vcarve_x_span,
        vcarve_y_span=vcarve_y_span,
        material_thickness=material_thickness,
        tools=tools,
        min_vx=min_vx,
        max_vx=max_vx,
        min_vy=min_vy,
        max_vy=max_vy,
        raw_lines=lines,
        min_z=min_z,
        max_z=max_z,
        safe_z=safe_z,
        z_validation=z_validation,
        passes=passes,
        segments=segments,
        runtime_seconds=runtime_seconds,
    )


def extract_blank_and_material(lines: List[str]) -> Tuple[float, float, Optional[float]]:
    vcarve_x_span = vcarve_y_span = 0.0
    material_thickness: Optional[float] = None
    for i, line in enumerate(lines):
        if "( Material Size" in line or "(Material Size" in line:
            if i + 1 < len(lines):
                size_line = lines[i + 1]
                size_match = HEADER_SIZE_PATTERN.search(size_line)
                if size_match:
                    vcarve_x_span = float(size_match.group(1))
                    vcarve_y_span = float(size_match.group(2))
                    material_thickness = float(size_match.group(3))
                    return vcarve_x_span, vcarve_y_span, material_thickness

    for line in lines:
        part_match = PART_SIZE_PATTERN.search(line)
        if part_match:
            vcarve_x_span = float(part_match.group(1))
            vcarve_y_span = float(part_match.group(2))
            break

    return vcarve_x_span, vcarve_y_span, material_thickness


def _extract_diameter(text: str) -> Optional[float]:
    """Tool diameter in inches from a VCarve description, or None.

    **Display only.** The identity library is the sole diameter authority (spec §3.5.2):
    it declares the tool's *maximum cutting diameter*, which no file supplies and which
    differs from a posted nominal size by design on every profile bit.

    The bare-decimal fallback that used to sit here — any `\\d+\\.\\d+` in the text — is
    **retired and must not come back**. With identity now carried by a shop code typed
    into the VCarve tool name, it would read `RK-004` as a 0.04" cutter: a 25×
    under-inflation of the X envelope and the tool-radius collision check, which is the
    crash direction.
    """
    # {N inch...} — curly brace notation, singular or plural
    m = re.search(r'\{([\d.]+)\s+inch', text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # N inch... — number before 'inch' without braces (e.g. ".5 inches Dia")
    m = re.search(r'([\d.]+)\s+inch', text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def file_is_inch(lines: List[str]) -> bool:
    """True if the program declares inch units. Comment lines are skipped so a stray
    'G20' in free text cannot rescale a tool."""
    for line in lines:
        if line.lstrip().startswith("("):
            continue
        m = UNITS_WORD_PATTERN.search(line)
        if m:
            return m.group(1) in _INCH_UNITS_WORDS
    return False


def _fusion_tool_entry(match: "re.Match", to_inches: float) -> Dict[str, Optional[float]]:
    """Build a tool entry from a Fusion header match.

    `description` is assembled from the *stable* fields only. ZMIN is deliberately
    left out even though it is right there in the line: it is the job's Z range, not
    a property of the cutter, so the same physical tool posts `ZMIN=0.` in one file
    and `ZMIN=-19.05` in another. Feeding that into `app._tool_compatibility`, which
    compares description strings per `T#`, would flag one cutter as a conflict with
    itself and block Generate on two perfectly compatible files.
    """
    diameter, corner_radius, taper, _zmin, type_name = match.group(2, 3, 4, 5, 6)

    description = f"{type_name} D={diameter} CR={corner_radius}"
    if taper is not None:
        description += f" TAPER={taper}DEG"

    return {
        "description": description,
        # Converted here rather than at the call site: `D=12.7` read as inches is a
        # 25.4x under-inflation of the X envelope and tool-radius checks, which is
        # the crash direction.
        "diameter_inches": float(diameter) * to_inches,
        "corner_radius_inches": float(corner_radius) * to_inches,
        "taper_degrees": float(taper) if taper is not None else None,
        "tool_type": type_name,
    }


def _toolid_fields(body: str) -> Dict[str, object]:
    """Parse the KEY=value pairs of a TOOLID comment.

    The post emits a blank field as `VENDOR=` rather than omitting it, and that
    distinction is the whole point: an empty value says the Fusion library entry is
    blank, which the operator can go and fill in, while a *missing* key says only
    that the file predates the identity comment. Preserve it — an absent key leaves
    nothing in the dict, so `.get("vendor")` returns None, whereas a blank one
    returns "".
    """
    fields: Dict[str, object] = {}
    for key, value in TOOLID_FIELD_PATTERN.findall(body):
        name = key.upper()
        if name == "VENDOR":
            fields["vendor"] = value
        elif name == "PRODUCT":
            fields["product_id"] = value
        elif name == "FLUTES":
            fields["flutes"] = int(value) if value.isdigit() else None
    return fields


def extract_tools(lines: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    tools: Dict[str, Dict[str, Optional[float]]] = {}
    to_inches = 1.0 if file_is_inch(lines) else 1.0 / 25.4

    for line in lines:
        # Checked before the VCarve patterns, and `continue`s on a hit: a Fusion
        # description must never reach _extract_diameter, whose bare-decimal fallback
        # would read the millimetre `12.7` in `D=12.7` as 12.7 inches.
        fusion_match = FUSION_TOOL_HEADER_PATTERN.search(line)
        if fusion_match:
            entry = tools.setdefault(fusion_match.group(1).upper(), {})
            entry.update(_fusion_tool_entry(fusion_match, to_inches))
            continue

        toolid_match = TOOLID_PATTERN.search(line)
        if toolid_match:
            entry = tools.setdefault(toolid_match.group(1).upper(), {})
            entry.update(_toolid_fields(toolid_match.group(2)))
            continue

        tooldesc_match = TOOLDESC_PATTERN.search(line)
        if tooldesc_match:
            entry = tools.setdefault(tooldesc_match.group(1).upper(), {})
            entry["cam_description"] = tooldesc_match.group(2).strip()
            continue

        header_match = TOOL_HEADER_PATTERN.search(line)
        if header_match:
            tool_number = header_match.group(1).upper()
            description = header_match.group(2).strip()
            tools[tool_number] = {"description": description, "diameter_inches": _extract_diameter(description)}
            continue

        inline_match = INLINE_TOOL_PATTERN.search(line)
        if inline_match:
            description = inline_match.group(1).strip()
            diameter = float(inline_match.group(2))
            maybe_tool = extract_tool_number_from_line(line)
            if maybe_tool:
                tool_number = maybe_tool.upper()
                tools.setdefault(tool_number, {})
                tools[tool_number].update({"description": description, "diameter_inches": diameter})

    return tools


def extract_tool_number_from_line(line: str) -> Optional[str]:
    match = re.search(r"\b(T\d+)\b", line, re.IGNORECASE)
    return match.group(1).upper() if match else None


def scan_coordinates(lines: List[str]) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Track min/max X/Y using modal coordinates — carries last known value forward."""
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    found = False
    cur_x: Optional[float] = None
    cur_y: Optional[float] = None

    for line in lines:
        if line.startswith("("):
            continue
        if MACHINE_COORD_PATTERN.search(line):
            continue

        coords = COORD_PATTERN.findall(line)
        if not coords:
            continue

        for axis, value in coords:
            val = float(value)
            if axis == "X":
                cur_x = val
            elif axis == "Y":
                cur_y = val

        if cur_x is not None and cur_y is not None:
            found = True
            min_x = min(min_x, cur_x)
            max_x = max(max_x, cur_x)
            min_y = min(min_y, cur_y)
            max_y = max(max_y, cur_y)

    if not found:
        return None, None, None, None

    return min_x, max_x, min_y, max_y


def scan_z_values(lines: List[str]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Returns (min_cutting_z, max_cutting_z, safe_z_from_g43).

    Fusion writes a `G43 Z<retract> H<n>` per operation, and the retracts differ
    between tools — 18G.nc has Z34.29 for T2 and Z57.15 for T1, because T1 cuts a
    feature standing above the material top. Taking any single one of them (the
    first, or the last) yields a clearance that is below what another tool in the
    same file needs, so the highest one is the only safe reading (#22).
    """
    min_z = float("inf")
    max_z = float("-inf")
    found_z = False
    safe_z: Optional[float] = None

    for line in lines:
        if line.startswith("("):
            continue

        g43_match = G43_Z_PATTERN.search(line)
        if g43_match:
            retract = float(g43_match.group(1))
            safe_z = retract if safe_z is None else max(safe_z, retract)
            continue

        if MACHINE_COORD_PATTERN.search(line):
            continue

        if not CUTTING_MOVE_PATTERN.search(line):
            continue

        z_match = re.search(r"Z([+-]?\d*\.?\d+)", line)
        if z_match:
            z_val = float(z_match.group(1))
            found_z = True
            min_z = min(min_z, z_val)
            max_z = max(max_z, z_val)

    if not found_z:
        return None, None, safe_z
    return min_z, max_z, safe_z


def validate_z(
    min_z: Optional[float],
    safe_z: Optional[float],
    material_thickness: Optional[float],
) -> ZValidation:
    if material_thickness is None:
        return ZValidation(
            status="blocked",
            messages=[
                "This file is missing the VCarve Material Size header. The app cannot "
                "determine the blank dimensions or thickness. Re-export through the "
                "current VCarve post-processor — older post-processors did not include "
                "this header."
            ],
        )

    if min_z is None:
        return ZValidation(status="ok", messages=[])

    # Check 1: Wrong Z reference convention (top-of-material files have large negative Z)
    if min_z < -OVERTRAVEL_TOLERANCE_MM:
        return ZValidation(
            status="blocked",
            messages=[
                f"This file uses top-of-material Z reference (min Z = {min_z / 25.4:.3f}\"). "
                "The Smartshop 2 expects spoilboard Z reference. "
                "Re-export through VCarve with 'Z origin = top of spoilboard' selected. "
                "Running this file as-is would crash the cutter into the spoilboard."
            ],
        )

    # Check 2: Cut too deep into spoilboard
    if abs(min_z) > material_thickness + OVERTRAVEL_TOLERANCE_MM:
        return ZValidation(
            status="blocked",
            messages=[
                f"Cut depth ({min_z / 25.4:.3f}\") exceeds material thickness ({material_thickness / 25.4:.3f}\") "
                f"by more than 0.03\". This would cut deeply into the spoilboard "
                "and could damage the machine. Verify Z reference and material thickness "
                "in VCarve, then re-export."
            ],
        )

    messages = []
    status = "ok"

    # Check 3: Cut too shallow (warning — valid for dadoes, pockets, engraving)
    max_cut_depth_from_top = material_thickness - min_z
    if max_cut_depth_from_top < material_thickness * 0.5:
        messages.append(
            f"Deepest cut ({max_cut_depth_from_top / 25.4:.3f}\") reaches less than half the "
            f"material thickness ({material_thickness / 25.4:.3f}\"). If this part should cut "
            "through, verify the toolpath in VCarve. Dadoes, pockets, and engraving "
            "are valid reasons for shallow cuts."
        )
        status = "warning"

    # Check 4: Safe Z too low to clear material
    if safe_z is not None and safe_z < material_thickness:
        messages.append(
            f"Safe Z height ({safe_z / 25.4:.3f}\") is below the material top "
            f"({material_thickness / 25.4:.3f}\"). Rapid moves would crash into the material. "
            f"Increase the safe Z setting in VCarve to at least {material_thickness / 25.4 + 0.25:.3f}\" "
            "and re-export."
        )
        return ZValidation(status="blocked", messages=messages)

    return ZValidation(status=status, messages=messages)


def _arc_points(
    x0: float, y0: float, x1: float, y1: float,
    *, r: Optional[float] = None,
    i: Optional[float] = None, j: Optional[float] = None,
    clockwise: bool,
) -> List[Tuple[float, float]]:
    """
    Flatten a G02/G03 arc into interpolated points, EXCLUDING the start and
    INCLUDING the exact end. Returns [(x1, y1)] (a single straight chord) for
    degenerate inputs. Center is taken from I/J when supplied, else derived from
    the chord + radius R. Mirrors the arc geometry in
    runtime_estimator._arc_length (center/radius/sweep via atan2).
    """
    if i is not None or j is not None:
        # I/J form: center is an offset from the start point.
        cx, cy = x0 + (i or 0.0), y0 + (j or 0.0)
        radius = hypot(cx - x0, cy - y0)
        if radius == 0:
            return [(x1, y1)]
    elif r is not None:
        radius = abs(r)
        d = hypot(x1 - x0, y1 - y0)
        # R-format can't express a full circle (d==0), and a zero radius is a no-op.
        if d == 0 or radius == 0:
            return [(x1, y1)]
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        # Unit perpendicular to the chord.
        px, py = -(y1 - y0) / d, (x1 - x0) / d
        h = (max(0.0, radius * radius - (d / 2.0) ** 2)) ** 0.5
        cand = [(mx + px * h, my + py * h), (mx - px * h, my - py * h)]
        # Pick the center whose swept angle matches R's sign: R>0 -> minor arc
        # (sweep <= pi), R<0 -> major arc (sweep > pi).
        def _sweep(c):
            a0 = atan2(y0 - c[1], x0 - c[0])
            a1 = atan2(y1 - c[1], x1 - c[0])
            s = (a0 - a1) if clockwise else (a1 - a0)
            while s <= 1e-9:
                s += 2 * pi
            return s
        # R<0 -> major arc (largest sweep); R>0 -> minor arc (smallest sweep).
        cx, cy = max(cand, key=_sweep) if r < 0 else min(cand, key=_sweep)
    else:
        return [(x1, y1)]

    a0 = atan2(y0 - cy, x0 - cx)
    a1 = atan2(y1 - cy, x1 - cx)
    sweep = (a0 - a1) if clockwise else (a1 - a0)
    # Normalize into (0, 2*pi]; a coincident start/end (I/J) is a full circle.
    while sweep <= 1e-9:
        sweep += 2 * pi

    n = min(64, max(2, ceil(sweep / radians(6))))
    direction = -1.0 if clockwise else 1.0
    pts: List[Tuple[float, float]] = []
    for k in range(1, n + 1):
        theta = a0 + direction * sweep * (k / n)
        pts.append((cx + radius * cos(theta), cy + radius * sin(theta)))
    # Force the exact endpoint to avoid rounding drift.
    pts[-1] = (x1, y1)
    return pts


def extract_file_segments(passes: List[GcodePass], material_thickness: Optional[float] = None) -> List[dict]:
    """
    Walk tool passes and extract lateral moves as file-coordinate segments.
    Each dict: {x1, y1, x2, y2, cutting}.
    cutting=True on G01/G02/G03 moves where Z is below the material surface.
    Z-only moves and G53 machine-coord lines are skipped.
    """
    segments: List[dict] = []
    rapid_pat = re.compile(r"\bG0?0\b", re.IGNORECASE)
    move_pat  = re.compile(r"\bG0?[0-3]\b", re.IGNORECASE)
    g2_pat = re.compile(r"\bG0?2\b", re.IGNORECASE)
    g3_pat = re.compile(r"\bG0?3\b", re.IGNORECASE)
    plane_pat = re.compile(r"\bG1([789])\b", re.IGNORECASE)

    for pass_ in passes:
        cur_x, cur_y, cur_z = 0.0, 0.0, 0.0
        plane = "G17"
        for line in pass_.lines:
            if line.startswith("("):
                continue
            # Track the modal plane before the motion filter: a plane word can
            # arrive on a line of its own (VCarve restores G17 that way).
            pm = plane_pat.search(line)
            if pm:
                plane = "G1" + pm.group(1)
            if MACHINE_COORD_PATTERN.search(line):
                continue
            if not move_pat.search(line):
                continue
            is_rapid = bool(rapid_pat.search(line))
            new_x, new_y, new_z = cur_x, cur_y, cur_z
            for axis, val in COORD_PATTERN.findall(line):
                a = axis.upper()
                if a == "X":
                    new_x = float(val)
                elif a == "Y":
                    new_y = float(val)
                elif a == "Z":
                    new_z = float(val)
            if new_x != cur_x or new_y != cur_y:
                cutting = (not is_rapid) and (new_z < (material_thickness if material_thickness else 0))
                is_g2 = bool(g2_pat.search(line))
                is_g3 = bool(g3_pat.search(line))
                arc_params: Dict[str, float] = {}
                if is_g2 or is_g3:
                    for p, val in ARC_PARAM_PATTERN.findall(line):
                        arc_params[p.upper()] = float(val)
                # Only G17 arcs curve in XY. A G18/G19 ramp arc curves into Z;
                # its footprint is the straight lateral line, and flattening it
                # as an XY arc invents a bulge the cutter never makes (I/J there
                # are X/Y-vs-Z centre offsets, not an XY centre).
                if (is_g2 or is_g3) and arc_params and plane == "G17":
                    points = _arc_points(
                        cur_x, cur_y, new_x, new_y,
                        r=arc_params.get("R"),
                        i=arc_params.get("I"), j=arc_params.get("J"),
                        clockwise=is_g2,
                    )
                    px, py = cur_x, cur_y
                    for qx, qy in points:
                        segments.append({
                            "x1": px, "y1": py,
                            "x2": qx, "y2": qy,
                            "cutting": cutting,
                        })
                        px, py = qx, qy
                else:
                    segments.append({
                        "x1": cur_x, "y1": cur_y,
                        "x2": new_x, "y2": new_y,
                        "cutting": cutting,
                    })
            cur_x, cur_y, cur_z = new_x, new_y, new_z

    return segments


def extract_passes(lines: List[str]) -> List[GcodePass]:
    """
    Split file into ordered tool passes at each T# M06 tool change.

    The toolpath name sits on the line *above* the tool change, so it falls
    outside the pass it names. Carry it across as `operation_name` rather than
    letting it drop: it is the only label the operator has for the first
    operation of each pass in the merged master file.
    """
    passes: List[GcodePass] = []
    current_pass: Optional[GcodePass] = None
    pass_index = 0
    pending_name = ""

    for line in lines:
        tool_match = TOOL_CHANGE_PATTERN.search(line)
        if tool_match:
            tool_number = f"T{tool_match.group(1).upper()}"
            current_pass = GcodePass(
                pass_index=pass_index,
                tool_number=tool_number,
                lines=[line],
                operation_name=pending_name,
            )
            passes.append(current_pass)
            pass_index += 1
            pending_name = ""
        elif current_pass is not None:
            current_pass.lines.append(line)

        # Track the comment standing immediately above the next tool change.
        # Blank lines and the optional stop don't break the association; any
        # real code line does.
        s = line.strip()
        if tool_match or not s or _PROGRAM_STOP_ONLY.match(s):
            continue
        pending_name = operation_name_in_comment(line)

    return passes
