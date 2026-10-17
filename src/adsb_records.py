from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from adsb import AdsbAircraft, format_altitude, format_speed

RecordWindow = Literal["day", "week", "forever"]
MetricDirection = Literal["max", "min"]

DAY_SECONDS = 24 * 60 * 60
WEEK_SECONDS = 7 * DAY_SECONDS
CURRENT_STORE_VERSION = 2
DEFAULT_OBSERVATION_BUCKET_SECONDS = 5 * 60
DEFAULT_RECORD_WINDOWS: tuple[RecordWindow, ...] = ("day", "week", "forever")


@dataclass(frozen=True)
class AdsbRecordObservation:
    """Single ADS-B observation persisted for rolling record windows."""

    timestamp: float
    hex: str
    label: str
    aircraft_type: str
    altitude_ft: int | None
    speed_kt: int | None
    distance_nm: float
    vertical_rate_fpm: int | None


@dataclass(frozen=True)
class AdsbMetricRecord:
    """Best aircraft observation for one record metric."""

    metric: str
    label: str
    value: float
    unit: str
    aircraft_label: str
    aircraft_type: str
    timestamp: float


@dataclass(frozen=True)
class AdsbRecordBoard:
    """Display-ready ADS-B records for one time window."""

    window: RecordWindow
    title: str
    observation_count: int
    records: list[AdsbMetricRecord]


@dataclass(frozen=True)
class AdsbRecordTrimResult:
    """Summary of an ADS-B record store trim operation."""

    observations_before: int
    observations_after: int
    forever_before: int
    forever_after: int


@dataclass(frozen=True)
class MetricDefinition:
    """Defines how to evaluate one aircraft record metric."""

    key: str
    label: str
    field: str
    direction: MetricDirection
    unit: str
    minimum_value: float | None = None
    maximum_value: float | None = None


METRIC_DEFINITIONS: tuple[MetricDefinition, ...] = (
    MetricDefinition("highest", "Highest", "altitude_ft", "max", "ft"),
    MetricDefinition("lowest", "Lowest", "altitude_ft", "min", "ft"),
    MetricDefinition("fastest", "Fastest", "speed_kt", "max", "kt"),
    MetricDefinition("slowest", "Slowest", "speed_kt", "min", "kt"),
    MetricDefinition("furthest", "Furthest", "distance_nm", "max", "nm"),
    MetricDefinition("nearest", "Nearest", "distance_nm", "min", "nm"),
    MetricDefinition(
        "climb",
        "Climb",
        "vertical_rate_fpm",
        "max",
        "fpm",
        minimum_value=1,
    ),
    MetricDefinition(
        "descent",
        "Descent",
        "vertical_rate_fpm",
        "min",
        "fpm",
        maximum_value=-1,
    ),
)

WINDOW_TITLES: dict[RecordWindow, str] = {
    "day": "Last 24 Hours",
    "week": "Last 7 Days",
    "forever": "All Time",
}

WINDOW_ALIASES: dict[str, RecordWindow] = {
    "24h": "day",
    "24hr": "day",
    "24hrs": "day",
    "24hour": "day",
    "24hours": "day",
    "day": "day",
    "daily": "day",
    "last24h": "day",
    "last24hr": "day",
    "last24hours": "day",
    "7d": "week",
    "7day": "week",
    "7days": "week",
    "week": "week",
    "weekly": "week",
    "last7d": "week",
    "last7days": "week",
    "all": "forever",
    "alltime": "forever",
    "forever": "forever",
}




def normalize_record_windows(
    raw_windows: str | Iterable[str] | None,
) -> list[RecordWindow]:
    """Normalize configured ADS-B record windows for display.

    Args:
        raw_windows: Comma-separated string or iterable of window names.

    Returns:
        Ordered, de-duplicated record windows. Invalid values are ignored, and
        all windows are returned when no valid value is configured.
    """
    if raw_windows is None:
        return list(DEFAULT_RECORD_WINDOWS)

    if isinstance(raw_windows, str):
        raw_values = raw_windows.split(",")
    else:
        raw_values = raw_windows

    windows: list[RecordWindow] = []
    for raw_value in raw_values:
        normalized = _normalize_window_token(str(raw_value))
        if normalized is None or normalized in windows:
            continue
        windows.append(normalized)

    if windows:
        return windows
    return list(DEFAULT_RECORD_WINDOWS)


def filter_record_boards(
    boards: Sequence[AdsbRecordBoard],
    windows: Sequence[RecordWindow],
) -> list[AdsbRecordBoard]:
    """Filter record boards to configured display windows."""
    allowed = set(windows)
    return [board for board in boards if board.window in allowed]


class AdsbRecordStoreError(ValueError):
    """Raised when ADS-B record storage cannot be read or written."""


def update_adsb_record_store(
    store_path: Path,
    aircraft: Iterable[AdsbAircraft],
    now: float | None = None,
    bucket_seconds: int = DEFAULT_OBSERVATION_BUCKET_SECONDS,
) -> list[AdsbRecordBoard]:
    """Persist aircraft observations and return updated record boards.

    Args:
        store_path: JSON file used for durable ADS-B record storage.
        aircraft: ADS-B aircraft observations to add to the store.
        now: Optional Unix timestamp, primarily for tests.
        bucket_seconds: Sampling bucket width. Within each bucket and aircraft,
            only observations that are extrema for at least one metric persist.

    Returns:
        Display-ready record boards for day, week, and forever windows.

    Raises:
        AdsbRecordStoreError: If the existing store is malformed.
    """
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")

    timestamp = time.time() if now is None else now
    store = _load_store(store_path)
    stored_observations = _read_observations(store)
    new_observations = list(
        dict.fromkeys(
            aircraft_to_observation(aircraft_item, timestamp)
            for aircraft_item in aircraft
        )
    )
    observations = sample_observations(
        [*stored_observations, *new_observations],
        bucket_seconds,
    )
    observations = prune_observations(observations, timestamp)

    forever_records = _read_forever_records(store)
    updated_forever_records = merge_forever_records(
        forever_records,
        new_observations,
    )

    persisted_changed = (
        store.get("version") != CURRENT_STORE_VERSION
        or store.get("policy") != {"bucket_seconds": bucket_seconds}
        or observations != stored_observations
        or updated_forever_records != forever_records
    )
    if persisted_changed:
        _write_store(
            store_path,
            {
                "version": CURRENT_STORE_VERSION,
                "policy": {"bucket_seconds": bucket_seconds},
                "observations": [asdict(item) for item in observations],
                "forever": {
                    key: asdict(value)
                    for key, value in updated_forever_records.items()
                },
            },
        )
    return build_record_boards(observations, updated_forever_records, timestamp)


def load_adsb_record_boards(
    store_path: Path,
    now: float | None = None,
) -> list[AdsbRecordBoard]:
    """Load record boards from persistent ADS-B record storage.

    Args:
        store_path: JSON file used for durable ADS-B record storage.
        now: Optional Unix timestamp, primarily for tests.

    Returns:
        Display-ready record boards. Missing storage returns empty boards.

    Raises:
        AdsbRecordStoreError: If the existing store is malformed.
    """
    timestamp = time.time() if now is None else now
    store = _load_store(store_path)
    observations = prune_observations(_read_observations(store), timestamp)
    forever_records = _read_forever_records(store)
    return build_record_boards(observations, forever_records, timestamp)


def trim_adsb_record_store(
    store_path: Path,
    *,
    hex_values: Iterable[str] = (),
    labels: Iterable[str] = (),
    forever_metrics: Iterable[str] = (),
    dry_run: bool = False,
) -> AdsbRecordTrimResult:
    """Remove erroneous observations and all-time records from storage.

    Args:
        store_path: JSON file used for durable ADS-B record storage.
        hex_values: Aircraft hex identifiers to remove from rolling windows.
        labels: Aircraft labels/callsigns to remove from rolling and forever records.
        forever_metrics: All-time metric keys to clear, for example ``highest``.
        dry_run: When true, validate and report counts without writing.

    Returns:
        Counts before and after the trim.

    Raises:
        AdsbRecordStoreError: If the existing store is malformed.
    """
    store = _load_store(store_path)
    observations = _read_observations(store)
    forever_records = _read_forever_records(store)
    normalized_hex_values = {_normalize_match_value(value) for value in hex_values}
    normalized_labels = {_normalize_match_value(value) for value in labels}
    normalized_metrics = {_normalize_match_value(value) for value in forever_metrics}

    trimmed_observations = [
        observation
        for observation in observations
        if not _matches_trim_criteria(
            _normalize_match_value(observation.hex),
            _normalize_match_value(observation.label),
            normalized_hex_values,
            normalized_labels,
        )
    ]
    trimmed_forever_records = {
        key: record
        for key, record in forever_records.items()
        if (
            _normalize_match_value(key) not in normalized_metrics
            and _normalize_match_value(record.aircraft_label) not in normalized_labels
        )
    }

    result = AdsbRecordTrimResult(
        observations_before=len(observations),
        observations_after=len(trimmed_observations),
        forever_before=len(forever_records),
        forever_after=len(trimmed_forever_records),
    )
    if dry_run:
        return result

    _write_store(
        store_path,
        {
            "version": CURRENT_STORE_VERSION,
            "observations": [asdict(item) for item in trimmed_observations],
            "forever": {
                key: asdict(value)
                for key, value in trimmed_forever_records.items()
            },
        },
    )
    return result


def aircraft_to_observation(
    aircraft: AdsbAircraft,
    timestamp: float,
) -> AdsbRecordObservation:
    """Convert one parsed ADS-B aircraft item to a record observation."""
    return AdsbRecordObservation(
        timestamp=timestamp,
        hex=aircraft.hex,
        label=aircraft.display_name,
        aircraft_type=aircraft.aircraft_type,
        altitude_ft=aircraft.altitude_ft,
        speed_kt=aircraft.true_air_speed_kt or aircraft.ground_speed_kt,
        distance_nm=aircraft.distance_nm,
        vertical_rate_fpm=aircraft.vertical_rate_fpm,
    )


def prune_observations(
    observations: Iterable[AdsbRecordObservation],
    now: float,
) -> list[AdsbRecordObservation]:
    """Return observations needed for rolling windows, newest first."""
    cutoff = now - WEEK_SECONDS
    return sorted(
        (item for item in observations if item.timestamp >= cutoff),
        key=lambda item: item.timestamp,
        reverse=True,
    )


def sample_observations(
    observations: Iterable[AdsbRecordObservation],
    bucket_seconds: int = DEFAULT_OBSERVATION_BUCKET_SECONDS,
) -> list[AdsbRecordObservation]:
    """Retain per-aircraft, per-time-bucket metric extrema.

    At most one source observation per metric is retained for each aircraft
    hex and bucket. An observation that wins multiple metrics is stored once,
    bounding growth at ``len(METRIC_DEFINITIONS)`` observations per group
    while preserving every day/week metric candidate.

    Args:
        observations: Observations to sample.
        bucket_seconds: Positive sampling bucket width in seconds.

    Returns:
        Sampled observations ordered newest first.

    Raises:
        ValueError: If ``bucket_seconds`` is not positive.
    """
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")

    groups: dict[tuple[str, int], list[AdsbRecordObservation]] = {}
    for observation in observations:
        bucket = int(observation.timestamp // bucket_seconds)
        key = (observation.hex.casefold(), bucket)
        groups.setdefault(key, []).append(observation)

    retained: list[AdsbRecordObservation] = []
    for group in groups.values():
        winner_ids = _metric_winner_ids(group)
        retained.extend(
            observation
            for observation in group
            if id(observation) in winner_ids
        )
    unique_retained = list(dict.fromkeys(retained))
    return sorted(unique_retained, key=lambda item: item.timestamp, reverse=True)


def merge_forever_records(
    existing: Mapping[str, AdsbMetricRecord],
    observations: Iterable[AdsbRecordObservation],
) -> dict[str, AdsbMetricRecord]:
    """Merge new observations into durable all-time metric records."""
    merged = dict(existing)
    candidates = _accumulate_metric_records(observations)
    for definition in METRIC_DEFINITIONS:
        candidate = candidates.get(definition.key)
        if candidate is None:
            continue
        current = merged.get(definition.key)
        if current is None or _is_better(candidate.value, current.value, definition):
            merged[definition.key] = candidate
    return merged


def build_record_boards(
    observations: Iterable[AdsbRecordObservation],
    forever_records: Mapping[str, AdsbMetricRecord],
    now: float,
) -> list[AdsbRecordBoard]:
    """Build day, week, and forever display boards from stored observations."""
    observations_list = list(observations)
    return [
        _build_rolling_board("day", observations_list, now - DAY_SECONDS),
        _build_rolling_board("week", observations_list, now - WEEK_SECONDS),
        AdsbRecordBoard(
            window="forever",
            title=WINDOW_TITLES["forever"],
            observation_count=len(observations_list),
            records=_ordered_records(forever_records),
        ),
    ]


def record_board_page_count(
    board: AdsbRecordBoard,
    rows_per_page: int = 2,
) -> int:
    """Return display pages needed for one ADS-B record board.

    Args:
        board: Record board to paginate.
        rows_per_page: Maximum metric records shown on each page.

    Returns:
        At least one page for each board, even when still collecting records.
    """
    safe_rows = max(1, rows_per_page)
    if not board.records:
        return 1
    return max(1, (len(board.records) + safe_rows - 1) // safe_rows)


def record_mode_entry_count(
    boards: list[AdsbRecordBoard],
    rows_per_page: int = 2,
) -> int:
    """Return total display pages for all ADS-B record boards."""
    if not boards:
        return 1
    return sum(record_board_page_count(board, rows_per_page) for board in boards)


def select_record_display_page(
    boards: list[AdsbRecordBoard],
    now: float,
    interval_s: float,
    rows_per_page: int = 2,
) -> tuple[AdsbRecordBoard | None, list[AdsbMetricRecord]]:
    """Select the active ADS-B record board and metric rows.

    Args:
        boards: Available day/week/forever record boards.
        now: Elapsed or monotonic seconds used for deterministic paging.
        interval_s: Seconds to keep each metric page active.
        rows_per_page: Maximum metric records shown in lower compact rows.

    Returns:
        Selected board and its visible metric rows. If no boards are available,
        returns ``(None, [])``.
    """
    if not boards:
        return None, []

    safe_interval = max(interval_s, 1.0)
    safe_rows = max(1, rows_per_page)
    page_index = int(now // safe_interval) % record_mode_entry_count(
        boards,
        safe_rows,
    )

    for board in boards:
        board_pages = record_board_page_count(board, safe_rows)
        if page_index >= board_pages:
            page_index -= board_pages
            continue
        start = page_index * safe_rows
        return board, board.records[start:start + safe_rows]

    return boards[0], boards[0].records[:safe_rows]


def select_record_board_index(
    boards: list[AdsbRecordBoard],
    now: float,
    interval_s: float,
) -> int:
    """Select which ADS-B record board should be displayed."""
    board, _rows = select_record_display_page(boards, now, interval_s)
    if board is None:
        return 0
    return boards.index(board)


def build_record_summary_text(board: AdsbRecordBoard) -> str:
    """Build compact scrolling summary text for a record board."""
    if not board.records:
        return "No ADS-B records collected yet"
    return "  ".join(format_record_line(record) for record in board.records)


def format_record_line(record: AdsbMetricRecord) -> str:
    """Format one ADS-B metric record for display."""
    return f"{record.label} {format_record_value(record)} {record.aircraft_label}"


def format_record_value(record: AdsbMetricRecord) -> str:
    """Format a metric record value with display units."""
    if record.unit == "ft":
        return format_altitude(int(record.value))
    if record.unit == "kt":
        return format_speed(int(record.value))
    if record.unit == "nm":
        return f"{record.value:.1f}nm"
    if record.metric == "descent":
        return f"-{abs(int(record.value))}fpm"
    return f"{int(record.value)}fpm"


def _normalize_match_value(value: str) -> str:
    return value.strip().casefold()


def _matches_trim_criteria(
    hex_value: str,
    label: str,
    hex_values: set[str],
    labels: set[str],
) -> bool:
    if hex_value and hex_value in hex_values:
        return True
    if label and label in labels:
        return True
    return False


def _normalize_window_token(raw_value: str) -> RecordWindow | None:
    normalized = "".join(
        character
        for character in raw_value.strip().lower()
        if character.isalnum()
    )
    if not normalized:
        return None
    return WINDOW_ALIASES.get(normalized)


def _build_rolling_board(
    window: RecordWindow,
    observations: list[AdsbRecordObservation],
    cutoff: float,
) -> AdsbRecordBoard:
    window_observations = [item for item in observations if item.timestamp >= cutoff]
    records = _ordered_records(_accumulate_metric_records(window_observations))
    return AdsbRecordBoard(
        window=window,
        title=WINDOW_TITLES[window],
        observation_count=len(window_observations),
        records=records,
    )


def _ordered_records(
    records: Mapping[str, AdsbMetricRecord],
) -> list[AdsbMetricRecord]:
    return [
        records[definition.key]
        for definition in METRIC_DEFINITIONS
        if definition.key in records
    ]


def _accumulate_metric_records(
    observations: Iterable[AdsbRecordObservation],
) -> dict[str, AdsbMetricRecord]:
    """Evaluate all metric definitions in one pass over observations."""
    records: dict[str, AdsbMetricRecord] = {}
    for observation in observations:
        for definition in METRIC_DEFINITIONS:
            raw_value = getattr(observation, definition.field)
            if raw_value is None:
                continue
            value = float(raw_value)
            if not _metric_value_allowed(value, definition):
                continue
            current = records.get(definition.key)
            if current is not None and not _is_better(
                value,
                current.value,
                definition,
            ):
                continue
            records[definition.key] = AdsbMetricRecord(
                metric=definition.key,
                label=definition.label,
                value=value,
                unit=definition.unit,
                aircraft_label=observation.label,
                aircraft_type=observation.aircraft_type,
                timestamp=observation.timestamp,
            )
    return records


def _metric_winner_ids(
    observations: Iterable[AdsbRecordObservation],
) -> set[int]:
    """Return identities of observations winning any configured metric."""
    winners: dict[str, tuple[float, AdsbRecordObservation]] = {}
    for observation in observations:
        for definition in METRIC_DEFINITIONS:
            raw_value = getattr(observation, definition.field)
            if raw_value is None:
                continue
            value = float(raw_value)
            if not _metric_value_allowed(value, definition):
                continue
            current = winners.get(definition.key)
            if current is None or _is_better(value, current[0], definition):
                winners[definition.key] = (value, observation)
    return {id(observation) for _value, observation in winners.values()}


def _metric_value_allowed(value: float, definition: MetricDefinition) -> bool:
    if definition.minimum_value is not None and value < definition.minimum_value:
        return False
    if definition.maximum_value is not None and value > definition.maximum_value:
        return False
    return True


def _is_better(
    candidate: float,
    current: float,
    definition: MetricDefinition,
) -> bool:
    if definition.direction == "max":
        return candidate > current
    return candidate < current


def _load_store(store_path: Path) -> dict[str, Any]:
    if not store_path.exists():
        return {"version": CURRENT_STORE_VERSION, "observations": [], "forever": {}}
    try:
        with store_path.open("r", encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
    except json.JSONDecodeError as err:
        raise AdsbRecordStoreError(
            f"ADS-B records store is not valid JSON: {store_path}"
        ) from err
    except OSError as err:
        raise AdsbRecordStoreError(
            f"ADS-B records store could not be read: {store_path}"
        ) from err
    if not isinstance(payload, dict):
        raise AdsbRecordStoreError("ADS-B records store must be a JSON object")
    return payload


def _write_store(store_path: Path, payload: Mapping[str, Any]) -> None:
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = store_path.with_suffix(f"{store_path.suffix}.tmp")
        with temp_path.open("w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, ensure_ascii=False, separators=(",", ":"))
        temp_path.replace(store_path)
    except OSError as err:
        raise AdsbRecordStoreError(
            f"ADS-B records store could not be written: {store_path}"
        ) from err


def _read_observations(store: Mapping[str, Any]) -> list[AdsbRecordObservation]:
    raw_observations = store.get("observations", [])
    if not isinstance(raw_observations, list):
        raise AdsbRecordStoreError("ADS-B records observations must be a list")
    observations = []
    for item in raw_observations:
        if not isinstance(item, Mapping):
            continue
        observation = _parse_observation(item)
        if observation is not None:
            observations.append(observation)
    return observations


def _read_forever_records(store: Mapping[str, Any]) -> dict[str, AdsbMetricRecord]:
    raw_records = store.get("forever", {})
    if not isinstance(raw_records, Mapping):
        raise AdsbRecordStoreError("ADS-B forever records must be an object")
    records = {}
    for key, value in raw_records.items():
        if not isinstance(key, str) or not isinstance(value, Mapping):
            continue
        record = _parse_metric_record(value)
        if record is not None:
            records[key] = record
    return records


def _parse_observation(item: Mapping[str, Any]) -> AdsbRecordObservation | None:
    timestamp = _optional_float(item.get("timestamp"))
    label = _clean_text(item.get("label"))
    hex_value = _clean_text(item.get("hex"))
    distance_nm = _optional_float(item.get("distance_nm"))
    if timestamp is None or not label or not hex_value or distance_nm is None:
        return None
    return AdsbRecordObservation(
        timestamp=timestamp,
        hex=hex_value,
        label=label,
        aircraft_type=_clean_text(item.get("aircraft_type")),
        altitude_ft=_optional_int(item.get("altitude_ft")),
        speed_kt=_optional_int(item.get("speed_kt")),
        distance_nm=distance_nm,
        vertical_rate_fpm=_optional_int(item.get("vertical_rate_fpm")),
    )


def _parse_metric_record(item: Mapping[str, Any]) -> AdsbMetricRecord | None:
    metric = _clean_text(item.get("metric"))
    label = _clean_text(item.get("label"))
    value = _optional_float(item.get("value"))
    unit = _clean_text(item.get("unit"))
    aircraft_label = _clean_text(item.get("aircraft_label"))
    timestamp = _optional_float(item.get("timestamp"))
    if not metric or not label or value is None or not unit or not aircraft_label:
        return None
    if timestamp is None:
        return None
    return AdsbMetricRecord(
        metric=metric,
        label=label,
        value=value,
        unit=unit,
        aircraft_label=aircraft_label,
        aircraft_type=_clean_text(item.get("aircraft_type")),
        timestamp=timestamp,
    )


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
