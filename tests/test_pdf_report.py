"""Smoke tests for the operator layout PDF."""
import pytest

pytest.importorskip("reportlab")

from pdf_report import generate_layout_pdf, palette_color


GEOM = {
    "bed_x_mm": 1524.0,
    "bed_y_mm": 3048.0,
    "rail_width_mm": 82.55,
    "slots": [0, 13, 19.5, 26, 39, 52, 65, 78, 91, 104, 117],
    "edge_margin_in": 1.5,
}

META = {
    "job_name": "smoke_job",
    "date": "2026-05-30 12:00",
    "bed_x_mm": 1524.0,
    "bed_y_mm": 3048.0,
    "safe_z": {"value": 49.0, "driven_by": "panel.nc"},
    "block_sequence": ["T1", "T2", "T1"],
    "park_tool": "T2",
    "tool_changes": 4,
    "parts_count": 2,
    "runtime": "12m 30s",
}


def _parts():
    # One A-rail and one B-rail part with a couple of cutting segments each.
    return [
        {
            "index": 1, "label": "A39", "name": "bracket.nc", "rail": "A",
            "slot_inches": 39.0, "size_mm": (300.0, 200.0),
            "blank": (82.55, 282.55, 2730.0, 3030.0),
            "segments": [
                {"x1": 100, "y1": 2800, "x2": 250, "y2": 2800, "cutting": True},
                {"x1": 250, "y1": 2800, "x2": 250, "y2": 2950, "cutting": False},
            ],
            "tools": [{"tool_number": "T1", "description": "End Mill",
                       "diameter_inches": 0.25}],
            "color": palette_color(0),
        },
        {
            "index": 2, "label": "B78", "name": "panel.mmg", "rail": "B",
            "slot_inches": 78.0, "size_mm": (400.0, 250.0),
            "blank": (1191.45, 1441.45, 1050.0, 1450.0),
            "segments": [
                {"x1": 1250, "y1": 1100, "x2": 1400, "y2": 1100, "cutting": True},
            ],
            "tools": [{"tool_number": "T2", "description": "Compression",
                       "diameter_inches": 0.5}],
            "color": palette_color(1),
        },
    ]


def test_generate_layout_pdf_writes_valid_pdf(tmp_path):
    out = tmp_path / "layout.pdf"
    generate_layout_pdf(out, META, _parts(), GEOM)
    assert out.is_file()
    data = out.read_bytes()
    assert data.startswith(b"%PDF-")
    assert len(data) > 1000


def test_generate_layout_pdf_handles_many_parts(tmp_path):
    # Enough rows to force table pagination — should still produce a valid PDF.
    base = _parts()[0]
    parts = []
    for i in range(40):
        p = dict(base)
        p["index"] = i + 1
        p["name"] = f"part_{i}.nc"
        parts.append(p)
    out = tmp_path / "many.pdf"
    generate_layout_pdf(out, {**META, "parts_count": len(parts)}, parts, GEOM)
    assert out.read_bytes().startswith(b"%PDF-")


def test_palette_color_cycles():
    assert palette_color(0) == palette_color(10)
    assert palette_color(0) != palette_color(1)


# ── stock thickness on the setup sheet (issue #28) ───────────────────────────

def test_stock_label_snaps_nominally_identical_stock_together():
    """19.05, 19.0 and a planed 18.9 are one board, not three materials.

    Binning on the raw millimetre figure gives two colours for one stock, which reads
    as a mixed rail that is actually fine — the gut check failing in the worst
    direction.
    """
    from pdf_report import stock_label
    assert stock_label(19.05) == '0.75"'
    assert stock_label(19.0) == '0.75"'
    assert stock_label(18.9) == '0.75"'
    assert stock_label(50.8) == '2.00"'


def test_stock_label_says_nothing_rather_than_guessing():
    from pdf_report import stock_label
    assert stock_label(None) == "—"


def test_distinct_stock_stays_distinct():
    from pdf_report import stock_label
    assert stock_label(19.05) != stock_label(50.8)
    # 1/64" apart is a real difference and must not be collapsed.
    assert stock_label(19.05) != stock_label(19.05 + 25.4 / 64)


# ── the footer's run order (_wrap_sequence) ──────────────────────────────────
#
# The sequence is the one footer field whose length is unbounded: a nest whose parts
# disagree about tool order revisits tools, so the list is as long as the job's
# tool-change count rather than its tool count. It is measured against the canvas's
# own metrics, and three widths have to be reserved before a word is accepted — the
# continuation arrow, the indent, and the park note. Each was an overflow first.

from reportlab.pdfgen import canvas as pdfcanvas

from pdf_report import MARGIN, PAGE, _SEQ_FONT, _SEQ_INDENT, _wrap_sequence


def _seq_canvas(tmp_path):
    return pdfcanvas.Canvas(str(tmp_path / "seq.pdf"), pagesize=PAGE)


def _avail():
    return PAGE[0] - 2 * MARGIN


def test_wrap_sequence_keeps_the_run_order_and_names_the_park_load(tmp_path):
    c = _seq_canvas(tmp_path)
    lines = _wrap_sequence(c, ["T1", "T3", "T1", "T3"], "T2", _avail())
    assert lines == [
        "Tool sequence: T1 → T3 → T1 → T3 → T2 (loaded for the next job, cuts nothing)"
    ]


def test_wrap_sequence_says_so_when_there_is_nothing_placed(tmp_path):
    assert _wrap_sequence(_seq_canvas(tmp_path), [], None, _avail()) == \
        ["Tool sequence: —"]


@pytest.mark.parametrize("blocks", [1, 2, 19, 20, 21, 30, 33, 60, 200])
@pytest.mark.parametrize("park", ["T2", None])
def test_no_wrapped_line_runs_off_the_page(tmp_path, blocks, park):
    """Including the indent every continuation line is drawn with."""
    c = _seq_canvas(tmp_path)
    seq = [f"T{1 + i % 7}" for i in range(blocks)]
    for i, line in enumerate(_wrap_sequence(c, seq, park, _avail())):
        drawn = c.stringWidth(line, *_SEQ_FONT) + (_SEQ_INDENT if i else 0)
        assert drawn <= _avail(), f"line {i} is {drawn:.1f}pt wide: {line}"


def test_a_wrapped_line_ends_on_an_arrow_so_the_order_reads_across_the_break(tmp_path):
    c = _seq_canvas(tmp_path)
    lines = _wrap_sequence(c, [f"T{1 + i % 7}" for i in range(60)], "T2", _avail())
    assert len(lines) > 1
    for line in lines[:-1]:
        assert line.endswith("→")
    # Every pocket survives the break, in order.
    flat = " ".join(lines).replace("Tool sequence: ", "")
    assert [w for w in flat.split() if w.startswith("T")][:60] == \
        [f"T{1 + i % 7}" for i in range(60)]
