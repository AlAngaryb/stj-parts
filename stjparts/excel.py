"""Reading and writing parts spreadsheets (.xlsx).

This file only turns spreadsheet rows into plain dictionaries and back. The rules (what is a
duplicate, what gets saved) live in services.py: plan_import() and run_import()."""
from __future__ import annotations

import io
import re
import zipfile
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Columns written by export, in order: (heading, part field, column width).
# Import reads these same headings, so an exported sheet can be edited and imported back.
EXPORT_COLUMNS = (
    ("STJ part no.", "part_no", 14),
    ("Part name", "name", 34),
    ("Brand", "brand", 16),
    ("Mfr part no.", "mfr_part_no", 20),
    ("Size / spec", "size_spec", 26),
    ("Usage", "usage", 34),
    ("Description", "description", 30),
    ("Room", "room", 18),
    ("Location", "location_text", 24),
    ("On hand", "qty", 9),
    ("Min", "min_qty", 7),
    ("Max", "max_qty", 7),
    ("Vendor", "vendor", 20),
    ("Supplier part no.", "supplier_part_no", 18),
    ("Notes", "notes", 34),
    ("Other barcodes", "barcodes", 24),
)

# Headings import understands, after tidying (lower case, no "*", no "(...)", no punctuation).
# Covers the app's own export, the STJ Spare Parts Template / Starter sheets, and common
# names people type. Fields starting with "_" are older template columns that get folded
# into Usage or Notes (see _fold_legacy).
HEADER_ALIASES = {
    "stj part no": "part_no", "stj part number": "part_no", "stj no": "part_no", "internal part no": "part_no",
    "part name": "name", "name": "name", "item": "name",
    "brand": "brand", "manufacturer": "brand", "mfr": "brand", "make": "brand",
    "mfr part no": "mfr_part_no", "mfr part number": "mfr_part_no", "manufacturer part number": "mfr_part_no",
    "manufacturer part no": "mfr_part_no", "part number": "mfr_part_no", "part no": "mfr_part_no",
    "model": "mfr_part_no", "model number": "mfr_part_no",
    "size spec": "size_spec", "size": "size_spec", "spec": "size_spec",
    "usage": "usage", "used for": "usage",
    "description": "description",
    "room": "room",
    "location": "location_text", "bin location": "location_text", "location description": "location_text",
    "on hand": "qty", "qty on hand": "qty", "qty": "qty", "quantity": "qty",
    "min": "min_qty", "min qty": "min_qty", "max": "max_qty", "max qty": "max_qty",
    "vendor": "vendor", "supplier": "vendor",
    "supplier part no": "supplier_part_no", "supplier part number": "supplier_part_no",
    "vendor part no": "supplier_part_no", "vendor part number": "supplier_part_no",
    "notes": "notes",
    "other barcodes": "barcodes", "barcodes": "barcodes",
    "area": "_area", "system": "_system", "used on": "_used_on", "criticality": "_criticality",
    "unit price": "_price", "price": "_price", "link": "_link",
}

# Cell text that means "not filled in yet" in the starter sheet. Treated as blank.
PLACEHOLDERS = {"fill in", "tbd", "n/a", "-"}


# Description: Tidies a column heading so small differences don't matter.
#     For example, "Part Number *" becomes "part number" and "Used On (equipment tags)"
#     becomes "used on".
# Parameters:
#     1: text - [Any] The heading cell's value.
# Returns: [str] The tidied heading ("" for an empty cell).
def _tidy_header(text: Any) -> str:
    s = re.sub(r"\(.*?\)", " ", str(text or "")).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


# Description: Turns one cell into text the way a person would read it: whole numbers lose
#     the ".0" Excel adds, and placeholders like "FILL IN" become blank.
#     For example, 4.0 becomes "4" and "  SKF " becomes "SKF".
# Parameters:
#     1: value - [Any] The cell's value from openpyxl.
# Returns: [str] The cell as text ("" if empty).
def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value).strip()
    return "" if s.lower() in PLACEHOLDERS else s


# Description: Folds columns from the older template (Area, System, Used On, Criticality,
#     Unit price, Link) into fields the app has. Area, System and Used On become Usage
#     (only when Usage is blank); the others are added to Notes.
#     For example, Area "Evap", System "Infusion Heater & Hold", Used On "CP20-0003"
#     becomes Usage "Evap › Infusion Heater & Hold: CP20-0003".
# Parameters:
#     1: row - [dict] One row's fields, including any "_" fields. Changed in place.
# Returns: Nothing.
def _fold_legacy(row: dict[str, str]) -> None:
    where = " › ".join(x for x in (row.pop("_area", ""), row.pop("_system", "")) if x)
    used_on = row.pop("_used_on", "")
    usage = f"{where}: {used_on}" if where and used_on else (where or used_on)
    if usage and not row.get("usage"):
        row["usage"] = usage
    extras = []
    if c := row.pop("_criticality", ""):
        extras.append(f"Criticality {c}")
    if p := row.pop("_price", ""):
        extras.append(f"Unit price {p}")
    if link := row.pop("_link", ""):
        extras.append(link)
    if extras:
        notes = row.get("notes", "")
        joiner = " " if notes.endswith(".") else "; "
        row["notes"] = joiner.join(x for x in (notes, "; ".join(extras)) if x)


# Description: Reads the parts from an Excel workbook. Uses the "Parts" tab if there is one,
#     otherwise the first tab. Finds the heading row by itself (within the first 10 rows),
#     skips empty rows and the template's example row, and keeps each row's sheet row number
#     so problems can be pointed out ("Row 14: ...").
#     For example, POST /api/import/preview calls read_parts(file bytes) and passes the rows
#     to services.plan_import().
# Parameters:
#     1: data - [bytes] The .xlsx file's contents.
# Returns: [dict] "sheet" (tab name), "rows" (list of dict: "row" number plus part fields as
#     text, "vendor" as a name and "barcodes" as a list) and "ignored" (headings not used).
# Raises: ValueError with a plain message if it isn't an .xlsx file or no heading row is found.
def read_parts(data: bytes) -> dict[str, Any]:
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError):
        raise ValueError("That file isn't an Excel .xlsx workbook. In Excel, use Save As > Excel Workbook (.xlsx).") from None
    ws = wb["Parts"] if "Parts" in wb.sheetnames else wb.worksheets[0]
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header_at, columns = None, {}
    for i, cells in enumerate(all_rows[:10]):
        found = {n: HEADER_ALIASES[_tidy_header(v)] for n, v in enumerate(cells) if _tidy_header(v) in HEADER_ALIASES}
        if "name" in found.values():
            header_at, columns = i, found
            break
    if header_at is None:
        raise ValueError('No heading row found. The sheet needs a "Part name" column (see the template).')
    ignored = [str(v) for n, v in enumerate(all_rows[header_at]) if v not in (None, "") and n not in columns]

    rows = []
    for i, cells in enumerate(all_rows[header_at + 1:], start=header_at + 2):
        row: dict[str, Any] = {}
        for n, field in columns.items():
            text = _cell_text(cells[n]) if n < len(cells) else ""
            if text and not row.get(field):
                row[field] = text
        if not any(row.values()):
            continue
        if "example row" in row.get("notes", "").lower():
            continue
        _fold_legacy(row)
        row["barcodes"] = [c for c in re.split(r"[,;\s]+", row.get("barcodes", "")) if c]
        row["row"] = i
        rows.append(row)
    return {"sheet": ws.title, "rows": rows, "ignored": ignored}


# Description: Writes parts to a new Excel workbook with one "Parts" tab: bold frozen
#     heading row, filter arrows, sensible column widths, and red/amber shading on parts
#     that are out or low. The headings match what read_parts() understands.
#     For example, GET /api/export.xlsx sends the result as a download.
# Parameters:
#     1: parts - [list of dict] Parts from services.export_parts() (fields named as in
#        EXPORT_COLUMNS, plus "status" and "barcodes" as a list).
# Returns: [bytes] The .xlsx file's contents.
def write_parts(parts: list[dict[str, Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Parts"
    ws.append([h for h, _, _ in EXPORT_COLUMNS])
    for n, (_, _, width) in enumerate(EXPORT_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(n)].width = width
        cell = ws.cell(row=1, column=n)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1B2733")
        cell.alignment = Alignment(vertical="center")
    shade = {"out": PatternFill("solid", fgColor="FCEDEC"), "low": PatternFill("solid", fgColor="FDF4E3")}
    for p in parts:
        values = []
        for _, field, _ in EXPORT_COLUMNS:
            v = p.get(field)
            values.append(", ".join(v) if isinstance(v, list) else ("" if v is None else v))
        ws.append(values)
        if fill := shade.get(p.get("status")):
            for cell in ws[ws.max_row]:
                cell.fill = fill
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
