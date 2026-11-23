"""Barcode images for labels and the screen (Code 128, what the shop scanner reads)."""
from __future__ import annotations

import io

import barcode
from barcode.writer import SVGWriter


def code128_svg(code: str, show_text: bool = False) -> bytes:
    """
    Description: Draws a barcode as an SVG picture for showing on screen.
        For example, the barcode on the part page comes from this through /api/barcode/CODE.svg.
        (Printed labels are drawn by labels.render() instead, at printer resolution.)
    Parameters:
        1: code - [str] The text to encode, e.g. "STJ-000001".
        2: show_text - [bool] Also print the code in text under the bars.
    Returns: [bytes] The SVG file contents.
    """
    out = io.BytesIO()
    bc = barcode.get("code128", code, writer=SVGWriter())
    bc.write(out, options={"write_text": show_text, "module_height": 12.0, "quiet_zone": 4.0, "module_width": 0.3})
    return out.getvalue()
