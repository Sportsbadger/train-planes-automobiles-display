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


def test_refresh_if_stale_reuses_in_flight_prefetch() -> None:
    calls = 0
    release_loader = Event()

    def load_value() -> str:
        nonlocal calls
        calls += 1
        release_loader.wait(timeout=1.0)
        return "ready"

    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(load_value, 10.0, executor)
        cache.refresh_if_stale(0.0, 5.0)
        cache.refresh_if_stale(10.0, 5.0)
        release_loader.set()
        executor.shutdown(wait=True)

        snapshot = cache.snapshot(11.0)

    assert snapshot.value == "ready"
    assert calls == 1


def test_refresh_if_stale_validates_maximum_age() -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(lambda: "ready", 10.0, executor)
        with pytest.raises(ValueError, match="maximum_age_s"):
            cache.refresh_if_stale(0.0, -1.0)
