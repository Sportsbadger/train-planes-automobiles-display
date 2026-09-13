from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CacheSnapshot(Generic[T]):
    """Current cache state returned to the display loop."""

    value: T | None
    is_refreshing: bool
    last_success_monotonic: float | None
    last_error: Exception | None


class AsyncRefreshCache(Generic[T]):
    """Refresh display data in a background thread and serve the last result.

    The display loop must stay fast enough to keep OLED animations smooth. This
    cache lets network loaders run off the render path while the UI keeps using
    the latest completed result.
    """

    def __init__(
        self,
        loader: Callable[[], T],
        refresh_interval_s: float,
        executor: ThreadPoolExecutor,
    ) -> None:
        """Create an async refresh cache.

        Args:
            loader: Callable that fetches and parses one data source.
            refresh_interval_s: Minimum seconds between refresh attempts.
            executor: Shared executor for background network work.
        """
        if refresh_interval_s <= 0:
            raise ValueError("refresh_interval_s must be greater than zero")

        self._loader = loader
        self._refresh_interval_s = refresh_interval_s
        self._executor = executor
        self._lock = Lock()
        self._future: Future[T] | None = None
        self._value: T | None = None
        self._last_attempt_monotonic: float | None = None
        self._last_success_monotonic: float | None = None
        self._last_error: Exception | None = None

    @property
    def refresh_interval_s(self) -> float:
        """Return the configured minimum interval between refresh attempts."""
        return self._refresh_interval_s

    def refresh_if_due(self, now: float, *, force: bool = False) -> None:
        """Start a background refresh if no refresh is active and one is due.

        Args:
            now: Current monotonic timestamp.
            force: Start immediately regardless of the refresh interval.
        """
        with self._lock:
            self._collect_completed_locked(now)
            if self._future is not None:
                return
            if not force and not self._is_due_locked(now):
                return
            self._last_attempt_monotonic = now
            self._future = self._executor.submit(self._loader)

    def snapshot(self, now: float) -> CacheSnapshot[T]:
        """Return the latest completed value and refresh status.

        Args:
            now: Current monotonic timestamp.
        """
        with self._lock:
            self._collect_completed_locked(now)
            return CacheSnapshot(
                value=self._value,
                is_refreshing=self._future is not None,
                last_success_monotonic=self._last_success_monotonic,
                last_error=self._last_error,
            )

    def refresh_if_stale(self, now: float, maximum_age_s: float) -> None:
        """Refresh when no usable value exists or its age exceeds a limit.

        Args:
            now: Current monotonic timestamp.
            maximum_age_s: Maximum acceptable age of the current value.
        """
        if maximum_age_s < 0:
            raise ValueError("maximum_age_s must not be negative")

        with self._lock:
            self._collect_completed_locked(now)
            if self._future is not None:
                return
            is_stale = (
                self._value is None
                or self._last_success_monotonic is None
                or now - self._last_success_monotonic >= maximum_age_s
            )
            if not is_stale:
                return
            self._last_attempt_monotonic = now
            self._future = self._executor.submit(self._loader)

    def _is_due_locked(self, now: float) -> bool:
        if self._last_attempt_monotonic is None:
            return True
        return now - self._last_attempt_monotonic >= self._refresh_interval_s

    def _collect_completed_locked(self, now: float) -> None:
        if self._future is None or not self._future.done():
            return

        future = self._future
        self._future = None
        try:
            self._value = future.result()
            self._last_success_monotonic = now
            self._last_error = None
        except Exception as err:
            self._last_error = err
