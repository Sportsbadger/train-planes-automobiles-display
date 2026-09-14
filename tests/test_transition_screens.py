import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from transition_screens import (  # noqa: E402
    TRANSITION_IMAGE_DATA,
    load_transition_image,
)


@pytest.mark.parametrize("mode", TRANSITION_IMAGE_DATA)
def test_transition_images_fit_monochrome_oled(mode: str) -> None:
    image = load_transition_image(mode)

    assert image.size == (256, 64)
    assert image.mode == "1"
    assert image.getbbox() is not None


def test_transition_images_are_cached() -> None:
    assert load_transition_image("train") is load_transition_image("train")


def test_unknown_transition_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported transition mode"):
        load_transition_image("boat")
