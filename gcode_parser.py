import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

COORD_PATTERN = re.compile(r"([XYZ])\s*([+-]?\d*\.?\d+)")
HEADER_SIZE_PATTERN = re.compile(
    r"\(\s*X\s*=\s*([0-9.+-]+)\s*,\s*Y\s*=\s*([0-9.+-]+)\s*,\s*Z\s*=\s*([0-9.+-]+)\s*\)",
    re.IGNORECASE,
)
PART_SIZE_PATTERN = re.compile(r"\(\s*PART SIZE X\s*=\s*([0-9.+-]+)\s*Y\s*=\s*([0-9.+-]+)\s*\)", re.IGNORECASE)
TOOL_HEADER_PATTERN = re.compile(
    r"\(\s*(T\d+)\s*=.*\{([0-9.]+)\s*inches\}\)", re.IGNORECASE
)
INLINE_TOOL_PATTERN = re.compile(r"\(\s*Tool:\s*([^\{\)]+)\{([0-9.]+)\s*inches\}\)", re.IGNORECASE)

@dataclass
class GcodePart:
    filename: str
    blank_width: float
    blank_height: float
    material_thickness: Optional[float]
    tools: Dict[str, Dict[str, Optional[float]]]
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    raw_lines: List[str]


def parse_vcarve_text(text: str, filename: str = "") -> GcodePart:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    blank_width, blank_height, material_thickness = extract_blank_and_material(lines)
    tools = extract_tools(lines)
    min_x, max_x, min_y, max_y = scan_coordinates(lines)

    if min_x is None or max_x is None or min_y is None or max_y is None:
        min_x, min_y = 0.0, 0.0
        max_x, max_y = blank_width, blank_height

    return GcodePart(
        filename=filename,
        blank_width=blank_width,
        blank_height=blank_height,
        material_thickness=material_thickness,
        tools=tools,
        min_x=min_x,
        max_x=max_x,
        min_y=min_y,
        max_y=max_y,
        raw_lines=lines,
    )


def extract_blank_and_material(lines: List[str]) -> Tuple[float, float, Optional[float]]:
    blank_width = blank_height = 0.0
    material_thickness: Optional[float] = None
    for i, line in enumerate(lines):
        if "( Material Size" in line or "(Material Size" in line:
            if i + 1 < len(lines):
                size_line = lines[i + 1]
                size_match = HEADER_SIZE_PATTERN.search(size_line)
                if size_match:
                    blank_width = float(size_match.group(1))
                    blank_height = float(size_match.group(2))
                    material_thickness = float(size_match.group(3))
                    return blank_width, blank_height, material_thickness

    for line in lines:
        part_match = PART_SIZE_PATTERN.search(line)
        if part_match:
            blank_width = float(part_match.group(1))
            blank_height = float(part_match.group(2))
            break

    return blank_width, blank_height, material_thickness


def extract_tools(lines: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    tools: Dict[str, Dict[str, Optional[float]]] = {}
    for line in lines:
        header_match = TOOL_HEADER_PATTERN.search(line)
        if header_match:
            tool_number = header_match.group(1).upper()
            diameter = float(header_match.group(2))
            tools[tool_number] = {"description": line.strip("()"), "diameter_inches": diameter}
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
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    found = False

    for line in lines:
        if line.startswith("("):
            continue
        coords = COORD_PATTERN.findall(line)
        if not coords:
            continue

        x_val = y_val = None
        for axis, value in coords:
            val = float(value)
            if axis == "X":
                x_val = val
            elif axis == "Y":
                y_val = val

        if x_val is not None and y_val is not None:
            found = True
            min_x = min(min_x, x_val)
            max_x = max(max_x, x_val)
            min_y = min(min_y, y_val)
            max_y = max(max_y, y_val)

    if not found:
        return None, None, None, None

    return min_x, max_x, min_y, max_y
