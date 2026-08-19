"""
The identity library: code extraction, match-on-load, guard (a), the description seal,
and the library's lifecycle rules (spec §3.1, §3.5).

The thing under test throughout is that **no string is ever compared for identity**. A
tool matches because the file carries a code the library knows, or it does not match at
all and the operator decides. `End Mill` never finds anything.
"""
import pytest

import app as app_module
from tests.conftest import make_tool
from gcode_parser import GcodePass, GcodePart, ZValidation, parse_vcarve_text
from tool_library import (
    LibraryTool, ToolLibrary, ToolLibraryError,
    cam_description_of, code_in_file_tool, resolve_part,
)


def _make_part(tools: dict, tool_numbers: list, filename="test.nc") -> GcodePart:
    passes = [GcodePass(pass_index=i, tool_number=tn) for i, tn in enumerate(tool_numbers)]
    return GcodePart(
        filename=filename,
        vcarve_x_span=100.0, vcarve_y_span=100.0,
        material_thickness=19.05,
        tools=tools,
        min_vx=0.0, max_vx=100.0, min_vy=0.0, max_vy=100.0,
        raw_lines=[],
        z_validation=ZValidation(status="ok"),
        passes=passes,
    )


def _lib(*tools):
    return ToolLibrary(list(tools))


# ── where a code comes from ───────────────────────────────────────────────────

def test_fusion_code_field_is_the_code():
    assert code_in_file_tool({"code": "EM-0512"}) == "EM-0512"


def test_vcarve_tool_name_carries_the_code():
    # The tool name is the only field VCarve's post lets reach a file (§3.1).
    assert code_in_file_tool({"description": "RK-004 End Mill {0.5 inches}"}) == "RK-004"


def test_code_is_uppercased_because_the_post_uppercases_comments():
    assert code_in_file_tool({"description": "em-0512 end mill"}) == "EM-0512"


def test_blank_product_field_yields_no_code_but_is_not_the_same_as_absent():
    # `VENDOR=`/`CODE=` empty says the CAM entry needs filling in; a *missing* key
    # says the file predates the comment. Neither matches, but only one is actionable,
    # and `_toolid_fields` keeps them distinguishable.
    assert code_in_file_tool({"code": ""}) is None
    assert code_in_file_tool({}) is None


def test_ordinary_descriptions_yield_no_code():
    # This is the whole safety property: a free-text description can never be mistaken
    # for identity, in either direction.
    for text in ("End Mill {0.5 inch}", "FLAT END MILL D=12.7 CR=0.",
                 "Ball Nose .5 inches Dia", "CHAMFER MILL D=12.7 TAPER=45DEG"):
        assert code_in_file_tool({"description": text}) is None


def test_toolid_code_wins_over_a_token_in_the_description():
    info = {"code": "EM-0520", "description": "EM-0512 End Mill"}
    assert code_in_file_tool(info) == "EM-0520"


def test_cam_description_prefers_tooldesc_over_description():
    # TOOLDESC is what the seal reads; `description` is what _tool_compatibility used to
    # compare, and free text must not move that signal.
    assert cam_description_of({"cam_description": "12 DOWNCUT SPIRAL",
                               "description": "FLAT END MILL D=12.7"}) == "12 DOWNCUT SPIRAL"
    assert cam_description_of({"description": "End Mill {0.5 inch}"}) == "End Mill {0.5 inch}"


# ── match-on-load ─────────────────────────────────────────────────────────────

def test_known_code_matches_and_takes_the_library_diameter():
    lib = _lib(make_tool("EM-0512", diameter=0.5, default_slot=2))
    part = _make_part({"T4": {"code": "EM-0512"}}, ["T4"])
    res = resolve_part(lib, part)
    assert res.bindings["T4"].status == "matched"
    assert res.bindings["T4"].library_code == "EM-0512"
    assert res.diameters_by_tool_number(lib) == {"T4": 0.5}
    assert not res.blocked


def test_unknown_code_is_offered_as_a_new_tool_not_guessed_at():
    lib = _lib(make_tool("EM-0512"))
    part = _make_part({"T4": {"code": "ZZ-9999"}}, ["T4"])
    res = resolve_part(lib, part)
    assert res.bindings["T4"].status == "unknown_code"
    assert res.bindings["T4"].code == "ZZ-9999"
    assert res.blocked


def test_a_file_with_no_code_orphans():
    """The floor, not a failure mode — all 26 library files predate the code."""
    lib = _lib(make_tool("EM-0512"))
    part = _make_part({"T4": {"description": "End Mill {0.5 inches}"}}, ["T4"])
    res = resolve_part(lib, part)
    assert res.bindings["T4"].status == "orphan"
    assert res.blocked


def test_an_orphan_says_whether_the_code_field_was_blank_or_absent():
    """Blank `CODE=` is actionable in CAM; an absent one is not.

    A blank field means the Fusion tool exists with an empty Product ID, so one edit
    there fixes every file that tool is ever used in. An absent field means the file
    carries no identity comment at all — every VCarve file — where a job-scoped bind
    is the whole answer. The resolver tells them apart with this.
    """
    lib = _lib(make_tool("EM-0512"))

    blank = resolve_part(lib, _make_part({"T1": {"code": ""}}, ["T1"]))
    assert blank.bindings["T1"].status == "orphan"
    assert blank.bindings["T1"].code_field == ""

    absent = resolve_part(lib, _make_part({"T1": {"description": "End Mill"}}, ["T1"]))
    assert absent.bindings["T1"].status == "orphan"
    assert absent.bindings["T1"].code_field is None


def test_an_orphan_binds_to_a_library_tool_for_this_run():
    lib = _lib(make_tool("EM-0512", diameter=0.5))
    part = _make_part({"T4": {"description": "End Mill {0.5 inches}"}}, ["T4"])
    res = resolve_part(lib, part, {"T4": "EM-0512"})
    assert res.bindings["T4"].status == "bound"
    assert res.diameters_by_tool_number(lib) == {"T4": 0.5}
    assert not res.blocked


def test_two_files_sharing_one_cutter_under_different_tool_numbers_both_resolve():
    """This is the feature working, not a collision — guard (a) is per file only."""
    lib = _lib(make_tool("EM-0512"))
    a = resolve_part(lib, _make_part({"T2": {"code": "EM-0512"}}, ["T2"]), None)
    b = resolve_part(lib, _make_part({"T4": {"code": "EM-0512"}}, ["T4"]), None)
    assert a.bindings["T2"].library_code == b.bindings["T4"].library_code == "EM-0512"
    assert not a.blocked and not b.blocked


def test_a_bind_never_overrides_a_code_the_file_already_carries():
    lib = _lib(make_tool("EM-0512"), make_tool("EM-0520"))
    part = _make_part({"T4": {"code": "EM-0512"}}, ["T4"])
    res = resolve_part(lib, part, {"T4": "EM-0520"})
    assert res.bindings["T4"].library_code == "EM-0512"


# ── guard (a): resolution is injective within one file ────────────────────────

def test_one_code_on_two_tool_numbers_in_one_file_is_a_hard_stop():
    """CAM already asserted they differ by giving them different pockets, so this is
    disambiguation work, never a merge (§3.5.3)."""
    lib = _lib(make_tool("EM-0512"))
    part = _make_part(
        {"T2": {"code": "EM-0512"}, "T4": {"code": "EM-0512"}},
        ["T2", "T4"],
    )
    res = resolve_part(lib, part)
    assert res.duplicate_codes == [("EM-0512", ["T2", "T4"])]
    assert res.blocked


def test_one_tool_used_across_several_passes_is_not_a_duplicate():
    lib = _lib(make_tool("EM-0512"))
    part = _make_part({"T2": {"code": "EM-0512"}}, ["T2", "T2", "T2"])
    res = resolve_part(lib, part)
    assert res.duplicate_codes == []
    assert not res.blocked


# ── the description seal ──────────────────────────────────────────────────────

def test_first_description_a_code_posts_is_learned_not_prompted():
    """An empty set has nothing to disagree with — the seal is a change detector."""
    lib = _lib(make_tool("EM-0512"))
    part = _make_part({"T2": {"code": "EM-0512",
                              "cam_description": "12 DOWNCUT SPIRAL"}}, ["T2"])
    res = resolve_part(lib, part)
    assert res.learned == [("EM-0512", "12 DOWNCUT SPIRAL")]
    assert res.seal_prompts == []
    assert not res.blocked


def test_a_known_description_passes_silently():
    lib = _lib(make_tool("EM-0512", cam_descriptions=["12 DOWNCUT SPIRAL"]))
    part = _make_part({"T2": {"code": "EM-0512",
                              "cam_description": "12 DOWNCUT SPIRAL"}}, ["T2"])
    res = resolve_part(lib, part)
    assert res.seal_prompts == []
    assert not res.blocked


def test_a_new_description_on_a_known_code_blocks():
    """The only cross-file detector of one code on two physical cutters."""
    lib = _lib(make_tool("EM-0512", cam_descriptions=["12 DOWNCUT SPIRAL"]))
    part = _make_part({"T2": {"code": "EM-0512",
                              "cam_description": "12 COMPRESSION"}}, ["T2"])
    res = resolve_part(lib, part)
    assert len(res.seal_prompts) == 1
    prompt = res.seal_prompts[0]
    # Both strings have to reach the operator — reading them is the whole check.
    assert prompt.known == ["12 DOWNCUT SPIRAL"]
    assert prompt.posted == "12 COMPRESSION"
    assert res.blocked


def test_the_seal_holds_a_set_so_a_rename_costs_one_prompt_not_a_loop():
    """Replace-on-confirm thrashes forever after a rename: new files carry the new
    string, old files still carry the old one, and the prompt alternates. That trains
    click-through, which destroys the check."""
    lib = _lib(make_tool("EM-0512", cam_descriptions=["12 DOWNCUT SPIRAL"]))
    lib.learn_description("EM-0512", "12 AMANA 46170-K DOWNCUT")

    old = resolve_part(lib, _make_part(
        {"T2": {"code": "EM-0512", "cam_description": "12 DOWNCUT SPIRAL"}}, ["T2"]))
    new = resolve_part(lib, _make_part(
        {"T2": {"code": "EM-0512", "cam_description": "12 AMANA 46170-K DOWNCUT"}}, ["T2"]))
    assert old.seal_prompts == [] and new.seal_prompts == []


def test_the_seal_does_not_learn_from_a_manual_bind():
    """A bind identifies a file that carries no code, so its description says nothing
    about what that code posts. Learning it there would let an unrelated string silently
    validate a later file."""
    lib = _lib(make_tool("EM-0512"))
    part = _make_part({"T4": {"description": "Some VCarve Name"}}, ["T4"])
    res = resolve_part(lib, part, {"T4": "EM-0512"})
    assert res.learned == []
    assert lib.get("EM-0512").cam_descriptions == []


# ── library lifecycle ─────────────────────────────────────────────────────────

def test_duplicate_default_slots_are_permitted():
    """The declared library puts two tools in slot 2 and two in slot 4 deliberately —
    that collision is the motivating case, and refusing it would make the case
    undeclarable (§3.5.6)."""
    lib = ToolLibrary()
    lib.upsert(make_tool("EM-0512", default_slot=2))
    lib.upsert(make_tool("EM-0520", default_slot=2))
    assert lib.get("EM-0512").default_slot == lib.get("EM-0520").default_slot == 2


def test_a_tool_with_no_default_slot_is_legitimate():
    lib = ToolLibrary()
    lib.upsert(make_tool("BB-0250", default_slot=None))
    assert lib.get("BB-0250").default_slot is None


def test_upsert_refuses_a_missing_or_zero_diameter():
    lib = ToolLibrary()
    with pytest.raises(ToolLibraryError):
        lib.upsert(make_tool("EM-0512", diameter=0))


def test_upsert_refuses_an_unknown_flute_direction():
    lib = ToolLibrary()
    with pytest.raises(ToolLibraryError):
        lib.upsert(make_tool("EM-0512", flute="sideways"))


def test_merge_folds_descriptions_and_drops_the_losers_code():
    """The loser's code is NOT kept as a second key — that would reintroduce
    many-keys-to-one-tool (§3.5.4)."""
    lib = _lib(
        make_tool("EM-0512", cam_descriptions=["A"]),
        make_tool("EM-9999", cam_descriptions=["B"]),
    )
    survivor = lib.merge("EM-0512", "EM-9999")
    assert survivor.cam_descriptions == ["A", "B"]
    assert "EM-9999" not in lib
    assert lib.codes == ["EM-0512"]


def test_round_trips_through_json(tmp_path):
    lib = _lib(make_tool("EM-0512", cam_descriptions=["A"], default_slot=2,
                         cutting_length=1.25, vendor="Amana"))
    path = tmp_path / "tool_library.json"
    lib.save(path)
    again = ToolLibrary.load(path)
    assert again.get("EM-0512").to_dict() == lib.get("EM-0512").to_dict()


def test_flute_direction_accepts_the_operators_words():
    assert LibraryTool.from_dict({
        "code": "X-1", "name": "x", "diameter_inches": 1,
        "geometry_class": "Flat End Mill", "flute_direction": "Downcut",
    }).flute_direction == "down"


# ── the library is the sole diameter authority (§3.5.2) ───────────────────────

def test_declared_diameter_beats_the_posted_header():
    """`.25 Bowl Bit` is declared 0.75 and posts a nominal 0.25. The declaration governs
    — under-declaring puts the cutting edge somewhere the check called clear."""
    lib = _lib(make_tool("BB-0250", diameter=0.75))
    part = _make_part({"T7": {"code": "BB-0250", "diameter_inches": 0.25}}, ["T7"])
    res = resolve_part(lib, part)
    assert res.diameters_by_tool_number(lib) == {"T7": 0.75}
    assert res.bindings["T7"].posted_diameter_inches == pytest.approx(0.25)


def test_a_code_is_never_read_as_a_bare_decimal_diameter():
    """The retired fallback would have read `RK-004` as a 0.04" cutter — a 25x
    under-inflation of the X envelope, which is the crash direction (§3.5.2)."""
    part = parse_vcarve_text(
        "( Material Size)\n( X= 100.0, Y= 100.0, Z= 19.05)\n"
        "(T4 = RK-004 End Mill)\nT4 M06\nG01 X10 Y10 Z-1\nM30\n")
    assert part.tools["T4"]["diameter_inches"] is None


# ── /api/place enforces resolution before the bed (§3.5.3) ───────────────────

_UNCODED = (
    "( Material Size)\n( X= 100.0, Y= 100.0, Z= 19.05)\n"
    "(T4 = Table Stiff)\nG43 H4 Z44.4754\nT4 M06\nG01 X50 Y50 Z-0.254\nM30\n"
)
_CODED = (
    "( Material Size)\n( X= 100.0, Y= 100.0, Z= 19.05)\n"
    "(T4 = EM-0500 End Mill)\nG43 H4 Z44.4754\nT4 M06\nG01 X50 Y50 Z-0.254\nM30\n"
)


def _seed(tmp_path, monkeypatch, name, content):
    (tmp_path / name).write_text(content)
    monkeypatch.setitem(app_module.config, "library_path", str(tmp_path))


def test_place_refuses_an_unresolved_tool(client, tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "part.nc", _UNCODED)
    r = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    assert r.status_code == 422
    body = r.get_json()
    assert body["error"] == "unresolved_tools"
    assert [t["tool_number"] for t in body["tools"]] == ["T4"]
    assert body["tools"][0]["status"] == "orphan"


def test_place_succeeds_once_the_orphan_is_bound(client, tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "part.nc", _UNCODED)
    client.post("/api/load-file", json={"path": "part.nc"})

    bind = client.post("/api/bind-tool",
                       json={"path": "part.nc", "tool_number": "T4", "code": "EM-0500"})
    assert bind.status_code == 200

    r = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_place_succeeds_straight_away_when_the_file_carries_a_code(client, tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "part.nc", _CODED)
    r = client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_bind_to_an_unknown_code_is_refused(client, tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "part.nc", _UNCODED)
    r = client.post("/api/bind-tool",
                    json={"path": "part.nc", "tool_number": "T4", "code": "NOPE-1"})
    assert r.status_code == 404


def test_placed_part_carries_the_declared_diameter_not_the_parsed_one(
        client, tmp_path, monkeypatch, isolated_library):
    isolated_library.upsert(make_tool("EM-0500", diameter=1.75, default_slot=2))
    _seed(tmp_path, monkeypatch, "part.nc", _CODED)
    client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    placed = next(iter(app_module._placements.values()))
    assert placed.tool_diameters == {"T4": 1.75}


# ── library routes ────────────────────────────────────────────────────────────

def test_delete_in_use_is_refused_and_names_the_parts(client, tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "part.nc", _CODED)
    client.post("/api/place", json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    r = client.delete("/api/tool-library/EM-0500")
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == "tool_in_use"
    assert "part.nc" in body["message"]


def test_editing_diameter_deletes_a_placement_that_now_collides(
        client, tmp_path, monkeypatch, isolated_library):
    """The only path by which an already-validated placement becomes wrong
    retroactively. The edit exists *because* the old data was wrong, so the placement
    derived from it is invalid rather than merely suspect (§3.5.4)."""
    _seed(tmp_path, monkeypatch, "part.nc", _CODED)
    placed = client.post("/api/place",
                         json={"path": "part.nc", "rail": "A", "slot_inches": 39})
    assert placed.status_code == 200

    # 40" across the bed cannot fit between the rail datum and the hard stop.
    r = client.post("/api/tool-library", json={
        "code": "EM-0500", "name": '1/2" End Mill', "diameter_inches": 40.0,
        "geometry_class": "Flat End Mill", "flute_direction": "down", "default_slot": 2,
    })
    assert r.status_code == 200
    assert [x["filename"] for x in r.get_json()["removed_placements"]] == ["part.nc"]
    assert app_module._placements == {}


def test_seal_rename_records_the_string_and_different_tool_refuses(client, isolated_library):
    isolated_library.upsert(make_tool("EM-0500", cam_descriptions=["OLD NAME"]))

    refused = client.post("/api/tool-library/seal", json={
        "library_code": "EM-0500", "posted": "12 COMPRESSION", "answer": "different"})
    assert refused.status_code == 409
    assert "EM-0500" in refused.get_json()["message"]
    assert isolated_library.get("EM-0500").cam_descriptions == ["OLD NAME"]

    ok = client.post("/api/tool-library/seal", json={
        "library_code": "EM-0500", "posted": "NEW NAME", "answer": "rename"})
    assert ok.status_code == 200
    assert isolated_library.get("EM-0500").cam_descriptions == ["OLD NAME", "NEW NAME"]


def test_library_route_refuses_a_default_slot_outside_the_changer(client):
    r = client.post("/api/tool-library", json={
        "code": "XX-0001", "name": "x", "diameter_inches": 0.5,
        "geometry_class": "Flat End Mill", "flute_direction": "down", "default_slot": 99,
    })
    assert r.status_code == 400


def test_library_route_permits_two_tools_in_one_default_slot(client, isolated_library):
    for code in ("XX-0001", "XX-0002"):
        r = client.post("/api/tool-library", json={
            "code": code, "name": code, "diameter_inches": 0.5,
            "geometry_class": "Flat End Mill", "flute_direction": "down",
            "default_slot": 5,
        })
        assert r.status_code == 200
    assert isolated_library.get("XX-0001").default_slot == 5
    assert isolated_library.get("XX-0002").default_slot == 5


def test_editing_a_tool_does_not_let_the_form_rewrite_the_seal(client, isolated_library):
    isolated_library.upsert(make_tool("EM-0500", cam_descriptions=["KNOWN"]))
    client.post("/api/tool-library", json={
        "code": "EM-0500", "name": "renamed", "diameter_inches": 0.5,
        "geometry_class": "Flat End Mill", "flute_direction": "down",
        "cam_descriptions": ["SOMETHING ELSE"],
    })
    assert isolated_library.get("EM-0500").cam_descriptions == ["KNOWN"]
