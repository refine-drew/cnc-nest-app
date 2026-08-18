"""Job-scoped pocket assignment and the validity gate (spec §3.2, §3.4).

The governing principle, and every rule here is a consequence of it: **the assigner
makes no arbitrary choices** (§3.2.1). It seeds each tool's declared default slot and
stops.

- **No tie-break.** Two tools declaring one pocket both sit in it, visibly and invalidly,
  until the operator drags one out. The app cannot know which cutter is cheaper to move,
  precisely because it holds no model of the changer's contents.
- **No fill rule.** A tool with no declared slot is *staged*, never dropped into the
  lowest free pocket.
- **No write-back.** A drag is a job-scoped override. The declared slot is
  *prescriptive*, and re-proposing it next job is the intended nag.
- **Nothing is refused at placement** — not even a ninth tool, because identity merging
  can still lower the count. Only *generation* is gated.

Determinism is therefore a **consequence, not a rule to enforce**: the map is a pure
function of (resolved tools, declared default slots) plus the operator's drags. Do not
add a tie-break "for stability" — that is the one thing that would break it.

One trap for anyone extending this: it reads **identity-ordered data, never the remapped
`T` numbers** (§4.2). An assigner reading post-remap numbers would make the emitted
output depend on an assignment that depends on the output.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tool_library import LibraryTool, ToolLibrary, flute_display

# Not a pocket. A tool parked here has nowhere to go yet, which is a legitimate state
# to *be in* and an invalid state to *generate from*.
STAGED = 0

DEFAULT_CAPACITY = 8


@dataclass
class ToolUsage:
    instance_id: str
    filename: str
    slot: str
    tool_number: str


@dataclass
class JobTool:
    """One library tool this job needs, with everything the dock has to show."""
    tool: LibraryTool
    usages: List[ToolUsage] = field(default_factory=list)

    @property
    def code(self) -> str:
        return self.tool.code

    @property
    def tool_numbers(self) -> List[str]:
        return sorted({u.tool_number for u in self.usages})

    @property
    def instance_ids(self) -> List[str]:
        seen: List[str] = []
        for u in self.usages:
            if u.instance_id not in seen:
                seen.append(u.instance_id)
        return seen


def seed_pocket(tool: LibraryTool, overrides: Optional[Dict[str, int]] = None) -> int:
    """Where a tool sits: the operator's drag if there is one, else its declared home,
    else staged. No third source, and nothing invented."""
    code = tool.code
    if overrides and code in overrides:
        return int(overrides[code])
    return int(tool.default_slot) if tool.default_slot else STAGED


def build_changer_state(
    library: ToolLibrary,
    placements: List[dict],
    overrides: Optional[Dict[str, int]] = None,
    capacity: int = DEFAULT_CAPACITY,
) -> dict:
    """Everything the changer dock and the Generate gate need, off one walk.

    `placements` is a list of `{instance_id, filename, slot, resolution}` where
    `resolution` is a `tool_library.PartResolution`.
    """
    overrides = dict(overrides or {})

    job_tools: Dict[str, JobTool] = {}
    unresolved: List[dict] = []
    duplicate_codes: List[dict] = []
    seal_prompts: List[dict] = []

    for placed in placements:
        res = placed["resolution"]
        for tn, binding in res.bindings.items():
            if binding.resolved and binding.library_code:
                tool = library.get(binding.library_code)
                if tool is None:          # library edited out from under the job
                    binding.status = "unknown_code"
                else:
                    job_tools.setdefault(tool.code, JobTool(tool=tool)).usages.append(
                        ToolUsage(placed["instance_id"], placed["filename"],
                                  placed["slot"], tn))
                    continue
            unresolved.append({
                "instance_id": placed["instance_id"],
                "filename": placed["filename"],
                # Carried so the dock can bind this orphan: a bind is scoped to
                # (path, T#), and the filename alone cannot locate the file.
                "path": placed.get("path", placed["filename"]),
                "slot": placed["slot"],
                "tool_number": tn,
                "status": binding.status,
                "code": binding.code,
                "cam_description": binding.cam_description,
                "description": binding.description,
                "posted_diameter_inches": binding.posted_diameter_inches,
            })
        for code, numbers in res.duplicate_codes:
            duplicate_codes.append({
                "instance_id": placed["instance_id"],
                "filename": placed["filename"],
                "library_code": code,
                "tool_numbers": numbers,
            })
        for prompt in res.seal_prompts:
            seal_prompts.append({
                "instance_id": placed["instance_id"],
                "filename": placed["filename"],
                "library_code": prompt.library_code,
                "tool_number": prompt.tool_number,
                "known": prompt.known,
                "posted": prompt.posted,
            })

    # ── the map ──
    assignment: Dict[str, int] = {
        code: seed_pocket(jt.tool, overrides) for code, jt in job_tools.items()
    }
    # A drag beyond the changer, or to a pocket that no longer exists, is not a state we
    # invent our way out of — it lands back in staging, visibly.
    for code, pocket in list(assignment.items()):
        if pocket != STAGED and not (1 <= pocket <= capacity):
            assignment[code] = STAGED

    occupants: Dict[int, List[str]] = {p: [] for p in range(1, capacity + 1)}
    staged: List[str] = []
    for code, pocket in assignment.items():
        (staged if pocket == STAGED else occupants[pocket]).append(code)
    staged.sort()
    for codes in occupants.values():
        codes.sort()

    doubled = [p for p, codes in occupants.items() if len(codes) > 1]
    free = [p for p, codes in occupants.items() if not codes]

    # ── which parts are the sole user of some tool (§3.4.1, "name the part") ──
    frees_by_instance: Dict[str, int] = {}
    for jt in job_tools.values():
        ids = jt.instance_ids
        if len(ids) == 1:
            frees_by_instance[ids[0]] = frees_by_instance.get(ids[0], 0) + 1
    for entry in unresolved:
        # An unresolved tool blocks too, so a part that solely owns one is worth naming.
        others = {e["instance_id"] for e in unresolved
                  if e["tool_number"] == entry["tool_number"] and e["code"] == entry["code"]}
        if len(others) == 1:
            frees_by_instance.setdefault(entry["instance_id"], 0)

    # ── the §3.4 validity gate ──
    # Three rules, and capacity is not a fourth: with eight pockets, rules 2 and 3
    # together make ">8 distinct tools" unsatisfiable, so the ninth tool is simply a
    # tool with nowhere to go.
    valid = (
        not unresolved
        and not duplicate_codes
        and not seal_prompts
        and not staged
        and not doubled
    )
    # Dragging only helps while a pocket is free. Once none is, the only move left is
    # taking a part off the bed — §3.4.1's "two messages off one state".
    needs_removal = not free and bool(staged or doubled)

    tools_payload = []
    for code in sorted(job_tools):
        jt = job_tools[code]
        t = jt.tool
        pocket = assignment[code]
        tools_payload.append({
            "code": code,
            "name": t.name,
            "display": t.display,
            "geometry_class": t.geometry_class,
            "flute_direction": t.flute_direction,
            "flute_display": flute_display(t.flute_direction),
            "diameter_inches": t.diameter_inches,
            "cutting_length_in": t.cutting_length_in,
            "vendor": t.vendor,
            "default_slot": t.default_slot,
            "pocket": pocket,
            # A tool sitting away from its declared pocket is marked, or the deviation is
            # invisible and never gets restored — which defeats the prescriptive default
            # (§3.2.1). Nag every time (operator's call, 2026-08-17): the contested pairs
            # are permanent, and a badge that goes quiet is one that stops being read.
            "off_home": bool(t.default_slot and pocket and pocket != t.default_slot),
            "tool_numbers": jt.tool_numbers,
            "part_count": len(jt.instance_ids),
            "sole_instance_id": jt.instance_ids[0] if len(jt.instance_ids) == 1 else None,
            "usages": [
                {"instance_id": u.instance_id, "filename": u.filename,
                 "slot": u.slot, "tool_number": u.tool_number}
                for u in jt.usages
            ],
        })

    return {
        "capacity": capacity,
        "assignment": assignment,
        "pockets": [{"number": p, "codes": occupants[p]} for p in range(1, capacity + 1)],
        "tools": tools_payload,
        "staged": staged,
        "unresolved": unresolved,
        "duplicate_codes": duplicate_codes,
        "seal_prompts": seal_prompts,
        "doubled": doubled,
        "free": free,
        "valid": valid,
        "needs_removal": needs_removal,
        "frees_by_instance": frees_by_instance,
        "removal_advice": _removal_advice(frees_by_instance, placements),
        "messages": _messages(staged, doubled, unresolved, duplicate_codes,
                              seal_prompts, free, len(job_tools)),
    }


def _removal_advice(frees_by_instance: Dict[str, int], placements: List[dict]) -> str:
    """"Removing bracket-L frees pocket 7." Better than making the operator scan.

    Cheapest first, since one pocket is usually all that is wanted. A part that shares
    every tool with something else cannot help and is deliberately not named.
    """
    names = {p["instance_id"]: p["filename"] for p in placements}
    candidates = sorted(
        ((iid, n) for iid, n in frees_by_instance.items() if n),
        key=lambda x: x[1],
    )
    if not candidates:
        return ("Every part shares its tools with another, so taking any single one off "
                "the bed will not free a pocket.")
    # Never `.capitalize()` this — it lowercases everything after the first character,
    # and these strings contain filenames. `18G300.NC` came back as `18g300.nc`, which
    # is a file the operator cannot find.
    parts = [f"{names.get(iid, iid)} frees {n} pocket{'s' if n > 1 else ''}"
             for iid, n in candidates[:2]]
    return "Removing " + "; ".join(parts) + "."


def _messages(staged, doubled, unresolved, duplicate_codes, seal_prompts,
              free, tool_count) -> List[dict]:
    """Two messages off one state (§3.4.1).

    "3 tools have no home" is a to-do when free pockets exist and a *stop* when the
    changer is full — dragging cannot fix the second, and an operator would otherwise
    hunt for a free pocket that does not exist. Operators are not developers; the
    wording carries the difference.
    """
    out: List[dict] = []
    room = len(free)

    if doubled:
        which = (f"Pocket {' and '.join(str(p) for p in doubled)} "
                 f"hold{'' if len(doubled) > 1 else 's'} two tools")
        out.append({"level": "error", "text": (
            f"{which} — drag one to a free pocket." if room else
            f"{which}, and every pocket is taken — take a part off the bed.")})

    if duplicate_codes:
        for d in duplicate_codes:
            out.append({"level": "error", "text": (
                f"{d['filename']} calls {' and '.join(d['tool_numbers'])} by the same "
                f"tool code ({d['library_code']}). The CAM file says they are different "
                f"cutters, so this has to be fixed there — the nest tool will not merge "
                f"them.")})

    if seal_prompts:
        for s in seal_prompts:
            out.append({"level": "error", "text": (
                f"{s['library_code']} has never posted \"{s['posted']}\" before. "
                f"Say whether it was renamed or whether two cutters share the code.")})

    if unresolved:
        n = len(unresolved)
        out.append({"level": "error", "text": (
            f"{n} tool{'s' if n > 1 else ''} in this nest "
            f"{'are' if n > 1 else 'is'} not in your tool library.")})

    if staged:
        n = len(staged)
        out.append({"level": "warning" if room else "error", "text": (
            f"{n} tool{'s' if n > 1 else ''} need{'' if n > 1 else 's'} a pocket — "
            f"{room} free." if room else
            f"{n} tool{'s' if n > 1 else ''} {'have' if n > 1 else 'has'} no home and "
            f"the changer is full — take a part off the bed.")})

    if not out:
        out.append({"level": "ok", "text": (
            f"{tool_count} tool{'s' if tool_count != 1 else ''} in "
            f"{tool_count} pocket{'s' if tool_count != 1 else ''}.")})
    return out
