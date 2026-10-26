"""Loopback-only API and static UI for case and archive browsing."""

__author__ = "Bálint Szabolcs"

import calendar
import csv
import hashlib
import io
import json
import logging
import mimetypes
import re
import sqlite3
import threading
import zipfile
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from indexer import IndexDatabase, IndexingJob, IndexingService, PersonDirectory
from sources.models import MessageLocator, SourceReader
from sources.content import message_body_text
from sources.discover import (
    DiscoveredSource,
    discover_sources,
    hash_source,
    source_signature,
)
from sources.reader_factory import open_source
from version import __version__

from .applog import application_log_path, attach_case_log


logger = logging.getLogger(__name__)
_UI_DIRECTORY = Path(__file__).resolve().parent.parent / "ui"
_LOGO_DIRECTORY = Path(__file__).resolve().parent.parent / "assets" / "logo"
_STATIC_ASSETS = {
    "/favicon.ico": (_LOGO_DIRECTORY / "mail-scope.ico", "image/vnd.microsoft.icon"),
    "/assets/logo/png/mail-scope-logo-dark-bg.png": (
        _LOGO_DIRECTORY / "png" / "mail-scope-logo-dark-bg.png",
        "image/png",
    ),
}
_MAX_REQUEST_BYTES = 1_000_000
_MAX_QUERY_LENGTH = 512


def _browse_local_path(kind: str, initial_path: str) -> str:
    """Show a native Windows picker for a directory or a supported mail file."""
    from tkinter import Tk, filedialog

    if kind not in {"directory", "file"}:
        raise ValueError("A tallózás típusa directory vagy file legyen.")

    initial_directory = ""
    if initial_path:
        candidate = Path(initial_path).expanduser()
        if not candidate.is_dir():
            candidate = candidate.parent
        if candidate.is_dir():
            initial_directory = str(candidate)

    root = Tk()
    root.withdraw()
    try:
        root.update()
        if kind == "directory":
            return filedialog.askdirectory(
                parent=root,
                title="Mappa kiválasztása",
                initialdir=initial_directory or None,
                mustexist=True,
            )
        return filedialog.askopenfilename(
            parent=root,
            title="Levelezési fájl kiválasztása",
            initialdir=initial_directory or None,
            filetypes=(
                ("Támogatott levelezési fájlok", "*.pst *.ost *.mbox *.mbx *.mbs *.eml"),
                ("Minden fájl", "*"),
            ),
        )
    finally:
        root.destroy()


class ApiError(Exception):
    """An expected API error with an appropriate HTTP status code."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        """Retain a safe response status and user-readable explanation."""
        super().__init__(message)
        self.status = status


class MailScopeApplication:
    """Hold the active case, discovery results, and optional indexing job."""

    def __init__(self) -> None:
        """Start with no active case or running background work."""
        self._lock = threading.RLock()
        self.case_directory: Path | None = None
        self.database: IndexDatabase | None = None
        self.indexing_service: IndexingService | None = None
        self.job: IndexingJob | None = None
        self._discovered: dict[str, DiscoveredSource] = {}
        self._verified_sources: dict[str, tuple[tuple[int, ...], str]] = {}
        self._hash_locks: dict[str, threading.Lock] = {}
        self._readers: dict[tuple[str, str], SourceReader] = {}
        self.case_log: Path | None = None

    def select_case(self, path: str) -> dict[str, Any]:
        """Create or open a case directory and initialize its local index."""
        case_path = Path(path).expanduser()
        if not case_path.is_absolute():
            raise ValueError("Az ügy mappájának abszolút elérési útját adja meg.")
        case_path = case_path.resolve()
        with self._lock:
            if self.job is not None and not self.job.wait(0):
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "Indexelés közben nem lehet másik ügyet megnyitni.",
                )
            if case_path.exists() and not case_path.is_dir():
                raise ValueError("Az ügy elérési útja nem mappa.")
            case_path.mkdir(parents=True, exist_ok=True)
            self.case_directory = case_path
            self.database = IndexDatabase.for_case(case_path)
            self.case_log = attach_case_log(case_path)
            logger.info("Case opened: %s", case_path)
            self.indexing_service = IndexingService(self.database)
            self._discovered.clear()
            self._verified_sources.clear()
            self._close_readers()
            self.job = None
            return self.status()

    def discover(self, path: str) -> list[dict[str, Any]]:
        """Scan one selected file or folder and retain its detected sources."""
        with self._lock:
            database = self._require_case()
        # Hashing large sources must not block other requests (status, archive).
        sources = discover_sources(path)
        for source in sources:
            logger.info(
                "Source discovered: %s kind=%s size=%d sha256=%s",
                source.path,
                source.kind,
                source.size,
                source.sha256,
            )
        with self._lock:
            if self.database is not database:
                raise ApiError(HTTPStatus.CONFLICT, "Az ügy a felismerés közben megváltozott.")
            self._discovered = {str(source.path.resolve()): source for source in sources}
            return [_source_json(source) for source in sources]

    def start_index(self, paths: list[str]) -> dict[str, Any]:
        """Start background indexing for previously discovered, selected files."""
        with self._lock:
            self._require_case()
            if self.job is not None and not self.job.wait(0):
                raise ApiError(HTTPStatus.CONFLICT, "Már fut indexelési feladat.")
            normalized_paths = [str(Path(path).resolve()) for path in paths]
            if not normalized_paths:
                raise ValueError("Válasszon ki legalább egy felismert forrást.")
            if len(set(normalized_paths)) != len(normalized_paths):
                raise ValueError("A kiválasztott források között ismétlődés van.")
            missing = [path for path in normalized_paths if path not in self._discovered]
            if missing:
                raise ValueError("A kiválasztott források közül néhányat újra fel kell ismerni.")
            sources = [self._discovered[path] for path in normalized_paths]
            service = self.indexing_service
            if service is None:
                raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "Az indexelő nem inicializálódott.")
            self.job = service.start(sources)
            return self.status()

    def cancel_index(self) -> dict[str, Any]:
        """Request a safe cancellation of the active indexing job."""
        with self._lock:
            if self.job is None or self.job.wait(0):
                raise ApiError(HTTPStatus.CONFLICT, "Nincs futó indexelési feladat.")
            self.job.cancel()
            return self.status()

    def stop_indexing_safely(self) -> bool:
        """Cancel active indexing and wait for its committed checkpoint."""
        with self._lock:
            job = self.job
            is_running = job is not None and not job.wait(0)
            if is_running:
                job.cancel()
        if is_running and job is not None:
            job.wait()
        return is_running

    def status(self) -> dict[str, Any]:
        """Return case, source, message-count, and job progress information."""
        with self._lock:
            if self.database is None or self.case_directory is None:
                return {
                    "version": __version__,
                    "log": {"application": _optional_path(application_log_path()), "case": None},
                    "case": None,
                    "sources": [],
                    "message_count": 0,
                    "job": None,
                }
            job_data: dict[str, Any] | None = None
            if self.job is not None:
                progress = self.job.progress
                job_data = {
                    "running": not self.job.wait(0),
                    "progress": None if progress is None else asdict(progress),
                    "summary": None,
                    "error": None,
                }
                if not job_data["running"]:
                    try:
                        job_data["summary"] = asdict(self.job.result(0))
                    except RuntimeError as error:
                        job_data["error"] = str(error.__cause__ or error)
            return {
                "version": __version__,
                "log": {
                    "application": _optional_path(application_log_path()),
                    "case": _optional_path(self.case_log),
                },
                "case": {"path": str(self.case_directory), "name": self.case_directory.name},
                "sources": [_row_json(row) for row in self.database.sources()],
                "message_count": self.database.message_count(),
                "job": job_data,
            }

    def archive(
        self,
        *,
        query: str,
        source_id: int | None,
        folder_id: int | None,
        has_attachments: bool,
        deleted_only: bool,
        item_type: str | None,
        include_duplicates: bool,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Query indexed archive rows and convert SQLite records to JSON values."""
        with self._lock:
            database = self._require_case()
            if len(query) > _MAX_QUERY_LENGTH:
                raise ValueError("A keresőkifejezés legfeljebb 512 karakter lehet.")
            return [
                _row_json(row)
                for row in database.archive_messages(
                    query,
                    source_id=source_id,
                    folder_id=folder_id,
                    has_attachments=has_attachments,
                    deleted_only=deleted_only,
                    item_type=item_type,
                    include_duplicates=include_duplicates,
                    limit=limit,
                    offset=offset,
                )
            ]

    def people(self) -> dict[str, list[dict[str, Any]]]:
        """Return curated people and observed addresses available for assignment."""
        with self._lock:
            database = self._require_case()
            directory = PersonDirectory(database)
            return {
                "people": [asdict(person) for person in directory.people()],
                "observed_addresses": [
                    _row_json(row) for row in database.observed_addresses()
                ],
            }

    def detailed_search(
        self,
        conditions: tuple[dict[str, Any], ...],
        *,
        operator: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        """Run a validated detailed search with pagination and result count."""
        with self._lock:
            database = self._require_case()
            rows, total = database.detailed_search(
                conditions,
                operator=operator,
                limit=limit,
                offset=offset,
            )
            return {
                "results": [_row_json(row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def detailed_search_export(
        self,
        conditions: tuple[dict[str, Any], ...],
        *,
        operator: str,
        export_format: str,
    ) -> tuple[bytes, str, str]:
        """Export bounded detailed-search hits as CSV or verified original EML files."""
        if export_format not in {"csv", "eml_zip"}:
            raise ValueError("Az exportformátum CSV vagy EML ZIP legyen.")
        with self._lock:
            database = self._require_case()
            rows, total = database.detailed_search(
                conditions,
                operator=operator,
                limit=10001 if export_format == "csv" else 501,
            )
            maximum = 10000 if export_format == "csv" else 500
            if total > maximum:
                raise ApiError(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    f"Ez az export legfeljebb {maximum} találatot tartalmazhat; szűkítse a keresést.",
                )
            if export_format == "csv":
                output = io.StringIO(newline="")
                writer = csv.writer(output)
                writer.writerow(
                    (
                        "subject",
                        "sent_date",
                        "sender",
                        "participants",
                        "source",
                        "folder",
                        "message_id",
                        "sha256",
                        "labels",
                        "copy_count",
                    )
                )
                for row in rows:
                    writer.writerow(
                        (
                            _csv_safe(row["subject"]),
                            _csv_safe(row["sent_date"]),
                            _csv_safe(row["sender"]),
                            _csv_safe(row["participant_addresses"]),
                            _csv_safe(row["source_path"]),
                            _csv_safe(
                                "/".join(
                                    value
                                    for value in (
                                        row["folder_path"],
                                        row["folder_name"],
                                    )
                                    if value
                                )
                            ),
                            _csv_safe(row["internet_message_id"]),
                            _csv_safe(row["content_hash"]),
                            _csv_safe(row["labels"]),
                            row["copy_count"],
                        )
                    )
                return (
                    output.getvalue().encode("utf-8-sig"),
                    "text/csv; charset=utf-8",
                    "Mail-Scope-search.csv",
                )

            selected = [
                (int(row["message_id"]), str(row["subject"] or "level")) for row in rows
            ]
        return (
            self._eml_zip(selected),
            "application/zip",
            "Mail-Scope-search.eml.zip",
        )

    def _eml_zip(self, messages: list[tuple[int, str]]) -> bytes:
        """Package messages as EML with a SHA-256 list and a provenance manifest.

        MBOX and EML entries hold the original source bytes. Outlook stores no
        original MIME, so PST/OST entries are reconstructions; MANIFEST.txt
        records which entries are which.
        """
        archive = BytesIO()
        hashes: list[str] = []
        manifest: list[str] = [
            "Mail-Scope EML export",
            f"Mail-Scope {__version__} – készítő: Bálint Szabolcs",
            "",
            "fájl\tSHA-256\teredet\tforrásfájl\tforrás SHA-256\thely a forrásban",
        ]
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            total_raw_bytes = 0
            for index, (message_id, subject) in enumerate(messages, start=1):
                opened = self.open_message(message_id, include_raw=True)
                raw = opened["raw_eml"]
                total_raw_bytes += len(raw)
                if total_raw_bytes > 256 * 1024 * 1024:
                    raise ApiError(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "Az EML ZIP-export legfeljebb 256 MiB forráslevelet tartalmazhat.",
                    )
                filename = _safe_eml_filename(index, subject)
                digest = hashlib.sha256(raw).hexdigest()
                output.writestr(filename, raw)
                hashes.append(f"{digest}  {filename}")
                source = opened["raw_source"]
                manifest.append(
                    "\t".join(
                        (
                            filename,
                            digest,
                            (
                                "PST/OST-ből rekonstruált (nem eredeti bájtok)"
                                if source["reconstructed"]
                                else "eredeti forrásbájtok"
                            ),
                            source["path"],
                            source["sha256"],
                            source["location"],
                        )
                    )
                )
            output.writestr("SHA256SUMS.txt", ("\n".join(hashes) + "\n").encode("utf-8"))
            output.writestr("MANIFEST.txt", ("\n".join(manifest) + "\n").encode("utf-8"))
        return archive.getvalue()

    def saved_searches(self) -> list[dict[str, Any]]:
        """Return named searches with their validated, decoded condition groups."""
        with self._lock:
            database = self._require_case()
            result: list[dict[str, Any]] = []
            for row in database.saved_searches():
                try:
                    query = json.loads(str(row["query_json"]))
                except json.JSONDecodeError as error:
                    logger.exception("Saved search could not be decoded: %s", row["name"])
                    raise ApiError(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        f"A mentett keresés hibás JSON: {row['name']}",
                    ) from error
                result.append(
                    {
                        "name": str(row["name"]),
                        "query": query,
                        "created_at": str(row["created_at"]),
                        "updated_at": str(row["updated_at"]),
                    }
                )
            return result

    def save_search(
        self,
        name: str,
        conditions: tuple[dict[str, Any], ...],
        operator: str,
    ) -> dict[str, Any]:
        """Validate and store one named flat or grouped detailed-search query."""
        with self._lock:
            database = self._require_case()
            database.detailed_search(conditions, operator=operator, limit=1)
            is_grouped = bool(conditions) and all(
                "conditions" in condition for condition in conditions
            )
            database.save_search(
                name,
                {
                    "groups" if is_grouped else "conditions": list(conditions),
                    "operator": operator,
                },
            )
            return {"name": name.strip()}

    def delete_saved_search(self, name: str) -> dict[str, Any]:
        """Delete a saved-search definition from the active case."""
        with self._lock:
            database = self._require_case()
            if not database.delete_saved_search(name):
                raise ApiError(HTTPStatus.NOT_FOUND, "A mentett keresés nem található.")
            return {"deleted": name}

    def set_labels(
        self, message_ids: tuple[int, ...], label: str, *, enabled: bool
    ) -> dict[str, Any]:
        """Assign or remove one case-local label across all message copies."""
        with self._lock:
            database = self._require_case()
            affected = database.set_message_label(message_ids, label, enabled=enabled)
            return {"label": label.strip(), "occurrences_affected": affected}

    def create_person(
        self, display_name: str, addresses: tuple[str, ...]
    ) -> dict[str, Any]:
        """Create a person and assign their selected observed email aliases."""
        with self._lock:
            database = self._require_case()
            person = PersonDirectory(database).create_person(display_name, addresses)
            return asdict(person)

    def conversation(
        self,
        *,
        person_ids: tuple[int, ...],
        mode: str,
        include_cc_bcc: bool,
        date_from: str,
        date_to: str,
        query: str,
        thread_key: str | None,
    ) -> dict[str, Any]:
        """Build a dated, person-based conversation timeline and activity summary."""
        with self._lock:
            database = self._require_case()
            if len(person_ids) < 2 or len(set(person_ids)) != len(person_ids):
                raise ValueError("A beszélgetésnézethez legalább két különböző személyt válasszon.")
            if len(person_ids) > 50:
                raise ValueError("Egyszerre legfeljebb 50 személy választható.")
            if len(query) > _MAX_QUERY_LENGTH:
                raise ValueError("A beszélgetés keresőkifejezése legfeljebb 512 karakter lehet.")
            selected_people = {
                person.id: person
                for person in PersonDirectory(database).people()
                if person.id in person_ids
            }
            if len(selected_people) != len(person_ids):
                raise ValueError("A kijelölt személyek között ismeretlen azonosító szerepel.")
            start_date = _optional_date(date_from, "Kezdő dátum")
            end_date = _optional_date(date_to, "Záró dátum")
            if start_date is not None and end_date is not None and start_date > end_date:
                raise ValueError("A kezdő dátum nem lehet későbbi a záró dátumnál.")
            start_timestamp = (
                None
                if start_date is None
                else calendar.timegm(
                    datetime.combine(
                        start_date, datetime.min.time(), tzinfo=timezone.utc
                    ).utctimetuple()
                )
            )
            end_timestamp = (
                None
                if end_date is None or end_date == date.max
                else calendar.timegm(
                    datetime.combine(
                        end_date + timedelta(days=1),
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    ).utctimetuple()
                )
            )

            rows = database.conversation_messages(
                person_ids,
                mode=mode,
                include_cc_bcc=include_cc_bcc,
                query=query,
                thread_key=thread_key,
                date_from_timestamp=start_timestamp,
                date_to_timestamp=end_timestamp,
                limit=10001,
            )
            if len(rows) > 10000:
                raise ApiError(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    "A beszélgetés több mint 10 000 levelet tartalmaz; szűkítsen dátumra vagy témára.",
                )
            participant_rows = database.conversation_participants(
                tuple(int(row["message_id"]) for row in rows)
            )
            participants_by_message: dict[int, list[dict[str, Any]]] = {}
            for participant in participant_rows:
                message_id = int(participant["message_id"])
                person_id = (
                    None
                    if participant["person_id"] is None
                    else int(participant["person_id"])
                )
                participants_by_message.setdefault(message_id, []).append(
                    {
                        "role": str(participant["role"]),
                        "address": str(participant["address"]),
                        "display_name": str(participant["display_name"] or ""),
                        "person_id": person_id,
                        "person_name": (
                            str(participant["person_name"])
                            if participant["person_name"] is not None
                            else str(participant["display_name"] or participant["address"])
                        ),
                        "selected": person_id in selected_people,
                    }
                )

            messages: list[dict[str, Any]] = []
            for row in rows:
                sent_at = _parse_message_date(row["sent_date"])
                sent_day = None if sent_at is None else sent_at.date()
                if start_date is not None and (sent_day is None or sent_day < start_date):
                    continue
                if end_date is not None and (sent_day is None or sent_day > end_date):
                    continue
                message = _row_json(row)
                message["sent_at"] = None if sent_at is None else sent_at.isoformat()
                message["day"] = None if sent_day is None else sent_day.isoformat()
                message["participants"] = participants_by_message.get(
                    int(row["message_id"]), []
                )
                messages.append(message)
            messages.sort(
                key=lambda message: (
                    message["sent_at"] is None,
                    message["sent_at"] or "",
                    message["message_id"],
                )
            )

            months: dict[str, int] = {}
            threads: dict[str, str] = {}
            previous_day: date | None = None
            for message in messages:
                message_date = _optional_date(message["day"] or "", "Levél dátuma")
                if message_date is None:
                    message["silent_days_before"] = 0
                    continue
                if previous_day is not None:
                    message["silent_days_before"] = max(
                        0, (message_date - previous_day).days - 1
                    )
                else:
                    message["silent_days_before"] = 0
                previous_day = message_date
                month = message_date.strftime("%Y-%m")
                months[month] = months.get(month, 0) + 1
                threads[str(message["thread_key"])] = str(
                    message["subject"] or "(nincs tárgy)"
                )
            return {
                "people": [asdict(selected_people[person_id]) for person_id in person_ids],
                "messages": messages,
                "months": [
                    {"month": month, "count": count}
                    for month, count in sorted(months.items())
                ],
                "threads": [
                    {"key": key, "subject": subject}
                    for key, subject in sorted(
                        threads.items(), key=lambda item: (item[1].casefold(), item[0])
                    )
                ],
                "truncated": False,
            }

    def conversation_eml_zip(self, **filters: Any) -> bytes:
        """Build a ZIP of verified original messages and their SHA-256 manifest."""
        result = self.conversation(**filters)
        messages = result["messages"]
        if len(messages) > 500:
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "Az EML ZIP-export legfeljebb 500 levelet tartalmazhat; szűkítse a keresést.",
            )
        return self._eml_zip(
            [
                (int(message["message_id"]), str(message["subject"] or "level"))
                for message in messages
            ]
        )

    def open_message(
        self, message_id: int, *, include_raw: bool = False
    ) -> dict[str, Any]:
        """Load a selected message from its original source after hash verification."""
        with self._lock:
            database = self._require_case()
            indexed_message = database.message_details(message_id)
            if indexed_message is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "A levél nem található az indexben.")
            occurrences = database.occurrences_for_message(message_id)
            if not occurrences:
                raise ApiError(HTTPStatus.NOT_FOUND, "A levélnek nincs forrásbeli példánya.")
        occurrence = occurrences[0]
        source_path = Path(str(occurrence["source_path"]))
        source_kind = str(occurrence["source_kind"])
        if not source_path.exists():
            raise ApiError(
                HTTPStatus.CONFLICT,
                "Az eredeti forrásfájl nem érhető el; a levél nem nyitható meg.",
            )
        expected_hash = str(occurrence["discovered_sha256"])
        # Verification may hash a multi-gigabyte source; it runs outside the
        # application lock so that status and browsing requests keep working.
        try:
            actual_hash = self._verified_source_hash(
                database, source_path, source_kind
            )
        except OSError as error:
            raise ApiError(
                HTTPStatus.CONFLICT,
                f"Az eredeti forrásfájl nem ellenőrizhető: {error}",
            ) from error
        if actual_hash != expected_hash:
            logger.error(
                "Source hash mismatch on open: %s expected=%s actual=%s",
                source_path,
                expected_hash,
                actual_hash,
            )
            raise ApiError(
                HTTPStatus.CONFLICT,
                "A forrásfájl az indexelés óta megváltozott; a forenzikus megnyitás leállt.",
            )
        with self._lock:
            if self.database is not database:
                raise ApiError(HTTPStatus.CONFLICT, "Az ügy a megnyitás közben megváltozott.")
            source = DiscoveredSource(
                path=source_path,
                kind=str(occurrence["source_kind"]),
                size=int(occurrence["source_size"]),
                sha256=expected_hash,
                folder=str(occurrence["folder_path"] or ""),
            )
            locator = MessageLocator(
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
            try:
                reader = self._reader(source)
                raw_message = reader.open_message(locator)
                body = message_body_text(raw_message)
                raw_eml = reader.open_message_bytes(locator) if include_raw else None
            except Exception as error:
                logger.exception("Unable to open indexed message %s", message_id)
                raise ApiError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    f"A levél nem olvasható az eredeti forrásból: {error}",
                ) from error
            result: dict[str, Any] = {
                "message": _row_json(indexed_message),
                "headers": _message_headers(raw_message),
                "body": body,
                "participants": [_row_json(row) for row in database.participant_rows(message_id)],
                "attachments": [
                    _row_json(row)
                    for row in database.attachment_rows_for_message(message_id)
                ],
                "labels": database.labels_for_message(message_id),
                "occurrences": [_row_json(row) for row in occurrences],
                "opened_from_occurrence": int(occurrence["id"]),
            }
            if include_raw:
                result["raw_eml"] = raw_eml
                result["raw_source"] = {
                    "reconstructed": source_kind in {"pst", "ost"},
                    "path": str(source_path),
                    "sha256": expected_hash,
                    "location": _locator_text(locator),
                }
            return result

    def _reader(self, source: DiscoveredSource) -> SourceReader:
        """Reuse one reader per source, so PST/OST stores are not reopened per message."""
        key = (str(source.path.resolve()), source.kind)
        reader = self._readers.get(key)
        if reader is None:
            reader = open_source(source)
            self._readers[key] = reader
        return reader

    def _close_readers(self) -> None:
        """Release file handles held by cached source readers."""
        readers, self._readers = self._readers, {}
        for reader in readers.values():
            try:
                reader.close()
            except Exception:
                logger.exception("Unable to close source reader: %s", reader.path)

    def close(self) -> None:
        """Release cached source handles before the server stops."""
        with self._lock:
            self._close_readers()

    def _verified_source_hash(
        self, database: IndexDatabase, source_path: Path, kind: str
    ) -> str:
        """Return a source's SHA-256, re-hashing only when its file signature changed.

        A full hash recorded at indexing or at an earlier open stays valid
        while size, modification time and file identity are unchanged.
        """
        path_key = str(source_path.resolve())
        current = source_signature(source_path, kind)
        cached = self._verified_sources.get(path_key)
        if cached is not None and cached[0] == current:
            return cached[1]
        stored = database.verified_source(path_key)
        if stored is not None and stored[1] == current:
            self._verified_sources[path_key] = (current, stored[0])
            return stored[0]
        with self._lock:
            hash_lock = self._hash_locks.setdefault(path_key, threading.Lock())
        with hash_lock:
            cached = self._verified_sources.get(path_key)
            if cached is not None and cached[0] == source_signature(source_path, kind):
                return cached[1]
            logger.info("Verifying source SHA-256 before opening: %s", source_path)
            actual_hash, signature = hash_source(source_path, kind)
            logger.info("Source SHA-256 on open: %s %s", source_path, actual_hash)
            self._verified_sources[path_key] = (signature, actual_hash)
            return actual_hash

    def _require_case(self) -> IndexDatabase:
        """Return the selected database or report that case setup is required."""
        if self.database is None:
            raise ApiError(HTTPStatus.CONFLICT, "Előbb nyisson meg vagy hozzon létre egy ügyet.")
        return self.database


class _RequestHandler(BaseHTTPRequestHandler):
    """Serve the static UI and same-origin JSON API without external access."""

    server: "_MailScopeHttpServer"

    def do_GET(self) -> None:
        """Handle local UI and read-only API requests."""
        if not self._valid_host():
            self._send_error(HTTPStatus.FORBIDDEN, "Csak a helyi Mail-Scope cím használható.")
            return
        request = urlsplit(self.path)
        try:
            if request.path in {
                "/",
                "/index.html",
                "/app.js",
                "/styles.css",
                *_STATIC_ASSETS,
            }:
                self._serve_ui(request.path)
            elif request.path == "/api/status":
                self._send_json(HTTPStatus.OK, self.server.application.status())
            elif request.path == "/api/folders":
                database = self.server.application._require_case()
                self._send_json(
                    HTTPStatus.OK,
                    [_row_json(row) for row in database.folders()],
                )
            elif request.path == "/api/people":
                self._send_json(HTTPStatus.OK, self.server.application.people())
            elif request.path == "/api/searches":
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.saved_searches(),
                )
            elif request.path == "/api/archive":
                self._send_archive(parse_qs(request.query))
            elif request.path == "/api/conversation":
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.conversation(
                        **self._conversation_filters(parse_qs(request.query))
                    ),
                )
            elif request.path == "/api/conversation/export":
                archive = self.server.application.conversation_eml_zip(
                    **self._conversation_filters(parse_qs(request.query))
                )
                self.send_response(HTTPStatus.OK.value)
                self.send_header("Content-Type", "application/zip")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="Mail-Scope-conversation.eml.zip"',
                )
                self.send_header("Content-Length", str(len(archive)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(archive)
            elif request.path.startswith("/api/messages/"):
                message_id = _positive_integer(request.path.removeprefix("/api/messages/"))
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.open_message(message_id),
                )
            else:
                self._send_error(HTTPStatus.NOT_FOUND, "A kért útvonal nem található.")
        except ApiError as error:
            self._send_error(error.status, str(error))
        except (ValueError, sqlite3.Error) as error:
            self._send_error(HTTPStatus.BAD_REQUEST, str(error))
        except Exception:
            logger.exception("Unhandled local API request error: %s", self.path)
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                _internal_error_message(),
            )

    def do_POST(self) -> None:
        """Handle case selection, discovery, indexing, and cancellation."""
        if not self._valid_host() or not self._valid_origin():
            self._send_error(HTTPStatus.FORBIDDEN, "A kérés nem a Mail-Scope helyi felületéről érkezett.")
            return
        try:
            payload = self._read_json()
            path = urlsplit(self.path).path
            if path == "/api/browse":
                kind = _required_string(payload, "kind")
                initial_path = payload.get("initial_path", "")
                if not isinstance(initial_path, str):
                    raise ValueError("Az initial_path mező szöveges útvonal legyen.")
                response = {"path": _browse_local_path(kind, initial_path)}
            elif path == "/api/shutdown":
                cancel_indexing = payload.get("cancel_indexing", False)
                if not isinstance(cancel_indexing, bool):
                    raise ValueError("A cancel_indexing mező logikai érték legyen.")
                status = self.server.application.status()
                job = status["job"]
                is_indexing = bool(job and job["running"])
                if is_indexing and not cancel_indexing:
                    raise ApiError(
                        HTTPStatus.CONFLICT,
                        "Indexelés fut. A leállításhoz erősítse meg a biztonságos megszakítást.",
                    )
                self._send_json(
                    HTTPStatus.OK,
                    {"stopping": True, "indexing_was_running": is_indexing},
                )
                self.server.request_shutdown()
                return
            elif path == "/api/case":
                response = self.server.application.select_case(_required_string(payload, "path"))
            elif path == "/api/people":
                display_name = _required_string(payload, "display_name")
                addresses = payload.get("addresses", [])
                if not isinstance(addresses, list) or not all(
                    isinstance(address, str) for address in addresses
                ):
                    raise ValueError("Az addresses mező e-mail-címek listája legyen.")
                response = self.server.application.create_person(
                    display_name, tuple(addresses)
                )
            elif path == "/api/search/export":
                conditions, operator = self._search_conditions(payload)
                data, content_type, filename = (
                    self.server.application.detailed_search_export(
                        conditions,
                        operator=operator,
                        export_format=_required_string(payload, "format"),
                    )
                )
                self.send_response(HTTPStatus.OK.value)
                self.send_header("Content-Type", content_type)
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"',
                )
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
                return
            elif path == "/api/search":
                conditions, operator = self._search_conditions(payload)
                limit = _payload_integer(payload, "limit", 100, maximum=200)
                offset = _payload_integer(payload, "offset", 0, maximum=1_000_000)
                response = self.server.application.detailed_search(
                    conditions,
                    operator=operator,
                    limit=limit,
                    offset=offset,
                )
            elif path == "/api/searches":
                conditions, operator = self._search_conditions(payload)
                response = self.server.application.save_search(
                    _required_string(payload, "name"),
                    conditions,
                    operator,
                )
            elif path.startswith("/api/searches/"):
                name = unquote(path.removeprefix("/api/searches/"))
                response = self.server.application.delete_saved_search(name)
            elif path == "/api/labels":
                message_ids = payload.get("message_ids")
                if not isinstance(message_ids, list) or not 1 <= len(message_ids) <= 200 or not all(
                    isinstance(message_id, int)
                    and not isinstance(message_id, bool)
                    and message_id > 0
                    for message_id in message_ids
                ):
                    raise ValueError("A message_ids pozitív egész azonosítók listája legyen.")
                enabled = payload.get("enabled", True)
                if not isinstance(enabled, bool):
                    raise ValueError("Az enabled mező logikai érték legyen.")
                response = self.server.application.set_labels(
                    tuple(message_ids),
                    _required_string(payload, "label"),
                    enabled=enabled,
                )
            elif path == "/api/discover":
                response = {
                    "sources": self.server.application.discover(
                        _required_string(payload, "path")
                    )
                }
            elif path == "/api/index":
                paths = payload.get("paths")
                if not isinstance(paths, list) or not all(
                    isinstance(value, str) for value in paths
                ):
                    raise ValueError("A paths mező szöveges útvonalak listája legyen.")
                response = self.server.application.start_index(paths)
            elif path == "/api/index/cancel":
                response = self.server.application.cancel_index()
            else:
                self._send_error(HTTPStatus.NOT_FOUND, "A kért útvonal nem található.")
                return
            self._send_json(HTTPStatus.OK, response)
        except ApiError as error:
            self._send_error(error.status, str(error))
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self._send_error(HTTPStatus.BAD_REQUEST, str(error))
        except OSError as error:
            self._send_error(HTTPStatus.BAD_REQUEST, f"Fájlműveleti hiba: {error}")
        except sqlite3.Error as error:
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Index-adatbázis hiba: {error}")
        except Exception:
            logger.exception("Unhandled local API request error: %s", self.path)
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                _internal_error_message(),
            )

    def log_message(self, format_string: str, *args: object) -> None:
        """Send concise request logs to Python's configured application logger."""
        logger.info("%s - %s", self.address_string(), format_string % args)

    def _send_archive(self, query: dict[str, list[str]]) -> None:
        """Apply validated URL parameters to the indexed archive query."""
        source_id = _optional_integer(query, "source_id")
        folder_id = _optional_integer(query, "folder_id")
        limit = min(_optional_integer(query, "limit") or 100, 200)
        offset = _optional_integer(query, "offset") or 0
        result = self.server.application.archive(
            query=_single_value(query, "q"),
            source_id=source_id,
            folder_id=folder_id,
            has_attachments=_truthy(query, "has_attachments"),
            deleted_only=_truthy(query, "deleted_only"),
            item_type=_single_value(query, "item_type") or None,
            include_duplicates=_truthy(query, "include_duplicates"),
            limit=limit,
            offset=offset,
        )
        self._send_json(HTTPStatus.OK, {"messages": result})

    @staticmethod
    def _conversation_filters(query: dict[str, list[str]]) -> dict[str, Any]:
        """Parse and validate all reusable conversation and export parameters."""
        raw_people = _single_value(query, "people")
        try:
            person_ids = tuple(int(value) for value in raw_people.split(",") if value)
        except ValueError as error:
            raise ValueError("A people paraméter személyazonosítók listája legyen.") from error
        if not person_ids or any(person_id <= 0 for person_id in person_ids):
            raise ValueError("Válasszon ki legalább két személyt a beszélgetéshez.")
        include_cc_bcc = _truthy(query, "include_cc_bcc")
        return {
            "person_ids": person_ids,
            "mode": _single_value(query, "mode") or "between",
            "include_cc_bcc": include_cc_bcc,
            "date_from": _single_value(query, "date_from"),
            "date_to": _single_value(query, "date_to"),
            "query": _single_value(query, "q"),
            "thread_key": _single_value(query, "thread_key") or None,
        }

    @staticmethod
    def _search_conditions(
        payload: Any,
    ) -> tuple[tuple[dict[str, Any], ...], str]:
        """Validate flat conditions or nested, bounded AND/OR condition groups."""
        if not isinstance(payload, dict):
            raise ValueError("A keresési feltételek JSON-objektum legyenek.")
        allowed_fields = {
            "text",
            "subject",
            "from",
            "to",
            "sent_after",
            "sent_before",
            "has_attachment",
            "source",
            "folder",
            "attachment_type",
            "label",
        }

        def validate_conditions(raw_conditions: Any) -> list[dict[str, str]]:
            """Validate one non-empty bounded list of condition objects."""
            if (
                not isinstance(raw_conditions, list)
                or not 1 <= len(raw_conditions) <= 20
            ):
                raise ValueError("Adjon meg 1 és 20 közötti keresési feltételt.")
            normalized_conditions: list[dict[str, str]] = []
            for raw_condition in raw_conditions:
                if not isinstance(raw_condition, dict):
                    raise ValueError("Minden keresési feltétel objektum legyen.")
                field = raw_condition.get("field")
                value = raw_condition.get("value")
                if not isinstance(field, str) or field not in allowed_fields:
                    raise ValueError(f"Ismeretlen keresési mező: {field}")
                if not isinstance(value, str):
                    raise ValueError("Minden keresési érték szöveg legyen.")
                normalized_conditions.append({"field": field, "value": value})
            return normalized_conditions

        operator = payload.get("operator", "AND")
        if not isinstance(operator, str) or operator not in {"AND", "OR"}:
            raise ValueError("A feltétel-összekötő AND vagy OR legyen.")
        raw_groups = payload.get("groups")
        if raw_groups is None:
            conditions = validate_conditions(payload.get("conditions"))
            return tuple(conditions), operator
        if not isinstance(raw_groups, list) or not 1 <= len(raw_groups) <= 10:
            raise ValueError("Adjon meg 1 és 10 közötti feltételcsoportot.")
        groups: list[dict[str, Any]] = []
        total_conditions = 0
        for raw_group in raw_groups:
            if not isinstance(raw_group, dict):
                raise ValueError("Minden keresési csoport objektum legyen.")
            group_operator = raw_group.get("operator", "AND")
            if not isinstance(group_operator, str) or group_operator not in {"AND", "OR"}:
                raise ValueError("A csoporton belüli összekötő AND vagy OR legyen.")
            conditions = validate_conditions(raw_group.get("conditions"))
            total_conditions += len(conditions)
            if total_conditions > 20:
                raise ValueError("Összesen legfeljebb 20 feltétel adható meg.")
            groups.append({"operator": group_operator, "conditions": conditions})
        return tuple(groups), operator

    def _read_json(self) -> dict[str, Any]:
        """Read a bounded JSON object and reject other request content types."""
        if self.headers.get_content_type() != "application/json":
            raise ValueError("A kérés tartalma application/json legyen.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Érvénytelen Content-Length fejléc.") from error
        if length <= 0 or length > _MAX_REQUEST_BYTES:
            raise ValueError("A kérés törzse üres vagy túl nagy.")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            raise
        if not isinstance(payload, dict):
            raise ValueError("A JSON-törzs egy objektum legyen.")
        return payload

    def _valid_host(self) -> bool:
        """Reject requests using unexpected Host values that enable rebinding."""
        expected_host = f"127.0.0.1:{self.server.server_address[1]}"
        return self.headers.get("Host", "").casefold() == expected_host.casefold()

    def _valid_origin(self) -> bool:
        """Allow absent origins for local CLI clients and the exact local UI origin."""
        origin = self.headers.get("Origin")
        expected_origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        return origin is None or origin == expected_origin

    def _serve_ui(self, route: str) -> None:
        """Serve only explicitly mapped UI and logo assets with restrictive headers."""
        if route in _STATIC_ASSETS:
            file_path, content_type = _STATIC_ASSETS[route]
        else:
            file_name = "index.html" if route in {"/", "/index.html"} else route.removeprefix("/")
            file_path = _UI_DIRECTORY / file_name
            content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        self._send_bytes(HTTPStatus.OK, file_path.read_bytes(), content_type)

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        """Serialize one JSON response without exposing filesystem objects."""
        encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send_bytes(status, encoded, "application/json; charset=utf-8")

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        """Return an explicit and consistently shaped API error."""
        self._send_json(status, {"error": message})

    def _send_bytes(self, status: HTTPStatus, content: bytes, content_type: str) -> None:
        """Write a bounded response with browser security headers."""
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(content)


class _MailScopeHttpServer(ThreadingHTTPServer):
    """Bind one application instance to a loopback-only HTTP listener."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], application: MailScopeApplication) -> None:
        """Keep the application context available to each request handler."""
        self.application = application
        self._shutdown_lock = threading.Lock()
        self._shutdown_requested = False
        super().__init__(server_address, _RequestHandler)

    def request_shutdown(self) -> bool:
        """Stop indexing safely before shutting down the local HTTP listener."""
        with self._shutdown_lock:
            if self._shutdown_requested:
                return False
            self._shutdown_requested = True
        threading.Thread(
            target=self._finish_shutdown,
            name="Mail-Scope shutdown",
            daemon=True,
        ).start()
        return True

    def _finish_shutdown(self) -> None:
        """Wait for the active indexing checkpoint, then stop serving requests."""
        self.application.stop_indexing_safely()
        self.shutdown()
        self.application.close()


def create_server(
    application: MailScopeApplication | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> _MailScopeHttpServer:
    """Create a server that refuses to bind to any non-loopback address."""
    if host != "127.0.0.1":
        raise ValueError("A Mail-Scope szerver kizárólag a 127.0.0.1 címen futhat.")
    if not 0 <= port <= 65535:
        raise ValueError("A portnak 0 és 65535 közé kell esnie.")
    return _MailScopeHttpServer(
        (host, port),
        application or MailScopeApplication(),
    )


def _source_json(source: DiscoveredSource) -> dict[str, Any]:
    """Expose only the selected source's type, size, hash, and folder mapping."""
    return {
        "path": str(source.path),
        "kind": source.kind,
        "size": source.size,
        "sha256": source.sha256,
        "folder": source.folder,
    }


def _row_json(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a database row to JSON-friendly Python values."""
    return {key: row[key] for key in row.keys()}


def _csv_safe(value: Any) -> str:
    """Neutralize spreadsheet formula prefixes in untrusted exported text."""
    text = "" if value is None else str(value)
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _message_headers(message: EmailMessage) -> dict[str, str]:
    """Return a concise set of RFC 5322 headers for the reading pane."""
    return {
        name.casefold(): str(message.get(name, ""))
        for name in ("From", "To", "Cc", "Bcc", "Date", "Subject", "Message-ID")
        if message.get(name) is not None
    }


def _required_string(payload: dict[str, Any], key: str) -> str:
    """Validate a required non-empty JSON string field."""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"A(z) {key} mező nem lehet üres.")
    return value.strip()


def _payload_integer(
    payload: dict[str, Any],
    key: str,
    default: int,
    *,
    maximum: int | None = None,
) -> int:
    """Read an integer from a JSON request and enforce safe pagination bounds."""
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"A {key} mező egész szám legyen.")
    if value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f"A {key} mező értéke kívül esik az engedélyezett tartományon.")
    if key == "limit" and value == 0:
        raise ValueError("A limit legalább 1 legyen.")
    return value


def _single_value(query: dict[str, list[str]], key: str) -> str:
    """Reject repeated query parameters rather than silently choosing one."""
    values = query.get(key, [""])
    if len(values) != 1:
        raise ValueError(f"A(z) {key} paraméter csak egyszer szerepelhet.")
    return values[0]


def _optional_integer(query: dict[str, list[str]], key: str) -> int | None:
    """Parse an optional non-negative query integer."""
    value = _single_value(query, key)
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"A(z) {key} paraméter egész szám legyen.") from error
    if parsed < 0:
        raise ValueError(f"A(z) {key} paraméter nem lehet negatív.")
    return parsed


def _truthy(query: dict[str, list[str]], key: str) -> bool:
    """Parse a boolean query flag using a small, explicit accepted set."""
    value = _single_value(query, key).casefold()
    if value in {"", "0", "false"}:
        return False
    if value in {"1", "true"}:
        return True
    raise ValueError(f"A(z) {key} paraméter értéke 0/1 vagy true/false legyen.")


def _positive_integer(value: str) -> int:
    """Validate an integer identifier embedded in a message URL."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("A levélazonosító egész szám legyen.") from error
    if parsed <= 0:
        raise ValueError("A levélazonosítónak pozitívnak kell lennie.")
    return parsed


def _optional_path(path: Path | None) -> str | None:
    """Expose an optional log path as JSON text."""
    return None if path is None else str(path)


def _locator_text(locator: MessageLocator) -> str:
    """Describe where a message lives inside its source file."""
    if locator.folder_path is not None and locator.item_index is not None:
        return f"{locator.folder_path} #{locator.item_index}"
    return f"bájt {locator.offset}, hossz {locator.length}"


def _internal_error_message() -> str:
    """Point the user to the log file that holds the full error details."""
    log_path = application_log_path()
    if log_path is None:
        return "Belső hiba történt."
    return f"Belső hiba történt. Részletek a naplófájlban: {log_path}"


def _optional_date(value: str, label: str) -> date | None:
    """Parse a strict YYYY-MM-DD date filter while allowing an empty value."""
    if not value:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label}: YYYY-MM-DD formátum szükséges.")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label}: érvénytelen naptári dátum.") from error


def _parse_message_date(value: str | None) -> datetime | None:
    """Normalize RFC 5322 message dates to UTC, leaving invalid dates explicitly empty."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _safe_eml_filename(index: int, subject: str) -> str:
    """Create a stable ZIP entry name without path separators or control characters."""
    safe_subject = re.sub(r"[^\w.-]+", "_", subject, flags=re.UNICODE).strip("._")
    return f"{index:05d}_{safe_subject[:80] or 'level'}.eml"
