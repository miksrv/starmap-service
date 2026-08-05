"""Tests for the pure, deterministic helpers in src.renderer.

These never perform a real render (no catalog data needed): they cover optic
construction, target resolution, resolution parsing, the direction table and
map-type dispatch.
"""

from types import SimpleNamespace
from unittest import mock

import pytest

from src import renderer as rmod
from src.errors import ValidationError
from src.renderer import Renderer
from src.request import RenderRequest


def _req(map_type="full", target=None, optic=None, options=None):
    return RenderRequest(
        request_id="rid",
        map_type=map_type,
        dt=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        lat=55.0,
        lon=37.0,
        target=target or {},
        optic=optic or {},
        options=options or {},
    )


# ---------------------------------------------------------------------------
# _build_optic
# ---------------------------------------------------------------------------
def test_build_binoculars():
    optic = Renderer._build_optic({"type": "binoculars", "magnification": 10, "fov": 5})
    assert isinstance(optic, rmod.Binoculars)


@pytest.mark.parametrize(
    "otype,cls_name",
    [
        ("telescope", "Scope"),
        ("scope", "Scope"),
        ("generic", "Scope"),
        ("refractor", "Refractor"),
        ("reflector", "Reflector"),
    ],
)
def test_build_scope_family(otype, cls_name):
    optic = Renderer._build_optic(
        {
            "type": otype,
            "focal_length": 1000,
            "eyepiece_focal_length": 25,
            "eyepiece_fov": 50,
        }
    )
    assert type(optic).__name__ == cls_name


def test_build_camera():
    optic = Renderer._build_optic(
        {
            "type": "camera",
            "sensor_height": 24,
            "sensor_width": 36,
            "lens_focal_length": 50,
        }
    )
    assert isinstance(optic, rmod.Camera)


def test_build_camera_rotation_optional():
    # rotation omitted must not raise (defaults to 0)
    optic = Renderer._build_optic({"type": "camera", "sensor_height": 1, "sensor_width": 1, "lens_focal_length": 1})
    assert isinstance(optic, rmod.Camera)


def test_build_optic_unknown_type():
    with pytest.raises(ValidationError) as exc:
        Renderer._build_optic({"type": "kaleidoscope"})
    assert "unknown optic.type" in str(exc.value)


def test_build_optic_missing_required_field():
    with pytest.raises(ValidationError) as exc:
        Renderer._build_optic({"type": "binoculars", "magnification": 10})
    assert "fov" in str(exc.value)


def test_build_optic_non_numeric_field():
    with pytest.raises(ValidationError) as exc:
        Renderer._build_optic({"type": "binoculars", "magnification": "ten", "fov": 5})
    assert "must be a number" in str(exc.value)


# ---------------------------------------------------------------------------
# _target_radec
# ---------------------------------------------------------------------------
def test_target_radec_from_coords():
    ra, dec = Renderer()._target_radec(_req(target={"ra": 10.5, "dec": 41.2}))
    assert ra == pytest.approx(10.5)
    assert dec == pytest.approx(41.2)


def test_target_radec_non_numeric():
    with pytest.raises(ValidationError) as exc:
        Renderer()._target_radec(_req(target={"ra": "x", "dec": "y"}))
    assert "must be numbers" in str(exc.value)


def test_target_radec_empty():
    with pytest.raises(ValidationError):
        Renderer()._target_radec(_req(target={}))


# ---------------------------------------------------------------------------
# _resolve_object_name — catalog-number parsing (M31 / "M 31" / "M_31" / ngc-224 / IC1396).
# DSO.get/.find, Star.find, Planet.get are mocked so these are deterministic
# regardless of whether the real starplot (with real catalogs, e.g. in CI) or
# the tests/conftest.py stub is active — we're testing the normalization and
# dispatch logic here, not real catalog data.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("spelling", ["M31", "M 31", "M_31", "m-31"])
def test_resolve_object_name_messier_spellings(spelling):
    fake_dso = SimpleNamespace(ra=10.68, dec=41.27)
    with mock.patch.object(rmod.DSO, "get", return_value=fake_dso) as mock_get:
        ra, dec = Renderer._resolve_object_name(spelling, observer=None)
    mock_get.assert_called_once_with(m="31")
    assert ra == pytest.approx(10.68)
    assert dec == pytest.approx(41.27)


@pytest.mark.parametrize(
    "spelling,field,number",
    [
        ("NGC224", "ngc", "224"),
        ("ngc 224", "ngc", "224"),
        ("IC1396", "ic", "1396"),
        ("ic_1396", "ic", "1396"),
    ],
)
def test_resolve_object_name_ngc_ic_spellings(spelling, field, number):
    fake_dso = SimpleNamespace(ra=1.0, dec=2.0)
    with mock.patch.object(rmod.DSO, "get", return_value=fake_dso) as mock_get:
        ra, dec = Renderer._resolve_object_name(spelling, observer=None)
    mock_get.assert_called_once_with(**{field: number})
    assert (ra, dec) == pytest.approx((1.0, 2.0))


def test_resolve_object_name_catalog_number_not_found():
    with mock.patch.object(rmod.DSO, "get", return_value=None):
        with pytest.raises(ValidationError) as exc:
            Renderer._resolve_object_name("M999999", observer=None)
    assert "not found" in str(exc.value)
    assert "M999999" in str(exc.value)


def test_resolve_object_name_empty_rejected():
    with pytest.raises(ValidationError) as exc:
        Renderer._resolve_object_name("   ", observer=None)
    assert "must not be empty" in str(exc.value)


def test_resolve_object_name_common_name_lookup():
    # Not a catalog number, not "sun"/"moon" — falls through past Planet/Star
    # (both miss) to the DSO common-name search.
    fake_dso = SimpleNamespace(ra=10.68, dec=41.27)
    with (
        mock.patch.object(rmod.DSO, "get", return_value=None),
        mock.patch.object(rmod.Planet, "get", return_value=None),
        mock.patch.object(rmod.Star, "find", return_value=[]),
        mock.patch.object(rmod.DSO, "find", return_value=[fake_dso]),
    ):
        ra, dec = Renderer._resolve_object_name("Andromeda Galaxy", observer=None)
    assert (ra, dec) == pytest.approx((10.68, 41.27))


def test_resolve_object_name_nothing_matches():
    with (
        mock.patch.object(rmod.DSO, "get", return_value=None),
        mock.patch.object(rmod.Planet, "get", return_value=None),
        mock.patch.object(rmod.Star, "find", return_value=[]),
        mock.patch.object(rmod.DSO, "find", return_value=[]),
    ):
        with pytest.raises(ValidationError) as exc:
            Renderer._resolve_object_name("Nonexistent Thing", observer=None)
    assert "not found" in str(exc.value)


# ---------------------------------------------------------------------------
# _resolution_for
# ---------------------------------------------------------------------------
def test_resolution_from_options():
    assert Renderer._resolution_for(_req(options={"resolution": 1500})) == 1500


def test_resolution_default_when_absent():
    from src import config

    assert Renderer._resolution_for(_req()) == config.RESOLUTION


def test_resolution_invalid_falls_back():
    from src import config

    assert Renderer._resolution_for(_req(options={"resolution": "big"})) == config.RESOLUTION


# ---------------------------------------------------------------------------
# Direction table
# ---------------------------------------------------------------------------
def test_direction_table_has_eight_points():
    assert set(rmod._DIRECTION_AZIMUTH) == {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
    assert rmod._DIRECTION_AZIMUTH["S"] == 180
    assert rmod._DIRECTION_AZIMUTH["N"] == 0


# ---------------------------------------------------------------------------
# _dso_label
# ---------------------------------------------------------------------------
class _DSO:
    def __init__(self, common_names=None, ngc=None, ic=None, name="anon"):
        self.common_names = common_names or []
        self.ngc = ngc
        self.ic = ic
        self.name = name


def test_dso_label_prefers_common_name():
    assert rmod._dso_label(_DSO(common_names=["Andromeda"], ngc="224")) == "Andromeda"


def test_dso_label_falls_back_to_ngc():
    assert rmod._dso_label(_DSO(ngc="7000")) == "7000"


def test_dso_label_falls_back_to_ic():
    assert rmod._dso_label(_DSO(ic="434")) == "IC434"


def test_dso_label_falls_back_to_name():
    assert rmod._dso_label(_DSO(name="weird")) == "weird"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def test_render_dispatches_by_map_type(monkeypatch):
    r = Renderer()
    called = {}

    def _stub(_mt):
        def _render(_req_arg):
            called["hit"] = _mt
            return b"png"

        return _render

    for mt in ("full", "zenith", "horizon", "galactic", "optic"):
        monkeypatch.setattr(r, f"_render_{mt}", _stub(mt))
    for mt in ("full", "zenith", "horizon", "galactic", "optic"):
        called.clear()
        assert r.render(_req(map_type=mt)) == b"png"
        assert called["hit"] == mt


def test_render_unknown_map_type_raises(monkeypatch):
    r = Renderer()
    bogus = _req()
    bogus.map_type = "teleport"
    with pytest.raises(NotImplementedError):
        r.render(bogus)
