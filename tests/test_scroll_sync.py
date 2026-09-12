from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT / "src"))

from scroll_sync import (  # noqa: E402
    SCROLL_REQUIRED_CYCLES,
    STATS_SCROLL_REQUIRED_CYCLES,
    ScrollCompletion,
    mode_scroll_required_cycles,
    scroll_cycle_duration_s,
    scroll_frame,
)


def test_scroll_completion_waits_for_required_cycles():
    completion = ScrollCompletion(required_cycles=2)

    completion.mark_cycle_complete()
    assert completion.completed_cycles == 1
    assert completion.complete is False

    completion.mark_cycle_complete()
    assert completion.completed_cycles == 2
    assert completion.complete is True

    completion.mark_cycle_complete()
    assert completion.completed_cycles == 2


def test_scroll_completion_requires_at_least_one_cycle():
    completion = ScrollCompletion(required_cycles=0)

    completion.mark_cycle_complete()

    assert completion.required_cycles == 1
    assert completion.complete is True


def test_mode_scroll_required_cycles_scrolls_stats_once():
    assert (
        mode_scroll_required_cycles("adsb-records")
        == STATS_SCROLL_REQUIRED_CYCLES
    )
    assert mode_scroll_required_cycles("adsb") == SCROLL_REQUIRED_CYCLES
    assert mode_scroll_required_cycles("plane-alert") == SCROLL_REQUIRED_CYCLES


def test_scroll_frame_uses_elapsed_time_after_delayed_render():
    frame = scroll_frame(
        1.5,
        text_width=100,
        text_height=10,
        initial_pause_frames=10,
    )

    assert frame.x == -55
    assert frame.y == 0
    assert frame.visible is True


def test_scroll_frame_reports_completed_cycles():
    duration = scroll_cycle_duration_s(100, 10, 10)

    frame = scroll_frame(duration * 2 + 0.1, 100, 10, 10)

    assert frame.completed_cycles == 2
    assert frame.x == 0
    assert frame.y == 5


def test_scroll_cycle_duration_requires_positive_frame_interval():
    with pytest.raises(ValueError, match="frame_interval_s"):
        scroll_cycle_duration_s(100, 10, 10, frame_interval_s=0.0)
