from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from gcode_parser import GcodePart


class ToolLibrary:
    def __init__(self, tools: Dict[str, Dict]):
        self.tools = {key.upper(): value for key, value in tools.items()}

    def get_tool(self, tool_number: str) -> Optional[Dict]:
        return self.tools.get(tool_number.upper())

    def resolve_diameter(self, tool_number: str, override: Optional[float] = None) -> Optional[float]:
        if override is not None:
            return override
        tool = self.get_tool(tool_number)
        return tool.get("diameter_inches") if tool else None

    def register_tool(self, tool_number: str, description: str, diameter_inches: float) -> None:
        self.tools[tool_number.upper()] = {
            "name": description,
            "diameter_inches": diameter_inches,
        }

    def resolve_for_part(self, part: "GcodePart", tool_number: str) -> Optional[float]:
        """
        Diameter resolution per spec priority:
          1. File header {N inches}
          2. Tool library in config
        Returns None only if neither source has a diameter.
        """
        tool_info = part.tools.get(tool_number.upper(), {})
        file_dia = tool_info.get("diameter_inches")
        if file_dia is not None:
            return float(file_dia)
        return self.resolve_diameter(tool_number)

    def find_unknown_tools(self, part: "GcodePart") -> List[Dict]:
        """
        Return tools referenced in the part's passes that have no resolvable
        diameter — not in the file header and not in the library.
        Placement must be blocked until the operator provides diameters.
        """
        unknown = []
        seen: set = set()
        for gp in part.passes:
            tn = gp.tool_number.upper()
            if tn in seen:
                continue
            seen.add(tn)
            if self.resolve_for_part(part, tn) is None:
                tool_info = part.tools.get(tn, {})
                unknown.append({
                    "tool_number": tn,
                    "description": tool_info.get("description", ""),
                })
        return unknown
