# Decisions

Coded record of decisions, findings, risks and open questions. Codes are stable and never reused.

## Decisions

- **D1 — Layout PDF page 1 is a monitor page; pages 2+ stay letter.** Page 1 is sized to the shop's 32" 16:9 monitor (`MONITOR_PAGE`, 27.9 × 15.7 in), so a point on the page is a point on the glass and font sizes are physical. It holds only the bed, with job name, part count and runtime across the top. The ATC graphic and placement table follow on letter pages at their old sizes. **Why:** page 1 is read from about 20 ft at the machine; the ATC and table are read up close, and mixed page sizes in one PDF cost nothing.
- **D2 — Each blank is lettered from its top-left corner: position, part number, size.** `A0` / `204101-1` / `18x12x.75`, base sizes 96 / 56 / 80 pt (`_BOARD_LINES`). Each line shrinks to fit its board width, then the stack scales together to fit the height. The part number never exceeds the position or size line. **Why:** operator's chosen order. Shrink-to-fit was chosen over margin callouts or a numbered legend.
- **D3 — The PDF size label drops every zero that carries nothing.** Length and width keep one decimal only where needed (`18`, `22.8`). Thickness is the 64th-snapped nominal at up to two decimals with no leading zero (`.75`, `1.25`, `.5`, `2`). Separator is a bare `x`. **Why:** the line is fitted to the board's width, so each character costs size on the whole line. This deliberately departs from bed.js `sizeLabel` (`18.0 × 12.0 × 0.75"`); see R1.
- **D4 — The tool changer is drawn as a symbol left of the bed, with no label.** An 8-holder rack, centred on the bed's height, in a 96 pt strip. **Why:** the changer sits past the table's machine Y max end, which page 1 draws on the left, and the operator needs to tell the bed's ends apart at a glance. Nothing in the app knows where along X the rack runs, so it is not a measured position. The bed is height-limited on this page, so the strip costs no scale. The text label was removed at the operator's request.

- **D5 — One `T#` declared as two different tools is a hard stop, at placement and at post time.** `gcode_parser.extract_tool_declarations` records the distinct header declarations under one number (`GcodePart.tool_conflicts`), `resolve_part` carries them as `redeclared`, `/api/place` refuses with `tool_number_reused`, the dock and the Generate gate show it, and `validateToolNumbersAreUnique` in the REFINE post aborts the post. **Why:** the body only ever says `T5 M06`, so nothing can tell which cutter a pass loads; the old key-by-`T#` parse silently kept the last declaration and merged two cutters into one pocket (F2). A pick rule would be a guess with a wrong-tool cut behind it.
- **D6 — Operator data lives on the Google Drive shared drive `SS2 Library`, outside git.** Part files in any folders, `tool_library.json` at the root. `live_library_path(root)` is the rule; the repo's `tool_library.json` is only a seed for a folder that has never held one. `ToolLibrary.reload_if_changed` runs before every request. **Why:** four machines run the app (Mac, AJ's PC, two shop PCs), the library is the sole diameter authority, and git-tracked copies written by the app on four machines cannot stay one library. Drive syncs continuously, so no daily refresh job exists or is needed.
- **D7 — The launchers pull from GitHub before starting the server.** `git pull --ff-only`, then pip again if the commit changed; offline or blocked by a local edit, they say so and start what is there. `update.bat` / `update.command` remain for a machine that has been up for days. **Why:** new code needs a server restart to take effect, and the shop PCs are launched each morning, so launch-time is the daily refresh. Reverses the earlier "launch never updates" rule.
- **D8 — `config.json` stays in git and identical on every machine; only the library path list is per-machine.** Each machine takes the first listed path that exists (Mac Drive mount, `G:/Shared drives/SS2 Library`, then the old Documents folder). Settings are never saved on a shop PC. **Why:** the file is machine geometry, which is shop truth; a shop-PC edit to a tracked file blocks the next launch-time pull (D7).
- **D9 — A typed job name is cleaned of what Windows refuses before it becomes anything.** `_safe_job_name` drops `"`, maps `< > : / \ | ? *` and control characters to `-`, strips trailing dots, spaces and dashes, and falls back to the auto name when nothing readable remains. **Why:** the same string is the file name and the program's first line, and macOS refuses only `/`, so a `3/4"` name reached `write_text` unchecked until the shop PCs.

## Findings

- **F1 — Board width, not page size, limits legibility on the monitor page.** The bed fills the page at 15.34 pt per bed inch. Capital heights with D2/D3: 18 × 18 board 0.96" position, 0.55" part number and size; 22.8 × 16.8 board 0.96" / 0.52"; 12 × 6 board 0.37" / 0.16". About 1" capitals are comfortable at 20 ft; 0.35" is the normal-vision limit.

- **F2 — Fusion lets two library tools share a tool number, and the REFINE post writes one header triple per tool.** A program posted that way carries two `(T5 …)`/`(TOOLID T5 …)` pairs with different codes while its body says only `T5`. Observed 2026-10-07 on a paddle job with two roundovers; the file was renumbered in Fusion before it was captured, so the regression test uses a synthetic copy. The whole library (62 files) declares each number once.
- **F3 — Windows readiness, reviewed 2026-10-08.** Every file read and write names UTF-8, library-relative paths go through pathlib, the server binds loopback, and no page loads anything from the internet. The dev venv is Python 3.9.6 and the shop PCs will install 3.13 or later; no removed stdlib API was found, both dependencies ship wheels, but nothing has run on Windows yet (R2).

## Risks

- **R1 — One board is quoted three ways.** Monitor PDF `18x12x.75`, canvas `18.0 × 12.0 × 0.75"`, page-2 placement table `18.00×12.00"`. bed.js's own rationale is that a board quoted two ways is a board someone loads rotated. Open until Q1 is decided.

- **R2 — `launch.bat` and the app have never run on Windows.** The batch logic was reviewed, not executed. First shop-PC launch is the test; run `.venv\Scripts\python -m pytest` there afterwards.
- **R3 — Two machines saving the tool library inside Drive's sync latency produce a conflict copy.** The app keeps whichever file Drive settles on and ignores `tool_library (1).json`. Accepted because library edits are rare.
- **R4 — Drive may not map the shared drive to `G:` on a given PC.** The fix is adding that path to the list in `config.json` on the Mac and pushing (D8), never editing Settings on the PC.
- **R5 — The seal learns on every machine, so a description learned on a shop PC is written to the shared file from there.** Intended under D6, but it means a shop-PC placement can change the library every other machine reads.

## Questions

- **Q1 — Should the canvas and the page-2 table adopt the D3 format?** Open. Not asked for yet.
- **Q2 — Is the shared drive named exactly `SS2 Library`?** Open until it is created. The config paths assume that name.
