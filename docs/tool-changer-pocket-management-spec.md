# Tool Changer Pocket Management — Spec

**Status: incomplete by design.** Sections 1–5 are settled and safe to build
against. Section 6 lists the decisions that are still open. Do not start
implementation until at least the safety-posture decision (#8) and the
identity-matching decision (#9) have closed — everything structural depends on
them.

> **2026-08-15 — Fusion is now the primary CAM tool.** VCarve is retained for
> simple one-offs only, and **the REFINE post-processor is editable**. This map
> was charted when Fusion was "down the road", so read §3.1 with that in mind:
> its conclusion that identity can never come from the file was established
> against VCarve, whose post we do not control. [#21](https://github.com/refine-drew/cnc-nest-app/issues/21)
> tests whether the Fusion post can carry a stable identity token, which would
> qualify — not overturn — that section for the primary corpus.

- Map: [Tool changer pocket management — spec](https://github.com/refine-drew/cnc-nest-app/issues/3)
- Discovery session: `brainstorms/2026-08-12-tool-changer-pocket-management.md`
- Reference Fusion output: `~/Documents/Windows Bridge Folder/18G.nc`
- Date: 2026-08-12, revised 2026-08-15

---

## 1. The problem

VCarve posts a tool as `T4`. That single token carries **two different meanings
at once**:

- *identity* — "a 1/2 inch downcut spiral"
- *pocket* — "position 4 in the tool changer"

Tool position is currently managed as a property of the tool in the CAM app, so
two programs authored at different times routinely use **different cutters in
the same pocket number**. Nesting them into one job is therefore unsafe today,
and the workaround — opening every VCarve file and shuffling tool positions by
hand — does not scale.

### 1.1 This is already a live defect, not just a missing feature

`gcode_generator._build_blocks` groups passes with
`by_tool.setdefault(gp.tool_number, [])` — the raw `T#` string. Two files that
both say `T4` while meaning different cutters are **merged into a single `T4`
block** and cut with whatever is physically loaded.

`app._tool_compatibility()` already detects this: it compares descriptions per
`T#` and sets `conflict` / `has_conflict`. **Nothing blocks generation on it.**
The app can emit a job that cuts geometry with the wrong tool right now.

This should be treated as a bug with a safety consequence, independent of
whether the full feature ships.

---

## 2. Destination

Any combination of source files using **8 or fewer distinct physical tools** can
be nested and run as a single job, with the app assigning and reassigning changer
pockets — **without altering any machining geometry**.

---

## 3. The domain model (decided)

The feature exists to **split identity from pocket**.

| Concept | Owner | Lifetime |
|---|---|---|
| **Tool identity** — what the cutter *is* | Tool library | Persistent, shared, operator-maintained |
| **Pocket** — where it *sits* for this run | The job | Job-scoped, recomputed per job |
| **Preferred pocket** — where it *usually* sits | Tool library | A hint to the auto-assigner, nothing more |

### 3.1 Identity comes from a library, never from the file

No tool library exists today; the operator will build one. The library — not the
`.nc` file — is the identity authority. Files *match into* it.

Description strings must not be the identity key, even though `app.py:280` uses
them today. They are free-form post-processor output: `"1/2 inch End Mill"`,
`"0.5 EM"`, and `"1/2 EndMill (new)"` are one cutter under three identities. The
inverse failure is worse — a stale copy-pasted description makes two different
cutters look identical, and *that* direction merges them into one block and cuts
with the wrong tool. Every matching rule must be biased against it.

Diameter alone is insufficient: a 1/4" upcut, downcut, and compression spiral are
all 0.25" and must never merge.

**Nor can identity come from VCarve's tool database** — resolved in [#4](https://github.com/refine-drew/cnc-nest-app/issues/4).
`.vtdb` is readable SQLite, but the only per-tool number it holds is the ATC
carousel position — the same `T#` that already collides — and no row key reaches
the posted file. Measured across the 26-file library: `T4` denotes four different
cutters, `T2` and `T9` post byte-identical strings in one file, and 3 files yield
no parseable diameter at all. The library must therefore be **operator-declared**,
keyed independently of `T#`, and must carry a **geometry class** plus a
many-to-one **alias list** of the raw strings seen in files — which is what lets
one library serve VCarve and Fusion output without a second mechanism.

**A Fusion tool library will not remove the need for this.** The app reads posted
`.nc`, not CAM source, so CAM knowledge reaches the app only through what the
post-processor writes into the file. The corpus is **not** almost entirely VCarve —
9 of 26 library files are already Fusion-posted, and the parser currently reads
**no tools whatsoever** from them ([#20](https://github.com/refine-drew/cnc-nest-app/issues/20)).
A future Fusion library does not retroactively fix existing files. Most
fundamentally, CAM records *what a tool is*; this app must record *where it
lives*. Good CAM hygiene makes matching reliable — it does not make the app-side
library unnecessary.

### 3.2 Pocket assignment is job-scoped

The app holds **no model of the physical changer contents**. There is no state to
sync, no drift, and no record that can silently lie about what is in the machine.

Assignment is derived per job from the loaded files, defaulting to each tool's
preferred pocket. Dragging is the exception path, not the normal one — the
operator's expectation is that suggested values "will likely match the real tool
changer anyway". Matching physical reality is an operator convenience, never an
app obligation.

Because assignment is job state, it **must round-trip through the `.cnj` save/load
format** alongside `_placements`.

### 3.3 Every `T#` is remappable

There are no reserved tool numbers, no manual-change convention, and no tooling
outside the ATC. All 8 tools live in the changer for the full duration of a
program. The `T99` entry in `config.json` is leftover test data, not a convention.

No allow-list, no reserved range, no exclusions. This is a significant
simplification and it should stay that way — see §7.

---

## 4. Remap mechanics (decided)

A pocket reassignment rewrites **exactly two things**:

1. the tool-change line — `T4 M06` becomes `T6 M06`
2. the height-offset line — `G43 H4` becomes `G43 H6`

No `X`, `Y`, `Z`, `I`, `J`, `R`, or `F` word is touched. This is what makes the
"no machining geometry affected" constraint *structurally enforceable* rather
than a promise in a comment.

### 4.1 H must follow the pocket — invariant

`gcode_generator.py:101` derives the H number from the T number:
`h_num = re.sub(r"\D", "", tool)`.

The shop's workflow is to load a tool into the changer and touch it off
immediately. **The offset is therefore established per pocket, not per cutter** —
so the offset table is pocket-indexed, and a remap that moves `T` without moving
`H` applies the wrong tool-length offset. Wrong Z. Crash or air-cut.

This invariant holds under both candidate safety postures, so it is safe to build
against before #8 resolves.

**2026-08-17 — #5 is resolved by assumption: the control honours `G43 H#`.**
Syntec's manual defines `H` as a register index; the machine test that would
confirm ShopSabre has not modified that has not been run, and the app now assumes
it has not. The assumption is deliberately in the **strict** direction — the world
where `H` is honoured is the world where getting it wrong cuts at the wrong Z, so
building for it is safe whether or not the live measured length would have
overridden it. The invariant is therefore **load-bearing, not merely tidy**, and
the geometry guarantee (#12) has to carry the full weight: `H` is the one word
outside the geometry set whose value can put the cutter at the wrong depth.

`gcode_validator._check_g43` enforces it at `ERROR` — an `H` that does not match
the preceding `T` blocks the file from being written. That severity is a
consequence of this assumption and must not be softened to a warning while it
stands.

### 4.2 Order of operations is preserved

`_build_blocks` walks pass indices `0..max` across all parts, so each file's
internal tool order is already maintained, and consecutive same-tool blocks
already merge across parts to minimise tool changes. **The feature must not
change this.**

One caveat to carry into implementation: `for tool in sorted(by_tool)` sorts by
the `T#` *string*, so renumbering pockets can change block ordering and therefore
the tool-change count. Whether that reordering is acceptable is open — see #12.

---

## 5. Counting is currently wrong (decided: must be fixed)

`app.py:232` computes `"tool_changes": max(0, len(ordered_tools) - 1)` — distinct
tools minus one. The true figure is `len(blocks)`, which is strictly greater
whenever a tool recurs: part A running `[T1, T2]` and part B running `[T2, T1]`
produces blocks `T1, T2, T1, T2` — four changes for two tools.

The app therefore **understates tool changes today**, which understates run time
and understates the cost of the always-on-touch-off posture. This must be fixed
before the posture tradeoff (#8) can be evaluated with real numbers.

---

## 6. Open decisions — these block the build

Frontier (takeable now):

| # | Ticket | Type | Why it matters |
|---|---|---|---|
| [#6](https://github.com/refine-drew/cnc-nest-app/issues/6) | Measure one touch-off cycle on the SS2 | task | **Needs the machine.** Turns the posture fork into arithmetic |
| [#7](https://github.com/refine-drew/cnc-nest-app/issues/7) | Fix the tool-change undercount | task | Nothing about posture cost is trustworthy until this is honest |
| [#20](https://github.com/refine-drew/cnc-nest-app/issues/20) | Parse Fusion tool headers | task | 9 of 26 library files yield no tool identity at all; identity matching has nothing to match on for them. **Now the primary path, not a compatibility patch** |
| [#12](https://github.com/refine-drew/cnc-nest-app/issues/12) | Define the no-geometry-change guarantee and its proving test | grilling | Turns the hard constraint into a test |

Resolved:

| # | Ticket | Answer |
|---|---|---|
| [#4](https://github.com/refine-drew/cnc-nest-app/issues/4) | Can tool identity come from the VCarve tool database? | **No** — see §3.1. The library is hand-maintained, with aliases |
| [#5](https://github.com/refine-drew/cnc-nest-app/issues/5) | Does the SS2 control honour `G43 H#`? | **Assumed yes** (2026-08-17, operator's call) — see §4.1. Syntec documents `H` as a register index; the machine check was not run. The assumption is the strict branch, so `H`-follows-pocket is load-bearing and the geometry test must cover `H` |
| [#21](https://github.com/refine-drew/cnc-nest-app/issues/21) | What stable per-tool identity can the Fusion REFINE post emit? | **`vendor` + `productId`**, or an explicit `comment`. No library-wide GUID exists; `toolId` is document-scoped and must not be used. Identity therefore rests on operator-maintained Fusion library fields — which puts CAM tool hygiene on the critical path, contesting an out-of-scope ruling. See §6.2 |

Blocked:

| # | Ticket | Blocked by |
|---|---|---|
| [#8](https://github.com/refine-drew/cnc-nest-app/issues/8) | Choose the safety posture | #6, #7 |
| [#9](https://github.com/refine-drew/cnc-nest-app/issues/9) | Define tool identity matching, and no-match behaviour | #20, #21 |
| [#10](https://github.com/refine-drew/cnc-nest-app/issues/10) | Define pocket auto-assignment and conflict resolution | #9 |
| [#11](https://github.com/refine-drew/cnc-nest-app/issues/11) | Tool changer interface: 8 pockets, drag to reassign | #10 |
| [#13](https://github.com/refine-drew/cnc-nest-app/issues/13) | Define the operator setup sheet | #10 |

### 6.1 The decision that matters most

**#8, the safety posture**, reshapes the safety argument for the whole feature:

1. **Rigorous per-tool touch-off at load** — every remap's correctness rests on
   operator discipline at load time.
2. **"Auto tool" always on** — the machine measures at every `T#` call, so a
   wrong pocket assignment or stale offset **cannot propagate into a crash**.
   Remapping becomes self-correcting, which deletes the single largest safety
   risk in the feature.

**Resolving #5 by assumption does not settle posture 2's safety claim.** Assuming
the control honours `H` says what the *program* means; it says nothing about where
ShopSabre's "auto tool" writes the length it measures at `T#` time. If that write
lands in the `H` register, posture 2 is self-correcting as described. If it
bypasses `H`, then under our assumption the program still reads the register — and
a stale register beats a fresh measurement. Posture 2's whole advantage rests on
that unobserved detail, so #8 cannot lean on "self-correcting" without checking
it on the machine. Posture 1 needs no such check: it is correct under the
assumption as it stands.

The operator's objection to posture 2 is wasted time. That cost is likely much
smaller than intuition suggests: order-of-operations merging already calls each
tool **once per job, not once per part**, so nesting twelve parts that share a
1/4" upcut costs *one* touch-off for that tool. The real cost is bounded by
*block count* — order of 8–15 per job. But the app cannot currently prove this,
because of §5. Fix the count, measure the cycle, then decide.

### 6.2 Fusion identity, and a scope ruling that no longer holds

[#21](https://github.com/refine-drew/cnc-nest-app/issues/21) settled what the post
*can* carry. Autodesk's `Tool` class exposes no library-wide GUID:

- **`toolId` must not be used.** It is documented as unique only *within a
  Fusion/Inventor document*, so the same physical cutter in two designs yields two
  ids. It is the plausible-looking wrong answer.
- **`vendor` + `productId`** is the real key — exact, not fuzzy — with `comment`
  as the operator-controlled fallback.
- Geometry (`type`, `cornerRadius`, `numberOfFlutes`, `diameter`) discriminates but
  cannot key: upcut, downcut and compression spirals are all
  `TOOL_MILLING_END_FLAT` at the same diameter and corner radius.

Recommended emission, for #9 to accept or replace:

```
(TOOLID T2 VENDOR=Amana PRODUCT=46170-K FLUTES=3 TYPE=TOOL_MILLING_END_FLAT D=12.7 CR=0.)
```

**The consequence is a scope problem.** `vendor` and `productId` are operator-
maintained fields in the Fusion tool library. §7 currently rules *CAM-side tool
hygiene* out of scope as "a parallel effort". That was decided when VCarve was
primary and CAM data could not reach the app at all. If identity keys on
`productId`, hygiene is **a precondition of this feature, not a parallel track** —
a blank or reused field produces exactly the wrong-tool merge the map exists to
prevent. The map must either adopt "every physical cutter has a unique, populated
`vendor`+`productId`" as a stated precondition, or accept `comment`-based identity
and say so plainly. **Operator's call — not re-scoped here.**

### 6.3 Recommended shape for the geometry guarantee (#12)

Generate the master G-code twice from identical placements — once with the
identity pocket map, once remapped — and assert the outputs are **token-for-token
identical except at `T#` and `H#`**. Any accidental geometry change becomes a test
failure by construction, rather than something a reviewer has to notice.

---

## 7. Out of scope

- **Building the feature.** This map's destination is the spec; implementation is
  a separate effort started from it.
- **Mid-program manual tool reloads** to exceed 8 distinct tools. `≤8` is a hard
  precondition; exceeding it stays a hard failure. (The operator said all 8 tools
  live in the changer for the full program "for now" — if that changes, this
  becomes a fresh effort, not a resumption.)
- **CAM-side tool hygiene in Fusion/VCarve.** A parallel effort already intended,
  independent of this map.

## 8. Not yet specified

- **Schema migration.** `config.json`'s `tools` map is keyed by `T#` — i.e. by
  *pocket* — and must become keyed by tool identity. `.cnj` job files must carry
  the pocket assignment. Both migrate existing saved data.
- **Over-capacity behaviour after de-dup.** Resolving identity may *lower* a
  job's distinct-tool count (two `T#`s that are one cutter) or *raise* it (one
  `T#` that is two cutters). What the app does at >8 is unclear until counting is
  trustworthy.
- **Pinning** a tool to a pocket the auto-assigner may not move.
- **Fusion-era identity** — what a Fusion post could write to make matching
  unambiguous, and whether specifying it now helps or prematurely constrains the
  CAM migration.

---

## 9. Code map

| File | Role in this feature |
|---|---|
| `gcode_generator.py:159` `_build_blocks` | Groups by raw `T#` — the merge defect lives here |
| `gcode_generator.py:101` | Derives `H` from `T` — must move with the pocket |
| `gcode_generator.py:56-71` | Capacity check counts `T#` strings, not physical tools |
| `app.py:241` `_tool_compatibility` | Already detects the conflict; advisory only |
| `app.py:232` | Under-reports tool changes (§5) |
| `gcode_parser.py:137` `extract_tools` | Source of per-file `{T#: {description, diameter}}` |
| `tool_library.py` | Today a `T#`→diameter registry; becomes the identity library |
| `tool_library.py:39` `find_unknown_tools` | Existing block-on-unresolvable pattern to mirror |
| `config.json` `tools` | Pocket-keyed today; must become identity-keyed |
| `static/bed.js`, `static/sidebar.js` | Where the changer UI has to live |
| `pdf_report.py` | Natural home for the setup sheet |
