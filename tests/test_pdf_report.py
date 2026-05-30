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
    "tool_sequence": ["T1", "T2"],
    "tool_changes": 1,
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
