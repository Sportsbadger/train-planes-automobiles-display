from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Generic, TypeVar

T = TypeVar("T")
CycleKey = tuple[str, int]


@dataclass(frozen=True)
class CacheSnapshot(Generic[T]):
    """Current published cache state returned to the display loop."""

    value: T | None
    is_refreshing: bool
    last_success_monotonic: float | None
    last_error: Exception | None


class AsyncRefreshCache(Generic[T]):
    """Load values asynchronously and publish them only at mode boundaries."""

    def __init__(
        self,
        loader: Callable[[], T],
        refresh_interval_s: float,
        executor: ThreadPoolExecutor,
    ) -> None:
        """Create an asynchronous mode-cycle cache.

        Args:
            loader: Callable that fetches and parses one data source.
            refresh_interval_s: Retained source refresh configuration.
            executor: Shared executor for background work.
        """
        if refresh_interval_s <= 0:
            raise ValueError("refresh_interval_s must be greater than zero")

        self._loader = loader
        self._refresh_interval_s = refresh_interval_s
        self._executor = executor
        self._lock = Lock()
        self._future: Future[T] | None = None
        self._future_key: CycleKey | None = None
        self._requested_cycles: set[CycleKey] = set()
        self._pending_key: CycleKey | None = None
        self._pending_value: T | None = None
        self._value: T | None = None
        self._last_success_monotonic: float | None = None
        self._last_error: Exception | None = None

    @property
    def refresh_interval_s(self) -> float:
        """Return the configured source refresh interval."""
        return self._refresh_interval_s

    @property
    def active_future(self) -> Future[T] | None:
        """Return the currently running future, if any."""
        with self._lock:
            self._collect_completed_locked()
            return self._future

    def start_refresh(
        self,
        mode: str,
        cycle_id: int,
        *,
        after: Future[object] | None = None,
    ) -> Future[T] | None:
        """Start at most one load for a mode-cycle pair.

        Args:
            mode: Mode whose upcoming entry will consume the value.
            cycle_id: Identifier of that upcoming mode entry.
            after: Optional work which must finish before the loader runs.

        Returns:
            The submitted future, or ``None`` when rejected as a duplicate or
            because another refresh is still running.
        """
        key = (mode, cycle_id)
        with self._lock:
            self._collect_completed_locked()
            if key in self._requested_cycles or self._future is not None:
                return None
            self._requested_cycles.add(key)
            self._future_key = key

            def ordered_load() -> T:
                if after is not None:
                    after.result()
                return self._loader()

            self._future = self._executor.submit(ordered_load)
            return self._future

    def promote(self, mode: str, cycle_id: int, now: float) -> CacheSnapshot[T]:
        """Publish a completed result for the specified mode boundary."""
        key = (mode, cycle_id)
        with self._lock:
            self._collect_completed_locked()
            if self._pending_key == key:
                self._value = self._pending_value
                self._pending_key = None
                self._pending_value = None
                self._last_success_monotonic = now
            return self._snapshot_locked()

    def snapshot(self, now: float) -> CacheSnapshot[T]:
        """Return the published value without publishing completed work."""
        del now
        with self._lock:
            self._collect_completed_locked()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> CacheSnapshot[T]:
        return CacheSnapshot(
            value=self._value,
            is_refreshing=self._future is not None,
            last_success_monotonic=self._last_success_monotonic,
            last_error=self._last_error,
        )

    def _collect_completed_locked(self) -> None:
        if self._future is None or not self._future.done():
            return

        future = self._future
        key = self._future_key
        self._future = None
        self._future_key = None
        try:
            self._pending_value = future.result()
            self._pending_key = key
            self._last_error = None
        except Exception as err:
            self._last_error = err
