from functools import cache
from pathlib import Path
from typing import Final

from PIL import Image


DISPLAY_SIZE: Final[tuple[int, int]] = (256, 64)
IMAGE_DIRECTORY: Final[Path] = Path(__file__).resolve().parent / "images"
MODE_IMAGE_FILES: Final[dict[str, str]] = {
    "train": "train-intermission.pbm",
    "adsb": "adsb-intermission.pbm",
    "adsb-records": "adsb-intermission.pbm",
    "plane-alert": "plane-alert-intermission.pbm",
}


def load_mode_image(mode: str) -> Image.Image | None:
    """Load a display-ready intermission image for a transport mode.

    Args:
        mode: Transport mode identifier.

    Returns:
        A detached one-bit image, or ``None`` when the mode has no artwork.

    Raises:
        FileNotFoundError: If configured artwork is missing.
        ValueError: If a configured image does not match the OLED format.
    """
    filename = MODE_IMAGE_FILES.get(mode)
    if filename is None:
        return None

    # PIL images are mutable. Return a detached copy so one renderer cannot
    # corrupt the cached source used by subsequent intermissions.
    return _load_display_image(IMAGE_DIRECTORY / filename).copy()


@cache
def _load_display_image(image_path: Path) -> Image.Image:
    """Load and validate one immutable cached display-image source."""
    with Image.open(image_path) as source:
        image = source.copy()

    if image.size != DISPLAY_SIZE or image.mode != "1":
        raise ValueError(
            f"Display image {image_path} must be mode 1 at "
            f"{DISPLAY_SIZE[0]}x{DISPLAY_SIZE[1]}; got {image.mode} at "
            f"{image.width}x{image.height}"
        )
    return image
