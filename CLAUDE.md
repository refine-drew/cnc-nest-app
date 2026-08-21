# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This App Does

CNC Nest is a Flask web app for optimizing CNC cutting layouts on a 5×10 ft dual-rail bed. Users load VCarve G-code files from a library folder, drag-place parts onto A/B rails, get live collision detection, then generate a merged master G-code file that combines all parts using order-of-operations (grouping cuts by tool across all parts).

**Tool identity and tool pocket are separate concerns, and that split is the app's
newest and most load-bearing idea** (issues #9, #10, #11, #12, #13, #24; spec
`docs/tool-changer-pocket-management-spec.md`). A `T#` in a posted file used to mean
both "which cutter" and "which pocket" at once, so two programs authored at different
times routinely used different cutters in the same pocket number and the generator
merged them into one block. Identity now comes from a shop-assigned code matched against
`tool_library.json`; the pocket is job state the operator drags in the changer dock. The
sections below assume that split throughout.

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
- `_tool_binds`: `path → {T#: code}` — the operator's job-scoped answers for files that carry no shop code
- `_pocket_overrides`: `code → pocket` — the operator's drags in the changer dock
- Key API routes: `/api/load-library`, `/api/place`, `/api/generate`, `/api/changer`, `/api/tool-library`, `/api/bind-tool`
- **Save/load job is gone** (issue #26, 2026-08-17). The routes, the `.cnj` format and
  its tests were deleted: a nest is quick enough to rebuild that reloading one was never
  worth the format. Do not reintroduce it, and do not persist the pocket map — it is
  deliberately in-memory job state, like `_placements`, which is what makes the whole
  class of "stale override in a saved job" problems not exist.

**An auto-named job is `0820-1430`, and the seconds were replaced rather than
dropped** (2026-08-20). `_timestamp` was `%Y%m%d_%H%M%S`, giving a 20-character stem
of which about six discriminated anything: every file the app writes is a nest, so
`nest_` said nothing, the year has never told two of this shop's jobs apart, and the
seconds were a uniqueness mechanism wearing a timestamp's clothes. The stem is now
`%m%d-%H%M` and `job_name_format` ships as a bare `{timestamp}` (the prefix machinery
stays; the shipped value is empty). **`_unique_job_name` is what makes that safe** —
it advances `0820-1430` → `0820-1430b` → `0820-1430c` against the output dir, so a
second Generate inside one minute cannot overwrite the first. That guard is not
housekeeping: the Syntec identifies a program **by file name** (a merged master
carries no O-word, see `writeProgramIdentification`), so two different programs
answering to one name are ambiguous *on the machine*, not just on disk. It guards
**only** the auto name — an operator who types one means it, and re-generating over
it is normal, so a typed name is used verbatim. Shortening `_timestamp` also shortens
the audit CSV stamps, which is free: three CSVs share one stamp, they are not program
identity, and two audit runs in a minute overwriting each other costs nothing.

**`gcode_parser.py`** — parses `.nc`/`.mmg` VCarve G-code files into `GcodePart` dataclasses. Extracts blank dimensions, material thickness, tool info, XYZ bounding boxes per pass, and validates Z depths.

**`post/syntec_Refine.cps`** — the REFINE Fusion post-processor. **It is ours to
change**, and it is the only place CAM knowledge can reach the app, because the app
reads posted `.nc` and never CAM source. It lives in `post/` rather than
`Source Data/` for one reason: **`Source Data/` is gitignored**, and a post the app's
parser has a contract with cannot be untracked and unreviewable. Anything else the app
depends on belongs out of that folder too. `writeToolIdentity` emits the identity
comment specified in `docs/tool-changer-pocket-management-spec.md` §6.2.1:

```
(TOOLID T2 CODE=EM-0512 VENDOR=AMANA FLUTES=3)
(TOOLDESC T2 12 DOWNCUT SPIRAL)
```

**`CODE` alone is the identity, and it carries a shop-assigned code** — not a
manufacturer part number (spec §3.1, #9, 2026-08-17). The operator types the same code
into Fusion's **Product ID** field and into the VCarve tool **name**, which is the only
field VCarve's post lets reach a file. Matching is exact or it does not happen: an
uncoded tool orphans to a job-scoped manual bind, and no string is ever compared.
`VENDOR` and `FLUTES` are emitted but unread. `toolId` is document-scoped and must never
be used. The `T#` only ties the line to the header above it.

**Product ID, and Product Link was tried and does not work** (2026-08-19). The code needs
a per-tool Fusion field that is (a) reachable from a post and (b) wanted for nothing else.
Product Link looked better on (b) — Product ID is the natural home for the manufacturer's
part number, and one field cannot hold both — so the code moved there for a morning. It
fails (a): `productLink` is **not a member of the post kernel's `Tool` class**
(`tool.productId` and `tool.vendor` are), and reading it as the section parameter
`operation:tool_productLink` — Autodesk's own idiom from their `setup-sheet.cps` — did not
deliver the value from this Fusion. Files posted with the field filled came out with an
empty `CODE=`. A field the post cannot read is not a candidate, whatever the division of
labour. `getSectionParameterForTool` is gone and `getToolCode` reads `tool.productId`.

**So (b) holds by rule instead: Product ID carries the shop code and nothing else.** That
rule is the price of the reversal and is load-bearing — a leftover catalogue part number
in the field posts as a code, and two cutters ground from one catalogue item then share it
and merge into one block. Nothing is displaced today (the shop's tool sheet has no
part-number column); if part numbers ever need a home it is `tool_library.json`, beside
`vendor` and `product_link`, never the CAM field.

**`getToolCode` aborts the post rather than emit a code it cannot write faithfully.** The
guard was written for Product Link, whose intended content was a URL, and it earns its
keep on Product ID too: `settings.comments` silently drops everything outside
`" a-z0-9.,=_-"`, so anything **pasted** rather than typed is rewritten without a word — a
product URL posts as `HTTPSWWW.AMANATOOL.COM46170-K-CNC-SOLID-…`, and `1/8" Roundover`
copied off a product page posts as `18 ROUNDOVER`. Both stay code-shaped to every reader,
and truncation at 78 characters then collapses two cutters whose values share a long
prefix onto **one** code — the exact failure the identity split exists to prevent, by the
one route the description seal cannot see. So the rule is byte-for-byte survival of the
comment filter, plus a 24-character cap, plus **no interior whitespace** — the parser's
`KEY=(\S*)` grammar reads `EM 0512` back as `EM`, silently, and
`test_a_code_containing_a_space_cannot_round_trip` pins why that third check exists.
An *empty* field is not an error; it is the designed path to a manual bind.

**The comment key is `CODE=`, and `PRODUCT=` must not come back as an alias.** The key was
named `PRODUCT=` until 2026-08-19; reverting the *source field* is no reason to revert the
*key*. `CODE=` names what the value is rather than where it came from, and one key with one
meaning is the point — an alias is a second candidate code in one line, and the app must
never choose between two. `test_toolid_product_field_is_not_read_as_a_code` guards it.
Renaming cost nothing — no posted file in the library carried a `TOOLID` comment yet, so
there was no migration.

**Program names are alphanumeric, and an alphanumeric one gets no O-word** (2026-08-19).
`programNameIsInteger` is `false`, because Fusion's program name doubles as the output
file name and the shop wants that name meaningful; typing a bogus number and renaming
the file afterwards was the whole annoyance. `writeProgramIdentification` keeps a bare
numeric name posting as `O1001` exactly as before, and writes anything else as a comment
with **no O-line at all** — the Syntec loads programs by file name and runs files that
carry no O-word (`gcode_generator`'s merged masters carry none), whereas a made-up
number would file every alphanumeric program in control memory under one O. Don't
"finish" this by inventing a number or by writing the name after `O`. The name reaches
the file through `formatComment`, so it is uppercased and filtered — that mangling is
cosmetic here, unlike `CODE=`, because nothing reads the line back. The function sits
outside the `getProgramNumber_fanuc.cpi` include block on purpose; `getProgramNumber`
itself is untouched.

**A blank field is emitted as `CODE=`/`VENDOR=`, never omitted** — empty means the Fusion
library entry needs filling in, missing means the file predates the comment, and only
the first is actionable. `_toolid_fields` preserves that as `""` vs absent; don't
collapse them.

`TOOLDESC` is **not decoration**: the library stores the set of descriptions each code
is known to post, and a new string blocks with a two-way prompt (rename → accept, or
the code is duplicated in CAM → fix it there). That seal is the only detector of one
code on two physical cutters — see spec §3.5.3, including the two risks it accepts.

**Three constraints from `settings.comments` shape this format, and they are easy to
trip over.** Comments are uppercased, filtered to `" a-z0-9.,=_-"` (so `/` and `"`
vanish from free text), and **truncated at 80 characters**. Truncation *omits* fields,
which would defeat the empty-vs-missing rule, so: `CODE=` is length-capped and
filter-checked at post time by `getToolCode` rather than given its own line, the
identity line carries no geometry (`D`/`CR`/`TYPE` are already on the tool-list line
above it — repeating them is what would spend the budget), and the one unbounded field,
the description, sits on its own `TOOLDESC` line where a truncation can only cost free
text. Adding a field to the `TOOLID` line means re-checking that budget.

`TOOLDESC` lands in `cam_description`, deliberately **not** in `description`.
`cam_description` is what the **description seal** reads (`tool_library.resolve_part`),
which is the only cross-file detector of one code on two physical cutters; `description`
feeds display and the orphan-binding dialog. Keeping free text out of the second was
originally about `_tool_compatibility`'s signal — that function is gone, but the split
now does a load-bearing job for the seal instead.

**`collision.py`** — rectangle overlap collision detection. Handles the two coordinate systems: A rail uses additive XY offsets; B rail applies 180° rotation (mirroring) around the bed center before offset.

**`gcode_generator.py`** — merges placed parts into a single master G-code. Walks tool passes in order-of-operations sequence (all T1 cuts across all parts, then all T2, etc.), applies coordinate transforms matching `collision.py`, and uses nearest-neighbor sorting to minimize rapid travel.

**Nothing reaches a comment without going through `gcode_generator.comment`**
(2026-08-20). A comment ends at the **first** `)` — no nesting, no escape — so a job
named `(9) 18G Test` posted `(Job: (9) 18G Test)`, whose comment ended after `(9`,
leaving `) 18G Test)` on the block as code for the control to alarm on. The job name
was the visible case; the one that mattered is that `_compute_job_safe_z` writes its
own driver as `18G.nc (retract)`, so **every master the app had written since #22
carried a broken header**, whatever the operator typed. Every comment string here is
authored elsewhere — job name, source file names, `driven_by`, the CAM `(Tool: …)`
text — so all of them are wrapped, not just the ones that have failed. Parens become
**brackets** rather than being deleted (`(9)` deleted reads as a bare `9`), and
non-printing characters become spaces: the `.nc` is UTF-8, so the header's old em dash
reached the Syntec as three bytes it has no code page for, and a stray newline would
split one comment into an unterminated block and a bare-code one. Whitespace is
otherwise untouched — the header's alignment is part of its format. Comments copied
out of a source file are the one thing `comment()` never composes, so `_transform_line`
passes them through byte-for-byte unless `comment_is_wellformed` says the control
cannot read them, and re-wraps only then; `(A) (B)` is two comments and is fine.

**A G-code number can end in a decimal point, and `gcode_generator._NUM` is the one
pattern that knows it** (2026-08-21). Fusion writes whole numbers as `X307.` / `Z24.` /
`F7620.`, and the obvious `[+-]?\d*\.?\d+` **cannot match one** — it has to end on a
digit, so it takes `307` and leaves the `.` where it sits. For a pattern that only reads
a value that is free (`float("307") == float("307.")`), which is why the parsers, the
validator's read patterns and the estimator were never affected and keep their own
patterns. The generator is the only module that **rewrites** what it matched, and there
the stranded point lands after the new value: `18G.nc`'s `X307.` on A slot 0 emitted
`Y2727.7000.`, two decimal points in one word, and the control alarms on the block
mid-cut. Four of the six Fusion files in the library carry such a word inside a pass
body (`18G`/`18GH` line 136, two arcs in `24GH`), so this was the ordinary case rather
than an edge one — and it is invisible in review, one character in a 400-line file of
coordinates. `_NUM` is `[+-]?(?:\d+\.?\d*|\.\d+)`, which also keeps a leading point
(`X.5`); every `X`/`Y`/`I`/`J` substitution and `_Z_RETRACT` share it. The trailing-dot
lines Fusion writes as `G28 G91 X0. Y0.` never reached the transform — they sit in the
footer, outside every pass body — which is why the bug surfaced only on the one part
whose *toolpath* landed on a whole millimetre.

**`gcode_validator._check_word_syntax` is the gate that should have caught it**, and it
is the independent half of that rule the same way `_check_comment_syntax` is for
`comment()` — the generator forms the words, and this says whether they are readable. A
block outside a comment is `letter` + optional signed number, repeated; anything the
tokeniser cannot consume is something the control cannot read, so it is an **ERROR**
under every reading and the file is never written. It stays quiet on a block whose
stripped form still holds a paren: that is a broken comment, and the comment check names
the real problem rather than reporting the same defect twice.

**Every job ends by loading pocket 2, and that `T#` is a pocket rather than an
identity** (2026-08-20). The shop starts almost every job with the ½" end mill and keeps
it in pocket 2, so the job that just finished is the cheap place to load it — the bed is
cut, nothing is waiting on the machine, and the next job starts with the tool already up.
`advanced.end_of_job_pocket` names the pocket; absent or `null` means no end-of-job
change, which is what every job did before this existed.
`gcode_generator.park_tool_word` is the **single rule** for whether one is emitted, read
by the generator, the live panel and the setup sheet, so none of the three can claim a
tool the program does not leave in the spindle.

It is the one `T#` in the file that names a **pocket** instead of a cutter identity, and
that is not a lapse in the split — the block cuts nothing, so it means "leave whatever is
in pocket 2 in the spindle", which is exactly the standing arrangement it restores.
Resolving an identity here would emit a *different* pocket whenever a job remapped the
mill, and the library holds two ½" mills (`EM-0512`, `EM-0520`) that both declare slot 2,
so it would also have to guess which one is loaded. The assumption it does carry is that
pocket 2 still holds a ½" mill: a job that drags another cutter there ends holding that
cutter, which is why the setup sheet names the pocket **and** what is in it.

Three details are load-bearing. It is **skipped when the last block already ran from that
pocket** — the tool is up, the machine state after the program is identical either way,
so the skip is free and saves a 27–57 s carousel cycle and touch-off. **No `G43` follows
it**, because nothing after it cuts; a `G43` would leave the file ending under an offset
the program never uses. And it sits **after** the park's `G53 Z0` and **before** the park
traverse — the same preconditions every mid-file change already runs under, and a change
after the traverse would drive the gantry off the park it just made.

The estimator counts it like any other `T# M06`, so it is charged a full change in both
the file-derived report and the live panel. Making the panel agree is what moved
`_compute_job_stats` onto `_identity_map`: it counted raw `T#` strings, which the skip
rule cannot be decided from (the last block's *pocket* decides it) and which disagreed
with the file anyway wherever identity merging joins two `T#` into one block.

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
pinned as fixtures in `tests/test_gcode_validator.py`. `_check_comment_syntax` is the
independent half of the comment rule above and shares no scanner with the generator —
the generator sanitises, and this is what says whether it did.

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

**`tool_library.py`** — the identity library, **keyed on a shop-assigned code**, and
the sole diameter authority. Built 2026-08-17 (issues #9, #24); data lives in
`tool_library.json`, which is operator data with its own lifecycle and deliberately
**not** inside `config.json`.

**Identity is matched exactly or not at all, and no string is ever compared.**
`code_in_file_tool` looks in two places, because there are two CAM apps: Fusion's
`CODE=` from the `TOOLID` comment (sourced from the tool's Product ID field), and —
for VCarve, whose post lets only the tool *name* reach a file — a code-shaped token
inside the description. A file either carries a code the library knows, or the tool
**orphans** to an explicit operator decision.
`End Mill` never finds anything, which is what kills §3.1's dangerous direction: a
stale copy-pasted description making two different cutters look like one.

Three rules in that module are load-bearing:

- **`diameter_inches` is the tool's *maximum cutting diameter*** — its widest point,
  which no file supplies. `.25 Bowl Bit` is declared 0.75 and `1/8 Roundover` is 0.3;
  the declared number governs and the name is only a label. Over-declaring costs a
  placement that would have fit; under-declaring puts the cutting edge somewhere the
  check called clear.
- **Guard (a): resolution is injective within a single file.** Two distinct `T#` in one
  `.nc` must never resolve to one library tool — CAM already asserted they differ by
  giving them different pockets. Hard stop, never a merge. Per file only: two
  *different* files' `T4` resolving to one tool is the feature working.
- **The description seal (`cam_descriptions[]`) stores a set, never a single value.**
  Replace-on-confirm thrashes forever after a rename — new files carry the new string,
  old files still carry the old one — which trains click-through and destroys the only
  cross-file detector of one code on two physical cutters. It **blocks** rather than
  warns, and it deliberately **does not learn from a manual bind**: a bind identifies a
  file that carries no code, so its description says nothing about what that code posts.

**`pocket_map.py`** — job-scoped pocket assignment and the §3.4 validity gate.

**The assigner makes no arbitrary choices.** Decided 2026-08-17
(issue #10), specified in `docs/tool-changer-pocket-management-spec.md` §3.2.1, §3.4
and §3.5. It seeds each tool's declared default slot and stops —
**no tie-break** (two tools declaring one pocket both sit in it, visibly and
invalidly), **no fill rule** (a tool with no declared slot is staged, never dropped
into the lowest free pocket), **no write-back** (a drag is a job-scoped override; the
default slot is *prescriptive*, and re-proposing it next job is the intended nag).
Nothing is refused at placement — not even a 9th tool, because identity merging can
still lower the count — and **only generation is gated**, on three rules: every file
tool resolved, every resolved tool in exactly one pocket, no pocket holding two.
Determinism is therefore a *consequence*, not a rule to enforce; don't add a
tie-break "for stability" — that would be the one thing that breaks it.

**The assigner reads identity-ordered data, never the remapped `T` numbers.** An
assigner reading post-remap numbers would make the emitted output depend on an
assignment that depends on the output. `_identity_map` in `app.py` is built off
library codes and declared slots and never touches a pocket number as an input.

**The Generate gate was re-pointed, not added.** It has existed in both layers since
the original build (`static/job.js`, `app.py`'s 422); what was unsound was its
*signal*. `_tool_compatibility`'s `has_conflict` fired only when one `T#` carried
**differing** description strings, so two genuinely different cutters sharing a stale
identical string were never flagged and sailed into a merged block — the library has
exactly that case (`T2` and `T9` post `End Mill {0.5 inch}` byte-for-byte in one file).
`_tool_compatibility` is **deleted**; both layers now read `_changer_state()["valid"]`.

**`config.py`** — loads/saves `config.json`. Config defines library paths (a list of candidates; the first that exists locally wins), output path, bed dimensions, per-rail geometry (`advanced.rails` — see Coordinate Systems), `tool_capacity` (generation is blocked above it), `end_of_job_pocket` (the tool every job
ends holding), fence-origin offsets, safe Z, and slot positions.

**`config.json` no longer has a `tools` map, and it must not get one back.** It was
junk in its entirety (operator, 2026-08-17): `T4` "Table Stiff" declared 0.75" for a
cutter that is actually 2.38", so migrating it would have imported a number that
under-inflates the X envelope by 1.6" on a real tool. Nothing keyed by *pocket*
survives into a library keyed by *identity*. `/api/config` ignores a `tools` key.

### Frontend (Vanilla JS + Canvas)

No framework, no bundler. Files in `/static/`:

- **`bed.js`** — HTML5 Canvas renderer. Draws the bed, rails, slots, placed parts with color coding, and ghost preview during drag. This is the largest and most complex frontend file.
- **`sidebar.js`** — library tree (left) and placement tray (right) UI
- **`placement.js`** — drag-and-drop placement logic, `/api/place`, and the resolver
  dialog for a tool the library cannot identify
- **`changer.js`** — the tool changer dock: 8 pockets, drag to reassign (#11)
- **`toollib.js`** — the tool library manager and the description-seal prompt (#24)
- **`layout.js`** — panel sizing, collapse, and `localStorage` persistence (#29)
- **`job.js`** — the Generate G-code button
- **`config.js`** — settings panel, reads/writes `/api/config`

**Colour carries two meanings and they are a mode apart, not merged** (#28). Identity
colouring (a colour per file) answers "which part is which" and is not confined to the
canvas — the library dot, the tray label and `pdf_report.PALETTE` all read it. Thickness
banding answers "is every part on this rail the same stock", which is a *categorical*
comparison, so it bins on the distinct values present rather than running a ramp.
**Quantize before binning**: nominally-identical stock does not arrive byte-identical
(19.05 vs 19.0 vs a planed 18.9), and binning raw millimetres paints one board as two,
which is the gut check failing in the worst direction. Unknown thickness is hatched and
says so in words — an unknown mistaken for 3/4" is the error the feature exists to catch.

**Layout state lives in `localStorage`, never in `config.json`.** Layout is
per-workstation; `config.json` holds shop truth and is the file you copy to a second
machine. The dock's height floor is load-bearing rather than cosmetic — §3.4.1
force-expands the dock while the map is invalid, so a height drag with no floor would be
a collapse by the back door.

### Data Flow

1. User picks library folder → `/api/load-library` → `gcode_parser` → populates `_loaded` → sidebar tree
2. User drags part to bed slot → `/api/place` → **tools resolve against the identity
   library first** (`tool_library.resolve_part`) → `collision.py` validates → adds to
   `_placements` → bed canvas and changer dock redraw
3. The dock seeds each resolved tool's declared pocket; the operator drags to resolve
   collisions → `/api/changer/assign`
4. User clicks Generate → `/api/generate` gates on the §3.4 validity rules →
   `gcode_generator` merges all `_placements` **by identity** and rewrites `T#`/`H#`
   for the pocket map → writes `.nc`, `.pdf`, `<job>_setup.txt`

**Resolution is strict and happens before the bed** (§3.5.3): an unresolved tool has no
declared radius, the library is its only source, and the app must not invent one.
Resolve or do not place. The dock still renders an unresolved state, because a library
edit can un-resolve a placed part retroactively (§3.5.4) — the one path in the app where
an already-validated placement becomes wrong after the fact.

Placements, tool binds and the pocket map all live only in memory for the life of the
server process. There is no GUI path to persist or restore a layout, deliberately.

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

#### Rail locating pins

Four 3/4" dowels locate the removable rails laterally. They are fixed hardware, so
`collision.check_pins` gates placement on them alongside the envelope. Measured in
machine inches 2026-08-19, two per rail — A (3.425, 119.100) and (3.425, 54.100),
B (62.275, 3.851) and (62.295, 68.851) — and stored in `config.json` as
`advanced.locating_pins`, in inches because that is how they will be re-measured.
An explicit `[]` means no pins; only a *missing* key falls back to `PIN_DEFAULTS`.

**The pattern is a cross-check on the readings, not a rule the code enforces.** Each
pair sits 1.875" outboard of its rail corner and 65.000" apart, with the first pin's
edge tangent to that rail's slot-0 datum. That pattern is what identified a transposed
digit in the original A reading (199.1 for 119.1, 77" off the end of the table);
`test_each_rail_pair_sits_65_inches_apart_outboard_of_its_rail` keeps it checkable.

**Outboard is why this check is subtle.** A blank registers against the rail corner
and runs inboard, so no blank covers a pin and no programmed coordinate reaches one.
What reaches a pin is the **cutter**: coordinates are tool centre with no comp, so the
edge stands one radius outboard of the path. The nearest pin edge is 1.5" out, so a
path hugging the datum edge only reaches it above a 3" cutter — a backstop like
`Y_FLOOR_MM`. A file whose path runs *outboard* of the datum is the live case:
`T24H.nc` reaches `vy −24.172` and clears pin A1 by **4.4 mm** with its 3/4" bowl bit,
which is the whole margin. Nothing in `tool_library.json` (widest: CF-2380 at 2.38")
is rejected anywhere today.

Unlike the X hard stop, a pin **is** a slot problem — two discrete points per rail, so
moving along the rail can clear one, and the message says so. Clearance is measured
circle-to-rect (`_rect_circle_gap`, tool radius + pin radius from the *uninflated*
toolpath rect) rather than box-to-box, so the corner rounds the way a round cutter
sweeps it; squaring it refuses placements that clear diagonally by millimetres.

This is placement-time only. `gcode_validator` reads tool-centre coordinates with no
radius to inflate them by, so it could only catch a path drawn straight through a pin;
the check with the radius is the one that has to be right. `static/bed.js` draws the
pins from the resolved mm `/api/slots` returns — never re-derive them there.

`gcode_validator` mirrors this per-axis policy (`_MARGINED_AXES`). It reads
tool-centre coordinates out of the finished file and has no radius to inflate
them by, so on X it is one radius **more permissive** than the placement gate —
a backstop, not the primary guard. That direction is deliberate: the stricter
check must be the one that runs first.

`_max_tool_radius` reads **`PlacedPart.tool_diameters`** — the declared diameters the
identity library supplied at placement time — and does not consult the parsed header at
all when they are present. Mixing the two would silently prefer whichever number
happened to be larger, and the parsed one is wrong in the crash direction on every
profile bit. `tool_diameters` is `None` only off the placement path (the audit sweep),
where it falls back to the header.

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
  direction. The Fusion branch also `continue`s before `_extract_diameter`.
- **`ZMIN` is excluded from `description`.** It is the job's Z range, not a property
  of the cutter, so one physical tool posts `ZMIN=0.` in one file and `ZMIN=-19.05`
  in another. This mattered acutely when `_tool_compatibility` compared descriptions
  per `T#`; that function is gone, but the reason survives — `cam_description` is the
  **description seal's** input now, and an unstable field there would prompt on every
  job. The description is built from the stable fields
  (`FLAT END MILL D=12.7 CR=0.`) — type alone would collapse every ½" and ¼" flat
  mill onto one string, which is the dangerous direction.

**Now built, and the *sole* authority:** the identity library supplies
the diameter (spec §3.5.2), because a file whose header is absent or wrong still needs
a true radius. Under #9 the parsed figure stops being authoritative entirely — every
tool resolves through its code, so the notation mess (four diameter notations, one
fractional, three files yielding nothing) simply stops being read. `_extract_diameter`'s
bare-decimal fallback must go with it: a code like `RK-004` would parse as a 0.04"
cutter, a 25× **under**-inflation. The earlier plan to *verify* `CR=`/`TAPER=` against
the declaration was dropped along with those schema fields — the check is not built,
and §3.5.3 records the risk that leaves as accepted.

**Motion is modal, and every walker must carry it** (2026-08-19). VCarve repeats the
motion word on every block, so `\bG0?[0-3]\b` on the line was an adequate stand-in for
"is this a move, and which kind" — until Fusion, which names `G01` once and then posts
bare coordinate lines (`X308.142 Y12.318`) for most of a file. Each walker that asked
the old question skipped those lines **and left its position stale**, then measured the
next explicit block from wherever the last one ended: a chord across the part instead of
the path around it. `18G5.nc` previewed as a skewed outline with a diagonal through it
and estimated at 75 s; across the library the Fusion files gained 1.5–3.6× the segments
and up to **23×** the runtime (`Vacuum Puck.nc`, 19.9 s → 465.8 s), while every VCarve
file moved by one segment and under a second. `gcode_parser.motion_mode` /
`is_modal_move` are the single rule; `extract_file_segments`, `scan_z_values`,
`runtime_estimator` and `audit_metrics` all read it. Three parts of it are load-bearing:

- **A coordinate is not a move on its own.** `G28 G91 X0. Y0.` — which Fusion writes to
  end a program — would inherit G01 and draw a cut back to the origin across everything
  the part just cut. `NON_MODAL_COORD_PATTERN` (G10/G28/G30/G53/G92) rules those out.
- **`scan_z_values` reads the modal mode too**, and that one is a safety check: Fusion
  ramps down on bare coordinate lines, so the old walk could miss the deepest Z in the
  file, which is exactly what `validate_z` compares against the spoilboard. No library
  file's `min_z` actually changed, but the hole was real.
- **A pass draws nothing before its first position is known.** Position starts unknown
  rather than at (0,0) — the tool is at the changer when a pass begins, so a segment
  from the blank's corner to the first rapid is a line the machine never travels. It was
  the diagonal slash in every preview. Carrying the previous pass's end across the tool
  change would just draw a different fictional line, so the reset is per pass.

`gcode_generator` was never affected — it rewrites coordinates wherever they appear and
reorders whole pass bodies, each of which opens with an explicit `G00`.

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