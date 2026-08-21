"""
API route tests using Flask's built-in test client.
All file system access is either stubbed or uses temp dirs.
"""
import json
import os
import re
from pathlib import Path

import pytest

import app as app_module
from app import app
from gcode_generator import RUNTIME_COMMENT_PREFIX, runtime_comment
from runtime_estimator import (
    DEFAULT_TOOL_CHANGE_SECONDS, SPINDLE_START_SECONDS, TOOL_SWAP_SECONDS,
    TOUCH_OFF_SECONDS, estimate_lines_runtime,
)


# ── /api/config ───────────────────────────────────────────────────────────────

def test_get_config(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.get_json()
    assert "advanced" in data


def test_config_no_longer_carries_a_tool_map(client, monkeypatch):
    """`config.json`'s pocket-keyed `tools` map is deleted, not migrated (spec §8).

    It was junk in its entirety: `T4` "Table Stiff" declared 0.75" for a cutter that is
    actually 2.38", so carrying it over would have imported a number that under-inflates
    the X envelope by 1.6" on a real tool. Nothing keyed by *pocket* survives into a
    library keyed by *identity*, which is the whole point of §1.
    """
    import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "save_config", lambda data: None)
    monkeypatch.setattr(app_module, "save_config", lambda data: None)

    assert "tools" not in client.get("/api/config").get_json()

    # And the settings panel cannot put one back.
    r = client.post("/api/config", json={"tools": {"T99": {"diameter_inches": 0.1}}})
    assert r.status_code == 200
    assert "tools" not in r.get_json()


def test_post_config_sets_the_park_position(client, monkeypatch):
    """The end-of-job park is an editable setting, written in machine mm.

    Values outboard of `machine_travel` are accepted on purpose: that box is the
    machinable surface, and travel runs past it — the shipped park X0 is already
    outboard of x_min 61.493.
    """
    import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "save_config", lambda data: None)
    monkeypatch.setattr(app_module, "save_config", lambda data: None)

    r = client.post("/api/config", json={"advanced": {"park_x": 0.0, "park_y": 3200.5}})
    assert r.status_code == 200
    adv = r.get_json()["advanced"]
    assert adv["park_x"] == pytest.approx(0.0)
    assert adv["park_y"] == pytest.approx(3200.5)


def test_post_config_rejects_a_non_numeric_park(client, monkeypatch):
    """A bad park is a G53 move, so it is refused at the field, not at Generate."""
    import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "save_config", lambda data: None)
    monkeypatch.setattr(app_module, "save_config", lambda data: None)

    adv = app_module.config["advanced"]
    adv["park_x"], adv["park_y"] = 0.0, 3048.0
    for bad in ("far left", None, float("nan")):
        r = client.post("/api/config", json={"advanced": {"park_x": bad}})
        assert r.status_code == 400, bad
        assert "Park X" in r.get_json()["error"]
        # and the previous park is still in force
        assert adv["park_x"] == pytest.approx(0.0)


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
        "(T2 = EM-0500 End Mill {0.5 inches})\nG00 X0 Y0\nG01 X10 Y10\n"
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
        "(T2 = EM-0500 End Mill {0.5 inches})\nG43 H2 Z44.4754\nT2 M06\n"
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
    "(T2 = EM-0500 End Mill {0.5 inches})\nG43 H2 Z44.4754\nT2 M06\n"
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
    # Fusion-posted, so the code arrives in the TOOLID comment's PRODUCT field rather
    # than in a VCarve tool name. Note the blank VENDOR: emitted empty, never omitted.
    tall = (
        "( Material Size)\n( X= 100.0, Y= 100.0, Z= 19.05)\n"
        "(T2 D=12.7 CR=0. - ZMIN=14.605 - FLAT END MILL)\n"
        "(TOOLID T2 VENDOR= CODE=EM-0500 FLUTES=3)\n"
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
        "(T2 = EM-0500 End Mill {0.5 inches})\nT2 M06\nG01 X50 Y50 Z-19.304\nM30\n"
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
        "(T2 = EM-0500 End Mill {0.5 inches})\nT2 M06\n"
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


def test_clear_all_placements_empties_the_bed(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "part.nc", "rail": "B", "slot_inches": 39})
    r = client.delete("/api/placements")
    assert r.status_code == 200
    assert r.get_json()["removed"] == 2
    assert client.get("/api/placements").get_json()["placements"] == []
    assert app_module._placement_paths == {}


def test_clear_all_keeps_binds_and_pocket_overrides(client, tmp_path, monkeypatch):
    """One button, one job: emptying the bed is not a job reset.

    A bind names what a *file's* tool is and a drag names where a *tool* lives; both
    outlive the nest they were made in, and the dock has its own reset for the second.
    """
    _seed_library(tmp_path, monkeypatch)
    client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    app_module._tool_binds["part.nc"] = {"T2": "TT-0001"}
    app_module._pocket_overrides["TT-0001"] = 5

    client.delete("/api/placements")

    assert app_module._tool_binds == {"part.nc": {"T2": "TT-0001"}}
    assert app_module._pocket_overrides == {"TT-0001": 5}


def test_clear_all_restarts_instance_numbering(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    first = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    client.delete("/api/placements")
    again = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    assert again.get_json()["instance_id"] == first.get_json()["instance_id"]


def test_clear_all_on_an_empty_bed_is_not_an_error(client):
    r = client.delete("/api/placements")
    assert r.status_code == 200
    assert r.get_json()["removed"] == 0


def test_delete_nonexistent_returns_404(client):
    r = client.delete("/api/place/ghost_1")
    assert r.status_code == 404


def test_place_multiple_instances_get_unique_ids(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch)
    r1 = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    r2 = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 26})
    assert r1.get_json()["instance_id"] != r2.get_json()["instance_id"]


# ── /api/changer — what retired the compatibility panel ──────────────────────

def test_two_files_sharing_one_cutter_make_one_dock_entry(client, tmp_path, monkeypatch):
    same_tool = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T2 = EM-0500 End Mill {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    _seed_library(tmp_path, monkeypatch, {"a.nc": same_tool, "b.nc": same_tool})
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "b.nc", "rail": "A", "slot_inches": 26})

    st = client.get("/api/changer").get_json()
    assert [t["code"] for t in st["tools"]] == ["EM-0500"]
    assert st["tools"][0]["part_count"] == 2
    assert st["valid"] is True


def test_two_files_whose_t2_is_a_different_cutter_do_not_merge(client, tmp_path, monkeypatch):
    """The case `_tool_compatibility` was sound on. The dock separates them by identity
    rather than by noticing their description strings differ."""
    file_a = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T2 = EM-0500 End Mill {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    file_b = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T2 = SB-0500 Spiral Bit {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    _seed_library(tmp_path, monkeypatch, {"a.nc": file_a, "b.nc": file_b})
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "b.nc", "rail": "A", "slot_inches": 26})

    st = client.get("/api/changer").get_json()
    assert sorted(t["code"] for t in st["tools"]) == ["EM-0500", "SB-0500"]
    # Different declared slots, so nothing to resolve — they simply do not merge.
    assert st["valid"] is True


def test_two_different_cutters_sharing_a_stale_description_are_still_separated(
        client, tmp_path, monkeypatch):
    """The case `_tool_compatibility` was BLIND to, and the reason its signal had to go.

    `conflict` fired only when one `T#` carried *differing* description strings, so two
    genuinely different cutters posting a byte-identical stale string sailed through
    into a merged block — the inverse failure §3.1 warns about. The real library has
    exactly this case. Identity is not read out of the string at all now.
    """
    body = "\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    header = "( Material Size)\n( X=100, Y=100, Z=19)\n"
    file_a = header + "(T2 = EM-0500 End Mill {0.5 inches})" + body
    file_b = header + "(T2 = SB-0500 End Mill {0.5 inches})" + body   # same words
    _seed_library(tmp_path, monkeypatch, {"a.nc": file_a, "b.nc": file_b})
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "b.nc", "rail": "A", "slot_inches": 26})

    st = client.get("/api/changer").get_json()
    assert sorted(t["code"] for t in st["tools"]) == ["EM-0500", "SB-0500"]


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


def test_the_header_states_the_cycle_time_the_setup_sheet_states(
        client, tmp_path, monkeypatch):
    """One estimate, three places to read it.

    The operator reads the cycle time off whichever of the three is in front of them —
    the program at the control, the setup sheet at the bench, the PDF on the wall — so
    a job that quotes two different numbers is worse than one that quotes none. They
    agree by construction (`with_runtime_header` is handed the estimate the sheet
    reports, rather than taking its own), and this is what keeps that true.
    """
    _seed_library(tmp_path, monkeypatch, {"a.nc": _SETUP_A, "b.nc": _SETUP_B})
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    for name, slot in (("a.nc", 39), ("b.nc", 26)):
        client.post("/api/place", json={"path": name, "rail": "A", "slot_inches": slot})
    r = client.post("/api/generate", json={"job_name": "timed"})
    assert r.status_code == 200, r.get_json()

    written = (tmp_path / "timed.nc").read_text().splitlines()
    header = [ln for ln in written if ln.startswith(f"({RUNTIME_COMMENT_PREFIX}")]
    assert len(header) == 1
    sheet = (tmp_path / "timed_setup.txt").read_text()
    stated = re.search(r"Estimated cycle time: (.+)", sheet).group(1).strip()
    assert stated in header[0]

    # A comment, so it sits above the first numbered block and renumbers nothing.
    assert written.index(header[0]) < next(
        i for i, ln in enumerate(written) if ln.startswith("N"))


def test_the_header_cycle_time_survives_its_own_insertion(
        client, tmp_path, monkeypatch):
    """The line states an estimate of the file it is inserted into.

    Which is circular unless adding it cannot change the estimate — it cannot, because
    the estimator skips comments, and this is the assertion that says so rather than
    the docstring. Re-reading the written file and re-pricing it has to reproduce the
    number the file claims.
    """
    _seed_library(tmp_path, monkeypatch, {"a.nc": _SETUP_A, "b.nc": _SETUP_B})
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    for name, slot in (("a.nc", 39), ("b.nc", 26)):
        client.post("/api/place", json={"path": name, "rail": "A", "slot_inches": slot})
    assert client.post("/api/generate", json={"job_name": "timed"}).status_code == 200

    written = (tmp_path / "timed.nc").read_text()
    again = estimate_lines_runtime(
        written.splitlines(),
        limits=app_module._motion_limits(),
        tool_change_seconds=app_module._tool_change_seconds(),
    )
    assert runtime_comment(again) in written


def test_generate_blocked_by_a_contested_pocket(client, tmp_path, monkeypatch,
                                                isolated_library):
    """The gate is the same 422 that has always been here, pointed at a sound signal."""
    from tests.conftest import make_tool
    isolated_library.upsert(make_tool("EM-0500", default_slot=2))
    isolated_library.upsert(make_tool("SB-0500", default_slot=2))    # same home
    file_a = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T2 = EM-0500 End Mill {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    file_b = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T2 = SB-0500 Spiral Bit {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    _seed_library(tmp_path, monkeypatch, {"a.nc": file_a, "b.nc": file_b})
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "b.nc", "rail": "A", "slot_inches": 26})
    r = client.post("/api/generate", json={})
    assert r.status_code == 422
    assert r.get_json()["error"] == "changer_map_invalid"


def test_generate_blocked_when_over_tool_capacity(client, tmp_path, monkeypatch):
    """Nine tools, eight pockets.

    Capacity is **not a rule of its own** (§3.4): rules 2 and 3 — every resolved tool in
    exactly one pocket, no pocket holding two — make a ninth tool unsatisfiable on their
    own, so it is simply a tool with nowhere to go, sitting in "needs a home" with
    Generate disabled. Nothing is refused at *placement*, because identity merging can
    still lower the count.
    """
    header = "( Material Size)\n( X=100, Y=100, Z=19)\n"
    files = {}
    for n in range(1, 10):  # T1..T9 across nine parts = 9 distinct tools
        files[f"p{n}.nc"] = (
            f"{header}(T{n} = TT-{n:04d} Tool {n} {{0.25 inches}})\n"
            f"T{n} M06\nG01 X10 Y10 Z-0.254\nM30\n"
        )
    _seed_library(tmp_path, monkeypatch, files)
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    monkeypatch.setitem(app_module.config["advanced"], "tool_capacity", 8)

    slots = [0, 13, 26, 39, 52, 65, 78, 91, 104]
    for n, slot in zip(range(1, 10), slots):
        client.post("/api/place", json={"path": f"p{n}.nc", "rail": "A", "slot_inches": slot})

    # Every one of the nine placements is accepted — only generation is gated.
    assert len(app_module._placements) == 9

    r = client.post("/api/generate", json={})
    assert r.status_code == 422
    body = r.get_json()
    assert body["error"] == "changer_map_invalid"
    assert "no home" in body["message"] and "changer is full" in body["message"]
    assert body["changer"]["staged"] == ["TT-0009"]
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
    "(T1 = EM-0250 End Mill {0.25 inches})\n(T2 = SB-0500 Spiral Bit {0.5 inches})\n"
    "T1 M06\nG01 X10 Y10 Z-0.254\nG53 G49 Z0\nM05\n"
    "T2 M06\nG01 X20 Y20 Z-0.254\nG53 G49 Z0\nM05\nM30\n"
)
_T2_THEN_T1 = (
    "( Material Size)\n( X=100, Y=100, Z=19)\n"
    "(T1 = EM-0250 End Mill {0.25 inches})\n(T2 = SB-0500 Spiral Bit {0.5 inches})\n"
    "T2 M06\nG01 X10 Y10 Z-0.254\nG53 G49 Z0\nM05\n"
    "T1 M06\nG01 X20 Y20 Z-0.254\nG53 G49 Z0\nM05\nM30\n"
)


def test_tool_changes_counts_revisits_not_distinct_tools(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch, {"ab.nc": _T1_THEN_T2, "ba.nc": _T2_THEN_T1})
    client.post("/api/place", json={"path": "ab.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "ba.nc", "rail": "A", "slot_inches": 26})

    info = client.get("/api/placements").get_json()
    # Two distinct tools, but the blocks run T1, T3, T1, T3 (pockets 1 and 3) — plus
    # the end-of-job change into pocket 2, which the shipped config asks for.
    assert info["tool_sequence"] == ["T1", "T2"]
    assert info["tool_count"] == 2
    assert info["tool_changes"] == 5


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

    The posture is pinned rather than inherited from `config.json`: the subject here
    is that *every* change is charged, not which of the two costs the shop is
    currently on, and a config the operator edits in Settings must not be able to
    fail a test about the count.
    """
    _seed_library(tmp_path, monkeypatch, {"ab.nc": _T1_THEN_T2, "ba.nc": _T2_THEN_T1})
    monkeypatch.setitem(app_module.config["advanced"], "auto_tool_touch_off", True)
    client.post("/api/place", json={"path": "ab.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "ba.nc", "rail": "A", "slot_inches": 26})

    info = client.get("/api/placements").get_json()
    cutting = sum(p.part.runtime_seconds for p in app_module._placements.values())
    assert info["runtime_seconds"] == pytest.approx(
        # Four blocks plus the end-of-job change into pocket 2 — and one spindle
        # start per *block*, since the end-of-job change cuts nothing and spins
        # nothing up.
        cutting + 5 * DEFAULT_TOOL_CHANGE_SECONDS + 4 * SPINDLE_START_SECONDS,
        abs=0.01,
    )


# ── the operator setup sheet (issue #13) ─────────────────────────────────────
#
# Pocket assignment is job-scoped and the app deliberately holds no model of the
# physical changer, so the operator has to load the machine to match a map the app
# invented. If that map is not communicated clearly, the feature manufactures exactly
# the failure it exists to prevent — which makes this sheet the feature's other half.

_SETUP_A = (
    "( Material Size)\n( X=100, Y=100, Z=19)\n"
    "(T2 = EM-0500 End Mill {0.5 inches})\nT2 M06\nG01 X10 Y10 Z-0.254\nM30\n"
)
_SETUP_B = (
    "( Material Size)\n( X=100, Y=100, Z=19)\n"
    "(T4 = SB-0500 Spiral Bit {0.5 inches})\nT4 M06\nG01 X10 Y10 Z-0.254\nM30\n"
)


def _generate_with_setup(client, tmp_path, monkeypatch, files=None, drags=None):
    _seed_library(tmp_path, monkeypatch, files or {"a.nc": _SETUP_A, "b.nc": _SETUP_B})
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    for name, slot in zip(sorted(files or {"a.nc": 1, "b.nc": 1}), (39, 26, 13, 0)):
        client.post("/api/place", json={"path": name, "rail": "A", "slot_inches": slot})
    for code, pocket in (drags or {}).items():
        client.post("/api/changer/assign", json={"code": code, "pocket": pocket})
    r = client.post("/api/generate", json={"job_name": "sheet"})
    assert r.status_code == 200, r.get_json()
    return (tmp_path / "sheet_setup.txt").read_text()


def test_setup_sheet_is_keyed_on_pocket_not_on_the_files_tool_number(
        client, tmp_path, monkeypatch):
    """One cutter can be T2 in one file and T4 in another; the pocket is what the
    operator loads and what the emitted `T# M06` calls."""
    sheet = _generate_with_setup(client, tmp_path, monkeypatch)
    assert "Pocket 2" in sheet and "Pocket 3" in sheet
    assert "EM-0500" in sheet and "SB-0500" in sheet
    # The file's own number is still named, because the operator reads this beside a
    # CAM file that says T4.
    assert "files call it T2" in sheet and "files call it T4" in sheet


def test_setup_sheet_names_the_parts_each_tool_is_needed_by(client, tmp_path, monkeypatch):
    sheet = _generate_with_setup(client, tmp_path, monkeypatch)
    assert "needed by a.nc" in sheet and "needed by b.nc" in sheet


def test_setup_sheet_marks_a_moved_tool_as_temporary(client, tmp_path, monkeypatch):
    """A deviation is a *temporary* instruction, because the declared slot is the
    standard the operator is being trained toward (spec §3.2.1)."""
    sheet = _generate_with_setup(client, tmp_path, monkeypatch, drags={"SB-0500": 7})
    assert "Pocket 7" in sheet
    assert "moved for this job only" in sheet
    assert "normally lives in pocket 3" in sheet
    assert "put it back after" in sheet


def test_setup_sheet_says_nothing_temporary_when_nothing_moved(client, tmp_path, monkeypatch):
    sheet = _generate_with_setup(client, tmp_path, monkeypatch)
    assert "moved for this job only" not in sheet


def test_setup_sheet_lists_the_pockets_the_job_leaves_alone(client, tmp_path, monkeypatch):
    sheet = _generate_with_setup(client, tmp_path, monkeypatch)
    assert "Pockets not used by this job: 1, 4, 5, 6, 7, 8" in sheet


def test_setup_sheet_says_which_tool_the_program_leaves_in_the_spindle(
        client, tmp_path, monkeypatch):
    """The program hands the machine back with a tool already up (`end_of_job_pocket`),
    which the operator has to know before reaching for the next job — and if this job
    borrowed that pocket, this is the line that says what the spindle actually keeps."""
    sheet = _generate_with_setup(client, tmp_path, monkeypatch)
    assert "Ends holding pocket 2" in sheet
    assert '1/2" End Mill' in sheet


def test_setup_sheet_is_silent_about_the_spindle_when_no_tool_is_left_in_it(
        client, tmp_path, monkeypatch):
    monkeypatch.setitem(app_module.config["advanced"], "end_of_job_pocket", None)
    sheet = _generate_with_setup(client, tmp_path, monkeypatch)
    assert "Ends holding" not in sheet


def test_setup_sheet_is_written_beside_the_output(client, tmp_path, monkeypatch):
    _seed_library(tmp_path, monkeypatch, {"a.nc": _SETUP_A})
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    r = client.post("/api/generate", json={"job_name": "beside"})
    data = r.get_json()
    assert os.path.isfile(data["setup_path"])
    assert data["setup_path"].endswith("beside_setup.txt")


def test_the_pdf_carries_the_same_setup_rows(client, tmp_path, monkeypatch):
    """The sheet and the PDF are built from one changer state, so they cannot disagree
    about which pocket holds what."""
    _seed_library(tmp_path, monkeypatch, {"a.nc": _SETUP_A, "b.nc": _SETUP_B})
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "b.nc", "rail": "A", "slot_inches": 26})
    client.post("/api/changer/assign", json={"code": "SB-0500", "pocket": 7})

    state = app_module._changer_state()
    meta, parts, _geom = app_module._build_pdf_model("x", app_module.config, "", state)
    rows = {r["pocket"]: r for r in meta["setup"]}
    assert set(rows) == {2, 7}
    assert rows[7]["code"] == "SB-0500" and rows[7]["off_home"] is True
    assert rows[2]["off_home"] is False
    # And the placement table shows the file's number beside the pocket it runs from.
    tools = {t["tool_number"]: t["pocket"] for p in parts for t in p["tools"]}
    assert tools == {"T2": 2, "T4": 7}


def test_generation_is_not_gated_on_confirming_the_changer_is_loaded(
        client, tmp_path, monkeypatch):
    """The sheet informs, it does not interrogate (operator's call, 2026-08-17)."""
    _seed_library(tmp_path, monkeypatch, {"a.nc": _SETUP_A})
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    assert client.post("/api/generate", json={"job_name": "nogate"}).status_code == 200


# ── stale-page protection ────────────────────────────────────────────────────

def test_static_assets_are_cache_busted_on_edit(client, tmp_path, monkeypatch):
    """A tab left open across a server restart is the normal case, not an edge one.

    `git pull` + restart changes the API underneath a page still holding the old JS,
    and that mismatch is silent: an old client that did not recognise a new error slug
    printed the slug itself into the status bar, with no dialog and no clue the page
    was stale. Stamping each asset URL with its mtime means a reload can never serve
    yesterday's module against today's routes.
    """
    page = client.get("/").get_data(as_text=True)
    stamped = re.findall(r'src="/static/([a-z]+\.js)\?v=(\d+)"', page)
    names = {name for name, _ in stamped}
    assert {"placement.js", "changer.js", "toollib.js", "layout.js"} <= names
    assert all(int(v) > 0 for _, v in stamped)

    # The stamp moves when the file does, so a cached copy cannot survive an edit.
    js = Path(app_module.app.static_folder) / "placement.js"
    before = app_module._asset("placement.js")
    os.utime(js, (js.stat().st_atime, js.stat().st_mtime + 60))
    try:
        assert app_module._asset("placement.js") != before
    finally:
        os.utime(js, (js.stat().st_atime, js.stat().st_mtime - 60))


def test_every_placement_refusal_carries_a_sentence_not_just_a_slug(
        client, tmp_path, monkeypatch):
    """Operators are not developers, so `error` is for the log and `message` is for
    them. A refusal that ships only a slug is one the operator cannot act on."""
    uncoded = (
        "( Material Size)\n( X=100, Y=100, Z=19)\n"
        "(T4 = Table Stiff)\nT4 M06\nG01 X10 Y10 Z-0.254\nM30\n"
    )
    _seed_library(tmp_path, monkeypatch, {"a.nc": uncoded})
    r = client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    assert r.status_code == 422
    body = r.get_json()
    assert body["error"] == "unresolved_tools"
    assert len(body.get("message", "")) > 40
    assert "_" not in body["message"]        # no slug leaked into the prose


# ── auto job names (2026-08-20) ───────────────────────────────────────────────

def test_the_auto_job_name_carries_no_year_and_no_seconds():
    """`0820-1430`. The name is what the operator reads off the control's program
    list, and neither the century nor the second ever told two jobs apart."""
    assert re.fullmatch(r"\d{4}-\d{4}", app_module._timestamp())


def test_a_second_nest_in_the_same_minute_does_not_overwrite_the_first(
        tmp_path, monkeypatch):
    """Dropping the seconds means the bare name repeats, so the guard replaces them.

    It costs more than a stray file: the Syntec identifies a program by file name —
    a merged master carries no O-word — so two programs answering to `0820-1430`
    are ambiguous on the machine, not just in the output folder.
    """
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    assert app_module._unique_job_name("0820-1430") == "0820-1430"
    (tmp_path / "0820-1430.nc").write_text("")
    assert app_module._unique_job_name("0820-1430") == "0820-1430b"
    (tmp_path / "0820-1430b.nc").write_text("")
    assert app_module._unique_job_name("0820-1430") == "0820-1430c"
    # An unrelated minute is untouched by any of it.
    assert app_module._unique_job_name("0820-1431") == "0820-1431"


def test_a_name_the_operator_typed_is_never_renamed(client, tmp_path, monkeypatch):
    """The guard is for names nobody chose. Typing one and re-generating over it is
    a normal thing to do, so an explicit name is used verbatim both times."""
    _seed_library(tmp_path, monkeypatch, {"a.nc": _SETUP_A})
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    first = client.post("/api/generate", json={"job_name": "table-legs"}).get_json()
    second = client.post("/api/generate", json={"job_name": "table-legs"}).get_json()
    assert first["job_name"] == second["job_name"] == "table-legs"
    assert first["nc_path"] == second["nc_path"]


def test_the_shipped_format_adds_nothing_to_the_stamp(client, tmp_path, monkeypatch):
    """`nest_` was five characters of no information — every file the app writes is
    a nest. The placeholder machinery stays; the shipped value is bare."""
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    with open("config.json") as f:
        assert json.load(f)["job_name_format"] == "{timestamp}"
    assert re.fullmatch(r"\d{4}-\d{4}", app_module._job_name({}))


# ── auto tool touch-off posture (2026-08-20) ──────────────────────────────────
#
# Issue #8 decided the posture — auto tool on, every job — and the default is still
# that. What is new is that the estimate can price the other one without a code edit.
# The gap is 4-7.5 minutes on a typical job, and a number you have to edit the source
# to see is a number nobody looks at.

def test_turning_auto_tool_off_moves_the_touch_off_out_of_the_cycle(
    client, tmp_path, monkeypatch,
):
    """The whole arithmetic issue #8 trades against, now live rather than hypothetical.

    Turning the posture off must drop the job total by exactly
    `TOUCH_OFF_SECONDS x tool_changes` — the touch-off does not vanish, it moves into
    setup. That this is a subtraction and not a re-derivation is why the two constants
    stay split.
    """
    _seed_library(tmp_path, monkeypatch, {"ab.nc": _T1_THEN_T2, "ba.nc": _T2_THEN_T1})
    client.post("/api/place", json={"path": "ab.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "ba.nc", "rail": "A", "slot_inches": 26})

    monkeypatch.setitem(app_module.config["advanced"], "auto_tool_touch_off", True)
    on = client.get("/api/placements").get_json()
    monkeypatch.setitem(app_module.config["advanced"], "auto_tool_touch_off", False)
    off = client.get("/api/placements").get_json()

    # Relational against the reported count rather than a literal: the posture must
    # not change *how many* changes there are, only what each one costs.
    changes = on["tool_changes"]
    assert changes == off["tool_changes"] and changes > 0
    assert on["runtime_seconds"] - off["runtime_seconds"] == pytest.approx(
        changes * TOUCH_OFF_SECONDS, abs=0.01,
    )
    assert app_module._tool_change_seconds() == TOOL_SWAP_SECONDS


def test_a_missing_posture_key_reads_as_the_shipped_posture(monkeypatch):
    """`config.py` has no defaults dict — every read site supplies its own fallback —
    so a config predating this key must behave as auto tool on, not off."""
    advanced = dict(app_module.config["advanced"])
    advanced.pop("auto_tool_touch_off", None)
    monkeypatch.setitem(app_module.config, "advanced", advanced)
    assert app_module._tool_change_seconds() == DEFAULT_TOOL_CHANGE_SECONDS


def test_a_boolean_advanced_setting_round_trips(client, monkeypatch):
    """Guards the flat `config["advanced"].update()` in `api_config_post`, and the
    JS-side trap it mirrors: `false` is a real value, so it must not be pruned the
    way a blank number field is."""
    import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "save_config", lambda data: None)
    monkeypatch.setattr(app_module, "save_config", lambda data: None)
    monkeypatch.setitem(app_module.config["advanced"], "auto_tool_touch_off", True)

    r = client.post("/api/config", json={"advanced": {"auto_tool_touch_off": False}})
    assert r.status_code == 200
    assert r.get_json()["advanced"]["auto_tool_touch_off"] is False
    assert client.get("/api/config").get_json()["advanced"]["auto_tool_touch_off"] is False


def test_changing_the_motion_model_reprices_loaded_parts(client, tmp_path, monkeypatch):
    """`PlacedPart.part` is the same object as `_loaded[rel]`, so a config edit that
    changes the acceleration would otherwise leave every cached per-part runtime on
    the old machine model while the job's tool-change term used the new one."""
    import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "save_config", lambda data: None)
    monkeypatch.setattr(app_module, "save_config", lambda data: None)
    # Needs a real feedrate: a G01 with no modal F is unpriceable, so a fixture
    # without one reprices from zero to zero and the test proves nothing.
    fed = _T1_THEN_T2.replace("G01 X10 Y10 Z-0.254", "G01 X10 Y10 Z-0.254 F1000")
    fed = fed.replace("G01 X20 Y20 Z-0.254", "G01 X20 Y20 Z-0.254 F1000")
    _seed_library(tmp_path, monkeypatch, {"ab.nc": fed})
    client.post("/api/place", json={"path": "ab.nc", "rail": "A", "slot_inches": 39})

    before = client.get("/api/placements").get_json()["runtime_seconds"]
    placed = next(iter(app_module._placements.values()))
    per_part_before = placed.part.runtime_seconds

    r = client.post("/api/config", json={"advanced": {"accel_mm_s2": 20.0}})
    assert r.status_code == 200

    assert placed.part.runtime_seconds > per_part_before
    assert client.get("/api/placements").get_json()["runtime_seconds"] > before
