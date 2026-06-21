"""Tests for src.request.parse_command — the command validation layer."""

from datetime import datetime, timezone

import pytest

from src import config
from src.errors import ValidationError
from src.request import MAP_TYPES, RenderRequest, parse_command


# ---------------------------------------------------------------------------
# Defaults & basic shape
# ---------------------------------------------------------------------------
def test_minimal_full_request():
    req = parse_command({"request_id": "abc", "map_type": "full"})
    assert isinstance(req, RenderRequest)
    assert req.request_id == "abc"
    assert req.map_type == "full"
    assert req.lat is None and req.lon is None
    assert req.dt.tzinfo is not None  # always tz-aware


def test_request_id_is_stringified():
    req = parse_command({"request_id": 12345, "map_type": "full"})
    assert req.request_id == "12345"


def test_map_type_defaults_to_config_default():
    req = parse_command({"request_id": "x"})
    assert req.map_type == config.DEFAULT_MAP_TYPE
    assert req.map_type in MAP_TYPES


def test_unknown_map_type_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_command({"request_id": "x", "map_type": "wormhole"})
    assert "unknown map_type" in str(exc.value)


# ---------------------------------------------------------------------------
# Observer / coordinates
# ---------------------------------------------------------------------------
def test_observer_block_parsed():
    req = parse_command({"request_id": "x", "map_type": "zenith", "observer": {"lat": 55.75, "lon": 37.62}})
    assert req.lat == pytest.approx(55.75)
    assert req.lon == pytest.approx(37.62)


def test_flat_lat_lon_fallback():
    req = parse_command({"request_id": "x", "map_type": "zenith", "lat": 10, "lon": 20})
    assert req.lat == pytest.approx(10.0)
    assert req.lon == pytest.approx(20.0)


def test_observer_block_overrides_flat():
    req = parse_command(
        {
            "request_id": "x",
            "map_type": "zenith",
            "lat": 1,
            "lon": 2,
            "observer": {"lat": 55, "lon": 37},
        }
    )
    assert req.lat == pytest.approx(55.0)
    assert req.lon == pytest.approx(37.0)


@pytest.mark.parametrize("map_type", ["zenith", "horizon", "optic"])
def test_observer_required_map_types_need_coords(map_type):
    payload = {"request_id": "x", "map_type": map_type}
    if map_type == "optic":
        payload["target"] = {"ra": 10, "dec": 20}
        payload["optic"] = {"type": "binoculars", "magnification": 10, "fov": 5}
    with pytest.raises(ValidationError) as exc:
        parse_command(payload)
    assert "observer coordinates" in str(exc.value)


def test_observer_not_a_dict_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_command({"request_id": "x", "map_type": "full", "observer": [1, 2]})
    assert "observer" in str(exc.value)


def test_lat_out_of_range():
    with pytest.raises(ValidationError) as exc:
        parse_command({"request_id": "x", "map_type": "zenith", "lat": 91, "lon": 0})
    assert "lat" in str(exc.value)


def test_lon_out_of_range():
    with pytest.raises(ValidationError) as exc:
        parse_command({"request_id": "x", "map_type": "zenith", "lat": 0, "lon": 181})
    assert "lon" in str(exc.value)


def test_non_numeric_lat_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_command({"request_id": "x", "map_type": "zenith", "lat": "north", "lon": 0})
    assert "must be a number" in str(exc.value)


def test_full_ignores_partial_coords_when_none():
    # full does not require coords; with neither provided, both stay None.
    req = parse_command({"request_id": "x", "map_type": "full"})
    assert req.lat is None and req.lon is None


# ---------------------------------------------------------------------------
# Datetime parsing
# ---------------------------------------------------------------------------
def test_missing_datetime_defaults_to_now_utc():
    req = parse_command({"request_id": "x", "map_type": "full"})
    assert req.dt.tzinfo is not None


def test_naive_datetime_treated_as_utc():
    req = parse_command({"request_id": "x", "map_type": "full", "datetime": "2026-06-17T22:00:00"})
    assert req.dt == datetime(2026, 6, 17, 22, 0, 0, tzinfo=timezone.utc)


def test_aware_datetime_preserved():
    req = parse_command({"request_id": "x", "map_type": "full", "datetime": "2026-06-17T22:00:00+03:00"})
    assert req.dt.utcoffset().total_seconds() == 3 * 3600


def test_datetime_from_observer_block():
    req = parse_command(
        {
            "request_id": "x",
            "map_type": "zenith",
            "observer": {"lat": 0, "lon": 0, "datetime": "2026-01-01T00:00:00"},
        }
    )
    assert req.dt.year == 2026


def test_non_string_datetime_rejected():
    with pytest.raises(ValidationError):
        parse_command({"request_id": "x", "map_type": "full", "datetime": 12345})


def test_bad_datetime_string_rejected():
    with pytest.raises(ValidationError):
        parse_command({"request_id": "x", "map_type": "full", "datetime": "yesterday"})


# ---------------------------------------------------------------------------
# Target & optic blocks (optic map type)
# ---------------------------------------------------------------------------
def _optic_payload(**overrides):
    payload = {
        "request_id": "x",
        "map_type": "optic",
        "lat": 55,
        "lon": 37,
        "target": {"ra": 10.0, "dec": 41.0},
        "optic": {"type": "binoculars", "magnification": 10, "fov": 5},
    }
    payload.update(overrides)
    return payload


def test_optic_happy_path():
    req = parse_command(_optic_payload())
    assert req.map_type == "optic"
    assert req.target == {"ra": 10.0, "dec": 41.0}
    assert req.optic["type"] == "binoculars"


def test_optic_accepts_object_name_target():
    req = parse_command(_optic_payload(target={"object": "M31"}))
    assert req.target == {"object": "M31"}


def test_optic_requires_target():
    with pytest.raises(ValidationError) as exc:
        parse_command(_optic_payload(target={}))
    assert "requires a target" in str(exc.value)


def test_optic_requires_optic_type():
    with pytest.raises(ValidationError) as exc:
        parse_command(_optic_payload(optic={}))
    assert "optic" in str(exc.value)


def test_target_not_a_dict_rejected():
    with pytest.raises(ValidationError):
        parse_command(_optic_payload(target="M31"))


def test_optic_not_a_dict_rejected():
    with pytest.raises(ValidationError):
        parse_command(_optic_payload(optic="binoculars"))


def test_options_must_be_dict():
    with pytest.raises(ValidationError) as exc:
        parse_command({"request_id": "x", "map_type": "full", "options": [1]})
    assert "options" in str(exc.value)


def test_options_passthrough():
    req = parse_command({"request_id": "x", "map_type": "full", "options": {"style": "GRAYSCALE"}})
    assert req.options == {"style": "GRAYSCALE"}
