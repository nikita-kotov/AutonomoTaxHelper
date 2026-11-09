"""The application icon, drawn in code so no binary asset has to live in the repository."""
from __future__ import annotations

from typing import Union

from PIL import Image, ImageDraw, ImageFont

_FONTS = ("arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf")


def make_icon(size: int = 64) -> Image.Image:
    """A rounded blue square with a white euro sign.

    Parameter: the side in pixels. Returns an RGBA image.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=(36, 87, 214, 255))
    font: Union[ImageFont.FreeTypeFont, ImageFont.ImageFont, None] = None
    for name in _FONTS:
        try:
            font = ImageFont.truetype(name, int(size * 0.72))
            break
        except OSError:
            continue
    if font is None:  # no system font: the default one, scaled where Pillow allows it
        font = ImageFont.load_default(size=int(size * 0.72))
    draw.text((size / 2, size / 2), "\u20ac", fill=(255, 255, 255, 255), font=font, anchor="mm")
    return image
