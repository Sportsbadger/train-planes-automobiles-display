import json
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT / "src"))

from adsb import AdsbAircraft  # noqa: E402
from adsb_records import (  # noqa: E402
    AdsbRecordBoard,
    AdsbRecordObservation,
    AdsbMetricRecord,
    build_record_boards,
    build_record_summary_text,
    filter_record_boards,
    load_adsb_record_boards,
    merge_forever_records,
    normalize_record_windows,
    record_mode_entry_count,
    select_record_display_page,
    sample_observations,
    trim_adsb_record_store,
    update_adsb_record_store,
)


def aircraft(
    hex_value: str,
    label: str,
    altitude_ft: int | None,
    speed_kt: int | None,
    distance_nm: float,
    vertical_rate_fpm: int | None = None,
) -> AdsbAircraft:
    return AdsbAircraft(
        hex=hex_value,
        flight=label,
        latitude=51.0,
        longitude=-0.1,
        distance_nm=distance_nm,
        bearing_deg=90,
        altitude_ft=altitude_ft,
        ground_speed_kt=speed_kt,
        true_air_speed_kt=None,
        mach=None,
        track_deg=180,
        vertical_rate_fpm=vertical_rate_fpm,
        squawk="",
        aircraft_type="A320",
        registration="",
        description="",
        seen_seconds=1.0,
    )


def test_update_adsb_record_store_builds_rolling_and_all_time_records(tmp_path):
    store_path = tmp_path / "adsb-records.json"

    boards = update_adsb_record_store(
        store_path,
        [
            aircraft("aaa111", "LOW1", 2_000, 150, 2.5, -500),
            aircraft("bbb222", "HIGH1", 39_000, 480, 44.2, 1_600),
        ],
        now=1_000_000.0,
    )

    day = boards[0]
    assert day.window == "day"
    assert day.title == "Last 24 Hours"
    assert day.observation_count == 2
    assert day.records[0].metric == "highest"
    assert day.records[0].aircraft_label == "HIGH1"
    assert day.records[2].metric == "fastest"
    assert day.records[2].value == 480
    assert "Furthest 44.2nm HIGH1" in build_record_summary_text(day)

    update_adsb_record_store(
        store_path,
        [aircraft("ccc333", "OLDER", 45_000, 510, 90.0)],
        now=1_000_000.0 - (8 * 24 * 60 * 60),
    )

    reloaded = load_adsb_record_boards(store_path, now=1_000_000.0)
    all_time = reloaded[2]
    assert all_time.window == "forever"
    assert all_time.title == "All Time"
    assert all_time.records[0].aircraft_label == "OLDER"
    assert all_time.records[4].aircraft_label == "OLDER"


def metric_record(metric: str, value: float) -> AdsbMetricRecord:
    return AdsbMetricRecord(
        metric=metric,
        label=metric.title(),
        value=value,
        unit="ft",
        aircraft_label=f"{metric.upper()}1",
        aircraft_type="A320",
        timestamp=1_000.0,
    )


def test_select_record_display_page_keeps_full_window_cycle_together():
    boards = [
        AdsbRecordBoard(
            window="day",
            title="Last 24 Hours",
            observation_count=4,
            records=[
                metric_record("highest", 40_000),
                metric_record("lowest", 1_000),
                metric_record("fastest", 500),
                metric_record("slowest", 120),
                metric_record("furthest", 80),
                metric_record("nearest", 1),
                metric_record("climb", 2_000),
                metric_record("descent", -1_500),
            ],
        ),
        AdsbRecordBoard(
            window="week",
            title="Last 7 Days",
            observation_count=1,
            records=[metric_record("highest", 41_000)],
        ),
    ]

    assert record_mode_entry_count(boards) == 5

    expected_day_pages = [
        (0.0, ["highest", "lowest"]),
        (10.0, ["fastest", "slowest"]),
        (20.0, ["furthest", "nearest"]),
        (30.0, ["climb", "descent"]),
    ]
    for now, expected_metrics in expected_day_pages:
        board, rows = select_record_display_page(
            boards,
            now=now,
            interval_s=10.0,
        )
        assert board is boards[0]
        assert [record.metric for record in rows] == expected_metrics

    week_board, week_rows = select_record_display_page(
        boards,
        now=40.0,
        interval_s=10.0,
    )

    assert week_board is boards[1]
    assert [record.metric for record in week_rows] == ["highest"]


def test_normalize_record_windows_accepts_requested_totals():
    assert normalize_record_windows("24hr,7 day,All Time") == [
        "day",
        "week",
        "forever",
    ]
    assert normalize_record_windows("all-time,24 hours,all") == [
        "forever",
        "day",
    ]
    assert normalize_record_windows("unknown") == ["day", "week", "forever"]


def test_filter_record_boards_uses_configured_windows():
    boards = [
        AdsbRecordBoard("day", "Last 24 Hours", 0, []),
        AdsbRecordBoard("week", "Last 7 Days", 0, []),
        AdsbRecordBoard("forever", "All Time", 0, []),
    ]

    filtered = filter_record_boards(boards, ["day", "forever"])

    assert [board.window for board in filtered] == ["day", "forever"]


def test_trim_adsb_record_store_removes_matching_entries(tmp_path):
    store_path = tmp_path / "adsb-records.json"
    update_adsb_record_store(
        store_path,
        [
            aircraft("bad123", "BADFLT", 99_000, 900, 500.0),
            aircraft("good12", "GOOD", 30_000, 450, 20.0),
        ],
        now=1_000_000.0,
    )

    result = trim_adsb_record_store(
        store_path,
        hex_values=["BAD123"],
        labels=["BADFLT"],
        forever_metrics=["highest", "fastest", "furthest"],
    )
    boards = load_adsb_record_boards(store_path, now=1_000_000.0)

    assert result.observations_before == 2
    assert result.observations_after == 1
    assert all(
        record.aircraft_label != "BADFLT"
        for board in boards
        for record in board.records
    )


def test_trim_adsb_record_store_dry_run_does_not_write(tmp_path):
    store_path = tmp_path / "adsb-records.json"
    update_adsb_record_store(
        store_path,
        [aircraft("bad123", "BADFLT", 99_000, 900, 500.0)],
        now=1_000_000.0,
    )

    result = trim_adsb_record_store(
        store_path,
        hex_values=["bad123"],
        forever_metrics=["highest"],
        dry_run=True,
    )
    boards = load_adsb_record_boards(store_path, now=1_000_000.0)

    assert result.observations_after == 0
    assert any(
        record.aircraft_label == "BADFLT"
        for board in boards
        for record in board.records
    )


def observation(
    timestamp: float,
    *,
    hex_value: str = "abc123",
    altitude: int = 10_000,
    speed: int = 200,
    distance: float = 10.0,
    vertical_rate: int = 0,
) -> AdsbRecordObservation:
    return AdsbRecordObservation(
        timestamp=timestamp,
        hex=hex_value,
        label=hex_value.upper(),
        aircraft_type="A320",
        altitude_ft=altitude,
        speed_kt=speed,
        distance_nm=distance,
        vertical_rate_fpm=vertical_rate,
    )


def test_sampling_deduplicates_sightings_but_preserves_metric_extrema():
    sightings = [
        observation(100, altitude=10_000, speed=300, vertical_rate=-500),
        observation(101, altitude=20_000, speed=200, vertical_rate=800),
        observation(101, altitude=20_000, speed=200, vertical_rate=800),
    ]

    sampled = sample_observations(sightings, bucket_seconds=300)

    assert len(sampled) == 2
    assert {item.altitude_ft for item in sampled} == {10_000, 20_000}
    assert {item.speed_kt for item in sampled} == {200, 300}
    assert {item.vertical_rate_fpm for item in sampled} == {-500, 800}


def test_sampling_separates_aircraft_and_bucket_boundaries():
    sightings = [
        observation(299, altitude=10_000),
        observation(300, altitude=20_000),
        observation(301, hex_value="def456", altitude=30_000),
    ]

    sampled = sample_observations(sightings, bucket_seconds=300)

    assert len(sampled) == 3


def test_store_migrates_legacy_schema_atomically(tmp_path):
    store_path = tmp_path / "records.json"
    legacy = {
        "version": 1,
        "observations": [
            {
                "timestamp": 1_000.0,
                "hex": "abc123",
                "label": "LEGACY",
                "aircraft_type": "A320",
                "altitude_ft": 20_000,
                "speed_kt": 300,
                "distance_nm": 12.0,
                "vertical_rate_fpm": 0,
            }
        ],
        "forever": {},
    }
    store_path.write_text(json.dumps(legacy), encoding="utf-8")

    update_adsb_record_store(store_path, [], now=1_000.0)

    migrated = json.loads(store_path.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    assert migrated["policy"] == {"bucket_seconds": 300}
    assert migrated["observations"] == legacy["observations"]
    assert not store_path.with_suffix(".json.tmp").exists()


def test_sampling_matches_unsampled_metric_results(tmp_path):
    sightings = [
        aircraft("abc123", "ONE", 1_000, 400, 30.0, -900),
        aircraft("abc123", "ONE", 40_000, 100, 2.0, 1_200),
    ]

    boards = update_adsb_record_store(
        tmp_path / "records.json",
        sightings,
        now=10_000.0,
    )
    values = {record.metric: record.value for record in boards[0].records}

    assert values == {
        "highest": 40_000,
        "lowest": 1_000,
        "fastest": 400,
        "slowest": 100,
        "furthest": 30.0,
        "nearest": 2.0,
        "climb": 1_200,
        "descent": -900,
    }


def test_store_growth_is_bounded_across_many_refreshes(tmp_path):
    store_path = tmp_path / "records.json"
    start = 1_000_000.0
    bucket_seconds = 60 * 60
    for refresh in range(190):
        update_adsb_record_store(
            store_path,
            [aircraft("abc123", "ONE", refresh, refresh, float(refresh))],
            now=start + (refresh * bucket_seconds),
            bucket_seconds=bucket_seconds,
        )

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    maximum_buckets = (7 * 24 * 60 * 60 // bucket_seconds) + 1
    assert len(payload["observations"]) <= maximum_buckets * 8


def test_large_synthetic_week_sampling_and_board_build_is_linear():
    now = 2_000_000.0
    sightings = [
        observation(
            now - (index % (7 * 24 * 60 * 60)),
            hex_value=f"{index % 250:06x}",
            altitude=index % 45_000,
            speed=index % 600,
            distance=float(index % 100),
            vertical_rate=(index % 4_000) - 2_000,
        )
        for index in range(50_000)
    ]

    started = time.perf_counter()
    sampled = sample_observations(sightings)
    boards = build_record_boards(
        sampled,
        merge_forever_records({}, sightings),
        now,
    )
    elapsed = time.perf_counter() - started

    assert len(boards) == 3
    assert len(sampled) <= 250 * ((7 * 24 * 60 * 60 // 300) + 1) * 8
    assert elapsed < 5.0
