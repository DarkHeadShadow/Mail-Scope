"""Describe attachments and produce cached, isolated previews for the viewer."""

__author__ = "Bálint Szabolcs"

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from sources.models import Attachment

from . import render as _render  # noqa: F401 – loaded by name in the worker; keeps it in the build
from .containers import seven_zip_path
from .detect import detect
from .libreoffice import EXACT_KINDS, convert_to_pdf
from .sandbox import run_isolated


PREVIEW_LIMIT_BYTES = 200 * 1024 * 1024
_SAFE_CACHE_NAME = re.compile(r"^[a-z0-9_.-]+$")


def describe(attachments: list[Attachment], parent: str = "", parent_trail: str = "") -> list[dict[str, Any]]:
    """Return the analyst view of each attachment: real type, hash and warnings.

    Nested attachments (inside an archive or an attached e-mail) get a path
    such as ``0/2`` and a trail that names every level for the data sheet.
    """
    entries: list[dict[str, Any]] = []
    for index, attachment in enumerate(attachments):
        info = detect(attachment.data, attachment.filename, attachment.content_type)
        warnings = list(info.warnings)
        if attachment.error:
            warnings.insert(0, f"A melléklet nem olvasható a forrásból: {attachment.error}")
        view = info.view
        if attachment.error or len(attachment.data) > PREVIEW_LIMIT_BYTES:
            view = "none"
        entries.append(
            {
                "path": f"{parent}/{index}" if parent else str(index),
                "trail": f"{parent_trail} › {attachment.filename}" if parent else f"{index + 1}. melléklet",
                "container": view if view in {"archive", "email"} else None,
                "name": attachment.filename,
                "size": len(attachment.data),
                "declared_type": attachment.content_type,
                "real_kind": info.kind,
                "real_label": info.label + (f" {info.detail}" if info.detail and info.kind == "pdf" else ""),
                "view": view,
                "executable": info.executable,
                "warnings": warnings,
                "sha256": hashlib.sha256(attachment.data).hexdigest(),
                "too_large": len(attachment.data) > PREVIEW_LIMIT_BYTES,
                # LibreOffice "Pontos nézet" can draw this one (if LibreOffice is installed).
                "exact": info.kind in EXACT_KINDS and not info.executable and not attachment.error
                and len(attachment.data) <= PREVIEW_LIMIT_BYTES,
            }
        )
    return entries


def expand(entry: dict[str, Any], data: bytes, password: str | None, work_dir: Path | None) -> dict[str, Any]:
    """Unpack an archive or an attached e-mail in the isolated worker.

    Returns the container facts plus ``entries`` (described like top-level
    attachments, with nested paths) and ``data`` (their bytes, or None when a
    file was not unpacked: encrypted, over a limit, or the archive stopped).
    """
    if entry["view"] == "email":
        result = run_isolated("attachments.containers:expand_email", data, entry["real_kind"])
    elif entry["view"] == "archive":
        seven_zip = seven_zip_path() if entry["real_kind"] in {"rar", "zip"} else None
        result = run_isolated(
            "attachments.containers:expand_archive", data, entry["real_kind"], entry["name"],
            password, seven_zip, str(work_dir) if work_dir else None,
        )
    else:
        raise ValueError("Ez a melléklet nem archívum és nem csatolt levél.")
    raw = result.pop("entries")
    children = [
        Attachment(item["name"], item.get("content_type") or "", item["data"] or b"", error=None)
        for item in raw
    ]
    described = describe(children, entry["path"], entry["trail"])
    for child, item in zip(described, raw):
        if item["inner_path"] != item["name"]:
            child["trail"] = f"{entry['trail']} › {item['inner_path']}"
        child["inner_path"] = item["inner_path"]
        child["encrypted"] = item["encrypted"]
        child["compressed"] = item["compressed"]
        child["modified"] = item["modified"]
        child["unpacked"] = item["data"] is not None
        if item["data"] is None:
            # Not unpacked: the directory size is known, the content is not.
            child.update(size=item["size"], sha256="", view="none", container=None,
                         real_kind="unknown", real_label="ismeretlen (nincs kibontva)", warnings=[], exact=False)
            child["reason"] = item["error"] or "A fájl nincs kibontva."
        elif item["error"]:
            child["warnings"].insert(0, item["error"])
    result["entries"] = described
    result["data"] = [item["data"] for item in raw]
    return result


class PreviewCache:
    """Store generated previews under the case's .mailscope/cache/attachments/<sha256>/."""

    def __init__(self, case_directory: Path) -> None:
        """Keep previews inside the case's private data folder only."""
        self.root = case_directory / ".mailscope" / "cache" / "attachments"

    def _file(self, sha256: str, name: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256) or not _SAFE_CACHE_NAME.match(name):
            raise ValueError("Érvénytelen gyorsítótár-azonosító.")
        return self.root / sha256 / name

    def read(self, sha256: str, name: str) -> bytes | None:
        """Return a cached preview file, if present."""
        path = self._file(sha256, name)
        try:
            return path.read_bytes()
        except OSError:
            return None

    def write(self, sha256: str, name: str, data: bytes) -> None:
        """Store a preview file atomically."""
        path = self._file(sha256, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)


def _cached_json(cache: PreviewCache, sha256: str, name: str, produce: Any) -> dict[str, Any]:
    """Return a cached JSON result or produce, store and return it."""
    cached = cache.read(sha256, name)
    if cached is None:
        result = produce()
        cached = json.dumps(result, ensure_ascii=False).encode("utf-8")
        cache.write(sha256, name, cached)
    return json.loads(cached)


def preview(
    cache: PreviewCache, entry: dict[str, Any], data: bytes, page: int = 0, sheet: int = 0
) -> dict[str, Any]:
    """Return a JSON-ready preview, rendering it in the isolated worker when not cached."""
    view = entry["view"]
    if view == "none":
        return {"kind": "none", "reason": _no_preview_reason(entry)}
    sha256 = entry["sha256"]
    page = max(0, int(page))
    sheet = max(0, int(sheet))
    if view in {"image", "pdf"}:
        prefix = "image" if view == "image" else "pdf"
        target = "attachments.render:render_image" if view == "image" else "attachments.render:render_pdf"

        def produce() -> dict[str, Any]:
            rendered = run_isolated(target, data, page)
            cache.write(sha256, f"{prefix}-{rendered['page']}.png", rendered.pop("png"))
            return rendered

        return _cached_json(cache, sha256, f"{prefix}-{page}.json", produce)
    if view in {"text", "html"}:
        result = _cached_json(
            cache, sha256, "text.json",
            lambda: run_isolated("attachments.render:render_text", data, entry["name"]),
        )
        if view == "html":
            document_html(cache, entry, data)
            result["formatted"] = True
        return result
    if view == "email":
        def produce_email() -> dict[str, Any]:
            rendered = run_isolated("attachments.containers:render_email", data, entry["real_kind"], entry["name"])
            document = rendered.pop("document")
            if document:
                cache.write(sha256, "document.html", document.encode("utf-8"))
            rendered["formatted"] = bool(document)
            return rendered

        return _cached_json(cache, sha256, "email.json", produce_email)
    if view == "table":
        return _cached_json(
            cache, sha256, f"table-{page}.json",
            lambda: run_isolated("attachments.render:render_table", data, page),
        )
    if view == "docx":
        def produce_docx() -> dict[str, Any]:
            rendered = run_isolated("attachments.render:render_docx", data, entry["name"])
            cache.write(sha256, "document.html", rendered.pop("document").encode("utf-8"))
            return rendered

        result = _cached_json(cache, sha256, "docx.json", produce_docx)
        result["formatted"] = True
        return result
    if view in {"xlsx", "xls"}:
        return _cached_json(
            cache, sha256, f"sheet-{sheet}-{page}.json",
            lambda: run_isolated(f"attachments.render:render_{view}", data, sheet, page),
        )
    return {"kind": "none", "reason": _no_preview_reason(entry)}


def analysis(cache: PreviewCache, entry: dict[str, Any], data: bytes) -> dict[str, Any]:
    """Return document metadata and analyst signals (PDF, DOCX, XLSX, XLS)."""
    if entry["view"] not in {"pdf", "docx", "xlsx", "xls"}:
        return {}
    return _cached_json(
        cache, entry["sha256"], "analysis.json",
        lambda: run_isolated("attachments.render:analyze", data, entry["view"]),
    )


def document_html(cache: PreviewCache, entry: dict[str, Any], data: bytes) -> bytes:
    """Return the sanitized HTML document shown in the sandboxed iframe."""
    if entry["view"] not in {"docx", "html", "email"}:
        raise ValueError("Ehhez a melléklethez nincs formázott nézet.")
    cached = cache.read(entry["sha256"], "document.html")
    if cached is None:
        if entry["view"] in {"docx", "email"}:
            preview(cache, entry, data)
        else:
            rendered = run_isolated("attachments.render:render_html_document", data, entry["name"])
            cache.write(entry["sha256"], "document.html", rendered["document"].encode("utf-8"))
        cached = cache.read(entry["sha256"], "document.html") or b""
    return cached


def extracted_text(cache: PreviewCache, entry: dict[str, Any], data: bytes) -> dict[str, Any]:
    """Return extracted text: per page for PDF, whole text for documents."""
    view = entry["view"]
    if view == "pdf":
        result = analysis(cache, entry, data)
        return {"pages": result.get("pages_text", []), "text_layer": result.get("text_layer", False)}
    if view in {"docx", "text", "html", "email"}:
        result = preview(cache, entry, data)
        return {"text": result.get("text", "")}
    raise ValueError("Ehhez a melléklethez nincs kinyerhető szöveg.")


def image_png(cache: PreviewCache, entry: dict[str, Any], data: bytes, page: int = 0) -> bytes:
    """Return the re-encoded PNG of an image page or a rendered PDF page."""
    if entry["view"] not in {"image", "pdf"}:
        raise ValueError("Ehhez a melléklethez nincs képnézet.")
    meta = preview(cache, entry, data, page)
    prefix = "image" if entry["view"] == "image" else "pdf"
    png = cache.read(entry["sha256"], f"{prefix}-{meta['page']}.png")
    if png is None:
        raise ValueError("A kép előnézete nem készült el.")
    return png


_exact_locks: dict[str, threading.Lock] = {}
_exact_locks_guard = threading.Lock()


def exact_pdf(cache: PreviewCache, entry: dict[str, Any], data: bytes, soffice: str | None, work_dir: Path) -> bytes:
    """Return the LibreOffice PDF of an Office attachment, converting it once per SHA-256."""
    if not entry.get("exact"):
        raise ValueError("Ez a melléklet nem jeleníthető meg Pontos nézetben.")
    sha256 = entry["sha256"]
    with _exact_locks_guard:
        lock = _exact_locks.setdefault(sha256, threading.Lock())
    # Parallel requests (page, text) for the same file wait for one conversion.
    with lock:
        cached = cache.read(sha256, "exact.pdf")
        if cached is None:
            if not soffice:
                raise LookupError("A Pontos nézethez LibreOffice kell, de a gépen nem található.")
            cached = convert_to_pdf(soffice, data, entry["real_kind"], entry["name"], work_dir)
            cache.write(sha256, "exact.pdf", cached)
    return cached


def exact_preview(cache: PreviewCache, entry: dict[str, Any], pdf: bytes, page: int = 0) -> dict[str, Any]:
    """Render one page of the LibreOffice PDF like a PDF attachment."""
    sha256 = entry["sha256"]
    page = max(0, int(page))

    def produce() -> dict[str, Any]:
        rendered = run_isolated("attachments.render:render_pdf", pdf, page)
        cache.write(sha256, f"exact-{rendered['page']}.png", rendered.pop("png"))
        return rendered

    result = _cached_json(cache, sha256, f"exact-{page}.json", produce)
    result["exact"] = True
    return result


def exact_image(cache: PreviewCache, entry: dict[str, Any], pdf: bytes, page: int = 0) -> bytes:
    """Return the PNG of one page of the LibreOffice PDF."""
    meta = exact_preview(cache, entry, pdf, page)
    png = cache.read(entry["sha256"], f"exact-{meta['page']}.png")
    if png is None:
        raise ValueError("A kép előnézete nem készült el.")
    return png


def exact_text(cache: PreviewCache, entry: dict[str, Any], pdf: bytes) -> dict[str, Any]:
    """Return the per-page text of the LibreOffice PDF for the document search."""
    result = _cached_json(
        cache, entry["sha256"], "exact-analysis.json",
        lambda: run_isolated("attachments.render:analyze", pdf, "pdf"),
    )
    return {"pages": result.get("pages_text", []), "text_layer": result.get("text_layer", False)}


def _no_preview_reason(entry: dict[str, Any]) -> str:
    """Explain why an attachment has no preview."""
    if entry.get("too_large"):
        return "A melléklet nagyobb 200 MB-nál; csak az adatlap és a mentés érhető el."
    if entry.get("executable"):
        return "Futtatható fájl: biztonsági okból nincs előnézet, csak adatlap és mentés."
    if entry.get("reason"):
        return entry["reason"]
    if entry["warnings"] and entry["warnings"][0].startswith("A melléklet nem olvasható"):
        return entry["warnings"][0]
    name = entry["name"].casefold()
    if entry["real_kind"] in {"ole", "rtf", "pptx", "odf"} and not name.endswith((".xls", ".xlt")):
        return "Ehhez a formátumhoz LibreOffice kell („Pontos nézet”); az adatlap és a mentés elérhető."
    return f"Ehhez a típushoz ({entry['real_label']}) még nincs beépített előnézet; az adatlap és a mentés elérhető."
