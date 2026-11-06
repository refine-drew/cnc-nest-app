"""
The no-geometry-change guarantee and its proving test (issue #12, spec §6.3).

The operator's hard constraint: *"It is of utmost importance that no machining geometry
be affected by the code, just tool position assignments."*

That is structurally achievable — a pocket reassignment rewrites only the `T# M06` line
and the matching `G43 H#` line — so it is **enforced by test rather than asserted in a
comment**. Generate the master G-code twice from identical placements, once with the
identity pocket map and once remapped, and assert the two outputs are token-for-token
identical **except at `T#` and `H#`**. Any accidental geometry change becomes a test
failure by construction rather than something a reviewer has to notice.

Two things make the *whole-file* form of that assertion available, rather than a weaker
per-block comparison:

- Blocks are ordered by **identity**, not by the `T#` string (§4.2). Under the old
  string sort, `"T10"` preceded `"T2"`, so a remap could reorder blocks — and an
  accidental geometry change could hide inside a legitimate reorder.
- `H` follows the pocket by construction, so the exception set is exactly two words.

Per §6.1 the test asserts **two** things about that exception set, not one: that `T#`
moved as the pocket map says, *and* that every `H` still equals its preceding `T`. With
posture 2's self-correction unproven on the machine, `H`-follows-pocket carries the
safety load alone, so it is covered as tightly as the geometry words.

The same guarantee is extended to the cycle-time estimate and pinned against golden
masters at the bottom of this file — see the section header there.
"""
import os
import re
from pathlib import Path

import pytest

import app as app_module
import runtime_estimator
from collision import PlacedPart
from gcode_generator import (
    RUNTIME_COMMENT_PREFIX, IdentityMap, block_tool_sequence, generate_master_gcode,
)
from gcode_parser import parse_vcarve_text
from tool_library import LibraryTool, ToolLibrary

SETTINGS = {
    "job_name": "remap_proof",
    "job_safe_z": {"value": 44.4754, "driven_by": "a.nc"},
    "advanced": {
        "bed_x_mm": 1606.4992,
        "bed_y_mm": 3098.0126,
        "safe_z_clearance_mm": 6.35,
        "park_x": 0.0,
        "park_y": 3048.0,
        "tool_capacity": 8,
        "slots": [0, 13, 19.5, 26, 39, 52, 58.5, 65, 78, 91, 97.5, 104, 117],
    },
}


def _nc(passes, thickness=19.05):
    """A minimal file whose body contains every word class the guarantee protects."""
    header = f"( Material Size)\n( X= 200.0, Y= 100.0, Z= {thickness})\n"
    for tn, name in passes:
        header += f"({tn} = {name} End Mill {{0.5 inches}})\n"
    body = ""
    for tn, name in passes:
        body += (
            f"{tn} M06\n(Tool: {name} {{0.5 inches}})\n"
            f"G43 H{tn[1:]} Z44.4754\nM03 S18000\n"
            "G00 X10 Y10\n"
            "G01 X50 Y50 Z-0.254 F2540\n"
            "G02 X70 Y70 I10 J0\n"
            "G18 G03 X90 Z-2 I5 K0\n"
            "G17 G01 X100 Y50\n"
            "G53 G49 Z0\nM05\n"
        )
    return header + body + "M30\n%\n"


# Two parts that disagree about tool order, so the pass-index walk revisits a tool and
# the file contains four blocks for two cutters — the case where a reordering bug would
# actually show up.
_A = _nc([("T2", "EM-0512"), ("T4", "EM-0750")])
_B = _nc([("T4", "EM-0750"), ("T2", "EM-0512")])


def _placements():
    return [
        PlacedPart(part=parse_vcarve_text(_A, filename="a.nc"),
                   rail="A", slot_inches=39, instance_id="a_1"),
        PlacedPart(part=parse_vcarve_text(_B, filename="b.nc"),
                   rail="B", slot_inches=26, instance_id="b_1"),
    ]


# `a.nc` and `b.nc` both call the ½" mill T2 and the ¾" mill T4.
_CODES = {
    "a_1": {"T2": "EM-0512", "T4": "EM-0750"},
    "b_1": {"T2": "EM-0512", "T4": "EM-0750"},
}

# The identity map: every cutter in the pocket its own T# already names.
IDENTITY = IdentityMap(codes=_CODES, pockets={"EM-0512": 2, "EM-0750": 4})
# The same job with both cutters moved — and deliberately *crossed*, so a naive
# rewrite that mapped T2→T4 in one direction only would corrupt the file.
REMAPPED = IdentityMap(codes=_CODES, pockets={"EM-0512": 4, "EM-0750": 2})
# A move into double digits, which is where the old `sorted()` over `T#` strings put
# "T10" before "T2" and reordered the blocks.
TO_DOUBLE_DIGITS = IdentityMap(codes=_CODES, pockets={"EM-0512": 10, "EM-0750": 3})


_TOOL_WORD = re.compile(r"^T\d+$")
_H_WORD = re.compile(r"^H\d+$")


def _tokens(gcode: str):
    return [t for line in gcode.splitlines() for t in line.split()]


def _strip_header_tool_list(gcode: str) -> str:
    """Drop the two header comments that name the tools.

    They are *documentation of* the mapping, so they are expected to differ — the
    guarantee is about the program the machine runs, and a comment commands no motion.
    """
    return "\n".join(
        line for line in gcode.splitlines()
        if not line.startswith("(Instances:") and not line.startswith("( ---- T")
    )


def _diff_positions(a: str, b: str):
    ta, tb = _tokens(_strip_header_tool_list(a)), _tokens(_strip_header_tool_list(b))
    assert len(ta) == len(tb), (
        "the two files have different token counts, so a remap changed more than "
        "two words per block")
    return [(x, y) for x, y in zip(ta, tb) if x != y]


# ── the guarantee ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("remap", [REMAPPED, TO_DOUBLE_DIGITS])
def test_a_remap_changes_only_the_tool_and_offset_words(remap):
    base = generate_master_gcode(_placements(), SETTINGS, IDENTITY)
    moved = generate_master_gcode(_placements(), SETTINGS, remap)

    differences = _diff_positions(base, moved)
    assert differences, "the remap changed nothing at all, so this proves nothing"
    for before, after in differences:
        assert (_TOOL_WORD.match(before) and _TOOL_WORD.match(after)) or \
               (_H_WORD.match(before) and _H_WORD.match(after)), (
            f"a remap altered {before!r} → {after!r}, which is not a T or H word")


@pytest.mark.parametrize("remap", [IDENTITY, REMAPPED, TO_DOUBLE_DIGITS])
def test_every_h_equals_its_preceding_t(remap):
    """`H` must follow the pocket (§4.1). Syntec indexes the touch-off register by `H`,
    and the shop touches a tool off as it is loaded, so the offset table is
    pocket-indexed: a remap that moved `T` without moving `H` would apply the wrong
    tool-length offset. Wrong Z — crash or air-cut."""
    gcode = generate_master_gcode(_placements(), SETTINGS, remap)
    current = None
    seen = 0
    for line in gcode.splitlines():
        m = re.search(r"\bT(\d+)\s+M06\b", line)
        if m:
            current = m.group(1)
        h = re.search(r"\bG43\s+H(\d+)\b", line)
        if h:
            assert current is not None, "a G43 arrived before any tool change"
            assert h.group(1) == current, (
                f"G43 H{h.group(1)} under T{current} — the offset does not follow the pocket")
            seen += 1
    assert seen == 4, "expected one G43 per emitted block"


@pytest.mark.parametrize("remap", [IDENTITY, REMAPPED, TO_DOUBLE_DIGITS])
def test_the_tool_words_are_exactly_what_the_pocket_map_says(remap):
    gcode = generate_master_gcode(_placements(), SETTINGS, remap)
    emitted = re.findall(r"\bT(\d+)\s+M06\b", gcode)
    expected = [str(remap.pockets["EM-0512"]), str(remap.pockets["EM-0750"])]
    # Four blocks: the two parts disagree about tool order, so each cutter is
    # changed back to. Distinct-minus-one would have said "one change" (issue #7).
    assert emitted == expected + expected


@pytest.mark.parametrize("remap", [IDENTITY, REMAPPED, TO_DOUBLE_DIGITS])
def test_the_tool_change_count_cannot_move_because_a_pocket_moved(remap):
    """Blocks are ordered by identity, so the estimate is invariant under a remap."""
    assert len(block_tool_sequence(_placements(), remap)) == 4


def test_block_order_is_unchanged_by_a_move_into_double_digits():
    """The specific regression §4.2 names: `sorted()` over a `T#` string puts "T10"
    before "T2", so renumbering would reorder blocks within a pass index for a job whose
    geometry did not change at all."""
    base = generate_master_gcode(_placements(), SETTINGS, IDENTITY)
    moved = generate_master_gcode(_placements(), SETTINGS, TO_DOUBLE_DIGITS)

    def bodies(gcode):
        # G43 is excluded because H legitimately moves with the pocket — that word is
        # the exception set, and the token test above already pins it.
        return [line for line in gcode.splitlines()
                if re.search(r"\b[XYZIJKF]-?\d", line) and "G43" not in line]

    assert bodies(base) == bodies(moved)


def test_two_files_calling_one_cutter_by_different_numbers_merge_into_one_block():
    """§1's defect, inverted: this is the merge the feature exists to make possible."""
    a = PlacedPart(part=parse_vcarve_text(_nc([("T2", "EM-0512")]), filename="a.nc"),
                   rail="A", slot_inches=39, instance_id="a_1")
    b = PlacedPart(part=parse_vcarve_text(_nc([("T7", "EM-0512")]), filename="b.nc"),
                   rail="A", slot_inches=26, instance_id="b_1")
    identity = IdentityMap(
        codes={"a_1": {"T2": "EM-0512"}, "b_1": {"T7": "EM-0512"}},
        pockets={"EM-0512": 5},
    )
    gcode = generate_master_gcode([a, b], SETTINGS, identity)
    assert re.findall(r"\bT(\d+)\s+M06\b", gcode) == ["5"]


def test_two_files_calling_different_cutters_by_one_number_do_not_merge():
    """The live defect of §1.1: `_build_blocks` used to group on the raw `T#`, so two
    files that both said `T4` were cut with whatever was physically in pocket 4."""
    a = PlacedPart(part=parse_vcarve_text(_nc([("T4", "EM-0512")]), filename="a.nc"),
                   rail="A", slot_inches=39, instance_id="a_1")
    b = PlacedPart(part=parse_vcarve_text(_nc([("T4", "EM-0750")]), filename="b.nc"),
                   rail="A", slot_inches=26, instance_id="b_1")
    identity = IdentityMap(
        codes={"a_1": {"T4": "EM-0512"}, "b_1": {"T4": "EM-0750"}},
        pockets={"EM-0512": 2, "EM-0750": 6},
    )
    gcode = generate_master_gcode([a, b], SETTINGS, identity)
    assert re.findall(r"\bT(\d+)\s+M06\b", gcode) == ["2", "6"]


def test_capacity_counts_physical_cutters_not_tool_numbers():
    """Two files' `T4` resolving to one cutter is a one-tool job, not a two-tool one."""
    parts = [
        PlacedPart(part=parse_vcarve_text(_nc([("T4", "EM-0512")]), filename=f"{i}.nc"),
                   rail="A", slot_inches=slot, instance_id=f"i{i}")
        for i, slot in enumerate([0, 13, 26, 39, 52, 65, 78, 91, 104])
    ]
    identity = IdentityMap(
        codes={p.instance_id: {"T4": "EM-0512"} for p in parts},
        pockets={"EM-0512": 1},
    )
    # Nine parts, nine `T4`s, one cutter — and the capacity backstop stays quiet.
    gcode = generate_master_gcode(parts, SETTINGS, identity)
    assert re.findall(r"\bT(\d+)\s+M06\b", gcode) == ["1"]


# ── the cycle-time estimate cannot touch the program ──────────────────────────
#
# Operator, 2026-11-06: a toolpath geometry change "absolutely 100% cannot happen". The
# estimator is about to be replaced (the Syntec-parameter model in `ss2_motion`), so this
# is proven here rather than argued in review. Two guards, over real Fusion-posted parts
# run through the real `/api/place` → `/api/generate` path:
#
# - **Golden masters.** Each job's emitted file is pinned byte-for-byte in
#   `fixtures/golden/`, except the `(Est cycle time: …)` and `(Date: …)` header comments.
#   That is what lets the *old* estimator's output be compared with the new one's after
#   the old one is gone, and it catches a geometry change from any other source too.
# - **Independence.** The same job generated under a stand-in estimator that returns
#   absurd numbers must differ from the real one only in those lines. That proves no
#   decision in the generator reads an estimate, whatever the estimate says.
#
# **Re-blessing a golden master is a G-code change and is never routine.** Set
# `CNC_NEST_BLESS_GOLDEN=1` to rewrite them; the test then fails on purpose, so a bless
# can never pass silently. Review `git diff tests/fixtures/golden/` line by line and get
# the operator's sign-off before committing it.

_PARTS = Path(__file__).resolve().parent / "fixtures" / "parts"
_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden"
_BLESS = os.environ.get("CNC_NEST_BLESS_GOLDEN") == "1"


def _golden_tool(code, diameter, slot, description, geometry="Flat End Mill", flute="down"):
    return LibraryTool(
        code=code, name=code, diameter_inches=diameter, geometry_class=geometry,
        flute_direction=flute, cutting_length_in=None, cam_descriptions=[description],
        default_slot=slot, vendor="",
    )


# Frozen here rather than read from `tool_library.json`: a declared slot is a `T#` in the
# output, so a seed edit would move the golden masters for a reason unrelated to the code.
# Codes and descriptions are what the fixture parts post; `EM-2510` is not in the seed.
_GOLDEN_TOOLS = [
    _golden_tool("BN-0500", 0.5, 1, "12 BALL NOSE - UPCUT", "Ball Nose", "up"),
    _golden_tool("EM-0512", 0.5, 2, "12 FLAT ENDMILL"),
    _golden_tool("BB-0250", 0.75, 4, "14 BOWL BIT", "Bowl Bit", "straight"),
    _golden_tool("CB-0375", 0.375, 4,
                 "SPETOOL W02002 SC COMPRESSION SPIRAL 38 DIA X 12 SHANK X 1-14 CUTT",
                 flute="up"),
    _golden_tool("RO-0125", 0.3, 5, "18 ROUNDOVER", "Roundover", "straight"),
    _golden_tool("EM-2510", 0.25, 6, "14 END MILL 1.0 LG"),
]

# Between them: both rails, G18 and G19 arcs on each (the plane swap), Fusion's
# trailing-dot words, a tool the pass-index walk returns to, a pocket drag, and both
# end-of-job rules (the config default, and an operator pick).
_GOLDEN_JOBS = {
    "t24": {
        "placements": [("T24.nc", "A", 0), ("T24G.nc", "A", 26), ("T24H.nc", "B", 0)],
        "drags": [],
        "end_pocket": None,
    },
    "mixed": {
        "placements": [("18G.nc", "A", 0), ("12H.nc", "A", 26),
                       ("Back.nc", "B", 0), ("12H.nc", "B", 39)],
        "drags": [("EM-2510", 7)],
        "end_pocket": 7,
    },
}


@pytest.fixture()
def golden_shop(client, monkeypatch, tmp_path):
    library = ToolLibrary(list(_GOLDEN_TOOLS))
    monkeypatch.setattr(library, "save", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "tool_library", library)
    monkeypatch.setitem(app_module.config, "library_path", str(_PARTS))
    monkeypatch.setitem(app_module.config, "output_path", str(tmp_path))
    return client


def _generate(client, name):
    """Lay the job out the way an operator would, generate it, return the file."""
    job = _GOLDEN_JOBS[name]
    for path, rail, slot in job["placements"]:
        r = client.post("/api/place", json={"path": path, "rail": rail, "slot_inches": slot})
        assert r.status_code == 200, r.get_json()
    for code, pocket in job["drags"]:
        assert client.post("/api/changer/assign",
                           json={"code": code, "pocket": pocket}).status_code == 200
    if job["end_pocket"] is not None:
        assert client.post("/api/end-pocket",
                           json={"pocket": job["end_pocket"]}).status_code == 200
    r = client.post("/api/generate", json={"job_name": f"golden-{name}"})
    assert r.status_code == 200, r.get_json()
    return Path(r.get_json()["nc_path"]).read_bytes().decode("utf-8")


# The two header comments allowed to differ: the estimate, and when the file was written.
# Neither commands motion, and each must appear exactly once so a stray copy cannot hide.
_UNPINNED = (f"({RUNTIME_COMMENT_PREFIX}", "(Date:")


def _without_unpinned(gcode):
    """`(line number, line)` for every pinned line, numbered as in the file."""
    lines = gcode.splitlines()
    for prefix in _UNPINNED:
        found = [ln for ln in lines if ln.startswith(prefix)]
        assert len(found) == 1, f"expected one {prefix!r} line, found {found}"
    return [(n, ln) for n, ln in enumerate(lines, start=1) if not ln.startswith(_UNPINNED)]


def _assert_same_program(expected, actual, what):
    for (n, a), (_, b) in zip(expected, actual):
        assert a == b, f"{what}: first difference at line {n}: {a!r} became {b!r}"
    assert len(expected) == len(actual), (
        f"{what}: {len(expected)} lines became {len(actual)}")


@pytest.mark.parametrize("name", sorted(_GOLDEN_JOBS))
def test_the_program_matches_its_golden_master(golden_shop, name):
    gcode = _generate(golden_shop, name)
    golden = _GOLDEN / f"{name}.nc"
    if _BLESS:
        golden.parent.mkdir(exist_ok=True)
        golden.write_bytes(gcode.encode("utf-8"))
        pytest.fail(f"Re-blessed {golden.name}. Review `git diff` on it line by line "
                    "before committing; this failure is deliberate.")
    assert golden.exists(), f"{golden} is missing — a deleted master proves nothing"
    _assert_same_program(_without_unpinned(golden.read_bytes().decode("utf-8")),
                         _without_unpinned(gcode),
                         f"{name} no longer matches its golden master")


@pytest.mark.parametrize("name", sorted(_GOLDEN_JOBS))
def test_the_cycle_time_estimate_cannot_change_the_program(golden_shop, monkeypatch, name):
    real = _generate(golden_shop, name)
    assert golden_shop.delete("/api/placements").status_code == 200

    def absurd(*args, **kwargs):
        return {"seconds": 987654.0, "cutting": 1.0, "rapid": 2.0, "tool_changes": 3.0,
                "tool_change_count": 99, "spindle": 4.0, "spindle_start_count": 99}

    # Every name an estimate is reached by: the job total in `api_generate`, and the
    # per-part figure at parse time and on a config save.
    for module in (app_module, runtime_estimator):
        monkeypatch.setattr(module, "estimate_lines_runtime", absurd)
        monkeypatch.setattr(module, "estimate_passes_runtime", absurd)
    app_module._loaded.clear()
    stand_in = _generate(golden_shop, name)

    assert real != stand_in, "the stand-in estimate never reached the file"
    _assert_same_program(_without_unpinned(real), _without_unpinned(stand_in),
                         f"{name} changed when only the estimate did")
