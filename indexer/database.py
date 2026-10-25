"""SQLite schema and persistence helpers for the mail index."""

__author__ = "Bálint Szabolcs"

import calendar
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Generator

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
    normalized_message_id TEXT NOT NULL DEFAULT '',
    thread_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    normalized_subject TEXT NOT NULL,
    sent_date TEXT,
    sent_timestamp INTEGER,
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

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS person_addresses (
    address TEXT PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL DEFAULT '',
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS saved_searches (
    name TEXT PRIMARY KEY,
    query_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        connection.create_function(
            "casefold", 1, _sqlite_casefold, deterministic=True
        )
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
                participant_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(participants)")
                }
                if "display_name" not in participant_columns:
                    connection.execute(
                        """
                        ALTER TABLE participants
                        ADD COLUMN display_name TEXT NOT NULL DEFAULT ''
                        """
                    )
                message_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(messages)")
                }
                if "normalized_message_id" not in message_columns:
                    connection.execute(
                        """
                        ALTER TABLE messages
                        ADD COLUMN normalized_message_id TEXT NOT NULL DEFAULT ''
                        """
                    )
                    for row in connection.execute(
                        "SELECT id, message_id FROM messages WHERE message_id IS NOT NULL"
                    ).fetchall():
                        connection.execute(
                            """
                            UPDATE messages SET normalized_message_id = ?
                            WHERE id = ?
                            """,
                            (_normalize_message_id(row["message_id"]), row["id"]),
                        )
                if "sent_timestamp" not in message_columns:
                    connection.execute(
                        "ALTER TABLE messages ADD COLUMN sent_timestamp INTEGER"
                    )
                    for row in connection.execute(
                        """
                        SELECT id, sent_date FROM messages
                        WHERE sent_date IS NOT NULL
                        """
                    ).fetchall():
                        connection.execute(
                            "UPDATE messages SET sent_timestamp = ? WHERE id = ?",
                            (_parse_message_timestamp(row["sent_date"]), row["id"]),
                        )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_messages_normalized_message_id
                    ON messages(normalized_message_id)
                    """
                )
                connection.execute(
                    """
                    INSERT INTO app_metadata(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    ("schema_version", "5"),
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_messages_sent_timestamp
                    ON messages(sent_timestamp)
                    """
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

    def resolve_thread_key(
        self,
        proposed_key: str,
        normalized_subject: str,
        parent_ids: tuple[str, ...],
    ) -> str:
        """Resolve known parent IDs and subject fallbacks to an existing thread."""
        with self.connection() as connection:
            if parent_ids:
                existing_messages = connection.execute(
                    """
                    SELECT normalized_message_id, thread_key FROM messages
                    WHERE normalized_message_id IN (
                    """
                    + ", ".join("?" for _ in parent_ids)
                    + """
                    )
                    ORDER BY id
                    """,
                    tuple(parent_ids),
                ).fetchall()
                threads_by_parent: dict[str, str] = {}
                for row in existing_messages:
                    threads_by_parent.setdefault(
                        str(row["normalized_message_id"]), str(row["thread_key"])
                    )
                for parent_id in parent_ids:
                    if parent_id.casefold() in threads_by_parent:
                        return threads_by_parent[parent_id.casefold()]
                return proposed_key

            if normalized_subject:
                row = connection.execute(
                    """
                    SELECT thread_key FROM messages
                    WHERE normalized_subject = ?
                    ORDER BY id LIMIT 1
                    """,
                    (normalized_subject,),
                ).fetchone()
                if row is not None:
                    return str(row["thread_key"])
        return proposed_key

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
                        dedup_key, message_id, normalized_message_id, thread_key, subject,
                        normalized_subject, sent_date, sent_timestamp, in_reply_to,
                        references_header, body, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dedup_key,
                        metadata.message_id,
                        _normalize_message_id(metadata.message_id or ""),
                        thread_key,
                        metadata.subject,
                        normalized_subject,
                        metadata.date,
                        _parse_message_timestamp(metadata.date),
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
                        normalized_message_id = CASE
                            WHEN normalized_message_id = '' THEN ?
                            ELSE normalized_message_id
                        END,
                        normalized_subject = CASE
                            WHEN normalized_subject = '' THEN ? ELSE normalized_subject
                        END,
                        sent_date = COALESCE(sent_date, ?),
                        sent_timestamp = COALESCE(sent_timestamp, ?),
                        in_reply_to = COALESCE(in_reply_to, ?),
                        references_header = COALESCE(references_header, ?)
                    WHERE id = ?
                    """,
                    (
                        metadata.text_body,
                        metadata.subject,
                        _normalize_message_id(metadata.message_id or ""),
                        normalized_subject,
                        metadata.date,
                        _parse_message_timestamp(metadata.date),
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
                for address in addresses:
                    normalized_address = address.strip().casefold()
                    if not normalized_address:
                        continue
                    display_name = _participant_display_name(
                        metadata, role, normalized_address
                    )
                    connection.execute(
                        """
                        INSERT INTO participants(
                            message_id, role, address, display_name
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(message_id, role, address)
                        DO UPDATE SET display_name = CASE
                            WHEN participants.display_name = ''
                            THEN excluded.display_name
                            ELSE participants.display_name
                        END
                        """,
                        (message_id, role, normalized_address, display_name),
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

    def search(
        self,
        query: str,
        limit: int = 100,
        *,
        person_ids: tuple[int, ...] | None = None,
        include_cc_bcc: bool = True,
    ) -> list[sqlite3.Row]:
        """Search FTS content, optionally restricted to selected people."""
        if limit <= 0:
            raise ValueError("Search result limit must be positive")
        if person_ids is not None and not person_ids:
            return []
        person_filter = ""
        parameters: list[object] = [query]
        if person_ids is not None:
            role_filter = "" if include_cc_bcc else "AND participant.role IN ('from', 'to')"
            placeholders = ", ".join("?" for _ in person_ids)
            person_filter = f"""
                AND EXISTS (
                    SELECT 1
                    FROM participants AS participant
                    JOIN person_addresses AS assigned_address
                      ON assigned_address.address = participant.address
                    WHERE participant.message_id = messages.id
                      {role_filter}
                      AND assigned_address.person_id IN ({placeholders})
                )
            """
            parameters.extend(person_ids)
        parameters.append(limit)
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
                    """
                    + person_filter
                    + """
                    ORDER BY rank, messages.id
                    LIMIT ?
                    """,
                    parameters,
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

    def folders(self, source_id: int | None = None) -> list[sqlite3.Row]:
        """List indexed folders with their source and occurrence counts."""
        conditions = ""
        parameters: tuple[object, ...] = ()
        if source_id is not None:
            conditions = "WHERE sources.id = ?"
            parameters = (source_id,)
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT folders.id, folders.source_id, sources.path AS source_path,
                           folders.path, folders.name, count(occurrences.id) AS message_count
                    FROM folders
                    JOIN sources ON sources.id = folders.source_id
                    LEFT JOIN occurrences ON occurrences.folder_id = folders.id
                    """
                    + conditions
                    + """
                    GROUP BY folders.id, folders.source_id, sources.path,
                             folders.path, folders.name
                    ORDER BY sources.path, folders.path
                    """,
                    parameters,
                )
            )

    def archive_messages(
        self,
        query: str = "",
        *,
        source_id: int | None = None,
        folder_id: int | None = None,
        has_attachments: bool = False,
        deleted_only: bool = False,
        item_type: str | None = None,
        include_duplicates: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        """List indexed occurrences with optional archive-browser filters."""
        if limit <= 0 or offset < 0:
            raise ValueError("Archive pagination values are invalid")
        filters: list[str] = []
        parameters: list[object] = []
        if query.strip():
            filters.append("messages_fts MATCH ?")
            parameters.append(query)
        if source_id is not None:
            filters.append("sources.id = ?")
            parameters.append(source_id)
        if folder_id is not None:
            filters.append("folders.id = ?")
            parameters.append(folder_id)
        if has_attachments:
            filters.append(
                "EXISTS (SELECT 1 FROM attachments WHERE attachments.message_id = messages.id)"
            )
        if deleted_only:
            filters.append("occurrences.deleted = 1")
        if item_type is not None:
            filters.append("occurrences.item_type = ?")
            parameters.append(item_type)
        where_clause = "WHERE " + " AND ".join(filters) if filters else ""
        fts_join = (
            "JOIN messages_fts ON messages_fts.rowid = messages.id"
            if query.strip()
            else ""
        )
        duplicate_filter = "" if include_duplicates else "AND ranked.copy_number = 1"
        parameters.extend((limit, offset))
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    WITH matched AS (
                        SELECT messages.id AS message_id, messages.subject,
                               messages.sent_date, messages.sent_timestamp,
                               messages.message_id AS internet_message_id,
                               messages.thread_key, messages.content_hash,
                               occurrences.id AS occurrence_id, occurrences.source_id,
                               occurrences.folder_id, occurrences.locator_offset,
                               occurrences.locator_length, occurrences.locator_folder_path,
                               occurrences.locator_item_index, occurrences.locator_item_id,
                               occurrences.message_hash, occurrences.deleted,
                               occurrences.item_type, occurrences.error,
                               sources.path AS source_path, sources.kind AS source_kind,
                               sources.sha256_after, folders.path AS folder_path,
                               folders.name AS folder_name,
                               (SELECT group_concat(address, ', ')
                                FROM participants
                                WHERE participants.message_id = messages.id
                                  AND participants.role = 'from') AS sender,
                               count(*) OVER (PARTITION BY messages.id) AS copy_count,
                               row_number() OVER (
                                   PARTITION BY messages.id ORDER BY occurrences.id
                               ) AS copy_number
                        FROM messages
                        """
                    + fts_join
                    + """
                        JOIN occurrences ON occurrences.message_id = messages.id
                        JOIN sources ON sources.id = occurrences.source_id
                        LEFT JOIN folders ON folders.id = occurrences.folder_id
                        """
                    + where_clause
                    + """
                    )
                    SELECT * FROM matched AS ranked
                    WHERE 1 = 1
                    """
                    + duplicate_filter
                    + """
                    ORDER BY sent_date IS NULL, sent_date DESC, message_id DESC,
                             occurrence_id
                    LIMIT ? OFFSET ?
                    """,
                    parameters,
                )
            )

    def detailed_search(
        self,
        conditions: tuple[dict[str, Any], ...],
        *,
        operator: str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[sqlite3.Row], int]:
        """Search indexed mail with flat conditions or nested AND/OR groups."""
        if operator not in {"AND", "OR"}:
            raise ValueError("Search operator must be AND or OR")
        if limit <= 0 or offset < 0:
            raise ValueError("Search pagination values are invalid")
        if any(not isinstance(item, dict) for item in conditions):
            raise ValueError("Search conditions must be objects")
        grouped = bool(conditions) and all("conditions" in item for item in conditions)
        group_sizes: list[tuple[int, str]] = []
        if grouped:
            if len(conditions) > 10:
                raise ValueError("At most 10 search groups are allowed")
            flattened_conditions: list[dict[str, Any]] = []
            for group in conditions:
                group_operator = group.get("operator")
                group_conditions = group.get("conditions")
                if group_operator not in {"AND", "OR"}:
                    raise ValueError("Each search group must use AND or OR")
                if (
                    not isinstance(group_conditions, list)
                    or not 1 <= len(group_conditions) <= 20
                ):
                    raise ValueError("Each search group needs between 1 and 20 conditions")
                group_sizes.append((len(group_conditions), group_operator))
                flattened_conditions.extend(group_conditions)
            if len(flattened_conditions) > 20:
                raise ValueError("At most 20 search conditions are allowed")
        else:
            if not conditions:
                raise ValueError("At least one search condition is required")
            if len(conditions) > 20:
                raise ValueError("At most 20 search conditions are allowed")
            flattened_conditions = list(conditions)
        expression_parts: list[str] = []
        parameters: list[object] = []
        for condition in flattened_conditions:
            if not isinstance(condition, dict):
                raise ValueError("Each search condition must be an object")
            field = condition.get("field", "")
            raw_value = condition.get("value", "")
            if not isinstance(field, str) or not isinstance(raw_value, str):
                raise ValueError("Search fields and values must be strings")
            value = raw_value.strip()
            if len(value) > 512:
                raise ValueError("Search condition values must be 512 characters or fewer")
            if field in {"text", "subject"}:
                if not value:
                    raise ValueError("Text search conditions must not be empty")
                if field == "text":
                    expression_parts.append(
                        """
                        EXISTS (
                            SELECT 1 FROM messages_fts
                            WHERE messages_fts.rowid = messages.id
                              AND messages_fts MATCH ?
                        )
                        """
                    )
                    parameters.append(f'"{value.replace(chr(34), chr(34) * 2)}"')
                else:
                    expression_parts.append(
                        "casefold(messages.subject) LIKE ? ESCAPE '\\'"
                    )
                    parameters.append(f"%{_escape_like(value.casefold())}%")
            elif field in {"from", "to"}:
                if not value:
                    raise ValueError("Participant search conditions must not be empty")
                expression_parts.append(
                    f"""
                    EXISTS (
                        SELECT 1 FROM participants
                        WHERE participants.message_id = messages.id
                          AND participants.role = ?
                          AND (
                              casefold(participants.address) LIKE ? ESCAPE '\\'
                              OR casefold(participants.display_name) LIKE ? ESCAPE '\\'
                          )
                    )
                    """
                )
                pattern = f"%{_escape_like(value.casefold())}%"
                parameters.extend((field, pattern, pattern))
            elif field in {"sent_after", "sent_before"}:
                bound = _search_date_timestamp(value, next_day=field == "sent_before")
                expression_parts.append(
                    "messages.sent_timestamp >= ?"
                    if field == "sent_after"
                    else "messages.sent_timestamp < ?"
                )
                parameters.append(bound)
            elif field == "has_attachment":
                if value not in {"yes", "no"}:
                    raise ValueError("Attachment condition must be yes or no")
                expression_parts.append(
                    (
                        "EXISTS"
                        if value == "yes"
                        else "NOT EXISTS"
                    )
                    + " (SELECT 1 FROM attachments WHERE attachments.message_id = messages.id)"
                )
            elif field in {"source", "folder"}:
                if not value:
                    raise ValueError("Source and folder search conditions must not be empty")
                column = (
                    "sources.path"
                    if field == "source"
                    else "folders.path || '/' || folders.name"
                )
                relation = "occurrences JOIN sources ON sources.id = occurrences.source_id"
                if field == "folder":
                    relation += " LEFT JOIN folders ON folders.id = occurrences.folder_id"
                expression_parts.append(
                    f"""
                    EXISTS (
                        SELECT 1 FROM {relation}
                        WHERE occurrences.message_id = messages.id
                          AND casefold({column}) LIKE ? ESCAPE '\\'
                    )
                    """
                )
                parameters.append(f"%{_escape_like(value.casefold())}%")
            elif field == "attachment_type":
                if not value:
                    raise ValueError("Attachment type search conditions must not be empty")
                expression_parts.append(
                    """
                    EXISTS (
                        SELECT 1 FROM attachments
                        WHERE attachments.message_id = messages.id
                          AND casefold(attachments.content_type) LIKE ? ESCAPE '\\'
                    )
                    """
                )
                parameters.append(f"%{_escape_like(value.casefold())}%")
            elif field == "label":
                if not value:
                    raise ValueError("Label search conditions must not be empty")
                expression_parts.append(
                    """
                    EXISTS (
                        SELECT 1
                        FROM occurrences
                        JOIN occurrence_labels
                          ON occurrence_labels.occurrence_id = occurrences.id
                        WHERE occurrences.message_id = messages.id
                          AND casefold(occurrence_labels.label) LIKE ? ESCAPE '\\'
                    )
                    """
                )
                parameters.append(f"%{_escape_like(value.casefold())}%")
            else:
                raise ValueError(f"Unsupported search condition: {field}")

        if grouped:
            grouped_expressions: list[str] = []
            expression_index = 0
            for group_size, group_operator in group_sizes:
                group_parts = expression_parts[
                    expression_index : expression_index + group_size
                ]
                group_expression = f" {group_operator} ".join(group_parts)
                grouped_expressions.append(f"({group_expression})")
                expression_index += group_size
            where_clause = f" {operator} ".join(grouped_expressions)
        else:
            where_clause = f" {operator} ".join(expression_parts)
        with self.connection() as connection:
            total_row = connection.execute(
                f"SELECT count(*) AS count FROM messages WHERE {where_clause}",
                parameters,
            ).fetchone()
            rows = list(
                connection.execute(
                    f"""
                    WITH matched AS (
                        SELECT messages.id AS message_id, messages.subject,
                               messages.sent_date, messages.sent_timestamp,
                               messages.message_id AS internet_message_id,
                               messages.thread_key, messages.content_hash,
                               occurrences.id AS occurrence_id, occurrences.source_id,
                               occurrences.folder_id, occurrences.locator_offset,
                               occurrences.locator_length, occurrences.locator_folder_path,
                               occurrences.locator_item_index, occurrences.locator_item_id,
                               occurrences.message_hash, occurrences.deleted,
                               occurrences.item_type, occurrences.error,
                               sources.path AS source_path, sources.kind AS source_kind,
                               folders.path AS folder_path, folders.name AS folder_name,
                               (
                                   SELECT group_concat(address, ', ')
                                   FROM participants
                                   WHERE participants.message_id = messages.id
                                     AND participants.role = 'from'
                               ) AS sender,
                               (
                                   SELECT group_concat(DISTINCT address)
                                   FROM participants
                                   WHERE participants.message_id = messages.id
                                     AND participants.role IN ('from', 'to')
                               ) AS participant_addresses,
                               (
                                   SELECT group_concat(DISTINCT occurrence_labels.label)
                                   FROM occurrences AS labeled_occurrence
                                   JOIN occurrence_labels
                                     ON occurrence_labels.occurrence_id = labeled_occurrence.id
                                   WHERE labeled_occurrence.message_id = messages.id
                               ) AS labels,
                               count(*) OVER (PARTITION BY messages.id) AS copy_count,
                               row_number() OVER (
                                   PARTITION BY messages.id ORDER BY occurrences.id
                               ) AS copy_number
                        FROM messages
                        JOIN occurrences ON occurrences.message_id = messages.id
                        JOIN sources ON sources.id = occurrences.source_id
                        LEFT JOIN folders ON folders.id = occurrences.folder_id
                        WHERE messages.id IN (
                            SELECT id FROM messages WHERE {where_clause}
                        )
                    )
                    SELECT * FROM matched
                    WHERE copy_number = 1
                    ORDER BY sent_timestamp IS NULL, sent_timestamp DESC, message_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*parameters, limit, offset],
                )
            )
        return rows, int(total_row["count"])

    def set_message_label(
        self, message_ids: tuple[int, ...], label: str, *, enabled: bool
    ) -> int:
        """Apply or remove a user label on every indexed copy of selected messages."""
        normalized_label = label.strip()
        if not normalized_label or len(normalized_label) > 64:
            raise ValueError("A label must contain between 1 and 64 characters")
        if any(ord(character) < 32 for character in normalized_label):
            raise ValueError("Labels must not contain control characters")
        if (
            not message_ids
            or len(message_ids) > 200
            or any(
                not isinstance(message_id, int)
                or isinstance(message_id, bool)
                or message_id <= 0
                for message_id in message_ids
            )
        ):
            raise ValueError("At least one valid message ID must be selected")
        placeholders = ", ".join("?" for _ in message_ids)
        with self.connection() as connection:
            if enabled:
                connection.execute(
                    f"""
                    INSERT OR IGNORE INTO occurrence_labels(occurrence_id, label)
                    SELECT id, ? FROM occurrences
                    WHERE message_id IN ({placeholders})
                    """,
                    (normalized_label, *message_ids),
                )
            else:
                connection.execute(
                    f"""
                    DELETE FROM occurrence_labels
                    WHERE label = ?
                      AND occurrence_id IN (
                          SELECT id FROM occurrences
                          WHERE message_id IN ({placeholders})
                      )
                    """,
                    (normalized_label, *message_ids),
                )
            affected = connection.execute(
                f"""
                SELECT count(DISTINCT id) AS count FROM occurrences
                WHERE message_id IN ({placeholders})
                """,
                message_ids,
            ).fetchone()
        return int(affected["count"])

    def saved_searches(self) -> list[sqlite3.Row]:
        """Return saved detailed searches in stable display order."""
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT name, query_json, created_at, updated_at
                    FROM saved_searches
                    ORDER BY name COLLATE NOCASE
                    """
                )
            )

    def save_search(self, name: str, query: dict[str, Any]) -> None:
        """Create or replace a named search without storing executable SQL."""
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 100:
            raise ValueError("A saved search name must contain 1 to 100 characters")
        if any(ord(character) < 32 for character in normalized_name):
            raise ValueError("Saved search names must not contain control characters")
        query_json = json.dumps(query, ensure_ascii=False, sort_keys=True)
        if len(query_json) > 16384:
            raise ValueError("A saved search must be 16 KB or smaller")
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO saved_searches(name, query_json)
                VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    query_json = excluded.query_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (normalized_name, query_json),
            )

    def delete_saved_search(self, name: str) -> bool:
        """Delete a named search and report whether it existed."""
        with self.connection() as connection:
            cursor = connection.execute(
                "DELETE FROM saved_searches WHERE name = ?", (name,)
            )
        return cursor.rowcount > 0

    def message_details(self, message_id: int) -> sqlite3.Row | None:
        """Return indexed message headers and body for an archive selection."""
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT id, subject, sent_date, message_id AS internet_message_id,
                       thread_key, in_reply_to, references_header, body, content_hash
                FROM messages WHERE id = ?
                """,
                (message_id,),
            ).fetchone()

    def conversation_messages(
        self,
        person_ids: tuple[int, ...],
        *,
        mode: str,
        include_cc_bcc: bool,
        query: str = "",
        thread_key: str | None = None,
        date_from_timestamp: int | None = None,
        date_to_timestamp: int | None = None,
        limit: int = 10001,
    ) -> list[sqlite3.Row]:
        """Select deduplicated messages for a person-based conversation view."""
        if not person_ids or any(person_id <= 0 for person_id in person_ids):
            raise ValueError("At least one valid person must be selected")
        if mode not in {"between", "shared", "any"}:
            raise ValueError(f"Unsupported conversation mode: {mode}")
        if limit <= 0:
            raise ValueError("Conversation result limit must be positive")

        role_filter = (
            "participant.role IN ('from', 'to', 'cc', 'bcc')"
            if include_cc_bcc
            else "participant.role IN ('from', 'to')"
        )
        participant_role_list = (
            "'from', 'to', 'cc', 'bcc'" if include_cc_bcc else "'from', 'to'"
        )
        placeholders = ", ".join("?" for _ in person_ids)
        selected = f"assigned.person_id IN ({placeholders})"
        filters = [
            f"""
            EXISTS (
                SELECT 1
                FROM participants AS participant
                JOIN person_addresses AS assigned
                  ON assigned.address = participant.address
                WHERE participant.message_id = messages.id
                  AND {role_filter}
                  AND {selected}
            )
            """
        ]
        parameters: list[Any] = [*person_ids]
        if mode == "shared":
            filters.append(
                f"""
                (
                    SELECT count(DISTINCT assigned.person_id)
                    FROM participants AS participant
                    JOIN person_addresses AS assigned
                      ON assigned.address = participant.address
                    WHERE participant.message_id = messages.id
                      AND {role_filter}
                      AND {selected}
                ) = ?
                """
            )
            parameters.extend(person_ids)
            parameters.append(len(person_ids))
        elif mode == "between":
            filters.extend(
                (
                    f"""
                    EXISTS (
                        SELECT 1 FROM participants AS sender
                        JOIN person_addresses AS sender_person
                          ON sender_person.address = sender.address
                        WHERE sender.message_id = messages.id
                          AND sender.role = 'from'
                          AND sender_person.person_id IN ({placeholders})
                    )
                    """,
                    f"""
                    EXISTS (
                        SELECT 1 FROM participants AS recipient
                        JOIN person_addresses AS recipient_person
                          ON recipient_person.address = recipient.address
                        WHERE recipient.message_id = messages.id
                          AND recipient.role = 'to'
                          AND recipient_person.person_id IN ({placeholders})
                    )
                    """,
                    f"""
                    NOT EXISTS (
                        SELECT 1 FROM participants AS outside_participant
                        LEFT JOIN person_addresses AS outside_person
                          ON outside_person.address = outside_participant.address
                        WHERE outside_participant.message_id = messages.id
                          AND outside_participant.role IN ({participant_role_list})
                          AND (
                              outside_person.person_id IS NULL
                              OR outside_person.person_id NOT IN ({placeholders})
                          )
                    )
                    """,
                )
            )
            parameters.extend(person_ids)
            parameters.extend(person_ids)
            parameters.extend(person_ids)
        if query.strip():
            filters.append("messages_fts MATCH ?")
            parameters.append(query)
        if thread_key:
            filters.append("messages.thread_key = ?")
            parameters.append(thread_key)
        if date_from_timestamp is not None:
            filters.append("messages.sent_timestamp >= ?")
            parameters.append(date_from_timestamp)
        if date_to_timestamp is not None:
            filters.append("messages.sent_timestamp < ?")
            parameters.append(date_to_timestamp)
        parameters.append(limit)
        fts_join = (
            "JOIN messages_fts ON messages_fts.rowid = messages.id"
            if query.strip()
            else ""
        )
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT messages.id AS message_id, messages.subject,
                           messages.sent_date, messages.message_id AS internet_message_id,
                           messages.thread_key, messages.body, messages.content_hash
                    FROM messages
                    """
                    + fts_join
                    + """
                    WHERE """
                    + " AND ".join(filters)
                    + """
                    ORDER BY messages.sent_timestamp IS NULL,
                             messages.sent_timestamp, messages.id
                    LIMIT ?
                    """,
                    parameters,
                )
            )

    def conversation_participants(
        self, message_ids: tuple[int, ...]
    ) -> list[sqlite3.Row]:
        """Return role-tagged participants for selected conversation messages."""
        if not message_ids:
            return []
        placeholders = ", ".join("?" for _ in message_ids)
        with self.connection() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT participants.message_id, participants.role,
                           participants.address, participants.display_name,
                           people.id AS person_id, people.display_name AS person_name
                    FROM participants
                    LEFT JOIN person_addresses
                      ON person_addresses.address = participants.address
                    LEFT JOIN people ON people.id = person_addresses.person_id
                    WHERE participants.message_id IN ({placeholders})
                    ORDER BY participants.message_id, participants.role,
                             participants.address
                    """,
                    message_ids,
                )
            )

    def observed_addresses(self) -> list[sqlite3.Row]:
        """List distinct indexed email addresses and the first observed display name."""
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT participants.address,
                           min(participants.display_name) AS display_name,
                           person_addresses.person_id,
                           people.display_name AS person_name
                    FROM participants
                    LEFT JOIN person_addresses
                      ON person_addresses.address = participants.address
                    LEFT JOIN people ON people.id = person_addresses.person_id
                    GROUP BY participants.address, person_addresses.person_id,
                             people.display_name
                    ORDER BY lower(coalesce(people.display_name,
                                            min(participants.display_name),
                                            participants.address)),
                             participants.address
                    """
                )
            )

    def occurrences_for_message(self, message_id: int) -> list[sqlite3.Row]:
        """Return source locators and integrity metadata for every message copy."""
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT occurrences.id, occurrences.source_id,
                           sources.path AS source_path, sources.kind AS source_kind,
                           sources.size AS source_size, sources.discovered_sha256,
                           sources.sha256_after, sources.status,
                           folders.path AS folder_path, folders.name AS folder_name,
                           occurrences.locator_offset, occurrences.locator_length,
                           occurrences.locator_folder_path,
                           occurrences.locator_item_index, occurrences.locator_item_id,
                           occurrences.message_hash, occurrences.deleted,
                           occurrences.item_type, occurrences.error
                    FROM occurrences
                    JOIN sources ON sources.id = occurrences.source_id
                    LEFT JOIN folders ON folders.id = occurrences.folder_id
                    WHERE occurrences.message_id = ?
                    ORDER BY occurrences.id
                    """,
                    (message_id,),
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

    def create_person(
        self, display_name: str, addresses: tuple[str, ...] = ()
    ) -> int:
        """Create a named person and optionally assign email aliases."""
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("Person display name must not be empty")
        normalized_addresses = _normalize_addresses(addresses)
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO people(display_name) VALUES (?)",
                (normalized_name,),
            )
            person_id = int(cursor.lastrowid)
            for address in normalized_addresses:
                self._assign_address(
                    connection, person_id, address, normalized_name
                )
            return person_id

    def assign_address(
        self, person_id: int, address: str, display_name: str = ""
    ) -> None:
        """Assign one address to a person, refusing cross-person alias collisions."""
        normalized_address = _normalize_address(address)
        if not normalized_address:
            raise ValueError("Email address must not be empty")
        with self.connection() as connection:
            self._assign_address(
                connection,
                person_id,
                normalized_address,
                display_name.strip(),
            )

    @staticmethod
    def _assign_address(
        connection: sqlite3.Connection,
        person_id: int,
        address: str,
        display_name: str,
    ) -> None:
        """Insert or update an address after validating its person."""
        person = connection.execute(
            "SELECT display_name FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if person is None:
            raise ValueError(f"Person does not exist: {person_id}")
        current = connection.execute(
            "SELECT person_id FROM person_addresses WHERE address = ?", (address,)
        ).fetchone()
        if current is not None and current["person_id"] != person_id:
            raise ValueError(
                f"Email address is already assigned to person "
                f"{current['person_id']}: {address}"
            )
        connection.execute(
            """
            INSERT INTO person_addresses(address, person_id, display_name)
            VALUES (?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET display_name = CASE
                WHEN excluded.display_name != '' THEN excluded.display_name
                ELSE person_addresses.display_name
            END
            """,
            (address, person_id, display_name or person["display_name"]),
        )

    def remove_address(self, person_id: int, address: str) -> bool:
        """Unassign an address from a person and report whether it existed."""
        normalized_address = _normalize_address(address)
        with self.connection() as connection:
            cursor = connection.execute(
                "DELETE FROM person_addresses WHERE person_id = ? AND address = ?",
                (person_id, normalized_address),
            )
        return cursor.rowcount > 0

    def rename_person(self, person_id: int, display_name: str) -> None:
        """Change a person's canonical display name."""
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("Person display name must not be empty")
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE people SET display_name = ? WHERE id = ?",
                (normalized_name, person_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Person does not exist: {person_id}")

    def merge_people(self, target_id: int, source_id: int) -> None:
        """Move source aliases into a target person and remove the source record."""
        if target_id == source_id:
            raise ValueError("A person cannot be merged into itself")
        with self.connection() as connection:
            people = connection.execute(
                "SELECT id FROM people WHERE id IN (?, ?)",
                (target_id, source_id),
            ).fetchall()
            if len(people) != 2:
                raise ValueError("Both people must exist before merging")
            connection.execute(
                "UPDATE person_addresses SET person_id = ? WHERE person_id = ?",
                (target_id, source_id),
            )
            connection.execute("DELETE FROM people WHERE id = ?", (source_id,))

    def people(self) -> list[sqlite3.Row]:
        """Return people with their assigned addresses and observed names."""
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT people.id, people.display_name,
                           person_addresses.address,
                           person_addresses.display_name AS address_display_name
                    FROM people
                    LEFT JOIN person_addresses ON person_addresses.person_id = people.id
                    ORDER BY people.display_name COLLATE NOCASE,
                             person_addresses.address
                    """
                )
            )

    def person_suggestion_rows(self) -> list[sqlite3.Row]:
        """Return observed names and addresses suitable for merge suggestions."""
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT participants.address, participants.display_name,
                           person_addresses.person_id
                    FROM participants
                    LEFT JOIN person_addresses
                      ON person_addresses.address = participants.address
                    WHERE trim(participants.display_name) != ''
                    ORDER BY lower(participants.display_name), participants.address
                    """
                )
            )

    def message_ids_for_person(self, person_id: int) -> list[int]:
        """Find messages involving a person through any assigned address."""
        with self.connection() as connection:
            return [
                int(row["message_id"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT participants.message_id
                    FROM participants
                    JOIN person_addresses
                      ON person_addresses.address = participants.address
                    WHERE person_addresses.person_id = ?
                    ORDER BY participants.message_id
                    """,
                    (person_id,),
                )
            ]

    def thread_messages(self, thread_key: str) -> list[sqlite3.Row]:
        """Return messages in a thread ordered by date, then stable message ID."""
        with self.connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT id, message_id, thread_key, subject, sent_date,
                           in_reply_to, references_header
                    FROM messages
                    WHERE thread_key = ?
                    ORDER BY sent_date IS NULL, sent_date, id
                    """,
                    (thread_key,),
                )
            )


def _normalize_address(address: str) -> str:
    """Normalize an email address for identity matching."""
    return address.strip().casefold()


def _normalize_message_id(message_id: str) -> str:
    """Normalize case and optional angle brackets for indexed thread lookups."""
    normalized = message_id.strip().casefold()
    if normalized.startswith("<") and normalized.endswith(">"):
        return normalized[1:-1]
    return normalized


def _parse_message_timestamp(sent_date: str | None) -> int | None:
    """Convert an RFC 5322 Date header to a sortable UTC Unix timestamp."""
    if not sent_date:
        return None
    try:
        parsed = parsedate_to_datetime(sent_date)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return calendar.timegm(parsed.astimezone(timezone.utc).utctimetuple())
    except (OverflowError, ValueError):
        return None


def _search_date_timestamp(value: str, *, next_day: bool) -> int:
    """Convert one strict calendar date into an inclusive/exclusive UTC boundary."""
    if len(value) != 10:
        raise ValueError("Date search conditions must use YYYY-MM-DD")
    try:
        selected_date = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Date search conditions must use a valid YYYY-MM-DD date") from error
    timestamp = calendar.timegm(
        datetime.combine(
            selected_date, datetime.min.time(), tzinfo=timezone.utc
        ).utctimetuple()
    )
    return timestamp + 86400 if next_day else timestamp


def _escape_like(value: str) -> str:
    """Escape SQL LIKE metacharacters so user values stay literal substrings."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sqlite_casefold(value: str | None) -> str:
    """Expose Unicode-aware casefolding to SQLite's detailed-search predicates."""
    return "" if value is None else value.casefold()


def _normalize_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize and deduplicate a set of email addresses."""
    return tuple(sorted({_normalize_address(address) for address in addresses if address.strip()}))


def _participant_display_name(
    metadata: MessageMetadata, role: str, normalized_address: str
) -> str:
    """Find the first observed display name for a role/address pair."""
    for participant_role, address, display_name in metadata.participant_names:
        if (
            participant_role == role
            and _normalize_address(address) == normalized_address
        ):
            return display_name.strip()
    return ""
