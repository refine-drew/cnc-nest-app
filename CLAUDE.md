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
- Key API routes: `/api/load-library`, `/api/place`, `/api/remove-placement`, `/api/generate`, `/api/save-job`, `/api/load-job`

**`gcode_parser.py`** — parses `.nc`/`.mmg` VCarve G-code files into `GcodePart` dataclasses. Extracts blank dimensions, material thickness, tool info, XYZ bounding boxes per pass, and validates Z depths.

**`collision.py`** — rectangle overlap collision detection. Handles the two coordinate systems: A rail uses additive XY offsets; B rail applies 180° rotation (mirroring) around the bed center before offset.

**`gcode_generator.py`** — merges placed parts into a single master G-code. Walks tool passes in order-of-operations sequence (all T1 cuts across all parts, then all T2, etc.), applies coordinate transforms matching `collision.py`, and uses nearest-neighbor sorting to minimize rapid travel.

**`tool_library.py`** — simple tool registry. Resolves tool diameters from file headers or user-supplied overrides.

**`config.py`** — loads/saves `config.json`. Config defines library path, output path, tool definitions, bed dimensions (default 1524×3048 mm / 60×120 in), rail width, safe Z, and slot positions.

### Frontend (Vanilla JS + Canvas)

No framework, no bundler. Files in `/static/`:

- **`bed.js`** — HTML5 Canvas renderer. Draws the bed, rails, slots, placed parts with color coding, and ghost preview during drag. This is the largest and most complex frontend file.
- **`sidebar.js`** — library tree (left) and placement tray (right) UI
- **`placement.js`** — drag-and-drop placement logic, communicates with `/api/place`
- **`job.js`** — save/load job state (`.cnj` JSON format)
- **`config.js`** — settings panel, reads/writes `/api/config`

### Data Flow

1. User picks library folder → `/api/load-library` → `gcode_parser` → populates `_loaded` → sidebar tree
2. User drags part to bed slot → `/api/place` → `collision.py` validates → adds to `_placements` → bed canvas redraws
3. User clicks Generate → `/api/generate` → `gcode_generator` merges all `_placements` → writes `.nc` + `.txt` report
4. Save/load state persists `_placements` + `_loaded` as a `.cnj` JSON file

### Coordinate Systems

This is the trickiest part of the codebase. A rail and B rail have different transforms:
- **A rail**: part origin is translated by `(slot_x_offset, rail_a_y_offset)` — additive
- **B rail**: part is rotated 180° (X and Y flipped relative to blank size) then translated — used for cutting the other side of a workpiece without re-fixturing

Both `collision.py` and `gcode_generator.py` must apply identical transforms or placements will collide in reality but not in simulation.
