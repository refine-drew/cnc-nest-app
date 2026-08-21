# Test fixtures

Two merged masters this app wrote and the shop then ran against a clock. They live
here rather than in `Source Data/` because that folder is gitignored, and a file the
test suite asserts against cannot be untracked and unreviewable — the same reason
`post/syntec_Refine.cps` moved out of it.

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
