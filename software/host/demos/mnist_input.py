"""Digit normalization shared by the canvas and headless image input."""

from PIL import Image


def prepare(image, scale):
    """Fit ink into a 20-pixel box and center its intensity in a 28-pixel frame."""
    image = image.convert('L')
    bounds = image.getbbox()
    if bounds is None:
        return None
    ink = image.crop(bounds)
    ratio = 20 / max(ink.size)
    ink = ink.resize(tuple(max(1, round(side * ratio)) for side in ink.size),
                     Image.Resampling.LANCZOS)
    pixels = list(ink.tobytes())
    mass = sum(pixels)
    if not mass:
        return None
    cx = sum((index % ink.width) * value for index, value in enumerate(pixels)) / mass
    cy = sum((index // ink.width) * value for index, value in enumerate(pixels)) / mass
    x = min(28 - ink.width, max(0, round(13.5 - cx)))
    y = min(28 - ink.height, max(0, round(13.5 - cy)))
    frame = Image.new('L', (28, 28))
    frame.paste(ink, (x, y))
    values = [max(-8, min(7, round((pixel / 255 * 2 - 1) / scale))) for pixel in frame.tobytes()]
    return frame, values
