from typing import Dict, Optional

class ToolLibrary:
    def __init__(self, tools: Dict[str, Dict[str, float]]):
        self.tools = {key.upper(): value for key, value in tools.items()}

    def get_tool(self, tool_number: str) -> Optional[Dict[str, float]]:
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
