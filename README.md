# CNC Nest Tool

Nest CNC parts on the shop's 5×10 dual-rail bed and post one merged program for the
whole sheet.

Load posted G-code from the library folder, drag parts onto the A and B rails, and the
app checks as you go: parts against each other, the cutter against the machine's hard
stops, and the cutter against the four dowels that locate the rails. It resolves every
tool in every file against the shop tool library, lays the changer out for you, and
then writes one master program that groups all the cuts by tool across every part —
so the job runs with the fewest tool changes it can.

Fusion is the primary CAM; VCarve files work too.

---

## First install

### Mac

1. **Install Git**, if you don't have it: `xcode-select --install`
2. **Install Python 3** from [python.org](https://python.org)
3. **Clone the repo** in Terminal:
   `git clone https://github.com/refine-drew/cnc-nest-app.git`
4. **Double-click `launch.command`** inside the cloned folder.
   If macOS blocks it: right-click → Open → Open.
5. Your browser opens to [http://localhost:5001](http://localhost:5001).

### Windows

1. **Install Git** from [git-scm.com](https://git-scm.com)
2. **Install Python 3** from [python.org](https://python.org) — tick
   **"Add Python to PATH"** during installation
3. **Clone the repo** in Command Prompt:
   `git clone https://github.com/refine-drew/cnc-nest-app.git`
4. **Double-click `launch.bat`** inside the cloned folder.
5. Your browser opens to [http://localhost:5001](http://localhost:5001).

On first run the launcher checks whether Flask and reportlab import, and if they don't
it runs `pip install -r requirements.txt` (Flask, reportlab, pytest). That is an
ordinary install into whichever Python is on PATH — the launchers do not create or
activate a virtual environment. For a developer setup that keeps the dependencies
isolated, see **Working on it** below and run `app.py` yourself.

---

## Updating

- **Mac:** double-click `update.command`
- **Windows:** double-click `update.bat`

The updater pulls from GitHub and relaunches. **The launchers do not update anything** —
running `launch` again just starts the version you already have.

---

## What you get out of a job

Clicking **Generate** writes four files to the output folder (Settings → Output path):

| file | what it is |
|---|---|
| `<job>.nc` | the merged master program — this is what goes on the machine |
| `<job>.pdf` | the layout: what sits in which slot on which rail |
| `<job>_setup.txt` | how to load the changer, and the estimated cycle time |
| `<job>_validation.txt` | anything the output validator flagged as worth a look |

The master program is checked before it is written. Anything the validator considers an
outright error — a cutting move with no tool-length offset active, a malformed
coordinate, a comment the control cannot read — **stops the file being written at all**,
and you get told why. Warnings go in the validation file and the job still runs.

---

## The parts you need to know about

**The tool library** (the wrench button) is the shop's list of real cutters, keyed by a
short code you assign — `EM-0512`, `BN-0500`. You type that same code into Fusion's
**Product ID** field, or into the VCarve tool **name**, and from then on the app knows
which physical cutter a file means no matter which tool number CAM happened to give it.
The library is also where the app gets each tool's true cutting diameter, which is what
the hard-stop check depends on — so a tool that is not in it cannot be placed until you
say what it is.

**The changer dock** along the bottom shows the 8 pockets. Each tool starts in the
pocket the library says it usually lives in; when two tools want the same pocket, drag
one somewhere free. Generate stays disabled until every tool has a pocket to itself,
and the dock tells you what is in the way. Pocket assignments last for the job only —
they are never written back to the library.

**Nothing is saved between sessions.** There is no save/load for a nest, deliberately:
rebuilding one is quick, and a stale saved layout is a real hazard. Closing the app
clears the bed.

---

## Project layout

```
app.py                  Flask application and API routes
config.py               config loading/saving (cross-platform paths)
config.json             machine geometry and app settings
tool_library.json       the shop tool library (operator data)

gcode_parser.py         reads posted .nc / .mmg files
gcode_generator.py      builds the merged master program
gcode_validator.py      checks the master before it is written
collision.py            part overlap, machine envelope, locating pins
tool_library.py         tool identity, and the authority on diameters
pocket_map.py           pocket assignment and the Generate gate
runtime_estimator.py    cycle-time estimate
pdf_report.py           layout PDF and the setup sheet
audit_library.py        library-wide sanity sweep
audit_metrics.py        per-file metrics for the audit
calibrate_runtime.py    re-fit the machine model against a timed job

post/syntec_Refine.cps  the Fusion post-processor (ours to change)
templates/              the single-page UI
static/                 browser JavaScript (CSS is inline in index.html)
tests/                  pytest suite
docs/                   the tool-changer / pocket-management spec
launch.command  launch.bat     start the app
update.command  update.bat     pull the latest version and restart
```

---

## Working on it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python app.py        # dev server on http://localhost:5001
pytest               # the full suite
```

There is no build step, linter or type checker.

Developer notes — and the reasoning behind the parts of this app that look arbitrary
until you know what they are refusing to do — are in `CLAUDE.md`. The tool-changer and
pocket-management design is in `docs/tool-changer-pocket-management-spec.md`.
