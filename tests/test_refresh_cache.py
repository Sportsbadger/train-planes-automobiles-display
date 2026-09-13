from concurrent.futures import ThreadPoolExecutor

import pytest

from refresh_cache import AsyncRefreshCache


def test_refresh_if_due_serves_completed_value_without_blocking() -> None:
    calls = 0

    def load_value() -> str:
        nonlocal calls
        calls += 1
        return "ready"

    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(load_value, 10.0, executor)
        cache.refresh_if_due(0.0)
        executor.shutdown(wait=True)

        snapshot = cache.snapshot(1.0)

    assert snapshot.value == "ready"
    assert snapshot.is_refreshing is False
    assert snapshot.last_success_monotonic == 1.0
    assert snapshot.last_error is None
    assert calls == 1


def test_refresh_if_due_does_not_start_again_before_interval() -> None:
    calls = 0

    def load_value() -> str:
        nonlocal calls
        calls += 1
        return f"value-{calls}"

    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(load_value, 10.0, executor)
        cache.refresh_if_due(0.0)
        executor.shutdown(wait=True)
        assert cache.snapshot(1.0).value == "value-1"

        cache.refresh_if_due(5.0)
        snapshot = cache.snapshot(5.0)

    assert snapshot.value == "value-1"
    assert snapshot.is_refreshing is False
    assert calls == 1


def test_refresh_if_due_records_loader_errors() -> None:
    def load_value() -> str:
        raise ValueError("bad source")

    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(load_value, 10.0, executor)
        cache.refresh_if_due(0.0)
        executor.shutdown(wait=True)
        snapshot = cache.snapshot(1.0)

    assert snapshot.value is None
    assert snapshot.is_refreshing is False
    assert isinstance(snapshot.last_error, ValueError)


def test_refresh_interval_must_be_positive() -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(ValueError, match="refresh_interval_s"):
            AsyncRefreshCache(lambda: "ready", 0.0, executor)


def test_refresh_interval_is_available_for_prefetch_policy() -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(lambda: "ready", 30.0, executor)

        assert cache.refresh_interval_s == 30.0


def test_refresh_if_stale_reuses_recent_value() -> None:
    calls = 0

    def load_value() -> str:
        nonlocal calls
        calls += 1
        return f"value-{calls}"

    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(load_value, 10.0, executor)
        cache.refresh_if_stale(0.0, 5.0)
        executor.shutdown(wait=True)
        assert cache.snapshot(1.0).value == "value-1"

        cache.refresh_if_stale(3.0, 5.0)

    assert calls == 1


def test_refresh_if_stale_validates_maximum_age() -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        cache = AsyncRefreshCache(lambda: "ready", 10.0, executor)
        with pytest.raises(ValueError, match="maximum_age_s"):
            cache.refresh_if_stale(0.0, -1.0)
