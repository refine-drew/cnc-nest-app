# Tool Changer Pocket Management: Brainstorm / Discovery Notes
Date: 2026-08-12 · Goal: Name the destination for a Wayfinder map covering tool-changer pocket management in CNC Nest — so any combination of files using ≤8 distinct tools can run as one job.

## Summary / key decisions

_(running synthesis — updated as we go)_

### The core reframe
`T#` in a VCarve `.nc` file currently does **double duty**: it is both the tool's
*identity* ("a 1/2 inch end mill") and its *pocket* in the changer ("slot 4").
The feature is fundamentally about **splitting those two concepts apart**:
identity comes from the file's tool metadata; pocket becomes an app-assigned,
operator-adjustable value.

### Decisions reached this session
1. **Identity comes from a tool library, not from the file.** No library exists
   today; the operator will build one. Description strings are too unstable to
   be the identity key, and the dangerous failure direction (two different
   cutters looking identical → merged into one block) argues against them.
2. **A Fusion library will not remove the need for an app-side library.** The
   app reads posted `.nc`, not CAM source; the existing corpus is vCarve; and
   CAM records *what a tool is* while the app must record *where it lives*.
3. **`H` is pocket-indexed, so `H` must follow the pocket on every remap.**
   Established because touch-off is performed per pocket at load time. Holds
   under both candidate safety postures.
4. **Pocket assignment is job-scoped.** The app keeps no model of physical
   changer contents. Auto-assign from library preference is the default path;
   dragging is the exception.
5. **Every `T#` is remappable** — no reserved numbers, no tools outside the ATC,
   no allow-list needed.
6. **Destination is a spec**, not a built feature. Execution is a separate
   effort.

### The safety posture fork (unresolved — the key open decision)
1. Rigorous per-tool touch-off at load, or
2. "Auto tool" always on — machine measures at every `T#` call.

Posture 2 makes remapping **self-correcting** (a wrong pocket cannot propagate
into a crash) and its cost is bounded by *block count*, which is far lower than
the operator's intuition of "a lot of wasted time" — but the app currently
under-reports that count (F10), so the tradeoff cannot yet be evaluated with
real numbers.

---

## Prior findings from the code (established before Q1, not from the user)

These are facts read out of the repo at the start of the session. They are
inputs to the grilling, not answers from it.

**F1 — The conflict is already a live correctness bug.**
`gcode_generator._build_blocks` (gcode_generator.py:159-191) groups passes with
`by_tool.setdefault(gp.tool_number, [])` — the raw `T4` string. Two files that
both say `T4` but mean different physical cutters are **merged into one T4
block** and cut with whatever is loaded. Today the app can emit a job that cuts
geometry with the wrong tool.

**F2 — The app already detects this but does not act on it.**
`app._tool_compatibility()` (app.py:241-285) builds a per-`T#` matrix of
`{filename, description, diameter_inches}` usages and sets
`conflict = len({u["description"] for u in usages}) > 1`, plus a job-level
`has_conflict`. So *description string* is the current de-facto identity key.
Nothing blocks generation on `has_conflict` — it is advisory only.

**F3 — Remapping a pocket is structurally narrow.**
A pocket reassignment only needs to rewrite two things: the `T# M06` tool-change
line and the matching `G43 H#` height-offset line. No XYZ/IJ word is touched, so
the "no machining geometry affected" constraint can be *structurally enforced*
rather than merely promised.

**F4 — `H#` is derived from the T number and must move with the pocket.**
`gcode_generator.py:101`: `h_num = re.sub(r"\D", "", tool)` → `T2` becomes `H2`.
If a tool is remapped to a new pocket and H does not follow, the machine applies
the wrong tool-length offset → wrong Z → crash or air-cut. Safety-critical.

**F5 — The capacity check counts `T#` strings, not physical tools.**
`gcode_generator.py:56-71` builds `all_tools` from distinct `tool_number` values
and raises above `tool_capacity` (8). This miscounts in both directions: two
identical cutters at different `T#` count as 2; two *different* cutters sharing a
`T#` count as 1.

**F6 — Tool metadata available per file.**
`gcode_parser.extract_tools` yields `{T#: {description, diameter_inches}}` from
header comments `(T4 = ...)` and inline `(Tool: ... {0.5 inches})`.
`_extract_diameter` parses diameter from the description text via three fallback
patterns.

**F7 — Per-file tool order is already preserved.**
`_build_blocks` walks pass indices 0..max across all parts, so each part's
internal operation order is maintained; same-tool consecutive blocks merge.
Note: `for tool in sorted(by_tool)` sorts by the `T#` *string*, so renumbering
pockets can change block ordering and therefore the tool-change count.

**F8 — `config.json` `tools` is keyed by `T#`** (`T1`, `T2`, `T4`, `T5`, `T99`),
i.e. it is currently a *pocket*-keyed map, not a tool-identity-keyed library.
The presence of `T99` means files reference T numbers well beyond 8.

---

## Q&A log

### Q1 — What makes two tools "the same tool"? (identity key)
- **Asked:** Should identity come from an operator-maintained tool library that
  files match into, or is the file's description string genuinely the best
  record available?
- **Captured:**
  - "We don't have a great tool library right now, but this is something I could
    work on." → No authoritative tool library exists today. Willing to build one.
  - **Open to ideas on implementation** — no preconceived design to honour.
  - Wants the master tool list to live "somewhere that we have access to so when
    we add new tools we can update it" → **shared, updatable location is a
    requirement**, not an app-internal blob.
  - "We also could do a much better job of managing the tools in Fusion/vCarve
    and we plan to." → CAM-side tool hygiene is a parallel effort already
    intended, independent of this map.
  - **Corpus reality: almost all files are vCarve today.** Fusion is the intended
    destination "down the road" but is not the present state. Any solution must
    work on the existing vCarve corpus first.
- **Their question back:** Will a definitive tool library in Fusion solve the
  ambiguity, or is a separate library still needed for this tool to de-dupe?
  → answered in Q1a below; short version: **Fusion helps matching, does not
  remove the need.** CAM library = what the tool *is*; app library = where the
  tool *lives*.
- **Flags:**
  - Can the vCarve tool database (`.vtdb`) be read directly, so the library is
    maintained where tools are already maintained? → **research ticket
    candidate**, do not assume the format. Owner: research subagent.
  - Where the shared library file physically lives (git repo / CNC library
    folder / other) → depends on the .vtdb answer. Fog for now.

### Q2 — Does `G43 H#` follow the pocket number on the SmartShop 2?
- **Asked:** If a tool moves from pocket 4 to pocket 6, is `G43 H6` correct —
  i.e. is the tool-length offset table indexed by pocket? Does anything ever set
  H to something other than the T number?
- **Captured:**
  - **Current workflow: "load a tool in the changer and do a tool touch off
    immediately."** Offset is established at load time, against the pocket.
  - **"We currently don't do anything with H, not intentionally at least."**
    → H is not consciously managed. The generator emits `G43 H{n}` derived from
    `T#` and nobody has been reasoning about it.
  - Operator sees **two candidate safety postures**:
    1. **Rigorous touch-off** of each tool as it is loaded into the changer.
    2. **"Auto tool" left on constantly** — machine touches off whenever a `T#`
       is called, before running that toolpath.
  - Their read: "2 is safest, but it will add a lot of wasted time touching off
    tools that are already correctly measured."
- **Consequence for the feature (important):** because touch-off is done
  *per pocket at load time*, the offset is pocket-indexed either way. So
  **H must follow the pocket on any remap** — this holds under both postures,
  and it is safe to treat `H = pocket` as an invariant rather than a guess.
- **Consequence #2:** posture 2 makes pocket remapping **self-correcting** —
  a stale or mismatched offset cannot propagate because every tool is measured
  fresh at call time. That removes the single biggest safety risk of this
  feature. Posture 1 leaves the correctness of a remap dependent on operator
  discipline at load time.
- **Cost of posture 2 is far smaller than intuition suggests** — see F9/F10.
- **Flags:**
  - Actual wall-clock duration of one touch-off cycle on the SS2 → needed to
    settle posture 1 vs 2. Owner: operator, measured on the machine (task ticket).
  - Confirm whether the SS2 control honours `G43 H#` at all, or ignores it in
    favour of the live measured length → research/task ticket.

### Q3 — Does the changer diagram represent the machine, or the job?
- **Asked:** Is the 8-position diagram a persistent record of what is physically
  loaded (machine-scoped), or derived per job from the loaded files (job-scoped)?
- **Captured:** **Job-scoped. Confirmed — "exactly what I was thinking."**
  - "We don't need to track what's reality." → The app holds **no persistent
    model of physical changer contents**. No sync problem, no drift risk, no
    trusted state that can silently lie.
  - "The operator can choose to match what's really in the toolchanger if they
    want" → matching reality is an **operator convenience, never an app
    obligation**. The drag interaction exists partly to serve this.
  - "I'd prefer to have the tool default to suggested values (which will likely
    match the real tool changer anyway)." → **Auto-assignment from the library's
    preferred position is the default path**; dragging is the exception, not the
    norm. The suggestion is expected to be right most of the time.
- **Design consequences:**
  - "Default position (1–8)" from the original brief is a **preference field on
    the tool in the library** — a hint to the auto-assigner, not an assertion
    about physical reality.
  - Pocket assignment is **job state**, so it must round-trip through the `.cnj`
    save/load format alongside `_placements`.
  - Because assignment is job-scoped and the operator must physically load the
    changer to match, the job **must produce a setup sheet** telling them what
    goes where. `pdf_report.py` is the natural home. (Not yet confirmed with
    the operator — fog.)

### Q4 — Is every tool the files call actually in the changer?
- **Asked:** Does the SS2 have tooling outside the 8 ATC pockets — drill bank,
  aggregate head, saw, or a reserved "manual change" tool number? (`T99` in
  `config.json` prompted this.) Such tools must be excluded from the pocket
  pool, never remapped into it.
- **Captured:**
  - **No manual swaps, no reserved/special tool numbers.** `T99` confirmed as
    leftover test data in `config.json`, not a convention.
  - **"All 8 tools live in the changer for the complete duration of the
    program."** → single class of tool; every tool a job calls is an ATC
    occupant. The pocket pool is a clean 1–8 with no exclusions.
- **Design consequence:** the remapper can treat *every* `T#` it encounters as
  remappable. No allow-list, no reserved range, no "don't touch this one" case.
  This is a significant simplification.
- **Scope boundary noted:** the operator said "for now". Mid-program manual
  reloads (which would let a job exceed 8 distinct tools) are a *possible*
  future, and are **out of scope** for this map — the ≤8 constraint is a hard
  precondition, and exceeding it stays a hard failure.

### Q5 — Where does this map end? (the destination)
- **Asked:** Does the map carry execution (working feature), or stop at the
  decisions (a spec to build from)?
- **Captured:** **Decisions only — the destination is a spec.**
  - "Let's close by building the spec as I'm almost out of time for this session."
  - "We'll start with the spec when it's time to build." → the spec is a
    **handoff artifact into a future build session**, not a document for its own
    sake. Wayfinder's default *plan, don't do* stands; **no override** in Notes.
- **Consequence:** the map is complete when nothing remains to decide. Execution
  is explicitly **out of scope** for this map and becomes a fresh effort.
- **Completeness backstop asked** ("anything we haven't touched?") → operator
  answered "I think that covers everything."

---

## Additional code findings (arising from Q2)

**F9 — Tool changes are counted by *block*, not by distinct tool.**
`_build_blocks` starts a new block whenever the tool differs from the previous
block. If two parts run their passes in different tool orders (part A `[T1,T2]`,
part B `[T2,T1]`), the pass-index walk emits blocks `T1, T2, T1, T2` — four
tool changes for two distinct tools. Block count, not tool count, is the real
cost driver for posture 2.

**F10 — `app.py:232` under-reports tool changes.**
`"tool_changes": max(0, len(ordered_tools) - 1)` computes changes as *distinct
tools minus one*. Per F9 the true figure is `len(blocks)`, which can be strictly
greater. The app therefore currently understates both run time and the cost of
posture 2. This is a reporting bug independent of the new feature, and it must
be fixed before the posture-1-vs-2 tradeoff can be evaluated with real numbers.

---

## Open flags (pending input)

All flags raised in this session were charted as tickets on the Wayfinder map,
[#3](https://github.com/refine-drew/cnc-nest-app/issues/3). Nothing is left
dangling here.

| Flag raised | Owner | Ticket |
|---|---|---|
| Can the vCarve `.vtdb` be read as an identity source? | research | [#4](https://github.com/refine-drew/cnc-nest-app/issues/4) |
| Does the SS2 control honour `G43 H#`? | research / machine | [#5](https://github.com/refine-drew/cnc-nest-app/issues/5) |
| Wall-clock duration of one touch-off cycle | **operator, on the machine** | [#6](https://github.com/refine-drew/cnc-nest-app/issues/6) |
| Tool-change undercount blocks cost evaluation | agent (AFK) | [#7](https://github.com/refine-drew/cnc-nest-app/issues/7) |
| Physical home for the shared tool library | blocked on .vtdb | [#9](https://github.com/refine-drew/cnc-nest-app/issues/9) |
| Setup sheet — assumed `pdf_report.py`, unconfirmed | operator | [#13](https://github.com/refine-drew/cnc-nest-app/issues/13) |

**Session outcome:** destination named (a spec), frontier mapped, map and ten
tickets created, spec drafted at
`docs/tool-changer-pocket-management-spec.md`. Charting resolved nothing by
design — no ticket was worked in this session.
