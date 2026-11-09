"""
database.py
===========
SQLite3 data layer for the Primary ESL DZ bot.

Responsibilities
----------------
* Schema creation + lightweight automatic migrations.
* CRUD for resources (uploaded teaching files) and users.
* Ranked keyword search (case-insensitive ``LIKE %token%`` across title,
  level, unit, category, tags and file name) with pagination.
* Guided browse queries + facet counts for the inline menus.
* Telegram-channel backup / restore, because Render's free tier has an
  *ephemeral* filesystem (the disk is wiped on every restart / redeploy):

    - ``backup_to_channel()`` takes a consistent snapshot with SQLite's
      online-backup API, posts it to the private archive channel with the
      ``#DB_BACKUP`` caption and pins it.
    - ``restore_from_channel()`` reads the channel's *pinned* message on
      boot, downloads the latest backup and restores it automatically.
    - ``restore_from_bytes()`` / ``restore_from_file()`` back the admin
      ``/restore_db`` command.

All public methods are thread-safe (one re-entrant lock serialises access;
a short-lived connection is opened per operation, which is the most robust
pattern for SQLite inside a multi-threaded webhook server).
"""

import html
import logging
import math
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

import curriculum

log = logging.getLogger("esl.database")

SQLITE_HEADER = b"SQLite format 3\x00"
BACKUP_TAG = "#DB_BACKUP"
LIKE_ESCAPE = "ESCAPE '\\'"
MAX_QUERY_TOKENS = 8

VALID_STATUSES = ("pending", "approved", "rejected")

# ---------------------------------------------------------------------------
# SCHEMA
# ---------------------------------------------------------------------------
SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS resources (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id           TEXT    NOT NULL,
    file_unique_id    TEXT,
    file_type         TEXT    NOT NULL,
    file_name         TEXT,
    file_size         INTEGER,
    mime_type         TEXT,
    title             TEXT    NOT NULL,
    level             TEXT    NOT NULL,
    unit              TEXT    NOT NULL,
    category          TEXT    NOT NULL,
    tags              TEXT    NOT NULL DEFAULT '',
    uploader_id       INTEGER NOT NULL,
    uploader_name     TEXT,
    uploader_username TEXT,
    status            TEXT    NOT NULL DEFAULT 'pending',
    downloads         INTEGER NOT NULL DEFAULT 0,
    archive_msg_id    INTEGER,
    admin_msg_id      INTEGER,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    approved_at       TEXT
);

CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    full_name   TEXT,
    username    TEXT,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_resources_status    ON resources(status);
CREATE INDEX IF NOT EXISTS idx_resources_browse    ON resources(status, level, unit, category);
CREATE INDEX IF NOT EXISTS idx_resources_unique    ON resources(file_unique_id);
CREATE INDEX IF NOT EXISTS idx_resources_downloads ON resources(downloads);
"""

# Columns that must exist (used to migrate databases restored from older
# backups). Format: table -> {column: SQL definition used by ALTER TABLE}.
REQUIRED_COLUMNS: Dict[str, Dict[str, str]] = {
    "resources": {
        "file_unique_id": "TEXT",
        "file_name": "TEXT",
        "file_size": "INTEGER",
        "mime_type": "TEXT",
        "tags": "TEXT NOT NULL DEFAULT ''",
        "uploader_name": "TEXT",
        "uploader_username": "TEXT",
        "downloads": "INTEGER NOT NULL DEFAULT 0",
        "archive_msg_id": "INTEGER",
        "admin_msg_id": "INTEGER",
        "approved_at": "TEXT",
    },
    "users": {
        "full_name": "TEXT",
        "username": "TEXT",
    },
}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so user input is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def tokenize_query(query: str) -> List[str]:
    """
    Turn a free-text teacher query into normalised search tokens.

    * lower-cases and strips punctuation (Unicode aware: Arabic/French kept)
    * maps synonyms (``3ap`` -> ``3ps``, ``quiz`` -> ``exam`` ...)
    * glues ``unit 3`` / ``seq 2`` into a single phrase token
    * strips simple English plurals (``worksheets`` -> ``worksheet``)
    * drops stop-words unless the query consists only of stop-words
    """
    if not query:
        return []
    cleaned = re.sub(r"[^\w\s]", " ", query.lower())
    raw = cleaned.split()

    tokens: List[str] = []
    i = 0
    while i < len(raw):
        word = raw[i]
        # "unit 3" / "seq 2" / "sequence 1" -> phrase token
        if word in ("unit", "seq", "sequence") and i + 1 < len(raw) and raw[i + 1].isdigit():
            tokens.append(f"{'seq' if word != 'unit' else 'unit'} {raw[i + 1]}")
            i += 2
            continue
        if word in curriculum.SEARCH_SYNONYMS:
            tokens.append(curriculum.SEARCH_SYNONYMS[word])  # may be a phrase
        else:
            if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
                word = word[:-1]
            tokens.append(word)
        i += 1

    meaningful = [t for t in tokens if t not in curriculum.STOPWORDS and len(t) >= 2]
    if not meaningful:
        meaningful = [t for t in tokens if t]

    unique: List[str] = []
    for tok in meaningful:
        if tok not in unique:
            unique.append(tok)
    return unique[:MAX_QUERY_TOKENS]


def normalize_tags(text: str) -> str:
    """'Phonics, CVC  family #father' -> 'phonics cvc family father'."""
    if not text:
        return ""
    words = re.split(r"[\s,;،#]+", text.lower())
    out: List[str] = []
    for w in words:
        w = re.sub(r"[^\w\-]", "", w).strip("-_")
        if len(w) >= 2 and w not in out:
            out.append(w[:40])
        if len(out) >= 30:
            break
    return " ".join(out)


# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------
class Database:
    def __init__(self, db_path: str = "esl_library.db"):
        self.db_path = os.path.abspath(db_path)
        folder = os.path.dirname(self.db_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._lock = threading.RLock()
        self._backup_lock = threading.Lock()
        # Change counters used to know if there is unsaved data worth backing up.
        self._changes = 0
        self._saved_changes = 0
        self.init_schema()

    # ------------------------------------------------------------------ core
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _mark_dirty(self) -> None:
        self._changes += 1

    def has_unsaved_changes(self) -> bool:
        return self._changes > self._saved_changes

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_TABLES)
            self._migrate(conn)
            conn.executescript(SCHEMA_INDEXES)
        log.info("Database ready at %s", self.db_path)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        for table, columns in REQUIRED_COLUMNS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    log.warning("Migrating: adding %s.%s", table, column)
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row is not None else None

    @staticmethod
    def _rows(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        # Meta bookkeeping is intentionally NOT marked dirty (avoids backup loops).
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ----------------------------------------------------------------- users
    def touch_user(self, user_id: int, full_name: str = "", username: str = "") -> None:
        now = utcnow()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE users SET full_name = ?, username = ?, last_seen = ? WHERE user_id = ?",
                (full_name, username, now, user_id),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO users(user_id, full_name, username, first_seen, last_seen) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (user_id, full_name, username, now, now),
                )
                self._mark_dirty()

    def count_users(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ------------------------------------------------------------- resources
    def add_resource(
        self,
        *,
        file_id: str,
        file_type: str,
        title: str,
        level: str,
        unit: str,
        category: str,
        uploader_id: int,
        tags: str = "",
        file_unique_id: Optional[str] = None,
        file_name: Optional[str] = None,
        file_size: Optional[int] = None,
        mime_type: Optional[str] = None,
        uploader_name: Optional[str] = None,
        uploader_username: Optional[str] = None,
        status: str = "pending",
    ) -> int:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        if file_type not in curriculum.FILE_TYPES:
            raise ValueError(f"Invalid file type: {file_type}")
        now = utcnow()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO resources (
                    file_id, file_unique_id, file_type, file_name, file_size, mime_type,
                    title, level, unit, category, tags,
                    uploader_id, uploader_name, uploader_username,
                    status, created_at, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id, file_unique_id, file_type, file_name, file_size, mime_type,
                    title.strip(), level, unit, category, normalize_tags(tags),
                    uploader_id, uploader_name, uploader_username,
                    status, now, now if status == "approved" else None,
                ),
            )
            self._mark_dirty()
            return int(cur.lastrowid)

    def get_resource(self, resource_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
        return self._row(row)

    def find_duplicate(self, file_unique_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Return an existing pending/approved resource with the same file, if any."""
        if not file_unique_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM resources WHERE file_unique_id = ? "
                "AND status IN ('pending', 'approved') ORDER BY id LIMIT 1",
                (file_unique_id,),
            ).fetchone()
        return self._row(row)

    def set_status(self, resource_id: int, status: str) -> bool:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        with self._connect() as conn:
            if status == "approved":
                cur = conn.execute(
                    "UPDATE resources SET status = ?, approved_at = ? WHERE id = ?",
                    (status, utcnow(), resource_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE resources SET status = ? WHERE id = ?", (status, resource_id)
                )
            self._mark_dirty()
            return cur.rowcount > 0

    def claim_pending(self, resource_id: int, new_status: str) -> bool:
        """
        Atomically move a resource from 'pending' to ``new_status``.
        Returns False if it was already processed (protects against double clicks).
        """
        if new_status not in ("approved", "rejected"):
            raise ValueError("new_status must be 'approved' or 'rejected'")
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE resources SET status = ?, approved_at = CASE WHEN ? = 'approved' "
                "THEN ? ELSE approved_at END WHERE id = ? AND status = 'pending'",
                (new_status, new_status, utcnow(), resource_id),
            )
            if cur.rowcount:
                self._mark_dirty()
            return cur.rowcount > 0

    def set_archive_msg(self, resource_id: int, message_id: Optional[int]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE resources SET archive_msg_id = ? WHERE id = ?", (message_id, resource_id)
            )
            self._mark_dirty()

    def set_admin_msg(self, resource_id: int, message_id: Optional[int]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE resources SET admin_msg_id = ? WHERE id = ?", (message_id, resource_id)
            )
            self._mark_dirty()

    def delete_resource(self, resource_id: int) -> Optional[Dict[str, Any]]:
        """Delete a resource and return the deleted row (or None if not found)."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
            self._mark_dirty()
            return dict(row)

    def increment_downloads(self, resource_id: int) -> int:
        with self._connect() as conn:
            conn.execute(
                "UPDATE resources SET downloads = downloads + 1 WHERE id = ?", (resource_id,)
            )
            row = conn.execute(
                "SELECT downloads FROM resources WHERE id = ?", (resource_id,)
            ).fetchone()
            self._mark_dirty()
        return int(row["downloads"]) if row else 0

    # ---------------------------------------------------------------- search
    def search(
        self, query: str, limit: int = 10, offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Ranked keyword search over approved resources.

        Every token is matched case-insensitively with ``LIKE %token%``
        against title, level, unit, category, tags and file name.
        A resource must match at least half of the tokens (rounded up);
        results are ordered by number of matched tokens, then title hits,
        then popularity.

        Returns ``(rows, total_count)``.
        """
        tokens = tokenize_query(query)
        if not tokens:
            return [], 0

        haystack = (
            "LOWER(r.title || ' ' || r.level || ' ' || r.unit || ' ' || r.category || ' ' "
            "|| IFNULL(r.tags, '') || ' ' || IFNULL(r.file_name, ''))"
        )
        match_parts, title_parts, params = [], [], []
        patterns = [f"%{escape_like(t)}%" for t in tokens]
        for _ in patterns:
            match_parts.append(f"(CASE WHEN {haystack} LIKE ? {LIKE_ESCAPE} THEN 1 ELSE 0 END)")
            title_parts.append(f"(CASE WHEN LOWER(r.title) LIKE ? {LIKE_ESCAPE} THEN 1 ELSE 0 END)")
        params.extend(patterns)  # for match_parts
        params.extend(patterns)  # for title_parts

        inner = (
            f"SELECT r.*, ({' + '.join(match_parts)}) AS matches, "
            f"({' + '.join(title_parts)}) AS title_hits "
            f"FROM resources r WHERE r.status = 'approved'"
        )
        min_matches = max(1, math.ceil(len(tokens) / 2))

        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM ({inner}) WHERE matches >= ?", (*params, min_matches)
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM ({inner}) WHERE matches >= ? "
                f"ORDER BY matches DESC, title_hits DESC, downloads DESC, id DESC "
                f"LIMIT ? OFFSET ?",
                (*params, min_matches, limit, offset),
            ).fetchall()
        return self._rows(rows), int(total)

    # ---------------------------------------------------------------- browse
    def browse(
        self,
        level: str,
        unit: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where = ["status = 'approved'", "level = ?"]
        params: List[Any] = [level]
        if unit:
            where.append("unit = ?")
            params.append(unit)
        if category:
            where.append("category = ?")
            params.append(category)
        clause = " AND ".join(where)
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM resources WHERE {clause}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM resources WHERE {clause} "
                f"ORDER BY title COLLATE NOCASE ASC, id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return self._rows(rows), int(total)

    def facet_counts(self, level: Optional[str] = None, unit: Optional[str] = None) -> Dict[str, Dict[str, int]]:
        """Counts of approved resources grouped by level, unit and category."""
        where = ["status = 'approved'"]
        params: List[Any] = []
        if level:
            where.append("level = ?")
            params.append(level)
        if unit:
            where.append("unit = ?")
            params.append(unit)
        clause = " AND ".join(where)
        result: Dict[str, Dict[str, int]] = {"levels": {}, "units": {}, "categories": {}}
        with self._connect() as conn:
            for col, key in (("level", "levels"), ("unit", "units"), ("category", "categories")):
                for row in conn.execute(
                    f"SELECT {col} AS k, COUNT(*) AS n FROM resources WHERE {clause} GROUP BY {col}",
                    params,
                ):
                    result[key][row["k"]] = row["n"]
        return result

    def top_downloads(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resources WHERE status = 'approved' "
                "ORDER BY downloads DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return self._rows(rows)

    def latest(self, limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resources WHERE status = 'approved' "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return self._rows(rows)

    def list_pending(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resources WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return self._rows(rows)

    def count_resources(self, status: Optional[str] = None) -> int:
        with self._connect() as conn:
            if status:
                return conn.execute(
                    "SELECT COUNT(*) FROM resources WHERE status = ?", (status,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]

    def stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            by_status = {
                row["status"]: row["n"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM resources GROUP BY status"
                )
            }
            total_downloads = conn.execute(
                "SELECT IFNULL(SUM(downloads), 0) FROM resources WHERE status = 'approved'"
            ).fetchone()[0]
            by_level = {
                row["level"]: row["n"]
                for row in conn.execute(
                    "SELECT level, COUNT(*) AS n FROM resources "
                    "WHERE status = 'approved' GROUP BY level"
                )
            }
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            contributors = conn.execute(
                "SELECT COUNT(DISTINCT uploader_id) FROM resources WHERE status = 'approved'"
            ).fetchone()[0]
            last_backup = conn.execute(
                "SELECT value FROM meta WHERE key = 'last_backup_at'"
            ).fetchone()
        return {
            "approved": by_status.get("approved", 0),
            "pending": by_status.get("pending", 0),
            "rejected": by_status.get("rejected", 0),
            "total": sum(by_status.values()),
            "downloads": int(total_downloads),
            "by_level": by_level,
            "users": users,
            "contributors": contributors,
            "last_backup_at": last_backup["value"] if last_backup else None,
        }

    # ================================================================ BACKUP
    def create_snapshot(self, dest_path: str) -> str:
        """Write a consistent copy of the live DB to ``dest_path`` (online backup API)."""
        with self._lock:
            src = sqlite3.connect(self.db_path, timeout=30)
            dst = sqlite3.connect(dest_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        return dest_path

    def backup_to_channel(self, bot: Any, channel_id: Optional[int], reason: str = "") -> Optional[Any]:
        """
        Send a ``#DB_BACKUP`` snapshot to the archive channel and pin it so it
        can be found automatically on the next boot.
        Returns the sent Telegram message, or None on failure.
        """
        if not channel_id:
            log.warning("ARCHIVE_CHANNEL_ID not set — skipping backup.")
            return None

        with self._backup_lock:
            changes_at_snapshot = self._changes
            stamp = datetime.now(timezone.utc)
            tmp_dir = tempfile.mkdtemp(prefix="esl_backup_")
            path = os.path.join(tmp_dir, f"esl_library_{stamp:%Y%m%d_%H%M%S}.db")
            try:
                self.create_snapshot(path)
                s = self.stats()
                caption = (
                    f"{BACKUP_TAG}\n"
                    f"🗓 {stamp:%Y-%m-%d %H:%M:%S} UTC\n"
                    f"📚 Approved: {s['approved']} | ⏳ Pending: {s['pending']}\n"
                    f"📥 Downloads: {s['downloads']} | 👥 Users: {s['users']}\n"
                    f"📝 Reason: {html.escape(reason or 'manual')}\n\n"
                    f"♻️ Restore: reply to this file with /restore_db"
                )
                with open(path, "rb") as fh:
                    msg = bot.send_document(
                        channel_id,
                        fh,
                        caption=caption,
                        parse_mode="HTML",
                        disable_notification=True,
                    )

                previous = self.get_meta("last_backup_msg_id")
                try:
                    bot.pin_chat_message(channel_id, msg.message_id, disable_notification=True)
                    if previous and previous.isdigit() and int(previous) != msg.message_id:
                        try:
                            bot.unpin_chat_message(channel_id, int(previous))
                        except Exception:  # noqa: BLE001 — old pin may already be gone
                            pass
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Backup sent but could not be pinned (give the bot the "
                        "'Edit messages' / 'Pin messages' admin right in the channel): %s",
                        exc,
                    )

                self.set_meta("last_backup_msg_id", str(msg.message_id))
                self.set_meta("last_backup_at", f"{stamp:%Y-%m-%d %H:%M:%S} UTC")
                self._saved_changes = max(self._saved_changes, changes_at_snapshot)
                log.info("Database backup sent to channel (msg %s, reason=%s)", msg.message_id, reason)
                return msg
            except Exception:  # noqa: BLE001
                log.exception("Database backup to channel failed")
                return None
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # =============================================================== RESTORE
    @staticmethod
    def validate_db_file(path: str) -> None:
        """Raise ValueError if ``path`` is not a healthy ESL library database."""
        with open(path, "rb") as fh:
            header = fh.read(16)
        if header != SQLITE_HEADER:
            raise ValueError("This file is not a SQLite database.")
        conn = sqlite3.connect(path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"Database integrity check failed: {integrity}")
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if "resources" not in tables:
                raise ValueError("Backup does not contain a 'resources' table.")
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"Corrupted database: {exc}") from exc
        finally:
            conn.close()

    def restore_from_file(self, path: str) -> Dict[str, Any]:
        """Replace the live database with the content of ``path``."""
        self.validate_db_file(path)
        with self._lock:
            src = sqlite3.connect(path)
            dst = sqlite3.connect(self.db_path, timeout=30)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        self.init_schema()  # migrate older backups if needed
        self._saved_changes = self._changes  # live data == a backup now
        stats = self.stats()
        log.info("Database restored: %s", stats)
        return stats

    def restore_from_bytes(self, data: bytes) -> Dict[str, Any]:
        tmp_dir = tempfile.mkdtemp(prefix="esl_restore_")
        path = os.path.join(tmp_dir, "restore.db")
        try:
            with open(path, "wb") as fh:
                fh.write(data)
            return self.restore_from_file(path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def restore_from_channel(self, bot: Any, channel_id: Optional[int]) -> bool:
        """
        Auto-restore on boot: read the archive channel's pinned message and,
        if it is a ``#DB_BACKUP`` document, download and restore it.
        """
        if not channel_id:
            return False
        try:
            chat = bot.get_chat(channel_id)
            pinned = getattr(chat, "pinned_message", None)
            if not pinned or not getattr(pinned, "document", None):
                log.info("No pinned backup found in the archive channel.")
                return False
            if BACKUP_TAG not in (pinned.caption or ""):
                log.info("Pinned message in archive channel is not a %s file.", BACKUP_TAG)
                return False
            file_info = bot.get_file(pinned.document.file_id)
            data = bot.download_file(file_info.file_path)
            stats = self.restore_from_bytes(data)
            log.info(
                "Auto-restored database from channel backup (msg %s): %s approved, %s pending",
                pinned.message_id, stats["approved"], stats["pending"],
            )
            return True
        except Exception:  # noqa: BLE001
            log.exception("Automatic restore from channel failed")
            return False
