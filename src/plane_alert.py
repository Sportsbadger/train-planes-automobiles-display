from __future__ import annotations

import csv
import heapq
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

import requests

LAST_LINE_TEXT = "****Last Line****"

TIMESTAMP_FORMATS = (
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
)

FIELD_ALIASES: Mapping[str, tuple[str, ...]] = {
    "hex": ("hex", "icao", "icao24", "icao_hex", "icao id", "hex id"),
    "tail": ("tail", "tail_number", "registration", "reg", "n-number"),
    "call": ("call", "callsign", "flight", "flight number"),
    "name": ("name", "owner", "owner_name", "operator"),
    "equipment": (
        "equipment",
        "type",
        "aircraft_type",
        "aircraft type",
        "make/model",
        "description",
    ),
    "timestamp": (
        "time:time_at_mindist",
        "time:lastseen",
        "time:firstseen",
        "timestamp",
        "first_seen",
        "last_seen",
        "time_at_mindist",
        "time",
        "date/time",
        "datetime",
    ),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "longitude", "lng"),
    "db_category": ("db category", "db:category", "db_category", "category"),
    "db_tag1": ("db tag1", "db:tag1", "db_tag1", "tag1"),
    "db_tag2": ("db tag2", "db:tag2", "db_tag2", "tag2"),
    "db_tag3": ("db tag3", "db:tag3", "db_tag3", "tag3"),
    "route": ("route", "route:name", "route_name"),
}

JSON_WRAPPER_KEYS = (
    "data",
    "records",
    "alerts",
    "plane_alert",
    "plane-alert",
    "aaData",
)


@dataclass(frozen=True)
class PlaneAlert:
    """Display-ready aircraft row from the Plane-Alert history stream."""

    hex: str
    tail: str
    call: str
    name: str
    equipment: str
    timestamp: datetime | None
    lat: float | None
    lon: float | None
    index: int | None = None
    distance: str = ""
    altitude: str = ""
    db_category: str = ""
    db_tag1: str = ""
    db_tag2: str = ""
    db_tag3: str = ""
    route: str = ""

    @property
    def display_name(self) -> str:
        """Return the preferred label for display."""
        if self.call:
            return self.call.removeprefix("@")
        if self.tail:
            return self.tail
        return self.hex.upper()

    @property
    def display_index(self) -> int | None:
        """Return the one-based row number shown by the Plane-Alert web UI."""
        if self.index is None:
            return None
        return self.index + 1


class PlaneAlertDataError(ValueError):
    """Raised when Plane-Alert data cannot be parsed or validated."""


def fetch_plane_alert_json(
    source_url: str,
    timeout_s: float,
    user_agent: str,
) -> Any:
    """Fetch Plane-Alert data from docker-planefence.

    Args:
        source_url: HTTP URL for the Plane-Alert stream or legacy query endpoint.
        timeout_s: Maximum request time in seconds.
        user_agent: HTTP User-Agent header for reverse proxies.

    Returns:
        Decoded JSON, NDJSON records, or CSV records.

    Raises:
        requests.RequestException: If the HTTP request fails or times out.
        PlaneAlertDataError: If the response cannot be decoded.
    """
    headers = {
        "Accept": (
            "application/x-ndjson, application/json, "
            "text/csv;q=0.9, */*;q=0.1"
        ),
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-GB,en;q=0.9,en-US;q=0.8",
        "User-Agent": user_agent,
    }
    response = requests.get(
        ensure_plane_alert_api_url(source_url),
        headers=headers,
        timeout=timeout_s,
    )
    response.raise_for_status()

    if hasattr(response, "text"):
        return decode_plane_alert_response(
            response.text,
            getattr(response, "status_code", None),
        )

    try:
        return response.json()
    except ValueError as err:
        raise PlaneAlertDataError("Plane-Alert response was not JSON") from err


def ensure_plane_alert_api_url(source_url: str) -> str:
    """Return the live Plane-Alert table stream URL.

    Args:
        source_url: Configured Plane-Alert source URL.

    Returns:
        The original non-Plane-Alert URL, or a canonical ``stream.sh`` URL for
        live Plane-Alert history. When the URL omits a port, the docker-
        planefence Plane-Alert web port ``8083`` is used.
    """
    split_url = urlsplit(source_url)
    if split_url.path.endswith("/cgi/stream.sh"):
        return urlunsplit(
            (
                split_url.scheme,
                _plane_alert_stream_netloc(split_url),
                split_url.path,
                _plane_alert_stream_query(split_url.query),
                split_url.fragment,
            ),
        )

    if not split_url.path.endswith("/pa_query.php"):
        return source_url

    return urlunsplit(
        (
            split_url.scheme,
            _plane_alert_stream_netloc(split_url),
            "/cgi/stream.sh",
            urlencode({"mode": "plane-alert", "date": "all"}),
            split_url.fragment,
        ),
    )


def _plane_alert_stream_netloc(split_url: SplitResult) -> str:
    if split_url.port is not None or split_url.scheme != "http":
        return split_url.netloc

    host = split_url.hostname or split_url.netloc
    if not host:
        return split_url.netloc
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:8083"


def _plane_alert_stream_query(query: str) -> str:
    params = dict(parse_qsl(query, keep_blank_values=True))
    params["mode"] = "plane-alert"
    params["date"] = "all"
    return urlencode(params)


def decode_plane_alert_response(text: str, status_code: int | None = None) -> Any:
    """Decode a Plane-Alert response body.

    Args:
        text: HTTP response body.
        status_code: Optional HTTP status for error messages.

    Returns:
        Decoded JSON, NDJSON records, or CSV records.

    Raises:
        PlaneAlertDataError: If response body cannot be decoded.
    """
    stripped = text.lstrip("\ufeff").strip()
    if not stripped:
        return []

    try:
        return json.loads(stripped)
    except json.JSONDecodeError as json_err:
        ndjson_records = _parse_ndjson_records(stripped)
        if ndjson_records is not None:
            return ndjson_records

        csv_records = _parse_csv_records(stripped)
        if csv_records is not None:
            return csv_records

        body_preview = stripped[:120] or "<empty response>"
        status_text = f"HTTP {status_code}: " if status_code is not None else ""
        raise PlaneAlertDataError(
            "Plane-Alert response was not JSON, NDJSON, or CSV "
            f"({status_text}{body_preview})",
        ) from json_err


def parse_plane_alerts(
    payload: Any,
    max_age_hours: float | None,
    limit: int,
    now: datetime | None = None,
    time_offset_hours: float = 0.0,
) -> list[PlaneAlert]:
    """Parse, filter, de-duplicate, and sort Plane-Alert rows newest-first.

    Args:
        payload: Decoded Plane-Alert JSON, NDJSON, or CSV payload.
        max_age_hours: Optional maximum alert age in hours.
        limit: Maximum number of most recent alerts to return.
        now: Optional current time used by tests.
        time_offset_hours: Hours to add to parsed Plane-Alert timestamps.

    Returns:
        Display-ready Plane-Alert rows, sorted newest/highest-index first.

    Raises:
        PlaneAlertDataError: If the payload shape is unsupported.
    """
    if limit <= 0:
        return []

    records = _extract_records(payload)
    parsed = [
        _apply_time_offset(alert, time_offset_hours)
        for alert in (_parse_record(record) for record in records)
        if alert is not None
    ]
    if not parsed:
        return []

    indexed_rows = [alert for alert in parsed if alert.index is not None]
    sortable_rows = indexed_rows or parsed
    filtered = [
        alert
        for alert in _dedupe_alerts(sortable_rows)
        if _passes_age_filter(alert, max_age_hours, now)
    ]
    return heapq.nlargest(limit, filtered, key=_plane_alert_sort_key)


def _apply_time_offset(alert: PlaneAlert, offset_hours: float) -> PlaneAlert:
    """Return a Plane-Alert row with its timestamp shifted by offset hours.

    Args:
        alert: Parsed Plane-Alert row.
        offset_hours: Number of hours to add to the timestamp.

    Returns:
        Original row when no timestamp/offset applies, otherwise a copied row
        with the adjusted timestamp.
    """
    if alert.timestamp is None or offset_hours == 0:
        return alert
    return PlaneAlert(
        hex=alert.hex,
        tail=alert.tail,
        call=alert.call,
        name=alert.name,
        equipment=alert.equipment,
        timestamp=alert.timestamp + timedelta(hours=offset_hours),
        lat=alert.lat,
        lon=alert.lon,
        index=alert.index,
        distance=alert.distance,
        altitude=alert.altitude,
        db_category=alert.db_category,
        db_tag1=alert.db_tag1,
        db_tag2=alert.db_tag2,
        db_tag3=alert.db_tag3,
        route=alert.route,
    )


def select_plane_alert_scroll_alerts(
    alerts: Sequence[PlaneAlert],
    display_count: int,
) -> list[PlaneAlert]:
    """Return Plane-Alert rows after the highlighted record.

    Args:
        alerts: Newest-first Plane-Alert rows including the highlighted row.
        display_count: Total rows to display, including the highlighted row.

    Returns:
        Lower-row Plane-Alert rows capped by ``display_count``.
    """
    if display_count <= 1:
        return []
    return list(alerts[1:display_count])


def select_featured_plane_alert_index(
    alerts: Sequence[PlaneAlert],
    now: float,
    interval_s: float,
) -> int:
    """Select which Plane-Alert row receives the full-detail display.

    Args:
        alerts: Displayable Plane-Alert rows ordered newest first.
        now: Monotonic time in seconds.
        interval_s: Seconds to keep each row highlighted.

    Returns:
        Zero-based row index, or 0 for an empty sequence.
    """
    if not alerts:
        return 0

    safe_interval = max(interval_s, 1.0)
    return int(now // safe_interval) % len(alerts)


def select_secondary_plane_alert_display_rows(
    alerts: Sequence[PlaneAlert],
    featured_index: int,
    window: int = 2,
) -> list[tuple[int | None, PlaneAlert | None]]:
    """Return lower display rows, ending with a last-line marker when visible.

    Args:
        alerts: Displayable Plane-Alert rows ordered newest first.
        featured_index: Zero-based index currently shown with full details.
        window: Maximum number of lower rows to return.

    Returns:
        One-based original positions and alert rows. A ``(None, None)`` row
        marks the end of the list when it falls within the lower-row window.
    """
    if window <= 0:
        return []
    if featured_index >= len(alerts):
        return []

    start = featured_index + 1
    end = start + window
    rows: list[tuple[int | None, PlaneAlert | None]] = [
        (position + 1, alert)
        for position, alert in enumerate(alerts[start:end], start=start)
    ]
    if len(rows) < window:
        rows.append((None, None))
    return rows


class _PlaneAlertTemplateContext(dict[str, Any]):
    """Lazy template context that renders unknown variables as empty text."""

    def __init__(self, alert: PlaneAlert, position: int | None) -> None:
        """Create a Plane-Alert template context.

        Args:
            alert: Plane-Alert row used to populate requested variables.
            position: Optional one-based alert position for lower detail rows.
        """
        super().__init__()
        self._alert = alert
        self._position = position

    def __missing__(self, key: str) -> Any:
        value = _plane_alert_template_value(key, self._alert, self._position)
        self[key] = value
        return value


def build_plane_alert_template_text(
    template: str,
    alert: PlaneAlert,
    position: int | None = None,
) -> str:
    """Build Plane-Alert display text from a configured template.

    Args:
        template: Python ``str.format_map``-style template containing
            Plane-Alert variable names in braces.
        alert: Plane-Alert row used to populate template variables.
        position: Optional one-based alert position for lower detail rows.

    Returns:
        Rendered display text with unknown variables converted to blank text.
    """
    try:
        return template.format_map(
            _PlaneAlertTemplateContext(alert, position),
        ).strip()
    except (KeyError, TypeError, ValueError):
        return ""


def build_plane_alert_summary_left_text(alert: PlaneAlert) -> str:
    """Build the left-side top-row summary for a Plane-Alert row.

    Args:
        alert: Plane-Alert row to summarize.

    Returns:
        Compact label, tail/hex, and timestamp text.
    """
    return "  ".join(
        part
        for part in [
            alert.display_name,
            alert.tail or alert.hex.upper(),
            format_plane_alert_timestamp(alert.timestamp),
        ]
        if part
    )


def build_plane_alert_summary_text(alert: PlaneAlert) -> str:
    """Build the complete top-row summary for a Plane-Alert row.

    Args:
        alert: Plane-Alert row to summarize.

    Returns:
        Combined left and right summary text.
    """
    return "    ".join(
        part
        for part in [
            build_plane_alert_summary_left_text(alert),
            alert.hex.upper(),
        ]
        if part
    )


def build_plane_alert_loop_alert_text(alert: PlaneAlert, position: int) -> str:
    """Build the lower-row left text for a secondary Plane-Alert row.

    Args:
        alert: Plane-Alert row to summarize.
        position: One-based position in the displayed row set.

    Returns:
        Ordinal, display name, and tail text.
    """
    return "  ".join(
        part
        for part in [_ordinal_text(position), alert.display_name, alert.tail]
        if part
    )


def build_plane_alert_loop_info_text(alert: PlaneAlert) -> str:
    """Build the lower-row right text for a secondary Plane-Alert row.

    Args:
        alert: Plane-Alert row to summarize.

    Returns:
        Equipment/owner and compact timestamp text.
    """
    return "  ".join(
        part
        for part in [
            alert.equipment or alert.name,
            format_plane_alert_timestamp(alert.timestamp),
        ]
        if part
    )


def build_plane_alert_detail_text(alert: PlaneAlert) -> str:
    """Build the scrolling detail line for a Plane-Alert row.

    Args:
        alert: Plane-Alert row to render.

    Returns:
        Detail text containing aircraft, owner, database metadata, route, and
        measurements.
    """
    parts = [
        alert.equipment,
        alert.name,
        alert.db_category,
        alert.db_tag1,
        alert.db_tag2,
        alert.db_tag3,
        alert.route,
    ]
    if alert.distance:
        parts.append(alert.distance)
    if alert.altitude:
        parts.append(alert.altitude)
    return "  ".join(part for part in parts if part)


def format_plane_alert_timestamp(timestamp: datetime | None) -> str:
    """Format a Plane-Alert timestamp for the compact display.

    Args:
        timestamp: Timestamp to format, or ``None`` when unknown.

    Returns:
        ``HH:MM`` text, or ``--:--`` for unknown timestamps.
    """
    if timestamp is None:
        return "--:--"
    return timestamp.strftime("%H:%M")


def _plane_alert_template_value(
    key: str,
    alert: PlaneAlert,
    position: int | None,
) -> Any:
    match key:
        case "hex":
            return alert.hex.upper()
        case "tail":
            return alert.tail
        case "tail_or_hex":
            return alert.tail or alert.hex.upper()
        case "call":
            return alert.call.removeprefix("@")
        case "display_name":
            return alert.display_name
        case "name" | "owner":
            return alert.name
        case "equipment" | "aircraft_type":
            return alert.equipment
        case "timestamp":
            if alert.timestamp is None:
                return ""
            return alert.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        case "time":
            return format_plane_alert_timestamp(alert.timestamp)
        case "date_time":
            if alert.timestamp is None:
                return ""
            return alert.timestamp.strftime("%d %b %H:%M")
        case "latitude":
            return "" if alert.lat is None else f"{alert.lat:.3f}"
        case "longitude":
            return "" if alert.lon is None else f"{alert.lon:.3f}"
        case "index":
            return alert.display_index or ""
        case "raw_index":
            return alert.index if alert.index is not None else ""
        case "distance":
            return alert.distance
        case "altitude":
            return alert.altitude
        case "db_category" | "db category":
            return alert.db_category
        case "db_tag1" | "db tag1":
            return alert.db_tag1
        case "db_tag2" | "db tag2":
            return alert.db_tag2
        case "db_tag3" | "db tag3":
            return alert.db_tag3
        case "route":
            return alert.route
        case "position":
            return position or ""
        case "position_ordinal":
            return _ordinal_text(position) if position is not None else ""
        case "summary_left":
            return build_plane_alert_summary_left_text(alert)
        case "summary_right":
            return alert.hex.upper() or "------"
        case "summary":
            return build_plane_alert_summary_text(alert)
        case "detail":
            return build_plane_alert_detail_text(alert)
        case "loop_alert":
            if position is None:
                return ""
            return build_plane_alert_loop_alert_text(alert, position)
        case "loop_info":
            return build_plane_alert_loop_info_text(alert)
        case "loop_time":
            return format_plane_alert_timestamp(alert.timestamp)
        case _:
            return ""


def _extract_records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        raise PlaneAlertDataError("Plane-Alert response must be a JSON list or object")

    for key in JSON_WRAPPER_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return value

    if all(isinstance(value, Mapping) for value in payload.values()):
        return list(payload.values())

    raise PlaneAlertDataError("Plane-Alert response must contain a records list")


def _parse_record(item: Any) -> PlaneAlert | None:
    if isinstance(item, Mapping):
        return _parse_mapping(item)
    if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
        return _parse_sequence(item)
    return None


def _parse_mapping(item: Mapping[str, Any]) -> PlaneAlert | None:
    normalized = {_normalize_header(key): value for key, value in item.items()}
    hex_value = _first_clean_text(normalized, *FIELD_ALIASES["hex"])
    tail = _first_clean_text(normalized, *FIELD_ALIASES["tail"])
    call = _first_clean_text(normalized, *FIELD_ALIASES["call"])
    if not any((hex_value, tail, call)):
        return None

    return PlaneAlert(
        hex=hex_value.upper(),
        tail=tail,
        call=call,
        name=_first_clean_text(normalized, *FIELD_ALIASES["name"]),
        equipment=_first_clean_text(normalized, *FIELD_ALIASES["equipment"]),
        timestamp=_parse_timestamp(
            _first_clean_text(normalized, *FIELD_ALIASES["timestamp"]),
        ),
        lat=_optional_float(_first_value(normalized, *FIELD_ALIASES["lat"])),
        lon=_optional_float(_first_value(normalized, *FIELD_ALIASES["lon"])),
        index=_optional_int(normalized.get("index")),
        distance=_format_measurement(normalized, "distance", "nm"),
        altitude=_format_measurement(normalized, "altitude", "ft"),
        db_category=_first_clean_text(normalized, *FIELD_ALIASES["db_category"]),
        db_tag1=_first_clean_text(normalized, *FIELD_ALIASES["db_tag1"]),
        db_tag2=_first_clean_text(normalized, *FIELD_ALIASES["db_tag2"]),
        db_tag3=_first_clean_text(normalized, *FIELD_ALIASES["db_tag3"]),
        route=_first_clean_text(normalized, *FIELD_ALIASES["route"]),
    )


def _parse_sequence(item: Sequence[Any]) -> PlaneAlert | None:
    values = [_clean_text(value) for value in item]
    if len(values) < 3:
        return None

    padded = values + [""] * 13
    return _parse_mapping(
        {
            "hex": padded[0],
            "tail": padded[1],
            "name": padded[2],
            "equipment": padded[3],
            "timestamp": padded[4],
            "call": padded[5],
            "lat": padded[6],
            "lon": padded[7],
            "db category": padded[8],
            "db tag1": padded[9],
            "db tag2": padded[10],
            "db tag3": padded[11],
            "route": padded[12],
        },
    )


def _plane_alert_sort_key(alert: PlaneAlert) -> tuple[int, int, datetime]:
    if alert.index is not None:
        return (1, alert.index, alert.timestamp or datetime.min)
    return (0, 0, alert.timestamp or datetime.min)


def _dedupe_alerts(alerts: Iterable[PlaneAlert]) -> list[PlaneAlert]:
    deduped: dict[tuple[str, str, str], PlaneAlert] = {}
    for alert in alerts:
        key = _dedupe_key(alert)
        existing = deduped.get(key)
        if existing is None or _field_score(alert) > _field_score(existing):
            deduped[key] = alert
    return list(deduped.values())


def _dedupe_key(alert: PlaneAlert) -> tuple[str, str, str]:
    if alert.index is not None:
        return ("index", str(alert.index), "")
    return (
        alert.hex.upper(),
        alert.call.upper(),
        alert.timestamp.isoformat() if alert.timestamp is not None else "",
    )


def _field_score(alert: PlaneAlert) -> int:
    return sum(
        1
        for value in (
            alert.hex,
            alert.tail,
            alert.call,
            alert.name,
            alert.equipment,
            alert.timestamp,
            alert.lat,
            alert.lon,
            alert.index,
            alert.distance,
            alert.altitude,
            alert.db_category,
            alert.db_tag1,
            alert.db_tag2,
            alert.db_tag3,
            alert.route,
        )
        if value not in (None, "")
    )


def _passes_age_filter(
    alert: PlaneAlert,
    max_age_hours: float | None,
    now: datetime | None,
) -> bool:
    if max_age_hours is None or alert.timestamp is None:
        return True
    comparison_now = (now or datetime.now()).replace(tzinfo=None)
    return comparison_now - alert.timestamp <= timedelta(hours=max_age_hours)


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    if value.isdigit():
        return _parse_unix_timestamp(value)

    for date_format in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            tzinfo=None,
        )
    except ValueError:
        return None


def _parse_unix_timestamp(value: str) -> datetime | None:
    try:
        timestamp = int(value)
    except ValueError:
        return None
    if timestamp > 9_999_999_999:
        timestamp //= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(
            tzinfo=None,
        )
    except (OSError, OverflowError, ValueError):
        return None


def _parse_ndjson_records(text: str) -> list[dict[str, Any]] | None:
    records: list[dict[str, Any]] = []
    saw_json_line = False
    for line in text.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue
        try:
            value = json.loads(stripped_line)
        except json.JSONDecodeError:
            continue
        saw_json_line = True
        if isinstance(value, Mapping):
            records.append(dict(value))

    if not saw_json_line:
        return None
    return records


def _parse_csv_records(text: str) -> list[dict[str, str]] | None:
    if "<html" in text[:100].lower():
        return None
    sample = text[:2048]
    if not any(delimiter in sample for delimiter in (",", ";", "\t")):
        return None

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    stream = io.StringIO(text.lstrip("\ufeff"), newline="")
    try:
        reader = csv.DictReader(stream, dialect=dialect)
        if reader.fieldnames is None:
            return None
        fieldnames = [_normalize_header(name) for name in reader.fieldnames]
        if not _looks_like_plane_alert_headers(fieldnames):
            return None

        records: list[dict[str, str]] = []
        for row in reader:
            if row is None:
                continue
            normalized_row = {
                _normalize_header(key): _clean_text(value)
                for key, value in row.items()
                if key is not None
            }
            if any(normalized_row.values()):
                records.append(normalized_row)
        return records
    except csv.Error:
        return None


def _looks_like_plane_alert_headers(fieldnames: Sequence[str]) -> bool:
    header_set = set(fieldnames)
    return any(alias in header_set for alias in FIELD_ALIASES["hex"]) and any(
        alias in header_set for alias in FIELD_ALIASES["timestamp"]
    )


def _first_clean_text(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        text = _clean_text(item.get(_normalize_header(key)))
        if text:
            return text
    return ""


def _first_value(item: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(_normalize_header(key))
        if value not in (None, ""):
            return value
    return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_header(value: Any) -> str:
    return _clean_text(value).lstrip("\ufeff").lower().replace("_", " ").strip()


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _format_measurement(
    item: Mapping[str, Any],
    prefix: str,
    default_unit: str,
) -> str:
    value = _clean_text(item.get(f"{prefix}:value"))
    if not value:
        return ""
    unit = _clean_text(item.get(f"{prefix}:unit")) or default_unit
    return f"{value} {unit}"


def _ordinal_text(position: int | None) -> str:
    if position is None:
        return ""
    if 10 <= position % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(position % 10, "th")
    return f"{position}{suffix}"
