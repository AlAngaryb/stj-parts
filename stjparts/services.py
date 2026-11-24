"""Business rules. The API and any future tools (Excel import, phone app,
AI helpers) call these functions instead of writing SQL themselves."""
from __future__ import annotations

import difflib
import re
import sqlite3
from typing import Any, Iterable

from . import config
from .db import transaction


# A problem the person at the screen can fix. The message is shown as-is.
class ServiceError(ValueError):
    pass


class NotFound(ServiceError):
    pass


PART_FIELDS = (
    "name", "brand", "mfr_part_no", "size_spec", "usage", "description", "room",
    "location_text", "min_qty", "max_qty", "vendor_id", "supplier_part_no", "notes",
)
TEXT_FIELDS = {f for f in PART_FIELDS if f not in ("min_qty", "max_qty", "vendor_id")}


# ======================================================================================
# SETTINGS
# Values a supervisor can change (part number prefix, label printer, ...).
# Stored in the settings table; anything never saved falls back to config.DEFAULT_SETTINGS.
# ======================================================================================

# Description: Reads one setting, such as the part number prefix or label printer name.
#     If a supervisor never saved it, the default from config.DEFAULT_SETTINGS is used.
#     For example, next_part_no() calls get_setting(conn, "part_no_prefix") to get "STJ-".
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: key - [str] The setting name, e.g. "description_required".
# Returns: [str] The setting's value (always text; "1"/"0" for on/off settings).
# Raises: KeyError if the setting name doesn't exist at all.
def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    if key in config.DEFAULT_SETTINGS:
        return config.DEFAULT_SETTINGS[key]
    raise KeyError(key)


# Description: Saves one setting, replacing any earlier value.
#     For example, set_label_printer() uses it to remember which printer to use.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: key - [str] The setting name. Must be one listed in config.DEFAULT_SETTINGS.
#     3: value - [str] The new value (numbers and on/off are stored as text).
# Returns: Nothing.
# Raises: ServiceError if the setting name is unknown.
def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    if key not in config.DEFAULT_SETTINGS:
        raise ServiceError(f"Unknown setting: {key}")
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


# Description: Reads every setting at once, with defaults filled in.
#     For example, the /api/settings endpoint returns this to the screens.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
# Returns: [dict of str to str] Setting name -> value.
def all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {k: get_setting(conn, k) for k in config.DEFAULT_SETTINGS}


# ======================================================================================
# BARCODES AND PART NUMBERS
# Cleaning scanned codes, choosing the next STJ number, and the stock color rule.
# ======================================================================================

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


# Description: Cleans up a barcode the way a scanner typed it: removes Enter, Tab and
#     other invisible control characters, and spaces at either end.
#     For example, "STJ-000001" followed by Enter (from a scanner that sends Enter)
#     becomes just "STJ-000001".
#     Every function that looks up or saves a barcode runs it through this first.
# Parameters:
#     1: code - [str] The raw text from the scanner or the keyboard.
# Returns: [str] The cleaned code ("" if nothing is left).
def normalize_code(code: str) -> str:
    return _CONTROL.sub("", code or "").strip()


# Description: Works out the next free STJ part number by finding the highest one in use.
#     For example, if STJ-000010 is the highest, it returns "STJ-000011".
#     Used by create_part() when the person leaves Part number blank.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
# Returns: [str] The next part number, e.g. "STJ-000011".
def next_part_no(conn: sqlite3.Connection) -> str:
    prefix = get_setting(conn, "part_no_prefix")
    digits = int(get_setting(conn, "part_no_digits"))
    rows = conn.execute(
        "SELECT part_no FROM parts WHERE part_no LIKE ? || '%'", (prefix,)
    ).fetchall()
    highest = 0
    for r in rows:
        tail = r["part_no"][len(prefix):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1:0{digits}d}"


# Description: Decides the stock color for a quantity: red (out), amber (low) or green (ok).
#     For example, the activity log and parts library color each row with this.
# Parameters:
#     1: qty - [int] How many are on hand.
#     2: min_qty - [int or None] The reorder point. None means no minimum is set.
# Returns: [str] "out" when qty is 0, "low" when qty is at or below min_qty, otherwise "ok".
def stock_status(qty: int, min_qty: int | None) -> str:
    if qty <= 0:
        return "out"
    if min_qty is not None and qty <= min_qty:
        return "low"
    return "ok"


# ======================================================================================
# PARTS
# Adding and editing parts, attaching barcodes, and looking a part up by any of its codes.
# Quantities are NOT changed here; see Stock moves below.
# ======================================================================================

# Description: Checks and tidies the fields typed on the Add part / Edit screens before they
#     are saved: trims spaces from text, turns number boxes into whole numbers, and blocks
#     negative numbers or a min larger than the max. Unknown fields are ignored.
#     Used by create_part() and update_part().
# Parameters:
#     1: data - [dict] Field name -> value, as sent by the screen.
# Returns: [dict] Only the known part fields, cleaned and ready to save.
# Raises: ServiceError with a plain message if a value isn't allowed.
def _clean_part_data(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in PART_FIELDS:
        if key not in data:
            continue
        value = data[key]
        if key in TEXT_FIELDS:
            out[key] = (value or "").strip()
        elif value in ("", None):
            out[key] = None
        else:
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                raise ServiceError(f"{key.replace('_', ' ').capitalize()} must be a whole number.") from None
            if key != "vendor_id" and out[key] < 0:
                raise ServiceError(f"{key.replace('_', ' ').capitalize()} can't be negative.")
    if out.get("min_qty") is not None and out.get("max_qty") is not None and out["min_qty"] > out["max_qty"]:
        raise ServiceError("Min can't be more than max.")
    return out


# Description: Adds a new part. It assigns the next STJ number if none was given, registers
#     the part number as a barcode, registers any extra barcodes (e.g. the manufacturer code
#     that was scanned on the Unregistered screen), logs an "Added" line in the activity log,
#     and adds the part to the search index. All of this happens together or not at all.
#     For example, saving the Add part screen calls this through POST /api/parts.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: data - [dict] The part's fields (name is required; see PART_FIELDS). "qty" is the
#        starting quantity and "part_no" is optional.
#     3: who - [str] Initials of the person adding it. Stored in capitals.
#     4: barcodes - [list of str] Optional extra barcodes that should open this part.
# Returns: [dict] The saved part, same shape as get_part().
# Raises: ServiceError if the name or initials are missing, a description is required but
#     blank, or a barcode already belongs to another part.
def create_part(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    who: str,
    barcodes: Iterable[str] = (),
) -> dict[str, Any]:
    who = (who or "").strip().upper()
    if not who:
        raise ServiceError("Enter your initials.")
    fields = _clean_part_data(data)
    if not fields.get("name"):
        raise ServiceError("Give the part a name.")
    if get_setting(conn, "description_required") == "1" and not fields.get("description"):
        raise ServiceError("A description is required (supervisor setting).")
    qty = _whole(data.get("qty", 0), "Quantity")
    extra = [normalize_code(c) for c in barcodes if normalize_code(c)]

    with transaction(conn):
        part_no = normalize_code(data.get("part_no") or "") or next_part_no(conn)
        for code in [part_no, *extra]:
            hit = conn.execute("SELECT part_id FROM barcodes WHERE code = ?", (code,)).fetchone()
            if hit:
                raise ServiceError(f"Barcode {code} already belongs to another part.")
        cols = ["part_no", "qty", "created_by", *fields]
        cur = conn.execute(
            f"INSERT INTO parts({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [part_no, qty, who, *fields.values()],
        )
        part_id = cur.lastrowid
        conn.execute("INSERT INTO barcodes(code, part_id, kind) VALUES (?, ?, 'internal')", (part_no, part_id))
        for code in dict.fromkeys(extra):
            if code.lower() != part_no.lower():
                conn.execute("INSERT INTO barcodes(code, part_id, kind) VALUES (?, ?, 'mfr')", (code, part_id))
        conn.execute(
            "INSERT INTO activity(part_id, action, qty_change, qty_after, who) VALUES (?, 'added', ?, ?, ?)",
            (part_id, qty, qty, who),
        )
        _index_part(conn, part_id)
    return get_part(conn, part_id)


# Description: Changes some of a part's details (not its quantity; use check_in, check_out
#     or adjust_count for that), logs which fields changed, and refreshes the search index.
#     For example, renaming a part from its edit screen calls this through PATCH /api/parts/ID.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
#     3: data - [dict] Only the fields to change, e.g. {"room": "Dryer basement"}.
#     4: who - [str] Initials of the person making the change.
# Returns: [dict] The updated part, same shape as get_part().
# Raises: NotFound if the part doesn't exist; ServiceError for a blank name or bad number.
def update_part(conn: sqlite3.Connection, part_id: int, data: dict[str, Any], who: str) -> dict[str, Any]:
    fields = _clean_part_data(data)
    if "name" in fields and not fields["name"]:
        raise ServiceError("Give the part a name.")
    if not fields:
        return get_part(conn, part_id)
    with transaction(conn):
        part = _part_row(conn, part_id)
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE parts SET {sets}, updated_at = datetime('now') WHERE id = ?", [*fields.values(), part_id])
        conn.execute(
            "INSERT INTO activity(part_id, action, qty_after, who, note) VALUES (?, 'edit', ?, ?, ?)",
            (part_id, part["qty"], (who or "").upper(), "Changed " + ", ".join(sorted(fields))),
        )
        _index_part(conn, part_id)
    return get_part(conn, part_id)


# Description: Attaches another barcode to an existing part so scanning it opens that part.
#     For example, the "Add barcode" button on the part page uses it for a manufacturer's code.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
#     3: code - [str] The barcode as scanned or typed.
#     4: kind - [str] "mfr" (manufacturer), "internal" (our label) or "other".
# Returns: Nothing. Adding a code the part already has does nothing.
# Raises: ServiceError if the code is blank or belongs to a different part.
def add_barcode(conn: sqlite3.Connection, part_id: int, code: str, kind: str = "other") -> None:
    code = normalize_code(code)
    if not code:
        raise ServiceError("Scan or type a barcode.")
    with transaction(conn):
        _part_row(conn, part_id)
        hit = conn.execute("SELECT part_id FROM barcodes WHERE code = ?", (code,)).fetchone()
        if hit and hit["part_id"] != part_id:
            raise ServiceError(f"Barcode {code} already belongs to another part.")
        if not hit:
            conn.execute("INSERT INTO barcodes(code, part_id, kind) VALUES (?, ?, ?)", (code, part_id, kind))


# Description: Finds the part a scanned barcode belongs to. If that part was merged into
#     another one, it follows the merge so old labels still open the right part.
#     For example, every scan on the home screen goes through this (GET /api/scan/CODE).
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: code - [str] The barcode as scanned (Enter/Tab are cleaned off).
# Returns: [dict or None] The part (same shape as get_part()), or None if no part has this code.
def lookup_code(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    code = normalize_code(code)
    if not code:
        return None
    row = conn.execute("SELECT part_id FROM barcodes WHERE code = ?", (code,)).fetchone()
    if not row:
        return None
    return get_part(conn, _follow_merge(conn, row["part_id"]))


# Description: Given a part ID, returns the ID of the part it was merged into (following
#     a chain if it was merged more than once). Returns the same ID if it was never merged.
#     Used by lookup_code().
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
# Returns: [int] The ID of the part that is still in use.
def _follow_merge(conn: sqlite3.Connection, part_id: int) -> int:
    seen = set()
    while part_id not in seen:
        seen.add(part_id)
        row = conn.execute("SELECT merged_into FROM parts WHERE id = ?", (part_id,)).fetchone()
        if not row or row["merged_into"] is None:
            return part_id
        part_id = row["merged_into"]
    return part_id


# Description: Reads one part's raw database row, or stops with a clear message if it's gone.
#     Used inside most part functions before they change anything.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
# Returns: [sqlite3.Row] The part's columns.
# Raises: NotFound if there is no part with that ID.
def _part_row(conn: sqlite3.Connection, part_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    if not row:
        raise NotFound("That part doesn't exist.")
    return row


# Description: Gathers everything the part page shows: the part's fields, its stock color,
#     all its barcodes, its vendor's details and its last 20 activity lines.
#     For example, opening a part page calls this through GET /api/parts/ID.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
# Returns: [dict] The part's fields plus "status", "barcodes" (list), "vendor" (dict or None)
#     and "activity" (list, newest first).
# Raises: NotFound if there is no part with that ID.
def get_part(conn: sqlite3.Connection, part_id: int) -> dict[str, Any]:
    row = _part_row(conn, part_id)
    part = dict(row)
    part["status"] = stock_status(part["qty"], part["min_qty"])
    part["barcodes"] = [dict(r) for r in conn.execute(
        "SELECT code, kind FROM barcodes WHERE part_id = ? ORDER BY kind = 'internal' DESC, created_at", (part_id,)
    )]
    part["vendor"] = None
    if part["vendor_id"]:
        v = conn.execute("SELECT * FROM vendors WHERE id = ?", (part["vendor_id"],)).fetchone()
        part["vendor"] = dict(v) if v else None
    part["activity"] = [dict(r) for r in conn.execute(
        "SELECT * FROM activity WHERE part_id = ? ORDER BY at DESC, id DESC LIMIT 20", (part_id,)
    )]
    return part


# ======================================================================================
# STOCK MOVES
# Check out, check in, count corrections and merging duplicates.
# Every change to a quantity also writes an activity line, in the same transaction.
# ======================================================================================

# Description: Turns a typed quantity into a whole number and rejects negatives.
#     Used for quantities, counts and copies.
# Parameters:
#     1: value - [any] What was typed or sent, e.g. "3" or 3.
#     2: label - [str] What to call it in the error message, e.g. "Quantity".
# Returns: [int] The whole number.
# Raises: ServiceError if it isn't a whole number or is negative.
def _whole(value: Any, label: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ServiceError(f"{label} must be a whole number.") from None
    if n < 0:
        raise ServiceError(f"{label} can't be negative.")
    return n


# Description: The shared engine behind check out and check in. It checks the quantity and
#     initials, makes sure stock never goes below zero, updates the on-hand count and writes
#     one activity line, all in one step so two scans at once can't get the count wrong.
#     Called only by check_out() and check_in().
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
#     3: qty - [int] How many are moving (must be 1 or more).
#     4: who - [str] Initials of the person taking or returning it.
#     5: ref - [str] Work order or PO number (optional).
#     6: action - [str] "out" or "in".
# Returns: [dict] The part after the move, same shape as get_part().
# Raises: ServiceError if the quantity or initials are missing, the part was merged, or
#     there aren't enough on hand.
def _move(conn, part_id, qty, who, ref, action) -> dict[str, Any]:
    qty = _whole(qty, "Quantity")
    if qty == 0:
        raise ServiceError("Quantity must be at least 1.")
    who = (who or "").strip().upper()
    if not who:
        raise ServiceError("Pick who is taking it, or type initials.")
    with transaction(conn):
        part = _part_row(conn, part_id)
        if part["merged_into"] is not None:
            raise ServiceError("This part was merged into another one. Scan again.")
        change = qty if action == "in" else -qty
        new_qty = part["qty"] + change
        if new_qty < 0:
            raise ServiceError(f"Only {part['qty']} on hand. Check the count, or ask a supervisor to adjust it.")
        conn.execute("UPDATE parts SET qty = ?, updated_at = datetime('now') WHERE id = ?", (new_qty, part_id))
        conn.execute(
            "INSERT INTO activity(part_id, action, qty_change, qty_after, who, ref) VALUES (?, ?, ?, ?, ?, ?)",
            (part_id, action, change, new_qty, who, (ref or "").strip()),
        )
    return get_part(conn, part_id)


# Description: Takes parts out of stock and logs who took them and for which work order.
#     For example, "Confirm check out" on the check-out screen calls this
#     through POST /api/parts/ID/checkout.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
#     3: qty - [int] How many are being taken.
#     4: who - [str] Initials of the person taking them.
#     5: ref - [str] Work order or PO number, e.g. "WO 24-1187" (optional).
# Returns: [dict] The part after the change, same shape as get_part().
# Raises: ServiceError if there aren't enough on hand or initials are missing.
def check_out(conn, part_id: int, qty: int, who: str, ref: str = "") -> dict[str, Any]:
    return _move(conn, part_id, qty, who, ref, "out")


# Description: Adds parts back into stock (a return, or a new delivery) and logs it.
#     For example, "Confirm check in" calls this through POST /api/parts/ID/checkin.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
#     3: qty - [int] How many are coming in.
#     4: who - [str] Initials of the person checking them in.
#     5: ref - [str] PO number for a delivery (optional).
# Returns: [dict] The part after the change, same shape as get_part().
def check_in(conn, part_id: int, qty: int, who: str, ref: str = "") -> dict[str, Any]:
    return _move(conn, part_id, qty, who, ref, "in")


# Description: Sets the on-hand count to what was actually counted on the shelf (a cycle
#     count correction), and logs the difference.
#     For example, when a supervisor finds 3 on the shelf but the app says 5.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
#     3: new_qty - [int] The real count.
#     4: who - [str] Initials of the person who counted.
#     5: note - [str] Why it changed, e.g. "Cycle count" (optional).
# Returns: [dict] The part after the change, same shape as get_part().
def adjust_count(conn, part_id: int, new_qty: int, who: str, note: str = "") -> dict[str, Any]:
    new_qty = _whole(new_qty, "Count")
    with transaction(conn):
        part = _part_row(conn, part_id)
        conn.execute("UPDATE parts SET qty = ?, updated_at = datetime('now') WHERE id = ?", (new_qty, part_id))
        conn.execute(
            "INSERT INTO activity(part_id, action, qty_change, qty_after, who, note) VALUES (?, 'adjust', ?, ?, ?, ?)",
            (part_id, new_qty - part["qty"], new_qty, (who or "").upper(), note),
        )
    return get_part(conn, part_id)


# Description: Combines a duplicate part into the one to keep. The duplicate's barcodes move
#     over (so its old labels keep working), the stock is added together, both parts get an
#     activity line, and the duplicate disappears from search.
#     For example, if "Bearing 6204" and "Bearing, ball, 6204-2RS" were entered separately.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: keep_id - [int] The part to keep.
#     3: remove_id - [int] The duplicate to fold into it.
#     4: who - [str] Initials of the person merging.
# Returns: [dict] The kept part after the merge, same shape as get_part().
# Raises: ServiceError if both IDs are the same or either part was already merged.
def merge_parts(conn, keep_id: int, remove_id: int, who: str) -> dict[str, Any]:
    if keep_id == remove_id:
        raise ServiceError("Pick two different parts.")
    with transaction(conn):
        keep, gone = _part_row(conn, keep_id), _part_row(conn, remove_id)
        if gone["merged_into"] is not None or keep["merged_into"] is not None:
            raise ServiceError("One of these parts was already merged.")
        conn.execute("UPDATE barcodes SET part_id = ? WHERE part_id = ?", (keep_id, remove_id))
        total = keep["qty"] + gone["qty"]
        conn.execute("UPDATE parts SET qty = ?, updated_at = datetime('now') WHERE id = ?", (total, keep_id))
        conn.execute("UPDATE parts SET qty = 0, merged_into = ?, updated_at = datetime('now') WHERE id = ?", (keep_id, remove_id))
        note = f"Merged {gone['part_no']} into {keep['part_no']}"
        conn.execute(
            "INSERT INTO activity(part_id, action, qty_change, qty_after, who, note) VALUES (?, 'merge', ?, ?, ?, ?)",
            (keep_id, gone["qty"], total, (who or "").upper(), note),
        )
        conn.execute(
            "INSERT INTO activity(part_id, action, qty_change, qty_after, who, note) VALUES (?, 'merge', ?, 0, ?, ?)",
            (remove_id, -gone["qty"], (who or "").upper(), note),
        )
        conn.execute("DELETE FROM parts_fts WHERE rowid = ?", (remove_id,))
    return get_part(conn, keep_id)


# ======================================================================================
# SEARCH
# The parts library search: full-text index (parts_fts), filters, sorting and typo fixes.
# ======================================================================================

# Description: Refreshes one part's entry in the search index (all its text fields, barcodes
#     and vendor name), or removes it if the part was merged. Called after every add or edit
#     so the parts library always finds the latest wording.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
# Returns: Nothing.
def _index_part(conn: sqlite3.Connection, part_id: int) -> None:
    p = conn.execute(
        "SELECT p.*, COALESCE(v.name, '') AS vendor FROM parts p LEFT JOIN vendors v ON v.id = p.vendor_id WHERE p.id = ?",
        (part_id,),
    ).fetchone()
    conn.execute("DELETE FROM parts_fts WHERE rowid = ?", (part_id,))
    if p and p["merged_into"] is None:
        extra_codes = " ".join(r["code"] for r in conn.execute("SELECT code FROM barcodes WHERE part_id = ?", (part_id,)))
        conn.execute(
            "INSERT INTO parts_fts(rowid, part_no, name, brand, mfr_part_no, size_spec, usage, description, room,"
            " location_text, supplier_part_no, vendor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (part_id, p["part_no"] + " " + extra_codes, p["name"], p["brand"], p["mfr_part_no"], p["size_spec"],
             p["usage"], p["description"], p["room"], p["location_text"], p["supplier_part_no"], p["vendor"]),
        )


_WORD = re.compile(r"[\w/.\-]+", re.UNICODE)

SORTS = {
    "name": "p.name COLLATE NOCASE",
    "stock": "CASE WHEN p.qty <= 0 THEN 0 WHEN p.min_qty IS NOT NULL AND p.qty <= p.min_qty THEN 1 ELSE 2 END, p.qty, p.name COLLATE NOCASE",
    "used": "(SELECT MAX(at) FROM activity a WHERE a.part_id = p.id AND a.action = 'out') DESC, p.name COLLATE NOCASE",
    "added": "p.created_at DESC, p.id DESC",
}


# Description: The parts library search. Every word typed must match some field (the start of
#     a word is enough, so "solen" finds "solenoid"). If nothing matches, it tries close
#     spellings so typos still work ("quik conect" finds "quick connect"). Also checks whether
#     the text is an exact barcode, so scanning into the search box can open the part.
#     For example, typing in the Parts library box calls this through GET /api/parts?q=...
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: q - [str] The search text or a scanned barcode. Blank lists every part.
#     3: room - [str] Only parts in this room (optional).
#     4: brand - [str] Only parts of this brand (optional).
#     5: stock - [str] "out", "low", "ok", or "" for all.
#     6: sort - [str] "name", "stock" (lowest first), "used" (recently used) or "added".
#     7: limit - [int] Most rows to return.
# Returns: [dict] "results" (list of parts), "exact" (the part if q was an exact barcode,
#     else None) and "did_you_mean" (the corrected words, or None).
def search_parts(
    conn: sqlite3.Connection,
    q: str = "",
    *,
    room: str = "",
    brand: str = "",
    stock: str = "",
    sort: str = "name",
    limit: int = 100,
) -> dict[str, Any]:
    q = normalize_code(q)
    exact = lookup_code(conn, q) if q else None
    words = [w for w in _WORD.findall(q.lower())]
    rows = _search(conn, words, room, brand, stock, sort, limit)
    suggested = None
    if q and not rows:
        fixed = _respell(conn, words)
        if fixed and fixed != words:
            rows = _search(conn, fixed, room, brand, stock, sort, limit)
            suggested = " ".join(fixed) if rows else None
    return {"exact": exact, "results": rows, "did_you_mean": suggested}


# Description: Runs the actual search query for search_parts(), with filters and sorting.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: words - [list of str] Lowercase search words.
#     3: room - [str] Room filter, or "".
#     4: brand - [str] Brand filter, or "".
#     5: stock - [str] "out", "low", "ok" or "".
#     6: sort - [str] One of the keys in SORTS.
#     7: limit - [int] Most rows to return.
# Returns: [list of dict] Matching parts with a "status" color each.
def _search(conn, words, room, brand, stock, sort, limit) -> list[dict[str, Any]]:
    where = ["p.merged_into IS NULL"]
    args: list[Any] = []
    if words:
        match = " ".join('"' + w.replace('"', "") + '"*' for w in words)
        where.append("p.id IN (SELECT rowid FROM parts_fts WHERE parts_fts MATCH ?)")
        args.append(match)
    if room:
        where.append("p.room = ? COLLATE NOCASE")
        args.append(room)
    if brand:
        where.append("p.brand = ? COLLATE NOCASE")
        args.append(brand)
    if stock == "out":
        where.append("p.qty <= 0")
    elif stock == "low":
        where.append("p.qty > 0 AND p.min_qty IS NOT NULL AND p.qty <= p.min_qty")
    elif stock == "ok":
        where.append("p.qty > 0 AND (p.min_qty IS NULL OR p.qty > p.min_qty)")
    order = SORTS.get(sort, SORTS["name"])
    sql = (
        "SELECT p.id, p.part_no, p.name, p.brand, p.mfr_part_no, p.size_spec, p.room, p.location_text, p.qty,"
        " p.min_qty, p.max_qty, COALESCE(v.name, '') AS vendor FROM parts p LEFT JOIN vendors v ON v.id = p.vendor_id"
        f" WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?"
    )
    out = []
    for r in conn.execute(sql, [*args, limit]):
        d = dict(r)
        d["status"] = stock_status(d["qty"], d["min_qty"])
        out.append(d)
    return out


# Description: Fixes likely typos by comparing each search word with the words already used in
#     part names, brands, sizes, usage and part numbers.
#     For example, ["quik", "conect"] becomes ["quick", "connect"].
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: words - [list of str] The search words as typed.
# Returns: [list of str] The words with close matches swapped in.
def _respell(conn, words: list[str]) -> list[str]:
    vocab: set[str] = set()
    for r in conn.execute("SELECT name, brand, size_spec, usage, mfr_part_no FROM parts WHERE merged_into IS NULL"):
        for value in r:
            vocab.update(_WORD.findall((value or "").lower()))
    fixed = []
    for w in words:
        if w in vocab or any(v.startswith(w) for v in vocab):
            fixed.append(w)
            continue
        close = difflib.get_close_matches(w, vocab, n=1, cutoff=0.75)
        fixed.append(close[0] if close else w)
    return fixed


# Description: Lists the rooms and brands in use, for the filter dropdowns and for the
#     type-ahead suggestions on the Add part screen.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
# Returns: [dict] "rooms" and "brands", each a sorted list of names.
def facets(conn) -> dict[str, list[str]]:
    # Description: Lists the different values used in one part column, skipping blanks and
    #     merged parts. Only used inside facets().
    # Parameters:
    #     1: col - [str] The column name, "room" or "brand".
    # Returns: [list of str] The values, sorted alphabetically.
    def distinct(col):
        return [r[0] for r in conn.execute(
            f"SELECT DISTINCT {col} FROM parts WHERE merged_into IS NULL AND {col} != '' ORDER BY {col} COLLATE NOCASE"
        )]
    return {"rooms": distinct("room"), "brands": distinct("brand")}


# ======================================================================================
# ACTIVITY AND HOME SCREEN COUNTS
# The activity log and the numbers on the home screen tiles.
# ======================================================================================

# Description: Reads the activity log (check outs, check ins, additions, count changes),
#     newest first, with each line's stock color and the vendor's phone for reordering.
#     For example, the home screen shows the last 12; the Activity log shows up to 500.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: limit - [int] Most lines to return.
#     3: to_order_only - [bool] True to show only lines that left a part out or low.
#     4: day - [str or None] Only this date, as "YYYY-MM-DD" (optional).
# Returns: [list of dict] Activity lines with part name, who, WO/PO, qty left and status.
def recent_activity(conn, limit: int = 50, to_order_only: bool = False, day: str | None = None) -> list[dict[str, Any]]:
    where, args = ["a.action IN ('in', 'out', 'added', 'adjust', 'merge')"], []
    if day:
        where.append("date(a.at) = ?")
        args.append(day)
    if to_order_only:
        where.append("(a.qty_after <= 0 OR (p.min_qty IS NOT NULL AND a.qty_after <= p.min_qty))")
    sql = (
        "SELECT a.*, p.part_no, p.name, p.min_qty, COALESCE(v.name, '') AS vendor, COALESCE(v.phone, '') AS vendor_phone"
        " FROM activity a JOIN parts p ON p.id = a.part_id LEFT JOIN vendors v ON v.id = p.vendor_id"
        f" WHERE {' AND '.join(where)} ORDER BY a.at DESC, a.id DESC LIMIT ?"
    )
    out = []
    for r in conn.execute(sql, [*args, limit]):
        d = dict(r)
        d["status"] = stock_status(d["qty_after"], d["min_qty"])
        out.append(d)
    return out


# Description: Counts for the home screen tiles: total parts, how many are out,
#     how many are low, and how many vendors.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
# Returns: [dict] "parts", "out", "low" and "vendors" as whole numbers.
def summary(conn) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS parts,"
        " SUM(qty <= 0) AS out_of_stock,"
        " SUM(qty > 0 AND min_qty IS NOT NULL AND qty <= min_qty) AS low"
        " FROM parts WHERE merged_into IS NULL"
    ).fetchone()
    vendors = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    return {"parts": row["parts"] or 0, "out": row["out_of_stock"] or 0, "low": row["low"] or 0, "vendors": vendors}


# ======================================================================================
# VENDORS AND PEOPLE
# The vendor list and the people shown as buttons on the check-out screen.
# ======================================================================================

VENDOR_FIELDS = ("name", "rep", "phone", "email", "after_hours", "website", "account_no", "lead_time", "notes")


# Description: Adds a vendor to the vendor list. Names must be unique (ignoring capitals).
#     For example, "+ New vendor..." on the Add part screen and the Vendors page use it.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: data - [dict] The vendor's fields (see VENDOR_FIELDS); "name" is required.
# Returns: [dict] The saved vendor, same shape as get_vendor().
# Raises: ServiceError if the name is blank or already in the list.
def create_vendor(conn, data: dict[str, Any]) -> dict[str, Any]:
    fields = {k: (data.get(k) or "").strip() for k in VENDOR_FIELDS}
    if not fields["name"]:
        raise ServiceError("Give the vendor a name.")
    with transaction(conn):
        if conn.execute("SELECT 1 FROM vendors WHERE name = ?", (fields["name"],)).fetchone():
            raise ServiceError(f"{fields['name']} is already in the vendor list.")
        cur = conn.execute(
            f"INSERT INTO vendors({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})", list(fields.values())
        )
    return get_vendor(conn, cur.lastrowid)


# Description: Reads one vendor's details and the parts bought from them.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: vendor_id - [int] The vendor's database ID.
# Returns: [dict] The vendor's fields plus "parts" (list of their parts with stock).
# Raises: NotFound if the vendor doesn't exist.
def get_vendor(conn, vendor_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    if not row:
        raise NotFound("That vendor doesn't exist.")
    v = dict(row)
    v["parts"] = [dict(r) for r in conn.execute(
        "SELECT id, part_no, name, qty, min_qty FROM parts WHERE vendor_id = ? AND merged_into IS NULL ORDER BY name COLLATE NOCASE",
        (vendor_id,),
    )]
    return v


# Description: Lists vendors, optionally filtered by name, rep or phone, with how many
#     parts each one supplies.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: q - [str] Text to look for (optional; blank lists all).
# Returns: [list of dict] Vendors sorted by name, each with "part_count".
def list_vendors(conn, q: str = "") -> list[dict[str, Any]]:
    like = f"%{q.strip()}%"
    return [dict(r) for r in conn.execute(
        "SELECT v.*, (SELECT COUNT(*) FROM parts p WHERE p.vendor_id = v.id AND p.merged_into IS NULL) AS part_count"
        " FROM vendors v WHERE v.name LIKE ? OR v.rep LIKE ? OR v.phone LIKE ? ORDER BY v.name COLLATE NOCASE",
        (like, like, like),
    )]


# Description: Lists the people shown as one-tap buttons on the check-out screen.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
# Returns: [list of dict] Active people sorted by initials.
def list_people(conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM people WHERE active = 1 ORDER BY initials")]


# Description: Adds a person to the check-out list, or updates their name and role if their
#     initials are already there.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: initials - [str] 1 to 4 letters, e.g. "AB". Stored in capitals.
#     3: name - [str] Full name (optional).
#     4: role - [str] Shown on their button, e.g. "Day shift" (optional).
# Returns: [dict] The saved person.
# Raises: ServiceError if the initials aren't 1 to 4 letters.
def add_person(conn, initials: str, name: str = "", role: str = "") -> dict[str, Any]:
    initials = (initials or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{1,4}", initials):
        raise ServiceError("Initials must be 1 to 4 letters.")
    conn.execute(
        "INSERT INTO people(initials, name, role) VALUES (?, ?, ?)"
        " ON CONFLICT(initials) DO UPDATE SET name = excluded.name, role = excluded.role, active = 1",
        (initials, name.strip(), role.strip()),
    )
    return dict(conn.execute("SELECT * FROM people WHERE initials = ?", (initials,)).fetchone())


# ======================================================================================
# LABELS AND PRINTING
# Label sizes (label_presets), drawing labels, and the print queue (print_jobs).
# If the printer isn't available, jobs wait in the queue instead of being lost.
# ======================================================================================

# Description: Lists the label sizes shown on the print screen, in display order.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
# Returns: [list of dict] Each size's name, description, width_in, height_in and whether the
#     part name and location print by default.
def list_presets(conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM label_presets ORDER BY sort, id")]


# Description: Reads one label size.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: preset_id - [int] The label size's database ID.
# Returns: [dict] The label size's fields.
# Raises: NotFound if it doesn't exist.
def get_preset(conn, preset_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM label_presets WHERE id = ?", (preset_id,)).fetchone()
    if not row:
        raise NotFound("That label size doesn't exist.")
    return dict(row)


# Description: Adds a new label size, or changes an existing one, after checking the name
#     and that the size is between 0.25 and 4 inches.
#     For example, adding a "1 x 0.5 in" size to match new label tape.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: data - [dict] "name", "width_in", "height_in", and optionally "description",
#        "show_name", "show_location" and "sort".
#     3: preset_id - [int or None] The size to change, or None to add a new one.
# Returns: [dict] The saved label size.
# Raises: ServiceError for a blank or duplicate name or a size out of range.
def save_preset(conn, data: dict[str, Any], preset_id: int | None = None) -> dict[str, Any]:
    name = (data.get("name") or "").strip()
    if not name:
        raise ServiceError("Give the label size a name.")
    try:
        w, h = float(data.get("width_in")), float(data.get("height_in"))
    except (TypeError, ValueError):
        raise ServiceError("Width and height must be numbers (inches).") from None
    if not (0.25 <= w <= 4 and 0.25 <= h <= 4):
        raise ServiceError("Width and height must be between 0.25 and 4 inches.")
    vals = (name, (data.get("description") or "").strip(), w, h, int(bool(data.get("show_name", True))),
            int(bool(data.get("show_location", True))), int(data.get("sort") or 0))
    try:
        if preset_id is None:
            cur = conn.execute(
                "INSERT INTO label_presets(name, description, width_in, height_in, show_name, show_location, sort)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)", vals)
            preset_id = cur.lastrowid
        else:
            get_preset(conn, preset_id)
            conn.execute(
                "UPDATE label_presets SET name = ?, description = ?, width_in = ?, height_in = ?, show_name = ?,"
                " show_location = ?, sort = ? WHERE id = ?", (*vals, preset_id))
    except sqlite3.IntegrityError:
        raise ServiceError(f"There's already a label size called {name}.") from None
    return get_preset(conn, preset_id)


# Description: Draws a part's label as a black-and-white image at the printer's resolution.
#     The same image is used for the preview, the PDF and the printer, so what you see is
#     what prints.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
#     3: preset_id - [int] The label size to use.
#     4: show_name - [bool or None] Print the part name; None uses the size's default.
#     5: show_location - [bool or None] Print the location; None uses the size's default.
# Returns: [PIL.Image.Image] The label image.
# Raises: ServiceError if the barcode won't fit (e.g. a long code on a small label).
def label_image(conn, part_id: int, preset_id: int, show_name: bool | None = None, show_location: bool | None = None):
    from . import labels

    part = _part_row(conn, part_id)
    preset = get_preset(conn, preset_id)
    location = ", ".join(x for x in (part["room"], part["location_text"]) if x)
    size = labels.LabelSize(
        preset["width_in"], preset["height_in"],
        bool(preset["show_name"]) if show_name is None else show_name,
        bool(preset["show_location"]) if show_location is None else show_location,
    )
    try:
        return labels.render(labels.LabelContent(part["part_no"], part["name"], location), size,
                             int(get_setting(conn, "label_dpi")))
    except labels.LabelTooSmall as exc:
        raise ServiceError(str(exc)) from None


# Description: Reports whether printing is possible on this PC, which printers Windows
#     knows about, which one will be used, and how many jobs are waiting.
#     For example, the print screen's green "Brady BMP71 ready" badge comes from this.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
# Returns: [dict] "can_print" (bool), "printers" (list), "selected" (str or None),
#     "saved" (str) and "waiting" (int).
def printer_status(conn) -> dict[str, Any]:
    from . import printing

    saved = get_setting(conn, "label_printer")
    return {
        "can_print": printing.available(),
        "printers": printing.list_printers(),
        "selected": printing.pick_printer(saved),
        "saved": saved,
        "waiting": conn.execute("SELECT COUNT(*) FROM print_jobs WHERE status = 'waiting'").fetchone()[0],
    }


# Description: Tries to print one queued job. On success the job is marked printed; on
#     failure the reason (printer off, out of tape, not on Windows) is saved on the job and
#     it stays in the queue. Used by print_label() and retry_print_jobs().
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: job - [sqlite3.Row] The print_jobs row to print.
# Returns: [tuple of (bool, str)] (True, "") if it printed, or (False, reason) if not.
def _run_job(conn, job: sqlite3.Row) -> tuple[bool, str]:
    from . import printing

    preset = get_preset(conn, job["preset_id"])
    img = label_image(conn, job["part_id"], job["preset_id"], bool(job["show_name"]), bool(job["show_location"]))
    printer = printing.pick_printer(get_setting(conn, "label_printer"))
    try:
        if not printing.available():
            raise printing.PrinterUnavailable("Printing only works on the Windows shop PC.")
        if not printer:
            raise printing.PrinterUnavailable("No label printer found. Check that the Brady is on and plugged in.")
        part = _part_row(conn, job["part_id"])
        printing.print_labels(printer, [img] * job["copies"], preset["width_in"], preset["height_in"], f"Label {part['part_no']}")
    except printing.PrinterUnavailable as exc:
        conn.execute("UPDATE print_jobs SET error = ? WHERE id = ?", (str(exc), job["id"]))
        return False, str(exc)
    conn.execute("UPDATE print_jobs SET status = 'printed', error = '' WHERE id = ?", (job["id"],))
    return True, ""


# Description: Prints labels for a part right away, or keeps the job in the print queue if
#     the printer isn't available, so nothing is lost.
#     For example, the "Print 1 label" button calls this through POST /api/parts/ID/print.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: part_id - [int] The part's database ID.
#     3: preset_id - [int] The label size.
#     4: copies - [int] How many labels, 1 to 200.
#     5: show_name - [bool] Print the part name.
#     6: show_location - [bool] Print the location.
#     7: who - [str] Initials of the person printing (optional).
# Returns: [dict] "printed" (bool), "queued" (bool), "error" (the reason it didn't print,
#     or "") and "job_id".
# Raises: ServiceError for a bad number of copies or a label the barcode won't fit on.
def print_label(conn, part_id: int, preset_id: int, copies: int, show_name: bool, show_location: bool, who: str = "") -> dict[str, Any]:
    copies = _whole(copies, "Copies")
    if not 1 <= copies <= 200:
        raise ServiceError("Copies must be between 1 and 200.")
    label_image(conn, part_id, preset_id, show_name, show_location)  # fail early if it doesn't fit
    cur = conn.execute(
        "INSERT INTO print_jobs(part_id, preset_id, copies, show_name, show_location, who) VALUES (?, ?, ?, ?, ?, ?)",
        (part_id, preset_id, copies, int(show_name), int(show_location), (who or "").upper()),
    )
    job = conn.execute("SELECT * FROM print_jobs WHERE id = ?", (cur.lastrowid,)).fetchone()
    ok, error = _run_job(conn, job)
    return {"printed": ok, "queued": not ok, "error": error, "job_id": job["id"]}


# Description: Lists print jobs in one state, oldest first, with the part and label size.
#     For example, the "Waiting to print" page shows the "waiting" ones.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: status - [str] "waiting", "printed" or "cancelled".
# Returns: [list of dict] The jobs.
def list_print_jobs(conn, status: str = "waiting") -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT j.*, p.part_no, p.name, l.name AS preset FROM print_jobs j JOIN parts p ON p.id = j.part_id"
        " JOIN label_presets l ON l.id = j.preset_id WHERE j.status = ? ORDER BY j.id", (status,)
    )]


# Description: Tries to print every waiting job, oldest first, stopping at the first
#     failure so the printer isn't sent jobs it can't handle.
#     For example, the home screen's "Print now" button.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
# Returns: [dict] "printed" (how many printed), "waiting" (how many are left) and
#     "error" (why it stopped, or "").
def retry_print_jobs(conn) -> dict[str, Any]:
    printed, last_error = 0, ""
    for job in conn.execute("SELECT * FROM print_jobs WHERE status = 'waiting' ORDER BY id").fetchall():
        ok, err = _run_job(conn, job)
        if not ok:
            last_error = err
            break
        printed += 1
    waiting = conn.execute("SELECT COUNT(*) FROM print_jobs WHERE status = 'waiting'").fetchone()[0]
    return {"printed": printed, "waiting": waiting, "error": last_error}


# Description: Removes a waiting job from the print queue (it is kept in history as cancelled).
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: job_id - [int] The print job's ID.
# Returns: Nothing. Jobs that already printed are left alone.
def cancel_print_job(conn, job_id: int) -> None:
    conn.execute("UPDATE print_jobs SET status = 'cancelled' WHERE id = ? AND status = 'waiting'", (job_id,))


# Description: Remembers which Windows printer to send labels to.
#     Leave it blank to pick the first printer with "Brady" or "BMP" in its name.
# Parameters:
#     1: conn - [sqlite3.Connection] The open database (from db.connect).
#     2: name - [str] The printer's name exactly as Windows lists it.
# Returns: Nothing.
def set_label_printer(conn, name: str) -> None:
    set_setting(conn, "label_printer", name.strip())
