# CNC Nest Tool

Optimize CNC cutting layouts on a 5×10 ft dual-rail bed.
Load VCarve G-code files, drag parts onto A/B rails, detect collisions,
and generate a merged master G-code file.

---

## First Install

### Mac

1. **Install Git** (if not already):
   ```
   xcode-select --install
   ```

2. **Install Python 3** from [python.org](https://python.org)

3. **Clone the repo** in Terminal:
   ```
   git clone https://github.com/refine-drew/cnc-nest-app.git
   ```

4. **Double-click `launch.command`** inside the cloned folder.
   - If macOS blocks it: right-click → Open → Open
   - The launcher checks for Python and installs Flask automatically on first run

5. Your browser opens to [http://localhost:5001](http://localhost:5001) automatically.

### Windows

1. **Install Git** from [git-scm.com](https://git-scm.com)

2. **Install Python 3** from [python.org](https://python.org)
   - Check **"Add Python to PATH"** during installation

3. **Clone the repo** in Command Prompt:
   ```
   git clone https://github.com/refine-drew/cnc-nest-app.git
   ```

4. **Double-click `launch.bat`** inside the cloned folder.
   - The launcher checks for Python and installs Flask automatically on first run

5. Your browser opens to [http://localhost:5001](http://localhost:5001) automatically.

---

## Updating

To get the latest version:

- **Mac:** double-click `update.command`
- **Windows:** double-click `update.bat`

The update script pulls the latest code from GitHub and relaunches the app automatically.

---

## Project Structure

```
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
update.bat          Windows updater
update.command      macOS updater
```
