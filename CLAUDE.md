# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This App Does

CNC Nest is a Flask web app for optimizing CNC cutting layouts on a 5×10 ft dual-rail bed. Users load VCarve G-code files from a library folder, drag-place parts onto A/B rails, get live collision detection, then generate a merged master G-code file that combines all parts using order-of-operations (grouping cuts by tool across all parts).

## Commands

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run (dev server on http://localhost:5000)
python app.py

# Tests
pytest                        # full suite
pytest tests/test_parser.py   # single file
pytest -k "test_collision"    # single test by name
```

No build step, linter, or type checker is configured.

## Architecture

### Backend (Python/Flask)

**`app.py`** — all Flask routes and in-memory application state:
- `_loaded`: dict of parsed parts (filename → `GcodePart`)
- `_placements`: dict of placed parts (placement ID → placement dict with rail, slot, transforms)
- `_instance_counts`: tracks how many times each part has been placed (for unique IDs)
- Key API routes: `/api/load-library`, `/api/place`, `/api/remove-placement`, `/api/generate`
- `/api/save-job` and `/api/load-job` still exist (and are still tested) but have no GUI — the Save/Load Job buttons were removed as unused. Nothing in the app calls them. **Slated for deletion** (2026-08-17, issue #10): a nest is quick enough to rebuild that reloading one was never worth the format. Do not build on them, and do not add pocket assignment to `.cnj` — the pocket map is deliberately in-memory job state, like `_placements`.

**`gcode_parser.py`** — parses `.nc`/`.mmg` VCarve G-code files into `GcodePart` dataclasses. Extracts blank dimensions, material thickness, tool info, XYZ bounding boxes per pass, and validates Z depths.

**`post/syntec 4.cps`** — the REFINE Fusion post-processor. **It is ours to
change**, and it is the only place CAM knowledge can reach the app, because the app
reads posted `.nc` and never CAM source. It lives in `post/` rather than
`Source Data/` for one reason: **`Source Data/` is gitignored**, and a post the app's
parser has a contract with cannot be untracked and unreviewable. Anything else the app
depends on belongs out of that folder too. `writeToolIdentity` emits the identity
comment specified in `docs/tool-changer-pocket-management-spec.md` §6.2.1:

```
(TOOLID T2 VENDOR=AMANA PRODUCT=46170-K FLUTES=3)
(TOOLDESC T2 12 DOWNCUT SPIRAL)
```

`VENDOR`+`PRODUCT` is the identity (`toolId` is document-scoped and must never be
used); the `T#` only ties the line to the header above it. **A blank field is emitted
as `VENDOR=`, never omitted** — empty means the Fusion library entry needs filling in,
missing means the file predates the comment, and only the first is actionable.
`_toolid_fields` preserves that as `""` vs absent; don't collapse them.

**Three constraints from `settings.comments` shape this format, and they are easy to
trip over.** Comments are uppercased, filtered to `" a-z0-9.,=_-"` (so `/` and `"`
vanish from free text), and **truncated at 80 characters**. Truncation *omits* fields,
which would defeat the empty-vs-missing rule, so: the identity line carries no
geometry (`D`/`CR`/`TYPE` are already on the tool-list line above it — repeating them
is what would spend the budget), and the one unbounded field, the description, sits on
its own `TOOLDESC` line where a truncation can only cost free text. Adding a field to
the `TOOLID` line means re-checking that budget.

`TOOLDESC` lands in `cam_description`, deliberately **not** in `description` — the
latter is what `_tool_compatibility` compares, and free text must not move that signal.

**`collision.py`** — rectangle overlap collision detection. Handles the two coordinate systems: A rail uses additive XY offsets; B rail applies 180° rotation (mirroring) around the bed center before offset.

**`gcode_generator.py`** — merges placed parts into a single master G-code. Walks tool passes in order-of-operations sequence (all T1 cuts across all parts, then all T2, etc.), applies coordinate transforms matching `collision.py`, and uses nearest-neighbor sorting to minimize rapid travel.

**`gcode_validator.py`** — gates `/api/generate`. Re-derives the emitted file's
modal state by reading it block by block, the way the control does, and reports
`Finding`s at two severities. **It deliberately shares no state with
`gcode_generator`** — the point is to catch the generator being wrong, which a
checker built from the generator's own bookkeeping cannot do. An `ERROR` means
the file is never written; a `WARNING` lands in `<job>_validation.txt` beside the
output. The split follows what a check can prove: a G49 cutting move is wrong
under every reading, a straight-down plunge may be exactly what was intended.
`G71` is correct for Syntec (the one documented deviation from Fanuc 0M) and must
never be "fixed" to `G21`. The three files from the 2026-08-15 output review are
pinned as fixtures in `tests/test_gcode_validator.py`.

**Job safe Z is the highest clearance any placed part needs, from either of two
sources.** `app._compute_job_safe_z` takes the max of *(thickest material +
`safe_z_clearance_mm`)* and *every part's own `part.safe_z`*, and names which one won
in `driven_by`. `part.safe_z` is the **maximum** `G43 Z` in the file, not the first or
last: Fusion writes one retract per operation and they differ per tool — `18G.nc` has
`Z34.29` for T2 and `Z57.15` for T1, because T1 cuts a feature standing above the
stock top. Reading a single one of them yields a clearance another tool in the same
file crashes through, and the stock rule alone misses it entirely (issue #22). Higher
is always the safe direction here; the cost is rapid seconds.

**`runtime_estimator.py`** — walks a line stream tracking modal units, feedrate
and XYZ, and returns cutting / rapid / tool-change seconds plus
`tool_change_count`.

**Tool changes are counted, never inferred from the tool list.** `_build_blocks`
starts a new block whenever the tool differs from the previous block, so a tool
that recurs at a later pass index is changed *back to*: parts running `[T1, T2]`
and `[T2, T1]` emit `T1, T2, T1, T2` — two tools, four changes. Distinct-minus-one
understated both the count and the run time (issue #7).
`gcode_generator.block_tool_sequence` is the single source: it and `_build_blocks`
walk the same `_iter_pass_groups`, and
`test_block_tool_sequence_matches_built_blocks` pins them together. `tool_sequence`
/ `tool_count` stay **distinct** — those drive `tool_capacity` and the
compatibility matrix, which are about how many pockets the changer needs.

Per-part `GcodePart.runtime_seconds` **excludes** tool-change time
(`tool_change_seconds=0.0` at the parse call). A part's own change count means
nothing once the generator merges same-tool passes across parts, so the cost is
charged once per emitted block at job level instead. The `.txt` report reads the
finished file and is the precise figure; `_compute_job_stats` is the live
approximation.

**The per-change cost is measured, and it is two numbers, not one.** Timed on the
SS2 2026-08-17 (issue #6): the swap alone is 27 s, swap plus touch-off is 57 s, so
`TOOL_SWAP_SECONDS = 27.0`, `TOUCH_OFF_SECONDS = 30.0`, and
`DEFAULT_TOOL_CHANGE_SECONDS` is their sum. Keep them split. **The posture is now
decided — "auto tool" on, every job (issue #8, 2026-08-17) — so the default is the
chosen posture and the estimate needs no flag.** The split stays because it is the
only arithmetic that shows what that choice costs (`TOUCH_OFF_SECONDS ×
tool_change_count`, 4–7.5 min on a typical job, every run); collapsing the two
constants deletes the record of an accepted cost.

Touch-off is charged on **every** `T# M06`, not once per distinct tool: with "auto
tool" on, the control measures at every call, so a tool the pass-index walk
returns to is measured again. The default therefore prices the always-on posture.
Price the other one — touch off once as each tool is loaded, auto tool off — by
passing `tool_change_seconds=TOOL_SWAP_SECONDS`; that moves the 30 s out of the cut
cycle into setup, and the gap between postures is exactly
`TOUCH_OFF_SECONDS × tool_change_count`. On a typical 8–15 block job that is 4–7.5
minutes, which is what #8 is actually trading against.

**`H` is assumed to be honoured, and `H` always equals `T`.** Decided
2026-08-17 (issue #5): Syntec documents `H` as an index into a touch-off register,
and we assume ShopSabre has not replaced that with a live measured length. So
`gcode_generator` derives `H` from the tool number (`T2` → `G43 H2`) and
`gcode_validator._check_g43` rejects any mismatch at **`ERROR`**. Both follow from
the assumption; don't soften either while it stands. The assumption was taken in
the strict direction on purpose — if `H` really is honoured, a wrong one cuts at
the wrong Z, and if it is inert, emitting the matching number costs nothing. The
machine check (run `G43` against a deliberately wrong register in air and watch
whether Z shifts) was **declined** on 2026-08-17, after #8 chose the always-on
posture. The one thing it would have shown — whether "auto tool" writes into the `H`
register — is therefore **unobserved, and the "self-correcting" property must not be
cited as the safety basis for anything.** With it unproven, `H`-follows-pocket carries
the full load alone: keep the strict reading, and make the #12 geometry test cover
`H` as tightly as geometry. See
`docs/tool-changer-pocket-management-spec.md` §6.1. Touch-off is per **pocket**,
not per cutter, so when pocket remapping lands, `H` moves with the pocket.

**`tool_library.py`** — simple tool registry. Resolves tool diameters from file headers or user-supplied overrides.

**Pocket assignment: the assigner makes no arbitrary choices.** Decided 2026-08-17
(issue #10), specified in `docs/tool-changer-pocket-management-spec.md` §3.2.1, §3.4
and §3.5; not built yet. It seeds each tool's declared default slot and stops —
**no tie-break** (two tools declaring one pocket both sit in it, visibly and
invalidly), **no fill rule** (a tool with no declared slot is staged, never dropped
into the lowest free pocket), **no write-back** (a drag is a job-scoped override; the
default slot is *prescriptive*, and re-proposing it next job is the intended nag).
Nothing is refused at placement — not even a 9th tool, because identity merging can
still lower the count — and **only generation is gated**, on three rules: every file
tool resolved, every resolved tool in exactly one pocket, no pocket holding two.
Determinism is therefore a *consequence*, not a rule to enforce; don't add a
tie-break "for stability" — that would be the one thing that breaks it.

Two traps for whoever builds it. **The assigner must read identity-ordered data,
never the remapped `T` numbers**: `_build_blocks` sorts by the `T#` string, so
reading post-remap numbers makes the output depend on an assignment that depends on
the output. And **the Generate gate already exists** — `static/job.js:36` and
`app.py:661` both block on `_tool_compatibility`'s `has_conflict`. That gate is not
missing, its *signal* is unsound: `conflict` fires only when descriptions **differ**,
so two different cutters sharing a stale identical string are never flagged (the
library has this case — `T2` and `T9` post `End Mill {0.5 inch}` byte-for-byte in one
file). Re-point the gate; don't add another.

**`config.py`** — loads/saves `config.json`. Config defines library paths (a list of candidates; the first that exists locally wins), output path, tool definitions, bed dimensions, per-rail geometry (`advanced.rails` — see Coordinate Systems), `tool_capacity` (generation is blocked above it), fence-origin offsets, safe Z, and slot positions.

### Frontend (Vanilla JS + Canvas)

No framework, no bundler. Files in `/static/`:

- **`bed.js`** — HTML5 Canvas renderer. Draws the bed, rails, slots, placed parts with color coding, and ghost preview during drag. This is the largest and most complex frontend file.
- **`sidebar.js`** — library tree (left) and placement tray (right) UI
- **`placement.js`** — drag-and-drop placement logic, communicates with `/api/place`
- **`job.js`** — the Generate G-code button
- **`config.js`** — settings panel, reads/writes `/api/config`

### Data Flow

1. User picks library folder → `/api/load-library` → `gcode_parser` → populates `_loaded` → sidebar tree
2. User drags part to bed slot → `/api/place` → `collision.py` validates → adds to `_placements` → bed canvas redraws
3. User clicks Generate → `/api/generate` → `gcode_generator` merges all `_placements` → writes `.nc` + `.txt` report

Placements live only in memory for the life of the server process; there is no
GUI path to persist or restore a layout.

### Coordinate Systems

This is the trickiest part of the codebase.

The A and B rails are **independent fixturing systems** at opposite ends of the
machine, loaded separately by the operator. They are not two views of one formula:
measured on the SS2, slot numbers count toward machine Y **max** on B and toward
Y **min** on A. Each rail therefore carries its own datum and directions in
`collision.RAIL_DEFAULTS` (overridable per rail via `config.advanced.rails`).

The defaults come from four machine readouts — X to the rail corner, Y to the slot
edge, two slots per rail so each direction is observed rather than assumed:

```
A0 (134.628, 3034.700)   A13 (134.628, 2704.500)   -> -330.200 mm per 13"
B0 (1534.160,   88.300)  B13 (1534.160,  418.500)  -> +330.200 mm per 13"
```

`tests/test_collision.py::test_rail_defaults_match_measured_machine_positions`
pins those readouts. Re-measure and update the test, the comment in
`collision.py`, and `config.json` together.

| key | meaning |
|---|---|
| `x_mm` | machine X of the rail corner the blank registers against |
| `slot0_y_mm` | machine Y of the slot-0 edge (the blank's datum edge) |
| `slot_dir` | `+1` if increasing slot number moves toward machine Y max, else `-1` |
| `x_dir` | `+1` if the blank extends from `x_mm` toward machine X max, else `-1` |

The transform, for both rails:

```
machine X = x_mm      + x_dir    * vcarve_Y      (vcarve_Y = across the bed)
machine Y = slot_mark + slot_dir * vcarve_X      (vcarve_X = along the rail)
slot_mark = slot0_y_mm + slot_dir * slot_inches * 25.4
```

**`collision.slot_mark_y` / `collision.rail_geom` are the single source of truth.**
`collision.py`, `gcode_generator._transform_params`, `app._transform_segments`,
`app.api_slots` and `static/bed.js` all derive from them. Do not re-derive slot
positions anywhere else — a part simulated somewhere it does not cut is how you
crash a cutter. (`bed.js` gets the resolved geometry from `/api/slots`; placement
blanks arrive pre-computed as `placement.blank` so the canvas never recomputes them.)

Slot positions are deliberately **independent of `bed_x_mm` / `bed_y_mm`**, which
only drive canvas/PDF extents and the utilization figure.

Those two are **no longer provisional.** Confirmed 2026-08-17: the `machine_travel`
extents below *are* the table's corners, so `bed_x_mm` / `bed_y_mm` are now the far
corner in machine coordinates — **1606.4992 × 3098.0126**, replacing the inferred
1668.788 × 3123.0. (The old pair came from the rails being antisymmetric about
Y = 1561.5 — true, and it fixes the rails' shared centre, not the table's extent.)

The canvas still draws from machine 0 to that far corner, so the drawn rectangle
includes a sliver — X < 61.493, Y < 24.994 mm — that is **not table**. Nothing can
be placed there (`check_envelope` gates on `machine_travel`), and utilization is
computed against the true table area from those extents, not against the drawn
rectangle. Keep it that way: the drawn extent is a frame, the travel box is the
truth.

#### Machine envelope

Separate from `bed_x_mm`/`bed_y_mm`: `advanced.machine_travel` holds the outer
extents of the **machinable surface** and `advanced.edge_margin_in` the keep-out
from them. `collision.check_envelope` gates placement on both.

Measured off the machine layout drawing on 2026-08-17, in inches. The X and Y
figures are the **table's own corners** — the machinable surface and the table are
the same rectangle:

```
X  2.421 → 63.248      (60.827" usable)     ->   61.4934 → 1606.4992 mm
Y  0.984 → 121.969     (120.985" usable)    ->   24.9936 → 3098.0126 mm
Z  not checkable                            ->   null (skipped)
```

**Z stays `null` on purpose, and #19 closed on that basis** (2026-08-17). The
machine's Z travel tops out at machine Z 0 and its useful floor is whatever the job
needs, but the envelope check reads **work coordinates** out of the file: X and Y are
comparable because the rail transform maps them into the machine frame, and Z is not,
because nothing here knows the `G54` Z offset the operator touched off. Comparing a
file's `Z57.15` against machine Z 0 would be an arithmetic error dressed as a safety
check. Z depth is already validated where it can be — `gcode_parser.validate_z`
against material thickness. Do not "finish" the envelope by adding a Z bound unless
the G54 offset becomes readable.

**The two axes are checked differently, because they fail differently.** This
asymmetry is the whole design; flattening it breaks the app in one direction or
the machine in the other.

| | bound on | margin | why |
|---|---|---|---|
| **X** | cutter **edge** (toolpath inflated by largest tool radius) | yes | a **hard stop** sits just outside each end — overrunning it is a crash |
| **Y** | tool **centre** | no | both ends are **open**; the tool may hang off the surface, so `y_max + radius` and `y_min − radius` are both fine |

The X inflation is the point. Programmed coordinates are **tool centre** with no
cutter comp (`G40`, computer-compensated toolpaths), so the cutting edge stands
one radius outside every number in the file. On Y that same radius is exactly
what is *allowed* to hang over, which is why the bound sits on centre there.

`collision.Y_FLOOR_MM = 0.0` is the one hard bound on the open axis: the machine
does not travel below Y 0, so the cutter **edge** must clear it. With `y_min` at
24.994 that only bites for a cutter over ~2" diameter — a backstop, not the
working limit.

Checking Y like X is what made **A slot 0** unusable (datum Y 3034.700);
`test_a_slot_zero_is_usable_with_the_shipped_config` guards it. Checking X like
Y would license a real crash.

Do not re-derive these from the park block. Its `G00 G53 X0 Y3048` is the only
machine-frame coordinate in the output, and both numbers were read as limits
once. `Y 3048` is a position the machine *reaches* — a lower bound on travel,
and exactly 120.000", a design round number; the surface actually runs to
3098.013 and travel runs past that to the tool changer. `X 0 → 1524`
contradicted the measured B rail corner at 1534.160 that every B-rail part cuts
inboard from; the surface reaches 1606.499.

`gcode_validator` mirrors this per-axis policy (`_MARGINED_AXES`). It reads
tool-centre coordinates out of the finished file and has no radius to inflate
them by, so on X it is one radius **more permissive** than the placement gate —
a backstop, not the primary guard. That direction is deliberate: the stricter
check must be the one that runs first.

`_max_tool_radius` reads `part.tools[...]["diameter_inches"]` straight from the
parsed file, not the resolved `ToolLibrary`.

**The Fusion side of that is fixed (#20, 2026-08-17).** The post writes
`(T2 D=12.7 CR=0. - ZMIN=0. - FLAT END MILL)` rather than `{0.5 inches}`, and
`extract_tools` matched only the VCarve shape, so all 9 Fusion files in the library
parsed to **no tools at all** — diameter 0, and therefore an X envelope check and a
tool-radius collision check that both under-inflated on the *primary* corpus. The
worst case was `39x35.nc`'s 59.728 mm form mill: 1.176" of radius per side that the
envelope check owed and did not add. `FUSION_TOOL_HEADER_PATTERN` now reads
`D`/`CR`/`TAPER` and the tool type. Re-checked across the library afterwards, **no
placement changed** — the correct radii do not push any current part over the bound,
so the hole closed at zero cost.

Two things about that parse are load-bearing, not incidental:

- **`D=` is millimetres and must be converted.** The file's own units word decides
  (`file_is_inch`, first `G70`/`G71`/`G20`/`G21` in a non-comment line, metric
  default). Reading `12.7` as inches is a 25.4× under-inflation — the crash
  direction. The Fusion branch also `continue`s before `_extract_diameter`, whose
  bare-decimal fallback would do exactly that.
- **`ZMIN` is excluded from `description`.** It is the job's Z range, not a property
  of the cutter, so one physical tool posts `ZMIN=0.` in one file and `ZMIN=-19.05`
  in another. `app._tool_compatibility` flags a conflict when descriptions for one
  `T#` differ, so carrying `ZMIN` would make a tool conflict with itself and block
  Generate on two compatible files. The description is built from the stable fields
  (`FLAT END MILL D=12.7 CR=0.`) — type alone would collapse every ½" and ¼" flat
  mill onto one string, which is the dangerous direction.

**Still true, and still the destination:** the identity library becomes the diameter
authority (spec §3.5.2), because a file whose header is absent or wrong still needs a
true radius, and because the parsed `CR=`/`TAPER=` are then *verifiable* against a
declaration rather than trusted.

**Every parse assumes the file's origin is the blank's registration corner** —
the VCarve convention, where all coordinates are positive. Nothing checks it.
Turning the envelope on immediately caught two library files that violate it:
`1001-combined.nc` and `1001-combined-Zbottom_1.nc` are hand-merged Fusion
output with `G54` at the blank **centre**, so their toolpaths run
−195.798 → +208.369 across the bed. Placed on the A rail with the corner-datum
transform, the cutter lands at machine X −61.2 — 122 mm past the hard stop.
Both are now rejected at every slot on both rails, which is correct but reads as
a placement failure rather than the file-origin mismatch it is.

**#23 closed as a CAM defect, not an app one** (2026-08-17): those two files are junk
hand-merged output and get re-posted from Fusion with a corner origin. The corner-datum
assumption **stands as a precondition of the library** — do not add centre-origin
detection or a per-file origin override to accommodate a mis-posted file. The rejection
is the app working. Its diagnostic wording is still the weak part, since it names a
placement collision rather than the origin mismatch.

#### Arc planes

The X↔Y swap moves arcs between the two vertical planes, so the **plane word is
part of the transform**, not a passenger. VCarve emits lead-in/lead-out ramps as
vertical arcs — G19 (file YZ) when the ramp runs along file Y, G18 (file XZ) when
along file X — and every one of them must be rewritten:

| file | machine | offsets |
|---|---|---|
| G17 (XY) | G17 | I↔J swap |
| G18 (XZ) | **G19** (YZ) | I→J, K unchanged |
| G19 (YZ) | **G18** (XZ) | J→I, K unchanged |

Arc direction follows the plane's **normal** axis, not its in-plane axes — an arc
reverses only when its normal reverses. Since the normal is an axial vector it
also picks up det(Jacobian). That is why the rails disagree per plane: G17 never
flips on either rail, G19 flips on A only, G18 flips on B only. Emitting a file
`G19` unchanged next to a swapped `X` word is an illegal block; the control
alarms mid-cut (it did, on 2026-08-14).

`gcode_generator._transform_body` tracks the modal plane across a pass and
restores G17 at the end of any body that leaves it vertical, so the travel sort
is free to reorder segments. `gcode_parser.extract_file_segments` tracks it too:
a G18/G19 arc's footprint is the straight lateral line, and flattening it as an
XY arc invents a bulge the cutter never makes.

Collision detection compares **both rails against each other**, not just parts
sharing a rail: everything is in machine coordinates. The rail datums are 1399.5 mm
(55.1") apart, so two parts whose across-bed dimensions sum to more than that
overlap in X and can genuinely interfere.

SS2 = Laguna SmartShop2 Pro 5x10