"""Tests for src.config — the topic contract, defaults and env overrides."""

import importlib

from src import config
from src.request import MAP_TYPES


def test_topics_match_contract():
    # These strings are the contract with the bot — they must not drift.
    assert config.TOPICS == {
        "command": "starmap/command",
        "result": "starmap/result",
        "status": "starmap/status",
    }


def test_default_map_type_is_valid():
    assert config.DEFAULT_MAP_TYPE in MAP_TYPES


def test_numeric_defaults_have_expected_types():
    assert isinstance(config.RESOLUTION, int)
    assert isinstance(config.QUEUE_MAX_SIZE, int)
    assert isinstance(config.STAR_MAGNITUDE_LIMIT, float)
    assert isinstance(config.OUTPUT_MAX_FILES, int)
    assert isinstance(config.OUTPUT_MAX_AGE_HOURS, float)


def test_output_dir_is_absolute():
    assert config.OUTPUT_DIR.is_absolute()


def test_env_override_resolution(monkeypatch):
    monkeypatch.setenv("STARMAP_RESOLUTION", "1234")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.RESOLUTION == 1234
    finally:
        monkeypatch.delenv("STARMAP_RESOLUTION", raising=False)
        importlib.reload(config)


def test_env_override_output_mode(monkeypatch):
    monkeypatch.setenv("STARMAP_OUTPUT_MODE", "file")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.OUTPUT_MODE == "file"
    finally:
        monkeypatch.delenv("STARMAP_OUTPUT_MODE", raising=False)
        importlib.reload(config)
