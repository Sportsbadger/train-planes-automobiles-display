from __future__ import annotations

from dataclasses import dataclass


SCROLL_FRAME_INTERVAL_S = 0.02

SCROLL_REQUIRED_CYCLES = 2
STATS_SCROLL_REQUIRED_CYCLES = 1


@dataclass(frozen=True)
class ScrollFrame:
    """Position and cycle metadata for one time-based scroll frame."""

    x: int
    y: int
    completed_cycles: int
    visible: bool


def scroll_cycle_duration_s(
    text_width: int,
    text_height: int,
    initial_pause_frames: int,
    *,
    final_pause_frames: int = 8,
    frame_interval_s: float = SCROLL_FRAME_INTERVAL_S,
) -> float:
    """Return the duration of one complete scroll cycle.

    Args:
        text_width: Rendered text width in pixels.
        text_height: Rendered text height in pixels.
        initial_pause_frames: Frames to hold after the vertical entrance.
        final_pause_frames: Frames to hold after the text exits left.
        frame_interval_s: Nominal duration of one animation frame.

    Returns:
        Complete cycle duration in seconds.

    Raises:
        ValueError: If the frame interval is not positive.
    """
    if frame_interval_s <= 0:
        raise ValueError("frame_interval_s must be greater than zero")

    moving_frames = max(0, text_height) + max(0, text_width) + 2
    pause_frames = max(0, initial_pause_frames) + max(0, final_pause_frames)
    return (moving_frames + pause_frames) * frame_interval_s


def scroll_frame(
    elapsed_s: float,
    text_width: int,
    text_height: int,
    initial_pause_frames: int,
    *,
    final_pause_frames: int = 8,
    frame_interval_s: float = SCROLL_FRAME_INTERVAL_S,
) -> ScrollFrame:
    """Calculate a scroll position from elapsed time.

    The calculation deliberately skips positions after a delayed render instead
    of slowing the animation by advancing only one pixel per callback.
    """
    cycle_duration = scroll_cycle_duration_s(
        text_width,
        text_height,
        initial_pause_frames,
        final_pause_frames=final_pause_frames,
        frame_interval_s=frame_interval_s,
    )
    elapsed = max(0.0, elapsed_s)
    completed_cycles = int(elapsed / cycle_duration)
    cycle_elapsed = elapsed % cycle_duration
    vertical_duration = max(0, text_height) * frame_interval_s
    if cycle_elapsed < vertical_duration:
        pixels_up = int((cycle_elapsed + 1e-9) / frame_interval_s)
        return ScrollFrame(0, text_height - pixels_up, completed_cycles, True)

    pause_duration = max(0, initial_pause_frames) * frame_interval_s
    if cycle_elapsed < vertical_duration + pause_duration:
        return ScrollFrame(0, 0, completed_cycles, True)

    horizontal_elapsed = cycle_elapsed - vertical_duration - pause_duration
    horizontal_frames = max(0, text_width) + 2
    if horizontal_elapsed < horizontal_frames * frame_interval_s:
        return ScrollFrame(
            -int((horizontal_elapsed + 1e-9) / frame_interval_s),
            0,
            completed_cycles,
            True,
        )
    return ScrollFrame(-text_width - 1, 0, completed_cycles, False)


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
