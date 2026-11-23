"""Barcode images for labels and the screen (Code 128, what the shop scanner reads)."""
from __future__ import annotations

import io

import barcode
from barcode.writer import SVGWriter


def code128_svg(code: str, show_text: bool = False) -> bytes:
    """Return an SVG of `code` as a Code 128 barcode."""
    out = io.BytesIO()
    bc = barcode.get("code128", code, writer=SVGWriter())
    bc.write(out, options={"write_text": show_text, "module_height": 12.0, "quiet_zone": 4.0, "module_width": 0.3})
    return out.getvalue()
