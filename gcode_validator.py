"""
Structural and safety validation of emitted G-code.

This is a deliberate **re-derivation from the emitted text**. It shares no state
with gcode_generator and imports none of its modal tracking: the point is to
catch the generator being wrong, and a checker built out of the generator's own
bookkeeping cannot do that. Everything below is rebuilt by reading the file the
way the control reads it — one block at a time, carrying modal state forward.

Findings come in two severities:

  ERROR    the file must not reach the machine. /api/generate refuses to write.
  WARNING  surfaced in the job report for a human to judge.

The split follows what the check can prove. A G49 cutting move is wrong under
every interpretation, so it is an ERROR. A straight-down plunge may be exactly
what the operator intended, so it is a WARNING with the depth and feed attached.
"""
import re
from dataclasses import dataclass
from math import hypot
from typing import List, Optional

ARC_TOLERANCE_MM = 0.02
PLUNGE_FEED_LIMIT = 2540.0        # 100 ipm — below this a straight plunge is routine
LONG_LINK_MM = 50.0               # link move worth a look when it runs below retract

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    severity: str
    check: str
    line_no: int          # 1-based, as the file reads
    line: str
    message: str

    def __str__(self) -> str:
        where = f"line {self.line_no}" if self.line_no else "file"
        return f"[{self.severity.upper()}] {self.check} ({where}): {self.message}"


# ── the Syntec code set ───────────────────────────────────────────────────────
#
# Per the Syntec Mill Programming Manual, the 900M set is Fanuc 0M with one
# documented deviation: G70/G71 for inch/metric where Fanuc uses G20/G21.
# G71 in our safety block is therefore CORRECT and must never be "fixed" to G21.
#
# Unknown codes are reported as WARNING, not ERROR. An unrecognised code is more
# often a gap in this list than a real defect, and hard-blocking a job on an
# incomplete allowlist is the worse failure.
SUPPORTED_G = {
    0, 1, 2, 3, 4, 9, 10, 11, 17, 18, 19, 20, 21, 28, 29, 30, 31,
    40, 41, 42, 43, 44, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59,
    61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 73, 74, 76,
    80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 94, 95, 96, 97, 98, 99,
}
SUPPORTED_M = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 19, 29, 30, 98, 99}

_COMMENT = re.compile(r"\([^)]*\)")
_SEQ = re.compile(r"^N(\d+)\b", re.IGNORECASE)
_G_WORD = re.compile(r"\bG(\d{1,3})\b", re.IGNORECASE)
_M_WORD = re.compile(r"\bM(\d{1,3})\b", re.IGNORECASE)
_AXIS = re.compile(r"\b([XYZ])\s*([+-]?\d*\.?\d+)", re.IGNORECASE)
_OFFSET = re.compile(r"\b([IJKR])\s*([+-]?\d*\.?\d+)", re.IGNORECASE)
_F_WORD = re.compile(r"\bF\s*([+-]?\d*\.?\d+)", re.IGNORECASE)
_T_WORD = re.compile(r"\bT(\d+)\b", re.IGNORECASE)
_H_WORD = re.compile(r"\bH(\d+)\b", re.IGNORECASE)

# Offset words each plane accepts. An offset outside its plane either alarms or
# produces a degenerate arc — both failure modes appeared in the 2026-08-14 file.
PLANE_OFFSETS = {17: ("I", "J"), 18: ("I", "K"), 19: ("J", "K")}
PLANE_AXES = {17: ("X", "Y"), 18: ("X", "Z"), 19: ("Y", "Z")}


def _strip(line: str) -> str:
    """Code content of a block, comments removed."""
    return _COMMENT.sub(" ", line).strip()


class _State:
    """Modal state carried block to block, exactly as the control carries it."""

    def __init__(self):
        self.motion: Optional[int] = None       # 0,1,2,3
        self.plane = 17
        self.absolute = True                    # G90
        self.comp_active = False                # G43 seen, G49 not yet
        self.spindle_on = False
        self.feed: Optional[float] = None
        self.pos = {"X": None, "Y": None, "Z": None}
        self.tool: Optional[str] = None
        self.retract_z: Optional[float] = None  # Z commanded on the G43 approach


def validate_gcode(text: str, advanced: Optional[dict] = None) -> List[Finding]:
    """Validate emitted G-code. Returns findings ordered by line."""
    findings: List[Finding] = []
    lines = text.splitlines()

    findings += _check_comment_syntax(lines)
    findings += _check_tape_marks(lines)
    findings += _walk(lines, advanced or {})
    findings.sort(key=lambda f: (f.line_no, f.check))
    return findings


def has_errors(findings: List[Finding]) -> bool:
    return any(f.severity == ERROR for f in findings)


def format_findings(findings: List[Finding]) -> str:
    """Plain-text block for the job report."""
    if not findings:
        return "G-code validation: no findings."
    errs = [f for f in findings if f.severity == ERROR]
    warns = [f for f in findings if f.severity == WARNING]
    out = [f"G-code validation: {len(errs)} error(s), {len(warns)} warning(s)."]
    out += [f"  {f}" for f in findings]
    return "\n".join(out)


# ── structure ─────────────────────────────────────────────────────────────────

def _check_comment_syntax(lines: List[str]) -> List[Finding]:
    """Every `(` opens a comment that closes with `)` before the block ends.

    A comment ends at the **first** `)` — there is no nesting and no escape — so
    a paren inside one does not stay inside it. `(Job: (9) 18G Test)` is a
    comment that ends after `(9`, and the control reads `) 18G Test)` as code and
    alarms. The alarm arrives at whatever line the bad comment sits on, which for
    a header comment is after the operator has loaded the program and started it.

    ERROR, not WARNING: the block is unreadable under every interpretation, and
    what the control does with the remainder is undefined.

    This reads the emitted characters and shares no scanner with
    `gcode_generator.comment`, which is the point — the generator sanitises every
    string it interpolates, and this is what says whether it did. The one thing
    it cannot see is a comment the generator never touched *and* the source file
    got right; those pass, correctly.
    """
    out: List[Finding] = []
    for idx, raw in enumerate(lines):
        n = idx + 1
        depth = 0
        problem = None
        for ch in raw:
            if ch == "(":
                if depth:
                    problem = ("a '(' inside a comment that is already open — the "
                               "comment ends at the first ')', so the rest of the "
                               "block is read as code")
                    break
                depth = 1
            elif ch == ")":
                if not depth:
                    problem = "a ')' with no comment open"
                    break
                depth = 0
        if problem is None and depth:
            problem = "a comment that is never closed before the end of the block"
        if problem:
            out.append(Finding(
                ERROR, "comment-syntax", n, raw.strip(),
                f"Malformed comment: {problem}.",
            ))
    return out


def _check_tape_marks(lines: List[str]) -> List[Finding]:
    marks = [i for i, l in enumerate(lines) if l.strip() == "%"]
    body = [l for l in lines if l.strip()]
    if not body:
        return []
    leading = body[0].strip() == "%"
    trailing = body[-1].strip() == "%"
    if leading == trailing:
        return []
    return [Finding(
        ERROR, "tape-marks", (marks[0] + 1) if marks else 0, "%",
        "'%' appears at only one end of the file. It must be present at both "
        "ends or neither — a lone tape mark makes the program's extent ambiguous.",
    )]


# ── the walk ──────────────────────────────────────────────────────────────────

def _walk(lines: List[str], advanced: dict) -> List[Finding]:
    out: List[Finding] = []
    st = _State()
    last_seq: Optional[int] = None
    seen_seq = set()
    m30_lines: List[int] = []
    m06_count = 0

    limits, margin = _envelope_config(advanced)

    for idx, raw in enumerate(lines):
        n = idx + 1
        code = _strip(raw)
        if not code or code == "%":
            continue

        gs = [int(m) for m in _G_WORD.findall(code)]
        ms = [int(m) for m in _M_WORD.findall(code)]

        out += _check_sequence(code, n, raw, seen_seq, last_seq)
        seq = _SEQ.match(code)
        if seq:
            last_seq = int(seq.group(1))

        out += _check_known_codes(gs, ms, n, raw)

        # ── modal updates, in the order the control applies them ──────────────
        for g in gs:
            if g in (17, 18, 19):
                st.plane = g
            elif g in (0, 1, 2, 3):
                st.motion = g
            elif g == 90:
                st.absolute = True
            elif g == 91:
                st.absolute = False
            elif g == 43:
                st.comp_active = True
            elif g == 49:
                st.comp_active = False

        machine_frame = 53 in gs
        f = _F_WORD.search(code)
        if f:
            st.feed = float(f.group(1))

        for m in ms:
            if m in (3, 4):
                st.spindle_on = True
            elif m == 5:
                st.spindle_on = False
            elif m == 6:
                m06_count += 1
                t = _T_WORD.search(code)
                st.tool = t.group(1) if t else None
                out += _check_tool_change(code, n, raw, t)
            elif m == 30:
                m30_lines.append(n)
            elif m in (0, 1, 2):
                out.append(Finding(
                    ERROR, "stray-stop", n, raw.strip(),
                    f"M{m:02d} in a merged master program. A stop inherited from a "
                    "source file halts the job part-way through with no operator "
                    "prompt explaining why.",
                ))

        axes = {a.upper(): float(v) for a, v in _AXIS.findall(code)}

        if 43 in gs:
            out += _check_g43(code, n, raw, gs, st)
            if "Z" in axes:
                st.retract_z = axes["Z"]

        if 53 in gs and "Z" in axes:
            out += _check_g53_retract(n, raw, gs)

        # ── per-block safety, evaluated against the state this block runs in ──
        moving = bool(axes) and st.motion is not None
        cutting = moving and st.motion in (1, 2, 3)

        if "Z" in axes and not machine_frame and not st.comp_active:
            out.append(Finding(
                ERROR, "g49-z-move", n, raw.strip(),
                f"Work-frame Z move to {axes['Z']:.4f} with tool length "
                "compensation cancelled (G49). The control positions the spindle "
                "gauge line, not the tool tip, so the head sits roughly one tool "
                "length below where this number reads. Retract in machine "
                "coordinates (G53) instead, where G49 has no bearing.",
            ))
        if cutting:
            out += _check_cutting_conditions(n, raw, st, "Z" in axes)
        if st.motion in (2, 3) and moving:
            out += _check_arc(code, n, raw, st, axes)
        if moving:
            out += _check_motion_height(n, raw, st, axes, machine_frame)
            if not machine_frame:
                out += _check_envelope(n, raw, axes, limits, margin)

        if st.absolute:
            for a, v in axes.items():
                if not machine_frame:
                    st.pos[a] = v
                else:
                    # A machine-frame move lands somewhere the work frame can no
                    # longer name from this block alone. Drop the axis rather
                    # than carry a number that is now in the wrong frame.
                    st.pos[a] = None

    out += _check_program_end(lines, m30_lines, m06_count, st)
    return out


# ── individual checks ─────────────────────────────────────────────────────────

def _check_sequence(code: str, n: int, raw: str, seen: set,
                    last: Optional[int]) -> List[Finding]:
    m = _SEQ.match(code)
    if not m:
        return []
    num = int(m.group(1))
    if num in seen:
        return [Finding(ERROR, "sequence", n, raw.strip(),
                        f"Sequence number N{num} is used more than once. Block search "
                        "and mid-program restart both key off N, so a duplicate sends "
                        "the control to the wrong place.")]
    seen.add(num)
    if last is not None and num <= last:
        return [Finding(ERROR, "sequence", n, raw.strip(),
                        f"Sequence number N{num} does not increase (previous was N{last}).")]
    return []


def _check_known_codes(gs: List[int], ms: List[int], n: int, raw: str) -> List[Finding]:
    out = []
    for g in gs:
        if g not in SUPPORTED_G:
            out.append(Finding(WARNING, "unknown-code", n, raw.strip(),
                               f"G{g:02d} is not in the known Syntec 900M set. Confirm the "
                               "control accepts it before running."))
    for m in ms:
        if m not in SUPPORTED_M:
            out.append(Finding(WARNING, "unknown-code", n, raw.strip(),
                               f"M{m:02d} is not in the known Syntec 900M set. Confirm the "
                               "control accepts it before running."))
    return out


def _check_tool_change(code: str, n: int, raw: str, t) -> List[Finding]:
    if not t:
        return [Finding(ERROR, "tool-change", n, raw.strip(),
                        "M06 with no T word — the changer has nothing to select.")]
    return []


def _check_g43(code: str, n: int, raw: str, gs: List[int], st: _State) -> List[Finding]:
    out = []
    h = _H_WORD.search(code)
    if not h:
        out.append(Finding(ERROR, "g43-offset", n, raw.strip(),
                           "G43 with no H word — the control applies an undefined "
                           "tool length offset."))
    # ERROR, not WARNING, because the control is assumed to honour H rather than
    # substitute a live measured length (issue #5, decided 2026-08-17). Under that
    # assumption a mismatched H is a wrong Z, which a check can prove from the file
    # alone. If the machine is ever observed to ignore H, this drops to advisory —
    # until then it blocks the write.
    elif st.tool is not None and h.group(1).lstrip("0") != st.tool.lstrip("0"):
        out.append(Finding(
            ERROR, "g43-offset", n, raw.strip(),
            f"G43 H{h.group(1)} follows a change to T{st.tool}. Touch-off is per "
            "pocket, so H must track the pocket the tool is actually in — this "
            "would apply another tool's length.",
        ))
    if not any(g == 0 for g in gs):
        out.append(Finding(
            ERROR, "modal-hygiene", n, raw.strip(),
            "G43 approach does not assert G00. It inherits the motion mode of "
            "the previous block, which on every tool after the first is the G01 "
            "left by the last cutting move — the approach then feeds to safe Z.",
        ))
    return out


def _check_g53_retract(n: int, raw: str, gs: List[int]) -> List[Finding]:
    if any(g == 0 for g in gs):
        return []
    return [Finding(
        ERROR, "modal-hygiene", n, raw.strip(),
        "G53 Z retract does not assert G00. It inherits the previous motion "
        "mode, so a retract after a cutting move runs at feed rate.",
    )]


def _check_cutting_conditions(n: int, raw: str, st: _State,
                              has_z: bool = False) -> List[Finding]:
    out = []
    if not st.comp_active and not has_z:
        # A block carrying Z is already reported by the g49-z-move check; this
        # catches the lateral cutting moves that follow one, where the wrong
        # depth is already dialled in and no Z word gives it away.
        out.append(Finding(
            ERROR, "g49-cutting", n, raw.strip(),
            "Cutting move with tool length compensation cancelled (G49). Depth is "
            "measured to the spindle gauge line, not the tool tip, so the cutter "
            "sits roughly one tool length below the commanded Z.",
        ))
    if not st.spindle_on:
        out.append(Finding(
            ERROR, "spindle-off", n, raw.strip(),
            "Cutting move with the spindle stopped.",
        ))
    if st.feed is None or st.feed == 0:
        out.append(Finding(
            ERROR, "no-feed", n, raw.strip(),
            "Feed move with no feed rate in effect"
            + ("." if st.feed is None else " (F0)."),
        ))
    return out


def _check_arc(code: str, n: int, raw: str, st: _State, axes: dict) -> List[Finding]:
    out = []
    offsets = {k.upper(): float(v) for k, v in _OFFSET.findall(code)}
    if not offsets:
        return out

    allowed = PLANE_OFFSETS[st.plane]
    for word in ("I", "J", "K"):
        if word in offsets and word not in allowed:
            out.append(Finding(
                ERROR, "arc-plane", n, raw.strip(),
                f"{word} offset in a G{st.plane} arc, which takes "
                f"{'/'.join(allowed)}. The offset does not belong to the active "
                "plane — the control either alarms or cuts a degenerate arc.",
            ))

    if "R" in offsets:
        return out   # R-format arcs carry no centre offsets to cross-check

    u, v = PLANE_AXES[st.plane]
    off_u = offsets.get({"X": "I", "Y": "J", "Z": "K"}[u])
    off_v = offsets.get({"X": "I", "Y": "J", "Z": "K"}[v])
    if off_u is None and off_v is None:
        return out
    off_u, off_v = off_u or 0.0, off_v or 0.0

    start_u, start_v = st.pos.get(u), st.pos.get(v)
    if start_u is None or start_v is None:
        return out          # no known start point; nothing provable
    end_u = axes.get(u, start_u)
    end_v = axes.get(v, start_v)

    r_start = hypot(off_u, off_v)
    r_end = hypot(start_u + off_u - end_u, start_v + off_v - end_v)
    if abs(r_start - r_end) > ARC_TOLERANCE_MM:
        out.append(Finding(
            ERROR, "arc-radius", n, raw.strip(),
            f"Arc centre is {r_start:.4f} mm from the start point but "
            f"{r_end:.4f} mm from the end point (tolerance {ARC_TOLERANCE_MM} mm). "
            "The two must agree or the arc is not a circle.",
        ))
    return out


def _check_motion_height(n: int, raw: str, st: _State, axes: dict,
                         machine_frame: bool) -> List[Finding]:
    """Rapids and links below the operation's retract plane."""
    if st.retract_z is None or machine_frame:
        return []
    z = axes.get("Z", st.pos.get("Z"))
    if z is None or z >= st.retract_z:
        return []

    if st.motion == 0 and ("X" in axes or "Y" in axes):
        # WARNING, not ERROR, and the distance is part of the message. The job's
        # retract plane is the maximum safe Z across every part in the nest, so
        # any part whose own clearance plane is lower trips this on every rapid
        # it makes — dozens per job, all of them fine inside that part's own
        # footprint. Proving which ones cross another part needs the footprints,
        # which the emitted text does not carry. Blocking on it would only teach
        # the operator to skip the check. The distance is what separates a hop
        # within one part from a traverse across the bed.
        dist = ""
        if st.pos["X"] is not None and st.pos["Y"] is not None:
            d = hypot(axes.get("X", st.pos["X"]) - st.pos["X"],
                      axes.get("Y", st.pos["Y"]) - st.pos["Y"])
            dist = f" travelling {d:.1f} mm"
        return [Finding(
            WARNING, "rapid-below-retract", n, raw.strip(),
            f"G00 lateral move at Z {z:.4f}{dist}, below this job's retract plane "
            f"of Z {st.retract_z:.4f}. Safe within the part's own footprint; "
            "confirm it does not cross another part on the bed.",
        )]

    if st.motion == 1 and st.pos["X"] is not None and st.pos["Y"] is not None:
        dx = axes.get("X", st.pos["X"]) - st.pos["X"]
        dy = axes.get("Y", st.pos["Y"]) - st.pos["Y"]
        if hypot(dx, dy) > LONG_LINK_MM:
            return [Finding(
                WARNING, "long-link", n, raw.strip(),
                f"{hypot(dx, dy):.1f} mm link move at Z {z:.4f}, below the retract "
                f"plane of Z {st.retract_z:.4f}. Confirm it stays in cut material.",
            )]

    if (st.motion == 1 and "Z" in axes and "X" not in axes and "Y" not in axes
            and st.pos["Z"] is not None and axes["Z"] < st.pos["Z"]):
        depth = st.pos["Z"] - axes["Z"]
        if st.feed and st.feed > PLUNGE_FEED_LIMIT:
            return [Finding(
                WARNING, "plunge", n, raw.strip(),
                f"Straight-down plunge of {depth:.3f} mm at F{st.feed:.0f} "
                f"({st.feed / 25.4:.0f} ipm). Confirm the tool is centre-cutting "
                "and the feed suits a full-width plunge.",
            )]
    return []


# Y takes no edge margin: nothing obstructs either end of the machinable
# surface, so the tool is allowed to hang off it and only the tool centre is
# bounded. X keeps its margin — there is a hard stop out there. This mirrors
# collision.check_envelope exactly; see _envelope_config.
_MARGINED_AXES = ("X", "Z")


def _check_envelope(n: int, raw: str, axes: dict, limits: dict,
                    margin: float) -> List[Finding]:
    out = []
    for axis in ("X", "Y", "Z"):
        if axis not in axes:
            continue
        val = axes[axis]
        band = margin if axis in _MARGINED_AXES else 0.0
        lo, hi = limits.get(f"{axis.lower()}_min"), limits.get(f"{axis.lower()}_max")
        if lo is not None:
            if val < lo:
                out.append(_envelope_finding(ERROR, n, raw, axis, val, lo, "below"))
            elif band and val < lo + band:
                out.append(_envelope_finding(WARNING, n, raw, axis, val, lo + band, "below"))
        if hi is not None:
            if val > hi:
                out.append(_envelope_finding(ERROR, n, raw, axis, val, hi, "past"))
            elif band and val > hi - band:
                out.append(_envelope_finding(WARNING, n, raw, axis, val, hi - band, "past"))
    return out


def _envelope_finding(severity: str, n: int, raw: str, axis: str, val: float,
                      bound: float, word: str) -> Finding:
    what = "the travel limit" if severity == ERROR else "the edge margin"
    # On Y the overhang is allowed, so the radius note would be actively
    # misleading — it is the reason the bound sits on tool centre in the first
    # place. On X the cutting edge is the thing the hard stop meets.
    note = ("This bound is on tool centre; the tool may overhang the end of "
            "the surface on Y, but its centre may not leave it."
            if axis == "Y" else
            "Coordinates are tool centre with no cutter compensation, so the "
            "cutting edge stands a further tool radius outside this.")
    return Finding(
        severity, "envelope", n, raw.strip(),
        f"{axis} {val:.4f} is {word} {what} of {bound:.4f}. {note}",
    )


def _check_program_end(lines: List[str], m30_lines: List[int], m06_count: int,
                       st: _State) -> List[Finding]:
    out = []
    if not m30_lines:
        out.append(Finding(ERROR, "program-end", len(lines), "",
                           "No M30 — the program never ends."))
    elif len(m30_lines) > 1:
        out.append(Finding(
            ERROR, "program-end", m30_lines[1], "",
            f"{len(m30_lines)} M30 blocks (lines {', '.join(map(str, m30_lines))}). "
            "Everything after the first is unreachable, so part of the job silently "
            "does not run.",
        ))
    if st.plane != 17:
        out.append(Finding(
            ERROR, "plane-restore", len(lines), "",
            f"Program ends in G{st.plane}. The next job starts in whatever plane "
            "this one left active, so its first arc is cut in the wrong plane.",
        ))
    if m06_count == 0:
        out.append(Finding(WARNING, "tool-change", 0, "",
                           "No M06 in the program — nothing loads a tool."))
    return out


def _envelope_config(advanced: dict):
    """
    Travel limits and edge margin — the same resolver collision.py's placement
    gate uses, so a layout accepted at placement time cannot be rejected by the
    validator over a different set of numbers. The per-axis policy has to match
    too: margin on X, none on Y (see _MARGINED_AXES).

    This check reads tool-centre coordinates out of the finished file and has no
    tool radius to inflate them by, so on X it is one radius more permissive
    than the placement gate. That direction is safe — the gate is the stricter
    of the two — but it means the validator is a backstop on X, not the primary
    guard. Z is still unmeasured and stays dormant (issue #19).
    """
    from collision import travel_limits, edge_margin_mm
    return travel_limits(advanced), edge_margin_mm(advanced)
