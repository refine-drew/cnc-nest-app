"""
Pocket assignment and the §3.4 validity gate.

Every test here is really the same test: **the assigner makes no arbitrary choices**
(§3.2.1). It seeds declared default slots and stops — no tie-break, no fill rule, no
write-back. Determinism falls out of that rather than being enforced, which is why a
"stabilising" tie-break would be the one change that breaks it.
"""
import pytest

import app as app_module
from tests.conftest import make_tool
from gcode_parser import GcodePass, GcodePart, ZValidation
from pocket_map import STAGED, build_changer_state
from tool_library import ToolLibrary, resolve_part


def _part(tools, tool_numbers, filename="p.nc"):
    return GcodePart(
        filename=filename,
        vcarve_x_span=100.0, vcarve_y_span=100.0, material_thickness=19.05,
        tools=tools,
        min_vx=0.0, max_vx=100.0, min_vy=0.0, max_vy=100.0,
        raw_lines=[], z_validation=ZValidation(status="ok"),
        passes=[GcodePass(pass_index=i, tool_number=tn)
                for i, tn in enumerate(tool_numbers)],
    )


def _entry(library, instance_id, filename, slot, tools, tool_numbers, binds=None):
    part = _part(tools, tool_numbers, filename)
    return {"instance_id": instance_id, "filename": filename, "slot": slot,
            "resolution": resolve_part(library, part, binds)}


def _state(library, entries, overrides=None, capacity=8):
    return build_changer_state(library, entries, overrides, capacity)


def _pocket(state, number):
    return next(p["codes"] for p in state["pockets"] if p["number"] == number)


# ── seeding ───────────────────────────────────────────────────────────────────

def test_a_tool_takes_its_declared_default_slot():
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2)])
    st = _state(lib, [_entry(lib, "p_1", "p.nc", "A0", {"T2": {"code": "EM-0512"}}, ["T2"])])
    assert st["assignment"] == {"EM-0512": 2}
    assert _pocket(st, 2) == ["EM-0512"]
    assert st["valid"] is True


def test_a_tool_with_no_declared_slot_is_staged_not_dropped_into_the_lowest_free_one():
    """Auto-fill was recommended and rejected. The app never invents a pocket."""
    lib = ToolLibrary([make_tool("BB-0250", default_slot=None)])
    st = _state(lib, [_entry(lib, "p_1", "p.nc", "A0", {"T7": {"code": "BB-0250"}}, ["T7"])])
    assert st["assignment"] == {"BB-0250": STAGED}
    assert st["staged"] == ["BB-0250"]
    assert st["free"] == [1, 2, 3, 4, 5, 6, 7, 8]   # nothing was filled
    assert st["valid"] is False


def test_two_tools_declaring_one_pocket_both_sit_in_it():
    """The motivating case (§3.5.6), surfaced rather than resolved. There is no
    tie-break — the app cannot know which cutter is cheaper to move."""
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2),
                       make_tool("EM-0520", default_slot=2)])
    st = _state(lib, [
        _entry(lib, "a_1", "a.nc", "A0", {"T2": {"code": "EM-0512"}}, ["T2"]),
        _entry(lib, "b_1", "b.nc", "A13", {"T2": {"code": "EM-0520"}}, ["T2"]),
    ])
    assert _pocket(st, 2) == ["EM-0512", "EM-0520"]
    assert st["doubled"] == [2]
    assert st["valid"] is False


def test_the_map_is_the_same_every_time_for_the_same_inputs():
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2),
                       make_tool("EM-0520", default_slot=2),
                       make_tool("BB-0250", default_slot=None)])
    entries = [
        _entry(lib, "a_1", "a.nc", "A0", {"T2": {"code": "EM-0512"}}, ["T2"]),
        _entry(lib, "b_1", "b.nc", "A13", {"T4": {"code": "EM-0520"},
                                           "T7": {"code": "BB-0250"}}, ["T4", "T7"]),
    ]
    first = _state(lib, entries)["assignment"]
    for _ in range(5):
        assert _state(lib, entries)["assignment"] == first


# ── drags ─────────────────────────────────────────────────────────────────────

def test_a_drag_overrides_the_declared_slot_for_this_job_only():
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2)])
    entries = [_entry(lib, "p_1", "p.nc", "A0", {"T2": {"code": "EM-0512"}}, ["T2"])]
    st = _state(lib, entries, {"EM-0512": 8})
    assert st["assignment"] == {"EM-0512": 8}
    # The library is untouched, so the next job re-proposes pocket 2. That nag is the
    # point: the declared slot is prescriptive, not descriptive.
    assert lib.get("EM-0512").default_slot == 2
    assert _state(lib, entries)["assignment"] == {"EM-0512": 2}


def test_a_tool_away_from_its_home_is_marked_every_time():
    """Nag every time (operator's call). A badge that goes quiet stops being read."""
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2)])
    st = _state(lib, [_entry(lib, "p_1", "p.nc", "A0", {"T2": {"code": "EM-0512"}}, ["T2"])],
                {"EM-0512": 8})
    assert st["tools"][0]["off_home"] is True
    assert st["tools"][0]["default_slot"] == 2


def test_dropping_onto_an_occupied_pocket_coexists_rather_than_swapping():
    """Swapping two tools is impossible without transiting a doubled pocket, so
    refusing the drop would deadlock the swap."""
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2),
                       make_tool("BN-0500", default_slot=4)])
    entries = [_entry(lib, "p_1", "p.nc", "A0",
                      {"T2": {"code": "EM-0512"},
                       "T1": {"code": "BN-0500"}}, ["T1", "T2"])]
    st = _state(lib, entries, {"BN-0500": 2})
    assert _pocket(st, 2) == ["BN-0500", "EM-0512"]
    assert _pocket(st, 4) == []
    assert st["valid"] is False        # invalid, permitted, and visible


def test_a_drag_beyond_the_changer_lands_back_in_staging():
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2)])
    st = _state(lib, [_entry(lib, "p_1", "p.nc", "A0", {"T2": {"code": "EM-0512"}}, ["T2"])],
                {"EM-0512": 99})
    assert st["staged"] == ["EM-0512"]


# ── the validity gate (§3.4) ──────────────────────────────────────────────────

def test_capacity_is_a_consequence_not_a_fourth_rule():
    """With eight pockets, "every tool in exactly one pocket" and "no pocket holds two"
    make a ninth tool unsatisfiable on their own — it is simply a tool with nowhere to
    go, sitting in "needs a home"."""
    lib = ToolLibrary([make_tool(f"TT-{n:04d}", default_slot=(n if n <= 8 else None))
                       for n in range(1, 10)])
    entries = [
        _entry(lib, f"p{n}_1", f"p{n}.nc", f"A{n}",
               {f"T{n}": {"code": f"TT-{n:04d}"}}, [f"T{n}"])
        for n in range(1, 10)
    ]
    st = _state(lib, entries)
    assert st["staged"] == ["TT-0009"]
    assert st["free"] == []
    assert st["valid"] is False
    assert st["needs_removal"] is True


def test_an_unresolved_tool_blocks_generation_and_cannot_take_a_pocket():
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2)])
    st = _state(lib, [_entry(lib, "p_1", "p.nc", "A0",
                             {"T3": {"description": "CHAMFER MILL D=12.7"}}, ["T3"])])
    assert st["valid"] is False
    assert st["tools"] == []
    assert [u["tool_number"] for u in st["unresolved"]] == ["T3"]


def test_a_valid_map_is_every_tool_in_its_own_pocket():
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2),
                       make_tool("BN-0500", default_slot=4)])
    st = _state(lib, [_entry(lib, "p_1", "p.nc", "A0",
                             {"T2": {"code": "EM-0512"},
                              "T1": {"code": "BN-0500"}}, ["T1", "T2"])])
    assert st["valid"] is True
    assert st["messages"][0]["level"] == "ok"


# ── what the operator has to be able to see (§3.4.1) ──────────────────────────

def test_a_tool_needed_by_three_parts_is_one_entry_naming_all_three():
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2)])
    entries = [
        _entry(lib, f"p{n}_1", f"p{n}.nc", f"A{n}",
               {"T2": {"code": "EM-0512"}}, ["T2"])
        for n in range(3)
    ]
    st = _state(lib, entries)
    tool = st["tools"][0]
    assert tool["part_count"] == 3
    assert [u["filename"] for u in tool["usages"]] == ["p0.nc", "p1.nc", "p2.nc"]
    # Nobody can free a pocket on their own, so nobody is named.
    assert tool["sole_instance_id"] is None
    assert "shares its tools" in st["removal_advice"]


def test_the_sole_user_of_a_tool_is_named_so_the_operator_need_not_scan():
    lib = ToolLibrary([make_tool("EM-0512", default_slot=2),
                       make_tool("BB-0250", default_slot=6)])
    st = _state(lib, [
        _entry(lib, "shared_1", "shared.nc", "A0", {"T2": {"code": "EM-0512"}}, ["T2"]),
        _entry(lib, "only_1", "only.nc", "A13", {"T2": {"code": "EM-0512"},
                                                 "T7": {"code": "BB-0250"}}, ["T2", "T7"]),
    ])
    bowl = next(t for t in st["tools"] if t["code"] == "BB-0250")
    assert bowl["sole_instance_id"] == "only_1"
    assert "only.nc" in st["removal_advice"]


def test_the_message_changes_when_dragging_can_no_longer_help():
    """"3 tools have no home" is a to-do when free pockets exist and a *stop* when the
    changer is full. Operators are not developers; the wording carries the difference."""
    lib = ToolLibrary([make_tool("BB-0250", default_slot=None)])
    roomy = _state(lib, [_entry(lib, "p_1", "p.nc", "A0",
                                {"T7": {"code": "BB-0250"}}, ["T7"])])
    assert "8 free" in roomy["messages"][0]["text"]
    assert roomy["messages"][0]["level"] == "warning"

    cramped = _state(lib, [_entry(lib, "p_1", "p.nc", "A0",
                                  {"T7": {"code": "BB-0250"}}, ["T7"])], capacity=0)
    assert "take a part off the bed" in cramped["messages"][0]["text"]
    assert cramped["messages"][0]["level"] == "error"


def test_the_doubled_pocket_message_stops_saying_drag_when_nothing_is_free():
    lib = ToolLibrary([make_tool("EM-0512", default_slot=1),
                       make_tool("EM-0520", default_slot=1)])
    st = _state(lib, [
        _entry(lib, "a_1", "a.nc", "A0", {"T2": {"code": "EM-0512"}}, ["T2"]),
        _entry(lib, "b_1", "b.nc", "A13", {"T2": {"code": "EM-0520"}}, ["T2"]),
    ], capacity=1)
    assert "every pocket is taken" in st["messages"][0]["text"]
    assert "drag" not in st["messages"][0]["text"]


# ── the routes ────────────────────────────────────────────────────────────────

_CODED_T2 = (
    "( Material Size)\n( X= 100.0, Y= 100.0, Z= 19.05)\n"
    "(T2 = EM-0500 End Mill)\nG43 H2 Z44.4754\nT2 M06\nG01 X50 Y50 Z-0.254\nM30\n"
)

_CODED_TWO_TOOLS = (
    "( Material Size)\n( X= 100.0, Y= 100.0, Z= 19.05)\n"
    "(T1 = BN-0500 Ball Nose)\n(T2 = EM-0500 End Mill)\n"
    "T1 M06\nG43 H1 Z44.4754\nG01 X10 Y10 Z-0.254\nG00 G53 G49 Z0\nM05\n"
    "T2 M06\nG43 H2 Z44.4754\nG01 X50 Y50 Z-0.254\nM30\n"
)


def _seed(tmp_path, monkeypatch, files):
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))


def test_assign_route_is_never_refused_even_onto_an_occupied_pocket(
        client, tmp_path, monkeypatch, isolated_library):
    isolated_library.upsert(make_tool("EM-0500", default_slot=2))
    isolated_library.upsert(make_tool("BN-0500", default_slot=4))
    _seed(tmp_path, monkeypatch, {"p.nc": _CODED_TWO_TOOLS})
    placed = client.post("/api/place", json={"path": "p.nc", "rail": "A", "slot_inches": 39})
    assert placed.status_code == 200

    r = client.post("/api/changer/assign", json={"code": "BN-0500", "pocket": 2})
    assert r.status_code == 200
    st = r.get_json()["changer"]
    assert sorted(_pocket(st, 2)) == ["BN-0500", "EM-0500"]
    assert st["valid"] is False


def test_reset_route_drops_every_drag(client, tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, {"p.nc": _CODED_T2})
    client.post("/api/place", json={"path": "p.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/changer/assign", json={"code": "EM-0500", "pocket": 7})
    assert client.get("/api/changer").get_json()["assignment"] == {"EM-0500": 7}

    client.post("/api/changer/reset", json={})
    assert client.get("/api/changer").get_json()["assignment"] == {"EM-0500": 2}


def test_generate_is_blocked_while_the_map_is_invalid(client, tmp_path, monkeypatch,
                                                      isolated_library):
    isolated_library.upsert(make_tool("EM-0500", default_slot=2))
    isolated_library.upsert(make_tool("SB-0500", default_slot=2))    # contested
    other = _CODED_T2.replace("EM-0500", "SB-0500")
    _seed(tmp_path, monkeypatch, {"a.nc": _CODED_T2, "b.nc": other})
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    client.post("/api/place", json={"path": "a.nc", "rail": "A", "slot_inches": 39})
    client.post("/api/place", json={"path": "b.nc", "rail": "A", "slot_inches": 26})

    blocked = client.post("/api/generate", json={"job_name": "j"})
    assert blocked.status_code == 422
    assert blocked.get_json()["error"] == "changer_map_invalid"

    # One drag is all it takes, and it is not refused.
    client.post("/api/changer/assign", json={"code": "SB-0500", "pocket": 8})
    ok = client.post("/api/generate", json={"job_name": "j"})
    assert ok.status_code == 200
