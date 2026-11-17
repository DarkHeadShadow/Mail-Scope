"""Read Outlook TNEF packages (winmail.dat) as e-mail.

Outlook wraps a message's rich-text body and its attachments into one
``winmail.dat`` (application/ms-tnef) attachment when it sends in rich-text
format. This reader follows [MS-OXTNEF]: it walks the attribute stream, reads
the MAPI properties, and returns a standard EmailMessage with the package's
body and attachments, so TNEF, MSG and EML attachments share one viewer. The
compressed RTF body ([MS-OXRTFCP]) is unpacked, and HTML wrapped in RTF
([MS-OXRTFEX]) is restored. Runs inside the isolated worker.
"""

__author__ = "Bálint Szabolcs"

import re
import struct
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

from sources.content import decode_text_bytes

from .msg import MAX_EMBEDDING_DEPTH, _address, _set_header


SIGNATURE = b"\x78\x9f\x3e\x22"

LEVEL_MESSAGE = 1
LEVEL_ATTACHMENT = 2

# Attribute IDs (the low word of the attribute tag).
ATT_SUBJECT = 0x8004
ATT_DATE_SENT = 0x8005
ATT_BODY = 0x800C
ATT_ATTACH_DATA = 0x800F
ATT_ATTACH_TITLE = 0x8010
ATT_ATTACH_RENDDATA = 0x9002
ATT_MAPI_PROPS = 0x9003
ATT_ATTACHMENT = 0x9005
ATT_OEM_CODEPAGE = 0x9007

PT_SHORT = 0x0002
PT_LONG = 0x0003
PT_BOOLEAN = 0x000B
PT_OBJECT = 0x000D
PT_STRING8 = 0x001E
PT_UNICODE = 0x001F
PT_SYSTIME = 0x0040
PT_CLSID = 0x0048
PT_BINARY = 0x0102
MV_FLAG = 0x1000
_FIXED_SIZES = {
    PT_SHORT: 4, PT_LONG: 4, 0x0004: 4, 0x0005: 8, 0x0006: 8, 0x0007: 8, 0x000A: 4,
    PT_BOOLEAN: 4, 0x0014: 8, PT_SYSTIME: 8, PT_CLSID: 16,
}
_VARIABLE_TYPES = {PT_OBJECT, PT_STRING8, PT_UNICODE, PT_BINARY}

ATTACH_EMBEDDED_MSG = 5


class _Truncated(Exception):
    """The stream ended inside a structure; what was read so far is kept."""


def is_tnef(data: bytes) -> bool:
    """Tell whether the bytes start with the TNEF signature."""
    return data[:4] == SIGNATURE


def _python_codec(codepage: int) -> str | None:
    name = "utf-8" if codepage == 65001 else f"cp{codepage}"
    try:
        "".encode(name)
    except LookupError:
        return None
    return name


def _mapi_properties(data: bytes, codepage: str) -> dict[int, Any]:
    """Decode a TNEF MAPI property list into {property id: value}.

    Strings are decoded, PT_LONG/PT_SHORT/PT_BOOLEAN become integers, and all
    other values stay bytes. A multi-valued property keeps its first value.
    """
    values: dict[int, Any] = {}
    position = 0

    def take(size: int) -> bytes:
        nonlocal position
        if size < 0 or position + size > len(data):
            raise _Truncated
        chunk = data[position:position + size]
        position += size
        return chunk

    def u32() -> int:
        return struct.unpack("<I", take(4))[0]

    try:
        for _ in range(u32()):
            prop_type, prop_id = struct.unpack("<HH", take(4))
            if prop_id >= 0x8000:
                # Named property: GUID, then a numeric or a string name.
                take(16)
                if u32() == 0:
                    take(4)
                else:
                    length = u32()
                    take(length + (-length % 4))
            multi = bool(prop_type & MV_FLAG)
            base = prop_type & ~MV_FLAG
            items: list[Any] = []
            if base in _VARIABLE_TYPES:
                for _ in range(u32()):
                    length = u32()
                    raw = take(length)
                    take(-length % 4)
                    if base == PT_UNICODE:
                        items.append(raw.decode("utf-16-le", errors="replace").rstrip("\x00"))
                    elif base == PT_STRING8:
                        items.append(raw.rstrip(b"\x00").decode(codepage, errors="replace"))
                    else:
                        items.append(raw)
            elif base in _FIXED_SIZES:
                for _ in range(u32() if multi else 1):
                    raw = take(_FIXED_SIZES[base])
                    if base == PT_LONG:
                        items.append(struct.unpack("<i", raw)[0])
                    elif base in {PT_SHORT, PT_BOOLEAN}:
                        items.append(struct.unpack("<h", raw[:2])[0])
                    else:
                        items.append(raw)
            else:
                # Unknown type: its size is unknown, so the rest cannot be read.
                break
            if items and prop_id < 0x8000 and (prop_id not in values or base == PT_UNICODE):
                values[prop_id] = items[0]
    except _Truncated:
        pass
    return values


def _attributes(data: bytes) -> list[tuple[int, int, bytes]]:
    """Return (level, attribute id, value) for every attribute of a TNEF stream."""
    if not is_tnef(data):
        raise ValueError("nem TNEF-csomag (hiányzik a TNEF-aláírás)")
    found: list[tuple[int, int, bytes]] = []
    position = 6  # signature and the 2-byte key
    while position + 9 <= len(data):
        level = data[position]
        tag, length = struct.unpack_from("<II", data, position + 1)
        start = position + 9
        if level not in (LEVEL_MESSAGE, LEVEL_ATTACHMENT):
            break
        # A cut-off file keeps the part of the last value that is present.
        found.append((level, tag & 0xFFFF, data[start:start + length]))
        position = start + length + 2  # value and its 16-bit checksum
    return found


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _systime(value: Any) -> datetime | None:
    if not isinstance(value, bytes) or len(value) != 8:
        return None
    ticks = struct.unpack("<Q", value)[0]
    if not ticks:
        return None
    try:
        return datetime.fromtimestamp(ticks / 10_000_000 - 11_644_473_600, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _date_attribute(value: bytes) -> datetime | None:
    if len(value) < 12:
        return None
    year, month, day, hour, minute, second = struct.unpack_from("<6H", value)
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def read_tnef(data: bytes, depth: int = 0) -> EmailMessage:
    """Parse TNEF bytes into an EmailMessage with the package's body and attachments."""
    attributes = _attributes(data)
    codepage = "cp1252"
    for level, attribute, value in attributes:
        if attribute == ATT_OEM_CODEPAGE and len(value) >= 4:
            codepage = _python_codec(struct.unpack_from("<I", value)[0]) or codepage
            break

    properties: dict[int, Any] = {}
    subject = body = ""
    sent: datetime | None = None
    attachments: list[dict[str, Any]] = []
    for level, attribute, value in attributes:
        if level == LEVEL_MESSAGE:
            if attribute == ATT_MAPI_PROPS:
                properties.update(_mapi_properties(value, codepage))
            elif attribute == ATT_SUBJECT:
                subject = value.rstrip(b"\x00").decode(codepage, errors="replace")
            elif attribute == ATT_BODY:
                body = decode_text_bytes(value.rstrip(b"\x00"), codepage)
            elif attribute == ATT_DATE_SENT:
                sent = _date_attribute(value)
        elif attribute == ATT_ATTACH_RENDDATA:
            # Every attachment starts with its rendering data.
            attachments.append({"title": "", "data": None, "properties": {}})
        elif attachments:
            current = attachments[-1]
            if attribute == ATT_ATTACH_TITLE:
                current["title"] = value.rstrip(b"\x00").decode(codepage, errors="replace")
            elif attribute == ATT_ATTACH_DATA:
                current["data"] = value
            elif attribute == ATT_ATTACHMENT:
                current["properties"].update(_mapi_properties(value, codepage))

    internet_codepage = properties.get(0x3FDE)
    body_codepage = (_python_codec(internet_codepage) if isinstance(internet_codepage, int) else None) or codepage

    message = EmailMessage(policy=policy.default)
    sent = _systime(properties.get(0x0039)) or sent
    sender_address = next(
        (item for item in (_text(properties.get(0x5D01)), _text(properties.get(0x0C1F))) if "@" in item), ""
    )
    headers = {
        "From": _address(_text(properties.get(0x0C1A)) or _text(properties.get(0x0042)), sender_address),
        "To": _text(properties.get(0x0E04)).replace(";", ","),
        "Cc": _text(properties.get(0x0E03)).replace(";", ","),
        "Date": format_datetime(sent) if sent else "",
        "Subject": _text(properties.get(0x0037)) or subject,
        "Message-ID": _text(properties.get(0x1035)),
    }
    for name, value in headers.items():
        _set_header(message, name, value)
    _set_header(message, "X-Mail-Scope-Converted", "Outlook TNEF (winmail.dat; MAPI-tulajdonságokból összeállítva)")

    plain = _text(properties.get(0x1000)) or body
    html_value = properties.get(0x1013)
    html = decode_text_bytes(html_value, body_codepage) if isinstance(html_value, bytes) else _text(html_value)
    compressed_rtf = properties.get(0x1009)
    if isinstance(compressed_rtf, bytes) and compressed_rtf and not (plain.strip() and html.strip()):
        try:
            content, is_html = rtf_content(decompress_rtf(compressed_rtf))
        except ValueError:
            content, is_html = "", False
        if is_html and not html.strip():
            html = content
        elif not is_html and not plain.strip():
            plain = content
    message.set_content(plain or "", subtype="plain")
    if html.strip():
        message.add_alternative(html, subtype="html")

    for index, attachment in enumerate(attachments, start=1):
        props = attachment["properties"]
        filename = (
            _text(props.get(0x3707)) or _text(props.get(0x3704)) or attachment["title"]
            or _text(props.get(0x3001)) or f"melleklet-{index}"
        )
        content = props.get(0x3701) if isinstance(props.get(0x3701), bytes) else None
        embedded = content[16:] if content is not None and is_tnef(content[16:20]) else None
        if embedded is not None and (props.get(0x3705) == ATTACH_EMBEDDED_MSG or attachment["data"] is None):
            # An attached Outlook item: an interface ID followed by its own TNEF stream.
            if depth >= MAX_EMBEDDING_DEPTH:
                continue
            inner = read_tnef(embedded, depth + 1)
            display = _text(props.get(0x3001)) or str(inner.get("Subject") or "csatolt-level")
            message.add_attachment(inner, filename=f"{display}.eml")
            continue
        payload = attachment["data"] if attachment["data"] is not None else content
        if payload is None:
            # OLE objects and references are not file attachments.
            continue
        mime = (_text(props.get(0x370E)) or "application/octet-stream").casefold()
        maintype, _, subtype = mime.partition("/")
        if maintype in {"text", "multipart", "message"} or not subtype or not re.fullmatch(r"[\w.+-]+/[\w.+-]+", mime):
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
    return message


# --- Compressed RTF ([MS-OXRTFCP]) ---------------------------------------------

_RTF_DICTIONARY = (
    b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss "
    b"\\fmodern \\fscript \\fdecor MS Sans SerifSymbolArialTimes New RomanCourier"
    b"{\\colortbl\\red0\\green0\\blue0\r\n\\par \\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx"
)
_RTF_COMPRESSED = 0x75465A4C  # "LZFu"
_RTF_UNCOMPRESSED = 0x414C454D  # "MELA"
MAX_RTF_BYTES = 64 * 1024 * 1024


def decompress_rtf(data: bytes) -> bytes:
    """Unpack a PR_RTF_COMPRESSED value into RTF bytes."""
    if len(data) < 16:
        raise ValueError("a tömörített RTF fejléce csonka")
    _, raw_size, kind, _ = struct.unpack_from("<IIII", data)
    if kind == _RTF_UNCOMPRESSED:
        return data[16:16 + raw_size]
    if kind != _RTF_COMPRESSED:
        raise ValueError("ismeretlen RTF-tömörítés")
    raw_size = min(raw_size, MAX_RTF_BYTES)
    dictionary = bytearray(4096)
    dictionary[:len(_RTF_DICTIONARY)] = _RTF_DICTIONARY
    write = len(_RTF_DICTIONARY)
    output = bytearray()
    position = 16
    end = len(data)
    while position < end and len(output) < raw_size:
        control = data[position]
        position += 1
        for bit in range(8):
            if position >= end or len(output) >= raw_size:
                break
            if control & (1 << bit):
                if position + 2 > end:
                    position = end
                    break
                reference = (data[position] << 8) | data[position + 1]
                position += 2
                offset = reference >> 4
                if offset == write:
                    # A reference to the write position marks the end.
                    return bytes(output)
                for step in range((reference & 0x0F) + 2):
                    byte = dictionary[(offset + step) % 4096]
                    output.append(byte)
                    dictionary[write] = byte
                    write = (write + 1) % 4096
            else:
                byte = data[position]
                position += 1
                output.append(byte)
                dictionary[write] = byte
                write = (write + 1) % 4096
    return bytes(output[:raw_size])


# --- RTF text and HTML wrapped in RTF ([MS-OXRTFEX]) ---------------------------

_RTF_TOKEN = re.compile(
    r"\\([a-z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-z])|([{}])|([^\\{}]+)", re.DOTALL
)
_SKIPPED_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "headerl", "headerr",
    "footerl", "footerr", "object", "datastore", "themedata", "colorschememapping", "latentstyles",
    "listtable", "listoverridetable", "generator", "xmlnstbl", "rsidtbl", "mmathPr",
}
_RTF_CHARACTERS = {
    "par": "\n", "line": "\n", "sect": "\n", "page": "\n", "tab": "\t",
    "lquote": "\u2018", "rquote": "\u2019", "ldblquote": "\u201c", "rdblquote": "\u201d",
    "bullet": "\u2022", "endash": "\u2013", "emdash": "\u2014",
    "emspace": "\u2003", "enspace": "\u2002", "qmspace": "\u2005",
}
_RTF_SYMBOLS = {"~": "\u00a0", "_": "-", "{": "{", "}": "}", "\\": "\\", "\n": "\n", "\r": "\n"}


def rtf_content(rtf: bytes) -> tuple[str, bool]:
    """Return (content, is_html) of an RTF body.

    When the RTF wraps an HTML body (``\\fromhtml1``), the original HTML is
    restored; otherwise the visible text is returned.
    """
    source = rtf.decode("latin-1")
    is_html = "\\fromhtml" in source[:4096]
    codepage = "cp1252"
    output: list[str] = []
    pending = bytearray()

    def flush() -> None:
        if pending:
            output.append(pending.decode(codepage, errors="replace"))
            pending.clear()

    # Per group: is it skipped, is it an \htmltag group, how many fallback characters follow \u.
    stack: list[tuple[bool, bool, int]] = []
    skip = in_tag = False
    unicode_skip = 1
    suppressed = False  # inside \htmlrtf … \htmlrtf0: RTF-only content of wrapped HTML
    star = False  # the previous token was \*
    fallback = 0  # characters still to drop after a \u
    group_start = False  # the previous token opened a group
    position = 0
    for match in _RTF_TOKEN.finditer(source):
        if match.start() < position:
            continue
        word, parameter, hex_byte, symbol, brace, text = match.groups()
        opened, group_start = group_start, False
        starred, star = star, False
        if brace == "{":
            stack.append((skip, in_tag, unicode_skip))
            group_start = True
            fallback = 0
            continue
        if brace == "}":
            if stack:
                skip, in_tag, unicode_skip = stack.pop()
            fallback = 0
            continue
        if symbol == "*":
            star = True
            group_start = opened
            continue
        visible = not skip and (in_tag or not (is_html and suppressed))
        if word is not None:
            number = int(parameter) if parameter else None
            if word == "htmltag" and starred and is_html:
                in_tag = True
                continue
            if starred or (opened and word in _SKIPPED_DESTINATIONS):
                skip = True
                continue
            if word == "bin":
                position = match.end() + max(0, number or 0)
            elif word == "htmlrtf":
                suppressed = number != 0
            elif word == "ansicpg" and number:
                flush()
                codepage = _python_codec(number) or codepage
            elif word == "uc" and number is not None:
                unicode_skip = max(0, number)
            elif word == "u" and number is not None:
                if visible:
                    flush()
                    output.append(chr(number % 65536))
                fallback = unicode_skip
            elif word in _RTF_CHARACTERS and visible:
                flush()
                output.append(_RTF_CHARACTERS[word])
            continue
        if hex_byte is not None:
            if fallback:
                fallback -= 1
            elif visible:
                pending.append(int(hex_byte, 16))
            continue
        if symbol is not None:
            if visible and symbol in _RTF_SYMBOLS:
                flush()
                output.append(_RTF_SYMBOLS[symbol])
            continue
        text = text.replace("\r", "").replace("\n", "")
        if fallback:
            dropped = min(fallback, len(text))
            text, fallback = text[dropped:], fallback - dropped
        if visible and text:
            pending.extend(text.encode("latin-1"))
    flush()
    content = "".join(output)
    # \u values above the BMP arrive as surrogate pairs.
    content = content.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return content, is_html
