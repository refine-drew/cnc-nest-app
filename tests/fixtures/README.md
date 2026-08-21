# Test fixtures

Six files. Two merged masters this app wrote and the shop then ran against a clock;
three masters from the 2026-08-15 output review; and one reconstructed tool header.
They live here rather than in `Source Data/` or `~/Downloads` because both are
outside the repo, and **a file the test suite asserts against cannot be untracked and
unreviewable** — the same reason `post/syntec_Refine.cps` moved out of `Source Data/`.

That rule is the one to apply to anything added here later. A fixture reached by an
absolute path into somebody's home directory is a test that does not run, and a
`skipif` guarding it turns that into silence rather than a failure: two of these files
were reached that way until 2026-08-21, and between them they were skipping twelve
assertions.

**A timed job is only worth what is written down beside it.** The posture of the
control's auto-tool touch-off setting is not in the file and moves the answer by 30 s
per tool change; the first calibration guessed it and buried the guess in the
acceleration. Record it with the stopwatch. Record the *window* too — where the
operator started and stopped counting.

## `t24-test.nc` — **the fit**

Three parts (`T24.nc`, `T24G.nc`, `T24H.nc`) on the A rail, five tool blocks,
4348 lines. Written 2026-08-21 14:16.

- **Ran 10m53s (653 s)**, cycle start to the final tool change into pocket 2 — the
  park is *outside* the window.
- **Auto tool touch-off OFF** at the control: 27 s per change, not 57 s.
- Ran clean: no alarm, no feed hold, no restart.
- Four tool blocks inside the window, so four spindle starts at 5 s
  (`SPINDLE_START_SECONDS`) — 20 s of the observation is priced by a measured
  constant rather than by the fitted acceleration.

`runtime_estimator.DEFAULT_ACCEL_MM_S2` is fitted to this observation and
`test_the_timed_job_predicts_its_observed_cycle` is the record of the fit.

One thing about this file is not the app's current output: it carries **five
malformed coordinate words** (`X134.6280.`, `Y1899.1000.` — two decimal points),
written by the generator before the `_NUM` trailing-dot fix landed later the same day.
It is kept verbatim because it is the file that ran, and because the run settles a
question the fix's comment had guessed at: the Syntec **read those blocks without
complaint**. `gcode_validator._check_word_syntax` still rejects them at ERROR, and
should — the app must not write a word whose grammar it cannot defend — but the
justification is that, not an observed alarm. The stray dots do not affect timing:
the estimator reads `134.6280.` as 134.628 like everything else does.

`test_t24_fixture_still_carries_its_five_malformed_words` asserts the count, so the
corpus cannot quietly stop being one.

## `18g-test.nc` — a cross-check only

Nine instances of `18G.nc` (Fusion-posted) on the A rail, two tools, 2819 lines.
Written 2026-08-20 13:06.

- **Ran 11m37s (697 s)**, whole cycle.
- **Posture not recorded.** This is what makes it unfittable: at 27 s per change the
  observation buys 60 s more motion than at 57 s, which is 9% of the job.

`test_the_earlier_timed_job_agrees_only_with_touch_off_off` holds it at the OFF
reading, where it predicts 3.4% low; the ON reading is 5.2% high and lands further
from what the job ran, which is the evidence for the reading. `test_the_reference_file_times_its_vertical_ramp_arcs` uses it for its
45 G19 ramp arcs, which is a property of the geometry and needs no clock at all.

The filename is flattened from `(9) 18G Test.nc`; the parentheses in the original are
what motivated `gcode_generator.comment`, and there is no reason to make a test path
carry them.

**It is the richer of the two as a regression corpus, and that went unrecorded until
2026-08-21.** It predates *both* of the generator's last two fixes, so it carries
eleven findings, not five:

- **9 × `word-syntax`** — one per instance of `18G.nc`, each a `Y…000.` (the
  trailing-dot bug, `ebc7629`).
- **2 × `comment-syntax`** in the header — `(Job: (9) 18G Test)` and
  `(Safe Z: 2.250" — driven by 18G.nc (retract))` (the paren bug, `ac7b391`). The
  second is the one that mattered: the operator typed the parens in the job name,
  but `_compute_job_safe_z` writes `18G.nc (retract)` itself, so every master
  written after #22 had a broken header whatever the job was called. It also carries
  an em dash, which is what `_check_comment_syntax`'s non-printable rule now catches.

All eleven are asserted by name and count in `tests/test_gcode_validator.py`.

## The three reviewed masters — `nest_20260814_090009.nc`, `nest_20260815_103400.nc`, `nest_20260815_105420.nc`

The 2026-08-15 output review: three files read line by line by hand, every defect
found by eye, and the validator then required to find each one unaided. They are what
`gcode_validator` was built against.

They lived in `~/Downloads` until 2026-08-21, guarded by a `skipif` — which meant the
eleven tests that use them ran on one laptop and skipped silently everywhere else.
`~/Downloads` is also the app's shipped `output_path`, so the corpus sat in the folder
every generated nest lands in, one tidy-up from disappearing. They are ~1.2 MB
together; that is the price of the policy at the top of this file, and it is worth
paying for the only real-output corpus the validator has.

Their names are the pre-#26 job-naming scheme (`nest_YYYYMMDD_HHMMSS`), kept as-is
because the name records when the file was written and nothing reads it.

## `form-mill-header.nc` — a header, not a run

Not a timed job and not real posted output: a minimal REFINE-shaped program carrying
the tool header line from `39x35.nc`, the #20 worst case —
`(T4 D=59.728 CR=0. - ZMIN=-6.35 - FORM MILL)`, a 2.3515" cutter that the pre-#20
parser read as diameter 0.

The original file is gone. Its test guarded on `~/Documents/cnc_library/39x35.nc`,
which stopped existing when the library was renamed to `~/Documents/SS2_library`, so
the `skipif` had been passing over the assertion silently. Only the header is under
test, so a reconstructed skeleton around the real line is enough — but it is a
reconstruction, and unlike the four files above it is not evidence of anything the
machine did.
