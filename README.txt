5x10 CNC Layout Tool

Setup:
1. Install Python 3.8+.
2. Create and activate a virtual environment:
   python3 -m venv .venv
   source .venv/bin/activate
3. Install dependencies:
   pip install -r requirements.txt
4. Run the app:
   python app.py
5. Open http://localhost:5000 in a browser.

Project structure:
  app.py            - Flask application entry point
  config.py         - Config loading and saving
  config.json       - Default application settings
  requirements.txt  - Python runtime dependencies
  templates/        - HTML templates
  static/           - Browser JavaScript and assets
  launch.command    - macOS launcher script
  launch.bat        - Windows launcher script

Next steps:
- Add parser and generator modules
- Implement canvas-based bed preview and drag/drop UI
- Add sample VCarve/G-code files for testing
