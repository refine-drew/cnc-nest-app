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
- `/api/save-job` and `/api/load-job` still exist (and are still tested) but have no GUI — the Save/Load Job buttons were removed as unused. Nothing in the app calls them.

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

**`tool_library.py`** — simple tool registry. Resolves tool diameters from file headers or user-supplied overrides.

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

Separate from `bed_x_mm`/`bed_y_mm`: `advanced.machine_travel` holds the **axis
travel limits** and `advanced.edge_margin_in` the keep-out from them.
`collision.check_envelope` rejects a placement whose footprint — the toolpath
**inflated by the part's largest tool radius** — crosses `limit − margin`.

The inflation is the point. Programmed coordinates are **tool centre** with no
cutter comp (`G40`, computer-compensated toolpaths), so the cutting edge stands
one radius outside every number in the file.

**No limit is measured yet, so all six ship as `null` and the check is dormant**
— an axis with `null` limits is skipped. Issue #19 tracks measuring them.

Do not infer them from the park block again. Its `G00 G53 X0 Y3048` is the only
machine-frame coordinate in the output, and reading those two numbers as limits
was wrong twice over. `Y 3048` is a position the machine *reaches*, which bounds
travel from below rather than fixing it; it is also exactly 120.000", the
nominal bed length. Real Y travel runs well past it — the tool changer sits
beyond that end of the rail. Asserting 3048 put **A slot 0** (datum Y 3034.700,
13.3 mm short of it) inside the edge margin and made the slot unusable, which is
what `test_a_slot_zero_is_usable_with_the_shipped_config` now guards. `X 0 →
1524` contradicts the measured B rail corner at 1534.160 that every B-rail part
cuts inboard from.

Guessing is harmful both ways: too tight rejects placements that cut fine today,
too loose licenses a real overtravel. Fill in `advanced.machine_travel` from the
machine and each axis starts checking itself.

`_max_tool_radius` reads `part.tools[...]["diameter_inches"]` straight from the
parsed file, not the resolved `ToolLibrary`. The Fusion post writes `(T2 D=12.7
… )` rather than `{0.5 inches}`, so those files parse to diameter 0 and both the
envelope check and the existing tool-radius collision check under-inflate them.

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
