from pathlib import Path
import sys

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT / "src"))

from transport_modes import (  # noqa: E402
    IntermissionState,
    aligned_mode_switch_interval_s,
    build_mode_state,
    intermission_is_complete,
    mode_run_duration_s,
    parse_modes,
    transition_image_name,
    update_mode_state,
)


def test_each_transport_mode_has_a_transition_image() -> None:
    transition_directory = PROJECT_ROOT / "src" / "images" / "transitions"

    for mode in ("train", "adsb", "adsb-records", "plane-alert"):
        image_path = transition_directory / transition_image_name(mode)

        assert image_path.is_file()
        assert image_path.read_text(encoding="ascii").startswith("#define ")
        with Image.open(image_path) as image:
            assert image.mode == "1"
            assert image.size == (256, 64)


def test_transition_image_name_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported transport mode: boat"):
        transition_image_name("boat")


def test_intermission_waits_for_minimum_duration_and_refresh() -> None:
    state = IntermissionState(target_mode="adsb", started_at=10.0)

    assert not intermission_is_complete(state, 11.9, 2.0, False)
    assert not intermission_is_complete(state, 12.0, 2.0, True)
    assert intermission_is_complete(state, 12.0, 2.0, False)


@pytest.mark.parametrize(
    "target_mode",
    ("train", "adsb", "adsb-records", "plane-alert"),
)
def test_intermission_enforces_two_second_floor(target_mode: str) -> None:
    state = IntermissionState(target_mode=target_mode, started_at=10.0)

    assert not intermission_is_complete(state, 11.9, 0.0, False)
    assert intermission_is_complete(state, 12.0, 0.0, False)


@pytest.mark.parametrize(
    "target_mode",
    ("train", "adsb", "adsb-records", "plane-alert"),
)
def test_configured_intermission_duration_applies_to_every_mode(
    target_mode: str,
) -> None:
    state = IntermissionState(target_mode=target_mode, started_at=10.0)

    assert not intermission_is_complete(state, 13.9, 4.0, False)
    assert intermission_is_complete(state, 14.0, 4.0, False)


def test_parse_modes_respects_explicit_adsb_only_mode():
    assert parse_modes("adsb", adsb_enabled=False) == ["train"]
    assert parse_modes("adsb", adsb_enabled=True) == ["adsb"]
    assert parse_modes("train,adsb,train", adsb_enabled=True) == ["train", "adsb"]
    assert parse_modes("", adsb_enabled=True) == ["train"]


def test_parse_modes_respects_explicit_plane_alert_mode():
    assert parse_modes("plane-alert", adsb_enabled=False) == ["train"]
    assert parse_modes(
        "train,planealert,adsb",
        adsb_enabled=True,
        plane_alert_enabled=True,
    ) == ["train", "plane-alert", "adsb"]


def test_parse_modes_ignores_removed_alerts_overlay_token():
    assert parse_modes(
        "train,adsb,plane-alert,alerts",
        adsb_enabled=True,
        plane_alert_enabled=True,
    ) == ["train", "adsb", "plane-alert"]


def test_update_mode_state_switches_after_interval():
    modes = ["train", "adsb"]
    state = build_mode_state(modes, now=0.0)

    update_mode_state(state, modes, now=299.0, switch_interval_s=300.0)
    assert state.active_mode == "train"

    update_mode_state(state, modes, now=300.0, switch_interval_s=300.0)
    assert state.active_mode == "adsb"

    update_mode_state(state, modes, now=600.0, switch_interval_s=300.0)
    assert state.active_mode == "train"


def test_mode_run_duration_doubles_train_runs():
    assert mode_run_duration_s(
        "train",
        entry_count=3,
        entry_interval_s=10.0,
        mode_run_count=1,
    ) == 60.0
    assert mode_run_duration_s(
        "adsb",
        entry_count=3,
        entry_interval_s=20.0,
        mode_run_count=1,
    ) == 60.0


def test_update_mode_state_mode_run_count_overrides_interval():
    modes = ["train", "adsb"]
    state = build_mode_state(modes, now=0.0)

    update_mode_state(
        state,
        modes,
        now=59.0,
        switch_interval_s=10.0,
        mode_run_count=1,
        entry_count=3,
        entry_interval_s=10.0,
    )
    assert state.active_mode == "train"

    update_mode_state(
        state,
        modes,
        now=60.0,
        switch_interval_s=10.0,
        mode_run_count=1,
        entry_count=3,
        entry_interval_s=10.0,
    )
    assert state.active_mode == "adsb"
    assert state.last_switch == 60.0


def test_aligned_mode_switch_interval_rounds_to_entry_boundary():
    assert aligned_mode_switch_interval_s(30.0, 22.0) == 44.0
    assert aligned_mode_switch_interval_s(44.0, 22.0) == 44.0
    assert aligned_mode_switch_interval_s(0.0, 0.0) == 1.0


def test_update_mode_state_waits_for_full_entry_without_run_count():
    modes = ["adsb", "adsb-records"]
    state = build_mode_state(modes, now=0.0)

    update_mode_state(
        state,
        modes,
        now=30.0,
        switch_interval_s=30.0,
        entry_interval_s=22.0,
    )
    assert state.active_mode == "adsb"

    update_mode_state(
        state,
        modes,
        now=44.0,
        switch_interval_s=30.0,
        entry_interval_s=22.0,
    )
    assert state.active_mode == "adsb-records"


def test_parse_modes_accepts_adsb_records_aliases():
    assert parse_modes(
        "train,adsb-stats,records,adsb-records",
        adsb_enabled=True,
    ) == ["train", "adsb-records"]
    assert parse_modes("adsb-records", adsb_enabled=False) == ["train"]
