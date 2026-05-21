"""
Tests for ToolLibrary.resolve_for_part / find_unknown_tools
and the /api/resolve-tool and /api/place tool-blocking routes.
"""
import json
import pytest

import app as app_module
from app import app
from gcode_parser import GcodePass, GcodePart, ZValidation
from tool_library import ToolLibrary


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state():
    app_module._loaded.clear()
    app_module._placements.clear()
    app_module._placement_paths.clear()
    app_module._instance_counts.clear()
    yield


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _make_part(tools: dict, tool_numbers: list) -> GcodePart:
    """Build a minimal GcodePart with given tools dict and pass tool sequence."""
    passes = [GcodePass(pass_index=i, tool_number=tn) for i, tn in enumerate(tool_numbers)]
    return GcodePart(
        filename="test.nc",
        blank_width=100.0,
        blank_height=100.0,
        material_thickness=19.05,
        tools=tools,
        min_x=0.0, max_x=100.0,
        min_y=0.0, max_y=100.0,
        raw_lines=[],
        z_validation=ZValidation(status="ok"),
        passes=passes,
    )


# ── ToolLibrary.resolve_for_part ──────────────────────────────────────────────

def test_resolve_for_part_uses_file_header_first():
    lib = ToolLibrary({"T2": {"name": "End Mill", "diameter_inches": 0.5}})
    part = _make_part({"T2": {"description": "End Mill", "diameter_inches": 0.75}}, ["T2"])
    # file header says 0.75 — must win over library's 0.5
    assert lib.resolve_for_part(part, "T2") == pytest.approx(0.75)


def test_resolve_for_part_falls_back_to_library():
    lib = ToolLibrary({"T2": {"name": "End Mill", "diameter_inches": 0.5}})
    part = _make_part({"T2": {"description": "End Mill"}}, ["T2"])  # no diameter_inches
    assert lib.resolve_for_part(part, "T2") == pytest.approx(0.5)


def test_resolve_for_part_returns_none_when_no_source():
    lib = ToolLibrary({})
    part = _make_part({"T2": {"description": "End Mill"}}, ["T2"])
    assert lib.resolve_for_part(part, "T2") is None


def test_resolve_for_part_case_insensitive():
    lib = ToolLibrary({"T2": {"name": "End Mill", "diameter_inches": 0.5}})
    part = _make_part({}, [])
    assert lib.resolve_for_part(part, "t2") == pytest.approx(0.5)


# ── ToolLibrary.find_unknown_tools ────────────────────────────────────────────

def test_find_unknown_tools_empty_when_all_resolved_via_file():
    lib = ToolLibrary({})
    part = _make_part({"T2": {"diameter_inches": 0.5}}, ["T2"])
    assert lib.find_unknown_tools(part) == []


def test_find_unknown_tools_empty_when_all_resolved_via_library():
    lib = ToolLibrary({"T4": {"name": "V-Bit", "diameter_inches": 0.25}})
    part = _make_part({"T4": {"description": "V-Bit"}}, ["T4"])
    assert lib.find_unknown_tools(part) == []


def test_find_unknown_tools_returns_missing_tool():
    lib = ToolLibrary({})
    part = _make_part({"T4": {"description": "Table Stiff"}}, ["T4"])
    result = lib.find_unknown_tools(part)
    assert len(result) == 1
    assert result[0]["tool_number"] == "T4"
    assert result[0]["description"] == "Table Stiff"


def test_find_unknown_tools_deduplicates_multiple_passes():
    lib = ToolLibrary({})
    # T2 appears twice in the pass sequence but should only be reported once
    part = _make_part({"T2": {"description": "End Mill"}}, ["T2", "T4", "T2"])
    result = lib.find_unknown_tools(part)
    tool_nums = [r["tool_number"] for r in result]
    assert tool_nums.count("T2") == 1
    assert "T4" in tool_nums


def test_find_unknown_tools_mixed_resolved_and_unknown():
    lib = ToolLibrary({"T2": {"name": "End Mill", "diameter_inches": 0.5}})
    part = _make_part(
        {"T2": {"description": "End Mill"}, "T4": {"description": "V-Bit"}},
        ["T2", "T4"],
    )
    result = lib.find_unknown_tools(part)
    assert len(result) == 1
    assert result[0]["tool_number"] == "T4"


def test_find_unknown_tools_tool_not_in_tools_dict():
    # Tool referenced in a pass but no entry in tools dict at all
    lib = ToolLibrary({})
    part = _make_part({}, ["T9"])
    result = lib.find_unknown_tools(part)
    assert len(result) == 1
    assert result[0]["tool_number"] == "T9"
    assert result[0]["description"] == ""


# ── /api/place blocks on unknown tools ───────────────────────────────────────

# A file with T4 having no diameter in header and T4 not in config tools
_NC_UNKNOWN_TOOL = (
    "( Material Size)\n( X= 100.0, Y= 100.0, Z= 19.05)\n"
    "(T4 = Table Stiff)\n"
    "G43 H4 Z44.4754\nT4 M06\nG01 X50 Y50 Z-0.254\nM30\n"
)


def test_place_blocks_when_tool_has_no_diameter(client, tmp_path, monkeypatch):
    (tmp_path / "part.nc").write_text(_NC_UNKNOWN_TOOL)
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    # Remove T4 from tools config so it's truly unknown
    monkeypatch.setitem(app_module.config, "tools", {"T1": {"name": "x", "diameter_inches": 0.5}})
    r = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    assert r.status_code == 422
    data = r.get_json()
    assert data["error"] == "unknown_tools"
    assert any(t["tool_number"] == "T4" for t in data["tools"])


def test_place_succeeds_when_tool_in_library(client, tmp_path, monkeypatch):
    (tmp_path / "part.nc").write_text(_NC_UNKNOWN_TOOL)
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    monkeypatch.setitem(
        app_module.config,
        "tools",
        {"T4": {"name": "Table Stiff", "diameter_inches": 0.75}},
    )
    r = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


# ── /api/resolve-tool ─────────────────────────────────────────────────────────

def test_resolve_tool_requires_tool_number(client):
    r = client.post("/api/resolve-tool", json={"diameter_inches": 0.5})
    assert r.status_code == 400


def test_resolve_tool_requires_valid_diameter(client):
    r = client.post("/api/resolve-tool", json={"tool_number": "T4", "diameter_inches": "bad"})
    assert r.status_code == 400


def test_resolve_tool_rejects_zero_diameter(client):
    r = client.post("/api/resolve-tool", json={"tool_number": "T4", "diameter_inches": 0})
    assert r.status_code == 400


def test_resolve_tool_rejects_negative_diameter(client):
    r = client.post("/api/resolve-tool", json={"tool_number": "T4", "diameter_inches": -0.5})
    assert r.status_code == 400


def test_resolve_tool_updates_cached_part(client, tmp_path, monkeypatch):
    (tmp_path / "part.nc").write_text(_NC_UNKNOWN_TOOL)
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    monkeypatch.setitem(app_module.config, "tools", {})

    # Pre-load the part into cache
    client.post("/api/load-file", json={"path": "part.nc"})
    assert "part.nc" in app_module._loaded

    r = client.post("/api/resolve-tool", json={"tool_number": "T4", "diameter_inches": 0.75})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["diameter_inches"] == pytest.approx(0.75)

    # Cache should now have the diameter
    cached = app_module._loaded["part.nc"]
    assert cached.tools["T4"]["diameter_inches"] == pytest.approx(0.75)


def test_resolve_tool_saves_to_library_when_requested(client, tmp_path, monkeypatch):
    saved = {}

    def fake_save(data):
        saved.update(data)

    monkeypatch.setattr(app_module, "save_config", fake_save)
    monkeypatch.setitem(app_module.config, "tools", {})

    r = client.post(
        "/api/resolve-tool",
        json={"tool_number": "T4", "description": "Table Stiff", "diameter_inches": 0.75, "save_to_library": True},
    )
    assert r.status_code == 200
    assert "T4" in app_module.config["tools"]
    assert app_module.config["tools"]["T4"]["diameter_inches"] == pytest.approx(0.75)
    assert "T4" in saved.get("tools", {})


def test_resolve_tool_does_not_save_when_flag_false(client, tmp_path, monkeypatch):
    saved = {}

    def fake_save(data):
        saved.update(data)

    monkeypatch.setattr(app_module, "save_config", fake_save)
    monkeypatch.setitem(app_module.config, "tools", {})

    client.post(
        "/api/resolve-tool",
        json={"tool_number": "T4", "diameter_inches": 0.75, "save_to_library": False},
    )
    assert saved == {}  # save_config was not called


def test_placement_succeeds_after_resolve(client, tmp_path, monkeypatch):
    (tmp_path / "part.nc").write_text(_NC_UNKNOWN_TOOL)
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    monkeypatch.setitem(app_module.config, "tools", {})

    # Place should fail first
    r1 = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    assert r1.status_code == 422
    assert r1.get_json()["error"] == "unknown_tools"

    # Resolve the tool
    client.post("/api/resolve-tool", json={"tool_number": "T4", "diameter_inches": 0.75})

    # Place should succeed now
    r2 = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    assert r2.status_code == 200
    assert r2.get_json()["ok"] is True


def test_resolve_tool_normalises_tool_number_to_uppercase(client):
    r = client.post("/api/resolve-tool", json={"tool_number": "t4", "diameter_inches": 0.5})
    assert r.status_code == 200
    assert r.get_json()["tool_number"] == "T4"
