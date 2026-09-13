from __future__ import annotations

from collections import OrderedDict
from typing import Any

from PIL import Image, ImageDraw

BitmapResult = tuple[int, int, Image.Image]
BitmapKey = tuple[str, tuple[str, str], int | None]


class BitmapTextCache:
    """Bounded least-recently-used cache of rendered text bitmaps."""

    def __init__(self, max_entries: int = 512) -> None:
        """Create a bitmap cache.

        Args:
            max_entries: Maximum rendered strings retained in memory.

        Raises:
            ValueError: If ``max_entries`` is not positive.
        """
        if max_entries <= 0:
            raise ValueError("max_entries must be greater than zero")
        self._max_entries = max_entries
        self._entries: OrderedDict[BitmapKey, BitmapResult] = OrderedDict()

    def get(self, text: str, font: Any) -> BitmapResult:
        """Return a cached or newly rendered monochrome text bitmap."""
        key = (text, font.getname(), getattr(font, "size", None))
        cached = self._entries.get(key)
        if cached is not None:
            self._entries.move_to_end(key)
            return cached

        _, _, text_width, text_height = font.getbbox(text)
        bitmap = Image.new("L", [text_width, text_height], color=0)
        ImageDraw.Draw(bitmap).text((0, 0), text=text, font=font, fill=255)
        result = (text_width, text_height, bitmap)
        self._entries[key] = result
        if len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return result

    def __len__(self) -> int:
        """Return the number of cached bitmap entries."""
        return len(self._entries)
