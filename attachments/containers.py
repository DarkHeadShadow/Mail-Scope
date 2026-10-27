"""Expand archives (ZIP, 7z, RAR, gzip) and attached e-mails (EML, MSG).

These functions run inside the isolated worker. Nothing is written next to
the case data except RAR (and AES ZIP) work files for the installed 7-Zip,
which go into a temporary folder that the caller removes. Limits guard
against ZIP bombs: entry count, total unpacked size and compression ratio
are checked from the archive directory before anything is unpacked.
"""

__author__ = "Bálint Szabolcs"

import gzip
import io
import os
import re
import subprocess
import zipfile
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

from sources.content import message_body_text

from .render import _document, _limited, sanitize_html


MAX_ENTRIES = 10_000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_RATIO = 100
# Small, highly repetitive files compress far beyond 100:1 without being
# dangerous, so the ratio only counts once this much would be unpacked.
RATIO_MIN_BYTES = 10 * 1024 * 1024
# Unpacked children are kept in memory; beyond this budget they are listed only.
MEMORY_BUDGET_BYTES = 512 * 1024 * 1024
CHILD_LIMIT_BYTES = 200 * 1024 * 1024
LISTED_WHEN_STOPPED = 500
SEVEN_ZIP_TIMEOUT_SECONDS = 25

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _entry(inner_path: str, size: int, compressed: int | None, encrypted: bool,
           modified: datetime | tuple | None = None) -> dict[str, Any]:
    inner_path = _CONTROL.sub("�", inner_path.replace("\\", "/"))
    name = PurePosixPath(inner_path).name or inner_path
    if isinstance(modified, tuple):
        try:
            modified = datetime(*modified)
        except (TypeError, ValueError):
            modified = None
    return {
        "name": name,
        "inner_path": inner_path,
        "size": int(size or 0),
        "compressed": None if compressed is None else int(compressed),
        "encrypted": bool(encrypted),
        "modified": modified.strftime("%Y-%m-%d %H:%M") if isinstance(modified, datetime) else None,
        "data": None,
        "error": None,
    }


def _bomb_reason(entries: list[dict[str, Any]], archive_size: int) -> str | None:
    """Return why an archive must not be unpacked, judged from its directory."""
    if len(entries) > MAX_ENTRIES:
        return f"Lehetséges ZIP-bomba: az archívum {len(entries):,} bejegyzést tartalmaz (legfeljebb {MAX_ENTRIES:,} engedélyezett).".replace(",", " ")
    total = sum(entry["size"] for entry in entries)
    if total > MAX_TOTAL_BYTES:
        return f"Lehetséges ZIP-bomba: a kibontott méret {total / 1024 ** 3:.1f} GB lenne (legfeljebb 2 GB engedélyezett)."
    ratio = total / max(1, archive_size)
    if total >= RATIO_MIN_BYTES and ratio > MAX_RATIO:
        return f"Lehetséges ZIP-bomba: a tömörítési arány {ratio:.0f}:1 (legfeljebb {MAX_RATIO}:1 engedélyezett)."
    return None


def _result(fmt: str, entries: list[dict[str, Any]], archive_size: int, **extra: Any) -> dict[str, Any]:
    total = sum(entry["size"] for entry in entries)
    return {
        "container": "archive",
        "format": fmt,
        "entries": entries,
        "total_entries": len(entries),
        "total_size": total,
        "archive_size": archive_size,
        "ratio": round(total / max(1, archive_size), 1),
        "stopped": None,
        "needs_password": False,
        "password_error": False,
        "warnings": [],
        **extra,
    }


def _stop_if_bomb(result: dict[str, Any]) -> bool:
    reason = _bomb_reason(result["entries"], result["archive_size"])
    if reason:
        result["stopped"] = reason
        result["entries"] = result["entries"][:LISTED_WHEN_STOPPED]
        return True
    return False


class _Budget:
    """Track how much unpacked data is kept in memory."""

    def __init__(self) -> None:
        self.used = 0

    def admit(self, entry: dict[str, Any]) -> bool:
        if entry["size"] > CHILD_LIMIT_BYTES:
            entry["error"] = "A fájl nagyobb 200 MB-nál: nem bontjuk ki, csak az archívum menthető."
            return False
        if self.used + entry["size"] > MEMORY_BUDGET_BYTES:
            entry["error"] = "A kibontás memóriakorlátja (512 MB) miatt nem töltődött be; mentse az archívumot."
            return False
        return True

    def take(self, entry: dict[str, Any], data: bytes) -> None:
        entry["data"] = data
        entry["size"] = len(data)
        self.used += len(data)


def _zip_name(info: zipfile.ZipInfo) -> str:
    """Decode non-UTF-8 ZIP names as the Central European OEM code page (Windows Explorer)."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp852")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _expand_zip(data: bytes, password: str | None, seven_zip: str | None, work_dir: str | None) -> dict[str, Any]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError(f"sérült ZIP-archívum ({error})") from error
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        entries = [
            _entry(_zip_name(info), info.file_size, info.compress_size, bool(info.flag_bits & 0x1), info.date_time)
            for info in infos
        ]
        result = _result("ZIP", entries, len(data))
        if _stop_if_bomb(result):
            return result
        encrypted = any(entry["encrypted"] for entry in entries)
        if encrypted and not password:
            result["needs_password"] = True
        budget = _Budget()
        aes = False
        for info, entry in zip(infos, entries):
            if entry["encrypted"] and not password:
                entry["error"] = "Jelszóval védett: adja meg a jelszót a kibontáshoz."
                continue
            if not budget.admit(entry):
                continue
            try:
                with archive.open(info, pwd=password.encode("utf-8") if entry["encrypted"] and password else None) as handle:
                    budget.take(entry, handle.read(CHILD_LIMIT_BYTES + 1))
            except RuntimeError as error:
                if "password" in str(error).casefold():
                    result["password_error"] = True
                    entry["error"] = "Hibás jelszó."
                else:
                    entry["error"] = f"Nem bontható ki: {error}"
            except NotImplementedError:
                aes = True
                entry["error"] = "Ez a tömörítési vagy titkosítási mód (pl. AES) csak telepített 7-Zip-pel bontható ki."
            except (zipfile.BadZipFile, OSError, EOFError, ValueError) as error:
                entry["error"] = f"Nem bontható ki: {error}"
    if aes and seven_zip and work_dir:
        return _expand_with_seven_zip(data, "ZIP", password, seven_zip, work_dir)
    if aes:
        result["warnings"].append("Az archívum egyes fájljai AES-titkosításúak vagy ritka tömörítésűek: ezekhez 7-Zip kell.")
    return result


def _expand_7z(data: bytes, password: str | None) -> dict[str, Any]:
    import py7zr
    from py7zr.exceptions import PasswordRequired
    from py7zr.io import BytesIOFactory

    try:
        archive = py7zr.SevenZipFile(io.BytesIO(data), password=password or None, max_extract_size=MAX_TOTAL_BYTES)
    except PasswordRequired:
        # Encrypted headers: not even the file list is readable without the password.
        result = _result("7z", [], len(data), needs_password=True)
        result["warnings"].append("A 7z-archívum fájllistája is titkosított: a tartalom a jelszó megadása után látszik.")
        return result
    except py7zr.Bad7zFile as error:
        raise ValueError(f"sérült 7z-archívum ({error})") from error
    except Exception as error:  # wrong password for encrypted headers, damaged headers
        if password:
            return _result("7z", [], len(data), needs_password=True, password_error=True)
        raise ValueError(f"a 7z-archívum nem olvasható ({type(error).__name__}: {error})") from error
    with archive:
        encrypted = archive.needs_password()
        infos = [info for info in archive.list() if not info.is_directory]
        entries = [
            _entry(info.filename, info.uncompressed, info.compressed, encrypted, info.creationtime)
            for info in infos
        ]
        result = _result("7z", entries, len(data))
        if _stop_if_bomb(result):
            return result
        if encrypted and not password:
            result["needs_password"] = True
            for entry in entries:
                entry["error"] = "Jelszóval védett: adja meg a jelszót a kibontáshoz."
            return result
        # The directory sizes decide in advance which files fit the memory budget.
        planned = _Budget()
        admitted = []
        for info, entry in zip(infos, entries):
            if planned.admit(entry):
                admitted.append(info.filename)
                planned.used += entry["size"]
        budget = _Budget()
        factory = BytesIOFactory(CHILD_LIMIT_BYTES + 1)
        try:
            archive.extract(targets=admitted, factory=factory)
        except Exception as error:
            if encrypted:
                result["password_error"] = True
                for entry in entries:
                    entry["error"] = "Hibás jelszó."
                return result
            raise ValueError(f"a 7z-archívum nem bontható ki ({type(error).__name__}: {error})") from error
        for info, entry in zip(infos, entries):
            if info.filename not in admitted:
                continue
            try:
                product = factory.get(info.filename)
            except KeyError:
                entry["error"] = "Nem bontható ki."
                continue
            product.seek(0)
            budget.take(entry, product.read())
    return result


def _expand_gzip(data: bytes, name: str) -> dict[str, Any]:
    inner = name[:-3] if name.casefold().endswith(".gz") else (name[:-4] + ".tar" if name.casefold().endswith(".tgz") else name + ".kibontva")
    declared = int.from_bytes(data[-4:], "little") if len(data) >= 4 else 0
    entry = _entry(inner, declared, len(data), False)
    result = _result("gzip", [entry], len(data))
    if _stop_if_bomb(result):
        return result
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
            unpacked = handle.read(CHILD_LIMIT_BYTES + 1)
    except (OSError, EOFError) as error:
        raise ValueError(f"sérült gzip-fájl ({error})") from error
    if len(unpacked) > CHILD_LIMIT_BYTES:
        entry["error"] = "A fájl nagyobb 200 MB-nál: nem bontjuk ki, csak az archívum menthető."
    else:
        entry["data"] = unpacked
        entry["size"] = len(unpacked)
    return result


def _seven_zip_run(arguments: list[str], timeout: float = SEVEN_ZIP_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Run the installed 7-Zip without a window, without input and with a time limit."""
    return subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )


def parse_seven_zip_listing(output: str) -> list[dict[str, Any]]:
    """Parse ``7z l -slt`` output into entries (directories skipped)."""
    entries: list[dict[str, Any]] = []
    _, _, body = output.partition("----------")
    for block in re.split(r"\r?\n\s*\r?\n", body):
        fields = {}
        for line in block.splitlines():
            key, separator, value = line.partition(" = ")
            if separator:
                fields[key.strip()] = value.strip()
        if "Path" not in fields or fields.get("Folder") == "+" or "D" in fields.get("Attributes", "")[:1]:
            continue
        modified = None
        try:
            modified = datetime.strptime(fields.get("Modified", "")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        packed = fields.get("Packed Size", "")
        entries.append(_entry(
            fields["Path"],
            int(fields.get("Size") or 0),
            int(packed) if packed.isdigit() else None,
            fields.get("Encrypted") == "+",
            modified,
        ))
    return entries


def _expand_with_seven_zip(data: bytes, fmt: str, password: str | None, seven_zip: str, work_dir: str) -> dict[str, Any]:
    """List and unpack an archive with the installed 7-Zip (RAR, AES ZIP)."""
    directory = Path(work_dir)
    directory.mkdir(parents=True, exist_ok=True)
    archive_path = directory / "archive.bin"
    archive_path.write_bytes(data)
    password_switch = [f"-p{password}"] if password else []
    try:
        listing = _seven_zip_run([seven_zip, "l", "-slt", "-sccUTF-8", *password_switch, "--", str(archive_path)])
        output = listing.stdout.decode("utf-8", errors="replace")
        errors = listing.stderr.decode("utf-8", errors="replace")
        if listing.returncode != 0 and "----------" not in output:
            if re.search(r"(?i)wrong password|encrypted|password", errors + output):
                return _result(fmt, [], len(data), needs_password=True, password_error=bool(password))
            raise ValueError(f"a 7-Zip nem tudta megnyitni az archívumot ({errors.strip()[:200]})")
        entries = parse_seven_zip_listing(output)
        result = _result(fmt, entries, len(data))
        if _stop_if_bomb(result):
            return result
        if any(entry["encrypted"] for entry in entries) and not password:
            result["needs_password"] = True
        budget = _Budget()
        for entry in entries:
            if entry["encrypted"] and not password:
                entry["error"] = "Jelszóval védett: adja meg a jelszót a kibontáshoz."
                continue
            if not budget.admit(entry):
                continue
            unpacked = _seven_zip_run(
                [seven_zip, "e", "-so", "-spd", "-sccUTF-8", *password_switch, "--", str(archive_path), entry["inner_path"]]
            )
            if unpacked.returncode != 0:
                message = unpacked.stderr.decode("utf-8", errors="replace")
                if re.search(r"(?i)wrong password", message):
                    result["password_error"] = True
                    entry["error"] = "Hibás jelszó."
                else:
                    entry["error"] = f"Nem bontható ki: {message.strip()[:200]}"
                continue
            budget.take(entry, unpacked.stdout[:CHILD_LIMIT_BYTES + 1])
        return result
    except subprocess.TimeoutExpired as error:
        raise ValueError("a 7-Zip nem végzett az időkorláton belül") from error
    finally:
        try:
            archive_path.unlink()
        except OSError:
            pass


def expand_archive(data: bytes, kind: str, name: str, password: str | None = None,
                   seven_zip: str | None = None, work_dir: str | None = None) -> dict[str, Any]:
    """List an archive and unpack its files into memory, within the limits."""
    if kind == "zip":
        return _expand_zip(data, password, seven_zip, work_dir)
    if kind == "7z":
        return _expand_7z(data, password)
    if kind == "gzip":
        return _expand_gzip(data, name)
    if kind == "rar":
        if not seven_zip or not work_dir:
            result = _result("RAR", [], len(data))
            result["stopped"] = "RAR-archívum kibontásához telepített 7-Zip (7z.exe) kell; az archívum adatlapja és mentése elérhető."
            return result
        return _expand_with_seven_zip(data, "RAR", password, seven_zip, work_dir)
    raise ValueError(f"nem archívum: {kind}")


# --- Attached e-mails ---------------------------------------------------------

EMAIL_HEADERS = ("From", "To", "Cc", "Bcc", "Reply-To", "Date", "Subject", "Message-ID", "In-Reply-To")


def parse_email(data: bytes, kind: str) -> EmailMessage:
    """Parse EML bytes, or convert MSG bytes, into an EmailMessage."""
    if kind == "msg":
        from .msg import read_msg

        return read_msg(data)
    return BytesParser(policy=policy.default).parsebytes(data)


def _direct_attachments(message: EmailMessage) -> list[tuple[str, str, bytes | None, str | None]]:
    """Return the message's own attachments; those of attached messages stay inside them."""
    found: list[tuple[str, str, bytes | None, str | None]] = []

    def visit(part: Any, top: bool) -> None:
        content_type = part.get_content_type()
        if content_type == "message/rfc822" and not top:
            inner = part.get_payload(0) if part.is_multipart() else None
            filename = part.get_filename() or f"{(inner.get('Subject') if inner is not None else None) or 'csatolt-level'}.eml"
            try:
                found.append((filename, content_type, inner.as_bytes(policy=policy.SMTP) if inner is not None else b"", None))
            except Exception as error:
                found.append((filename, content_type, None, f"{type(error).__name__}: {error}"))
            return
        if part.is_multipart():
            for sub in part.get_payload():
                visit(sub, False)
            return
        if top:
            return
        if part.get_content_disposition() == "attachment" or part.get_filename():
            filename = part.get_filename() or f"melleklet-{len(found) + 1}"
            try:
                found.append((filename, content_type, part.get_payload(decode=True) or b"", None))
            except Exception as error:
                found.append((filename, content_type, None, f"{type(error).__name__}: {error}"))

    visit(message, True)
    return found


def expand_email(data: bytes, kind: str) -> dict[str, Any]:
    """Return the attachments of an attached EML or MSG message, in memory."""
    message = parse_email(data, kind)
    entries = []
    for filename, content_type, content, error in _direct_attachments(message):
        entry = _entry(filename, len(content or b""), None, False)
        entry["name"] = _CONTROL.sub("�", filename)
        entry["inner_path"] = entry["name"]
        entry["content_type"] = content_type
        entry["data"] = content
        entry["error"] = error
        entries.append(entry)
    return {
        "container": "email",
        "format": "MSG" if kind == "msg" else "EML",
        "entries": entries,
        "total_entries": len(entries),
        "stopped": None,
        "needs_password": False,
        "password_error": False,
        "warnings": [],
    }


def render_email(data: bytes, kind: str, filename: str) -> dict[str, Any]:
    """Return headers, body text and, when present, a sanitized HTML body document."""
    message = parse_email(data, kind)
    headers = [[name, str(message.get(name))] for name in EMAIL_HEADERS if message.get(name)]
    try:
        text = message_body_text(message)
    except ValueError as error:
        text = f"(A levél törzse nem dekódolható: {error})"
    text, truncated = _limited(text)
    html_part = message.get_body(preferencelist=("html",))
    document = None
    if html_part is not None:
        try:
            html = html_part.get_content()
            if isinstance(html, str) and html.strip():
                document = _document(sanitize_html(html, keep_styles=True), filename)
        except (LookupError, UnicodeError):
            document = None
    return {
        "kind": "email",
        "format": "MSG" if kind == "msg" else "EML",
        "headers": headers,
        "text": text,
        "truncated": truncated,
        "document": document,
        "attachments": [name for name, *_ in _direct_attachments(message)],
        "converted": kind == "msg",
    }


def seven_zip_path() -> str | None:
    """Find an installed 7-Zip command-line program."""
    import shutil

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "7-Zip" / "7z.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("7z") or shutil.which("7z.exe")
    return found
