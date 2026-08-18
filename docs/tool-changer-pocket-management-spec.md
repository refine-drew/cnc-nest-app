# Tool Changer Pocket Management — Spec

**Status: built (2026-08-17).** Every decision below is settled and implemented.
The spec is now a **record of why**, not a plan — read it before changing any of
the behaviour it explains, because most of these rules look like arbitrary
choices until you know what they are refusing to do.

> **2026-08-17 — the feature is built.** #11, #12, #13 and #24 all landed, along
> with #2, #20, #25, #26, #28 and #29. What shipped:
>
> - **`tool_library.py` / `tool_library.json`** — identity keyed on a shop code,
>   matched exactly, with guard (a) and the description seal (§3.1, §3.5).
> - **`pocket_map.py`** — the assigner that makes no arbitrary choices, and the
>   §3.4 validity gate. `_tool_compatibility` is **deleted**; both layers of the
>   Generate gate now read the three rules (§3.4, §1.1).
> - **`gcode_generator.IdentityMap`** — blocks grouped and ordered by identity,
>   so a remap rewrites `T# M06` and `G43 H#` and nothing else (§4, §4.2).
> - **`tests/test_geometry_guarantee.py`** — #12's proving test, in the strong
>   whole-file form, asserting both that `T#` moved as the map says and that every
>   `H` still equals its preceding `T` (§6.3).
> - **`static/changer.js` / `static/toollib.js`** — the dock and the library UI.
> - **The setup sheet** — the PDF's ATC graphic re-keyed onto pockets, plus
>   `<job>_setup.txt`, with off-home pockets called out as temporary (#13).
>
> **The one prerequisite that remains is the operator's**: the ten shop codes are
> seeded in `tool_library.json` (`EM-0512`, `EM-0520`, `EM-0750`, `EM-08MM`,
> `BN-0500`, `RN-1000`, `RO-0125`, `CF-2380`, `CF-1500`, `BB-0250`) and need typing
> into Fusion's Product id and the VCarve tool names. Until then every file orphans
> to a one-time manual bind, which is the safe default working — but nothing
> auto-matches, so the feature is not yet exercised end to end (§3.5.5).
>
> Two things the build confirmed against the real corpus. The motivating case of
> §3.5.6 reproduces exactly: `39x35` and `18G300` open with pocket 2 doubled and
> Generate dark, one drag clears it, and the emitted file carries `T8`/`H8` for the
> moved cutter with no other word changed. And **no placement changed** when the
> library became the diameter authority — the correct radii do not push any current
> part over the envelope bound.

**Everything below this line is the original spec, preserved.** Sections 1–5 are
settled; section 6's open decisions are all resolved.

> **2026-08-17 — #9 is resolved, and the gate is fully open.** Identity is a
> **shop-assigned code** the operator types into Fusion's Product id and VCarve's tool
> name; the post already emits it and the parser already reads it. Matching is exact,
> the alias list is **deleted**, and an uncoded tool orphans to a job-scoped manual
> bind. See **§3.1**, **§3.5.1** (schema), **§3.5.3** (match-on-load and the
> description seal). Both original bars — #8 and #9 — are now closed. What remains
> open is build work (#11, #13, #24) and **one operator prerequisite: assigning the
> ten codes** (§3.5.5).

> **2026-08-17 — the structural gate is now open.** The original bar for starting
> implementation was that #8 (safety posture) and #9 (identity matching) both close.
> #8 is closed (posture 2, §6.1) and #9's blocking sub-questions are closed — the two
> guards in §3.5.3, the alias-collision hole (§3.5.3), and block ordering (§4.2). #9
> retains only the matching rule itself, which is a *refinement* of behaviour that
> §3.5.3 already bounds, not a structural unknown. What blocked the build after that
> was not a decision but **data: the operator-declared tool library content** (§3.5.1).

> **2026-08-17 — the library data has landed.** `Source Data/Refine Tools - Sheet1.csv`
> declares 10 real tools with `flute_direction` populated for every one — the field that
> exists in no file the shop owns, and the last stated blocker. See **§3.5.5** for what it
> settles, what it corrects, and the two questions it opens.

> **2026-08-17 — #10 is resolved.** Pocket auto-assignment, the validity gate and
> the tool library are now specified in **§3.2, §3.4 and §3.5**. The headline is
> that the auto-assigner **makes no arbitrary choices at all**: it seeds each
> tool's declared default slot, surfaces collisions instead of breaking ties,
> stages tools that have no declared home, and blocks generation until the
> operator has resolved everything. Determinism therefore falls out rather than
> needing a rule. #11 and #13 are unblocked.

> **2026-08-15 — Fusion is now the primary CAM tool.** VCarve is retained for
> simple one-offs only, and **the REFINE post-processor is editable**. This map
> was charted when Fusion was "down the road", so read §3.1 with that in mind:
> its conclusion that identity can never come from the file was established
> against VCarve, whose post we do not control. [#21](https://github.com/refine-drew/cnc-nest-app/issues/21)
> tests whether the Fusion post can carry a stable identity token, which would
> qualify — not overturn — that section for the primary corpus.

- Map: [Tool changer pocket management — spec](https://github.com/refine-drew/cnc-nest-app/issues/3)
- Discovery sessions: `brainstorms/2026-08-12-tool-changer-pocket-management.md`,
  `brainstorms/2026-08-17-pocket-auto-assignment.md` (#10 — assignment, the
  validity gate, the tool library)
- Reference Fusion output: `~/Documents/Windows Bridge Folder/18G.nc`
- Date: 2026-08-12, revised 2026-08-15 and 2026-08-17

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
`T#` and sets `conflict` / `has_conflict`.

**2026-08-17 — correcting this section.** It previously read *"Nothing blocks
generation on it."* **That is wrong, and was wrong when written.** The gate exists
in both layers and has since the original build:

- `static/job.js:36` and `templates/index.html:342` `_updateTopButtons()` disable
  the Generate button whenever `compatibility.has_conflict` is set (`404e228`).
- `app.py:661` rejects `/api/generate` with **422 "Resolve tool compatibility
  conflicts before generating"** (`785bea9`).

The defect is real but **narrower and nastier than "unenforced"**: what is unsound
is *detection*, not enforcement. `conflict` fires only when one `T#` carries
**differing** description strings, so two genuinely different cutters that share a
stale identical string are never flagged, and sail through both gates into a merged
block. The library corpus contains exactly that case — `T2` and `T9` both post
`(T… = End Mill {0.5 inch})` byte-for-byte in one file. So the gate is sound in the
safe direction and blind in the dangerous one, which is the inverse failure §3.1
warns about.

This should be treated as a bug with a safety consequence, independent of
whether the full feature ships. The fix is a sound signal feeding the gate that is
already there — see §3.4 — not a new gate.

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
no parseable diameter at all. The library must therefore be **operator-declared** and
keyed independently of `T#`. (#4 concluded it must also carry a many-to-one **alias
list** of the raw strings seen in files. **Superseded** — see the code decision below,
which serves VCarve and Fusion from one key without any string matching at all.)

**A Fusion tool library will not remove the need for this.** The app reads posted
`.nc`, not CAM source, so CAM knowledge reaches the app only through what the
post-processor writes into the file. The corpus is **not** almost entirely VCarve —
9 of 26 library files are already Fusion-posted, and the parser read **no tools
whatsoever** from them until [#20](https://github.com/refine-drew/cnc-nest-app/issues/20)
closed on 2026-08-17. A future Fusion library does not retroactively fix existing files. Most
fundamentally, CAM records *what a tool is*; this app must record *where it
lives*. Good CAM hygiene makes matching reliable — it does not make the app-side
library unnecessary.

**2026-08-17 — identity is a shop-assigned code, and the alias list is deleted (#9).**
An earlier revision of this section argued that `vendor` + `productId` could not be
relied on to match what a post wrote, so identity had to be *learned* one manual bind
at a time, with an alias list as the mechanism. That reasoning assumed those fields
carried whatever the cutter's manufacturer happened to publish. **They do not have
to.** The operator assigns a **unique code by hand, one per physical cutter**, and
types it into the CAM tool library:

- **Fusion** — the code goes in **Product id**, which `writeToolIdentity`
  (`post/syntec 4.cps:3088`) already emits as `PRODUCT=`. Neither the post nor the
  parser changes; the field simply stops being empty.
- **VCarve** — the code goes in the tool **name**, the only field that reaches a
  posted file. The corpus confirms it arrives verbatim — `(T1 = Ball Nose .5 inches
  Dia)` is a hand-typed name rendered unaltered — so `(T4 = RK-004 End Mill)` is
  available for the asking.

**The matching rule is one sentence.** Find a token matching the code pattern; if it
is present and known to the library, that is an exact match. Otherwise the tool is
**orphaned**, and the operator binds it for that run only (§3.5.3).

**This does not reopen the argument above.** What this section forbids is treating
description text *as identity* — inferring which cutter a file means from free-form
words. A code is self-identifying: the app never guesses, because the token is either
present or absent, and the absent case falls through to an explicit operator decision
rather than a similarity score. `End Mill` is never compared against anything.

**What the code deletes.** The `aliases[]` field, the alias-collision hole and its
`(filename, T#)` scoping, string normalisation, and the whole class of "a stale alias
outlives a corrected library" failures. A bind is job-scoped and never remembered,
which is how pocket assignment already works (§3.2).

**The key is `PRODUCT` alone, not `VENDOR`+`PRODUCT`.** A shop-assigned code is
unique by construction, so requiring a second field to be populated would only add
one more thing that can be blank. `vendor` stays as reorder information.

**Two format constraints, both from the post's own comment handling** (CLAUDE.md):
comment text is uppercased and filtered to `" a-z0-9.,=_-"`, so `RK-004` survives
intact while `RK#4` silently becomes `RK4`. Keep the code visibly unlike a `T#`, so
that nobody reads a pocket number into it.

**Scope.** Assigning the codes is CAM-side work, which §7 rules out of scope — but it
is now a bounded, one-time setup task (ten codes typed into two tool databases), not
an open-ended hygiene effort. The app still functions without it: an uncoded tool
orphans and is bound by hand, so the ruling stands and the degradation is graceful.

**What no file can ever supply.** Measured across the whole library on 2026-08-17:
**nothing from either CAM names the flute direction.** VCarve writes `End Mill`;
Fusion writes `FLAT END MILL`. Upcut, downcut and compression — the distinction
this section opens by saying must never merge — appear in **no file the shop owns**.
That is the library's deepest justification: it is not merely a convenient place to
record where a tool lives, it is the **only** place that fact can exist at all.

### 3.2 Pocket assignment is job-scoped

The app holds **no model of the physical changer contents**. There is no state to
sync, no drift, and no record that can silently lie about what is in the machine.

Assignment is derived per job from the loaded files, defaulting to each tool's
preferred pocket. Matching physical reality is an operator convenience, never an
app obligation.

**2026-08-17 — "dragging is the exception path, not the normal one" is struck.** That
sentence rested on the operator's expectation that suggested values "will likely match
the real tool changer anyway", which is true of any *one* tool and false of the jobs
this feature exists to enable. The declared library over-subscribes the changer — 11
tools, 8 pockets — and two pockets are contested by design (§3.5.5). **For the nests
that motivated the feature, dragging is the main event, not an exception.** The rest of
§3.2.1 is unaffected: the assigner still chooses nothing, and a drag is still a
job-scoped override. What changes is how often the operator is expected to do it, and
therefore how much the drag interaction has to be worth (#11).

**Assignment is pure in-memory job state, like `_placements`.** This section
previously required it to round-trip through the `.cnj` save/load format; that
requirement is **void** — save/load job is being sunset, because a nest is quick
enough to rebuild that reloading one was never worth the format (2026-08-17). One
consequence worth naming: nothing persists a pocket map, so the entire class of
"stale override in a saved job" problems does not exist.

#### 3.2.1 The assigner makes no arbitrary choices (#10)

This is the governing principle, and every rule below is a consequence of it. The
app never invents a pocket, never breaks a tie, and never learns one behind the
operator's back.

- **An unhonoured default slot is real physical work**, not a cosmetic loss. The
  operator pulls a cutter, re-seats it and touches it off (27 s + 30 s, §6.1). So
  the objective is to **minimise physical tool moves**, and honouring declared
  default slots is the proxy for it.
- **Collisions are surfaced, never resolved.** Two tools declaring the same default
  slot both occupy it, visibly and invalidly, until the operator drags one out.
  **There is no tie-break rule** — the app cannot know which cutter is cheaper to
  move, precisely because it holds no model of the changer's contents.
- **The invalid state must be permitted, not refused at drop time.** Swapping two
  tools is impossible without transiting a double-occupied pocket, so refusing the
  drop would deadlock the swap. Dropping onto an occupied pocket therefore makes the
  two **coexist** — it does not swap them and is not rejected.
- **A tool with no declared default slot is staged, not placed.** It goes to a
  "needs a home" area and waits. The app does **not** fill the lowest free pocket.
  Because the create-tool flow is where a default slot is declared (§3.5), a
  populated library leaves little staged, so this stays an exception path.
- **The default slot is prescriptive, not descriptive.** It records where a tool
  *belongs*. A drag is a **job-scoped override that never writes back to the
  library**; the app re-proposing the standard pocket on the next job is the
  **intended nag**, and operators getting used to seeing tools in the same places is
  the point. The library UI is the only place a default slot changes.
- **Nothing is ever refused at placement — not even exceeding 8 tools.** The
  distinct-tool count is **not final at placement time**, because identity merging
  (§3.5) can still lower it; blocking the 9th tool would block a job that is really
  an 8-tool job once the operator corrects a duplicate. Only *generation* is
  refused.

Because none of these steps chooses anything, **determinism is a consequence rather
than a requirement**: the map is a pure function of *(resolved tools, library
default slots)* plus the operator's drags. No fill order, no tie-break, no
write-back, no clock, nothing dictionary-ordered. The same files against the same
library give the same map every time, so setup sheets cannot drift.

One constraint for the implementer, from §4.2: the assigner must run off
**identity-ordered data, never the remapped `T` numbers**. `_build_blocks` sorts by
the `T#` string, so an assigner reading post-remap numbers would make the output
depend on an assignment that depends on the output.

### 3.3 Every `T#` is remappable

There are no reserved tool numbers, no manual-change convention, and no tooling
outside the ATC. All 8 tools live in the changer for the full duration of a
program. The `T99` entry in `config.json` is leftover test data, not a convention.

No allow-list, no reserved range, no exclusions. This is a significant
simplification and it should stay that way — see §7.

### 3.4 The validity gate (#10)

A job is **valid** — Generate enabled — when all three hold:

1. **Every file tool resolves to a library tool.** Nothing unmatched.
2. **Every resolved tool sits in exactly one pocket.** Nothing left staged.
3. **No pocket holds more than one tool.**

**Capacity is not a fourth rule, it is a consequence.** With eight pockets, rules 2
and 3 together make ">8 distinct tools" unsatisfiable, so over-capacity needs no
mechanism of its own: the ninth tool is simply a tool with nowhere to go, sitting in
"needs a home" with Generate disabled. This also answers §8's open question about
over-capacity after de-dup — the staging framing holds whether identity resolution
*raises* the count (one `T#` that is two cutters) or *lowers* it (two `T#`s that are
one cutter), because it never needed to know which happened.

`gcode_generator`'s capacity `ValueError` and the 422 at `app.py:661` remain as
**backstops**, the way `gcode_validator` backs up the placement-time envelope check.
The stricter check runs first; the second line of defence stays.

**This gate is existing machinery re-pointed, not new machinery** — see the §1.1
correction. What changes is the *signal*: `has_conflict`, which is unsound in the
dangerous direction, is replaced by the three rules above.

#### 3.4.1 What the operator has to be able to see

- **Every tool shows its parent part(s)** — which placed instances require it — so
  the operator can find the part to delete when the changer is full. The data
  already exists as `_tool_compatibility`'s per-tool `usages`, which is
  what makes this cheap: the panel this feature retires supplies its replacement.
  Show placed **instances**, not just filenames (`_instance_counts`).
- **Better than making them scan: name the part.** Compute which placed parts are
  the **sole** user of a tool, so the app can say *"removing bracket-L frees pocket 7
  — no other part uses ⅜" comp."* Parts that share every tool with something else
  cannot help and get no badge.
- **A tool sitting away from its default pocket must be marked**, or the deviation is
  invisible and never gets restored — which defeats §3.2.1's prescriptive default.
- **Two messages off one state.** "3 tools have no home" is a to-do when free pockets
  exist and a *stop* when the changer is full — dragging cannot fix the second, and
  an operator would otherwise hunt for a free pocket that does not exist. Operators
  are not developers; the wording carries the difference.
- **The changer surface is a full-window-width collapsible dock** above the status
  bar, 8 pockets left-to-right to match how the machine is read, force-expanded while
  the map is invalid so a disabled Generate always has a visible reason. It replaces
  the right sidebar's `#compat-section`, which is where the screen budget comes from.
  Layout detail and the agreed mockup are in
  `brainstorms/2026-08-17-pocket-auto-assignment.md`; **#11 owns the build.**

### 3.5 The tool library (#10)

**Standalone, hand-maintained, and it imports nothing.** Reading Fusion `.tools`
(JSON) or VCarve `.vtdb` (SQLite, per #4) was considered and **rejected for v1** on a
hard constraint: the shop PC running CNC Nest may have no Fusion install at all, so
the design must not depend on reaching a CAM library. Dual maintenance is an accepted
cost. A sync feature is a **future ticket, explicitly not a dependency**.

It lives in its own **`tool_library.json`** — operator data with its own lifecycle,
backed up separately, portable to a second machine, and kept out of the settings file
the settings panel writes. `config.json`'s pocket-keyed `tools` map seeds a one-time
migration; most of it is junk to discard rather than carry (`T99` marked "test", `T5`
with an empty name).

#### 3.5.1 Entry schema

The discipline: **every field has a named consumer. A field without one does not
belong.**

| field | req? | consumed by |
|---|---|---|
| `code` | ✔ | **primary key and match key** — operator-assigned, unique, typed into both CAM libraries (§3.1). What the pocket map references |
| `name` | ✔ | changer dock, setup sheet (#13) — e.g. `1/2" downcut spiral`. **Display only**, and the app's own; renaming it is free |
| `diameter_inches` | ✔ | **maximum cutting diameter** (§3.5.2) — tool-radius collision, **X envelope inflation**. The **sole** authority; no file supplies this |
| `geometry_class` | ✔ | dock and setup-sheet display. `Flat End Mill` / `Ball Nose` / `Roundover` / `Custom Form` / `Bowl Bit` — **the operator's own names, and only tools that exist** (§3.5.5) |
| `flute_direction` | ✔ | `up` / `down` / `compression` / `straight` — **the field no file can supply** (§3.1) |
| `cutting_length_in` | ✔ | tells apart two cutters identical on every other field (§3.5.5) |
| `cam_descriptions[]` | ✔ | **the tamper seal** (§3.5.3) — the set of description strings this code is known to post. Grows by one on each confirmed rename |
| `default_slot` | — | pocket seeding; **null → staged**, and null is a legitimate answer |
| `vendor`, `product_link` | — | reordering information for the operator. **Never read by the app** |

**`code` replaces the app-assigned `id`.** An earlier draft specified both, because
identity was then carried by aliases and the match key was not stable enough to be a
primary key. The code is stable by construction and is what the file matches on, so a
second identifier has no distinct job. The usual objection — renaming a key breaks
stored references — does not apply: nothing persists a tool reference, since pocket
maps are in-memory job state and save/load is sunset (§3.2). Correcting a typo'd code
means any file already posted under the old one orphans until re-posted, which is the
cheapest it will ever be, as no production Fusion files exist yet.

**`geometry_class` and `flute_direction` no longer discriminate identity** — the code
does that alone. They remain required because the operator needs to see what a tool
*is* in the dock and on the setup sheet, and because `flute_direction` is the one fact
§3.1 proves exists nowhere else. Their job moved from matching to display.

**Deleted: `corner_radius_in`, `taper_deg`, `flutes`.** Their only named consumer was
verifying a file's `CR=` / `TAPER=` against the declaration, and that check is not
built (§3.5.3). A field with no consumer does not belong, per the discipline above.

`geometry_class` and `flute_direction` are **deliberately separate**: up/down/
compression is orthogonal to flat/ball/chamfer, so two short lists beat a dozen
combined classes.

**The class list is the operator's, and it grows on demand** (2026-08-17). An earlier
draft proposed `flat`/`ball`/`radius`/`chamfer`/`form`/`drill` as "enumerated from the
real corpus"; when the real corpus arrived it named `Roundover` and `Bowl Bit`, which
that list cannot express, and contained no chamfer, drill or corner-radius endmill at
all. Since the field's job is to *discriminate identity*, a class that forces two
different profile bits into one bucket makes matching worse, and a class no tool
belongs to is the §3.5.1 discipline's own smell one level down. So: **five classes,
each with a tool behind it, and add a sixth when a sixth tool needs one.** Adding an
enum value is cheap; a wrong bucket is not.

**Explicitly excluded: feeds, speeds, stepover, gauge length, holder.** CAM owns
cutting parameters; the app needs geometry only for collision, envelope and identity.
Stated as a boundary so the schema stops growing.

**"Tool length" was excluded above and that was too coarse — the two lengths are
different fields with different owners.** *Gauge length* — stick-out from the holder,
what the `H` register measures — stays excluded: it is per-setup, not per-cutter, and
§4.1 already routes it through the pocket. *Cutting length* — flute length, a fixed
property of the cutter — is now **required**, because the corpus contains two entries
that are identical on every other field (§3.5.5). Excluding it would have made two
distinct physical cutters unrepresentable, which is §3.1's dangerous direction written
into the schema.

`default_slot` is **optional on purpose.** Requiring it would force a guess at create
time, and a guessed default is a wrong prescription — which §3.2.1 makes the
library's whole job to get right. Optional is also the only thing that makes staging
meaningful rather than dead code.

#### 3.5.2 The library is the diameter authority

This is a safety fix that falls out of the feature and has nothing to do with pockets.
CLAUDE.md records that `_max_tool_radius` reads
`part.tools[...]["diameter_inches"]` **straight from the parsed file**, and that
Fusion's `(T2 D=12.7 …)` header parses to diameter **0** (#20). So Fusion files —
the *primary* corpus — currently **under-inflate both the X envelope check and the
tool-radius collision check.** Once a tool resolves to a library entry, its declared
diameter supplies a true radius, which routes around #20 for safety purposes even
before the parser is fixed.

**2026-08-17 — the library is the *sole* authority, and diameter parsing is retired
(#9).** With every tool resolving through a code (§3.1), the parsed figure has no
consumer left. That deletes the whole notation problem measured across the corpus in
one stroke: four diameter notations, one of them fractional, and three files yielding
nothing parseable at all. It also removes `_extract_diameter`'s bare-decimal fallback,
which would otherwise have read a code like `RK-004` as a 0.04" cutter — a 25×
**under**-inflation, the crash direction.

One narrow use survives, as **display and never as authority**: Fusion's structured
`D=` header is shown beside candidate tools when binding an orphan (§3.5.3), so a
gross mismatch is visible. VCarve's diameter, which exists only inside free-text names
like `End Mill {0.5 inch}`, is not read at all.

**`diameter_inches` is the tool's maximum cutting diameter — its widest point — and
nothing else** (operator, 2026-08-17): *"the diameter of the tool used to calculate
collisions with other parts and the extents of the table."* Not the shank, not the
nominal size in the name, not the radius a profile bit is sold by. **The declared
number governs and the name is only a label**, which matters because the real library
disagrees with itself on exactly this point: `.25 Bowl Bit` is declared **0.75** and
`1/8 Roundover` is declared **0.3**. Both are correct — the name is the feature the
bit is sold by, the number is how much room it needs.

The asymmetry is the reason to state it. Over-declaring costs a placement that would
have fit; under-declaring puts the cutting edge somewhere the check said was clear.
So where the name and the number disagree, **the larger reading is the safe one**, and
the library UI must ask for the widest point rather than "diameter" unqualified.

#### 3.5.3 Match-on-load, the seal, and what guards it (#9)

**Resolution happens at load, before placement, and it is strict.** An unresolved
tool must never reach the bed: the collision and envelope checks need a radius, the
library is now its sole source (§3.5.2), and the app must not invent one. Resolve or
do not place.

Loading a job reads each file tool's code token and resolves it:

- **Code present and known** → match. Bind, and take that tool's default slot.
- **Code present but unknown** → prompt the operator to **create a tool**. This is
  where a default slot gets declared, and where the code is recorded.
- **Code absent** → the tool is **orphaned**. The operator says which library tool it
  is, and that bind lasts **for this run only**. Nothing is remembered.

**The orphan path is the floor, not a failure mode.** Every one of the 26 files in the
library today predates the code, so all of them orphan until re-posted or renamed.
That is the safe default working, and it is why the path must be pleasant rather than
punitive — for a VCarve one-off with one or two tools it is a couple of clicks.

**Guard (a) survives, with a new job. Resolution is injective within a single file.**
Two distinct `T#` in one `.nc` must **never** resolve to the same library tool — CAM
already asserted they differ by giving them different pockets. A collision is a
**hard stop requiring manual disambiguation, never a merge.** Under the alias scheme
this caught two cutters posting one description; under codes it catches a **duplicated
code within one file**. Deliberately **per file only**: two *different* files' `T4`
resolving to one library tool is the feature working as intended.

**Guard (b) is retired.** It refused a manual bind when the file's parsed diameter
disagreed with the library's. Two decisions removed its footing: the library is now
the sole diameter authority and files are no longer parsed for diameter as a source of
truth (§3.5.2), and `diameter_inches` is defined as *maximum cutting diameter*, which
for every profile bit differs from a posted `D=` **by design** — `.25 Bowl Bit` is
declared 0.75 against a nominal 0.25. An exact comparison would refuse correctly
declared tools; a tolerance was already rejected as the wrong shape for the risk. So
instead of a rule, **a display**: when binding an orphan, show the file's posted `D=`
beside each candidate library tool, so a gross mismatch is visible without the app
making a false-positive-prone judgement. What remains unguarded is an operator binding
an orphan to the wrong tool — which was always operator judgement, and is now confined
to the exception path.

##### The description seal

The code is assigned by hand, so it can be duplicated by hand. The failure is
specific: you duplicate a Fusion tool entry to make a similar cutter, edit its
description, and **forget to change the Product id**. Two physically different cutters
now post one code, the app merges their passes into a single block, and one of them is
cut with the wrong tool — §1's defect, reintroduced by copy-paste.

**Guard (a) cannot catch this**, and the reason is structural. It only fires when two
`T#` *in one file* resolve to one tool. Here they are in different files — and across
files, two `T#` resolving to one tool is the **intended** behaviour: it is precisely
how one cutter shared by two parts merges into a single block. The app cannot
distinguish "two parts share a cutter" from "two cutters share a code" by that route.

**So the library stores the descriptions each code is known to post, and a
disagreement prompts.** This works because of *why* a tool gets duplicated: you
duplicate it in order to make a different cutter, so you edit the description. That
edit is the whole purpose of the duplication, and it is what gives the duplicate away.
The legitimate case — one cutter used by six parts — posts a byte-identical string
every time, because it is the same CAM library row rendered through a deterministic
post.

**This is not the alias list returning.** An alias list is a lookup index: a string
finds the tool, so two cutters can collide on one string and ambiguity is possible.
Here the code remains the only lookup key and `cam_descriptions[]` is a set of values
*accepted under* that key. Nothing is ever searched by description, so nothing can be
ambiguous.

**Store a set, never a single string.** A single expected value that is replaced on
each confirmation **thrashes**: after a rename, new files carry the new string and old
files still carry the old one, so the prompt alternates between them forever. That
trains click-through, which destroys the check. A set costs one prompt per rename,
answered once, after which old and new files both match cleanly.

**The prompt is two-way and must show both strings**, because its whole value is the
operator's ability to read it:

```
RK-001 previously posted as:  12 DOWNCUT SPIRAL
This file posts:              12 AMANA 46170-K DOWNCUT     -> a rename
```
```
RK-001 previously posted as:  12 DOWNCUT SPIRAL
This file posts:              12 COMPRESSION               -> not a rename
```

*"Same tool, I renamed it"* adds the string to the set. *"Different tool"* means the
code is duplicated and must be fixed in CAM. **It blocks rather than warns** — it is
now the only cross-file detector of a wrong-tool cut, and a warning on a crash-class
check is one that gets clicked through. Surface the set in the library UI too: three
accepted descriptions on one code is a smell the operator can see.

The plumbing exists. `TOOLDESC` already lands in `cam_description`, deliberately kept
out of `description` so free text cannot move the compatibility signal (CLAUDE.md).
That is the field this check reads.

**Two accepted risks, recorded as accepted rather than guarded.**

1. **A duplicate whose description was never edited** posts an identical code *and*
   description, and is undetectable. But then the two CAM entries are indistinguishable
   to the operator as well as to the app, in a ten-tool library.
2. **Every string in the set is trusted permanently**, so the check is exactly as good
   as the reading of the prompt. A wrong "same tool" answer is silent and durable.

Both are accepted on the operator's judgement (2026-08-17) that tools are always drawn
from one central CAM library, which makes duplication rare and puts it in front of the
one person maintaining it. **Neither is monitored, and neither may be cited as a check
that exists.**

#### 3.5.4 Lifecycle

The pattern across all of these: **the library can always be corrected, but a
correction never silently changes what the machine will cut.**

- **Merge two entries** — the operator picks the survivor, the loser's
  `cam_descriptions` fold into it, the loser is deleted. **Rarer than it was**: one
  code is typed into both CAM libraries, so a VCarve file and a Fusion file for one
  physical cutter now resolve to one entry without help. Merging is left for
  duplicates created before the codes were assigned. The survivor keeps its own code;
  the loser's code is **not** kept as a second key, since that would reintroduce
  many-keys-to-one-tool — fold the descriptions, drop the code.
- **Deleting a tool that is in use is refused**, listing the placed parts that resolve
  to it (§3.4.1's attribution doing a second job).
- **Editing `diameter_inches` or geometry on a placed tool re-runs collision and the
  envelope check, and newly-colliding placements are DELETED, with the operator
  told.** Not flagged-and-kept: the edit exists *because* the old data was wrong, so a
  placement derived from the error is **invalid rather than merely suspect**. This is
  the first path in the app by which an already-validated placement can become wrong
  retroactively — everything else is checked once, at drop time.
- **Re-binding a toolpath to a different library tool ("shift") is an operator
  power** — selecting a valid library tool for those toolpaths.

**Two different "shift" operations exist and must not be conflated in the UI:**
re-binding a *toolpath to a library tool* (identity) versus moving a *library tool to
a pocket* (position). Blurring them re-creates the exact double-duty confusion §1
exists to remove. Identity re-binding is also the one path that can merge two
genuinely different cutters **by operator action** — and with guard (b) retired
(§3.5.3) nothing refuses it, so the UI must show enough for the operator to get it
right rather than rely on a check to catch it.

#### 3.5.5 The declared library (2026-08-17)

Source: **`Source Data/Refine Tools - Sheet1.csv`**, operator-authored — 10 tools,
columns `Name, Diameter, Geometry Class, Flute Direction, Default Slot, Vendor,
Product ID, Notes`. This is the real library, not a sample. Every §3.5.1 required
field is populated for every row, `id` excepted (app-assigned).

**That file is operator-local and deliberately untracked** (`Source Data/` is
gitignored). It is the *source* the library is transcribed from, not a repo artifact;
`tool_library.json` is what the app reads and what version control will hold. The
contents that matter are transcribed here so this section stands on its own if the CSV
moves or changes:

| name | dia. | class | flute | slot | vendor | notes |
|---|---|---|---|---|---|---|
| `0.5" x 1.25 End Mill` | 0.5 | Flat End Mill | Downcut | **2** | | |
| `0.5" x 2.0 End Mill` | 0.5 | Flat End Mill | Downcut | **2** | | 2" cutting length |
| `0.75" End Mill` | 0.75 | Flat End Mill | Downcut | **4** | | |
| `8mm End Mill` | 0.3149 | Flat End Mill | Downcut | **4** | | |
| `.5" Ball Nose` | 0.5 | Ball Nose | Upcut | 1 | | |
| `1" Round Nose` | 1.0 | Ball Nose | Straight | 3 | Freud | Maris Christmas Sets |
| `1/8 Roundover` | 0.3 | Roundover | Straight | 5 | | |
| `Table Stiffener` | 2.38 | Custom Form | Straight | 6 | | |
| `Side Handle Bit` | 1.5 | Custom Form | Straight | 7 | Rockler | |
| `.25 Bowl Bit` | 0.75 | Bowl Bit | Straight | *none* | Whiteside | |

Slot 8 is unclaimed; slots 2 and 4 are contested (§3.5.6); `.25 Bowl Bit` stages.

**The `Product ID` column is where the code goes, and it is empty on all ten rows**
(#9, 2026-08-17). The CSV's shape does not change — the column already exists; what
changes is what fills it. It carries a **shop-assigned code**, unique per physical
cutter, rather than a manufacturer part number, and **the same code is typed into
Fusion's Product id and into the VCarve tool name** (§3.1). Where the manufacturer's
part number matters for reordering it belongs in `product_link` or the notes, not
here.

Assigning those ten codes is the one prerequisite to building against this spec, and
it is the operator's to do. Until then every file orphans, which is the safe default
rather than a broken state — but nothing auto-matches, so the feature cannot be
exercised end to end. Two rows to watch while assigning: `0.5" x 1.25 End Mill` and
`0.5" x 2.0 End Mill` are the pair most likely to have been created in CAM by
duplication, which is precisely the case §3.5.3's seal exists to catch.

**`flute_direction` exists, and that was the whole blocker.** Four Downcut, one Upcut,
five Straight, no compression. §3.1's claim — that this fact lives in no file the shop
owns and therefore only the library can hold it — is now backed by data rather than
argument.

**The library is larger than the changer, as designed.** 10 tools against 8 pockets is
not an over-capacity condition: pockets are job-scoped (§3.2), the library is not. One
tool (`.25 Bowl Bit`) declares no default slot and stages on load, so §3.2.1's staging
path is exercised by real data on day one rather than being dead code.

**Two corrections this data forces:**

- **`Table Stiffener` is 2.38", not the 0.75" in `config.json`'s `T4` "Table Stiff"**
  (operator-confirmed). That is 1.19" of X-envelope inflation against 0.375" — the
  difference between a valid placement and running a hard stop. It is the sharpest
  available argument for §3.5.2: the stale number was in the file the app reads today.

  **2.38" is a rough measurement, and nothing in the app will now object to that**
  (revised 2026-08-17 under #9). The parser reads `D=59.728` mm = **2.3515"** from
  `39x35.nc`, disagreeing with the declared 2.38 by 0.0285". The earlier ruling made
  guard (b) refuse such a bind, on the reasoning that an exact-or-refuse rule is what
  gets a rough figure corrected. **Guard (b) is now retired** (§3.5.3): it compared
  against `diameter_inches`, which is *maximum cutting diameter* and therefore differs
  from a posted `D=` by design on every profile bit.

  The conclusion that ruling reached still stands, and now stands **unenforced**:
  **declared diameters must be the cutter's real geometry, not a tape-measure
  figure.** §3.5.2 makes the library the sole authority the app reads, so a rough
  number propagates straight into the X envelope check with nothing downstream to
  catch it. Where the safe direction is unclear, over-declare — that costs a placement
  that would have fit, while under-declaring puts the cutting edge somewhere the check
  called clear.

  This is the general case, not a one-off: `1/8 Roundover` is declared 0.3" while the
  only radius mill in the corpus posts `D=3.175` mm = 0.125", and the 45° chamfer mill
  in all four Rail files has **no library entry at all**. The chamfer mill still
  surfaces on first load — it orphans, and creating it is the prompt's job. The rough
  diameters do not surface at all, which is exactly why they are worth a deliberate
  pass over the ten rows before the library is built.
- **`config.json`'s `tools` map is junk in its entirety** (operator, 2026-08-17), not
  "mostly junk". Nothing in it survives — see §8.

**One schema change it forces.** `0.5" x 1.25 End Mill` and `0.5" x 2.0 End Mill` are
identical on diameter (0.5), geometry class (Flat End Mill) and flute direction
(Downcut). Under the original §3.5.1 schema they are the same entry, and the operator's
ruling is that *the length for those bits is crucial*. Hence `cutting_length_in`,
required. Note where it was hiding: `2" cutting length` sat in the free-text `Notes`
column, which has no consumer — load-bearing geometry in a field the schema does not
read. That is the §3.5.1 discipline catching a real case.

**Two questions this data opened, both now closed** (2026-08-17):

1. **The `geometry_class` enum did not match the corpus it was supposedly enumerated
   from.** Resolved in favour of the operator's five names — `Flat End Mill`,
   `Ball Nose`, `Roundover`, `Custom Form`, `Bowl Bit` — growing on demand. Reasoning
   in §3.5.1.
2. **The library's default-slot collisions are deliberate, and they are the point of
   the feature.** See §3.5.6.

#### 3.5.6 The slot-4 collision is the feature's motivating case

The library declares two tools into slot 2 (`0.5" x 1.25` and `0.5" x 2.0 End Mill`)
and two into slot 4 (`0.75" End Mill` and `8mm End Mill`). Both were read here first as
possible oversights, or as alternates that never co-run. **Neither** (operator,
2026-08-17): *"Currently we can't run parts that use the T4 tools the way we do now, so
being able to shift them to allow parts like this to run together is the point."*

So a contested default slot is **not an exception to handle — it is the defect of §1,
recorded in the library**. The corpus confirms it directly. `T4` across the library
denotes at least four physically different cutters:

```
(T4 = End Mill {.75 inches})                                  0.75"   ×2 files
(T4 = End Mill {1/4"})                                        0.25"
(T4 D=12.7 CR=0. - ZMIN=±9.525 - FLAT END MILL)               0.5"    ×2 files
(T4 D=59.728 CR=0. - ZMIN=16.129 - FORM MILL)   39x35.nc      2.35"
```

Those parts cannot be nested today: `_build_blocks` merges them all into one `T4` block
and cuts every one of them with whatever is physically in pocket 4. The library's
default slots were declared **from current CAM practice**, so they collide in exactly
the place the defect bites. The collision is a *fossil of the problem*, and the feature
is the tool that resolves it — the operator drags one cutter to a free pocket and both
parts run in one job.

Three consequences, all of which change what gets built:

- **§3.2's "dragging is the exception path" is struck** (see there). The nests this
  feature exists to enable are precisely the ones that open with a contested pocket, so
  the drag interaction carries real weight and #11 should be built for routine use, not
  for a rare corner.
- **The library UI must not refuse a duplicate default slot.** That was the live
  alternative under reading (b), and it would have made the motivating case
  undeclarable. Duplicate defaults are legitimate library state.
- **The over-subscription is structural, not a data-entry backlog.** 10 tools against 8
  pockets means the default slots *cannot* all be simultaneously honourable, no matter
  how carefully they are declared. §3.2.1's "intended nag" therefore has a floor: for a
  contested pair the app will re-propose the collision on every job that pairs them, and
  no library edit removes that — only a physical changer with more pockets would. The
  nag is correct where a deviation is temporary and is noise where the contest is
  permanent, and **the app cannot tell those apart**, because it holds no model of the
  changer's contents (§3.2). Whether that distinction is worth a field — a *deliberate*
  co-default, declared as such — is a real question for #11, and is deliberately left
  open rather than guessed at here.

One incidental verification case, for §3.5.2. The `39x35.nc` form mill posts
`D=59.728` (2.3515"); `config.json` names `T4` "Table Stiff", and the declared library
gives `Table Stiffener` as **2.38"**. If those are the same cutter — the naming says
they are, though nothing proves it — the library number is the larger, which is the
safe direction for envelope inflation, and the stale `config.json` figure of **0.75"**
is wrong by more than an inch and a half against both. That is the argument for §3.5.2
made three ways on one tool.

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

**Decided 2026-08-17 — blocks are ordered by identity, not by `T#`.**
`_build_blocks` does `for tool in sorted(by_tool)`, which sorts by the `T#`
*string* (so `"T10"` precedes `"T2"`). Renumbering pockets would therefore reorder
blocks within a pass index and change the tool-change count — and hence the runtime
estimate — for a job whose geometry did not change at all. The sort key becomes a
stable identity order, so **a remap changes the tool *word* and nothing else**:

```
- for tool in sorted(by_tool):
+ for tool in sorted(by_tool, key=identity_order):
```

Two things follow, and both are the reason to prefer this over accepting the
reordering. `tool_change_count` becomes **invariant under remapping**, so the
estimate cannot move because a pocket moved. And the §6.3 test gets to assert
token-for-token identity across the *whole file* except at `T#`/`H#` — the strong
form. Under the string sort it could only have compared per-block sets, and an
accidental geometry change could hide inside a legitimate reorder.

This is also where §3.2.1's constraint on the assigner bites, so it is repeated
here: **the assigner must run off identity-ordered data, never the remapped `T`
numbers.** An assigner that read post-remap numbers would make the emitted output
depend on an assignment that depends on the output.

---

## 5. Counting was wrong (#7 — fixed)

`app.py` computed `"tool_changes": max(0, len(ordered_tools) - 1)` — distinct
tools minus one. The true figure is `len(blocks)`, which is strictly greater
whenever a tool recurs: part A running `[T1, T2]` and part B running `[T2, T1]`
produces blocks `T1, T2, T1, T2` — four changes for two tools.

The app therefore **understated tool changes**, which understated run time and
understated the cost of the always-on-touch-off posture. It now counts off
`gcode_generator.block_tool_sequence` (live) and off the emitted file's own
`T# M06` lines (report). The count matters here beyond run time: touch-off is
charged **per change, not per distinct tool**, so this figure — not `tool_count` —
is what multiplies the 30 s in §6.1.

---

## 6. Open decisions — these block the build

Frontier (takeable now):

| # | Ticket | Type | Why it matters |
|---|---|---|---|
| ~~[#20](https://github.com/refine-drew/cnc-nest-app/issues/20)~~ | ~~Parse Fusion tool headers~~ | **done 2026-08-17** | All 26 library files now yield tools. `D`/`CR`/`TAPER`/type are read and converted from the file's declared units; the §6.2.1 `TOOLID`/`TOOLDESC` comments are read too, and the post emits them. Re-checked after: **no placement changed**, so the envelope under-inflation closed at zero cost |
| [#12](https://github.com/refine-drew/cnc-nest-app/issues/12) | Define the no-geometry-change guarantee and its proving test | grilling | Turns the hard constraint into a test. **Its one open sub-question closed 2026-08-17** — blocks sort by identity, so the test takes the whole-file form (§4.2, §6.3) |
| [#11](https://github.com/refine-drew/cnc-nest-app/issues/11) | Tool changer interface: 8 pockets, drag to reassign | prototype | **Unblocked by #10.** Surface, drag semantics and the mockup are settled in §3.4.1 — the build and the layout details are #11's. **Weightier than it looked:** dragging is the main path, not an exception (§3.5.6), the UI must *permit* duplicate default slots, and the one open question — whether a permanently-contested pair should be declarable as such — sits here |
| [#13](https://github.com/refine-drew/cnc-nest-app/issues/13) | Define the operator setup sheet | grilling | **Unblocked by #10.** Note §3.2.1: a deviation from a default slot is a **temporary** instruction ("⅜" comp → pocket 6 for this job, return to 4"), because the standard is what the operator is trained toward |

Resolved:

| # | Ticket | Answer |
|---|---|---|
| [#9](https://github.com/refine-drew/cnc-nest-app/issues/9) | Define tool identity matching, and no-match behaviour | **Resolved 2026-08-17** — see §3.1, §3.5.1, §3.5.3. Identity is a **shop-assigned code**, typed by hand into Fusion's Product id and VCarve's tool name, and matched exactly. Present and known → match; absent → orphan and bind for that run only. The alias list, its collision hole and guard (b) are all **deleted**; guard (a) survives with a new job. A **description seal** (`cam_descriptions[]`) is the only detector of a code duplicated in CAM, and it blocks |
| [#8](https://github.com/refine-drew/cnc-nest-app/issues/8) | Which safety posture? | **Posture 2 — "auto tool" on, every job** (2026-08-17, operator's call). The machine check that would confirm the self-correcting claim was **declined**; see §6.1 for what that leaves unproven. The 57 s default already prices this posture, so no code changes |
| [#6](https://github.com/refine-drew/cnc-nest-app/issues/6) | How long is one touch-off cycle? | **Swap 27 s, swap + touch-off 57 s → touch-off is 30 s** (2026-08-17, timed on the machine). Charged on every `T# M06`. See §6.1 |
| [#10](https://github.com/refine-drew/cnc-nest-app/issues/10) | Pocket auto-assignment and conflict resolution | **Resolved 2026-08-17** — see §3.2.1, §3.4, §3.5. **The assigner makes no arbitrary choices**: no tie-break (collisions surface), no fill rule (blanks stage), no write-back (defaults are prescriptive), nothing refused at placement. Determinism follows. Taken **ahead of #9** on the grounds that assignment consumes *"a set of resolved tools each with an optional default slot"* and does not care how resolution happened |
| [#7](https://github.com/refine-drew/cnc-nest-app/issues/7) | Tool-change undercount | **Fixed** — counted off the emitted blocks, not distinct tools |
| [#4](https://github.com/refine-drew/cnc-nest-app/issues/4) | Can tool identity come from the VCarve tool database? | **No** — see §3.1. The library is hand-maintained. Its "many-to-one alias list" conclusion was **superseded by #9**: identity is a hand-assigned code instead, which VCarve *can* carry, in the tool name |
| [#5](https://github.com/refine-drew/cnc-nest-app/issues/5) | Does the SS2 control honour `G43 H#`? | **Assumed yes** (2026-08-17, operator's call) — see §4.1. Syntec documents `H` as a register index; the machine check was not run. The assumption is the strict branch, so `H`-follows-pocket is load-bearing and the geometry test must cover `H` |
| [#21](https://github.com/refine-drew/cnc-nest-app/issues/21) | What stable per-tool identity can the Fusion REFINE post emit? | **`vendor` + `productId`**, or an explicit `comment`. No library-wide GUID exists; `toolId` is document-scoped and must not be used. Identity therefore rests on operator-maintained Fusion library fields — which looked like it put CAM tool hygiene on the critical path. **It does not, as of #10:** the app imports no CAM library and the alias list carries identity, so hygiene is a hit-rate improvement, not a precondition. **Revised by #9:** `productId` *is* the key after all, but because the operator writes a shop code into it — exactness comes from the shop's numbering, not from CAM hygiene. See §6.2 |

Blocked:

*(nothing.)*

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

#### The wasted-time objection, now priced (#6 resolved)

The operator's objection to posture 2 is wasted time. Timed on the machine
2026-08-17: **swap alone 27 s, swap + touch-off 57 s — touch-off is 30 s.**

The 27 s is paid under both postures, so it is not part of the tradeoff. The
30 s is the whole of it:

- **Posture 2** pays it **per tool change, on every run of the job.** Order-of-
  operations merging keeps that well below once-per-part — twelve parts sharing a
  1/4" upcut do not touch off twelve times — but it is bounded by *block count*,
  not distinct tools, so a tool the pass-index walk returns to is measured again.
  At 8–15 blocks per job: **4.0–7.5 minutes**.
- **Posture 1** pays it once per tool **physically loaded**, in setup rather than
  in the cut cycle, and not at all when the changer already holds the tools.

So the intuition was right in direction and the merging argument does not rescue
posture 2: **the cost is real, it is minutes not seconds, and it recurs on every
repeat run of the same nest.** That is the sharpest version of the tradeoff —
posture 2 buys crash-immunity with a per-run tax that production quantities
multiply, while posture 1 buys speed with a dependence on operator discipline at
load time. Note also that posture 2's safety claim is itself unconfirmed (the
`H`-register question above), so it is currently paying a known cost for an
unverified benefit.

The app prices the always-on posture by default
(`DEFAULT_TOOL_CHANGE_SECONDS = 57.0`). Pricing the other branch is
`tool_change_seconds=TOOL_SWAP_SECONDS`; the gap is
`TOUCH_OFF_SECONDS × tool_change_count`.

#### Decided: posture 2, and the check is declined (2026-08-17)

**"Auto tool" on, every job.** The per-run tax above is accepted with the cost
known. The machine check — running `G43` against a deliberately wrong register in
air to see whether Z shifts, and whether auto-tool overwrites that register — was
**explicitly declined**, so record precisely what does and does not follow:

- **What holds.** The app already prices this posture
  (`DEFAULT_TOOL_CHANGE_SECONDS = 57.0`), so nothing in `runtime_estimator` changes.
  The touch-off happens at every `T# M06` regardless of where the length lands, so
  the operator is never running on a length measured days ago.
- **What is now an accepted risk, not a proven benefit.** Posture 2 was originally
  attractive because remapping becomes *self-correcting* — a wrong pocket cannot
  propagate into a crash. That rests on the measured length landing in the `H`
  register, which is exactly what the declined check would have shown. It is
  unobserved, so **it must not be cited as the safety basis for anything the feature
  does.**
- **Consequence for §4.1.** With self-correction unproven, `H`-follows-pocket carries
  the full safety load on its own. The strict reading of #5 — derive `H` from the
  pocket, `ERROR` on any mismatch — is therefore **load-bearing, not belt-and-braces**,
  and §6.3's geometry test must cover `H` as tightly as geometry.

That is the whole of it: the posture is settled, the arithmetic is settled, and the
one unverified claim is written down as unverified rather than leaned on.

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

**2026-08-17 — settled by #10, and neither branch was needed.** The app imports no
CAM library (§3.5), so `vendor`+`productId` is a *bonus* when populated rather than
the key. Identity is carried by the **alias list**, learned one manual bind at a time
(§3.1). Hygiene therefore improves the hit rate without being a precondition, and
**§7's out-of-scope ruling stands unchanged.** The scope problem dissolved rather
than being decided.

**Superseded the same day by #9, and the resolution is better than either branch.**
The reasoning above treats `productId` as whatever the *manufacturer* published, which
is why it looked unreliable enough to need aliases behind it. It does not have to be:
**the operator writes a shop-assigned code into that field** (§3.1). So the key is
exact after all — but the exactness comes from the shop's own numbering rather than
from CAM hygiene, and it is `PRODUCT` **alone**, with `VENDOR` demoted to reordering
information. §7's ruling still stands, for a narrower reason than the one above: what
stays out of scope is the *content* of the CAM libraries, and filling in ten codes is
a bounded setup task rather than an open-ended hygiene programme.

#### 6.2.1 Decided: the REFINE post will emit an identity token (2026-08-17)

*"Let's change the Fusion post to emit what we need to make the library safer to
use."* The post is ours, so the primary corpus stops being something the app has to
guess at. **Emission format** — one comment per tool, in the header block beside the
existing `(T2 D=12.7 …)` line:

```
(TOOLID T2 VENDOR=Amana PRODUCT=46170-K FLUTES=3 TYPE=TOOL_MILLING_END_FLAT D=12.7 CR=0. DESC=1/2 downcut spiral)
```

> **The format below is the proposal, not what shipped.** The built version splits
> across two lines and carries no geometry:
>
> ```
> (TOOLID T2 VENDOR=AMANA PRODUCT=46170-K FLUTES=3)
> (TOOLDESC T2 12 DOWNCUT SPIRAL)
> ```
>
> Three constraints in `settings.comments` forced it, and they are easy to trip over
> when editing the post: comments are uppercased, filtered to `" a-z0-9.,=_-"` (so
> `/` and `"` vanish from free text), and **truncated at 80 characters**. Truncation
> *omits* fields, which would defeat the empty-vs-missing rule below — so the identity
> line carries no geometry (`D`/`CR`/`TYPE` are already on the tool-list line above
> it), and the one unbounded field, the description, sits on its own line where a
> truncation can only cost free text. **Adding a field to the `TOOLID` line means
> re-checking that budget.** Both halves are built: `post/syntec 4.cps:3088`
> `writeToolIdentity` emits them and `gcode_parser` reads them
> (`TOOLID_PATTERN` / `TOOLDESC_PATTERN`).
>
> **Under #9 the load-bearing field is `PRODUCT`, and it now carries a shop-assigned
> code** rather than a manufacturer part number (§3.1). `VENDOR` and `FLUTES` are
> retained but unread by the app. `TOOLDESC` feeds the description seal (§3.5.3),
> which is what makes it worth emitting rather than merely nice to have.

Rules the format has to obey, each for a stated reason:

- **`TOOLID` is a leading keyword**, so a parser can find the line without guessing at
  comment shapes; `T2` repeats the pocket only so the line can be tied to the header
  it annotates. **The token that matters is `VENDOR`+`PRODUCT`, and the pocket in the
  line is not part of the identity.**
- **Every field is `KEY=value`, whitespace-separated, `DESC` last** because it is the
  only free-text field and may contain spaces.
- **A missing field is emitted as an empty value, never omitted.** `VENDOR=` tells the
  app the Fusion library entry is blank; a *missing* `VENDOR` is indistinguishable
  from an older post. This is the difference between "unpopulated" and "unknown",
  and only the first is actionable by the operator.
- **`D=` and `CR=` are millimetres**, matching what the post already writes.
- The comment must be **inert to the control** — a parenthesised comment in the header
  block, before the first motion, exactly like the lines already there.

**What this fixes, and what it does not.** It kills the alias-collision hole (end of
§3.5.3) for Fusion output: two cutters can no longer arrive as one byte-identical
string, because `VENDOR`+`PRODUCT` distinguishes them even when both are
`FLAT END MILL` at `D=12.7`. It does **not** supply flute direction — Fusion has no
such field, so `up`/`down`/`compression` remains library-declared (§3.5.1) and this
changes nothing about §3.1's core claim. The win is *identity*, not *geometry*: once
`VENDOR`+`PRODUCT` pins which physical cutter this is, the library supplies everything
Fusion cannot.

**Two things stay unsolved and must not be assumed away.**

1. **VCarve files are unaffected.** Its post is not ours, so the six notation variants
   in F9 and the `T2`/`T9` byte-identical case remain exactly as measured. VCarve is
   retained for simple one-offs, so the alias-collision hole shrinks to that corpus —
   **it does not close.** §3.5.3's `(filename, T#)` scoping is what closes it.

   *Amended 2026-08-17, from the #20 parse:* **the hole is not VCarve-exclusive, and
   the corpus proves it.** Now that Fusion headers parse, `1001-combined.nc` and
   `1001-combined-Zbottom_1.nc` are visible as having `T2` **and** `T4` both posting
   `FLAT END MILL D=12.7 CR=0.` byte-for-byte — the same two-cutters-one-string case,
   in Fusion output. `TOOLID` fixes it *going forward only*: all 9 existing Fusion
   files predate the comment, so they carry the hole until re-posted. (These two
   particular files are the #23 junk hand-merged pair already slated for re-post, but
   the mechanism is general.) Read claim 1 as "VCarve permanently, Fusion until
   re-posted."
2. **`vendor`+`productId` must actually be populated in the Fusion library**, per
   cutter, uniquely. The post can only emit what Fusion holds. This is now a
   *quality-of-outcome* dependency rather than a precondition — a blank pair degrades
   to alias matching (§3.1), which still works — so §7's ruling continues to stand,
   but the empty-value rule above exists so the app can *say* which entries are blank
   instead of silently matching worse.

### 6.3 Shape of the geometry guarantee (#12)

Generate the master G-code twice from identical placements — once with the
identity pocket map, once remapped — and assert the outputs are **token-for-token
identical except at `T#` and `H#`**. Any accidental geometry change becomes a test
failure by construction, rather than something a reviewer has to notice.

**The whole-file form of that assertion is available as of the §4.2 decision**
(2026-08-17): with blocks ordered by identity, a remap cannot reorder anything, so
the diff between the two files is *exactly* the tool and offset words. There is no
need to weaken the comparison to per-block sets.

Per §6.1, `H` must be covered as tightly as the geometry words, not treated as an
incidental passenger: with posture 2's self-correction unproven, `H`-follows-pocket
carries the safety load alone. So the test asserts two things about the exception
set, not one — that `T#` moved as the pocket map says, **and** that every `H`
still equals its preceding `T`.

---

## 7. Out of scope

- **Building the feature.** This map's destination is the spec; implementation is
  a separate effort started from it.
- **Mid-program manual tool reloads** to exceed 8 distinct tools. `≤8` is a hard
  precondition; exceeding it stays a hard failure. (The operator said all 8 tools
  live in the changer for the full program "for now" — if that changes, this
  becomes a fresh effort, not a resumption.)
- **CAM-side tool hygiene in Fusion/VCarve.** A parallel effort already intended,
  independent of this map. *Amended 2026-08-17:* **the REFINE post itself is in
  scope** — §6.2.1 specifies an identity comment it must emit, because we own that
  post. What stays out is the *content* of the CAM libraries: assigning a code per
  cutter (§3.1) is the operator's work, and without it a tool orphans to a manual bind
  rather than failing. *Revised again under #9:* that content is no longer open-ended
  hygiene but **one bounded task — ten codes, typed into two tool databases**. Out of
  scope still, but it is now a prerequisite with a finish line rather than a standing
  effort, and §3.5.5 names it as the one thing blocking an end-to-end trial.

## 8. Not yet specified

**This list is now empty of blockers.** Everything it opened with is closed below; what
remains under "New" is one deliberately deferred non-dependency.

Closed since this list was written:

- ~~**Schema migration** from `config.json`'s `T#`-keyed `tools` map.~~ **There is no
  migration** (2026-08-17). §3.5 said "mostly discard"; the operator's ruling is that
  the map is **junk in its entirety** and the declared list (§3.5.5) is the real one.
  `tool_library.json` is therefore **seeded from the CSV and the `tools` map is
  deleted**, which is strictly safer than a partial carry-over: `T4` "Table Stiff"
  declares 0.75" for a cutter that is actually 2.38", so migrating it would import a
  number that under-inflates the X envelope by 0.8" on a real tool. Nothing keyed by
  pocket survives into a library keyed by identity — which is the whole point of §1.
- ~~**Over-capacity behaviour after de-dup.**~~ **Answered** — §3.4. Capacity stops
  being a rule at all: the ninth tool simply has nowhere to go. The framing holds
  whether resolution raises or lowers the count, because it never needed to know which.
- ~~**Pinning** a tool to a pocket the auto-assigner may not move.~~ **Moot** — with
  no fill rule, no tie-break and no write-back, **the assigner never moves anything**
  (§3.2.1). There is nothing to pin against.
- ~~**`.cnj` job files must carry the pocket assignment.**~~ **Void** — save/load job
  is sunset (§3.2). The pocket map is pure in-memory job state.
- ~~**Fusion-era identity.**~~ **Answered** by #21 and §6.2: `vendor`+`productId` when
  populated, `comment` as fallback — and per §3.1 the alias list carries identity
  regardless, so this no longer gates anything.

New, opened by #10:

- ~~**The alias-collision hole**~~ **Void** — the mechanism it was a hole in no longer
  exists (#9, 2026-08-17). It was first answered by scoping a bind to `(filename, T#)`,
  then made moot hours later: with identity carried by a code, there are no aliases to
  collide, no string is ever compared, and a bind is job-scoped so nothing durable is
  keyed on `T#` at all. §3.5.3 replaces both answers.

  **A different hole opened in its place, and it is not the same shape.** Aliases could
  collide because a *string* was the key; a code can be duplicated because a *human*
  assigns it. The first was a property of the data the app read, detectable by
  inspecting one file. The second is a property of the CAM library the app cannot see,
  detectable only through the description seal — which catches it whenever the operator
  edited the description, and never when they did not.
- **Fusion/VCarve library sync** — deliberately deferred (§3.5), explicitly not a
  dependency. Whoever takes it must pin a real `.tools` / `.vtdb` file as a fixture;
  neither format has been verified against an actual file yet. Note the Fusion library
  *file* carries a per-tool `guid` even though no GUID can reach a **posted file**
  (#21 is right about the post) — different problems, different keys, and the guid is
  what a re-import should reconcile on.

---

## 9. Code map

| File | Role in this feature |
|---|---|
| `gcode_generator.py:159` `_build_blocks` | Groups by raw `T#` — the merge defect lives here. Its `sorted(by_tool)` becomes an identity order (§4.2) |
| `gcode_generator.py:101` | Derives `H` from `T` — must move with the pocket |
| `gcode_generator.py:56-71` | Capacity check counts `T#` strings, not physical tools |
| `app.py:241` `_tool_compatibility` | Already detects the conflict; advisory only |
| `app.py:232` | Under-reports tool changes (§5) |
| `gcode_parser.py:137` `extract_tools` | Source of per-file tool data. What the feature reads from it narrows to the **code token** and `cam_description`; the parsed diameter stops being authoritative (§3.5.2) |
| `gcode_parser.py` `_extract_diameter` | **Retire the bare-decimal fallback** — it would read a code like `RK-004` as a 0.04" cutter, a 25× under-inflation (§3.5.2) |
| `gcode_parser.py` `TOOLID_PATTERN` / `_toolid_fields` | Already reads `PRODUCT=`; that value becomes the match key. The `""`-vs-absent distinction stays load-bearing — empty means the CAM entry needs a code |
| `post/syntec 4.cps:3088` `writeToolIdentity` | Already emits `PRODUCT=` from `tool.productId`. **No post change is needed for #9** — the field just stops being empty |
| `tool_library.py` | Today a `T#`→diameter registry; becomes the identity library keyed by **code** (§3.5) |
| `tool_library.py:39` `find_unknown_tools` | Existing block-on-unresolvable pattern to mirror |
| `config.json` `tools` | **Delete** — junk in its entirety, migrates nothing (§8). Its `T4` diameter is wrong by 1.6" |
| `Source Data/Refine Tools - Sheet1.csv` | The operator's declared library — 10 tools; seeds `tool_library.json` (§3.5.5). Its **`Product ID` column carries the shop code** and is empty on every row today |
| `tool_library.json` | **New** — the identity library, **`code`-keyed**, operator data (§3.5) |
| `static/bed.js`, `static/sidebar.js` | Where the changer UI has to live |
| `templates/index.html:24` `#main` grid | The dock is a new full-width row above `#statusbar` (§3.4.1) |
| `templates/index.html:201` `#compat-section` | **Retired** by the changer dock — this is the screen budget |
| `static/job.js:36`, `index.html:342` | The Generate gate, already present; re-point it at §3.4's three rules |
| `app.py:661` | The 422 gate, already present; becomes a backstop |
| `collision.py:204` `_max_tool_radius` | Reads the parsed file's diameter; must read the library instead (§3.5.2) |
| `app.py` `/api/save-job`, `/api/load-job` | **Delete** — save/load is sunset (§3.2) |
| `pdf_report.py` | Natural home for the setup sheet |
