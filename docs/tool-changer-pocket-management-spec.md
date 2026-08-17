# Tool Changer Pocket Management — Spec

**Status: incomplete by design.** Sections 1–5 are settled and safe to build
against. Section 6 lists the decisions that are still open.

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
no parseable diameter at all. The library must therefore be **operator-declared**,
keyed independently of `T#`, and must carry a **geometry class** plus a
many-to-one **alias list** of the raw strings seen in files — which is what lets
one library serve VCarve and Fusion output without a second mechanism.

**A Fusion tool library will not remove the need for this.** The app reads posted
`.nc`, not CAM source, so CAM knowledge reaches the app only through what the
post-processor writes into the file. The corpus is **not** almost entirely VCarve —
9 of 26 library files are already Fusion-posted, and the parser read **no tools
whatsoever** from them until [#20](https://github.com/refine-drew/cnc-nest-app/issues/20)
closed on 2026-08-17. A future Fusion library does not retroactively fix existing files. Most
fundamentally, CAM records *what a tool is*; this app must record *where it
lives*. Good CAM hygiene makes matching reliable — it does not make the app-side
library unnecessary.

**2026-08-17 — the alias list is the identity mechanism, not the match key (#10).**
Because the library is hand-typed and the app imports nothing (§3.5), `vendor` +
`productId` cannot be relied on to match byte-for-byte what a post wrote into a
file. First encounter of a tool therefore usually **misses**; the operator binds it
once by hand and the alias remembers it. **Auto-matching is the steady state,
manual binding is the onboarding step** — which is why the alias list, introduced
above as a supporting detail, is in fact load-bearing.

This also settles the scope question §6.2 raised: with aliases carrying identity,
CAM-side tool hygiene improves the hit rate but is **not a precondition** of the
feature, so §7's out-of-scope ruling stands.

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
| `id` | ✔ | primary key — app-assigned, **never a `T#`**. What the pocket map references |
| `name` | ✔ | changer dock, setup sheet (#13) — e.g. `1/2" downcut spiral` |
| `diameter_inches` | ✔ | **maximum cutting diameter** (§3.5.2) — tool-radius collision, **X envelope inflation**; **replaces** the parsed-file diameter |
| `geometry_class` | ✔ | identity discrimination + dock/setup-sheet display. `Flat End Mill` / `Ball Nose` / `Roundover` / `Custom Form` / `Bowl Bit` — **the operator's own names, and only tools that exist** (§3.5.5) |
| `flute_direction` | ✔ | `up` / `down` / `compression` / `straight` — **the field no file can supply** (§3.1); what blocks the merge |
| `cutting_length_in` | ✔ | identity discrimination — for two same-diameter cutters of differing length it is the **only** separating field (§3.5.5) |
| `default_slot` | — | pocket seeding; **null → staged**, and null is a legitimate answer |
| `aliases[]` | — | the actual match mechanism (§3.1); grows one entry per manual bind |
| `vendor` + `product_id` | — | exact match when populated (#21); usually blank with no import |
| `corner_radius_in`, `taper_deg`, `flutes` | — | **verification only, never keys** — check the file's `CR=` / `TAPER=` against the declaration |

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

Fusion's headers also make verification non-vacuous: `D=12.7 CR=6.35 TAPER=45DEG`
can be checked *against* the declaration rather than trusted as identity.

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

#### 3.5.3 Match-on-load, and the two guards

Loading a job resolves each file tool against the library:

- **Match** → bind, take its default slot.
- **No entry** → prompt the operator to **create a new tool** (this is where a default
  slot gets declared).
- **No automatic match** → the operator **binds it by hand**, and the alias remembers.

Two guards, both biased against §3.1's dangerous direction:

- **(a) Resolution is injective within a single file.** Two distinct `T#` in one `.nc`
  must **never** resolve to the same library tool — CAM already asserted they differ
  by giving them different pockets. A collision here is a **hard stop requiring manual
  disambiguation, never a merge.** This is what catches the measured `T2`/`T9`
  byte-identical case. Deliberately **per file only**: two *different* files' `T4`
  resolving to one library tool is the feature working as intended.
- **(b) A manual bind is refused on diameter disagreement.** Binding a 0.25" toolpath
  to a 0.5" library tool is scrap or a crash with no legitimate use, so refuse rather
  than warn. Where diameter agrees but flute direction cannot be read from the file —
  always, per §3.1 — the app binds what the operator says and that ambiguity is theirs.
  **Exact, with no tolerance** (decided 2026-08-17): the declared library figure must be
  the cutter's real geometry rather than a measurement, and the refusal is what gets a
  rough figure corrected. Three rows of the shipped library fail this on first load —
  see §3.5.5, which is where the reasoning lives.

**Resolved 2026-08-17 — the bind is scoped to `(filename, T#)`.** Guards (a) and the
alias mechanism were in tension on one real file: `T2` and `T9` emit the same string
in the same file, so (a) correctly refuses to merge them, but the manual bind then has
nothing durable to key on except `T#` — the token this feature exists to distrust. The
operator's call is to **accept `T#` as a discriminator, but only inside the file it was
read from**:

```
aliases: [
  { string: "End Mill {0.5 inch}", file: "1001.nc", tool: "T2" } -> 1/2" downcut
  { string: "End Mill {0.5 inch}", file: "1001.nc", tool: "T9" } -> 1/2" compression
]
```

Two properties this has and the rejected candidates did not. **It keeps the file
usable** — the alternative was hard-stopping any VCarve file with colliding tool
strings, which takes files out of the library until they are re-posted. And **it
expires by construction**: a re-post of that file invalidates the bind, which is
correct rather than unfortunate, because a re-post may genuinely have changed which
cutter sits in `T9`. A file-scoped bind cannot leak across files, so the cross-file
`T#` trust §3.1 warns about is never created — which is exactly why the bare-`T#`
variant was rejected.

The scoping is **narrower than an alias, and must be stored as such.** An unqualified
alias (`string` → tool) still matches anywhere; a `(string, file, T#)` alias matches
only that file. A plain string alias must therefore never be *derived* from a
file-scoped bind by dropping its qualifiers — that would silently widen a bind the
operator made under a collision. Where a file has no collision, the ordinary
string alias still applies and nothing is scoped.

#### 3.5.4 Lifecycle

The pattern across all of these: **the library can always be corrected, but a
correction never silently changes what the machine will cut.**

- **Merge two entries** — the operator picks the survivor, the loser's aliases fold
  into it, the loser is deleted. That is all a merge needs to be, because aliases
  carry identity: merging is the operator saying *"these strings were always the same
  cutter."* Required because a VCarve file and a Fusion file for one physical cutter
  will otherwise produce two entries.
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
genuinely different cutters **by operator action**, which is what guard (b) is for.

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

  **2.38" is a rough measurement, and that decides how guard (b) behaves** (operator,
  2026-08-17). The parser reads `D=59.728` mm = **2.3515"** from `39x35.nc`, which
  disagrees with the declared 2.38 by 0.0285". §3.5.3 guard (b) refuses a manual bind
  on diameter disagreement, so as it stands this real pair would be **refused**. The
  operator's ruling is to **fix the declared figures when the library is built, not to
  loosen the guard** — so:

  - **Guard (b) stays exact. Do not add a tolerance.** A tolerance is the wrong shape
    for the risk it guards: the failure it exists to catch is binding a 0.25" toolpath
    to a 0.5" tool, and any tolerance wide enough to absorb tape-measure error is
    narrower than nothing useful and wider than zero — it buys nothing and licenses
    drift. Exact-or-refuse also makes the refusal *informative*: it tells the operator
    their declared number is wrong, which is the only way a rough figure ever gets
    corrected.
  - **Therefore declared diameters must be the cutter's real geometry, not a
    measurement.** For any tool that appears in a posted file, the file's own `D=` is
    the better source — and §3.5.2 already makes the library the authority the *app*
    reads, so a rough number there propagates straight into the X envelope check. The
    library UI should show the parsed `D=` alongside the declared value on a refused
    bind, so correcting it is one glance rather than an investigation.

  This is the general case, not a one-off: `1/8 Roundover` is declared 0.3" while the
  only radius mill in the corpus posts `D=3.175` mm = 0.125", and the 45° chamfer mill
  in all four Rail files has **no library entry at all**. Both surface as refusals or
  no-matches on first load, which is the system working — but they are the same
  rough-declaration problem, so expect to correct several rows, not one.
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
| [#8](https://github.com/refine-drew/cnc-nest-app/issues/8) | Which safety posture? | **Posture 2 — "auto tool" on, every job** (2026-08-17, operator's call). The machine check that would confirm the self-correcting claim was **declined**; see §6.1 for what that leaves unproven. The 57 s default already prices this posture, so no code changes |
| [#6](https://github.com/refine-drew/cnc-nest-app/issues/6) | How long is one touch-off cycle? | **Swap 27 s, swap + touch-off 57 s → touch-off is 30 s** (2026-08-17, timed on the machine). Charged on every `T# M06`. See §6.1 |
| [#10](https://github.com/refine-drew/cnc-nest-app/issues/10) | Pocket auto-assignment and conflict resolution | **Resolved 2026-08-17** — see §3.2.1, §3.4, §3.5. **The assigner makes no arbitrary choices**: no tie-break (collisions surface), no fill rule (blanks stage), no write-back (defaults are prescriptive), nothing refused at placement. Determinism follows. Taken **ahead of #9** on the grounds that assignment consumes *"a set of resolved tools each with an optional default slot"* and does not care how resolution happened |
| [#7](https://github.com/refine-drew/cnc-nest-app/issues/7) | Tool-change undercount | **Fixed** — counted off the emitted blocks, not distinct tools |
| [#4](https://github.com/refine-drew/cnc-nest-app/issues/4) | Can tool identity come from the VCarve tool database? | **No** — see §3.1. The library is hand-maintained, with aliases |
| [#5](https://github.com/refine-drew/cnc-nest-app/issues/5) | Does the SS2 control honour `G43 H#`? | **Assumed yes** (2026-08-17, operator's call) — see §4.1. Syntec documents `H` as a register index; the machine check was not run. The assumption is the strict branch, so `H`-follows-pocket is load-bearing and the geometry test must cover `H` |
| [#21](https://github.com/refine-drew/cnc-nest-app/issues/21) | What stable per-tool identity can the Fusion REFINE post emit? | **`vendor` + `productId`**, or an explicit `comment`. No library-wide GUID exists; `toolId` is document-scoped and must not be used. Identity therefore rests on operator-maintained Fusion library fields — which looked like it put CAM tool hygiene on the critical path. **It does not, as of #10:** the app imports no CAM library and the alias list carries identity, so hygiene is a hit-rate improvement, not a precondition. See §6.2 |

Blocked:

*(nothing — #9 was the last entry and #20, its blocker, closed 2026-08-17.)*

| # | Ticket | Status |
|---|---|---|
| [#9](https://github.com/refine-drew/cnc-nest-app/issues/9) | Define tool identity matching, and no-match behaviour | **Unblocked** — #20 and #21 both closed. It now owns only the matching rule itself; every branch that surrounded it (no-match, manual bind, the two guards, the alias-collision hole) is settled in §3.5.3 |

#10 answered several of #9's branches in passing — no-match prompts tool creation,
manual binding is supported, and the two guards in §3.5.3 are settled. The
alias-collision hole **closed 2026-08-17**: a bind under a string collision is scoped
to `(filename, T#)` (§3.5.3). What #9 still owns is the **matching rule itself**.

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

#### 6.2.1 Decided: the REFINE post will emit an identity token (2026-08-17)

*"Let's change the Fusion post to emit what we need to make the library safer to
use."* The post is ours, so the primary corpus stops being something the app has to
guess at. **Emission format** — one comment per tool, in the header block beside the
existing `(T2 D=12.7 …)` line:

```
(TOOLID T2 VENDOR=Amana PRODUCT=46170-K FLUTES=3 TYPE=TOOL_MILLING_END_FLAT D=12.7 CR=0. DESC=1/2 downcut spiral)
```

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
  post. What stays out is the *content* of the Fusion tool library: populating
  `vendor`/`productId` per cutter is the operator's parallel effort, and the app
  degrades to alias matching without it.

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

- ~~**The alias-collision hole**~~ **Answered** 2026-08-17 — §3.5.3. A bind made under a
  string collision is scoped to `(filename, T#)`: `T#` is accepted as a discriminator
  but only inside the file it was read from, so the file stays usable and the bind
  expires on re-post rather than leaking across files.
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
| `gcode_parser.py:137` `extract_tools` | Source of per-file `{T#: {description, diameter}}` |
| `tool_library.py` | Today a `T#`→diameter registry; becomes the identity library (§3.5) |
| `tool_library.py:39` `find_unknown_tools` | Existing block-on-unresolvable pattern to mirror |
| `config.json` `tools` | **Delete** — junk in its entirety, migrates nothing (§8). Its `T4` diameter is wrong by 1.6" |
| `Source Data/Refine Tools - Sheet1.csv` | The operator's declared library — 10 tools; seeds `tool_library.json` (§3.5.5) |
| `tool_library.json` | **New** — the identity library, `id`-keyed, operator data (§3.5) |
| `static/bed.js`, `static/sidebar.js` | Where the changer UI has to live |
| `templates/index.html:24` `#main` grid | The dock is a new full-width row above `#statusbar` (§3.4.1) |
| `templates/index.html:201` `#compat-section` | **Retired** by the changer dock — this is the screen budget |
| `static/job.js:36`, `index.html:342` | The Generate gate, already present; re-point it at §3.4's three rules |
| `app.py:661` | The 422 gate, already present; becomes a backstop |
| `collision.py:204` `_max_tool_radius` | Reads the parsed file's diameter; must read the library instead (§3.5.2) |
| `app.py` `/api/save-job`, `/api/load-job` | **Delete** — save/load is sunset (§3.2) |
| `pdf_report.py` | Natural home for the setup sheet |
