# Session — 2026-10-08

## Status
- **Unproved:** the Drive layout (D6) exists only in config and code; the shared drive has not been created and the Mac's library still sits in `~/Documents/SS2_library` (seeded with `tool_library.json` on first import).
- **Unproved:** Windows launch (R2) and the post-time refusal of a reused tool number (D5) — the batch file was reviewed and the post function was exercised against a mocked kernel, neither has run for real.
- **Unproved:** the monitor page (D1, D4) has not been viewed on the shop's 32" monitor from 20 ft (F1).

## Next Steps
1. Create the shared drive `SS2 Library` in the business Google account (Q2), move the part folders and the Mac's `~/Documents/SS2_library/tool_library.json` to its root, and confirm the app on the Mac resolves the Drive path first (D6, D8).
2. Install Drive for desktop on AJ's PC and both shop PCs, mark the shared drive Available offline, clone the repo, double-click `launch.bat`, then run `.venv\Scripts\python -m pytest` there (R2, F3). Note the drive letter (R4).
3. Post a Fusion program with two tools on one number and confirm the post aborts with the "used for two different cutters" error (D5).
4. View page 1 on the shop monitor from about 20 ft and judge whether the 0.5" part number and size lines are enough (F1).
5. Decide Q1, which closes R1.
6. `CLAUDE.md` is 68 KB against a 30 KB cap. Move its dated decision prose into `DECISIONS.md` as coded entries, leaving what every session needs.
