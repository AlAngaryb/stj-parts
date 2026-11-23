"""Start STJ Parts: run the local server and open the app window.

    python -m stjparts            # start and open the app window
    python -m stjparts --no-open  # server only (for development)
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

from . import __version__, config
from .api import create_app


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((config.HOST, port)) == 0


def open_window(url: str) -> None:
    """Open the app as its own window (Edge or Chrome app mode), else the normal browser."""
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        ]
    else:
        candidates = [shutil.which(n) or "" for n in ("microsoft-edge", "google-chrome", "chromium")]
    for exe in candidates:
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--start-maximized"])
            return
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="stjparts", description="STJ Parts tracker")
    ap.add_argument("--no-open", action="store_true", help="don't open the app window")
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    args = ap.parse_args(argv)
    url = f"http://{config.HOST}:{args.port}/"

    if sys.stdout is None or sys.stderr is None:
        # Packaged without a console window: send output to a log file instead.
        log = open(config.data_dir() / "stjparts.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log

    if _port_in_use(args.port):
        # Already running (for example from the tray): just open another window.
        if not args.no_open:
            open_window(url)
        return

    print(f"STJ Parts {__version__}: data in {config.data_dir()}")
    print(f"Open {url}")
    if not args.no_open:
        threading.Thread(target=lambda: (time.sleep(1.0), open_window(url)), daemon=True).start()
    if args.reload:
        uvicorn.run("stjparts.main:app_factory", factory=True, host=config.HOST, port=args.port, reload=True)
    else:
        uvicorn.run(create_app(), host=config.HOST, port=args.port, log_level="warning")


def app_factory():
    return create_app()


if __name__ == "__main__":
    main()
