from pathlib import Path
import sys

import pytest
from PIL import ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT / "src"))

from bitmap_cache import BitmapTextCache  # noqa: E402


def test_bitmap_cache_reuses_rendered_image() -> None:
    cache = BitmapTextCache(max_entries=2)
    font = ImageFont.load_default()

    first = cache.get("aircraft", font)
    second = cache.get("aircraft", font)

    assert first[2] is second[2]
    assert len(cache) == 1


def test_bitmap_cache_evicts_least_recently_used_entry() -> None:
    cache = BitmapTextCache(max_entries=2)
    font = ImageFont.load_default()

    first = cache.get("first", font)[2]
    cache.get("second", font)
    cache.get("third", font)

    assert len(cache) == 2
    assert cache.get("first", font)[2] is not first


def test_bitmap_cache_requires_positive_capacity() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        BitmapTextCache(max_entries=0)
