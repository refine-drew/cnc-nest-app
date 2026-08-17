"""
API route tests using Flask's built-in test client.
All file system access is either stubbed or uses temp dirs.
"""
import json
import os
import re
import tempfile

import pytest

import app as app_module
from app import app
from runtime_estimator import DEFAULT_TOOL_CHANGE_SECONDS


@pytest.fixture(autouse=True)
def reset_state():
    """Clear in-memory state and restore config before every test."""
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


# ── /api/config ───────────────────────────────────────────────────────────────

def test_get_config(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.get_json()
    assert "tools" in data
    assert "advanced" in data


def test_post_config_updates_tool(client, tmp_path, monkeypatch):
    # Point config output at tmp_path so save_config doesn't touch the real file
    import config as cfg_mod
    real_path = cfg_mod.get_config_path()

    def fake_save(data):
        pass  # no-op for tests

    monkeypatch.setattr(cfg_mod, "save_config", fake_save)
    r = client.post(
        "/api/config",
        data=json.dumps({"tools": {"T99": {"name": "test", "diameter_inches": 0.1}}}),
        content_type="application/json",
    )
    assert r.status_code == 200
    data = r.get_json()
    assert "T99" in data["tools"]


def test_post_config_normalizes_library_paths(client, monkeypatch):
    """A pasted quoted path is stored clean, and a bare string becomes a list."""
    import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "save_config", lambda data: None)
    monkeypatch.setattr(app_module, "save_config", lambda data: None)

    r = client.post("/api/config", json={"library_path": "  '/tmp/quoted lib'  "})
    assert r.status_code == 200
    assert r.get_json()["library_path"] == ["/tmp/quoted lib"]

    r = client.post("/api/config", json={
        "library_path": ["'/tmp/a'", "  ", '"/tmp/b"'],
        "output_path": "'/tmp/out'",
    })
    body = r.get_json()
    assert body["library_path"] == ["/tmp/a", "/tmp/b"]
    assert body["output_path"] == "/tmp/out"


def test_library_root_picks_first_existing_candidate(client, tmp_path, monkeypatch):
    """A shared config can list several paths; the local one wins."""
    real = tmp_path / "lib"
    real.mkdir()
    (real / "part.nc").write_text(NC_CONTENT)
    monkeypatch.setitem(app_module.config, "library_path",
                        ["/definitely/not/here", str(real)])

    r = client.get("/api/library")
    assert r.status_code == 200
    body = r.get_json()
    assert body["library_path"] == str(real.resolve())
    assert body["exists"] is True
    assert any(e["name"] == "part.nc" for e in body["entries"])


def test_library_root_accepts_legacy_string(client, tmp_path, monkeypatch):
    """Older configs stored a single string — that must keep working."""
    (tmp_path / "part.nc").write_text(NC_CONTENT)
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    body = client.get("/api/library").get_json()
    assert body["exists"] is True
    assert body["library_path"] == str(tmp_path.resolve())


# ── /api/slots ────────────────────────────────────────────────────────────────

def test_slots_returns_all_positions(client):
    r = client.get("/api/slots")
    assert r.status_code == 200
    slots = r.get_json()["slots"]
    inches = [s["inches"] for s in slots]
    assert 0 in inches
    assert 39 in inches
    assert 117 in inches
    assert 19.5 in inches


def test_slots_report_a_and_b_machine_y_separately(client):
    """The rails run in opposite directions, so each slot has two machine Y values.

    Regression: /api/slots reported one Y for both rails, which put every B-rail
    slot marker on the canvas up to ~3 m from where it actually is.
    """
    body = client.get("/api/slots").get_json()
    slots = {s["inches"]: s for s in body["slots"]}
    a0 = body["rails"]["A"]["slot0_y_mm"]
    b0 = body["rails"]["B"]["slot0_y_mm"]

    assert slots[0]["machine_y_a"] == pytest.approx(a0)
    assert slots[0]["machine_y_b"] == pytest.approx(b0)
    # A counts down, B counts up, both at exactly 13" per slot inch
    assert slots[39]["machine_y_a"] == pytest.approx(a0 - 39 * 25.4)
    assert slots[39]["machine_y_b"] == pytest.approx(b0 + 39 * 25.4)
    # machine_y stays as an A-rail alias for older callers
    assert slots[39]["machine_y"] == pytest.approx(slots[39]["machine_y_a"])
    # and the two rails never coincide
    for s in body["slots"]:
        assert s["machine_y_a"] != pytest.approx(s["machine_y_b"])


def test_slots_honor_rail_config_override(client):
    """Moving one rail's datum must move only that rail's slot positions."""
    adv = app_module.config["advanced"]
    original = adv.get("rails")
    try:
        adv["rails"] = {
            "A": {"x_mm": 100.0, "slot0_y_mm": 2000.0, "slot_dir": -1, "x_dir": 1},
        }
        body = client.get("/api/slots").get_json()
        slots = {s["inches"]: s for s in body["slots"]}
        assert slots[39]["machine_y_a"] == pytest.approx(2000.0 - 39 * 25.4)
        # B falls back to RAIL_DEFAULTS and is unaffected
        assert slots[39]["machine_y_b"] == pytest.approx(
            body["rails"]["B"]["slot0_y_mm"] + 39 * 25.4)
    finally:
        if original is None:
            adv.pop("rails", None)
        else:
            adv["rails"] = original


def test_slots_pitch_labels(client):
    r = client.get("/api/slots")
    slots = {s["inches"]: s for s in r.get_json()["slots"]}
    # 39 is in both pitch systems
    assert "13" in slots[39]["pitch"]
    assert "19.5" in slots[39]["pitch"]
    # 13 is only 13" pitch
    assert "13" in slots[13]["pitch"]
    assert "19.5" not in slots[13]["pitch"]
    # 19.5 is only 19.5" pitch
    assert "19.5" in slots[19.5]["pitch"]
    assert "13" not in slots[19.5]["pitch"]


def test_slots_labels(client):
    r = client.get("/api/slots")
    slots = {s["inches"]: s for s in r.get_json()["slots"]}
    assert slots[39]["label_a"] == "A39"
    assert slots[39]["label_b"] == "B39"
    assert slots[19.5]["label_a"] == "A19.5"


# ── /api/library ──────────────────────────────────────────────────────────────

def test_library_missing_path_returns_exists_false(client, monkeypatch):
    monkeypatch.setitem(app_module.config, "library_path", "/nonexistent/path/xyz")
    r = client.get("/api/library")
    assert r.status_code == 200
    data = r.get_json()
    assert data["exists"] is False
    assert data["entries"] == []


def test_library_scans_nc_files(client, tmp_path, monkeypatch):
    nc = tmp_path / "part.nc"
    nc.write_text(
        "( Material Size)\n( X= 100.0, Y= 200.0, Z= 19.0)\n"
        "(T2 = End Mill {0.5 inches})\nG00 X0 Y0\nG01 X10 Y10\n"
    )
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    r = client.get("/api/library")
    assert r.status_code == 200
    entries = r.get_json()["entries"]
    file_entries = [e for e in entries if e["type"] == "file"]
    assert len(file_entries) == 1
    assert file_entries[0]["name"] == "part.nc"
    assert file_entries[0]["vcarve_x_span"] == 100.0
    assert file_entries[0]["z_status"] == "ok"


def test_library_skips_non_nc_files(client, tmp_path, monkeypatch):
    (tmp_path / "readme.txt").write_text("ignore me")
    (tmp_path / "part.nc").write_text("( Material Size)\n( X=50, Y=50, Z=19)\nG00 X0 Y0\n")
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    r = client.get("/api/library")
    entries = r.get_json()["entries"]
    names = [e["name"] for e in entries]
    assert "readme.txt" not in names
    assert "part.nc" in names


# ── /api/load-file ────────────────────────────────────────────────────────────

def test_load_file_parses_and_returns_metadata(client, tmp_path, monkeypatch):
    nc = tmp_path / "602894-3.nc"
    nc.write_text(
        "( Material Size)\n( X= 426.0, Y= 648.0, Z= 19.05)\n"
        "(T2 = End Mill {0.5 inches})\nG43 H2 Z44.4754\nT2 M06\n"
        "G01 X10 Y10 Z-0.254\nM30\n"
    )
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    r = client.post("/api/load-file", json={"path": "602894-3.nc"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["vcarve_x_span"] == 426.0
    assert data["vcarve_y_span"] == 648.0
    assert data["material_thickness"] == 19.05
    assert data["z_status"] == "ok"
    assert data["pass_count"] == 1


def test_load_file_missing_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    r = client.post("/api/load-file", json={"path": "ghost.nc"})
    assert r.status_code == 404


def test_load_file_path_traversal_blocked(client, tmp_path, monkeypatch):
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    r = client.post("/api/load-file", json={"path": "../../../etc/passwd"})
    assert r.status_code == 400


# ── /api/place and /api/placements ───────────────────────────────────────────

NC_CONTENT = (
    "( Material Size)\n( X= 100.0, Y= 100.0, Z= 19.05)\n"
    "(T2 = End Mill {0.5 inches})\nG43 H2 Z44.4754\nT2 M06\n"
    "G01 X50 Y50 Z-0.254\nM30\n"
)


def _seed_library(tmp_path, monkeypatch, files=None):
    files = files or {"part.nc": NC_CONTENT}
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))


def test_place_returns_instance_id(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    r = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["instance_id"].startswith("part_")
    assert data["slot"] == "A39"


def test_job_safe_z_is_driven_by_stock_when_that_is_higher(client, tmp_path, monkeypatch):
    """19.05 stock + 29.972 clearance = 49.022, above part.nc's own 44.4754 retract."""
    _seed_library(tmp_path, monkeypatch)
    r = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    safe_z = r.get_json()["job_safe_z"]
    assert safe_z["value"] == pytest.approx(19.05 + 29.972, abs=1e-3)
    assert safe_z["driven_by"] == "part.nc (stock)"


def test_job_safe_z_honours_a_file_retract_above_stock_clearance(client, tmp_path, monkeypatch):
    """A part with a feature above the stock top retracts higher than the stock rule (#22)."""
    tall = (
        "( Material Size)\n( X= 100.0, Y= 100.0, Z= 19.05)\n"
        "(T2 D=12.7 CR=0. - ZMIN=14.605 - FLAT END MILL)\n"
        "T2 M06\nG43 Z57.15 H02\nG01 X50 Y50 Z14.605\nM30\n"
    )
    _seed_library(tmp_path, monkeypatch, {"tall.nc": tall})
    r = client.post("/api/place", json={"path": "tall.nc", "rail": "A", "slot_inches": 39})
    safe_z = r.get_json()["job_safe_z"]
    assert safe_z["value"] == pytest.approx(57.15)
    assert safe_z["driven_by"] == "tall.nc (retract)"


def test_place_invalid_slot_rejected(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    r = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 99})
    assert r.status_code == 400


def test_place_invalid_rail_rejected(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    r = client.post("/api/place", json={"path": "part.nc", "rail": "C", "slot_inches": 39})
    assert r.status_code == 400


def test_place_blocked_file_rejected(client, tmp_path, monkeypatch):
    legacy = (
        "( Material Size)\n( X= 100.0, Y= 100.0, Z= 19.05)\n"
        "(T2 = End Mill {0.5 inches})\nT2 M06\nG01 X50 Y50 Z-19.304\nM30\n"
    )
    _seed_library(tmp_path, monkeypatch, {"legacy.nc": legacy})
    r = client.post("/api/place", json={"path": "legacy.nc", "rail": "A", "slot_inches": 39})
    assert r.status_code == 422
    assert r.get_json()["error"] == "z_blocked"


def test_place_collision_returns_409(client, tmp_path, monkeypatch):
    # VCarve X = along rail = machine Y. A toolpath with X=-5000 extends far toward
    # higher machine Y, reaching adjacent slots.
    # Part at slot 52 (slot_mark=1727.2): toolpath min_y = 1727.2 - max_vx.
    # With max_vx=500, min_y = 1727.2-500=1227.2 (doesn't reach slot 39 blank at 1857.4-2057.4).
    # But with min_vx=-400, toolpath max_y = slot_mark - min_vx = 1727.2+400=2127.2 > 1857.4 → collision!
    oversized = (
        "( Material Size)\n( X= 200.0, Y= 100.0, Z= 19.05)\n"
        "(T2 = End Mill {0.5 inches})\nT2 M06\n"
        "G01 X100 Y10 Z-0.254\nG01 X-400 Y10 Z-0.254\nM30\n"
    )
    _seed_library(tmp_path, monkeypatch, {"big.nc": oversized})
    client.post("/api/place", json={"path": "big.nc", "rail": "A", "slot_inches": 52})
    r = client.post("/api/place", json={"path": "big.nc", "rail": "A", "slot_inches": 39})
    assert r.status_code == 409
    assert r.get_json()["error"] == "collision"


def test_get_placements_reflects_placed_parts(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    r = client.get("/api/placements")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["placements"]) == 1
    assert data["placements"][0]["slot"] == "A39"


def test_delete_placement(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    place_r = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    iid = place_r.get_json()["instance_id"]
    del_r = client.delete(f"/api/place/{iid}")
    assert del_r.status_code == 200
    assert del_r.get_json()["ok"] is True
    r = client.get("/api/placements")
    assert r.get_json()["placements"] == []


def test_delete_nonexistent_returns_404(client):
    r = client.delete("/api/place/ghost_1")
    assert r.status_code == 404


def test_place_multiple_instances_get_unique_ids(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    r1 = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    r2 = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 26})
    assert r1.get_json()["instance_id"] != r2.get_json()["instance_id"]


# ── /api/compatibility ────────────────────────────────────────────────────────

def test_compatibility_no_conflict(client, tmp_path, monkeypatch):
    same_tool = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T2 = End Mill {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    _seed_library(tmp_path, monkeypatch, {"a.nc": same_tool, "b.nc": same_tool})
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "b.nc", "rail": "A", "slot_inches": 26})
    r = client.get("/api/compatibility")
    data = r.get_json()
    assert data["has_conflict"] is False


def test_compatibility_detects_conflict(client, tmp_path, monkeypatch):
    file_a = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T2 = End Mill {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    file_b = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T2 = Spiral Bit {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    _seed_library(tmp_path, monkeypatch, {"a.nc": file_a, "b.nc": file_b})
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "b.nc", "rail": "A", "slot_inches": 26})
    r = client.get("/api/compatibility")
    data = r.get_json()
    assert data["has_conflict"] is True
    conflicts = [t for t in data["matrix"] if t["conflict"]]
    assert any(t["tool_number"] == "T2" for t in conflicts)


# ── /api/generate ─────────────────────────────────────────────────────────────

def test_generate_no_parts_returns_400(client):
    r = client.post("/api/generate", json={})
    assert r.status_code == 400


def test_generate_writes_nc_and_pdf(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    r = client.post("/api/generate", json={"job_name": "test_job"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert os.path.isfile(data["nc_path"])
    assert data["pdf_path"].endswith(".pdf")
    assert os.path.isfile(data["pdf_path"])
    with open(data["pdf_path"], "rb") as f:
        assert f.read(5) == b"%PDF-"


def test_generate_blocked_by_tool_conflict(client, tmp_path, monkeypatch):
    file_a = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T2 = End Mill {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    file_b = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T2 = Spiral Bit {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    _seed_library(tmp_path, monkeypatch, {"a.nc": file_a, "b.nc": file_b})
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "b.nc", "rail": "A", "slot_inches": 26})
    r = client.post("/api/generate", json={})
    assert r.status_code == 422


def test_generate_blocked_when_over_tool_capacity(client, tmp_path, monkeypatch):
    """A job needing more distinct tools than the changer holds must be refused."""
    header = "( Material Size)\n( X=100, Y=100, Z=19)\n"
    files = {}
    for n in range(1, 10):  # T1..T9 across nine parts = 9 distinct tools
        files[f"p{n}.nc"] = (
            f"{header}(T{n} = Tool {n} {{0.25 inches}})\n"
            f"T{n} M06\nG01 X10 Y10 Z-0.254\nM30\n"
        )
    _seed_library(tmp_path, monkeypatch, files)
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    monkeypatch.setitem(app_module.config["advanced"], "tool_capacity", 8)

    slots = [0, 13, 26, 39, 52, 65, 78, 91, 104]
    for n, slot in zip(range(1, 10), slots):
        client.post("/api/place", json={"path": f"p{n}.nc", "rail": "A", "slot_inches": slot})

    r = client.post("/api/generate", json={})
    assert r.status_code == 422
    body = r.get_json()
    assert body["error"] == "tool_capacity_exceeded"
    assert "9 tools" in body["message"]
    assert "holds only 8" in body["message"]
    # Nothing should have been written.
    assert not list(tmp_path.glob("*.nc")) or all(
        p.name.startswith("p") for p in tmp_path.glob("*.nc")
    )


def test_placements_report_tool_capacity(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    info = client.get("/api/placements").get_json()
    assert info["tool_capacity"] == app_module._tool_capacity()
    assert info["tools_over_capacity"] is False
    assert info["tool_count"] == len(info["tool_sequence"])


# ── tool-change count (issue #7) ──────────────────────────────────────────────
#
# `tool_changes` is a count of T# M06 events, not of distinct tools. When two
# parts disagree about tool order the pass-index walk revisits a tool, and every
# revisit is a real change back. Reporting distinct-minus-one understated it.

_T1_THEN_T2 = (
    "( Material Size)\n( X=100, Y=100, Z=19)\n"
    "(T1 = End Mill {0.25 inches})\n(T2 = Spiral Bit {0.5 inches})\n"
    "T1 M06\nG01 X10 Y10 Z-0.254\nG53 G49 Z0\nM05\n"
    "T2 M06\nG01 X20 Y20 Z-0.254\nG53 G49 Z0\nM05\nM30\n"
)
_T2_THEN_T1 = (
    "( Material Size)\n( X=100, Y=100, Z=19)\n"
    "(T1 = End Mill {0.25 inches})\n(T2 = Spiral Bit {0.5 inches})\n"
    "T2 M06\nG01 X10 Y10 Z-0.254\nG53 G49 Z0\nM05\n"
    "T1 M06\nG01 X20 Y20 Z-0.254\nG53 G49 Z0\nM05\nM30\n"
)


def test_tool_changes_counts_revisits_not_distinct_tools(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch, {"ab.nc": _T1_THEN_T2, "ba.nc": _T2_THEN_T1})
    client.post("/api/place", json={"path": "ab.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "ba.nc", "rail": "A", "slot_inches": 26})

    info = client.get("/api/placements").get_json()
    # Two distinct tools, but the blocks run T1, T2, T1, T2.
    assert info["tool_sequence"] == ["T1", "T2"]
    assert info["tool_count"] == 2
    assert info["tool_changes"] == 4


def test_tool_changes_matches_the_emitted_file(client, tmp_path, monkeypatch):
    """The live figure and the file the machine runs must agree."""
    _seed_library(tmp_path, monkeypatch, {"ab.nc": _T1_THEN_T2, "ba.nc": _T2_THEN_T1})
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    client.post("/api/place", json={"path": "ab.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "ba.nc", "rail": "A", "slot_inches": 26})

    expected = client.get("/api/placements").get_json()["tool_changes"]
    r = client.post("/api/generate", json={"job_name": "changes"})
    assert r.status_code == 200
    with open(r.get_json()["nc_path"]) as f:
        emitted = f.read()
    assert len(re.findall(r"^N\d+\s+T\d+ M06\b", emitted, re.MULTILINE)) == expected


def test_job_runtime_charges_every_tool_change(client, tmp_path, monkeypatch):
    """
    Per-part runtimes exclude tool-change time; the job total adds it back once
    per emitted block. Without this, the always-on-touch-off posture looks free.
    """
    _seed_library(tmp_path, monkeypatch, {"ab.nc": _T1_THEN_T2, "ba.nc": _T2_THEN_T1})
    client.post("/api/place", json={"path": "ab.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "ba.nc", "rail": "A", "slot_inches": 26})

    info = client.get("/api/placements").get_json()
    cutting = sum(p.part.runtime_seconds for p in app_module._placements.values())
    assert info["runtime_seconds"] == pytest.approx(
        cutting + 4 * DEFAULT_TOOL_CHANGE_SECONDS, abs=0.01,
    )


# ── /api/save-job and /api/load-job ──────────────────────────────────────────

def test_save_and_reload_job(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})

    save_r = client.post("/api/save-job", json={"job_name": "myjob"})
    assert save_r.status_code == 200
    cnj_path = save_r.get_json()["path"]
    assert os.path.isfile(cnj_path)

    # Clear state and reload
    app_module._placements.clear()
    app_module._placement_paths.clear()

    load_r = client.post("/api/load-job", json={"path": cnj_path})
    assert load_r.status_code == 200
    data = load_r.get_json()
    assert data["ok"] is True
    assert len(data["placements"]) == 1
    assert data["placements"][0]["slot"] == "A39"


def test_save_job_no_parts_returns_400(client):
    r = client.post("/api/save-job", json={})
    assert r.status_code == 400


def test_load_job_missing_file_returns_404(client):
    r = client.post("/api/load-job", json={"path": "/nonexistent/job.cnj"})
    assert r.status_code == 404


def test_load_job_missing_library_file_warns(client, tmp_path, monkeypatch):
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))
    job = {
        "version": "1.0",
        "created": "2026-01-01T00:00:00",
        "job_name": "test",
        "placements": [
            {"filename": "ghost.nc", "path": "ghost.nc", "rail": "A", "slot_inches": 39, "instance_id": "ghost_1"}
        ],
    }
    cnj = tmp_path / "test.cnj"
    cnj.write_text(json.dumps(job))
    r = client.post("/api/load-job", json={"path": str(cnj)})
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["warnings"]) == 1
    assert "ghost.nc" in data["warnings"][0]
    assert data["placements"] == []
