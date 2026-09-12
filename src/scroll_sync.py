from __future__ import annotations

from dataclasses import dataclass

SCROLL_REQUIRED_CYCLES = 2
STATS_SCROLL_REQUIRED_CYCLES = 1


@dataclass
class ScrollCompletion:
    """Tracks render-driven completion of repeated scroll cycles."""

    required_cycles: int = SCROLL_REQUIRED_CYCLES
    completed_cycles: int = 0
    complete: bool = False

    def __post_init__(self) -> None:
        """Normalize the required cycle count after initialization."""
        self.required_cycles = max(1, self.required_cycles)

    def mark_cycle_complete(self) -> None:
        """Record one fully exited scroll cycle."""
        if self.complete:
            return
        self.completed_cycles += 1
        self.complete = self.completed_cycles >= self.required_cycles


def mode_scroll_required_cycles(mode: str) -> int:
    """Return how many full scroll exits are required before advancing.

    Args:
        mode: Active transport mode name.

    Returns:
        One scroll cycle for ADS-B statistics, otherwise the default cycle
        count used by aircraft detail rows.
    """
    if mode == "adsb-records":
        return STATS_SCROLL_REQUIRED_CYCLES
    return SCROLL_REQUIRED_CYCLES
