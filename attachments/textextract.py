"""Extract searchable attachment text; these functions run inside the isolated worker.

One call handles every attachment of one message, including the files inside
archives and attached e-mails, so a message costs one worker process.
"""

__author__ = "Bálint Szabolcs"

import hashlib
import io
from typing import Any

from .detect import detect


TEXT_LIMIT_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 5
NO_OCR_NOTE = "szöveg nem kinyerhető (OCR nélkül)"


def extract_message(
    attachments: list[tuple[str, str, bytes]],
    known: list[str],
    seven_zip: str | None = None,
    work_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Describe every attachment at every level and extract its text.

    ``attachments`` holds (filename, content type, bytes) in source order;
    ``known`` lists SHA-256 values whose text is already stored, so their
    text is not extracted again. Paths match the attachment viewer's (``2``,
    ``2/0/3``). A file that cannot be read gets status ``error`` and does not
    stop the others.
    """
    results: list[dict[str, Any]] = []
    _walk(attachments, "", 0, set(known), seven_zip, work_dir, results)
    return results


def _walk(
    items: list[tuple[str, str, bytes | None]],
    parent: str,
    depth: int,
    known: set[str],
    seven_zip: str | None,
    work_dir: str | None,
    results: list[dict[str, Any]],
) -> None:
    for index, (name, content_type, data) in enumerate(items):
        path = f"{parent}/{index}" if parent else str(index)
        if data is None:
            continue
        info = detect(data, name, content_type)
        sha256 = hashlib.sha256(data).hexdigest()
        result: dict[str, Any] = {
            "path": path,
            "name": name,
            "content_type": content_type,
            "size": len(data),
            "sha256": sha256,
            "real_kind": info.kind,
            "suspicious": bool(info.warnings),
            "status": "known" if sha256 in known else "unsupported",
            "note": "",
            "segments": [],
        }
        results.append(result)
        if info.executable:
            continue
        if info.view == "archive":
            result["status"] = "container"
        if sha256 not in known and result["status"] != "container":
            try:
                result["status"], result["segments"], result["note"] = _text(info.view, info.kind, name, data)
            except Exception as error:  # one damaged file must not stop the message
                result["status"], result["note"] = "error", f"{type(error).__name__}: {error}"
            known.add(sha256)
        if info.view in {"archive", "email"} and depth < MAX_DEPTH:
            try:
                children = _children(info.view, info.kind, name, data, seven_zip, work_dir)
            except Exception as error:
                result["note"] = f"nem bontható ki: {type(error).__name__}: {error}"
                continue
            _walk(children, path, depth + 1, known, seven_zip, work_dir, results)


def _children(
    view: str, kind: str, name: str, data: bytes, seven_zip: str | None, work_dir: str | None
) -> list[tuple[str, str, bytes | None]]:
    """Unpack an archive (without a password) or an attached e-mail in memory."""
    from .containers import expand_archive, expand_email

    if view == "email":
        expansion = expand_email(data, kind)
    else:
        expansion = expand_archive(
            data, kind, name, None, seven_zip if kind in {"rar", "zip"} else None, work_dir
        )
    return [
        (entry["name"], entry.get("content_type") or "", entry["data"])
        for entry in expansion["entries"]
    ]


def _text(view: str, kind: str, name: str, data: bytes) -> tuple[str, list[list[Any]], str]:
    """Return (status, [[location, page, text], ...], note) for one file."""
    if view == "pdf":
        return _pdf(data)
    if view in {"xlsx", "xls"}:
        return _sheets(view, data)
    if view == "docx":
        import mammoth

        return _single(mammoth.extract_raw_text(io.BytesIO(data)).value)
    if view in {"text", "html", "table"}:
        from .render import render_text

        return _single(render_text(data, name)["text"])
    if view == "email":
        from .containers import render_email

        return _single(render_email(data, kind, name)["text"])
    return "unsupported", [], ""


def _single(text: str) -> tuple[str, list[list[Any]], str]:
    text = _limited(text, TEXT_LIMIT_BYTES)
    return ("text" if text.strip() else "empty"), ([["", None, text]] if text.strip() else []), ""


def _pdf(data: bytes) -> tuple[str, list[list[Any]], str]:
    import pypdfium2 as pdfium

    segments: list[list[Any]] = []
    budget = TEXT_LIMIT_BYTES
    document = pdfium.PdfDocument(data)
    try:
        for index in range(len(document)):
            if budget <= 0:
                break
            text = _limited(document[index].get_textpage().get_text_range(), budget)
            if text.strip():
                segments.append([f"{index + 1}. oldal", index, text])
                budget -= len(text.encode("utf-8"))
    finally:
        document.close()
    if not segments:
        return "no_ocr", [], NO_OCR_NOTE
    return "text", segments, ""


def _sheets(view: str, data: bytes) -> tuple[str, list[list[Any]], str]:
    """One segment per worksheet, hidden sheets included, values only."""
    from .render import _cell_text

    sheets: list[tuple[str, list[str]]] = []
    if view == "xlsx":
        from openpyxl import load_workbook

        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            for worksheet in book.worksheets:
                lines = []
                for row in worksheet.iter_rows(values_only=True):
                    cells = [_cell_text(value) for value in row if value is not None]
                    if cells:
                        lines.append("\t".join(cells))
                sheets.append((worksheet.title, lines))
        finally:
            book.close()
    else:
        import xlrd

        book = xlrd.open_workbook(file_contents=data, on_demand=True)
        for sheet_index in range(book.nsheets):
            worksheet = book.sheet_by_index(sheet_index)
            lines = []
            for row_index in range(worksheet.nrows):
                cells = [_cell_text(value) for value in worksheet.row_values(row_index) if value != ""]
                if cells:
                    lines.append("\t".join(cells))
            sheets.append((worksheet.name, lines))
    segments: list[list[Any]] = []
    budget = TEXT_LIMIT_BYTES
    for index, (title, lines) in enumerate(sheets):
        if budget <= 0:
            break
        text = _limited("\n".join(lines), budget)
        if text.strip():
            label = title if title.casefold().endswith("lap") else f"{title} lap"
            segments.append([label, index, text])
            budget -= len(text.encode("utf-8"))
    return ("text" if segments else "empty"), segments, ""


def _limited(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")
