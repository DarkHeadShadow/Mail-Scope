"""Synchronous and background source indexing orchestration."""

__author__ = "Bálint Szabolcs"

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from sources.discover import DiscoveredSource, hash_source, source_signature
from sources.models import MessageMetadata, SourceReader
from sources.reader_factory import open_source

from .database import IndexDatabase, is_busy_error


logger = logging.getLogger(__name__)
COMMIT_BATCH_SIZE = 500
COMMIT_INTERVAL_SECONDS = 1.0
BATCH_LOCK_TIMEOUT_SECONDS = 5
BATCH_WRITE_ATTEMPTS = 5
_REPLY_PREFIX = re.compile(r"^\s*(?:(?:re|fw|fwd|vs)\s*:\s*)+", re.IGNORECASE)
_MESSAGE_ID = re.compile(r"<([^<>]+)>")


@dataclass(frozen=True)
class IndexProgress:
    """A point-in-time progress update from a source indexing job."""

    status: str
    source_path: str | None
    source_number: int
    source_total: int
    messages_scanned: int
    messages_indexed: int
    messages_skipped: int
    subject: str = ""
    error: str | None = None


@dataclass(frozen=True)
class IndexSummary:
    """Totals and final state returned by an indexing run."""

    status: str
    sources_completed: int
    sources_skipped: int
    sources_changed: int
    messages_scanned: int
    messages_indexed: int
    messages_skipped: int
    sources_failed: int = 0


ProgressCallback = Callable[[IndexProgress], None]


class IndexingService:
    """Index discovered mail sources into a case-local SQLite database."""

    def __init__(self, database: IndexDatabase) -> None:
        """Retain the initialized database used by indexing operations."""
        self.database = database

    def start(
        self,
        sources: Iterable[DiscoveredSource],
        progress_callback: ProgressCallback | None = None,
    ) -> "IndexingJob":
        """Start a cancellable indexing job on a dedicated background thread."""
        return IndexingJob(self, list(sources), progress_callback)

    def index_sources(
        self,
        sources: Iterable[DiscoveredSource],
        *,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
        reader_factory: Callable[[DiscoveredSource], SourceReader] = open_source,
    ) -> IndexSummary:
        """Index sources serially, committing in batches to support resume."""
        source_list = list(sources)
        cancellation = cancel_event or threading.Event()
        source_total = len(source_list)
        sources_completed = 0
        sources_skipped = 0
        sources_changed = 0
        sources_failed = 0
        messages_scanned = 0
        messages_indexed = 0
        messages_skipped = 0
        final_status = "completed"

        def report(
            status: str,
            source_number: int,
            source_path: str | None,
            subject: str = "",
            error: str | None = None,
        ) -> None:
            """Publish immutable, cumulative progress to the caller."""
            if progress_callback is not None:
                progress_callback(
                    IndexProgress(
                        status=status,
                        source_path=source_path,
                        source_number=source_number,
                        source_total=source_total,
                        messages_scanned=messages_scanned,
                        messages_indexed=messages_indexed,
                        messages_skipped=messages_skipped,
                        subject=subject,
                        error=error,
                    )
                )

        for source_number, source in enumerate(source_list, start=1):
            if cancellation.is_set():
                final_status = "cancelled"
                report(final_status, source_number, str(source.path))
                break

            source_path = str(source.path)
            source_id: int | None = None
            sha256_before: str | None = None
            try:
                sha256_before = self._hash_before(source)
                source_id, already_complete = self.database.register_source(
                    path=source_path,
                    kind=source.kind,
                    size=source.size,
                    discovered_sha256=source.sha256,
                    sha256_before=sha256_before,
                )
                if sha256_before != source.sha256:
                    message = "Source changed after discovery and was not indexed."
                    logger.error(
                        "%s expected=%s actual=%s",
                        message,
                        source.sha256,
                        sha256_before,
                    )
                    self.database.mark_source(
                        source_id,
                        "source_changed",
                        sha256_after=sha256_before,
                        error=message,
                    )
                    sources_changed += 1
                    final_status = "source_changed"
                    report(final_status, source_number, source_path, error=message)
                    continue
                if already_complete:
                    sources_skipped += 1
                    report("skipped", source_number, source_path)
                    continue

                reader = reader_factory(source)
                try:
                    with self.database.connection(write=True) as connection:
                        folders = {
                            folder.path: self.database.add_folder(
                                source_id, folder.path, folder.name, connection=connection
                            )
                            for folder in reader.iter_folders()
                        }
                    last_locator = self.database.last_locator(source_id)
                    report("indexing", source_number, source_path)

                    # Messages are parsed without holding the write lock and
                    # written in short IMMEDIATE transactions: after 500
                    # messages or about one second, whichever comes first.
                    batch: list[MessageMetadata] = []
                    batch_started = time.monotonic()
                    for metadata in reader.iter_messages(after_locator=last_locator):
                        if cancellation.is_set():
                            break
                        messages_scanned += 1
                        batch.append(metadata)
                        if (
                            len(batch) >= COMMIT_BATCH_SIZE
                            or time.monotonic() - batch_started >= COMMIT_INTERVAL_SECONDS
                        ):
                            indexed, skipped = self._write_batch(source_id, folders, batch)
                            messages_indexed += indexed
                            messages_skipped += skipped
                            batch = []
                            batch_started = time.monotonic()
                        report("indexing", source_number, source_path, metadata.subject)
                    if batch:
                        indexed, skipped = self._write_batch(source_id, folders, batch)
                        messages_indexed += indexed
                        messages_skipped += skipped
                finally:
                    reader.close()
                self.database.resolve_address_aliases()
                self.database.split_unanchored_threads()

                logger.info("Hashing source after indexing: %s", source_path)
                sha256_after, signature_after = hash_source(source.path, source.kind)
                logger.info(
                    "Source SHA-256 after indexing: %s %s",
                    source_path,
                    sha256_after,
                )
                if sha256_after != sha256_before:
                    message = "Source changed while it was being indexed."
                    logger.error(
                        "%s before=%s after=%s",
                        message,
                        sha256_before,
                        sha256_after,
                    )
                    self.database.mark_source(
                        source_id,
                        "source_changed",
                        sha256_after=sha256_after,
                        error=message,
                    )
                    sources_changed += 1
                    final_status = "source_changed"
                    report(final_status, source_number, source_path, error=message)
                    continue

                logger.info(
                    "Source unchanged after indexing: %s sha256=%s",
                    source_path,
                    sha256_after,
                )
                if cancellation.is_set():
                    final_status = "cancelled"
                    self.database.mark_source(
                        source_id,
                        "cancelled",
                        sha256_after=sha256_after,
                        verified_signature=signature_after,
                    )
                    report(final_status, source_number, source_path)
                    continue

                self.database.mark_source(
                    source_id,
                    "completed",
                    sha256_after=sha256_after,
                    verified_signature=signature_after,
                )
                sources_completed += 1
                report("completed", source_number, source_path)
            except Exception as error:
                # One unreadable source must not prevent the remaining
                # sources from being indexed; the failure is recorded.
                logger.exception("Indexing source failed: %s", source_path)
                sources_failed += 1
                if final_status == "completed":
                    final_status = "completed_with_errors"
                error_text = f"{type(error).__name__}: {error}"
                if source_id is not None:
                    self._mark_failed(source, source_id, sha256_before, error_text)
                report("failed", source_number, source_path, error=error_text)

        if cancellation.is_set() and final_status == "completed":
            final_status = "cancelled"
        return IndexSummary(
            status=final_status,
            sources_completed=sources_completed,
            sources_skipped=sources_skipped,
            sources_changed=sources_changed,
            messages_scanned=messages_scanned,
            messages_indexed=messages_indexed,
            messages_skipped=messages_skipped,
            sources_failed=sources_failed,
        )

    def _write_batch(
        self,
        source_id: int,
        folders: dict[str, int],
        batch: list[MessageMetadata],
    ) -> tuple[int, int]:
        """Store one batch in a single transaction, retrying on lock contention.

        A failed attempt rolls back completely, so a retry repeats the whole
        batch. Only a persistent lock or a non-lock error is raised.
        """
        delay = 0.1
        for attempt in range(1, BATCH_WRITE_ATTEMPTS + 1):
            new_folders: dict[str, int] = {}
            indexed = 0
            skipped = 0
            try:
                with self.database.connection(
                    write=True, timeout=BATCH_LOCK_TIMEOUT_SECONDS
                ) as connection:
                    for metadata in batch:
                        folder_id = folders.get(metadata.folder) or new_folders.get(metadata.folder)
                        if folder_id is None:
                            folder_id = self.database.add_folder(
                                source_id,
                                metadata.folder,
                                metadata.folder.rsplit("/", 1)[-1],
                                connection=connection,
                            )
                            new_folders[metadata.folder] = folder_id
                        dedup_key, thread_key, normalized_subject, content_hash = (
                            _message_keys(source_id, metadata)
                        )
                        inserted = self.database.store_message(
                            source_id=source_id,
                            folder_id=folder_id,
                            metadata=metadata,
                            dedup_key=dedup_key,
                            thread_key=thread_key,
                            normalized_subject=normalized_subject,
                            content_hash=content_hash,
                            message_hash=content_hash,
                            parent_ids=tuple(
                                _parent_ids(
                                    metadata.references,
                                    metadata.in_reply_to,
                                    metadata.message_id,
                                )
                            ),
                            subject_fallback=_is_reply_subject(metadata.subject),
                            connection=connection,
                        )
                        if inserted:
                            indexed += 1
                        else:
                            skipped += 1
            except Exception as error:
                if not is_busy_error(error) or attempt == BATCH_WRITE_ATTEMPTS:
                    raise
                logger.warning(
                    "Database busy while writing %d messages (attempt %d/%d); retrying in %.1f s",
                    len(batch),
                    attempt,
                    BATCH_WRITE_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)
                delay *= 2
                continue
            folders.update(new_folders)
            return indexed, skipped
        raise AssertionError("unreachable")

    def _hash_before(self, source: DiscoveredSource) -> str:
        """Hash a source, reusing the discovery hash while its file signature is unchanged."""
        source_path = str(source.path)
        if source.signature is not None:
            try:
                current = source_signature(source.path, source.kind)
            except OSError:
                current = None
            if current == source.signature:
                logger.info(
                    "Source unchanged since discovery hashing: %s sha256=%s",
                    source_path,
                    source.sha256,
                )
                return source.sha256
        logger.info("Hashing source before indexing: %s", source_path)
        sha256_before, _ = hash_source(source.path, source.kind)
        logger.info("Source SHA-256 before indexing: %s %s", source_path, sha256_before)
        return sha256_before

    def _mark_failed(
        self,
        source: DiscoveredSource,
        source_id: int,
        sha256_before: str | None,
        error_text: str,
    ) -> None:
        """Record a failed source after checking whether it changed meanwhile."""
        try:
            sha256_after, _ = hash_source(source.path, source.kind)
        except OSError as hash_error:
            self.database.mark_source(
                source_id,
                "failed",
                error=f"{error_text}; unable to verify source after indexing: {hash_error}",
            )
            return
        logger.info(
            "Source SHA-256 after indexing failure: %s %s", source.path, sha256_after
        )
        if sha256_before is not None and sha256_after != sha256_before:
            self.database.mark_source(
                source_id,
                "source_changed",
                sha256_after=sha256_after,
                error="Source changed while indexing failed.",
            )
        else:
            self.database.mark_source(
                source_id, "failed", sha256_after=sha256_after, error=error_text
            )


class IndexingJob:
    """Run an indexing service in a thread with cancellation and result access."""

    def __init__(
        self,
        service: IndexingService,
        sources: list[DiscoveredSource],
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """Create and start a daemon worker and its cancellation event."""
        self._service = service
        self._sources = sources
        self._progress_callback = progress_callback
        self._cancel_event = threading.Event()
        self._done_event = threading.Event()
        self._lock = threading.Lock()
        self._last_progress: IndexProgress | None = None
        self._summary: IndexSummary | None = None
        self._error: Exception | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="Mail-Scope indexer",
            daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        """Request cancellation; committed messages remain available for resume."""
        self._cancel_event.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the background job to finish and report whether it did."""
        return self._done_event.wait(timeout)

    def result(self, timeout: float | None = None) -> IndexSummary:
        """Return the job summary or re-raise its recorded exception."""
        if not self.wait(timeout):
            raise TimeoutError("Indexing job has not finished")
        if self._error is not None:
            raise RuntimeError("Background indexing failed") from self._error
        if self._summary is None:
            raise RuntimeError("Indexing job finished without a summary")
        return self._summary

    @property
    def progress(self) -> IndexProgress | None:
        """Return the latest thread-safe progress snapshot."""
        with self._lock:
            return self._last_progress

    def _run(self) -> None:
        """Execute indexing and preserve either its summary or explicit error."""
        def publish(progress: IndexProgress) -> None:
            """Update job state before forwarding progress to the consumer."""
            with self._lock:
                self._last_progress = progress
            if self._progress_callback is not None:
                self._progress_callback(progress)

        try:
            self._summary = self._service.index_sources(
                self._sources,
                cancel_event=self._cancel_event,
                progress_callback=publish,
            )
        except Exception as error:
            self._error = error
            logger.exception("Background indexing job failed")
        finally:
            self._done_event.set()


def _message_keys(
    source_id: int, metadata: MessageMetadata
) -> tuple[str, str, str, str]:
    """Build stable duplicate, thread, subject, and content-hash keys."""
    normalized_subject = _normalize_subject(metadata.subject)
    content_data = {
        "subject": normalized_subject,
        "from": _normalized_addresses(metadata.from_addresses),
        "to": _normalized_addresses(metadata.to_addresses),
        "cc": _normalized_addresses(metadata.cc_addresses),
        "bcc": _normalized_addresses(metadata.bcc_addresses),
        "date": (metadata.date or "").strip(),
        "body_sha256": _sha256_text(metadata.text_body),
        "attachments": sorted(name.casefold() for name in metadata.attachment_names),
    }
    content_hash = _sha256_text(_canonical_json(content_data))

    message_id = _normalize_message_id(metadata.message_id)
    if message_id:
        dedup_key = f"mid:{message_id}"
    elif metadata.error:
        locator = metadata.locator
        error_data = (
            source_id,
            locator.offset,
            locator.folder_path,
            locator.item_index,
        )
        dedup_key = f"error:{_sha256_text(_canonical_json(error_data))}"
    else:
        dedup_key = f"content:{content_hash}"

    parent_ids = _parent_ids(
        metadata.references,
        metadata.in_reply_to,
        metadata.message_id,
    )
    if parent_ids and parent_ids[0]:
        thread_key = f"mid:{parent_ids[0]}"
    elif message_id:
        thread_key = f"mid:{message_id}"
    else:
        # Subject-only matches are resolved against nearby, shared-participant
        # threads in the database; never by subject alone.
        thread_key = f"message:{dedup_key}"
    return dedup_key, thread_key, normalized_subject, content_hash


def _is_reply_subject(subject: str) -> bool:
    """Report whether a subject carries a Re:/Fw:/VS: style prefix."""
    return _REPLY_PREFIX.match(subject or "") is not None


def _parent_ids(
    references: str | None,
    in_reply_to: str | None,
    message_id: str | None,
) -> list[str]:
    """Prefer root IDs from References, then use the direct reply parent."""
    current_id = _normalize_message_id(message_id)
    referenced_ids = [
        identifier
        for identifier in _reference_ids(references)
        if identifier and identifier != current_id
    ]
    if referenced_ids:
        return referenced_ids
    direct_parent = _normalize_message_id(in_reply_to)
    if direct_parent and direct_parent != current_id:
        return [direct_parent]
    return []


def _normalize_subject(subject: str) -> str:
    """Remove common reply/forward prefixes and normalize subject whitespace."""
    normalized = " ".join(subject.casefold().split())
    while True:
        without_prefix = _REPLY_PREFIX.sub("", normalized, count=1)
        if without_prefix == normalized:
            return normalized
        normalized = without_prefix.strip()


def _normalize_message_id(message_id: str | None) -> str:
    """Normalize one message identifier, including optional angle brackets."""
    value = (message_id or "").strip()
    match = _MESSAGE_ID.search(value)
    if match is not None:
        value = match.group(1)
    return value.strip().casefold()


def _reference_ids(references: str | None) -> list[str]:
    """Extract message IDs from an In-Reply-To or References header."""
    value = references or ""
    identifiers = [match.group(1).strip().casefold() for match in _MESSAGE_ID.finditer(value)]
    if identifiers:
        return identifiers
    return [part.strip().casefold() for part in value.split() if part.strip()]


def _normalized_addresses(addresses: tuple[str, ...]) -> list[str]:
    """Return normalized, stable address lists for message identity."""
    return sorted({address.strip().casefold() for address in addresses if address.strip()})


def _canonical_json(value: object) -> str:
    """Serialize hash inputs deterministically without ASCII loss."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    """Hash a UTF-8 string using SHA-256."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
