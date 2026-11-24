"""Business rules. The API and any future tools (Excel import, phone app,
AI helpers) call these functions instead of writing SQL themselves."""
from __future__ import annotations

import difflib
import re
import sqlite3
from typing import Any, Iterable

from . import config
from .db import transaction


class ServiceError(ValueError):
    """A problem the person at the screen can fix. The message is shown as-is."""


class NotFound(ServiceError):
    pass


PART_FIELDS = (
    "name", "brand", "mfr_part_no", "size_spec", "usage", "description", "room",
    "location_text", "min_qty", "max_qty", "vendor_id", "supplier_part_no", "notes",
)
TEXT_FIELDS = {f for f in PART_FIELDS if f not in ("min_qty", "max_qty", "vendor_id")}


# ---------------------------------------------------------------- settings

def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    if key in config.DEFAULT_SETTINGS:
        return config.DEFAULT_SETTINGS[key]
    raise KeyError(key)


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    if key not in config.DEFAULT_SETTINGS:
        raise ServiceError(f"Unknown setting: {key}")
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {k: get_setting(conn, k) for k in config.DEFAULT_SETTINGS}


# ---------------------------------------------------------------- codes

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def normalize_code(code: str) -> str:
    """Clean what a scanner typed: drop Enter/Tab/control characters and edge spaces."""
    return _CONTROL.sub("", code or "").strip()


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


def stock_status(qty: int, min_qty: int | None) -> str:
    if qty <= 0:
        return "out"
    if min_qty is not None and qty <= min_qty:
        return "low"
    return "ok"


# ---------------------------------------------------------------- parts

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


def create_part(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    who: str,
    barcodes: Iterable[str] = (),
) -> dict[str, Any]:
    """Add a part. Blank part_no gets the next STJ number. The part number is
    always a barcode for the part; `barcodes` adds others (for example the
    manufacturer code that was scanned on the Unregistered screen)."""
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


def lookup_code(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    """What a scan opens. Follows merges so old labels still work."""
    code = normalize_code(code)
    if not code:
        return None
    row = conn.execute("SELECT part_id FROM barcodes WHERE code = ?", (code,)).fetchone()
    if not row:
        return None
    return get_part(conn, _follow_merge(conn, row["part_id"]))


def _follow_merge(conn: sqlite3.Connection, part_id: int) -> int:
    seen = set()
    while part_id not in seen:
        seen.add(part_id)
        row = conn.execute("SELECT merged_into FROM parts WHERE id = ?", (part_id,)).fetchone()
        if not row or row["merged_into"] is None:
            return part_id
        part_id = row["merged_into"]
    return part_id


def _part_row(conn: sqlite3.Connection, part_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
    if not row:
        raise NotFound("That part doesn't exist.")
    return row


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


# ---------------------------------------------------------------- stock moves

def _whole(value: Any, label: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ServiceError(f"{label} must be a whole number.") from None
    if n < 0:
        raise ServiceError(f"{label} can't be negative.")
    return n


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


def check_out(conn, part_id: int, qty: int, who: str, ref: str = "") -> dict[str, Any]:
    return _move(conn, part_id, qty, who, ref, "out")


def check_in(conn, part_id: int, qty: int, who: str, ref: str = "") -> dict[str, Any]:
    return _move(conn, part_id, qty, who, ref, "in")


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


def merge_parts(conn, keep_id: int, remove_id: int, who: str) -> dict[str, Any]:
    """Fold a duplicate into the part to keep: barcodes move over (old labels
    keep working), stock is added together, history stays on both."""
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


# ---------------------------------------------------------------- search

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
    """Library search. Every word must match some field (prefixes count).
    If nothing matches, retry with close spellings so typos still find parts."""
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


def facets(conn) -> dict[str, list[str]]:
    def distinct(col):
        return [r[0] for r in conn.execute(
            f"SELECT DISTINCT {col} FROM parts WHERE merged_into IS NULL AND {col} != '' ORDER BY {col} COLLATE NOCASE"
        )]
    return {"rooms": distinct("room"), "brands": distinct("brand")}


# ---------------------------------------------------------------- activity

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


def summary(conn) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS parts,"
        " SUM(qty <= 0) AS out_of_stock,"
        " SUM(qty > 0 AND min_qty IS NOT NULL AND qty <= min_qty) AS low"
        " FROM parts WHERE merged_into IS NULL"
    ).fetchone()
    vendors = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    return {"parts": row["parts"] or 0, "out": row["out_of_stock"] or 0, "low": row["low"] or 0, "vendors": vendors}


# ---------------------------------------------------------------- vendors and people

VENDOR_FIELDS = ("name", "rep", "phone", "email", "after_hours", "website", "account_no", "lead_time", "notes")


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


def list_vendors(conn, q: str = "") -> list[dict[str, Any]]:
    like = f"%{q.strip()}%"
    return [dict(r) for r in conn.execute(
        "SELECT v.*, (SELECT COUNT(*) FROM parts p WHERE p.vendor_id = v.id AND p.merged_into IS NULL) AS part_count"
        " FROM vendors v WHERE v.name LIKE ? OR v.rep LIKE ? OR v.phone LIKE ? ORDER BY v.name COLLATE NOCASE",
        (like, like, like),
    )]


def list_people(conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM people WHERE active = 1 ORDER BY initials")]


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


# ---------------------------------------------------------------- labels and printing

def list_presets(conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM label_presets ORDER BY sort, id")]


def get_preset(conn, preset_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM label_presets WHERE id = ?", (preset_id,)).fetchone()
    if not row:
        raise NotFound("That label size doesn't exist.")
    return dict(row)


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


def print_label(conn, part_id: int, preset_id: int, copies: int, show_name: bool, show_location: bool, who: str = "") -> dict[str, Any]:
    """Print now if the printer is there; otherwise keep the job in the queue to print later."""
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


def list_print_jobs(conn, status: str = "waiting") -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT j.*, p.part_no, p.name, l.name AS preset FROM print_jobs j JOIN parts p ON p.id = j.part_id"
        " JOIN label_presets l ON l.id = j.preset_id WHERE j.status = ? ORDER BY j.id", (status,)
    )]


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


def cancel_print_job(conn, job_id: int) -> None:
    conn.execute("UPDATE print_jobs SET status = 'cancelled' WHERE id = ? AND status = 'waiting'", (job_id,))


def set_label_printer(conn, name: str) -> None:
    set_setting(conn, "label_printer", name.strip())
