"""Recognize an attachment's real type from its content and flag risky files."""

__author__ = "Bálint Szabolcs"

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePath


EXECUTABLE_EXTENSIONS = {
    "exe", "dll", "msi", "msp", "bat", "cmd", "com", "scr", "pif", "cpl", "ps1", "psm1",
    "vbs", "vbe", "js", "jse", "wsf", "wsh", "hta", "lnk", "jar", "reg", "sys", "drv",
    "ocx", "app", "gadget", "msc", "sh", "appx", "msix", "iso", "img", "vhd", "vhdx",
}
TEXT_EXTENSIONS = {
    "txt", "log", "xml", "json", "html", "htm", "eml", "ini", "cfg", "md", "yaml", "yml", "tsv",
}
IMAGE_KINDS = {"png", "jpeg", "gif", "bmp", "webp", "tiff"}

# Extensions and the real kinds that legitimately carry them.
EXTENSION_KINDS = {
    "pdf": {"pdf"},
    "png": {"png"}, "jpg": {"jpeg"}, "jpeg": {"jpeg"}, "jpe": {"jpeg"}, "gif": {"gif"},
    "bmp": {"bmp"}, "webp": {"webp"}, "tif": {"tiff"}, "tiff": {"tiff"},
    "zip": {"zip"}, "7z": {"7z"}, "rar": {"rar"}, "gz": {"gzip"},
    "docx": {"docx"}, "docm": {"docx"}, "dotx": {"docx"},
    "xlsx": {"xlsx"}, "xlsm": {"xlsx"}, "xltx": {"xlsx"},
    "pptx": {"pptx"}, "pptm": {"pptx"},
    "odt": {"odf"}, "ods": {"odf"}, "odp": {"odf"},
    "doc": {"ole"}, "xls": {"ole"}, "ppt": {"ole"}, "msg": {"ole"},
    "rtf": {"rtf"}, "csv": {"text"}, "eml": {"text", "email"},
    **{extension: {"text", "email"} for extension in TEXT_EXTENSIONS - {"eml"}},
}

KIND_LABELS = {
    "pdf": "PDF-dokumentum",
    "png": "PNG-kép", "jpeg": "JPEG-kép", "gif": "GIF-kép", "bmp": "BMP-kép",
    "webp": "WebP-kép", "tiff": "TIFF-kép",
    "zip": "ZIP-archívum", "7z": "7z-archívum", "rar": "RAR-archívum", "gzip": "gzip-archívum",
    "docx": "Word-dokumentum (DOCX)", "xlsx": "Excel-munkafüzet (XLSX)",
    "pptx": "PowerPoint-bemutató (PPTX)", "odf": "OpenDocument-fájl",
    "ole": "OLE-fájl (régi Office vagy Outlook MSG)", "rtf": "RTF-dokumentum",
    "exe": "Windows-program (futtatható)", "elf": "Linux-program (futtatható)",
    "email": "E-mail (RFC 5322)", "text": "Szöveg", "empty": "Üres fájl",
    "binary": "Ismeretlen bináris adat",
}


@dataclass(frozen=True)
class TypeInfo:
    """What an attachment really is and how it may be shown."""

    kind: str
    label: str
    view: str
    executable: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""


def extension(filename: str) -> str:
    """Return the lower-case last extension of a file name, without the dot."""
    suffix = PurePath(filename.replace("\\", "/")).suffix
    return suffix[1:].casefold() if suffix else ""


def is_suspicious_name(filename: str) -> bool:
    """Flag executable extensions and decoy double extensions from the name alone."""
    name = PurePath((filename or "").replace("\\", "/")).name.casefold().rstrip(". ")
    parts = name.split(".")
    if len(parts) < 2:
        return False
    if parts[-1] in EXECUTABLE_EXTENSIONS:
        return True
    return False


def _zip_kind(data: bytes) -> tuple[str, str]:
    """Tell Office Open XML and OpenDocument files from plain ZIP archives."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if "mimetype" in names:
                mimetype = archive.read("mimetype")[:100].decode("ascii", "replace")
                if "opendocument" in mimetype:
                    return "odf", mimetype
            if "[Content_Types].xml" in names:
                if any(name.startswith("word/") for name in names):
                    return "docx", ""
                if any(name.startswith("xl/") for name in names):
                    return "xlsx", ""
                if any(name.startswith("ppt/") for name in names):
                    return "pptx", ""
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError):
        # Password-protected or partly broken archives are still ZIP files.
        return "zip", ""
    return "zip", ""


def _looks_like_text(sample: bytes) -> bool:
    """Accept content as text when it decodes cleanly and has few control bytes."""
    if not sample:
        return False
    if sample.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return True
    if b"\x00" in sample:
        return False
    controls = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 12, 13))
    return controls <= len(sample) // 100


_EMAIL_HEADER = re.compile(rb"^(?:from|received|return-path|message-id|date|subject|mime-version):", re.I | re.M)


def detect(data: bytes, filename: str = "", declared_type: str = "") -> TypeInfo:
    """Recognize the real type of attachment bytes and list analyst warnings."""
    head = data[:4096]
    detail = ""
    if not data:
        kind = "empty"
    elif head.startswith(b"%PDF-"):
        kind = "pdf"
        detail = head[5:8].decode("ascii", "replace")
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = "png"
    elif head.startswith(b"\xff\xd8\xff"):
        kind = "jpeg"
    elif head.startswith((b"GIF87a", b"GIF89a")):
        kind = "gif"
    elif head.startswith(b"BM") and len(head) > 14:
        kind = "bmp"
    elif head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        kind = "webp"
    elif head.startswith((b"II*\x00", b"MM\x00*")):
        kind = "tiff"
    elif head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        kind, detail = _zip_kind(data)
    elif head.startswith(b"7z\xbc\xaf\x27\x1c"):
        kind = "7z"
    elif head.startswith(b"Rar!\x1a\x07"):
        kind = "rar"
    elif head.startswith(b"\x1f\x8b"):
        kind = "gzip"
    elif head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        kind = "ole"
    elif head.startswith(b"{\\rtf"):
        kind = "rtf"
    elif head.startswith(b"MZ"):
        kind = "exe"
    elif head.startswith(b"\x7fELF"):
        kind = "elf"
    elif _looks_like_text(head):
        kind = "email" if len(_EMAIL_HEADER.findall(head[:2048])) >= 2 else "text"
    else:
        kind = "binary"

    ext = extension(filename)
    executable = kind in {"exe", "elf"} or ext in EXECUTABLE_EXTENSIONS
    warnings: list[str] = []
    name = PurePath((filename or "").replace("\\", "/")).name
    parts = name.casefold().rstrip(". ").split(".")
    if executable:
        warnings.append("Futtatható fájl: előnézet nincs, csak adatlap és mentés.")
    if len(parts) >= 3 and parts[-1] in EXECUTABLE_EXTENSIONS and parts[-2] in EXTENSION_KINDS:
        warnings.append(f"Kettős kiterjesztés ({parts[-2]}.{parts[-1]}): a fájl ártalmatlannak álcázhatja magát.")
    expected = EXTENSION_KINDS.get(ext)
    if kind in {"exe", "elf"} and ext not in EXECUTABLE_EXTENSIONS:
        warnings.append(f"A tartalom futtatható program, de a neve .{ext or '(nincs kiterjesztés)'} – álcázott fájl.")
    elif expected is not None and kind not in expected and kind not in {"empty", "binary"}:
        warnings.append(
            f"A kiterjesztés (.{ext}) mást ígér, mint a tartalom ({KIND_LABELS.get(kind, kind)})."
        )
    declared = (declared_type or "").split(";", 1)[0].strip().casefold()
    if declared == "application/pdf" and kind != "pdf" and kind != "empty":
        warnings.append(f"A megadott típus PDF, a tartalom viszont {KIND_LABELS.get(kind, kind)}.")
    if kind == "binary" and ext in EXTENSION_KINDS and "text" not in EXTENSION_KINDS[ext]:
        warnings.append(f"A tartalom nem ismerhető fel .{ext} fájlként.")

    if executable:
        view = "none"
    elif kind in IMAGE_KINDS:
        view = "image"
    elif ext in {"csv", "tsv"} and kind == "text":
        view = "table"
    elif kind in {"text", "email"}:
        view = "text"
    else:
        view = "none"
    return TypeInfo(
        kind=kind,
        label=KIND_LABELS.get(kind, kind),
        view=view,
        executable=executable,
        warnings=tuple(dict.fromkeys(warnings)),
        detail=detail,
    )
