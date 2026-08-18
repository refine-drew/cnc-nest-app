CNC Nest Tool
=============
Optimize CNC cutting layouts on a 5×10 ft dual-rail bed.
Load VCarve G-code files, drag parts onto A/B rails, detect collisions,
and generate a merged master G-code file.


WINDOWS SETUP
=============
1. Install Python from python.org
   - Check "Add Python to PATH" during installation

2. Install Git from git-scm.com

3. Open Command Prompt and run:
   git clone https://github.com/refine-drew/cnc-nest-app.git
   cd cnc-nest-app

4. Double-click launch.bat

5. Browser opens automatically at http://localhost:5001

To update: just double-click launch.bat again (pulls latest automatically)


MAC SETUP
=========
1. Python is pre-installed on Mac

2. Install Git (if not already):
   xcode-select --install

3. In Terminal:
   git clone https://github.com/refine-drew/cnc-nest-app.git
   cd cnc-nest-app
   chmod +x launch.command

4. Double-click launch.command
   (If macOS blocks it: right-click → Open → Open)

5. Browser opens automatically at http://localhost:5001

To update: just double-click launch.command again (pulls latest automatically)


PROJECT STRUCTURE
=================
app.py              Flask application and API routes
config.py           Config loading/saving (cross-platform paths)
config.json         Default application settings
gcode_parser.py     VCarve G-code parser
gcode_generator.py  Master G-code builder (order-of-operations merge)
collision.py        Rectangle overlap collision detection
tool_library.py     Tool registry and diameter resolution
requirements.txt    Python dependencies
templates/          HTML templates
static/             Browser JavaScript and CSS
tests/              Pytest test suite
launch.bat          Windows launcher
launch.command      macOS launcher
