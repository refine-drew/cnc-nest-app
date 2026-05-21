import json
import os

CONFIG_FILE = "config.json"

def get_config_path():
    return os.path.join(os.path.dirname(__file__), CONFIG_FILE)

def load_config():
    path = get_config_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(data):
    path = get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
