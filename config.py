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


def load_config() -> dict:
    path = get_config_path()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(data: dict) -> None:
    path = get_config_path()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
