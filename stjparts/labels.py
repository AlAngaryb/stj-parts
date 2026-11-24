"""Draw labels as images at the printer's resolution.

The same image is used for the on-screen preview, the PDF download and the
Brady printer, so what you see is what prints. Bars are drawn on whole
printer dots so the barcode stays sharp and scannable.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache

import barcode
from PIL import Image, ImageDraw, ImageFont


@dataclass
class LabelContent:
    code: str
    name: str = ""
    location: str = ""


@dataclass
class LabelSize:
    width_in: float
    height_in: float
    show_name: bool = True
    show_location: bool = True


class LabelTooSmall(ValueError):
    pass


QUIET_MODULES = 10  # Code 128 needs blank space either side of the bars


@lru_cache(maxsize=32)
def _font(size: int, bold: bool) -> ImageFont.ImageFont:
    names = (["arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["arial.ttf", "DejaVuSans.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int, bold: bool) -> tuple[str, ImageFont.ImageFont]:
    """Largest font that fits the box; long text is shortened with an ellipsis."""
    size = max(8, max_h)
    while size > 8:
        font = _font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_w:
            return text, font
        size -= 1
    font = _font(8, bold)
    while text and draw.textbbox((0, 0), text + "…", font=font)[2] > max_w:
        text = text[:-1]
    return (text + "…") if text else "", font


def render(content: LabelContent, size: LabelSize, dpi: int = 300) -> Image.Image:
    """Return a 1-bit image of the label, exactly width x height at `dpi`."""
    W, H = round(size.width_in * dpi), round(size.height_in * dpi)
    img = Image.new("1", (W, H), 1)
    draw = ImageDraw.Draw(img)
    margin = round(0.05 * dpi)
    inner_w, inner_h = W - 2 * margin, H - 2 * margin

    # Small labels drop the extras so the barcode stays big enough to scan.
    show_name = size.show_name and bool(content.name) and size.height_in >= 0.7
    show_loc = size.show_location and bool(content.location) and size.height_in >= 0.7

    big = size.height_in >= 1.5
    line_h = round(dpi * (0.15 if big else 0.10 if size.height_in >= 0.7 else 0.085))
    small_h = round(line_h * 0.85)
    gap = max(2, round(dpi * 0.015))

    modules = barcode.get("code128", content.code).build()[0]
    module_px = inner_w // (len(modules) + 2 * QUIET_MODULES)
    if module_px < 2:
        raise LabelTooSmall(
            f"{content.code} is too long to scan reliably on a {size.width_in:g} in wide label. Use a wider label or a shorter number."
        )
    module_px = min(module_px, max(2, round(dpi * 0.02)))  # very wide labels: cap bar width

    fixed = line_h + gap  # the readable code under the bars always prints
    if show_name:
        fixed += line_h + gap
    if show_loc:
        fixed += small_h + gap
    bar_h = inner_h - fixed
    if bar_h < dpi * 0.18:
        raise LabelTooSmall("Not enough room for the barcode. Turn off the name or location, or use a taller label.")
    bar_h = min(bar_h, round(dpi * 0.9))

    block_h = fixed + bar_h
    y = margin + (inner_h - block_h) // 2

    def centered_text(text, h, bold):
        nonlocal y
        t, font = _fit_text(draw, text, inner_w, h, bold)
        box = draw.textbbox((0, 0), t, font=font)
        draw.text((margin + (inner_w - (box[2] - box[0])) // 2 - box[0], y - box[1] + (h - (box[3] - box[1])) // 2), t, font=font, fill=0)
        y += h + gap

    if show_name:
        centered_text(content.name, line_h, True)

    bars_w = len(modules) * module_px
    x = margin + (inner_w - bars_w) // 2
    for i, bit in enumerate(modules):
        if bit == "1":
            draw.rectangle([x + i * module_px, y, x + (i + 1) * module_px - 1, y + bar_h - 1], fill=0)
    y += bar_h + gap

    centered_text(content.code, line_h, True)
    if show_loc:
        centered_text(content.location, small_h, False)
    return img


def to_png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def to_pdf(images: list[Image.Image], dpi: int = 300) -> bytes:
    """Labels as a PDF, one per page at true size (for printing from another PC)."""
    out = io.BytesIO()
    first, rest = images[0].convert("L"), [i.convert("L") for i in images[1:]]
    first.save(out, "PDF", resolution=dpi, save_all=True, append_images=rest)
    return out.getvalue()
