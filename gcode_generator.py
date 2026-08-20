"""
Master G-code generator.

Takes a list of PlacedPart objects (each with a parsed GcodePart and placement
info) and produces a single merged .nc file following the order-of-operations
merge rules and nearest-neighbor travel sort from the spec.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from collision import PlacedPart, rail_geom, slot_mark_y


# ── identity and pocket ───────────────────────────────────────────────────────

@dataclass
class IdentityMap:
    """Which physical cutter each file's `T#` means, and which pocket it runs from.

    The `T#` in a source file carries two meanings at once — *identity* and *pocket*
    (spec §1). This splits them: `codes` says what a file's `T4` **is**, and `pockets`
    says where that cutter **sits** for this run. A remap therefore rewrites exactly two
    things (§4) — the `T# M06` line and the matching `G43 H#` line — and touches no `X`,
    `Y`, `Z`, `I`, `J`, `R` or `F` word.

    **Blocks are ordered by identity, not by the `T#` string** (§4.2). `sorted()` over a
    `T#` string puts `"T10"` before `"T2"`, so renumbering pockets would reorder blocks
    within a pass index and move the tool-change count — and hence the runtime estimate
    — for a job whose geometry did not change at all. Sorting by code makes the whole
    file invariant under a remap except at `T#`/`H#`, which is what lets the #12 test
    take its strong whole-file form.

    With no map supplied, identity *is* the `T#` and the pocket is the one it names, so
    an unremapped job emits exactly what it always did.
    """

    codes: Dict[str, Dict[str, str]] = field(default_factory=dict)   # instance_id → {T#: code}
    pockets: Dict[str, int] = field(default_factory=dict)            # code → pocket number

    def code_for(self, placed: PlacedPart, tool_number: str) -> str:
        per_part = self.codes.get(placed.instance_id) or {}
        return per_part.get(tool_number.upper()) or tool_number

    def word_for(self, code: str) -> str:
        """The tool word the file will carry — `T6` for pocket 6."""
        pocket = self.pockets.get(code)
        return f"T{int(pocket)}" if pocket else code


_IDENTITY = IdentityMap()

# ── compiled patterns ─────────────────────────────────────────────────────────

_IJ = re.compile(r"([IJ])([+-]?\d*\.?\d+)")
_N_CODE = re.compile(r"^N\d+\s*")
_TOOL_CMT = re.compile(r"\(\s*Tool:\s*(.+?)\)", re.IGNORECASE)
_SPINDLE = re.compile(r"\bM03\b.*\bS(\d+)\b", re.IGNORECASE)
_S_WORD = re.compile(r"\bS(\d+(?:\.\d+)?)\b", re.IGNORECASE)
_MOTION_WORD = re.compile(r"[XYZIJKR]", re.IGNORECASE)
_PLANE = re.compile(r"\bG1([789])\b", re.IGNORECASE)
_G43_LINE = re.compile(r"\bG43\b", re.IGNORECASE)
_Z_RETRACT = re.compile(r"^G0?0\s+Z[+-]?\d*\.?\d+\s*$", re.IGNORECASE)


# ── comments ──────────────────────────────────────────────────────────────────

_COMMENT_BRACKETS = str.maketrans({"(": "[", ")": "]"})
_NON_PRINTING = re.compile(r"[^\x20-\x7e]")


def comment(text: str) -> str:
    """`text` wrapped as a comment the control can actually parse.

    A comment runs from `(` to the **first** `)`. There is no nesting and no
    escape, so a paren inside one does not stay inside it: a job named
    `(9) 18G Test` emitted verbatim ends its comment after `(9`, and the control
    reads the rest of the block — `) 18G Test)` — as code and alarms on the stray
    `)`. That alarm lands mid-file, after the operator has loaded the program.

    Almost every comment this file writes carries a string somebody else authored
    — the operator's job name, CAM toolpath names, library tool descriptions,
    source file names, and `driven_by`, which this app itself writes as
    `18G.nc (retract)`. None of it can be assumed paren-free, so all of it goes
    through here.

    Two rules, and both are about what a *control* reads rather than what looks
    tidy:

    - Parens become **brackets**, not nothing. `(9)` deleted leaves a bare `9`
      that reads as part of the sentence; `[9]` still reads as the operator wrote
      it, and no Fanuc-family control treats brackets as anything inside a
      comment.
    - Anything outside printable ASCII becomes a space. The `.nc` is written as
      UTF-8, so one em dash reaches the Syntec as three bytes it has no code page
      for, and a stray newline would split one comment across two blocks —
      leaving the first unterminated and the second bare code.

    Whitespace is otherwise left exactly as passed: the header's alignment is
    part of its format, and `gcode_validator._check_comment_syntax` is the
    independent check that this ran.
    """
    return f"({_NON_PRINTING.sub(' ', str(text).translate(_COMMENT_BRACKETS))})"


def comment_is_wellformed(line: str) -> bool:
    """Can the control read every comment on this line?

    Used on the comment lines copied out of a source file, which `comment()`
    never sees — a toolpath named `Pocket (2)` in VCarve reaches the master the
    same way it reaches the source. `(A) (B)` is two comments and is fine; what
    is not fine is a `(` inside an open comment, a `)` with nothing open, or a
    comment still open at the end of the block.

    `gcode_validator` scans for the same thing and shares none of this code, on
    purpose — it is checking whether the generator got it right.
    """
    if _NON_PRINTING.search(line):
        return False
    depth = 0
    for ch in line:
        if ch == "(":
            if depth:
                return False
            depth = 1
        elif ch == ")":
            if not depth:
                return False
            depth = 0
    return depth == 0


# ── public entry point ────────────────────────────────────────────────────────

def generate_master_gcode(placements: List[PlacedPart], settings: Dict,
                          identity: Optional[IdentityMap] = None) -> str:
    """
    Build a merged master G-code string from a list of placed parts.

    Order of operations:
      Walk pass indices 0, 1, 2... across all parts.
      At each index, group by tool **identity** (see IdentityMap).
      Consecutive blocks with the same identity are merged (no tool change inserted).
      Within each merged block, segments are sorted by nearest-neighbor to
      minimise rapid-travel distance without violating per-part operation order.
    """
    identity = identity or _IDENTITY
    adv = settings["advanced"]
    rails = adv.get("rails")
    tool_capacity = int(adv.get("tool_capacity", 8))
    # Fence-origin offset: the machine's fence 00 differs from its working-area 00.
    # Shift every cut coordinate by this constant so cuts land in the right physical
    # place. x_off_mm → machine X (across bed); y_off_mm → machine Y (along rail).
    # NOT applied to the park move below: that is a G53 in absolute machine
    # coordinates, which the work/fence origin does not affect.
    x_off_mm = float(adv.get("fence_offset_x_in", 0.0)) * 25.4
    y_off_mm = float(adv.get("fence_offset_y_in", 0.0)) * 25.4
    park_x = float(adv.get("park_x", 0.0))
    park_y = float(adv.get("park_y", 3048.0))
    job_name = settings.get("job_name", "master_job")
    safe_z_info = settings.get("job_safe_z") or {}
    job_safe_z = float(safe_z_info.get("value") or adv.get("safe_z_clearance_mm", 25.4))
    safe_z_driver = safe_z_info.get("driven_by") or ""

    filenames = [p.part.filename for p in placements]
    # Distinct *physical cutters*, not distinct `T#` strings. Under identity merging the
    # two differ in both directions: two files' `T4` can be one cutter, and one file's
    # `T4` can be a different cutter from another's.
    all_codes: List[str] = []
    for p in placements:
        for gp in p.part.passes:
            code = identity.code_for(p, gp.tool_number)
            if code not in all_codes:
                all_codes.append(code)
    all_tools = [identity.word_for(c) for c in all_codes]

    # The tool changer holds a fixed number of tools (default 8). A job needing
    # more cannot be loaded on the machine, so fail loudly rather than emitting
    # G-code that will stall mid-run at an unavailable tool.
    #
    # This is a **backstop**, not the primary guard — the §3.4 validity gate refuses to
    # generate long before here, because rules 2 and 3 (every tool in exactly one
    # pocket, no pocket holding two) make a ninth tool unsatisfiable. The stricter
    # check runs first; the second line of defence stays.
    if len(all_codes) > tool_capacity:
        raise ValueError(
            f"Job needs {len(all_codes)} tools ({', '.join(all_tools)}) but the "
            f"Smartshop 2 tool changer holds only {tool_capacity}. "
            "Remove parts or reduce distinct tools."
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    out: List[str] = []
    _n = [10]

    def N(line: str) -> str:
        n = _n[0]
        _n[0] += 10
        return f"N{n} {line}"

    # ── file header (no line numbers) ─────────────────────────────────────────
    # Leading '%' pairs with the trailing one at the end of the file. Syntec
    # doesn't require either, but the posts that produce our source files write
    # both, and "present at both ends or neither" is a structural check.
    out += [
        "%",
        "(MASTER JOB - Generated by CNC Nest Tool)",
        comment(f"Date: {now}"),
        comment(f"Job: {job_name}"),
        comment(f"Parts: {', '.join(filenames)}"),
        comment(f"Instances: {len(placements)}  Tools: {', '.join(all_tools)}"),
        comment(f"Safe Z: {job_safe_z / 25.4:.3f}\" - driven by {safe_z_driver}"),
        "",
        N("G00 G17 G71 G40 G49 G80 G90"),
        N("G54"),
        N("G00 G53 Z0"),
    ]

    # ── tool blocks ───────────────────────────────────────────────────────────
    blocks = _build_blocks(placements, rails, x_off_mm, y_off_mm, identity)

    for block_num, block in enumerate(blocks, start=1):
        tool = block["tool"]
        # H follows the POCKET, not the cutter (spec §4.1). The shop loads a tool and
        # touches it off immediately, so the offset table is pocket-indexed: a remap
        # that moved `T` without moving `H` would apply the wrong tool-length offset —
        # wrong Z, crash or air-cut. Deriving H from the emitted tool word is what makes
        # that automatic. `gcode_validator._check_g43` rejects a mismatch at ERROR.
        h_num = re.sub(r"\D", "", tool)  # "T2" → "2"
        segs = _nearest_neighbor_sort(block["segments"])

        out += [
            "",
            comment(f" ---- {tool} pass {block_num} ---- "),
            N(f"{tool} M06"),
        ]
        if block["description"]:
            out.append(N(comment(block["description"])))
        # G00 is asserted, not inherited. On the first tool it would inherit G00
        # from the safety block, but on every later tool the previous pass ended
        # in G01 — which would feed down to safe Z at cutting rate instead of
        # rapiding. Same reasoning for the retract at the end of the block.
        out += [
            N(f"G00 G43 H{h_num} Z{job_safe_z:.4f}"),
            N(f"M03 S{block['spindle_speed']}"),
        ]
        current_spindle = block["spindle_speed"]
        for seg_idx, seg in enumerate(segs):
            if seg_idx > 0:
                out.append(N(f"G00 Z{job_safe_z:.4f}"))
            for ln in seg:
                s = ln.strip()
                if not s:
                    continue
                kept, current_spindle = _dedup_spindle(s, current_spindle)
                if kept:
                    out.append(N(kept))
        out += [
            N("G00 G53 G49 Z0"),
            N("M05"),
        ]

    # ── park and end ──────────────────────────────────────────────────────────
    # Retract in MACHINE coordinates, never G54. The per-tool retract above left
    # G49 active, and with tool length comp cancelled a G54 Z targets the spindle
    # gauge line rather than the tool tip — a G54 safe-Z here would drop the head
    # roughly one tool length below where the number reads, and the traverse on
    # the next line then crosses the full length of the bed at that height.
    # G53 Z0 is unambiguous under G49 and repeats the retract harmlessly when a
    # tool block already issued one.
    out += [
        "",
        "( ---- park ---- )",
        N("G00 G53 Z0"),
        N(f"G00 G53 X{park_x:.4f} Y{park_y:.4f} M05"),
        N("M30"),
        "%",
    ]

    return "\n".join(out) + "\n"


# ── block building ────────────────────────────────────────────────────────────

def _iter_pass_groups(placements: List[PlacedPart],
                      identity: Optional[IdentityMap] = None):
    """
    Yield `(code, [(placed, GcodePass), ...])` in the order blocks are emitted.

    Walk pass indices 0..max in order; at each index, group that index's passes
    by **identity**, not by the raw `T#`. This is the single source of the emitted
    block order — both `_build_blocks` and `block_tool_sequence` walk it, so a
    count taken from one can never disagree with the file produced by the other.

    Grouping by identity is what fixes §1's defect: two files that both say `T4`
    while meaning different cutters no longer merge into one block, and two files
    that say `T2` and `T4` for the *same* cutter now do.
    """
    identity = identity or _IDENTITY
    max_passes = max((len(p.part.passes) for p in placements), default=0)
    for idx in range(max_passes):
        by_code: Dict[str, list] = {}
        for placed in placements:
            if idx < len(placed.part.passes):
                gp = placed.part.passes[idx]
                by_code.setdefault(
                    identity.code_for(placed, gp.tool_number), []).append((placed, gp))

        for code in sorted(by_code):
            yield code, by_code[code]


def block_tool_sequence(placements: List[PlacedPart],
                        identity: Optional[IdentityMap] = None) -> List[str]:
    """
    The tool word of every block the generator will emit, in order.

    Each block opens with its own `T# M06`, so `len()` of this list is the job's
    tool-change count. It is NOT the distinct-tool list: a tool that recurs at a
    later pass index appears again, because the machine really does change back
    to it. `[T1, T2, T1, T2]` is two tools and four changes.

    The count is **invariant under remapping** — blocks are grouped and ordered by
    identity, so moving a cutter to another pocket cannot merge or split a block.
    """
    identity = identity or _IDENTITY
    seq: List[str] = []
    for code, _group in _iter_pass_groups(placements, identity):
        word = identity.word_for(code)
        if not seq or seq[-1] != word:
            seq.append(word)
    return seq


def _build_blocks(placements: List[PlacedPart], rails: Optional[dict] = None,
                  x_off_mm: float = 0.0, y_off_mm: float = 0.0,
                  identity: Optional[IdentityMap] = None) -> list:
    """
    Produce an ordered list of tool blocks from all placements.

    Append to the previous block when the identity matches; otherwise start a new
    one. This preserves each part's internal operation order while merging
    identical consecutive tools across parts.
    """
    identity = identity or _IDENTITY
    blocks: list = []

    for code, group in _iter_pass_groups(placements, identity):
        tool = identity.word_for(code)
        segs: list = []
        description = ""
        spindle_speed = 18000

        for placed, gp in group:
            raw_lines = gp.lines
            if not description:
                description = _tool_comment(raw_lines)
            spd = _spindle_speed(raw_lines)
            if spd:
                spindle_speed = spd
            body = _extract_body(raw_lines)
            params = _transform_params(placed, rails, x_off_mm, y_off_mm)
            seg = _transform_body(body, params)
            # The pass's first operation is named on the line above the tool
            # change, which puts it outside the body; later operations carry
            # their names inline and survive on their own. Restore it here so
            # every operation in the master file is labelled. It rides on the
            # segment rather than the block because the travel sort reorders
            # segments, and the name has to stay with its own geometry.
            if gp.operation_name:
                seg.insert(0, comment(gp.operation_name))
            segs.append(seg)

        if blocks and blocks[-1]["code"] == code:
            blocks[-1]["segments"].extend(segs)
        else:
            blocks.append({
                "code": code,
                "tool": tool,
                "description": description,
                "spindle_speed": spindle_speed,
                "segments": segs,
            })

    return blocks


# ── pass helpers ──────────────────────────────────────────────────────────────

def _tool_comment(lines: List[str]) -> str:
    for line in lines:
        m = _TOOL_CMT.search(line)
        if m:
            return m.group(1).strip()
    return ""


def _spindle_speed(lines: List[str]) -> Optional[int]:
    for line in lines:
        m = _SPINDLE.search(line)
        if m:
            return int(m.group(1))
    return None


def _dedup_spindle(line: str, current: Optional[int]) -> Tuple[Optional[str], Optional[int]]:
    """
    Suppress redundant spindle-speed commands within a tool block.

    `current` is the spindle speed already commanded (and still running) at this
    point in the block. Returns (output_line, new_current):
      - S-word equals `current` (redundant):
          * spindle-only line (no motion words) -> drop entirely -> (None, current)
          * motion line -> strip the redundant S-word, keep the motion
      - S-word differs -> keep the line, update current to the new speed
      - no S-word -> returned unchanged
    """
    m = _S_WORD.search(line)
    if not m:
        return line, current
    speed = int(float(m.group(1)))
    if current is not None and speed == current:
        if not _MOTION_WORD.search(line):
            return None, current
        stripped = re.sub(r"\s{2,}", " ", _S_WORD.sub("", line)).strip()
        return stripped, current
    return line, speed


def _extract_body(lines: List[str]) -> List[str]:
    """
    Return the movement lines from a pass: everything after M03 and before
    the trailing G53/M05/M30 retract block. Strips N-codes and any G43 lines
    (G43 is emitted once per tool block by the generator; source copies are dropped).
    Trailing Z-only retracts are also stripped so the generator can emit a single
    clean G00 Z[job_safe_z] between part segments.
    """
    body: List[str] = []
    in_body = False
    for line in lines:
        s = line.strip()
        if not in_body:
            if re.search(r"\bM03\b", s, re.IGNORECASE):
                in_body = True
        else:
            if (s == "%" or
                    re.search(r"\bM05\b|\bM30\b", s, re.IGNORECASE) or
                    (re.search(r"\bG53\b", s, re.IGNORECASE) and "Z" in s.upper())):
                break
            if s and not _G43_LINE.search(s):
                body.append(_N_CODE.sub("", s))
    while body and _Z_RETRACT.match(body[-1]):
        body.pop()
    return body


# ── coordinate transformation ─────────────────────────────────────────────────

def _transform_params(placed: PlacedPart, rails: Optional[dict] = None,
                      x_off_mm: float = 0.0, y_off_mm: float = 0.0) -> dict:
    """
    Pre-compute per-placement transform constants.

    VCarve X → G-code X output → machine Y direction
    VCarve Y → G-code Y output → machine X direction

    Each rail's datum and direction come from collision.rail_geom / slot_mark_y so
    the generator, collision detection and the canvas cannot drift apart:

      machine X = x_mm      + x_dir    * vcarve_Y
      machine Y = slot_mark + slot_dir * vcarve_X

    A negative direction is a mirrored axis (b_x / b_y True). With exactly one of
    the two mirrored the transform is a proper rotation, which is the normal case
    for both rails; _transform_line detects the reflection case from these flags
    and swaps arc direction accordingly.

    NOTE on the 'x'/'y' keys: they are named for the VCarve axis each transforms,
    not the output word. _transform_line swaps the axes, so the 'x' constant
    (slot_mark, machine Y) is emitted in the output Y word and the 'y' constant
    (rail, machine X) in the output X word.

    x_off_mm / y_off_mm are the fence-origin offsets in machine X / machine Y.
    y_off_mm therefore folds into the slot_mark constant ('x') and x_off_mm into
    the rail constant ('y'), which lands each one in the matching output word.
    _transform_line computes `const ± vcarve`, so adding to the constant shifts
    that axis by a fixed amount on BOTH rails regardless of the mirror flag.
    """
    g = rail_geom(placed.rail, rails)
    slot_mark = slot_mark_y(placed.rail, placed.slot_inches, rails)
    return {
        "b_x": float(g["slot_dir"]) < 0, "x": slot_mark + y_off_mm,
        "b_y": float(g["x_dir"]) < 0,    "y": float(g["x_mm"]) + x_off_mm,
    }


def _transform_body(lines: List[str], p: dict) -> List[str]:
    """
    Transform one pass body, tracking the modal work plane.

    The plane word is part of the transform, not a passenger. The X↔Y axis swap
    turns a file XZ arc (G18) into a machine YZ arc (G19) and vice versa, so the
    plane word has to be rewritten alongside the axis words — emitting a file
    G19 unchanged next to a swapped X word is an illegal block that alarms the
    control mid-cut. Direction (G02/G03) also flips per-plane, so each line has
    to know which plane is active when it runs.

    A pass always begins in G17 (the generator's own header commands it, and
    VCarve pass bodies open there). If a body ends in a vertical plane, G17 is
    restored so the next segment — which the travel sort may reorder — starts
    from the same known state.
    """
    plane = "G17"
    out: List[str] = []
    for ln in lines:
        # Comments are not code. _extract_body keeps them, and toolpath names
        # reach here too, so a bare "G18" inside one would otherwise shift the
        # modal plane for every line after it. gcode_parser guards this the
        # same way.
        if not ln.lstrip().startswith("("):
            m = _PLANE.search(ln)
            if m:
                plane = "G1" + m.group(1)
        out.append(_transform_line(ln, p, plane))
    if plane != "G17":
        out.append("G17")
    return out


def _transform_line(line: str, p: dict, plane: str = "G17") -> str:
    """
    Apply placement transform to a single G-code line.

    Axis mapping: VCarve X (file X word) → machine Y (output Y word)
                  VCarve Y (file Y word) → machine X (output X word)
    Arc offsets:  file I (VCarve-X direction) → output J (machine-Y direction)
                  file J (VCarve-Y direction) → output I (machine-X direction)
                  file K (Z direction) → output K (Z is untouched)

    `plane` is the modal work plane in effect for this line, in FILE terms
    (see _transform_body). The swap G18↔G19 falls out of the same axis mapping:
    a file XZ arc becomes a machine YZ arc. G17 maps to itself.

    p['b_x']/p['x']: mirror flag and constant for the VCarve-X → machine-Y transform
    p['b_y']/p['y']: mirror flag and constant for the VCarve-Y → machine-X transform
    Comments and G53 machine-coord lines are returned unchanged.
    """
    s = line.strip()
    if not s:
        return s
    if s.startswith("("):
        # A comment is not code, so it passes through byte-for-byte — unless the
        # source file wrote one the control cannot read, in which case re-wrapping
        # it as a single comment keeps every character and costs only the look of
        # the line. Emitting it unchanged would put a malformed block in the
        # master, and the master is the file that gets run.
        return s if comment_is_wellformed(s) else comment(s)
    if re.search(r"\bG53\b", s, re.IGNORECASE):
        return s

    result = s
    x_mirror = p["b_x"]
    y_mirror = p["b_y"]
    s_x = -1.0 if x_mirror else 1.0   # sign of VCarve X → machine Y
    s_y = -1.0 if y_mirror else 1.0   # sign of VCarve Y → machine X

    # Arc direction follows the plane's NORMAL axis, not its in-plane axes: an arc
    # reverses sense exactly when its normal reverses. The normal is an axial
    # vector, so it picks up det(Jacobian) as well as the axis it maps onto.
    #
    #   det = -s_x * s_y   (negative = reflection; both rails are proper rotations,
    #                       exactly one axis mirrored, so det = +1)
    #   G17 normal Z → machine Z, sign +1
    #   G18 normal Y → machine X, sign s_y
    #   G19 normal X → machine Y, sign s_x
    #
    # Flip when the transformed normal opposes the target plane's normal. For G17
    # this reduces to the old rule (flip iff x_mirror == y_mirror); the vertical
    # planes differ per rail, which is why they need the plane to decide.
    det = -s_x * s_y
    normal_sign = {"G18": s_y, "G19": s_x}.get(plane, 1.0)
    if det * normal_sign < 0:
        if re.search(r"\bG02\b", result, re.IGNORECASE):
            result = re.sub(r"\bG02\b", "G03", result, flags=re.IGNORECASE)
        elif re.search(r"\bG03\b", result, re.IGNORECASE):
            result = re.sub(r"\bG03\b", "G02", result, flags=re.IGNORECASE)

    # Plane word: G18 (file XZ) ↔ G19 (machine YZ) under the axis swap; G17 is fixed.
    result = _PLANE.sub(
        lambda m: {"7": "G17", "8": "G19", "9": "G18"}[m.group(1)], result)

    # Arc offsets: file I (VCarve-X direction) → machine-Y → output J (negate if x_mirror)
    #              file J (VCarve-Y direction) → machine-X → output I (negate if y_mirror)
    # Use placeholders to avoid cross-contamination between the two substitutions.
    result = re.sub(r"I([+-]?\d*\.?\d+)",
                    lambda m: f"__J__{-float(m.group(1)):.4f}" if x_mirror
                    else f"__J__{float(m.group(1)):.4f}", result)
    result = re.sub(r"J([+-]?\d*\.?\d+)",
                    lambda m: f"__I__{-float(m.group(1)):.4f}" if y_mirror
                    else f"__I__{float(m.group(1)):.4f}", result)
    result = result.replace("__J__", "J").replace("__I__", "I")

    # Coordinates: file X (VCarve X) → machine Y → output Y word
    #              file Y (VCarve Y) → machine X → output X word
    result = re.sub(r"X([+-]?\d*\.?\d+)",
                    lambda m: f"__Y__{p['x'] - float(m.group(1)):.4f}" if x_mirror
                    else f"__Y__{float(m.group(1)) + p['x']:.4f}", result)
    result = re.sub(r"Y([+-]?\d*\.?\d+)",
                    lambda m: f"__X__{p['y'] - float(m.group(1)):.4f}" if y_mirror
                    else f"__X__{float(m.group(1)) + p['y']:.4f}", result)
    result = result.replace("__Y__", "Y").replace("__X__", "X")

    return result


# ── nearest-neighbour travel sort ─────────────────────────────────────────────

def _first_xy(lines: List[str]) -> Tuple[float, float]:
    x = y = None
    for line in lines:
        # Comments are not motion. A toolpath name like "(POCKET X2)" would
        # otherwise read as an X word and anchor the sort at the wrong point.
        if line.lstrip().startswith("("):
            continue
        for axis, val in re.findall(r"([XY])([+-]?\d*\.?\d+)", line):
            if axis == "X" and x is None:
                x = float(val)
            elif axis == "Y" and y is None:
                y = float(val)
        if x is not None and y is not None:
            break
    return (x or 0.0, y or 0.0)


def _last_xy(lines: List[str]) -> Tuple[float, float]:
    return _first_xy(list(reversed(lines)))


def _nearest_neighbor_sort(segs: list) -> list:
    """Greedy nearest-neighbour ordering to minimise rapid travel between segments."""
    if len(segs) <= 1:
        return segs
    remaining = list(segs)
    result = [remaining.pop(0)]
    last = _last_xy(result[0])
    while remaining:
        best_i, best_d = 0, float("inf")
        for i, seg in enumerate(remaining):
            dx, dy = _first_xy(seg)[0] - last[0], _first_xy(seg)[1] - last[1]
            d = (dx * dx + dy * dy) ** 0.5
            if d < best_d:
                best_d, best_i = d, i
        result.append(remaining.pop(best_i))
        last = _last_xy(result[-1])
    return result
