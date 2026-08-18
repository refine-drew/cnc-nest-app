"""Shared fixtures.

Two jobs. First, reset every piece of in-memory job state between tests — the app keeps
its whole session in module globals, so a leaked placement or a leaked pocket drag would
make tests order-dependent. Second, install an **isolated tool library** so no test can
read or write the repo's real `tool_library.json`.

The test library's codes are what the fixture G-code carries, so tests exercise the real
match-on-load path (spec §3.5.3) rather than a bypass. Declared diameters match what the
old header parse produced for the same fixtures, which keeps every collision and
envelope expectation meaningful.
"""
import pytest

import app as app_module
from tool_library import LibraryTool, ToolLibrary


def make_tool(code, name="Test Tool", diameter=0.5, geometry_class="Flat End Mill",
              flute="down", default_slot=None, cutting_length=None,
              cam_descriptions=None, vendor=""):
    return LibraryTool(
        code=code,
        name=name,
        diameter_inches=diameter,
        geometry_class=geometry_class,
        flute_direction=flute,
        cutting_length_in=cutting_length,
        cam_descriptions=list(cam_descriptions or []),
        default_slot=default_slot,
        vendor=vendor,
    )


def default_test_tools():
    """One entry per code the shared fixtures use.

    `TT-000n` exists so a test can ask for n distinct tools without inventing a
    plausible-looking cutter each time. Slots 1..8 are declared and the ninth is
    deliberately blank — that is what makes the over-capacity case a *staged* tool with
    nowhere to go rather than a rule of its own (§3.4).
    """
    tools = [
        make_tool("EM-0500", '1/2" End Mill', 0.5, default_slot=2),
        make_tool("EM-0250", '1/4" End Mill', 0.25, default_slot=1),
        make_tool("SB-0500", '1/2" Spiral Bit', 0.5, flute="up", default_slot=3),
        make_tool("BN-0500", '1/2" Ball Nose', 0.5, "Ball Nose", "up", default_slot=4),
    ]
    for n in range(1, 10):
        tools.append(make_tool(f"TT-{n:04d}", f"Test Tool {n}", 0.25,
                               default_slot=n if n <= 8 else None))
    return tools


@pytest.fixture(autouse=True)
def isolated_library(monkeypatch):
    """A fresh library per test, whose `save` never touches disk."""
    library = ToolLibrary(default_test_tools())
    monkeypatch.setattr(library, "save", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "tool_library", library)
    return library


@pytest.fixture(autouse=True)
def reset_job_state():
    for state in (app_module._loaded, app_module._placements,
                  app_module._placement_paths, app_module._instance_counts,
                  app_module._tool_binds, app_module._pocket_overrides):
        state.clear()
    yield
    for state in (app_module._loaded, app_module._placements,
                  app_module._placement_paths, app_module._instance_counts,
                  app_module._tool_binds, app_module._pocket_overrides):
        state.clear()


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
