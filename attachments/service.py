"""Describe attachments and produce cached, isolated previews for the viewer."""

__author__ = "Bálint Szabolcs"

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sources.models import Attachment

from . import render as _render  # noqa: F401 – loaded by name in the worker; keeps it in the build
from .detect import detect
from .sandbox import run_isolated


PREVIEW_LIMIT_BYTES = 200 * 1024 * 1024
_SAFE_CACHE_NAME = re.compile(r"^[a-z0-9_.-]+$")


def describe(attachments: list[Attachment]) -> list[dict[str, Any]]:
    """Return the analyst view of each attachment: real type, hash and warnings."""
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
                "path": str(index),
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
            }
        )
    return entries


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


def preview(cache: PreviewCache, entry: dict[str, Any], data: bytes, page: int = 0) -> dict[str, Any]:
    """Return a JSON-ready preview, rendering it in the isolated worker when not cached."""
    view = entry["view"]
    if view == "none":
        return {"kind": "none", "reason": _no_preview_reason(entry)}
    sha256 = entry["sha256"]
    page = max(0, int(page))
    if view == "image":
        meta_name = f"image-{page}.json"
        cached = cache.read(sha256, meta_name)
        if cached is None:
            rendered = run_isolated("attachments.render:render_image", data, page)
            cache.write(sha256, f"image-{rendered['page']}.png", rendered.pop("png"))
            cached = json.dumps(rendered, ensure_ascii=False).encode("utf-8")
            cache.write(sha256, meta_name, cached)
        return json.loads(cached)
    name = "text.json" if view == "text" else f"table-{page}.json"
    cached = cache.read(sha256, name)
    if cached is None:
        if view == "text":
            rendered = run_isolated("attachments.render:render_text", data, entry["name"])
        else:
            rendered = run_isolated("attachments.render:render_table", data, page)
        cached = json.dumps(rendered, ensure_ascii=False).encode("utf-8")
        cache.write(sha256, name, cached)
    return json.loads(cached)


def image_png(cache: PreviewCache, entry: dict[str, Any], data: bytes, page: int = 0) -> bytes:
    """Return the re-encoded PNG of an image page, rendering it if necessary."""
    if entry["view"] != "image":
        raise ValueError("Ehhez a melléklethez nincs képnézet.")
    meta = preview(cache, entry, data, page)
    png = cache.read(entry["sha256"], f"image-{meta['page']}.png")
    if png is None:
        raise ValueError("A kép előnézete nem készült el.")
    return png


def _no_preview_reason(entry: dict[str, Any]) -> str:
    """Explain why an attachment has no preview."""
    if entry.get("too_large"):
        return "A melléklet nagyobb 200 MB-nál; csak az adatlap és a mentés érhető el."
    if entry.get("executable"):
        return "Futtatható fájl: biztonsági okból nincs előnézet, csak adatlap és mentés."
    if entry["warnings"] and entry["warnings"][0].startswith("A melléklet nem olvasható"):
        return entry["warnings"][0]
    return f"Ehhez a típushoz ({entry['real_label']}) még nincs beépített előnézet; az adatlap és a mentés elérhető."
