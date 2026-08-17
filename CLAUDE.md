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
`DEFAULT_TOOL_CHANGE_SECONDS` is their sum. Keep them split — the safety posture
(issue #8) switches the touch-off on and off, and collapsing them back to one
constant deletes the only arithmetic that decides it.

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
whether Z shifts) was never run, and it still matters for one thing only: whether
"auto tool" writes into the `H` register, which is the entire basis of the
self-correcting safety posture in
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
now only drive canvas/PDF extents and the utilization figure. Those two are still
**provisional**: the readouts confirm the rails are antisymmetric about
Y = 1561.5 (`A_Y(slot) + B_Y(slot) = 3123.0` at every slot), but that fixes the
rails' shared centre, not the table's extent. Treating the centre as the bed centre
is what yields 1668.788 × 3123.0. Replace both with real table dimensions when
available; nothing in the cut path depends on them.

#### Machine envelope

Separate from `bed_x_mm`/`bed_y_mm`: `advanced.machine_travel` holds the outer
extents of the **machinable surface** and `advanced.edge_margin_in` the keep-out
from them. `collision.check_envelope` gates placement on both.

Measured off the machine layout drawing on 2026-08-17, in inches:

```
X  2.421 → 63.248      (60.827" usable)     ->   61.4934 → 1606.4992 mm
Y  0.984 → 121.969     (120.985" usable)    ->   24.9936 → 3098.0126 mm
Z  unmeasured                               ->   null (skipped) — issue #19
```

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
parsed file, not the resolved `ToolLibrary`. The Fusion post writes `(T2 D=12.7
… )` rather than `{0.5 inches}`, so those files parse to diameter 0 and both the
envelope check and the existing tool-radius collision check under-inflate them.
The fix is decided but not built: **the identity library becomes the diameter
authority**, so a Fusion file that parses to 0 still inflates correctly once its
tool resolves (spec §3.5.2). That routes around #20 for safety purposes without
waiting on the parser.

**Every parse assumes the file's origin is the blank's registration corner** —
the VCarve convention, where all coordinates are positive. Nothing checks it.
Turning the envelope on immediately caught two library files that violate it:
`1001-combined.nc` and `1001-combined-Zbottom_1.nc` are hand-merged Fusion
output with `G54` at the blank **centre**, so their toolpaths run
−195.798 → +208.369 across the bed. Placed on the A rail with the corner-datum
transform, the cutter lands at machine X −61.2 — 122 mm past the hard stop.
Both are now rejected at every slot on both rails, which is correct but reads as
a placement failure rather than the file-origin mismatch it is. Issue #23.

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