# Session — 2026-10-10

## Status
- **Unproved:** the end-of-job pocket pick (D10) has only been checked by tests and a Node DOM shim (F4); no job generated with a picked or Auto-resolved pocket has run on the SS2, and the dock header has not been viewed at the shop PCs' window width.
- **Unproved:** Windows launch (R2) and the post-time refusal of a reused tool number (D5) — the batch file was reviewed and the post function was exercised against a mocked kernel, neither has run for real.
- **Unproved:** the monitor page (D1, D4) has not been viewed on the shop's 32" monitor from 20 ft (F1).

## Next Steps
1. Install Drive for desktop on AJ's PC and both shop PCs, mark the shared drive Available offline, clone the repo, double-click `launch.bat`, then run `.venv\Scripts\python -m pytest` there (R2, F3). Note the drive letter (R4).
2. Post a Fusion program with two tools on one number and confirm the post aborts with the "used for two different cutters" error (D5).
3. View page 1 on the shop monitor from about 20 ft and judge whether the 0.5" part number and size lines are enough (F1).
4. Decide Q1, which closes R1.
5. `CLAUDE.md` is over the 30 KB cap. Move its dated decision prose into `DECISIONS.md` as coded entries, leaving what every session needs.
6. Generate a one-tool job and a two-tool job with a picked pocket, confirm each file's last `T# M06` and the PDF footer's "loaded for the next job" entry, and run one on the SS2 to see it end holding that pocket (D10, R6).
