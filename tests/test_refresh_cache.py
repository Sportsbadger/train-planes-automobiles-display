from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from refresh_cache import AsyncRefreshCache


def test_duplicate_mode_cycle_requests_run_loader_once() -> None:
    calls = 0

    def load_value() -> str:
        nonlocal calls
        calls += 1
        return "ready"

    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(load_value, 10.0, executor)
        first = cache.start_refresh("train", 3)
        duplicate = cache.start_refresh("train", 3)
        assert first is not None
        first.result()
        assert cache.start_refresh("train", 3) is None

    assert duplicate is None
    assert calls == 1


def test_completed_pending_result_is_not_published_mid_cycle() -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(lambda: "next", 1.0, executor)
        future = cache.start_refresh("adsb", 2)
        assert future is not None
        future.result()
        snapshot = cache.snapshot(9.0)

    assert snapshot.value is None
    assert snapshot.is_refreshing is False


def test_pending_result_is_promoted_at_matching_boundary() -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(lambda: "next", 1.0, executor)
        future = cache.start_refresh("plane-alert", 8)
        assert future is not None
        future.result()
        wrong_boundary = cache.promote("plane-alert", 7, 4.0)
        matching_boundary = cache.promote("plane-alert", 8, 5.0)

    assert wrong_boundary.value is None
    assert matching_boundary.value == "next"
    assert matching_boundary.last_success_monotonic == 5.0


def test_published_value_remains_exact_during_next_load() -> None:
    release = Event()
    values = iter(("first", "second"))

    def load_value() -> str:
        value = next(values)
        if value == "second":
            release.wait(timeout=1.0)
        return value

    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(load_value, 1.0, executor)
        first = cache.start_refresh("train", 1)
        assert first is not None
        first.result()
        assert cache.promote("train", 1, 1.0).value == "first"
        second = cache.start_refresh("train", 2)
        assert second is not None
        assert cache.snapshot(2.0).value == "first"
        release.set()
        second.result()
        assert cache.snapshot(3.0).value == "first"
        assert cache.promote("train", 2, 4.0).value == "second"


def test_refresh_interval_must_be_positive() -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError, match="refresh_interval_s"):
            AsyncRefreshCache(lambda: "ready", 0.0, executor)


def test_ordered_refresh_waits_for_dependency() -> None:
    events: list[str] = []

    def load_adsb() -> str:
        events.append("adsb")
        return "aircraft"

    def load_records() -> str:
        events.append("records")
        return "boards"

    with ThreadPoolExecutor(max_workers=2) as executor:
        adsb = AsyncRefreshCache(load_adsb, 1.0, executor)
        records = AsyncRefreshCache(load_records, 1.0, executor)
        dependency = adsb.start_refresh("adsb-records-source", 4)
        assert dependency is not None
        result = records.start_refresh("adsb-records", 4, after=dependency)
        assert result is not None
        result.result()

    assert events == ["adsb", "records"]


def test_mode_cycle_scheduling_ignores_short_refresh_intervals() -> None:
    calls = {"train": 0, "adsb": 0, "plane-alert": 0}

    def loader(mode: str):
        def load() -> str:
            calls[mode] += 1
            return f"{mode}-{calls[mode]}"

        return load

    with ThreadPoolExecutor(max_workers=3) as executor:
        caches = {
            mode: AsyncRefreshCache(loader(mode), 0.01, executor)
            for mode in calls
        }
        modes = list(caches)
        # This mirrors the main loop: each boundary schedules exactly the next
        # mode, regardless of how many interval checks fit inside a mode run.
        for cycle_id in range(6):
            mode = modes[cycle_id % len(modes)]
            future = caches[mode].start_refresh(mode, cycle_id)
            assert future is not None
            future.result()
            caches[mode].promote(mode, cycle_id, float(cycle_id))
            for _ in range(100):
                caches[mode].snapshot(float(cycle_id) + 0.02)

    assert calls == {"train": 2, "adsb": 2, "plane-alert": 2}
