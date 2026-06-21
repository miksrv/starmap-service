"""Tests for src.storage — on-disk chart output and retention pruning."""

import time

import pytest

from src import config, storage


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    """Point storage at a throwaway directory with predictable retention limits."""
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUT_MAX_FILES", 3)
    monkeypatch.setattr(config, "OUTPUT_MAX_AGE_HOURS", 24)
    return tmp_path


# ---------------------------------------------------------------------------
# _safe_name
# ---------------------------------------------------------------------------
def test_safe_name_passes_clean_id():
    assert storage._safe_name("req-123_ok") == "req-123_ok"


def test_safe_name_strips_path_traversal():
    name = storage._safe_name("../../etc/passwd")
    assert "/" not in name and ".." not in name


def test_safe_name_empty_falls_back_to_chart():
    assert storage._safe_name("") == "chart"
    assert storage._safe_name("///") == "chart"


def test_safe_name_truncated_to_64():
    assert len(storage._safe_name("a" * 200)) == 64


# ---------------------------------------------------------------------------
# save_chart
# ---------------------------------------------------------------------------
def test_save_chart_writes_png(output_dir):
    path = storage.save_chart(b"PNGDATA", "req-1")
    assert path.exists()
    assert path.name == "req-1.png"
    assert path.read_bytes() == b"PNGDATA"


def test_save_chart_creates_missing_dir(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "out"
    monkeypatch.setattr(config, "OUTPUT_DIR", target)
    monkeypatch.setattr(config, "OUTPUT_MAX_FILES", 0)
    monkeypatch.setattr(config, "OUTPUT_MAX_AGE_HOURS", 0)
    path = storage.save_chart(b"x", "r")
    assert path.exists()


def test_save_chart_sanitizes_filename(output_dir):
    path = storage.save_chart(b"x", "../evil")
    assert path.parent == output_dir
    assert "/" not in path.name


# ---------------------------------------------------------------------------
# prune_output
# ---------------------------------------------------------------------------
def test_prune_by_count_keeps_newest(output_dir):
    # max_files = 3; write 5 and ensure the 2 oldest are removed.
    for i in range(5):
        f = output_dir / f"c{i}.png"
        f.write_bytes(b"x")
        # space mtimes apart so ordering is deterministic
        ts = time.time() - (5 - i)
        import os

        os.utime(f, (ts, ts))
    storage.prune_output()
    remaining = sorted(p.name for p in output_dir.glob("*.png"))
    assert remaining == ["c2.png", "c3.png", "c4.png"]


def test_prune_by_age_removes_old(output_dir):
    import os

    fresh = output_dir / "fresh.png"
    old = output_dir / "old.png"
    fresh.write_bytes(b"x")
    old.write_bytes(b"x")
    old_ts = time.time() - 48 * 3600  # older than 24h limit
    os.utime(old, (old_ts, old_ts))
    storage.prune_output()
    assert fresh.exists()
    assert not old.exists()


def test_prune_unlimited_count_keeps_all(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUT_MAX_FILES", 0)  # unlimited
    monkeypatch.setattr(config, "OUTPUT_MAX_AGE_HOURS", 0)  # no age limit
    for i in range(10):
        (tmp_path / f"c{i}.png").write_bytes(b"x")
    storage.prune_output()
    assert len(list(tmp_path.glob("*.png"))) == 10


def test_prune_missing_dir_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "does-not-exist")
    monkeypatch.setattr(config, "OUTPUT_MAX_FILES", 3)
    monkeypatch.setattr(config, "OUTPUT_MAX_AGE_HOURS", 24)
    storage.prune_output()  # must not raise
