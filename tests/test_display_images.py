from pathlib import Path
import sys

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT / "src"))

import display_images  # noqa: E402
from display_images import DISPLAY_SIZE, load_mode_image  # noqa: E402


@pytest.mark.parametrize(
    "mode",
    ["train", "adsb", "adsb-records", "plane-alert"],
)
def test_load_mode_image_returns_display_ready_artwork(mode: str) -> None:
    image = load_mode_image(mode)

    assert image is not None
    assert image.mode == "1"
    assert image.size == DISPLAY_SIZE
    assert image.getpixel((0, 0)) == 0


def test_each_transport_mode_has_distinct_artwork() -> None:
    assert len(set(display_images.MODE_IMAGE_FILES.values())) == 4


@pytest.mark.parametrize("filename", display_images.MODE_IMAGE_FILES.values())
def test_display_artwork_is_ascii_text(filename: str) -> None:
    artwork = (display_images.IMAGE_DIRECTORY / filename).read_bytes()

    assert artwork.startswith(b"#define ")
    assert b"\0" not in artwork
    artwork.decode("ascii")


def test_load_mode_image_returns_none_for_mode_without_artwork() -> None:
    assert load_mode_image("automobile") is None


def test_load_mode_image_raises_when_configured_artwork_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(display_images, "IMAGE_DIRECTORY", tmp_path)
    monkeypatch.setattr(
        display_images,
        "MODE_IMAGE_FILES",
        {"missing": "missing.png"},
    )

    with pytest.raises(FileNotFoundError):
        load_mode_image("missing")


def test_load_mode_image_returns_an_independent_copy() -> None:
    first = load_mode_image("train")
    second = load_mode_image("train")

    assert first is not None
    assert second is not None
    assert first is not second
    first.putpixel((0, 0), 1 - first.getpixel((0, 0)))
    assert first.getpixel((0, 0)) != second.getpixel((0, 0))


def test_load_mode_image_rejects_incompatible_artwork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Image.new("L", (128, 32)).save(tmp_path / "invalid.png")
    monkeypatch.setattr(display_images, "IMAGE_DIRECTORY", tmp_path)
    monkeypatch.setattr(
        display_images,
        "MODE_IMAGE_FILES",
        {"invalid": "invalid.png"},
    )
    with pytest.raises(ValueError, match="must be mode 1 at 256x64"):
        load_mode_image("invalid")
