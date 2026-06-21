"""Sky-chart rendering with starplot.

The heavy catalogs are loaded lazily by starplot's DuckDB backend; the default
style is built once here and reused. matplotlib is not thread-safe and the
Raspberry Pi cannot afford parallel renders, so callers MUST serialize calls to
:meth:`Renderer.render` (the MQTT service holds a lock for this).

Implemented map types: `full`, `galactic`, `zenith`, `horizon`, `optic`.
"""

import logging
from io import BytesIO

from starplot import (
    Binoculars,
    Camera,
    GalaxyPlot,
    HorizonPlot,
    MapPlot,
    Miller,
    Observer,
    OpticPlot,
    Reflector,
    Refractor,
    Scope,
    ZenithPlot,
    _,
    settings,
)
from starplot.styles import PlotStyle, extensions

from src import config
from src.errors import ValidationError
from src.request import RenderRequest

logger = logging.getLogger(__name__)

# The full-sky style was tuned at this resolution (scale 0.8); we scale relative
# to it so the map looks the same at any resolution, just lighter.
_FULL_REFERENCE_RESOLUTION = 6000

# Compass direction -> azimuth (degrees). Used to center a horizon panorama.
_DIRECTION_AZIMUTH = {
    "N": 0,
    "NE": 45,
    "E": 90,
    "SE": 135,
    "S": 180,
    "SW": 225,
    "W": 270,
    "NW": 315,
}


def _dso_label(d):
    """Pick a human-friendly label for a deep-sky object."""
    if d.common_names:
        return d.common_names[0]
    if d.ngc:
        return d.ngc
    if d.ic:
        return f"IC{d.ic}"
    return d.name


class Renderer:
    def __init__(self):
        # Point starplot at our data directory before any catalog is touched.
        # Must be a Path: starplot does `settings.data_path / "duckdb-extensions"`.
        settings.data_path = config.DATA_DIR
        logger.info("starplot data_path = %s", config.DATA_DIR)
        if config.LANGUAGE:
            settings.language = config.LANGUAGE
            logger.info("starplot language set to '%s'", config.LANGUAGE)
        # Build the default style once and reuse it across renders.
        self._style = self._make_style(config.STYLE)
        logger.info("Renderer ready (style=%s, resolution=%d)", config.STYLE, config.RESOLUTION)

    # ------------------------------------------------------------- dispatch
    def render(self, request: RenderRequest) -> bytes:
        """Render a chart for a validated request and return it as PNG bytes."""
        logger.info(
            "Rendering map_type=%s lat=%s lon=%s dt=%s",
            request.map_type,
            request.lat,
            request.lon,
            request.dt.isoformat(),
        )

        if request.map_type == "full":
            return self._render_full(request)
        if request.map_type == "zenith":
            return self._render_zenith(request)
        if request.map_type == "horizon":
            return self._render_horizon(request)
        if request.map_type == "galactic":
            return self._render_galactic(request)
        if request.map_type == "optic":
            return self._render_optic(request)
        # Should be unreachable: parse_command rejects unknown map types.
        raise NotImplementedError(f"map_type '{request.map_type}' is not implemented yet")

    # -------------------------------------------------------------- helpers
    def _make_style(self, style_name: str, base=extensions.MAP) -> PlotStyle:
        if not hasattr(extensions, style_name):
            logger.warning("Unknown style '%s', falling back to %s", style_name, config.STYLE)
            style_name = config.STYLE
        return PlotStyle().extend(getattr(extensions, style_name), base)

    def _style_for(self, request: RenderRequest, base=extensions.MAP) -> PlotStyle:
        name = request.options.get("style") or config.STYLE
        # Reuse the cached default only for the common MAP-based case.
        if base is extensions.MAP and name == config.STYLE:
            return self._style
        return self._make_style(str(name), base)

    @staticmethod
    def _resolution_for(request: RenderRequest) -> int:
        try:
            return int(request.options.get("resolution", config.RESOLUTION))
        except (TypeError, ValueError):
            return config.RESOLUTION

    @staticmethod
    def _observer(request: RenderRequest) -> Observer:
        # request.dt is always timezone-aware (parse_command guarantees it),
        # which is what starplot's Observer requires.
        return Observer(dt=request.dt, lat=request.lat, lon=request.lon)

    @staticmethod
    def _export(plot) -> bytes:
        buf = BytesIO()
        plot.export(buf, format="png", padding=0.5)
        return buf.getvalue()

    def _plot_dsos(self, p):
        """Plot a modest set of deep-sky objects (shared by full/zenith)."""
        mag_filters = (_.magnitude < 8) | (_.magnitude.isnull())
        p.open_clusters(
            where=[_.size < 0.2, _.magnitude < 8],
            where_labels=[False],
            label_fn=_dso_label,
            where_true_size=[False],
        )
        with p.style.dso_open_cluster as oc:
            oc.label.font_size = 26
            oc.label.font_weight = 800
            p.open_clusters(where=[_.size > 0.2, mag_filters], label_fn=_dso_label)
        p.nebula(
            where=[mag_filters, _.size < 0.2],
            label_fn=_dso_label,
            where_true_size=[False],
        )
        with p.style.dso_nebula as neb:
            neb.label.font_size = 26
            neb.label.font_weight = 800
            p.nebula(where=[mag_filters, _.size > 0.2], label_fn=_dso_label)
        p.globular_clusters(where=[_.magnitude <= 9], where_labels=[True], where_true_size=[False])
        p.galaxies(where=[_.magnitude <= 10], where_labels=[True], where_true_size=[False])

    # ------------------------------------------------------------- renderers
    def _render_full(self, request: RenderRequest) -> bytes:
        """All-sky RA/DEC map (the original main.py behavior, parameterized)."""
        resolution = self._resolution_for(request)
        # The full-sky style (fonts, markers, the font_size overrides below) was
        # tuned for a 6000px map at scale 0.8. Keep that ratio at any resolution
        # so labels/objects don't look oversized on smaller renders.
        scale = 0.8 * resolution / _FULL_REFERENCE_RESOLUTION
        p = MapPlot(
            projection=Miller(),
            ra_min=0,
            ra_max=360,
            dec_min=-80,
            dec_max=80,
            style=self._style_for(request),
            resolution=resolution,
            scale=scale,
        )
        p.gridlines()
        p.constellations()
        p.stars(
            where=[_.magnitude < config.STAR_MAGNITUDE_LIMIT],
            where_labels=[_.magnitude < 2.1],
        )
        self._plot_dsos(p)
        p.constellation_labels(style__font_size=28)
        p.milky_way()
        p.ecliptic()
        p.celestial_equator()
        return self._export(p)

    def _render_zenith(self, request: RenderRequest) -> bytes:
        """Dome of sky overhead at the observer's time and place."""
        p = ZenithPlot(
            observer=self._observer(request),
            style=self._style_for(request),
            resolution=self._resolution_for(request),
            autoscale=True,
        )
        p.constellations()
        p.stars(
            where=[_.magnitude < config.STAR_MAGNITUDE_LIMIT],
            where_labels=[_.magnitude < 2.1],
        )
        self._plot_dsos(p)
        p.constellation_labels()
        p.milky_way()
        p.horizon()  # great circle + N/E/S/W cardinal labels
        # NOTE: p.info() is broken in starplot 0.20.4 (references a missing
        # `self.dt`), so we don't call it.
        return self._export(p)

    def _render_horizon(self, request: RenderRequest) -> bytes:
        """Panorama of the sky above the horizon, centered on a compass direction."""
        direction = str(request.options.get("direction", "S")).upper()
        center = _DIRECTION_AZIMUTH.get(direction, 180)
        azimuth = (center - 90, center + 90)  # 180-deg wide swath (starplot's max)
        altitude = (0, 70)

        p = HorizonPlot(
            altitude=altitude,
            azimuth=azimuth,
            observer=self._observer(request),
            style=self._style_for(request),
            resolution=self._resolution_for(request),
            autoscale=True,
        )
        p.constellations()
        p.stars(
            where=[_.magnitude < config.STAR_MAGNITUDE_LIMIT],
            where_labels=[_.magnitude < 2.1],
        )
        p.milky_way()
        p.gridlines()
        p.horizon()  # ground rectangle + azimuth/cardinal labels
        return self._export(p)

    def _render_galactic(self, request: RenderRequest) -> bytes:
        """All-sky map in galactic coordinates (Mollweide). No observer needed."""
        p = GalaxyPlot(
            style=self._style_for(request),
            resolution=self._resolution_for(request),
            autoscale=True,
        )
        p.constellations()
        p.stars(
            where=[_.magnitude < config.STAR_MAGNITUDE_LIMIT],
            where_labels=[_.magnitude < 2.1],
        )
        self._plot_dsos(p)
        p.milky_way()
        p.galactic_equator()
        p.constellation_labels()
        return self._export(p)

    def _render_optic(self, request: RenderRequest) -> bytes:
        """A target as seen through a given optic (telescope/binoculars/camera)."""
        ra, dec = self._target_radec(request)
        optic = self._build_optic(request.optic)
        try:
            p = OpticPlot(
                ra=ra,
                dec=dec,
                optic=optic,
                observer=self._observer(request),
                style=self._style_for(request, base=extensions.OPTIC),
                resolution=self._resolution_for(request),
                raise_on_below_horizon=True,
                autoscale=True,
            )
        except ValueError as e:
            # starplot raises ValueError for "below horizon" and "FOV too big".
            # Turn these into bot-safe validation messages.
            raise ValidationError(str(e))
        p.stars(where=[_.magnitude < 14])  # optic views go much deeper than naked eye
        p.open_clusters(where=[(_.magnitude < 12) | (_.magnitude.isnull())], label_fn=_dso_label)
        p.galaxies(where=[(_.magnitude < 14) | (_.magnitude.isnull())], label_fn=_dso_label)
        p.nebula(where=[(_.magnitude < 14) | (_.magnitude.isnull())], label_fn=_dso_label)
        p.info()
        return self._export(p)

    @staticmethod
    def _target_radec(request: RenderRequest):
        target = request.target
        if target.get("ra") is not None and target.get("dec") is not None:
            try:
                return float(target["ra"]), float(target["dec"])
            except (TypeError, ValueError):
                raise ValidationError("target.ra and target.dec must be numbers (degrees)")
        # Resolving an object name (e.g. "M31") to coordinates is not wired up yet.
        raise ValidationError(
            "optic charts currently require target.ra and target.dec (degrees); "
            "object-name lookup is not supported yet"
        )

    @staticmethod
    def _build_optic(spec: dict):
        otype = str(spec.get("type", "")).lower()

        def num(key):
            value = spec.get(key)
            if value is None:
                raise ValidationError(f"optic.{key} is required for optic.type '{otype}'")
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ValidationError(f"optic.{key} must be a number")

        if otype == "binoculars":
            return Binoculars(magnification=num("magnification"), fov=num("fov"))
        if otype in ("telescope", "scope", "generic"):
            return Scope(
                focal_length=num("focal_length"),
                eyepiece_focal_length=num("eyepiece_focal_length"),
                eyepiece_fov=num("eyepiece_fov"),
            )
        if otype == "refractor":
            return Refractor(
                focal_length=num("focal_length"),
                eyepiece_focal_length=num("eyepiece_focal_length"),
                eyepiece_fov=num("eyepiece_fov"),
            )
        if otype == "reflector":
            return Reflector(
                focal_length=num("focal_length"),
                eyepiece_focal_length=num("eyepiece_focal_length"),
                eyepiece_fov=num("eyepiece_fov"),
            )
        if otype == "camera":
            camera = Camera(
                sensor_height=num("sensor_height"),
                sensor_width=num("sensor_width"),
                lens_focal_length=num("lens_focal_length"),
                rotation=float(spec.get("rotation", 0) or 0),
            )
            return camera
        raise ValidationError(
            f"unknown optic.type '{otype}'; expected one of: " "binoculars, telescope, refractor, reflector, camera"
        )
