from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Sequence


MIN_INTERMISSION_DURATION_S = 2.0


@dataclass
class ModeState:
    """Tracks the active transport mode and last switch time."""

    active_mode: str
    last_switch: float


@dataclass(frozen=True)
class IntermissionState:
    """Tracks a mode transition while the next mode prepares its data."""

    target_mode: str
    started_at: float


def intermission_is_complete(
    state: IntermissionState,
    now: float,
    minimum_duration_s: float,
    data_is_refreshing: bool,
) -> bool:
    """Return whether an intermode loading screen can be dismissed.

    Args:
        state: Active intermission state.
        now: Current monotonic timestamp.
        minimum_duration_s: Minimum time to show the intermission screen.
        data_is_refreshing: Whether the target mode still has an active load.

    Returns:
        ``True`` once the minimum display time and target refresh are complete.
    """
    elapsed_s = now - state.started_at
    safe_duration_s = max(MIN_INTERMISSION_DURATION_S, minimum_duration_s)
    return elapsed_s >= safe_duration_s and not data_is_refreshing


def parse_modes(
    raw_modes: str,
    adsb_enabled: bool,
    plane_alert_enabled: bool = False,
) -> list[str]:
    """Parse configured transport modes.

    Args:
        raw_modes: Comma-separated transport mode names.
        adsb_enabled: Whether ADS-B mode is allowed.
        plane_alert_enabled: Whether Plane-Alert mode is allowed.

    Returns:
        Ordered, de-duplicated cyclic mode names.
    """
    allowed = {"train"}
    if adsb_enabled:
        allowed.add("adsb")
        allowed.add("adsb-records")
        allowed.add("adsb-stats")
        allowed.add("records")
    if plane_alert_enabled:
        allowed.add("plane-alert")
        allowed.add("planealert")
    modes: list[str] = []
    for raw_mode in raw_modes.split(","):
        mode = raw_mode.strip().lower()
        if mode == "planealert":
            mode = "plane-alert"
        if mode in {"adsb-stats", "records"}:
            mode = "adsb-records"
        if not mode or mode not in allowed or mode in modes:
            continue
        modes.append(mode)

    if modes:
        return modes
    return ["train"]


def build_mode_state(modes: Sequence[str], now: float) -> ModeState:
    """Build initial mode state from available modes."""
    if not modes:
        return ModeState(active_mode="train", last_switch=now)
    return ModeState(active_mode=modes[0], last_switch=now)


def mode_run_duration_s(
    mode: str,
    entry_count: int,
    entry_interval_s: float,
    mode_run_count: int,
) -> float:
    """Return the run-count based duration for a mode.

    Args:
        mode: Current transport mode name.
        entry_count: Number of display entries/pages in one complete cycle.
        entry_interval_s: Seconds each entry/page remains active.
        mode_run_count: Number of full cycles before switching modes. Train mode
            intentionally runs twice this value because its primary departure
            remains fixed while the lower rows cycle.

    Returns:
        Seconds to keep the mode active before advancing.
    """
    safe_entries = max(1, entry_count)
    safe_interval = max(1.0, entry_interval_s)
    safe_run_count = max(1, mode_run_count)
    if mode == "train":
        safe_run_count *= 2
    return safe_entries * safe_interval * safe_run_count


def aligned_mode_switch_interval_s(
    switch_interval_s: float,
    entry_interval_s: float,
) -> float:
    """Round a mode switch interval up to a full entry boundary.

    Args:
        switch_interval_s: Requested transport mode switch interval in seconds.
        entry_interval_s: Seconds required to display the current entry fully.

    Returns:
        The requested interval rounded up to a complete entry interval.
    """
    safe_entry_interval = max(1.0, entry_interval_s)
    safe_switch_interval = max(1.0, switch_interval_s)
    return ceil(safe_switch_interval / safe_entry_interval) * safe_entry_interval


def update_mode_state(
    state: ModeState,
    modes: Sequence[str],
    now: float,
    switch_interval_s: float,
    *,
    mode_run_count: int | None = None,
    entry_count: int = 1,
    entry_interval_s: float = 1.0,
) -> None:
    """Switch to the next configured mode when the active limit has elapsed.

    ``mode_run_count`` overrides ``switch_interval_s`` when provided. In that
    mode, each transport mode remains active until it has displayed every entry
    ``mode_run_count`` times; train mode doubles that count.
    """
    if len(modes) < 2:
        state.active_mode = modes[0] if modes else "train"
        state.last_switch = now
        return

    if mode_run_count is None:
        active_limit_s = aligned_mode_switch_interval_s(
            switch_interval_s,
            entry_interval_s,
        )
    else:
        active_limit_s = mode_run_duration_s(
            state.active_mode,
            entry_count,
            entry_interval_s,
            mode_run_count,
        )

    if now - state.last_switch < active_limit_s:
        return

    try:
        current_index = modes.index(state.active_mode)
    except ValueError:
        current_index = 0

    state.active_mode = modes[(current_index + 1) % len(modes)]
    state.last_switch = now
