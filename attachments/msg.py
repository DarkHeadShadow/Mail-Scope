"""Read Outlook MSG files (OLE compound files with MAPI properties) as e-mail.

Only the properties an analyst needs are read: subject, sender, recipients,
dates, Message-ID, transport headers, plain and HTML body, and attachments,
including attached Outlook items. The result is a standard EmailMessage, so
MSG and EML attachments share one viewer. Runs inside the isolated worker.
"""

__author__ = "Bálint Szabolcs"

import io
import struct
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesHeaderParser
from email.utils import format_datetime, formataddr
from typing import Any

from sources.content import decode_text_bytes


MAX_EMBEDDING_DEPTH = 5

PT_LONG = 0x0003
PT_SYSTIME = 0x0040
ATTACH_EMBEDDED_MSG = 5
RECIPIENT_HEADERS = {1: "To", 2: "Cc", 3: "Bcc"}


class _Storage:
    """One MSG storage (the message, a recipient, an attachment or an embedded item)."""

    def __init__(self, ole: Any, prefix: str, header_size: int, codepage: str = "cp1252") -> None:
        self.ole = ole
        self.prefix = prefix
        self.codepage = codepage
        self.fixed = self._fixed_properties(header_size)

    def _path(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def _fixed_properties(self, header_size: int) -> dict[int, tuple[int, bytes]]:
        """Read fixed-size property values from the __properties_version1.0 stream."""
        path = self._path("__properties_version1.0")
        if not self.ole.exists(path):
            return {}
        data = self.ole.openstream(path).read()
        values: dict[int, tuple[int, bytes]] = {}
        for offset in range(header_size, len(data) - 15, 16):
            prop_type, prop_id = struct.unpack_from("<HH", data, offset)
            values[prop_id] = (prop_type, data[offset + 8:offset + 16])
        return values

    def long(self, prop_id: int) -> int | None:
        entry = self.fixed.get(prop_id)
        if entry is None or entry[0] != PT_LONG:
            return None
        return struct.unpack("<i", entry[1][:4])[0]

    def time(self, prop_id: int) -> datetime | None:
        entry = self.fixed.get(prop_id)
        if entry is None or entry[0] != PT_SYSTIME:
            return None
        ticks = struct.unpack("<Q", entry[1])[0]
        if not ticks:
            return None
        try:
            return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ticks // 10)
        except OverflowError:
            return None

    def binary(self, prop_id: int) -> bytes | None:
        path = self._path(f"__substg1.0_{prop_id:04X}0102")
        if self.ole.exists(path):
            return self.ole.openstream(path).read()
        return None

    def text(self, prop_id: int) -> str:
        """Return a string property stored as Unicode (001F) or 8-bit (001E)."""
        unicode_path = self._path(f"__substg1.0_{prop_id:04X}001F")
        if self.ole.exists(unicode_path):
            return self.ole.openstream(unicode_path).read().decode("utf-16-le", errors="replace").rstrip("\x00")
        ansi_path = self._path(f"__substg1.0_{prop_id:04X}001E")
        if self.ole.exists(ansi_path):
            return self.ole.openstream(ansi_path).read().decode(self.codepage, errors="replace").rstrip("\x00")
        return ""

    def children(self, prefix: str) -> list[str]:
        """Return the names of direct sub-storages starting with ``prefix``, in order."""
        depth = self.prefix.count("/")
        names = set()
        for entry in self.ole.listdir(streams=False, storages=True):
            if len(entry) == depth + 1 and "/".join(entry[:depth]) + ("/" if depth else "") == self.prefix:
                if entry[depth].startswith(prefix):
                    names.add(entry[depth])
        return sorted(names)


def _codepage(storage: _Storage) -> str:
    """Return the Python codec for 8-bit strings (PR_MESSAGE_CODEPAGE, PR_INTERNET_CPID)."""
    for prop_id in (0x3FFD, 0x3FDE):
        value = storage.long(prop_id)
        if value:
            name = "utf-8" if value == 65001 else f"cp{value}"
            try:
                "".encode(name)
                return name
            except LookupError:
                continue
    return "cp1252"


def _clean_header(value: str) -> str:
    """Make a property value safe for one header line."""
    return " ".join((value or "").replace("\x00", "").split())


def _set_header(message: EmailMessage, name: str, value: str) -> None:
    value = _clean_header(value)
    if not value:
        return
    try:
        message[name] = value
    except (ValueError, TypeError, IndexError):
        # A value the address parser rejects is still kept, unparsed.
        message[name] = value.replace(",", " ")


def _address(name: str, address: str) -> str:
    name, address = _clean_header(name), _clean_header(address)
    if address and "@" in address:
        return formataddr((name, address)) if name and name != address else address
    return name or address


def _read_message(ole: Any, prefix: str, header_size: int, depth: int) -> EmailMessage:
    """Build an EmailMessage from one MSG message storage."""
    storage = _Storage(ole, prefix, header_size)
    storage.codepage = _codepage(storage)
    message = EmailMessage(policy=policy.default)

    transport = storage.text(0x007D)
    original = BytesHeaderParser(policy=policy.default).parsebytes(transport.encode("utf-8", "replace")) if transport else None

    recipients: dict[str, list[str]] = {"To": [], "Cc": [], "Bcc": []}
    for name in storage.children("__recip_version1.0_#"):
        recipient = _Storage(ole, f"{prefix}{name}/", 8, storage.codepage)
        header = RECIPIENT_HEADERS.get(recipient.long(0x0C15) or 1, "To")
        smtp = recipient.text(0x39FE) or recipient.text(0x3003)
        recipients[header].append(_address(recipient.text(0x3001), smtp))

    sent = storage.time(0x0039) or storage.time(0x0E06)
    values = {
        "From": _address(storage.text(0x0C1A) or storage.text(0x0042), storage.text(0x5D01) or storage.text(0x0C1F) or storage.text(0x0065)),
        "To": ", ".join(item for item in recipients["To"] if item) or storage.text(0x0E04),
        "Cc": ", ".join(item for item in recipients["Cc"] if item) or storage.text(0x0E03),
        "Bcc": ", ".join(item for item in recipients["Bcc"] if item) or storage.text(0x0E02),
        "Date": format_datetime(sent) if sent else "",
        "Subject": storage.text(0x0037),
        "Message-ID": storage.text(0x1035),
        "In-Reply-To": storage.text(0x1042),
    }
    for name, value in values.items():
        if original is not None and original.get(name):
            value = str(original.get(name))
        _set_header(message, name, value)
    _set_header(message, "X-Mail-Scope-Converted", "Outlook MSG (MAPI-tulajdonságokból összeállítva)")

    body = storage.text(0x1000)
    html_bytes = storage.binary(0x1013)
    html = decode_text_bytes(html_bytes, storage.codepage) if html_bytes else storage.text(0x1013)
    message.set_content(body or "", subtype="plain")
    if html.strip():
        message.add_alternative(html, subtype="html")

    for name in storage.children("__attach_version1.0_#"):
        attachment = _Storage(ole, f"{prefix}{name}/", 8, storage.codepage)
        filename = attachment.text(0x3707) or attachment.text(0x3704) or attachment.text(0x3001) or name
        if attachment.long(0x3705) == ATTACH_EMBEDDED_MSG:
            inner_prefix = f"{prefix}{name}/__substg1.0_3701000D/"
            if depth >= MAX_EMBEDDING_DEPTH or not ole.exists(inner_prefix.rstrip("/")):
                continue
            inner = _read_message(ole, inner_prefix, 24, depth + 1)
            display = attachment.text(0x3001) or str(inner.get("Subject") or "csatolt-level")
            message.add_attachment(inner, filename=f"{display}.eml")
            continue
        data = attachment.binary(0x3701)
        if data is None:
            # OLE objects and references are not file attachments.
            continue
        mime = (attachment.text(0x370E) or "application/octet-stream").casefold()
        maintype, _, subtype = mime.partition("/")
        if maintype in {"text", "multipart", "message"} or not subtype:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return message


def read_msg(data: bytes) -> EmailMessage:
    """Parse MSG bytes into an EmailMessage with its attachments."""
    import olefile

    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        return _read_message(ole, "", 32, 0)
