# Decisions

Coded record of decisions, findings, risks and open questions. Codes are stable and never reused.

## Decisions

- **D1 — Layout PDF page 1 is a monitor page; pages 2+ stay letter.** Page 1 is sized to the shop's 32" 16:9 monitor (`MONITOR_PAGE`, 27.9 × 15.7 in), so a point on the page is a point on the glass and font sizes are physical. It holds only the bed, with job name, part count and runtime across the top. The ATC graphic and placement table follow on letter pages at their old sizes. **Why:** page 1 is read from about 20 ft at the machine; the ATC and table are read up close, and mixed page sizes in one PDF cost nothing.
- **D2 — Each blank is lettered from its top-left corner: position, part number, size.** `A0` / `204101-1` / `18x12x.75`, base sizes 96 / 56 / 80 pt (`_BOARD_LINES`). Each line shrinks to fit its board width, then the stack scales together to fit the height. The part number never exceeds the position or size line. **Why:** operator's chosen order. Shrink-to-fit was chosen over margin callouts or a numbered legend.
- **D3 — The PDF size label drops every zero that carries nothing.** Length and width keep one decimal only where needed (`18`, `22.8`). Thickness is the 64th-snapped nominal at up to two decimals with no leading zero (`.75`, `1.25`, `.5`, `2`). Separator is a bare `x`. **Why:** the line is fitted to the board's width, so each character costs size on the whole line. This deliberately departs from bed.js `sizeLabel` (`18.0 × 12.0 × 0.75"`); see R1.
- **D4 — The tool changer is drawn as a symbol left of the bed, with no label.** An 8-holder rack, centred on the bed's height, in a 96 pt strip. **Why:** the changer sits past the table's machine Y max end, which page 1 draws on the left, and the operator needs to tell the bed's ends apart at a glance. Nothing in the app knows where along X the rack runs, so it is not a measured position. The bed is height-limited on this page, so the strip costs no scale. The text label was removed at the operator's request.

## Findings

- **F1 — Board width, not page size, limits legibility on the monitor page.** The bed fills the page at 15.34 pt per bed inch. Capital heights with D2/D3: 18 × 18 board 0.96" position, 0.55" part number and size; 22.8 × 16.8 board 0.96" / 0.52"; 12 × 6 board 0.37" / 0.16". About 1" capitals are comfortable at 20 ft; 0.35" is the normal-vision limit.

## Risks

- **R1 — One board is quoted three ways.** Monitor PDF `18x12x.75`, canvas `18.0 × 12.0 × 0.75"`, page-2 placement table `18.00×12.00"`. bed.js's own rationale is that a board quoted two ways is a board someone loads rotated. Open until Q1 is decided.

## Questions

- **Q1 — Should the canvas and the page-2 table adopt the D3 format?** Open. Not asked for yet.
