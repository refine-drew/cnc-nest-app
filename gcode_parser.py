import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

COORD_PATTERN = re.compile(r"([XYZ])\s*([+-]?\d*\.?\d+)")
HEADER_SIZE_PATTERN = re.compile(
    r"\(\s*X\s*=\s*([0-9.+-]+)\s*,\s*Y\s*=\s*([0-9.+-]+)\s*,\s*Z\s*=\s*([0-9.+-]+)\s*\)",
    re.IGNORECASE,
)
PART_SIZE_PATTERN = re.compile(r"\(\s*PART SIZE X\s*=\s*([0-9.+-]+)\s*Y\s*=\s*([0-9.+-]+)\s*\)", re.IGNORECASE)
TOOL_HEADER_PATTERN = re.compile(r"\(\s*(T\d+)\s*=\s*(.+?)\s*\)", re.IGNORECASE)
INLINE_TOOL_PATTERN = re.compile(r"\(\s*Tool:\s*([^\{\)]+)\{([0-9.]+)\s*inches\}\)", re.IGNORECASE)
TOOL_CHANGE_PATTERN = re.compile(r"\bT(\d+)\s+M06\b", re.IGNORECASE)
G43_Z_PATTERN = re.compile(r"\bG43\b.*\bZ([+-]?\d*\.?\d+)", re.IGNORECASE)
CUTTING_MOVE_PATTERN = re.compile(r"\bG0?[123]\b", re.IGNORECASE)
MACHINE_COORD_PATTERN = re.compile(r"\bG53\b", re.IGNORECASE)

OVERTRAVEL_TOLERANCE_MM = 0.762  # 0.03 inches


@dataclass
class ZValidation:
    status: str  # 'ok', 'warning', 'blocked'
    messages: List[str] = field(default_factory=list)


@dataclass
class GcodePass:
    pass_index: int
    tool_number: str
    lines: List[str] = field(default_factory=list)


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

    segments = extract_file_segments(passes)

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
    """Extract tool diameter in inches from description text, trying multiple formats."""
    # Pattern 1: {N inch...} — curly brace notation, singular or plural
    m = re.search(r'\{([\d.]+)\s+inch', text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # Pattern 2: N inch... — number before 'inch' without braces (e.g. ".5 inches Dia")
    m = re.search(r'([\d.]+)\s+inch', text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # Pattern 3: bare decimal number only (e.g. ROUNDOVER 0.125); integer-only values ignored
    m = re.search(r'\b(\d+\.\d+)\b', text)
    if m:
        return float(m.group(1))
    return None


def extract_tools(lines: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    tools: Dict[str, Dict[str, Optional[float]]] = {}
    for line in lines:
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
    """Returns (min_cutting_z, max_cutting_z, safe_z_from_g43)."""
    min_z = float("inf")
    max_z = float("-inf")
    found_z = False
    safe_z: Optional[float] = None

    for line in lines:
        if line.startswith("("):
            continue

        g43_match = G43_Z_PATTERN.search(line)
        if g43_match:
            safe_z = float(g43_match.group(1))
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
                f"This file uses top-of-material Z reference (min Z = {min_z:.3f}mm). "
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
                f"Cut depth ({min_z:.3f}mm) exceeds material thickness ({material_thickness:.3f}mm) "
                f"by more than 0.03 inches. This would cut deeply into the spoilboard "
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
            f"Deepest cut ({max_cut_depth_from_top:.3f}mm) reaches less than half the "
            f"material thickness ({material_thickness:.3f}mm). If this part should cut "
            "through, verify the toolpath in VCarve. Dadoes, pockets, and engraving "
            "are valid reasons for shallow cuts."
        )
        status = "warning"

    # Check 4: Safe Z too low to clear material
    if safe_z is not None and safe_z < material_thickness:
        messages.append(
            f"Safe Z height ({safe_z:.3f}mm) is below the material top "
            f"({material_thickness:.3f}mm). Rapid moves would crash into the material. "
            f"Increase the safe Z setting in VCarve to at least {material_thickness + 6.35:.3f}mm "
            "and re-export."
        )
        return ZValidation(status="blocked", messages=messages)

    return ZValidation(status=status, messages=messages)


def extract_file_segments(passes: List[GcodePass]) -> List[dict]:
    """
    Walk tool passes and extract lateral moves as file-coordinate segments.
    Each dict: {x1, y1, x2, y2, cutting}.
    cutting=True on G01/G02/G03 moves where current Z < 0 (through/overcut depth).
    Z-only moves and G53 machine-coord lines are skipped.
    """
    segments: List[dict] = []
    rapid_pat = re.compile(r"\bG0?0\b", re.IGNORECASE)
    move_pat  = re.compile(r"\bG0?[0-3]\b", re.IGNORECASE)

    for pass_ in passes:
        cur_x, cur_y, cur_z = 0.0, 0.0, 0.0
        for line in pass_.lines:
            if line.startswith("("):
                continue
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
                cutting = (not is_rapid) and (new_z < 0)
                segments.append({
                    "x1": cur_x, "y1": cur_y,
                    "x2": new_x, "y2": new_y,
                    "cutting": cutting,
                })
            cur_x, cur_y, cur_z = new_x, new_y, new_z

    return segments


def extract_passes(lines: List[str]) -> List[GcodePass]:
    """Split file into ordered tool passes at each T# M06 tool change."""
    passes: List[GcodePass] = []
    current_pass: Optional[GcodePass] = None
    pass_index = 0

    for line in lines:
        tool_match = TOOL_CHANGE_PATTERN.search(line)
        if tool_match:
            tool_number = f"T{tool_match.group(1).upper()}"
            current_pass = GcodePass(
                pass_index=pass_index,
                tool_number=tool_number,
                lines=[line],
            )
            passes.append(current_pass)
            pass_index += 1
        elif current_pass is not None:
            current_pass.lines.append(line)

    return passes
