"""Parsing and validation of `starmap/command` payloads.

`parse_command` turns a raw dict into a validated :class:`RenderRequest`, or
raises :class:`ValidationError` with a bot-safe message. All map-type-specific
rules live here so the service and renderer can stay simple.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src import config
from src.errors import ValidationError

# All recognized chart types.
MAP_TYPES = ("full", "galactic", "zenith", "horizon", "optic")

# Which blocks each map type requires.
_OBSERVER_REQUIRED = {"zenith", "horizon", "optic"}
_TARGET_REQUIRED = {"optic"}
_OPTIC_REQUIRED = {"optic"}


@dataclass
class RenderRequest:
    request_id: str
    map_type: str
    dt: datetime
    lat: Optional[float] = None
    lon: Optional[float] = None
    target: dict = field(default_factory=dict)
    optic: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)


def _as_float(value, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be a number")


def _parse_datetime(raw) -> datetime:
    # Missing time → current system time (naive times are treated as UTC).
    if raw in (None, ""):
        return datetime.now(timezone.utc)
    if not isinstance(raw, str):
        raise ValidationError("datetime must be an ISO 8601 string, e.g. 2026-06-17T22:00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise ValidationError("datetime must be ISO 8601, e.g. 2026-06-17T22:00:00")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_command(data: dict) -> RenderRequest:
    """Validate a command dict and return a RenderRequest.

    Assumes ``data`` is a dict and ``data['request_id']`` is already known to be
    a non-empty value (the service checks that before calling, since a reply
    cannot be routed without it).
    """
    request_id = str(data.get("request_id"))

    map_type = data.get("map_type") or config.DEFAULT_MAP_TYPE
    if map_type not in MAP_TYPES:
        raise ValidationError(f"unknown map_type '{map_type}'; expected one of: {', '.join(MAP_TYPES)}")

    # Observer block, with a flat top-level lat/lon/datetime fallback.
    observer = data.get("observer")
    if observer is None:
        observer = {}
    if not isinstance(observer, dict):
        raise ValidationError("'observer' must be an object with lat, lon and optional datetime")

    raw_lat = observer.get("lat", data.get("lat"))
    raw_lon = observer.get("lon", data.get("lon"))
    raw_dt = observer.get("datetime", data.get("datetime"))

    lat = lon = None
    if map_type in _OBSERVER_REQUIRED and (raw_lat is None or raw_lon is None):
        raise ValidationError(f"map_type '{map_type}' requires observer coordinates (observer.lat and observer.lon)")
    if raw_lat is not None or raw_lon is not None:
        lat = _as_float(raw_lat, "observer.lat")
        lon = _as_float(raw_lon, "observer.lon")
        if not -90 <= lat <= 90:
            raise ValidationError("observer.lat must be between -90 and 90")
        if not -180 <= lon <= 180:
            raise ValidationError("observer.lon must be between -180 and 180")

    dt = _parse_datetime(raw_dt)

    # Target block (object name OR ra/dec) — required for optic.
    target = data.get("target") or {}
    if not isinstance(target, dict):
        raise ValidationError("'target' must be an object with 'object' or 'ra'/'dec'")
    if map_type in _TARGET_REQUIRED:
        has_coords = target.get("ra") is not None and target.get("dec") is not None
        # Object-name lookup (e.g. "M31") is not implemented yet (see ROADMAP.md),
        # so reject it here rather than accepting the request and failing later
        # in the renderer after the bot has already been told "queued".
        if not has_coords:
            raise ValidationError(
                f"map_type '{map_type}' requires target.ra and target.dec (degrees); "
                "object-name lookup is not supported yet"
            )

    # Optic block — required for optic.
    optic = data.get("optic") or {}
    if not isinstance(optic, dict):
        raise ValidationError("'optic' must be an object with a 'type'")
    if map_type in _OPTIC_REQUIRED and not optic.get("type"):
        raise ValidationError(f"map_type '{map_type}' requires an 'optic' definition (e.g. type=binoculars)")

    options = data.get("options") or {}
    if not isinstance(options, dict):
        raise ValidationError("'options' must be an object")

    return RenderRequest(
        request_id=request_id,
        map_type=map_type,
        dt=dt,
        lat=lat,
        lon=lon,
        target=target,
        optic=optic,
        options=options,
    )
