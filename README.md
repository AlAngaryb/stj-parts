# STJ Parts

Spare parts tracking for the St Johns maintenance shop: scan a label, see the part,
check it in or out. Runs on one Windows PC with a USB barcode scanner and a Brady BMP71
label printer. No server or internet needed.

## What works now (v0.1)

- Scan anywhere: a known barcode opens the part; an unknown one offers "Create new part".
  Works with scanners that press Enter after a scan and ones that don't.
- Add parts (only the name is required), with the next `STJ-000001` number assigned automatically.
  Manufacturer barcodes can be added to a part, so either label opens it.
- Check out (who, quantity, work order) and check in (who, quantity, PO), with a warning when
  stock drops to the minimum. Stock can never go below zero.
- Parts library: search every field, typos allowed, filter by room, brand and stock, sort four ways.
- Activity log colored by what's left, with a "To order only" view.
- Vendors list, people list, merging duplicate parts (old labels keep working).

Next up: label printing, photos and documents, locations, Excel import/export, supervisor login,
daily report. The full design is in `docs/spec.md`.

## Set up a development PC (Windows)

Install once: **VS Code**, **Python 3.12** (tick "Add python.exe to PATH"), **Git for Windows**.

1. In VS Code: *Clone Repository* → `https://github.com/AlAngaryb/stj-parts`.
2. Accept the recommended extensions when VS Code offers them.
3. *Terminal → Run Task → Set up (first time)*. This creates `.venv` and installs everything.
4. Press **F5** ("Run STJ Parts (test data)"). The app opens in a window. Test data goes in the
   `data` folder of the repo, so it never touches the real shop database.
5. *Terminal → Run Task → Run tests* runs the automatic tests.

Or from a terminal:

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m stjparts
```

## Getting the Windows app

Every push to `main` runs the tests on Windows and Linux, builds `STJParts.exe`, and checks that it starts.
Download it from the repo's **Actions** tab → latest run → *STJParts-windows*. Unzip and run `STJParts.exe`.
An installer with a desktop shortcut and tray icon comes later.

Real data lives in `%LOCALAPPDATA%\STJParts` (database `parts.db`). Set `STJ_PARTS_DATA` to use another folder.

## How the code is laid out

| Path | What it is |
|---|---|
| `stjparts/db.py` | SQLite file and numbered migrations (never edit a shipped migration; add one) |
| `stjparts/services.py` | All the rules: parts, stock moves, search, merge, vendors, people |
| `stjparts/api.py` | HTTP API the screens use (`/api/...`); a phone app could use the same one later |
| `stjparts/web/` | The screens: plain HTML, CSS and JavaScript, no build step |
| `stjparts/web/scanner.js` | Tells scanner input apart from typing |
| `stjparts/main.py` | Starts the server and opens the app window |
| `tests/` | Automatic tests (`pytest`) |
