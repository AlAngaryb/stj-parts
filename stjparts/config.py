"""Where the app keeps its data, and the settings it starts with."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "STJParts"
HOST = "127.0.0.1"
PORT = int(os.environ.get("STJ_PARTS_PORT", "8765"))


# Description: Finds (and creates if needed) the folder that holds the database, photos and
#     documents. The STJ_PARTS_DATA environment variable overrides it; F5 in VS Code uses this
#     to keep test data in the repo's data folder, away from the real shop database.
# Parameters: None.
# Returns: [Path] %LOCALAPPDATA%\STJParts on Windows, ~/.stjparts elsewhere, or the override.
def data_dir() -> Path:
    override = os.environ.get("STJ_PARTS_DATA")
    if override:
        path = Path(override)
    elif sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        path = Path(os.environ["LOCALAPPDATA"]) / APP_NAME
    else:
        path = Path.home() / ".stjparts"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Settings a supervisor can change later. Stored in the settings table;
# these are only the values used when a setting has never been saved.
DEFAULT_SETTINGS: dict[str, str] = {
    "part_no_prefix": "STJ-",
    "part_no_digits": "6",
    "description_required": "0",
    "supervisor_logout_minutes": "10",
    "low_stock_default_min": "1",
    "label_printer": "",  # blank = pick the Brady automatically
    "label_dpi": "300",  # BMP71 prints at 300 dpi
}
