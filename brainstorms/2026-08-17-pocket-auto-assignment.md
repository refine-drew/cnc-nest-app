# Pocket Auto-Assignment & Conflict Resolution: Brainstorm / Discovery Notes
Date: 2026-08-17 · Goal: Settle issue #10 — how pockets are assigned automatically, how ties are broken, what the operator is asked to resolve by hand, and when assignment is refused.

- Ticket: [#10](https://github.com/refine-drew/cnc-nest-app/issues/10) (`wayfinder:grilling`)
- Map: [#3](https://github.com/refine-drew/cnc-nest-app/issues/3) · Spec: `docs/tool-changer-pocket-management-spec.md`
- Prior session: `brainstorms/2026-08-12-tool-changer-pocket-management.md`

## Summary / key decisions

_(running synthesis — updated after every answer)_

1. **An unhonoured preferred pocket is real physical work**, not a lost
   familiarity. Operator's words: *"An unhonored preference is real physical
   work."* So the assigner's objective is to **minimise physical tool moves**, and
   honouring preferred pockets is the proxy for that. Every tool landing on its
   preference is a cutter the operator never touches; every one that doesn't is a
   pull, a re-seat and a touch-off.
2. **The app does not auto-resolve pocket collisions. It surfaces them and blocks
   generation.** Two tools may sit in the same pocket as a *temporary, visible,
   invalid* state; the **Generate G-code button is disabled until the pocket map is
   valid** (one tool per pocket). Auto-assign proposes, the operator disposes.
   - Consequence: **no tie-break rule is needed for the automatic step.** The
     assigner seeds each tool's preferred pocket as-is, collisions included, and
     never silently picks a winner. This is what the operator asked for and it is
     the safer direction — the app cannot know which cutter is cheaper to move,
     because §3.2 deliberately gives it no model of the changer's contents.
   - Independent reason the invalid state must be *allowed* rather than refused at
     drop time: **swapping two tools is impossible without transiting a
     double-occupied pocket.** Refusing the drop deadlocks the swap. This also
     answers one of #11's open points — dropping onto an occupied pocket makes the
     two **coexist** (flagged), it does not swap them and is not refused.
   - This is a **different gating pattern from the rest of the app**, which refuses
     the offending *action* (`/api/place` rejects a colliding placement,
     `find_unknown_tools` blocks placement on an unresolvable diameter). Pockets
     gate the *outcome* instead. The difference is deliberate and traceable to the
     swap-deadlock argument above.
3. **The app never guesses a pocket.** Tools with no default slot go to a
   **"needs a home" staging area**, not into the lowest free pocket. Auto-fill was
   recommended and **rejected**.
   - The day-one objection to this (every job opening with 8 mandatory drags)
     mostly dissolves under decision 4: the **create-tool prompt is where the
     default slot is set**, so a populated library means almost every tool arrives
     with a preference. The staging area is a genuine exception path, not the
     normal one — §3.2 holds.
4. **A tool management library, with its own UI, is part of this feature.** It holds
   identity *and* **the default slot per tool** — that is where the preferred
   pocket is declared. The spec says only "the operator will build one"; that a
   management GUI is in scope is new here.
5. **Match-on-load flow:** loading a job checks each file tool against the library.
   - **Match** → bind, take its default slot.
   - **No entry** → prompt the operator to **create a new tool**.
   - **No automatic match** → the operator can **tell the software which library
     tool it is** (manual bind).
   This answers #9's no-match and manual-bind branches ahead of #9 itself.
6. **Duplicate identities must be mergeable.** A VCarve file and a Fusion file for
   the same physical cutter can produce **two library entries** for one tool; the
   operator needs to correct that. Mechanism is already specified — §3.1's
   many-to-one **alias list**; merging = moving both raw strings onto one identity.
7. **Toolpath→tool re-binding ("shift") is an operator power.** *"Shift would
   involve selecting a valid tool from the library for those toolpaths."*
8. **Two distinct "shift" operations now exist and must not be conflated in the
   UI:** re-binding a *toolpath to a library tool* (identity) versus moving a
   *library tool to a pocket* (position). Blurring them re-creates the exact
   double-duty confusion the feature exists to remove. Identity re-binding is also
   the one path that can merge two genuinely different cutters **by operator
   action**, which is §3.1's dangerous direction — see Q5.
9. **The alias list is the identity mechanism, not the match key.** A consequence of
   decision 4 (no import): the operator hand-types the library, so `vendor` +
   `productId` (#21) cannot be relied on to match byte-for-byte what the post wrote.
   First encounter of a tool therefore usually **misses**, the operator binds it once
   by hand, and the alias remembers it. Auto-matching is the steady state, manual
   binding is the onboarding step.
   - **This settles §6.2's open scope question.** §6.2 warned that if identity keys
     on `productId`, CAM-side tool hygiene stops being a parallel effort and becomes
     a **precondition** of the feature. With aliases carrying identity, it does not:
     hygiene improves the hit rate, and the app learns from corrections regardless.
     §7's out-of-scope ruling on CAM hygiene **stands**.
10. **Two guards on identity resolution** (confirmed — *"Yes that makes sense."*):
    - **(a) Resolution is injective within a single file.** Two distinct `T#` in one
      `.nc` must never resolve to the same library tool — CAM already asserted they
      differ by giving them different pockets. Collision here is a **hard stop
      requiring manual disambiguation**, never a merge. This is what catches the
      measured `T2`/`T9` byte-identical-string case from §3.1. Deliberately **per
      file only**: two *different* files' `T4` resolving to one library tool is the
      feature working as intended.
    - **(b) A manual bind is refused on diameter disagreement.** Binding a 0.25"
      toolpath to a 0.5" library tool is scrap or a crash with no legitimate use, so
      refuse rather than warn. Where diameter agrees but geometry class is
      unreadable from the file (upcut / downcut / compression are identical on
      paper), the app binds what the operator says and the ambiguity is theirs.
    - Together: the operator can always fix a mismatch, and can never silently
      merge two cutters.
11. **The default slot is prescriptive, not descriptive — a drag never writes back
    to the library.** Operator's reasoning: *"it's theoretically what should be done
    for each job. Operators will get used to seeing the tools in the same spots, so I
    want to encourage that behavior."* The library states where a tool **belongs**;
    the app re-proposing that pocket on the next job is the **intended nag**, not
    churn. A drag is a **job-scoped override only.**
    - The library UI is therefore the **only** place a default slot changes, which
      makes the management UI from decision 4 load-bearing rather than convenient.
    - **Implication for #11:** the changer dock must visually mark a tool sitting
      **away from its default pocket**, or the deviation is invisible and never gets
      restored.
    - **Implication for #13:** a deviation belongs on the setup sheet as a
      **temporary** instruction — "⅜" comp → pocket 6 for this job, return to 4" —
      because the standard is what the operator is being trained toward.
12. **Nothing is ever refused at placement — not even exceeding 8 tools.** A drag onto
    an occupied pocket is allowed (Q1), a drag onto an empty one always works, and
    placing a part that pushes the job past 8 distinct tools is allowed too. **Only
    generation is refused.**
    - The operator's reason is stronger than the consistency argument that was
      offered: *"two tools may need to be merged together"* — at placement time the
      distinct-tool count **is not yet final**, because identity merging (decision 6)
      can still lower it. Blocking the 9th tool would block a job that is actually an
      8-tool job once the operator corrects a duplicate identity.
    - Over-capacity needs **no special mechanism**: the 9th tool is just a tool with
      nowhere to go, sitting in "needs a home" with the map invalid and Generate
      disabled. `gcode_generator`'s `ValueError` and the 422 at `app.py:661` become
      **backstops**, the way `gcode_validator` backs up the envelope check.
    - *"Eventually, the operator will figure out that they've assigned too many tools
      if they can't get them to fit into the eight slots. They'll just need to delete
      one of the parts that adds extra tools."*
13. **Every tool must show its parent part(s).** *"It would be helpful if each tool
    showed which parent part put it there, so you could see how many parts are being
    used by each of the tools and easily identify the one part that needs to be
    removed to remove a tool from the changer."*
    - **The data already exists.** `app._tool_compatibility()` builds per-tool
      `usages` of `{filename, description, diameter_inches}`. This is the panel the
      changer dock **retires** (F8) — so its usage data is repurposed, not rebuilt.
    - Show **placed instances**, not just filenames, since a part can be placed more
      than once (`_instance_counts`).
    - Message wording must distinguish the **fixable** case (tools unplaced, free
      pockets exist) from the **unfixable** one (changer full — dragging cannot help,
      remove parts). Operators are not developers; "3 tools have no home" reads as a
      to-do in one case and a stop in the other.
14. **The tool library entry — confirmed schema.** Discipline: *every field has a named
    consumer; a field without one does not belong.*

    | field | req? | consumed by |
    |---|---|---|
    | `id` | ✔ | primary key — app-assigned, **never a `T#`**. What `.cnj` and the pocket map reference |
    | `name` | ✔ | changer dock, setup sheet (#13) — e.g. `1/2" downcut spiral` |
    | `diameter_inches` | ✔ | tool-radius collision, **X envelope inflation**; **replaces** the parsed-file diameter (F10) |
    | `geometry_class` | ✔ | `flat`/`ball`/`radius`/`chamfer`/`form`/`drill` — enumerated from the real corpus (F9/F10) |
    | `flute_direction` | ✔ | `up`/`down`/`compression`/`straight` — **the field no file from either CAM can supply**; what blocks the §3.1 merge |
    | `default_slot` | ? | pocket seeding; null → "needs a home" |
    | `aliases[]` | — | the actual match mechanism (decision 9); grows one entry per manual bind |
    | `vendor` + `product_id` | — | exact match when populated (#21); usually blank with no import |
    | `corner_radius_in`, `taper_deg`, `flutes` | — | **verification only, never keys** — lets Q5(b) check the file's `CR=`/`TAPER=` against the declaration |

    - `geometry_class` and `flute_direction` are **deliberately split**: up/down/
      compression is orthogonal to flat/ball/chamfer, so two short lists beat a dozen
      combined classes.
    - **Explicitly excluded: feeds, speeds, stepover, tool length, holder.** CAM owns
      cutting parameters; the app needs geometry only for collision, envelope and
      identity. Stated as a boundary so the schema stops growing.
    - **The library is the diameter authority.** This is what routes around #20 for
      safety purposes: a Fusion file that parses to diameter 0 still inflates the X
      envelope correctly once its tool resolves to a library entry.
15. **`default_slot` is optional.** A tool may exist with no declared home, meaning
    *"I haven't decided where this lives."* Those tools stage on every job until the
    operator assigns one. This is what keeps the staging area (decision 3) meaningful
    instead of dead code, and it avoids forcing a **guess** at create time — a guessed
    default is a wrong prescription, and decision 11 made getting the prescription
    right the library's whole job.
16. **The library lives in its own `tool_library.json`**, not inside `config.json`.
    Operator data with its own lifecycle: backed up separately, portable to a second
    machine, and it keeps a growing library out of the settings file that the settings
    panel writes. `config.json`'s `tools` map seeds a **one-time migration**, and most
    of it is junk to discard rather than carry (`T99` "test", `T5` with an empty name).
17. **Save/load job is sunset.** *"I want us to sunset the save/load job feature. These
    are quick enough to build that I don't ever see us needing to load a saved job."*
    `/api/save-job` and `/api/load-job` already have no GUI (the buttons were removed
    as unused); now the routes, the `.cnj` format and their tests go too.
    - **This deletes requirements from the spec, not just code.** §3.2's *"assignment
      … **must** round-trip through the `.cnj` save/load format"* is void, and §8's
      pending schema item *".cnj job files must carry the pocket assignment"* is
      struck. The pocket map is **pure in-memory job state**, like `_placements`.
    - It also removes the whole class of "stale override in a saved job" problems.
18. **Library merge = pick a survivor, fold the loser's aliases in, delete the loser.**
    Confirmed. That is all a merge needs to be, because aliases carry identity —
    merging is the operator saying *"these strings were always the same cutter."*
19. **Deleting a tool that is in use is refused**, listing the placed parts that
    resolve to it. Confirmed. Reuses decision 13's parent-part attribution.
20. **Editing diameter or geometry on a placed tool re-runs collision and envelope, and
    newly-colliding placements are DELETED, not flagged-and-kept.** Confirmed, and
    **stronger than the recommendation offered** (which was flag-without-dropping).
    Operator's reasoning: *"The change was made because the original data was wrong in
    the first place. Delete any placements that created the collision and flag to
    user."* → the corrected data is authoritative, so a placement derived from the
    error is **invalid rather than merely suspect**. The operator is told what was
    removed and why.
    - This is the first path in the app by which an **already-validated placement can
      become wrong retroactively** — everything else is checked once at drop time.
    - Consistent with decision 17: both rest on placements being cheap to rebuild.

---

## Scope note: #10 is only partly blocked by #9

The spec lists #10 as blocked by #9 (identity matching). Three of #10's four
questions — tie-break, determinism, refusal — take as input *"a set of distinct
resolved tools, each carrying an optional preferred pocket"* and do not depend on
**how** that resolution happened. Only "what counts as a conflict worth showing"
leans on #9, and it can be settled in terms of #9's **outcome classes** (matched /
no-match / ambiguous) rather than its matching rule.

Taken on that basis. Anything that turns out to need the matching rule itself gets
flagged back to #9 rather than guessed at here.

---

## Prior findings from the code (established before Q1, not from the user)

Inputs to the grilling, read out of the repo.

**F1 — An execution-ordered distinct-tool list already exists.**
`app._tool_compatibility()` walks pass indices `0..max` across all placed parts
and builds `ordered_tools` — the distinct tools in job execution order. Same walk
as `_build_blocks`. Any assignment rule that wants "execution order" as a
tie-break already has it computed.

**F2 — The existing conflict surface is per-`T#` description disagreement.**
Same function sets `conflict = len({u["description"] for u in usages}) > 1` and
`has_conflict`. Advisory only — nothing blocks generation. This is the "two tools
in position 4" symptom from the brief, and it is a symptom of `T#` doing double
duty. Once identity is split out it should largely evaporate, which is exactly
what #10 has to decide the replacement for.

**F3 — There is no preferred-pocket field today, because the pocket *is* the key.**
`config.json.tools` is `{"T1": {"name", "diameter_inches"}, ...}`. A preferred
pocket cannot be added as a field until the map is re-keyed by identity (spec §8,
schema migration). The shipped config also carries leftover `T99` ("test") and a
`T5` with an empty name — i.e. the real library does not exist yet, so the
preferred-pocket values will be entered fresh, not migrated.

**F4 — Capacity is counted on distinct tools, and refusal already exists.**
`gcode_generator` raises `ValueError` when `len(all_tools) > tool_capacity`,
counting raw `T#` strings. `tool_count`/`tool_sequence` (distinct) drive capacity;
`tool_changes` (block count) does not. So a refusal mechanism and a message
pattern are already in place to extend.

**F5 — Assignment and block order are circular unless deliberately broken.**
`_build_blocks` does `for tool in sorted(by_tool)` — it sorts by the `T#` *string*.
If pockets are renumbered, block order can change, which changes the tool-change
count and the emitted file. So the assigner must run off **identity-ordered** data,
never off post-remap `T` numbers, or the output depends on the assignment which
depends on the output. Spec §4.2 flags the reordering as open (#12); #10 inherits
the constraint that its own input must not be the remapped numbers.

**F7 — The "block Generate on conflict" mechanism already exists, in both layers.**
The operator's requested gate is a **tightening of existing machinery, not new
machinery**:
- `static/job.js:36` and `templates/index.html:342` `_updateTopButtons()` both do
  `btn.disabled = !placements.length || compatibility.has_conflict`.
- `app.py:661` rejects `/api/generate` with **422 "Resolve tool compatibility
  conflicts before generating"**.
Both have been there since the original build (`785bea9`, `404e228`), so **spec
§1.1's "Nothing blocks generation on it" is incorrect as written** — not stale.
What is true, and is the real defect, is narrower: `has_conflict` fires only when
one `T#` carries **differing description strings**, so two genuinely different
cutters sharing a stale identical description are **not detected**, merge into one
block, and cut. The gate is sound in the safe direction and blind in the dangerous
one — exactly the inverse failure §3.1 warns about. Spec §1.1 needs correcting to
say *detection* is unsound rather than *enforcement* absent.

**F8 — The frontend has room, and a precedent for each candidate placement.**
`#main` is a 5-column grid: left sidebar `--lw` 240px, 6px handle, `1fr` canvas,
handle, right sidebar `--rw` 260px; both sidebars resize 140–520px and collapse to
a tab. Fixed 40px `#topbar` (holds the already-disabled `#btn-generate`) and 26px
`#statusbar`. The right sidebar's **`#compat-section` is the panel this feature
supersedes** (`placement.js:231` renders its ✓/⚠ rows; `sidebar.js:166` tags
conflicting tools in the tray). Overlay precedents: `#modal-unknown-tool` (small,
blocking, with a "Save to tool library" checkbox — the closest existing analogue to
a pocket prompt) and `#audit-panel` (92vw/1180px full-screen review panel).
At 260px the right sidebar cannot hold 8 pockets **across**; a full-window-width
dock gives each pocket ~180–240px.

**F9 — The corpus proves every failure the spec predicted. Six distinct VCarve tool
lines across 26 files:**

```
(T1 = Ball Nose .5 inches Dia)      ← diameter outside the braces
(T2 = End Mill {0.5 inch})          ← "inch"
(T4 = End Mill {.75 inches})        ← "inches", leading dot
(T4 = End Mill {1/4"})              ← fraction!
(T5 = ROUNDOVER 125)                ← no diameter at all
(T9 = End Mill {0.5 inch})          ← byte-identical to T2
```

- **`T2` and `T9` are byte-identical** — §3.1's measured collision, visible here. The
  Q5(a) per-file injectivity guard fires on exactly this file.
- **`T4` is a 0.75" cutter in one file and a 1/4" cutter in another** — the live merge
  defect (§1.1).
- **`End Mill` describes 0.25", 0.5" and 0.75" cutters.** Description is not identity.
- **Four diameter notations**, one of them fractional.
- **Nothing anywhere says upcut / downcut / compression.** That distinction — the one
  §3.1 says must never merge — exists in **no file from either CAM**. It can only ever
  be library-declared.

**F10 — Fusion posts *more* geometry than VCarve, and it is all currently thrown away.**

```
(T1 D=12.7  CR=6.35   - ZMIN=14.605  - BALL END MILL)
(T2 D=12.7  CR=0.     - ZMIN=-19.05  - FLAT END MILL)
(T3 D=12.7  CR=0. TAPER=45DEG - ZMIN=18.542 - CHAMFER MILL)
(T4 D=59.728 CR=0.    - ZMIN=16.129  - FORM MILL)
(T5 D=3.175 CR=3.175  - ZMIN=-12.7   - RADIUS MILL)
```

- Diameter (**mm**), corner radius, taper angle and a **type name** are all present —
  richer than VCarve — yet `gcode_parser` reads **no tools at all** from these files
  (#20), so they parse to diameter 0.
- **Consequence for safety, beyond identity:** CLAUDE.md records that
  `_max_tool_radius` reads `part.tools[...]["diameter_inches"]` from the parsed file,
  so Fusion files **under-inflate the X envelope check and the tool-radius collision
  check** — on the *primary* corpus. Once identity resolves to a library entry, **the
  library becomes the diameter authority and both checks get a true radius.** That is
  a safety win falling out of this feature, independent of pockets.
- These fields also make Q5(b) non-vacuous: the library's declared 0.5" can be
  **verified** against the file's `D=12.7`, rather than the guard having nothing to
  compare.
- `T4` is `D=12.7` in one file and `D=59.728` (a 2.35" form mill) in another — the
  same defect as VCarve's `T4`. Conversely `T2` and `T4` are byte-identical 1/2" flat
  end mills across files, which is a case the feature **should** merge.

**F6 — With ≤8 tools and 8 free pockets, a valid assignment always exists.**
Every `T#` is remappable, no reserved numbers, no tools outside the ATC (§3.3).
So assignment can never be *unsatisfiable* — the only refusal available is the
capacity one that already exists. Preferred-pocket collisions are therefore always
resolvable; the question is who resolves them and whether the operator is told.

---

## Q&A log

### Q1 — What is the auto-assigner optimising for?
- **Asked:** Is an unhonoured preferred pocket real physical work for the operator
  (pull the cutter, re-seat it, touch it off), or just a mild loss of familiarity?
  Recommended the former, making "minimise physical tool moves" the objective.
- **Captured:** *"An unhonored preference is real physical work."* Confirmed.
  Volunteered, unprompted, the resolution model as well: *"What I'd like to do is
  temporarily allow you to have both assigned to the same tool changer number, but
  prevent the code from generating. Only when there is a valid tool changer
  configuration should you allow the Generate G-code button to be clicked."*
  → Collisions are **surfaced, not auto-resolved**; validity gates Generate.
- **Also raised (operator's own question):** the GUI. *"Do we have room for it on
  the existing screen, or do we need to create a separate window to manage the tool
  changer?"* Taken up as Q2 — strictly #11's territory, but #11 was blocked on
  #10, so answering it here unblocks rather than duplicates.
- **Flags:** none.

### Q2 — Where does the 8-pocket changer UI live? (operator's own question)
- **Asked:** Is there room on the existing screen, or does the changer need its own
  window? Recommended **a full-width collapsible dock above the status bar**, on the
  argument that the gate decision in Q1 forces it: if an invalid map disables
  Generate, the reason must be permanently on screen, so the changer cannot live
  only in an overlay. Offered two alternatives (strip nested under the canvas;
  compact vertical list in the right sidebar + audit-style overlay for dragging).
- **Captured:** **Full-width dock, confirmed.** Chosen layout:

```
┌──────────────────────────────────────────────────┐
│ CNC Nest Tool        Job:[____]  [Generate] ⚙    │  ← disabled
├────────┬───────────────────────────────┬─────────┤
│Library │                               │Placement│
│        │                               │─────────│
│        │         BED CANVAS            │Job Info │
│Parts   │                               │         │
│Tray    │                               │         │
├────────┴───────────────────────────────┴─────────┤
│ TOOL CHANGER                  ⚠ 1 conflict    ▼  │
│ ┌──────┬──────┬──────┬══════╗┬──────┬──────┬───┐ │
│ │  1   │  2   │  3   ║  4  ⚠║│  5   │  6   │ 7 │ │
│ │¼"up  │½"EM  │empty ║¼"down║│empty │empty │   │ │
│ │      │      │      ║⅜"comp║│      │      │   │ │
│ └──────┴──────┴──────╚══════╝┴──────┴──────┴───┘ │
├──────────────────────────────────────────────────┤
│ 4 placed · 12 empty slots · Tools: T1→T2         │
└──────────────────────────────────────────────────┘

Drag ⅜"comp out of 4 → Generate enables.
```

  Properties this locks in, all for #11 to build against:
  - **Full window width**, so pocket size is independent of sidebar widths
    (~180–240px per pocket) and the left-to-right order matches how the operator
    reads the physical changer.
  - **Pocket 1 on the left** (confirmed implicitly by choosing the horizontal
    layout on that stated rationale — #11 should still confirm explicitly).
  - A conflicted pocket **shows both cutters stacked in the same cell**, not a
    count. This is why the narrower nested-strip option was rejected.
  - Empty pockets read **"empty"** and are drag targets.
  - **Collapsible**, but it force-expands and cannot be collapsed while the map is
    invalid — a disabled Generate must never lack a visible reason.
  - Header carries the validity summary (`⚠ 1 conflict`).
  - **Retires `#compat-section`** ("Tool Compatibility") from the right sidebar;
    that panel's per-`T#` ✓/⚠ rows exist to surface the double-duty symptom this
    feature removes. That is where the screen budget comes from.
  - Cost accepted: ~100px of canvas height.
- **Flags:** none.

### Q3 — Where do tools with no preferred pocket go?
- **Asked:** Auto-fill them into the lowest free pocket in execution order (skipping
  pockets any tool prefers), or hold them in a staging area for the operator to
  place? Recommended auto-fill, arguing that with no library yet every tool is
  unpreferred, so a staging area would make manual placement the normal path.
- **Captured:** **Staging area — "needs a home".** *"I think putting them in a
  needs-a-home area is probably the preferred method at this point."* The app does
  not guess a pocket, ever.
  - The day-one objection is answered by the library: the **create-tool prompt sets
    the default slot**, so a populated library leaves almost nothing unpreferred.
  - So the execution-order fill rule is **not needed**, and the tie-break question
    from the ticket is moot in both directions: collisions are surfaced (Q1) and
    blanks are staged (here). **The auto-assigner makes no arbitrary choices at all.**
- **Flags:** none.

### Q4 — Import the CAM libraries, or maintain the app's separately?
- **Asked:** (Operator's own question: *"it sounds like we're just gonna have to
  manage it in two different places"*, plus a request for a better option.)
  Recommended **one authority plus imports** — read Fusion `.tools` (JSON) and
  VCarve `.vtdb` (SQLite, per #4) for identity, match on `vendor`+`productId` per
  #21, reconcile re-imports on the Fusion library file's per-tool `guid`, and
  **never import the pocket** (`post-process.number` / ATC position are "the pocket
  as CAM believes it" — the value §1 exists to stop trusting). One-shot and
  re-runnable, never a live sync.
- **Captured:** **Rejected for v1, on a constraint I had missed:** *"What if the
  computer running the software doesn't have access to Fusion?"* The shop PC running
  CNC Nest may have no Fusion install at all, so the design must not depend on
  reaching a CAM library. Decision: *"For now it might be best to separate them and
  in the future we could add a feature to sync with the Fusion/Vcarve library."*
  → **The app's library is standalone and hand-maintained. Dual maintenance is
  accepted as a known cost. Sync is a future enhancement, not a dependency.**
  - §3.1 therefore stands exactly as written ("operator-declared").
  - The `guid` observation still refines #21 and should be recorded for the future
    sync ticket: no GUID can reach a **posted file** (the `Tool` class exposes
    none — #21 is right), but the Fusion **library file** carries one per tool, so a
    future sync can reconcile on it. Different problems, different keys.
- **Flags:**
  - Fusion/VCarve library sync → **future ticket**, not this feature.
  - `.tools` and `.vtdb` schemas unverified — no such file exists on this Mac
    (Fusion/VCarve live on the Windows side; the bridge folder holds only `.nc`).
    Whoever takes the sync ticket must pin a real file as a fixture.

### Q5 — What guards identity binding, now that aliases carry identity?
- **Asked:** Presented the consequence of Q4 first — a hand-typed library makes
  exact `vendor`+`productId` matching miss on first encounter, so the alias list is
  the real mechanism, which in turn settles §6.2's scope question. Then asked whether
  two guards hold: **(a)** resolution must be injective *within a single file*, and
  **(b)** a manual bind is refused when diameters disagree.
- **Captured:** **Both confirmed** — *"Yes that makes sense."* Recorded as summary
  decisions 9 and 10.
- **Flags:** none.

### Q6 — Does a drag write the new pocket back to the library default?
- **Asked:** Never write back / explicit opt-in per drag / automatic. Recommended
  **explicit opt-in**, on the §3.2 argument that automatic write-back records intent
  for one job while the field's value is predicting physical reality, and cites the
  existing "Save to tool library" checkbox on the unknown-diameter modal as precedent.
- **Captured:** **Option 1 — never write back.** *"I like option 1 because it's
  theoretically what should be done for each job. Operators will get used to seeing
  the tools in the same spots, so I want to encourage that behavior."*
  → Recorded as decision 11. The recommendation's premise was wrong: the default slot
  is **prescriptive, not descriptive**, so the app re-proposing the standard pocket is
  the desired nag rather than churn to be avoided.
- **Flags:** none.

### Q7 — What happens above 8 tools, and is a reassignment ever refused?
- **Asked:** Proposed that a drag is never refused and only generation is, with the
  9th tool simply staging as "nowhere to go" rather than getting its own code path.
  Asked whether the operator would rather **block the placement** that pushes a job
  past 8, at the bed, before the tool ever reaches the changer. Also recorded §8's
  **pinning** item as moot (with no auto-fill, no tie-break and no write-back the
  assigner never moves anything, so there is nothing to pin against) — not objected to.
- **Captured:** **Allow the placement.** Confirmed, with a stronger reason than the one
  offered: merging can still *lower* the count, so the total is not final at placement
  time. Added requirement: **per-tool parent-part attribution** so the operator can
  find the part to delete. Recorded as decisions 12 and 13.
- **Flags:** none. §8 "pinning" → struck as moot.

### Q8 — The complete validity gate, and determinism
- **Asked:** Presented the full definition of a valid job — (1) every file tool
  resolves to a library tool, (2) every resolved tool sits in exactly one pocket,
  (3) no pocket holds two tools — plus the observations that **capacity stops being a
  separate rule** (rules 2+3 over 8 pockets make >8 unsatisfiable, so over-capacity
  manifests as staged tools) and that **determinism comes for free** (the map is a
  pure function of resolved tools + library defaults + job-scoped drags; the assigner
  makes no arbitrary choice anywhere). Also offered a refinement to decision 13:
  compute which parts are the **sole user** of a tool, so the app can say *"removing
  bracket-L frees pocket 7"* instead of making the operator scan eight pockets.
  Asked for anything missing.
- **Captured:** Not contested — the gate, the capacity-as-consequence observation and
  the determinism statement all stand as written; the sole-user refinement was not
  objected to. Operator redirected to the next topic: **"We need to fully define the
  tool library."** Taken up as Q9.
- **Flags:** none.

### Q9 — Define the tool library (operator's request: *"We need to fully define the tool library."*)
- **Asked:** Proposed a schema on the discipline that **every field must have a named
  consumer**, grounded in F9/F10's real corpus, and asked what to cut or add.
- **Captured:** **"I like the table."** Schema confirmed as decision 14. The two
  sub-questions attached to it (is `default_slot` required; own file or inside
  `config.json`) were **not directly answered** — carried to Q10.
- **Flags:** none.

### Q10 — `default_slot` required or optional; library file or config?
- **Asked:** the two items Q9 left open, each with a recommendation.
- **Captured:** both recommendations **confirmed** — `default_slot` **optional**,
  library in its **own `tool_library.json`**. Recorded as decisions 15 and 16.
- **Flags:** none.

### Q11 — Library lifecycle: merge, delete-in-use, edit-in-use, stale saved jobs
- **Asked:** four operations with recommendations — merge by survivor+alias-union;
  refuse deletion of a tool in use; re-run collision/envelope on a geometry edit and
  **flag** newly-invalid placements; and store only the *drags* in `.cnj`, dropping
  stale overrides back to the library default.
- **Captured:**
  - Merge → **yes** (decision 18). Delete-in-use refused → **yes** (decision 19).
  - Edit-in-use → **yes, re-run collision, and delete the offending placements**
    (decision 20) — a stronger position than was recommended, on the grounds that the
    edit is a *correction* of wrong data.
  - **Item 4 is moot: save/load job is being sunset entirely** (decision 17), which
    removes the `.cnj` format and with it the whole stale-override question.
- **Flags:** save/load sunset is a **code deletion task**, tracked separately from this
  spec decision — see Next steps.

---

## Open flags (pending input)

- **Fusion/VCarve library sync** → future ticket (Q4). Not a dependency of this
  feature; the app's library is standalone.
- **`.tools` / `.vtdb` schemas unverified** → whoever takes the sync ticket must pin a
  real file as a fixture. Neither format exists on this Mac.
- **#11 to confirm explicitly:** pocket 1 on the left (chosen implicitly with the
  horizontal dock in Q2).
- **UNRESOLVED, and the sharpest remaining hole — belongs to #9.** Decisions 9 and 10(a)
  are in tension on one real file. Aliases key on the **raw string** the file emits, but
  F9 shows `T2` and `T9` both emit `End Mill {0.5 inch}` **in the same file**. So:
  - Guard 10(a) correctly refuses to merge them and demands a manual bind. Good.
  - But an alias on that string can only ever point at **one** library tool, so the
    manual bind has nothing durable to key on. The only datum distinguishing `T2` from
    `T9` in that file is **`T#` itself** — the token the whole feature exists to stop
    trusting.
  - Candidate resolutions, none chosen here: scope the binding to `(filename, T#)`;
    treat such a file as unusable until re-posted with distinguishable metadata; or
    accept `T#` as a *within-file* discriminator only (it is, after all, CAM's own
    assertion that the two differ). **Not invented at the end of a long session —
    handed to #9, which owns matching.**
- **Whether "shift" (identity re-bind) is remembered as an alias or applies to one load**
  — decision 7 establishes the power, not its persistence. #9 territory.

---

## Next steps

1. **Write these decisions into `docs/tool-changer-pocket-management-spec.md`** and
   close #10. Sections needing edits, beyond adding the new material:
   - **§1.1 is factually wrong** and must be corrected — the gate exists in both layers
     (F7); the defect is that *detection* is unsound, not that enforcement is absent.
   - **§3.2** — delete the `.cnj` round-trip requirement (decision 17).
   - **§6.2** — the scope question is now **settled**: aliases carry identity, so CAM
     hygiene is not a precondition and §7's out-of-scope ruling stands (decision 9).
   - **§8** — strike *pinning* (moot, Q7) and *".cnj must carry the pocket assignment"*
     (decision 17). "Over-capacity behaviour after de-dup" is now answered (decision 12).
   - **§6 dependency table** — #10 resolved; #11 and #13 unblocked.
2. **Sunset save/load job** (decision 17) — delete `/api/save-job`, `/api/load-job`,
   the `.cnj` format and their tests. Separate commit; it is code deletion, not spec.
3. **New ticket: the tool management library** (decisions 4, 14–16, 18–20). It is a
   feature in its own right and #10 did not previously imply one.
4. **New ticket: Fusion/VCarve library sync** (Q4 flag), explicitly not a dependency.
5. **#9 still owns** the alias-collision hole flagged above before implementation starts.
