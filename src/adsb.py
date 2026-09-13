from __future__ import annotations

import heapq
from dataclasses import dataclass, replace
from math import atan2, cos, radians, sin, sqrt
from typing import Any, Mapping, Sequence

import requests

EARTH_RADIUS_NM = 3440.065
LAST_LINE_TEXT = "****Last Line****"


DEFAULT_ADSB_TOP_LEFT_TEMPLATE = "{summary_left}"
DEFAULT_ADSB_TOP_RIGHT_TEMPLATE = (
    "{aircraft_type}  {summary_speed}  {distance}  {altitude}"
)
DEFAULT_ADSB_SCROLL_TEMPLATE = "{detail}"
DEFAULT_ADSB_NEXT_LEFT_TEMPLATE = "{loop_aircraft}"
DEFAULT_ADSB_NEXT_RIGHT_TEMPLATE = "{loop_info}"


@dataclass(frozen=True)
class AdsbAircraft:
    """Display-ready aircraft data from readsb/tar1090 JSON."""

    hex: str
    flight: str
    latitude: float
    longitude: float
    distance_nm: float
    bearing_deg: int
    altitude_ft: int | None
    ground_speed_kt: int | None
    true_air_speed_kt: int | None
    mach: float | None
    track_deg: int | None
    vertical_rate_fpm: int | None
    squawk: str
    aircraft_type: str
    registration: str
    description: str
    seen_seconds: float
    origin: str = ""
    destination: str = ""

    @property
    def route(self) -> str:
        """Return a compact origin-destination route label when known."""
        if not self.origin or not self.destination:
            return ""
        return f"{self.origin}-{self.destination}"

    @property
    def display_name(self) -> str:
        """Return the preferred aircraft label for display."""
        if self.flight:
            return self.flight
        if self.registration:
            return self.registration
        return self.hex.upper()


class AdsbDataError(ValueError):
    """Raised when ADS-B data cannot be parsed or validated."""


class AdsbRouteDataError(ValueError):
    """Raised when ADS-B route lookup data cannot be parsed."""


@dataclass(frozen=True)
class AdsbRoute:
    """Origin and destination route data for an aircraft callsign."""

    callsign: str
    origin: str
    destination: str


def fetch_aircraft_json(
    source_url: str,
    timeout_s: float,
    user_agent: str,
) -> Mapping[str, Any]:
    """Fetch aircraft JSON from a readsb/tar1090 endpoint.

    Args:
        source_url: HTTP URL for the readsb/tar1090 aircraft.json file.
        timeout_s: Maximum request time in seconds.
        user_agent: HTTP User-Agent header for reverse proxies.

    Returns:
        Decoded JSON mapping.

    Raises:
        requests.RequestException: If the HTTP request fails or times out.
        AdsbDataError: If the response is not a JSON object.
    """
    headers = {"User-Agent": user_agent}
    response = requests.get(source_url, headers=headers, timeout=timeout_s)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise AdsbDataError("ADS-B response must be a JSON object")
    return payload


def fetch_route_lookup_json(
    route_url: str,
    aircraft: list[AdsbAircraft],
    timeout_s: float,
    user_agent: str,
) -> Any:
    """Fetch route data from a tar1090-compatible routeset endpoint.

    Args:
        route_url: HTTP URL for the route lookup endpoint.
        aircraft: Aircraft to include in the batched lookup request.
        timeout_s: Maximum request time in seconds.
        user_agent: HTTP User-Agent header for reverse proxies.

    Returns:
        Decoded JSON payload.

    Raises:
        requests.RequestException: If the HTTP request fails or times out.
        AdsbRouteDataError: If the response body is not JSON.
    """
    planes = [
        {
            "callsign": aircraft_item.flight,
            "lat": aircraft_item.latitude,
            "lng": aircraft_item.longitude,
        }
        for aircraft_item in aircraft
        if aircraft_item.flight
    ]
    if not planes:
        return []

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    }
    response = requests.post(
        route_url,
        headers=headers,
        json={"planes": planes},
        timeout=timeout_s,
    )
    response.raise_for_status()
    if not response.content and not response.text:
        return []

    try:
        return response.json()
    except ValueError as err:
        body_preview = response.text[:120].strip() or "<empty response>"
        raise AdsbRouteDataError(
            "ADS-B route response was not JSON "
            f"(HTTP {response.status_code}): {body_preview}"
        ) from err


def enrich_aircraft_routes(
    aircraft: list[AdsbAircraft],
    route_payload: Any,
    route_display: str,
) -> list[AdsbAircraft]:
    """Add route origin and destination to matching aircraft.

    Args:
        aircraft: Parsed aircraft from readsb/tar1090 aircraft JSON.
        route_payload: Decoded tar1090-compatible route lookup response.
        route_display: Route display format: ``iata``, ``icao``, or ``city``.

    Returns:
        Aircraft with origin/destination populated where route data is available.

    Raises:
        AdsbRouteDataError: If the route payload has an unsupported shape.
    """
    routes = parse_route_lookup(route_payload, route_display)
    if not routes:
        return aircraft

    enriched = []
    for aircraft_item in aircraft:
        route = routes.get(_normalize_callsign(aircraft_item.flight))
        if route is None:
            enriched.append(aircraft_item)
            continue
        enriched.append(
            replace(
                aircraft_item,
                origin=route.origin,
                destination=route.destination,
            )
        )
    return enriched


def parse_route_lookup(route_payload: Any, route_display: str) -> dict[str, AdsbRoute]:
    """Parse tar1090-compatible route lookup JSON by callsign.

    Args:
        route_payload: Decoded route lookup response.
        route_display: Route display format: ``iata``, ``icao``, or ``city``.

    Returns:
        Mapping of normalized callsign to route data.

    Raises:
        AdsbRouteDataError: If the route payload has an unsupported shape.
    """
    if route_payload in (None, ""):
        return {}
    route_display = route_display.lower()
    route_items = _route_items(route_payload)

    routes: dict[str, AdsbRoute] = {}
    for item in route_items:
        if not isinstance(item, Mapping):
            continue
        route = _parse_route_item(item, route_display)
        if route is None:
            continue
        routes[_normalize_callsign(route.callsign)] = route
    return routes


def parse_aircraft(
    payload: Mapping[str, Any],
    home_lat: float,
    home_lon: float,
    max_age_s: float,
    max_distance_nm: float | None,
    min_altitude_ft: int | None,
    limit: int,
) -> list[AdsbAircraft]:
    """Parse, filter, and sort ADS-B aircraft by distance.

    Args:
        payload: Decoded readsb/tar1090 aircraft.json payload.
        home_lat: Receiver/display latitude in decimal degrees.
        home_lon: Receiver/display longitude in decimal degrees.
        max_age_s: Maximum accepted ``seen`` age in seconds.
        max_distance_nm: Optional maximum distance in nautical miles.
        min_altitude_ft: Optional minimum altitude in feet.
        limit: Maximum number of aircraft to return.

    Returns:
        Nearest matching aircraft.

    Raises:
        AdsbDataError: If the payload does not contain an aircraft list.
    """
    aircraft_items = payload.get("aircraft")
    if not isinstance(aircraft_items, list):
        raise AdsbDataError("ADS-B response must contain an aircraft list")

    aircraft = [
        parsed
        for item in aircraft_items
        if isinstance(item, Mapping)
        for parsed in [_parse_aircraft_item(item, home_lat, home_lon)]
        if parsed is not None
    ]

    filtered = [
        item
        for item in aircraft
        if _passes_filters(item, max_age_s, max_distance_nm, min_altitude_ft)
    ]
    return heapq.nsmallest(limit, filtered, key=lambda item: item.distance_nm)


def select_featured_aircraft_index(
    aircraft: Sequence[AdsbAircraft],
    now: float,
    interval_s: float,
) -> int:
    """Select which aircraft should receive the full-detail display.

    Args:
        aircraft: Displayable aircraft ordered by distance.
        now: Monotonic time in seconds.
        interval_s: Seconds to keep each aircraft highlighted.

    Returns:
        Zero-based aircraft index, or 0 for an empty sequence.
    """
    if not aircraft:
        return 0

    safe_interval = max(interval_s, 1.0)
    return int(now // safe_interval) % len(aircraft)


def select_secondary_aircraft(
    aircraft: Sequence[AdsbAircraft],
    featured_index: int,
    window: int = 2,
) -> list[tuple[int, AdsbAircraft]]:
    """Return aircraft immediately after the highlighted aircraft.

    Args:
        aircraft: Displayable aircraft ordered by distance.
        featured_index: Zero-based index currently shown with full details.
        window: Maximum number of following aircraft to return.

    Returns:
        One-based original aircraft positions and aircraft records for summary rows.
    """
    if window <= 0:
        return []

    start = featured_index + 1
    end = start + window
    return [
        (idx + 1, aircraft_item)
        for idx, aircraft_item in enumerate(aircraft[start:end], start=start)
    ]


def select_secondary_aircraft_display_rows(
    aircraft: Sequence[AdsbAircraft],
    featured_index: int,
    window: int = 2,
) -> list[tuple[int | None, AdsbAircraft | None]]:
    """Return secondary ADS-B rows, ending with a last-line marker.

    Args:
        aircraft: Displayable aircraft ordered by distance.
        featured_index: Zero-based index currently shown with full details.
        window: Maximum number of lower rows to return.

    Returns:
        One-based original positions and aircraft records for summary rows. A
        ``(None, None)`` row marks the end of the list when it falls within the
        lower-row window.
    """
    if window <= 0:
        return []

    rows: list[tuple[int | None, AdsbAircraft | None]] = [
        (position, aircraft_item)
        for position, aircraft_item in select_secondary_aircraft(
            aircraft,
            featured_index,
            window,
        )
    ]
    if len(rows) < window and featured_index < len(aircraft):
        rows.append((None, None))
    return rows


class _AircraftTemplateContext(dict[str, Any]):
    """Lazy template context that renders unknown variables as empty text."""

    def __init__(
        self,
        aircraft: AdsbAircraft,
        position: int | None,
    ) -> None:
        """Create an aircraft template context.

        Args:
            aircraft: Aircraft used to populate requested variables.
            position: Optional one-based aircraft position for lower detail rows.
        """
        super().__init__()
        self._aircraft = aircraft
        self._position = position

    def __missing__(self, key: str) -> Any:
        value = _aircraft_template_value(key, self._aircraft, self._position)
        self[key] = value
        return value


def build_aircraft_template_text(
    template: str,
    aircraft: AdsbAircraft,
    position: int | None = None,
) -> str:
    """Build aircraft display text from a user-configured template.

    Args:
        template: Python ``str.format_map``-style template containing ADS-B
            variable names in braces, for example ``"{display_name} {altitude}"``.
        aircraft: Aircraft used to populate template variables.
        position: Optional one-based aircraft position for lower detail rows.

    Returns:
        Rendered display text with surrounding whitespace removed. Unknown
        variables render as blank text so a typo does not crash animation.
    """
    try:
        return template.format_map(
            _AircraftTemplateContext(aircraft, position),
        ).strip()
    except (KeyError, TypeError, ValueError):
        return ""


def _aircraft_template_value(
    key: str,
    aircraft: AdsbAircraft,
    position: int | None,
) -> Any:
    match key:
        case "hex":
            return aircraft.hex.upper()
        case "flight":
            return aircraft.flight
        case "display_name":
            return aircraft.display_name
        case "registration":
            return aircraft.registration
        case "route":
            return aircraft.route
        case "origin":
            return aircraft.origin
        case "destination":
            return aircraft.destination
        case "aircraft_type":
            return aircraft.aircraft_type
        case "description":
            return aircraft.description
        case "latitude":
            return aircraft.latitude
        case "longitude":
            return aircraft.longitude
        case "distance_nm":
            return aircraft.distance_nm
        case "distance":
            return f"{aircraft.distance_nm:.0f}nm"
        case "bearing_deg":
            return aircraft.bearing_deg
        case "bearing":
            return format_bearing(aircraft.bearing_deg)
        case "altitude_ft":
            if aircraft.altitude_ft is None:
                return ""
            return aircraft.altitude_ft
        case "altitude":
            return format_altitude(aircraft.altitude_ft)
        case "ground_speed_kt":
            if aircraft.ground_speed_kt is None:
                return ""
            return aircraft.ground_speed_kt
        case "speed":
            return format_speed(aircraft.ground_speed_kt)
        case "ground_speed":
            return format_ground_speed(aircraft.ground_speed_kt)
        case "true_air_speed_kt":
            if aircraft.true_air_speed_kt is None:
                return ""
            return aircraft.true_air_speed_kt
        case "true_air_speed":
            return format_true_air_speed(aircraft.true_air_speed_kt)
        case "summary_speed":
            return format_summary_speed(aircraft)
        case "mach_value":
            return aircraft.mach if aircraft.mach is not None else ""
        case "mach":
            return format_mach(aircraft.mach)
        case "track_deg":
            if aircraft.track_deg is None:
                return ""
            return aircraft.track_deg
        case "heading":
            return format_heading(aircraft.track_deg)
        case "vertical_rate_fpm":
            if aircraft.vertical_rate_fpm is None:
                return ""
            return aircraft.vertical_rate_fpm
        case "vertical_rate":
            return format_vertical_rate(aircraft.vertical_rate_fpm)
        case "squawk":
            return aircraft.squawk
        case "squawk_label":
            return f"sq {aircraft.squawk}" if aircraft.squawk else ""
        case "seen_seconds":
            return aircraft.seen_seconds
        case "seen":
            return format_seen(aircraft.seen_seconds)
        case "position":
            return position or ""
        case "position_ordinal":
            return ordinal_text(position) if position is not None else ""
        case "summary_left":
            return build_summary_left_text(aircraft)
        case "summary_right":
            return build_summary_right_text(aircraft)
        case "summary":
            return build_summary_text(aircraft)
        case "detail":
            return build_detail_text(aircraft)
        case "loop_aircraft":
            if position is None:
                return ""
            return build_loop_aircraft_text(aircraft, position)
        case "loop_info":
            return build_loop_info_text(aircraft)
        case _:
            return ""


def build_summary_left_text(aircraft: AdsbAircraft) -> str:
    """Build the left-side top-row summary text."""
    return "  ".join(
        part for part in [aircraft.display_name, aircraft.route] if part
    )


def build_summary_right_text(aircraft: AdsbAircraft) -> str:
    """Build the right-side top-row summary text."""
    parts = [
        aircraft.registration,
        aircraft.aircraft_type,
        format_summary_speed(aircraft),
        f"{aircraft.distance_nm:.0f}nm",
        format_altitude(aircraft.altitude_ft),
    ]
    return "  ".join(part for part in parts if part)


def build_summary_text(aircraft: AdsbAircraft) -> str:
    """Build the complete top-row summary line for an aircraft."""
    return "    ".join(
        part
        for part in [
            build_summary_left_text(aircraft),
            build_summary_right_text(aircraft),
        ]
        if part
    )


def build_loop_aircraft_text(aircraft: AdsbAircraft, position: int) -> str:
    """Build the lower-row left text for a secondary aircraft."""
    parts = [
        ordinal_text(position),
        aircraft.display_name,
        aircraft.aircraft_type,
    ]
    return "  ".join(part for part in parts if part)


def build_loop_info_text(aircraft: AdsbAircraft) -> str:
    """Build the lower-row right text for a secondary aircraft."""
    parts = [
        format_speed(aircraft.ground_speed_kt),
        f"{aircraft.distance_nm:.0f}nm",
        format_altitude(aircraft.altitude_ft),
    ]
    return " ".join(part for part in parts if part)


def ordinal_text(value: int) -> str:
    """Return the ordinal suffix representation for a positive integer."""
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def format_summary_speed(aircraft: AdsbAircraft) -> str:
    """Format the preferred speed for the top-row aircraft summary."""
    if aircraft.true_air_speed_kt is not None:
        return f"{aircraft.true_air_speed_kt}kt"
    if aircraft.ground_speed_kt is not None:
        return f"{aircraft.ground_speed_kt}kt"
    return ""


def format_true_air_speed(speed_kt: int | None) -> str:
    """Format true airspeed for the scrolling detail line."""
    if speed_kt is None:
        return ""
    return f"tas {speed_kt}kt"


def format_mach(mach: float | None) -> str:
    """Format Mach number for the scrolling detail line."""
    if mach is None:
        return ""
    return f"mach {mach:.2f}"


def format_altitude(altitude_ft: int | None) -> str:
    """Format altitude for the compact display."""
    if altitude_ft is None:
        return "----ft"
    if altitude_ft == 0:
        return "Ground"
    return f"{altitude_ft}ft"


def format_speed(speed_kt: int | None) -> str:
    """Format ground speed for the compact display."""
    if speed_kt is None:
        return "---kt"
    return f"{speed_kt}kt"


def format_ground_speed(speed_kt: int | None) -> str:
    """Format ground speed for the scrolling detail line."""
    if speed_kt is None:
        return ""
    return f"gs {speed_kt}kt"


def format_vertical_rate(vertical_rate_fpm: int | None) -> str:
    """Format vertical rate with an arrow-like prefix for OLED fonts."""
    if vertical_rate_fpm is None:
        return ""
    if vertical_rate_fpm == 0:
        return "level"
    if vertical_rate_fpm > 0:
        return f"climb {vertical_rate_fpm}fpm"
    return f"desc {abs(vertical_rate_fpm)}fpm"


def format_heading(degrees: int | None) -> str:
    """Format track heading with compass point and degrees."""
    if degrees is None:
        return "trk ---"
    return f"{_compass_point(degrees)} {degrees:03d}"


def format_bearing(degrees: int) -> str:
    """Format bearing from receiver to aircraft."""
    return f"brg {degrees:03d}deg"


def format_seen(seconds: float) -> str:
    """Format aircraft seen age."""
    return f"seen {seconds:.0f}s"


def build_detail_text(aircraft: AdsbAircraft) -> str:
    """Build the scrolling detail line for an aircraft."""
    parts = [
        aircraft.description,
        format_bearing(aircraft.bearing_deg),
        format_heading(aircraft.track_deg),
        format_ground_speed(aircraft.ground_speed_kt),
        format_true_air_speed(aircraft.true_air_speed_kt),
        format_mach(aircraft.mach),
        format_vertical_rate(aircraft.vertical_rate_fpm),
        f"sq {aircraft.squawk}" if aircraft.squawk else "",
        aircraft.hex.upper(),
    ]
    return "  ".join(part for part in parts if part)


def _route_items(route_payload: Any) -> list[Any]:
    if isinstance(route_payload, list):
        return route_payload
    if not isinstance(route_payload, Mapping):
        raise AdsbRouteDataError("ADS-B route response must be a list")

    for key in ("routes", "data", "results", "response"):
        value = route_payload.get(key)
        if isinstance(value, list):
            return value

    raise AdsbRouteDataError("ADS-B route response must be a list")


def _parse_route_item(
    item: Mapping[str, Any],
    route_display: str,
) -> AdsbRoute | None:
    callsign = _clean_text(item.get("callsign"))
    if not callsign:
        return None

    origin, destination = _route_endpoints(item, route_display)
    if not origin or not destination:
        return None

    return AdsbRoute(callsign=callsign, origin=origin, destination=destination)


def _route_endpoints(
    item: Mapping[str, Any],
    route_display: str,
) -> tuple[str, str]:
    if route_display == "city":
        return _airport_route_endpoints(item, "location")
    if route_display == "icao":
        origin, destination = _split_route_codes(
            _clean_text(item.get("airport_codes")),
        )
        if origin and destination:
            return origin, destination
        return _airport_route_endpoints(item, "icao")

    origin, destination = _split_route_codes(
        _clean_text(item.get("_airport_codes_iata")),
    )
    if origin and destination:
        return origin, destination
    return _airport_route_endpoints(item, "iata")


def _airport_route_endpoints(
    item: Mapping[str, Any],
    key: str,
) -> tuple[str, str]:
    airports = item.get("_airports")
    if not isinstance(airports, list) or len(airports) < 2:
        return "", ""

    first = airports[0]
    last = airports[-1]
    if not isinstance(first, Mapping) or not isinstance(last, Mapping):
        return "", ""

    return _clean_text(first.get(key)), _clean_text(last.get(key))


def _split_route_codes(route_codes: str) -> tuple[str, str]:
    if not route_codes or "-" not in route_codes:
        return "", ""

    parts = [part.strip() for part in route_codes.split("-") if part.strip()]
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[-1]


def _normalize_callsign(callsign: str) -> str:
    return "".join(callsign.upper().split())


def _parse_aircraft_item(
    item: Mapping[str, Any],
    home_lat: float,
    home_lon: float,
) -> AdsbAircraft | None:
    lat = _optional_float(item.get("lat"))
    lon = _optional_float(item.get("lon"))
    if lat is None or lon is None:
        return None

    hex_value = _clean_text(item.get("hex"))
    if not hex_value:
        return None

    distance_nm = distance_between_nm(home_lat, home_lon, lat, lon)
    bearing_deg = bearing_between_deg(home_lat, home_lon, lat, lon)

    return AdsbAircraft(
        hex=hex_value,
        flight=_clean_text(item.get("flight")),
        latitude=lat,
        longitude=lon,
        distance_nm=distance_nm,
        bearing_deg=bearing_deg,
        altitude_ft=_parse_altitude(item.get("alt_baro", item.get("alt_geom"))),
        ground_speed_kt=_optional_int(item.get("gs")),
        true_air_speed_kt=_optional_int(item.get("tas")),
        mach=_optional_float(item.get("mach")),
        track_deg=_optional_int(item.get("track")),
        vertical_rate_fpm=_optional_int(item.get("baro_rate", item.get("geom_rate"))),
        squawk=_clean_text(item.get("squawk")),
        aircraft_type=_clean_text(item.get("t")),
        registration=_clean_text(item.get("r")),
        description=_clean_text(item.get("desc")),
        seen_seconds=_optional_float(item.get("seen")) or 0.0,
    )


def _passes_filters(
    aircraft: AdsbAircraft,
    max_age_s: float,
    max_distance_nm: float | None,
    min_altitude_ft: int | None,
) -> bool:
    if aircraft.seen_seconds > max_age_s:
        return False
    if max_distance_nm is not None and aircraft.distance_nm > max_distance_nm:
        return False
    if min_altitude_ft is None:
        return True
    if aircraft.altitude_ft is None:
        return False
    return aircraft.altitude_ft >= min_altitude_ft


def distance_between_nm(
    origin_lat: float,
    origin_lon: float,
    target_lat: float,
    target_lon: float,
) -> float:
    """Calculate great-circle distance between coordinates in nautical miles."""
    lat1 = radians(origin_lat)
    lon1 = radians(origin_lon)
    lat2 = radians(target_lat)
    lon2 = radians(target_lon)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    haversine = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )
    return EARTH_RADIUS_NM * 2 * atan2(sqrt(haversine), sqrt(1 - haversine))


def bearing_between_deg(
    origin_lat: float,
    origin_lon: float,
    target_lat: float,
    target_lon: float,
) -> int:
    """Calculate initial bearing from origin to target in degrees."""
    lat1 = radians(origin_lat)
    lat2 = radians(target_lat)
    delta_lon = radians(target_lon - origin_lon)

    y = sin(delta_lon) * cos(lat2)
    x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(delta_lon)
    return int(round((atan2(y, x) * 180 / 3.141592653589793 + 360) % 360))


def _parse_altitude(value: Any) -> int | None:
    if value == "ground":
        return 0
    return _optional_int(value)


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _compass_point(degrees: int) -> str:
    points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return points[int((degrees % 360 + 22.5) // 45) % len(points)]
