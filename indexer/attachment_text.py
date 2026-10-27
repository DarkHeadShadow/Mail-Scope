"""Background extraction of searchable attachment text for one case.

Only messages that have attachments are read again from their source; the
messages themselves are not re-indexed. Every processed message is recorded,
so a stopped or interrupted run continues where it left off.
"""

__author__ = "Bálint Szabolcs"

import hashlib
import logging
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from attachments import textextract as _textextract  # noqa: F401 – loaded by name in the worker; keeps it in the build
from attachments.containers import seven_zip_path
from attachments.sandbox import AttachmentProcessingError, run_isolated
from sources.discover import DiscoveredSource, hash_source, source_signature
from sources.models import MessageLocator, SourceReader
from sources.reader_factory import open_source

from .database import IndexDatabase, is_busy_error


logger = logging.getLogger(__name__)
MESSAGE_TIMEOUT_SECONDS = 120.0
BATCH_SIZE = 50
# Measured with the bundled test files: starting the worker dominates.
SECONDS_PER_MESSAGE = 1.0


@dataclass(frozen=True)
class AttachmentTextProgress:
    """A snapshot of the running extraction."""

    processed: int
    failed: int
    current: str


class AttachmentTextJob:
    """Extract attachment text in a daemon thread until nothing is pending or cancelled."""

    def __init__(self, database: IndexDatabase, case_directory: Path) -> None:
        """Start the worker thread immediately."""
        self._database = database
        self._work_root = case_directory / ".mailscope" / "tmp"
        self._cancel_event = threading.Event()
        self._done_event = threading.Event()
        self._lock = threading.Lock()
        self._progress = AttachmentTextProgress(0, 0, "")
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, name="Mail-Scope attachment text", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Stop after the message being processed; finished messages stay stored."""
        self._cancel_event.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the thread to finish and report whether it did."""
        return self._done_event.wait(timeout)

    @property
    def progress(self) -> AttachmentTextProgress:
        """Return the latest progress snapshot."""
        with self._lock:
            return self._progress

    def _run(self) -> None:
        readers: dict[str, SourceReader] = {}
        checked_sources: dict[int, str | None] = {}
        processed = failed = 0
        logger.info("Attachment text extraction started")
        try:
            while not self._cancel_event.is_set():
                pending = self._database.attachment_text_pending(BATCH_SIZE)
                if not pending:
                    break
                for message_id in pending:
                    if self._cancel_event.is_set():
                        break
                    error = self._process(message_id, readers, checked_sources)
                    processed += 1
                    failed += error is not None
                    with self._lock:
                        self._progress = AttachmentTextProgress(processed, failed, "")
        except Exception as error:
            if is_busy_error(error):
                self.error = "Az adatbázis foglalt volt; a feldolgozás később folytatódik."
            else:
                self.error = str(error)
            logger.exception("Attachment text extraction stopped")
        finally:
            for reader in readers.values():
                try:
                    reader.close()
                except Exception:
                    logger.exception("Unable to close source reader: %s", reader.path)
            logger.info(
                "Attachment text extraction %s: %d messages, %d failed",
                "cancelled" if self._cancel_event.is_set() else "finished", processed, failed,
            )
            self._done_event.set()

    def _process(
        self,
        message_id: int,
        readers: dict[str, SourceReader],
        checked_sources: dict[int, str | None],
    ) -> str | None:
        """Extract and store one message's attachment text; return the error, if any."""
        occurrences = self._database.occurrences_for_message(message_id)
        if not occurrences:
            self._database.store_attachment_texts(message_id, [], error="A levélnek nincs forrásbeli példánya.")
            return "no occurrence"
        occurrence = occurrences[0]
        source_id = int(occurrence["source_id"])
        source = DiscoveredSource(
            path=Path(str(occurrence["source_path"])),
            kind=str(occurrence["source_kind"]),
            size=int(occurrence["source_size"]),
            sha256=str(occurrence["discovered_sha256"]),
            folder=str(occurrence["folder_path"] or ""),
        )
        if source_id not in checked_sources:
            checked_sources[source_id] = self._check_source(source_id, source)
        problem = checked_sources[source_id]
        if problem is None:
            work_dir = self._work_root / uuid.uuid4().hex
            try:
                key = str(source.path)
                if key not in readers:
                    readers[key] = open_source(source)
                attachments = readers[key].open_attachments(
                    MessageLocator(
                        offset=int(occurrence["locator_offset"]),
                        length=int(occurrence["locator_length"]),
                        folder_path=str(occurrence["locator_folder_path"] or "") or None,
                        item_index=(
                            None
                            if int(occurrence["locator_item_index"]) < 0
                            else int(occurrence["locator_item_index"])
                        ),
                        item_id=str(occurrence["locator_item_id"] or "") or None,
                    )
                )
                known = self._database.known_attachment_texts(
                    [hashlib.sha256(item.data).hexdigest() for item in attachments if not item.error]
                )
                with self._lock:
                    self._progress = AttachmentTextProgress(
                        self._progress.processed, self._progress.failed,
                        ", ".join(item.filename for item in attachments)[:200],
                    )
                results = run_isolated(
                    "attachments.textextract:extract_message",
                    # Unreadable attachments keep their place, so paths match the viewer's.
                    [(item.filename, item.content_type, None if item.error else item.data) for item in attachments],
                    known,
                    seven_zip_path(),
                    str(work_dir),
                    timeout=MESSAGE_TIMEOUT_SECONDS,
                )
                self._database.store_attachment_texts(message_id, results)
                return None
            except (AttachmentProcessingError, OSError, ValueError, RuntimeError) as error:
                if is_busy_error(error):
                    raise
                problem = str(error)
                logger.warning("Attachment text not extracted: message %s: %s", message_id, problem)
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)
        self._database.store_attachment_texts(message_id, [], error=problem)
        return problem

    def _check_source(self, source_id: int, source: DiscoveredSource) -> str | None:
        """Verify a source once per run, like opening a message does; return the problem."""
        if not source.path.exists():
            return "Az eredeti forrásfájl nem érhető el."
        try:
            current = source_signature(source.path, source.kind)
            stored = self._database.verified_source(str(source.path))
            if stored is not None and stored[1] == current and stored[0] == source.sha256:
                return None
            logger.info("Verifying source SHA-256 before attachment text extraction: %s", source.path)
            sha256, signature = hash_source(source.path, source.kind)
        except OSError as error:
            return f"Az eredeti forrásfájl nem ellenőrizhető: {error}"
        if not self._database.record_full_check(source_id, sha256, signature):
            logger.error("Source changed since indexing, attachment text skipped: %s", source.path)
            return "A forrásfájl az indexelés óta megváltozott."
        return None
