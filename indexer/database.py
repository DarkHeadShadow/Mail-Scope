"""SQLite schema and persistence helpers for the mail index."""

__author__ = "Bálint Szabolcs"

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sources.models import MessageLocator, MessageMetadata


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    discovered_sha256 TEXT NOT NULL,
    sha256_before TEXT,
    sha256_after TEXT,
    status TEXT NOT NULL,
    last_error TEXT,
    indexed_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    UNIQUE(source_id, path)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    message_id TEXT,
    thread_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    normalized_subject TEXT NOT NULL,
    sent_date TEXT,
    in_reply_to TEXT,
    references_header TEXT,
    body TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('from', 'to', 'cc', 'bcc')),
    address TEXT NOT NULL,
    UNIQUE(message_id, role, address)
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    UNIQUE(message_id, filename, content_type)
);

CREATE TABLE IF NOT EXISTS occurrences (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    locator_offset INTEGER NOT NULL,
    locator_length INTEGER NOT NULL,
    locator_folder_path TEXT NOT NULL DEFAULT '',
    locator_item_index INTEGER NOT NULL DEFAULT -1,
    locator_item_id TEXT NOT NULL DEFAULT '',
    message_hash TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    item_type TEXT NOT NULL DEFAULT 'mail',
    error TEXT,
    UNIQUE(
        source_id,
        folder_id,
        locator_offset,
        locator_folder_path,
        locator_item_index
    )
);

CREATE TABLE IF NOT EXISTS occurrence_labels (
    occurrence_id INTEGER NOT NULL REFERENCES occurrences(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    PRIMARY KEY(occurrence_id, label)
);

CREATE INDEX IF NOT EXISTS idx_occurrences_message ON occurrences(message_id);
CREATE INDEX IF NOT EXISTS idx_participants_address ON participants(address);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_key);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    subject,
    body,
    attachment_names,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS cleanup_orphaned_messages
AFTER DELETE ON occurrences
BEGIN
    DELETE FROM messages_fts
    WHERE rowid = OLD.message_id
      AND NOT EXISTS (
          SELECT 1 FROM occurrences WHERE message_id = OLD.message_id
      );
    DELETE FROM messages
    WHERE id = OLD.message_id
      AND NOT EXISTS (
          SELECT 1 FROM occurrences WHERE message_id = OLD.message_id
      );
END;
"""


class IndexDatabase:
    """Own the case-local SQLite database and its schema."""

    def __init__(self, path: str | Path) -> None:
        """Create the database parent directory and initialize its schema."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @classmethod
    def for_case(cls, case_directory: str | Path) -> "IndexDatabase":
        """Create or open the database under a case's private .mailscope directory."""
        return cls(Path(case_directory) / ".mailscope" / "index.sqlite3")

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a foreign-key-aware connection with explicit transaction handling."""
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create tables, indexes, triggers, and the required FTS5 virtual table."""
        try:
            with self.connection() as connection:
                connection.executescript(SCHEMA)
                connection.execute(
                    "INSERT OR IGNORE INTO app_metadata(key, value) VALUES (?, ?)",
                    ("creator", "Bálint Szabolcs"),
                )
        except sqlite3.OperationalError as error:
            if "fts5" in str(error).casefold():
                raise RuntimeError(
                    "A Mail-Scope indexhez SQLite FTS5 támogatás szükséges."
                ) from error
            raise

    def register_source(
        self,
        *,
        path: str,
        kind: str,
        size: int,
        discovered_sha256: str,
        sha256_before: str,
    ) -> tuple[int, bool]:
        """Register a source and return its ID and whether it was already complete."""
        normalized_path = str(Path(path).resolve())
        with self.connection() as connection:
            existing = connection.execute(
                """
                SELECT id, discovered_sha256, sha256_before, status
                FROM sources WHERE path = ?
                """,
                (normalized_path,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["sha256_before"] == sha256_before
                    and existing["discovered_sha256"] == discovered_sha256
                    and existing["status"] == "completed"
                ):
                    return int(existing["id"]), True
                if existing["sha256_before"] != sha256_before:
                    connection.execute(
                        "DELETE FROM sources WHERE id = ?", (existing["id"],)
                    )
                    existing = None

            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO sources (
                        path, kind, size, discovered_sha256, sha256_before,
                        status, last_error, indexed_count
                    ) VALUES (?, ?, ?, ?, ?, 'indexing', NULL, 0)
                    """,
                    (
                        normalized_path,
                        kind,
                        size,
                        discovered_sha256,
                        sha256_before,
                    ),
                )
                return int(cursor.lastrowid), False

            source_id = int(existing["id"])
            connection.execute(
                """
                UPDATE sources
                SET kind = ?, size = ?, discovered_sha256 = ?,
                    sha256_before = ?, sha256_after = NULL, status = 'indexing',
                    last_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (kind, size, discovered_sha256, sha256_before, source_id),
            )
            return source_id, False

    def mark_source(
        self,
        source_id: int,
        status: str,
        *,
        sha256_after: str | None = None,
        error: str | None = None,
    ) -> None:
        """Persist the indexing result and final source integrity hash."""
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE sources
                SET status = ?, sha256_after = ?, last_error = ?,
                    indexed_count = (
                        SELECT count(*) FROM occurrences WHERE source_id = ?
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, sha256_after, error, source_id, source_id),
            )

    def add_folder(self, source_id: int, path: str, name: str) -> int:
        """Insert or retrieve a source folder by its reader-relative path."""
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO folders(source_id, path, name)
                VALUES (?, ?, ?)
                ON CONFLICT(source_id, path) DO UPDATE SET name = excluded.name
                """,
                (source_id, path, name),
            )
            row = connection.execute(
                "SELECT id FROM folders WHERE source_id = ? AND path = ?",
                (source_id, path),
            ).fetchone()
            return int(row["id"])

    def last_locator(self, source_id: int) -> MessageLocator | None:
        """Return the most recently committed locator for a resumable source."""
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT locator_offset, locator_length, locator_folder_path,
                       locator_item_index, locator_item_id
                FROM occurrences
                WHERE source_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        return MessageLocator(
            offset=row["locator_offset"],
            length=row["locator_length"],
            folder_path=row["locator_folder_path"] or None,
            item_index=(
                None if row["locator_item_index"] < 0 else row["locator_item_index"]
            ),
            item_id=row["locator_item_id"] or None,
        )

    def has_occurrence(
        self, source_id: int, folder_id: int, locator: MessageLocator
    ) -> bool:
        """Check whether this source message locator has already been committed."""
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM occurrences
                WHERE source_id = ? AND folder_id = ? AND locator_offset = ?
                  AND locator_folder_path = ? AND locator_item_index = ?
                """,
                (
                    source_id,
                    folder_id,
                    locator.offset,
                    locator.folder_path or "",
                    -1 if locator.item_index is None else locator.item_index,
                ),
            ).fetchone()
        return row is not None

    def store_message(
        self,
        *,
        source_id: int,
        folder_id: int,
        metadata: MessageMetadata,
        dedup_key: str,
        thread_key: str,
        normalized_subject: str,
        content_hash: str,
        message_hash: str,
    ) -> bool:
        """Persist one message, occurrence, participants, attachments, and labels."""
        locator = metadata.locator
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT id FROM messages WHERE dedup_key = ?", (dedup_key,)
            ).fetchone()
            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO messages (
                        dedup_key, message_id, thread_key, subject,
                        normalized_subject, sent_date, in_reply_to,
                        references_header, body, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dedup_key,
                        metadata.message_id,
                        thread_key,
                        metadata.subject,
                        normalized_subject,
                        metadata.date,
                        metadata.in_reply_to,
                        metadata.references,
                        metadata.text_body,
                        content_hash,
                    ),
                )
                message_id = int(cursor.lastrowid)
            else:
                message_id = int(existing["id"])
                connection.execute(
                    """
                    UPDATE messages
                    SET body = CASE WHEN body = '' THEN ? ELSE body END,
                        subject = CASE WHEN subject = '' THEN ? ELSE subject END,
                        normalized_subject = CASE
                            WHEN normalized_subject = '' THEN ? ELSE normalized_subject
                        END,
                        sent_date = COALESCE(sent_date, ?),
                        in_reply_to = COALESCE(in_reply_to, ?),
                        references_header = COALESCE(references_header, ?)
                    WHERE id = ?
                    """,
                    (
                        metadata.text_body,
                        metadata.subject,
                        normalized_subject,
                        metadata.date,
                        metadata.in_reply_to,
                        metadata.references,
                        message_id,
                    ),
                )

            for role, addresses in (
                ("from", metadata.from_addresses),
                ("to", metadata.to_addresses),
                ("cc", metadata.cc_addresses),
                ("bcc", metadata.bcc_addresses),
            ):
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO participants(message_id, role, address)
                    VALUES (?, ?, ?)
                    """,
                    [
                        (message_id, role, address.strip().casefold())
                        for address in addresses
                        if address.strip()
                    ],
                )
            connection.executemany(
                """
                INSERT OR IGNORE INTO attachments(
                    message_id, filename, content_type
                ) VALUES (?, ?, ?)
                """,
                [
                    (
                        message_id,
                        name,
                        (
                            metadata.attachment_content_types[index]
                            if index < len(metadata.attachment_content_types)
                            else ""
                        ),
                    )
                    for index, name in enumerate(metadata.attachment_names)
                    if name
                ],
            )
            message = connection.execute(
                "SELECT subject, body FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            indexed_attachment_names = connection.execute(
                "SELECT filename FROM attachments WHERE message_id = ? ORDER BY filename",
                (message_id,),
            ).fetchall()
            connection.execute(
                "DELETE FROM messages_fts WHERE rowid = ?", (message_id,)
            )
            connection.execute(
                """
                INSERT INTO messages_fts(rowid, subject, body, attachment_names)
                VALUES (?, ?, ?, ?)
                """,
                (
                    message_id,
                    message["subject"],
                    message["body"],
                    ", ".join(row["filename"] for row in indexed_attachment_names),
                ),
            )

            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO occurrences (
                    source_id, folder_id, message_id, locator_offset,
                    locator_length, locator_folder_path, locator_item_index,
                    locator_item_id, message_hash, deleted, item_type, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    folder_id,
                    message_id,
                    locator.offset,
                    locator.length,
                    locator.folder_path or "",
                    -1 if locator.item_index is None else locator.item_index,
                    locator.item_id or "",
                    message_hash,
                    int(metadata.deleted),
                    metadata.item_type,
                    metadata.error,
                ),
            )
            occurrence_inserted = cursor.rowcount == 1
            if occurrence_inserted:
                occurrence = connection.execute(
                    """
                    SELECT id FROM occurrences
                    WHERE source_id = ? AND folder_id = ? AND locator_offset = ?
                      AND locator_folder_path = ? AND locator_item_index = ?
                    """,
                    (
                        source_id,
                        folder_id,
                        locator.offset,
                        locator.folder_path or "",
                        -1 if locator.item_index is None else locator.item_index,
                    ),
                ).fetchone()
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO occurrence_labels(occurrence_id, label)
                    VALUES (?, ?)
                    """,
                    [
                        (occurrence["id"], label)
                        for label in metadata.labels
                        if label
                    ],
                )
            return occurrence_inserted

    def search(self, query: str, limit: int = 100) -> list[sqlite3.Row]:
        """Search subject, body, and attachment names using the FTS5 index."""
        if limit <= 0:
            raise ValueError("Search result limit must be positive")
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT messages.id, messages.subject, messages.sent_date,
                           messages.message_id, messages.thread_key,
                           bm25(messages_fts) AS rank
                    FROM messages_fts
                    JOIN messages ON messages.id = messages_fts.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY rank, messages.id
                    LIMIT ?
                    """,
                    (query, limit),
                )
            )

    def sources(self) -> list[sqlite3.Row]:
        """Return source records for status and integrity reporting."""
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT id, path, kind, size, discovered_sha256,
                           sha256_before, sha256_after, status, last_error,
                           indexed_count
                    FROM sources ORDER BY path
                    """
                )
            )

    def message_count(self) -> int:
        """Return the number of unique indexed messages."""
        with self.connection() as connection:
            row = connection.execute("SELECT count(*) AS count FROM messages").fetchone()
        return int(row["count"])

    def occurrence_count(self) -> int:
        """Return the number of source-specific message occurrences."""
        with self.connection() as connection:
            row = connection.execute(
                "SELECT count(*) AS count FROM occurrences"
            ).fetchone()
        return int(row["count"])

    def participant_rows(self, message_id: int) -> list[sqlite3.Row]:
        """Return a message's role-tagged participant addresses."""
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT role, address FROM participants
                    WHERE message_id = ? ORDER BY role, address
                    """,
                    (message_id,),
                )
            )

    def labels_for_message(self, message_id: int) -> list[str]:
        """Return unique labels attached to any copy of a message."""
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT occurrence_labels.label
                FROM occurrence_labels
                JOIN occurrences ON occurrences.id = occurrence_labels.occurrence_id
                WHERE occurrences.message_id = ?
                ORDER BY occurrence_labels.label
                """,
                (message_id,),
            )
            return [row["label"] for row in rows]

    def attachment_names_for_message(self, message_id: int) -> list[str]:
        """Return indexed attachment filenames for a unique message."""
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT filename FROM attachments WHERE message_id = ? ORDER BY filename",
                (message_id,),
            )
            return [row["filename"] for row in rows]

    def attachment_rows_for_message(self, message_id: int) -> list[sqlite3.Row]:
        """Return attachment filenames and MIME types for a unique message."""
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT filename, content_type FROM attachments
                    WHERE message_id = ? ORDER BY filename, content_type
                    """,
                    (message_id,),
                )
            )

    def metadata_value(self, key: str) -> str | None:
        """Return a persisted application metadata value, if present."""
        with self.connection() as connection:
            row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else str(row["value"])
