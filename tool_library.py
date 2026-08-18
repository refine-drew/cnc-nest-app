"""The tool identity library — code-keyed, operator-declared, and the sole diameter
authority (spec §3.1, §3.5).

The `T#` in a posted file means two things at once: *which cutter* and *which pocket*.
This module owns the first. A file matches into the library on a **shop-assigned code**
the operator types into Fusion's Product id and into the VCarve tool name; the pocket is
job state and lives elsewhere (`pocket_map.py`).

Three rules run through everything below and are easy to undo by accident:

- **No string is ever compared for identity.** The code is either present in the file
  and present in the library, or the tool orphans to an explicit operator decision.
  `End Mill` is never matched against `End Mill` — §3.1's dangerous direction is a
  stale description making two different cutters look like one.
- **The library is the sole diameter authority** (§3.5.2). `diameter_inches` is the
  tool's *maximum cutting diameter*, and no file supplies it. A posted `D=` is shown
  beside candidates when binding an orphan and is never read as truth.
- **The description seal only learns from code-matched files** (§3.5.3). A manual bind
  says nothing about what a code posts, so binding an orphan must not teach the seal.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from gcode_parser import GcodePart

LIBRARY_FILE = "tool_library.json"

# What a shop code looks like in free text. Used only to *find* a candidate token in a
# VCarve tool name; whether it is a real code is decided by exact membership in the
# library, never by this pattern. The hyphen is what keeps it visibly unlike a `T#`
# (§3.1) and out of ordinary words — `End Mill` yields no candidate at all.
#
# It survives the post's comment filter (`" a-z0-9.,=_-"`, uppercased) intact, which is
# why `RK-004` works and `RK#4` would silently arrive as `RK4`.
CODE_TOKEN_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]*-[A-Z0-9]+\b", re.IGNORECASE)

# The operator's own class names, growing on demand (§3.5.1). Five classes, each with a
# tool behind it. Adding a sixth is cheap; forcing two different profile bits into one
# bucket is not.
GEOMETRY_CLASSES = (
    "Flat End Mill",
    "Ball Nose",
    "Roundover",
    "Custom Form",
    "Bowl Bit",
)

# Deliberately separate from geometry class: up/down/compression is orthogonal to
# flat/ball/roundover, so two short lists beat a dozen combined ones. This is also the
# one field **no file from either CAM can supply** (§3.1) — it exists only here.
FLUTE_DIRECTIONS = ("up", "down", "compression", "straight")

_FLUTE_DISPLAY = {
    "up": "upcut",
    "down": "downcut",
    "compression": "compression",
    "straight": "straight",
}

# What the operator may have typed into the CSV's Flute Direction column.
_FLUTE_ALIASES = {
    "upcut": "up",
    "up": "up",
    "downcut": "down",
    "down": "down",
    "compression": "compression",
    "comp": "compression",
    "straight": "straight",
}


def flute_display(value: str) -> str:
    """`down` → `downcut`, for anything an operator reads."""
    return _FLUTE_DISPLAY.get((value or "").lower(), value or "")


def normalize_flute(value: str) -> str:
    return _FLUTE_ALIASES.get((value or "").strip().lower(), (value or "").strip().lower())


def normalize_code(value) -> str:
    """Codes are compared exactly, so they are normalised exactly once — here.

    Uppercased because the post uppercases every comment it writes, so a code typed in
    lower case into Fusion arrives upper case and would otherwise never match.
    """
    return str(value or "").strip().upper()


# ── the entry ─────────────────────────────────────────────────────────────────

@dataclass
class LibraryTool:
    """One physical cutter. Every field has a named consumer (§3.5.1)."""

    code: str                       # primary key AND match key — operator-assigned
    name: str                       # display only, and the app's own; renaming is free
    diameter_inches: float          # MAXIMUM cutting diameter — collision, X envelope
    geometry_class: str             # display
    flute_direction: str            # the fact no file can supply
    cutting_length_in: Optional[float] = None   # tells apart two otherwise-identical cutters
    cam_descriptions: List[str] = field(default_factory=list)   # the description seal
    default_slot: Optional[int] = None          # None → staged, a legitimate answer
    vendor: str = ""                # reorder info; never read by the app
    product_link: str = ""          # reorder info; never read by the app

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "diameter_inches": self.diameter_inches,
            "geometry_class": self.geometry_class,
            "flute_direction": self.flute_direction,
            "cutting_length_in": self.cutting_length_in,
            "cam_descriptions": list(self.cam_descriptions),
            "default_slot": self.default_slot,
            "vendor": self.vendor,
            "product_link": self.product_link,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LibraryTool":
        slot = data.get("default_slot")
        length = data.get("cutting_length_in")
        return cls(
            code=normalize_code(data.get("code")),
            name=str(data.get("name") or "").strip(),
            diameter_inches=float(data.get("diameter_inches") or 0.0),
            geometry_class=str(data.get("geometry_class") or "").strip(),
            flute_direction=normalize_flute(data.get("flute_direction")),
            cutting_length_in=None if length in (None, "") else float(length),
            cam_descriptions=[str(d) for d in (data.get("cam_descriptions") or [])],
            default_slot=None if slot in (None, "") else int(slot),
            vendor=str(data.get("vendor") or "").strip(),
            product_link=str(data.get("product_link") or "").strip(),
        )

    @property
    def display(self) -> str:
        """`0.5" · downcut` — the one line under the name on a dock card."""
        dia = f"{self.diameter_inches:g}\""
        return f"{dia} · {flute_display(self.flute_direction)}"

    @property
    def radius_mm(self) -> float:
        return self.diameter_inches * 25.4 / 2


class ToolLibraryError(ValueError):
    """A library edit that must be refused rather than absorbed."""


# ── the library ───────────────────────────────────────────────────────────────

class ToolLibrary:
    """Code → LibraryTool. Hand-maintained, imports nothing (§3.5)."""

    def __init__(self, tools: Optional[List[LibraryTool]] = None):
        self.tools: Dict[str, LibraryTool] = {}
        for tool in tools or []:
            self.tools[tool.code] = tool

    # ── persistence ──
    @classmethod
    def from_dict(cls, data: dict) -> "ToolLibrary":
        return cls([LibraryTool.from_dict(t) for t in (data or {}).get("tools", [])])

    def to_dict(self) -> dict:
        return {"version": 1, "tools": [t.to_dict() for t in self.sorted_tools()]}

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "ToolLibrary":
        p = Path(path) if path else default_library_path()
        if not p.exists():
            return cls()
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, path: Optional[Path] = None) -> None:
        p = Path(path) if path else default_library_path()
        p.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    # ── reads ──
    def sorted_tools(self) -> List[LibraryTool]:
        """Code order. Nothing depends on it — the assigner makes no choices (§3.2.1) —
        so this is for the operator's eyes, not for determinism."""
        return sorted(self.tools.values(), key=lambda t: t.code)

    def get(self, code) -> Optional[LibraryTool]:
        return self.tools.get(normalize_code(code))

    def __contains__(self, code) -> bool:
        return normalize_code(code) in self.tools

    def __len__(self) -> int:
        return len(self.tools)

    @property
    def codes(self) -> List[str]:
        return sorted(self.tools)

    # ── writes ──
    def upsert(self, tool: LibraryTool) -> LibraryTool:
        if not tool.code:
            raise ToolLibraryError("A tool needs a code — it is what every file matches on.")
        if tool.diameter_inches <= 0:
            raise ToolLibraryError(
                "Diameter must be the tool's widest cutting point, in inches, and greater "
                "than zero. It is what the collision and table-edge checks are built on.")
        if tool.flute_direction not in FLUTE_DIRECTIONS:
            raise ToolLibraryError(
                f"Flute direction must be one of: {', '.join(FLUTE_DIRECTIONS)}.")
        if not tool.geometry_class:
            raise ToolLibraryError("A tool needs a geometry class.")
        # Duplicate default slots are LEGITIMATE and must never be refused (§3.5.6) —
        # the library declares two tools into slot 2 and two into slot 4 on purpose, and
        # that collision is the case the whole feature exists to resolve.
        self.tools[tool.code] = tool
        return tool

    def delete(self, code) -> None:
        self.tools.pop(normalize_code(code), None)

    def merge(self, survivor_code, loser_code) -> LibraryTool:
        """Fold the loser's accepted descriptions into the survivor and delete it.

        The loser's **code is not kept as a second key** — that would reintroduce
        many-keys-to-one-tool, which is what §1 exists to remove (§3.5.4).
        """
        survivor = self.get(survivor_code)
        loser = self.get(loser_code)
        if survivor is None or loser is None:
            raise ToolLibraryError("Both tools must exist to merge them.")
        if survivor.code == loser.code:
            raise ToolLibraryError("Cannot merge a tool into itself.")
        for desc in loser.cam_descriptions:
            if desc not in survivor.cam_descriptions:
                survivor.cam_descriptions.append(desc)
        self.delete(loser.code)
        return survivor

    def learn_description(self, code, description: str) -> None:
        """Add an accepted CAM description to a code's set.

        A **set, never a single value** (§3.5.3). Replace-on-confirm thrashes forever
        after a rename — new files carry the new string, old files still carry the old
        one, and the prompt alternates between them. That trains click-through, which
        destroys the only cross-file detector of a wrong-tool cut.
        """
        tool = self.get(code)
        if tool is None or not description:
            return
        if description not in tool.cam_descriptions:
            tool.cam_descriptions.append(description)


def default_library_path() -> Path:
    return Path(__file__).parent / LIBRARY_FILE


# ── reading identity out of a parsed file ─────────────────────────────────────

def code_in_file_tool(info: dict) -> Optional[str]:
    """The shop code a parsed file tool carries, or None.

    Two sources, because two CAM apps:

    - **Fusion** — `PRODUCT=` from the `TOOLID` comment, which is the field the code is
      typed into. An **empty** `PRODUCT=` is not the same as an absent one: empty says
      the Fusion library entry needs a code, absent says the file predates the comment.
      Neither yields a code, but only the first is actionable, and `_toolid_fields`
      preserves the difference.
    - **VCarve** — the tool *name* is the only field its post lets reach a file, so the
      code is typed there and arrives inside the description.
    """
    product = (info.get("product_id") or "").strip()
    if product:
        return normalize_code(product)
    for source in (info.get("cam_description"), info.get("description")):
        if not source:
            continue
        match = CODE_TOKEN_PATTERN.search(str(source))
        if match:
            return normalize_code(match.group(0))
    return None


def cam_description_of(info: dict) -> str:
    """What this file posts as the tool's description — the seal's input.

    `TOOLDESC` for Fusion, and the tool name for VCarve, which is the same string the
    code was typed into. Deliberately *not* `_tool_compatibility`'s `description`
    field for Fusion: free text must not move that signal (CLAUDE.md).
    """
    return str(info.get("cam_description") or info.get("description") or "").strip()


def posted_diameter_of(info: dict) -> Optional[float]:
    """Fusion's structured `D=`, in inches. **Display only, never authority** (§3.5.2).

    Shown beside candidate tools when binding an orphan so a gross mismatch is visible,
    which is what replaced the retired guard (b). It differs from `diameter_inches` by
    design on every profile bit — `.25 Bowl Bit` is declared 0.75 against a nominal
    0.25 — so comparing them as a rule would refuse correctly declared tools.
    """
    value = info.get("diameter_inches")
    return float(value) if value else None


# ── resolution ────────────────────────────────────────────────────────────────

# One binding per `T#` in one file.
#   matched      — code present and known; the library tool is bound and the seal applies
#   bound        — no code, but the operator said which tool it is, for this run only
#   unknown_code — a code is present and the library has never seen it → create a tool
#   orphan       — no code at all → the operator says which tool it is
_RESOLVED = ("matched", "bound")


@dataclass
class ToolBinding:
    tool_number: str
    status: str
    code: Optional[str] = None              # the code token found in the file
    library_code: Optional[str] = None      # what it resolved to
    cam_description: str = ""
    posted_diameter_inches: Optional[float] = None
    description: str = ""

    @property
    def resolved(self) -> bool:
        return self.status in _RESOLVED


@dataclass
class SealPrompt:
    """A code posting a description it has never posted before (§3.5.3).

    Blocks rather than warns: it is the only cross-file detector of one code on two
    physical cutters, and a warning on a crash-class check gets clicked through.
    """
    library_code: str
    tool_number: str
    known: List[str]
    posted: str


@dataclass
class PartResolution:
    filename: str
    bindings: Dict[str, ToolBinding] = field(default_factory=dict)
    # Guard (a): two distinct T# in one file resolving to one library tool. CAM already
    # asserted they differ by giving them different pockets, so this is a hard stop
    # requiring manual disambiguation, NEVER a merge. Per file only — two *different*
    # files' T4 resolving to one tool is the feature working (§3.5.3).
    duplicate_codes: List[Tuple[str, List[str]]] = field(default_factory=list)
    seal_prompts: List[SealPrompt] = field(default_factory=list)
    # Descriptions safe to adopt without asking: a code whose set is empty has nothing
    # to disagree with, so the first string it posts is learned rather than prompted.
    learned: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def unresolved(self) -> List[ToolBinding]:
        return [b for b in self.bindings.values() if not b.resolved]

    @property
    def blocked(self) -> bool:
        return bool(self.unresolved or self.duplicate_codes or self.seal_prompts)

    def library_codes(self) -> List[str]:
        seen: List[str] = []
        for binding in self.bindings.values():
            if binding.library_code and binding.library_code not in seen:
                seen.append(binding.library_code)
        return seen

    def diameters_by_tool_number(self, library: ToolLibrary) -> Dict[str, float]:
        """`T#` → declared diameter, for the collision and envelope checks."""
        out: Dict[str, float] = {}
        for tn, binding in self.bindings.items():
            tool = library.get(binding.library_code) if binding.library_code else None
            if tool is not None:
                out[tn] = tool.diameter_inches
        return out


def resolve_part(
    library: ToolLibrary,
    part: "GcodePart",
    binds: Optional[Dict[str, str]] = None,
) -> PartResolution:
    """Resolve every tool a file uses, at load, strictly (§3.5.3).

    An unresolved tool must never reach the bed: the collision and envelope checks need
    a radius, the library is now its only source, and the app must not invent one.

    `binds` is the operator's job-scoped answers for this file — `{T#: code}`. Nothing
    about them is remembered past the run.
    """
    binds = {k.upper(): normalize_code(v) for k, v in (binds or {}).items()}
    result = PartResolution(filename=part.filename)

    # Pass order, deduplicated — the tools the file actually cuts with, not every T#
    # that appears in a header.
    tool_numbers: List[str] = []
    for gp in part.passes:
        tn = gp.tool_number.upper()
        if tn not in tool_numbers:
            tool_numbers.append(tn)
    for tn in part.tools:
        if tn.upper() not in tool_numbers:
            tool_numbers.append(tn.upper())

    for tn in tool_numbers:
        info = part.tools.get(tn, {}) or {}
        code = code_in_file_tool(info)
        binding = ToolBinding(
            tool_number=tn,
            status="orphan",
            code=code,
            cam_description=cam_description_of(info),
            posted_diameter_inches=posted_diameter_of(info),
            description=str(info.get("description") or ""),
        )

        if code and code in library:
            binding.status = "matched"
            binding.library_code = code
        elif code:
            binding.status = "unknown_code"
        elif tn in binds and binds[tn] in library:
            binding.status = "bound"
            binding.library_code = binds[tn]
        # A bind for a tool that *does* carry a code is ignored on purpose: the code is
        # self-identifying and an override would be a silent re-bind of an exact match.

        result.bindings[tn] = binding

    # Guard (a) — injective within this one file.
    by_code: Dict[str, List[str]] = {}
    for tn, binding in result.bindings.items():
        if binding.library_code:
            by_code.setdefault(binding.library_code, []).append(tn)
    for code, numbers in by_code.items():
        if len(numbers) > 1:
            result.duplicate_codes.append((code, sorted(numbers)))

    # The description seal — only for code-matched bindings. A manual bind is the
    # operator identifying a file that carries no code, so its description says nothing
    # about what that code posts; learning it there would let an unrelated string
    # silently validate a later file.
    for binding in result.bindings.values():
        if binding.status != "matched" or not binding.cam_description:
            continue
        tool = library.get(binding.library_code)
        if tool is None:
            continue
        if not tool.cam_descriptions:
            result.learned.append((tool.code, binding.cam_description))
        elif binding.cam_description not in tool.cam_descriptions:
            result.seal_prompts.append(SealPrompt(
                library_code=tool.code,
                tool_number=binding.tool_number,
                known=list(tool.cam_descriptions),
                posted=binding.cam_description,
            ))

    return result
