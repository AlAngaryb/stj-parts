"""Send label images to a Windows printer (the Brady BMP71 driver).

Uses the normal Windows printing system, so any printer with a driver works.
On other systems (and in tests) printing reports itself as unavailable and
labels wait in the print queue instead.
"""
from __future__ import annotations

import sys

from PIL import Image


class PrinterUnavailable(RuntimeError):
    """Printing can't happen right now. The message is shown to the person."""


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32print  # noqa: F401
    except ImportError:
        return False
    return True


def list_printers() -> list[str]:
    if not available():
        return []
    import win32print

    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags)]


def pick_printer(saved: str = "") -> str | None:
    """The saved printer if it still exists, else the first one named Brady/BMP."""
    names = list_printers()
    if saved and saved in names:
        return saved
    for n in names:
        if "brady" in n.lower() or "bmp" in n.lower():
            return n
    return None


def print_labels(printer: str, images: list[Image.Image], width_in: float, height_in: float, title: str = "STJ Parts labels") -> None:
    """Print each image as one label at its true physical size."""
    if not available():
        raise PrinterUnavailable("Printing only works on the Windows shop PC.")
    import win32con
    import win32gui
    import win32print
    import win32ui
    from PIL import ImageWin

    if printer not in list_printers():
        raise PrinterUnavailable(f"Printer \"{printer}\" isn't connected.")
    try:
        hprinter = win32print.OpenPrinter(printer)
        try:
            # Tell the driver the label size so it doesn't scale to a sheet of paper.
            devmode = win32print.GetPrinter(hprinter, 2)["pDevMode"]
            devmode.Fields |= win32con.DM_PAPERWIDTH | win32con.DM_PAPERLENGTH
            devmode.PaperWidth = round(width_in * 254)   # tenths of a millimetre
            devmode.PaperLength = round(height_in * 254)
            devmode.Orientation = 1
        finally:
            win32print.ClosePrinter(hprinter)

        hdc = win32gui.CreateDC("WINSPOOL", printer, devmode)
        dc = win32ui.CreateDCFromHandle(hdc)
        dpi_x = dc.GetDeviceCaps(win32con.LOGPIXELSX)
        dpi_y = dc.GetDeviceCaps(win32con.LOGPIXELSY)
        dc.StartDoc(title)
        try:
            for img in images:
                dc.StartPage()
                w, h = round(width_in * dpi_x), round(height_in * dpi_y)
                ImageWin.Dib(img.convert("RGB")).draw(dc.GetHandleOutput(), (0, 0, w, h))
                dc.EndPage()
            dc.EndDoc()
        except Exception:
            dc.AbortDoc()
            raise
        finally:
            dc.DeleteDC()
    except PrinterUnavailable:
        raise
    except Exception as exc:  # driver errors: out of tape, offline, wrong cartridge...
        raise PrinterUnavailable(f"The printer reported a problem: {exc}") from exc
