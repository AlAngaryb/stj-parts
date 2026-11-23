"""SQLite storage. One file, versioned with PRAGMA user_version."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config

# Each entry upgrades the database by one version. Never edit an entry
# that has shipped; add a new one instead.
MIGRATIONS: list[str] = [
    # 1: core tables
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE vendors (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
        rep         TEXT NOT NULL DEFAULT '',
        phone       TEXT NOT NULL DEFAULT '',
        email       TEXT NOT NULL DEFAULT '',
        after_hours TEXT NOT NULL DEFAULT '',
        website     TEXT NOT NULL DEFAULT '',
        account_no  TEXT NOT NULL DEFAULT '',
        lead_time   TEXT NOT NULL DEFAULT '',
        notes       TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE people (
        id       INTEGER PRIMARY KEY,
        initials TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name     TEXT NOT NULL DEFAULT '',
        role     TEXT NOT NULL DEFAULT '',
        active   INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE parts (
        id               INTEGER PRIMARY KEY,
        part_no          TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name             TEXT NOT NULL,
        brand            TEXT NOT NULL DEFAULT '',
        mfr_part_no      TEXT NOT NULL DEFAULT '',
        size_spec        TEXT NOT NULL DEFAULT '',
        usage            TEXT NOT NULL DEFAULT '',
        description      TEXT NOT NULL DEFAULT '',
        room             TEXT NOT NULL DEFAULT '',
        location_text    TEXT NOT NULL DEFAULT '',
        qty              INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0),
        min_qty          INTEGER,
        max_qty          INTEGER,
        vendor_id        INTEGER REFERENCES vendors(id),
        supplier_part_no TEXT NOT NULL DEFAULT '',
        notes            TEXT NOT NULL DEFAULT '',
        merged_into      INTEGER REFERENCES parts(id),
        created_by       TEXT NOT NULL DEFAULT '',
        created_at       TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Every code that should open a part: our own label plus any
    -- manufacturer barcodes. Merging parts moves codes here, so old
    -- labels keep working.
    CREATE TABLE barcodes (
        code       TEXT PRIMARY KEY COLLATE NOCASE,
        part_id    INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
        kind       TEXT NOT NULL DEFAULT 'other' CHECK (kind IN ('internal', 'mfr', 'other')),
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX barcodes_part ON barcodes(part_id);

    CREATE TABLE activity (
        id         INTEGER PRIMARY KEY,
        at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        part_id    INTEGER NOT NULL REFERENCES parts(id),
        action     TEXT NOT NULL CHECK (action IN ('in', 'out', 'added', 'adjust', 'edit', 'merge')),
        qty_change INTEGER NOT NULL DEFAULT 0,
        qty_after  INTEGER NOT NULL,
        who        TEXT NOT NULL DEFAULT '',
        ref        TEXT NOT NULL DEFAULT '',
        note       TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX activity_at ON activity(at);
    CREATE INDEX activity_part ON activity(part_id, at);

    -- Full-text index for the library search box.
    CREATE VIRTUAL TABLE parts_fts USING fts5(
        part_no, name, brand, mfr_part_no, size_spec, usage, description,
        room, location_text, supplier_part_no, vendor,
        tokenize='unicode61 remove_diacritics 2', prefix='2 3'
    );
    """,
]


def db_path() -> Path:
    return config.data_dir() / "parts.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or db_path()), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        with transaction(conn):
            for statement in _split(sql):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")


def _split(sql: str) -> list[str]:
    """Split a migration into statements (no semicolons inside our SQL strings)."""
    out, buf = [], []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt.rstrip(";").strip():
                out.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return out


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
