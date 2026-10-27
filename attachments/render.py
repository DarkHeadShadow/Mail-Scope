"""Attachment preview renderers; these functions run inside the isolated worker."""

__author__ = "Bálint Szabolcs"

import csv
import io
from typing import Any

from sources.content import html_to_text


TEXT_LIMIT_BYTES = 5 * 1024 * 1024
TABLE_PAGE_ROWS = 2000
IMAGE_MAX_SIDE = 4096


def _decode(data: bytes) -> tuple[str, str]:
    """Decode text with BOM detection, strict UTF-8, then Windows-1250 and Latin-1."""
    for bom, codec in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(codec, errors="replace"), codec.upper()
    for codec, label in (("utf-8", "UTF-8"), ("cp1250", "Windows-1250")):
        try:
            return data.decode(codec), label
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "ISO-8859-1"


def render_text(data: bytes, filename: str) -> dict[str, Any]:
    """Return text (and for HTML, the cleaned text plus the source) up to 5 MB."""
    truncated = len(data) > TEXT_LIMIT_BYTES
    text, encoding = _decode(data[:TEXT_LIMIT_BYTES])
    lowered = filename.casefold()
    is_html = lowered.endswith((".html", ".htm")) or text.lstrip()[:200].casefold().startswith(
        ("<!doctype html", "<html")
    )
    result: dict[str, Any] = {
        "kind": "text",
        "encoding": encoding,
        "truncated": truncated,
        "text": html_to_text(text) if is_html else text,
    }
    if is_html:
        result["source"] = text
    return result


def render_table(data: bytes, page: int = 0) -> dict[str, Any]:
    """Return one page of up to 2000 CSV rows with the detected delimiter."""
    text, encoding = _decode(data[:TEXT_LIMIT_BYTES * 4])
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    start = max(0, page) * TABLE_PAGE_ROWS
    return {
        "kind": "table",
        "encoding": encoding,
        "delimiter": {"\t": "tabulátor"}.get(delimiter, delimiter),
        "total_rows": len(rows),
        "page": max(0, page),
        "page_size": TABLE_PAGE_ROWS,
        "rows": rows[start:start + TABLE_PAGE_ROWS],
    }


def render_image(data: bytes, page: int = 0) -> dict[str, Any]:
    """Re-encode one image page as PNG and read EXIF and GPS data.

    The browser only receives this re-encoded PNG, never the original bytes.
    Pillow's decompression-bomb guard stays active.
    """
    from PIL import ExifTags, Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        source_format = image.format or ""
        pages = getattr(image, "n_frames", 1)
        image.seek(min(max(0, page), pages - 1))
        width, height = image.size
        exif = image.getexif()
        details: dict[str, str] = {}
        for tag, value in exif.items():
            name = ExifTags.TAGS.get(tag)
            if name in {"Make", "Model", "DateTime", "Software", "Artist", "Copyright", "ImageDescription"}:
                details[name] = str(value).strip("\x00 ")
        try:
            sub = exif.get_ifd(ExifTags.IFD.Exif)
            for tag in (0x9003, 0x9004):  # DateTimeOriginal, DateTimeDigitized
                if tag in sub:
                    details[ExifTags.TAGS.get(tag, str(tag))] = str(sub[tag]).strip("\x00 ")
        except Exception:
            pass
        gps = _gps_text(exif)
        frame = ImageOps.exif_transpose(image.copy())
        if frame.mode not in {"RGB", "RGBA", "L", "LA"}:
            frame = frame.convert("RGBA" if "A" in frame.getbands() else "RGB")
        frame.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
        output = io.BytesIO()
        frame.save(output, format="PNG")
    return {
        "kind": "image",
        "png": output.getvalue(),
        "width": width,
        "height": height,
        "pages": pages,
        "page": min(max(0, page), pages - 1),
        "format": Image.MIME.get(source_format, source_format),
        "exif": details,
        "gps": gps,
    }


def _gps_text(exif: Any) -> str | None:
    """Render EXIF GPS coordinates as plain decimal text (no map is shown)."""
    try:
        from PIL import ExifTags

        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    except Exception:
        return None
    if not gps or 2 not in gps or 4 not in gps:
        return None

    def decimal(values: Any, reference: str) -> float:
        degrees, minutes, seconds = (float(value) for value in values)
        result = degrees + minutes / 60 + seconds / 3600
        return -result if reference in {"S", "W"} else result

    try:
        latitude = decimal(gps[2], str(gps.get(1, "N")))
        longitude = decimal(gps[4], str(gps.get(3, "E")))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return f"{latitude:.6f}, {longitude:.6f}"
