"""Runtime configuration.

Defaults live in ``config/config.yaml``; environment variables override them
(the YAML file is mounted separately in Docker so it can be changed without
rebuilding the image). Secrets and deployment-specific values should come from
environment variables only.
"""

import os
from pathlib import Path
from typing import Dict

import yaml
from dotenv import load_dotenv

load_dotenv()

# Project root (one level above this package).
BASE_DIR = Path(__file__).resolve().parent.parent
_CONFIG_FILE = BASE_DIR / "config" / "config.yaml"

# starplot data catalogs (star/DSO/constellation/milky-way parquet files, the
# de421.bsp ephemeris, and the DuckDB spatial extension). starplot reads these
# from `settings.data_path`; we point it at this directory. Override with the
# STARPLOT_DATA_PATH env var.
DATA_DIR = Path(os.getenv("STARPLOT_DATA_PATH", BASE_DIR / "data"))


def _load_yaml_config() -> dict:
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    return {}


_yaml = _load_yaml_config()
_mqtt_cfg = _yaml.get("mqtt", {})
_render_cfg = _yaml.get("render", {})
_queue_cfg = _yaml.get("queue", {}) or {}
_output_cfg = _yaml.get("output", {})
_log_cfg = _yaml.get("logging", {})

# --- MQTT (env overrides YAML) ---
MQTT_BROKER = os.getenv("MQTT_BROKER", _mqtt_cfg.get("broker", "localhost"))
MQTT_PORT = int(os.getenv("MQTT_PORT", _mqtt_cfg.get("port", 1883)))
MQTT_KEEPALIVE = int(_mqtt_cfg.get("keepalive", 60))
MQTT_CLIENT_ID = "starmap-service"

# Topics — the contract with the bot. Never hardcode these strings elsewhere.
TOPICS: Dict[str, str] = {
    "command": "starmap/command",  # bot  -> service: render request
    "result": "starmap/result",  # service -> bot: chart or error
    "status": "starmap/status",  # service -> bot: online/offline (retained + LWT)
}

# --- Rendering ---
# Output image width in pixels. Lower = faster + lighter (important on the Pi).
RESOLUTION = int(os.getenv("STARMAP_RESOLUTION", _render_cfg.get("resolution", 2600)))
DEFAULT_MAP_TYPE = _render_cfg.get("default_map_type", "full")
STYLE = _render_cfg.get("style", "BLUE_NIGHT")  # name of a starplot extensions theme
STAR_MAGNITUDE_LIMIT = float(_render_cfg.get("star_magnitude_limit", 6))
# starplot UI language, e.g. "ru" — leave empty until Russian support is verified.
LANGUAGE = _render_cfg.get("language") or None

# --- Queue ---
# Render requests are processed one at a time by a single worker; extra requests
# wait in a bounded FIFO queue. A request that arrives when the queue is full is
# rejected with "queue full". 0 = unbounded (no limit).
QUEUE_MAX_SIZE = int(os.getenv("STARMAP_QUEUE_MAX_SIZE", _queue_cfg.get("max_size", 10)))

# --- Output delivery ---
# "base64": embed the PNG in the MQTT reply. "file": write to OUTPUT_DIR and
# return its path (lighter on the broker when bot and service share one host).
OUTPUT_MODE = os.getenv("STARMAP_OUTPUT_MODE", _output_cfg.get("mode", "file"))
_output_dir = Path(_output_cfg.get("dir", "output"))
OUTPUT_DIR = _output_dir if _output_dir.is_absolute() else BASE_DIR / _output_dir

# Output retention (file mode): cap the number/age of generated PNGs.
_retention_cfg = _output_cfg.get("retention", {}) or {}
OUTPUT_MAX_FILES = int(_retention_cfg.get("max_files", 50))  # 0 = unlimited
OUTPUT_MAX_AGE_HOURS = float(_retention_cfg.get("max_age_hours", 24))  # 0 = no age limit

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", _log_cfg.get("level", "INFO"))
