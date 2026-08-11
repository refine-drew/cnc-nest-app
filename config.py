import json
from pathlib import Path

CONFIG_FILE = "config.json"


def get_config_path() -> Path:
    return Path(__file__).parent / CONFIG_FILE


def get_default_output_path() -> str:
    return str(Path.home() / "Downloads")


def get_default_library_path() -> str:
    return str(Path.home() / "Documents" / "cnc_library")


def resolve_path(p: str) -> Path:
    """Resolve ~ and environment variables cross-platform."""
    return Path(p).expanduser().resolve()


def _sanitize_path_str(p) -> str:
    """Strip surrounding whitespace and matching wrapping quotes from a pasted path.

    Pasting a shell-quoted path (e.g. '/Users/.../lib') stores the quotes literally,
    which makes Path() treat it as relative. This undoes that.
    """
    if not isinstance(p, str):
        return ""
    p = p.strip()
    while len(p) >= 2 and p[0] == p[-1] and p[0] in ("'", '"'):
        p = p[1:-1].strip()
    return p


def normalize_library_paths(value) -> list:
    """Return a clean list of candidate library paths from a str or list."""
    items = value if isinstance(value, list) else [value]
    out = []
    for it in items:
        s = _sanitize_path_str(it)
        if s:
            out.append(s)
    return out


def resolve_library_root(value) -> Path:
    """Pick the first existing candidate directory; otherwise fall back sensibly.

    Lets a single shared config.json carry both the Mac and Windows Google Drive
    paths — each machine resolves to whichever one exists locally.
    """
    resolved = [Path(c).expanduser().resolve() for c in normalize_library_paths(value)]
    for r in resolved:
        if r.is_dir():
            return r
    if resolved:
        return resolved[0]
    return Path(get_default_library_path()).expanduser().resolve()


def load_config() -> dict:
    path = get_config_path()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(data: dict) -> None:
    path = get_config_path()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
