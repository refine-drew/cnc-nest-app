CNC Nest Tool
=============
Nest CNC parts on the 5x10 dual-rail bed and post one merged program for the
whole sheet.

This is the short version, kept as plain text so it opens in Notepad on the shop
PC. README.md is the full one - same instructions, plus what the tool library and
the changer dock are for, and what each generated file is.


WINDOWS SETUP
=============
1. Install Python from python.org
   - Check "Add Python to PATH" during installation

2. Install Git from git-scm.com

3. Open Command Prompt and run:
   git clone https://github.com/refine-drew/cnc-nest-app.git

4. Double-click launch.bat

5. Browser opens automatically at http://localhost:5001


MAC SETUP
=========
1. Install Python 3 from python.org
   (The Python that ships with macOS is not enough on its own.)

2. Install Git, if you don't have it:
   xcode-select --install

3. In Terminal:
   git clone https://github.com/refine-drew/cnc-nest-app.git

4. Double-click launch.command
   (If macOS blocks it: right-click -> Open -> Open)

5. Browser opens automatically at http://localhost:5001


UPDATING
========
Windows:  double-click update.bat
Mac:      double-click update.command

Do NOT expect launch.bat or launch.command to update anything. They only start
the version you already have. Updating is update.bat / update.command, and
nothing else.


WHAT A JOB WRITES
=================
Generate produces four files in your output folder:

  <job>.nc               the master program - this goes on the machine
  <job>.pdf              the layout: which part is in which slot
  <job>_setup.txt        how to load the changer, and the cycle-time estimate
  <job>_validation.txt   anything the validator flagged

If the validator finds a hard error the .nc is NOT written, and the app tells you
why. That is deliberate.
