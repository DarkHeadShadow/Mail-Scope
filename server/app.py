"""Loopback-only API and static UI for case and archive browsing."""

__author__ = "Bálint Szabolcs"

import json
import logging
import mimetypes
import os
import sqlite3
import threading
from dataclasses import asdict
from email.message import EmailMessage
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from indexer import IndexDatabase, IndexingJob, IndexingService
from sources.content import message_body_text
from sources.discover import DiscoveredSource, discover_sources, sha256_file
from sources.models import MessageLocator
from sources.reader_factory import open_source


logger = logging.getLogger(__name__)
_UI_DIRECTORY = Path(__file__).resolve().parent.parent / "ui"
_MAX_REQUEST_BYTES = 1_000_000
_MAX_QUERY_LENGTH = 512


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
        self._verified_sources: dict[
            str, tuple[tuple[int, int, int, int, int], str]
        ] = {}

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
            self.indexing_service = IndexingService(self.database)
            self._discovered.clear()
            self._verified_sources.clear()
            self.job = None
            return self.status()

    def discover(self, path: str) -> list[dict[str, Any]]:
        """Scan one selected file or folder and retain its detected sources."""
        with self._lock:
            self._require_case()
            sources = discover_sources(path)
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

    def status(self) -> dict[str, Any]:
        """Return case, source, message-count, and job progress information."""
        with self._lock:
            if self.database is None or self.case_directory is None:
                return {"case": None, "sources": [], "message_count": 0, "job": None}
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

    def open_message(self, message_id: int) -> dict[str, Any]:
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
            if not source_path.is_file():
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "Az eredeti forrásfájl nem érhető el; a levél nem nyitható meg.",
                )
            expected_hash = str(occurrence["discovered_sha256"])
            try:
                actual_hash = self._verified_source_hash(source_path)
            except OSError as error:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    f"Az eredeti forrásfájl nem ellenőrizhető: {error}",
                ) from error
            if actual_hash != expected_hash:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "A forrásfájl az indexelés óta megváltozott; a forenzikus megnyitás leállt.",
                )
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
                raw_message = open_source(source).open_message(locator)
                body = message_body_text(raw_message)
            except (OSError, ValueError, IndexError, RuntimeError) as error:
                logger.exception("Unable to open indexed message %s", message_id)
                raise ApiError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    f"A levél nem olvasható az eredeti forrásból: {error}",
                ) from error
            return {
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

    def _verified_source_hash(self, source_path: Path) -> str:
        """Hash each unchanged source once, invalidating the cache on file metadata changes."""
        path_key = str(source_path.resolve())
        before = _file_signature(source_path.stat())
        cached = self._verified_sources.get(path_key)
        if cached is not None and cached[0] == before:
            return cached[1]
        actual_hash = sha256_file(source_path)
        after = _file_signature(source_path.stat())
        if before != after:
            raise OSError("A forrásfájl az ellenőrzés közben megváltozott.")
        self._verified_sources[path_key] = (after, actual_hash)
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
            if request.path in {"/", "/index.html", "/app.js", "/styles.css"}:
                self._serve_ui(request.path)
            elif request.path == "/api/status":
                self._send_json(HTTPStatus.OK, self.server.application.status())
            elif request.path == "/api/folders":
                database = self.server.application._require_case()
                self._send_json(
                    HTTPStatus.OK,
                    [_row_json(row) for row in database.folders()],
                )
            elif request.path == "/api/archive":
                self._send_archive(parse_qs(request.query))
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
                "Belső hiba történt. Részletek a helyi alkalmazásnaplóban.",
            )

    def do_POST(self) -> None:
        """Handle case selection, discovery, indexing, and cancellation."""
        if not self._valid_host() or not self._valid_origin():
            self._send_error(HTTPStatus.FORBIDDEN, "A kérés nem a Mail-Scope helyi felületéről érkezett.")
            return
        try:
            payload = self._read_json()
            path = urlsplit(self.path).path
            if path == "/api/case":
                response = self.server.application.select_case(_required_string(payload, "path"))
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
                "Belső hiba történt. Részletek a helyi alkalmazásnaplóban.",
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
        """Serve only the three known local UI assets with restrictive headers."""
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
        super().__init__(server_address, _RequestHandler)


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


def _file_signature(file_stat: os.stat_result) -> tuple[int, int, int, int, int]:
    """Build a local file identity from size and available change metadata."""
    return (
        int(file_stat.st_dev),
        int(file_stat.st_ino),
        int(file_stat.st_size),
        int(file_stat.st_mtime_ns),
        int(file_stat.st_ctime_ns),
    )
