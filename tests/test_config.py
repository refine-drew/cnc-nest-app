"""
Tests for path sanitization and cross-platform library-path resolution
(config._sanitize_path_str / normalize_library_paths / resolve_library_root).
"""
import pytest

from config import _sanitize_path_str, normalize_library_paths, resolve_library_root


# ── _sanitize_path_str ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("'/Users/me/lib'", "/Users/me/lib"),          # single quotes (the reported bug)
    ('"/Users/me/lib"', "/Users/me/lib"),          # double quotes
    ("  /Users/me/lib  ", "/Users/me/lib"),        # surrounding whitespace
    ("  '/Users/me/lib'  ", "/Users/me/lib"),      # quotes + whitespace
    ('"\'/Users/me/lib\'"', "/Users/me/lib"),      # nested/doubled quotes
    ("/Users/me/lib", "/Users/me/lib"),            # already clean
    ("", ""),
    (None, ""),                                    # non-string
    (123, ""),
])
def test_sanitize_path_str(raw, expected):
    assert _sanitize_path_str(raw) == expected


# ── normalize_library_paths ──────────────────────────────────────────────────

def test_normalize_from_string():
    assert normalize_library_paths("'/a/b'") == ["/a/b"]


def test_normalize_from_list_drops_empties_and_cleans():
    assert normalize_library_paths(["'/a'", "  ", '"/b"', ""]) == ["/a", "/b"]


def test_normalize_from_garbage():
    assert normalize_library_paths(None) == []
    assert normalize_library_paths([None, 5]) == []


# ── resolve_library_root ─────────────────────────────────────────────────────

def test_resolve_picks_first_existing(tmp_path):
    real = tmp_path / "real_lib"
    real.mkdir()
    value = ["/does/not/exist/one", str(real), "/does/not/exist/two"]
    assert resolve_library_root(value) == real.resolve()


def test_resolve_strips_quotes_before_checking(tmp_path):
    real = tmp_path / "lib"
    real.mkdir()
    # quoted path must still resolve to the existing dir
    assert resolve_library_root([f"'{real}'"]) == real.resolve()


def test_resolve_falls_back_to_first_when_none_exist():
    value = ["/nope/one", "/nope/two"]
    from pathlib import Path
    assert resolve_library_root(value) == Path("/nope/one").expanduser().resolve()


def test_resolve_empty_falls_back_to_default():
    # empty list → default library path, never crashes
    result = resolve_library_root([])
    assert result.name == "cnc_library"
