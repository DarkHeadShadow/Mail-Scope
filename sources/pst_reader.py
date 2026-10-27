"""Read PST and OST files through the optional libpff Python bindings."""

__author__ = "Bálint Szabolcs"

import importlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, formataddr
from pathlib import Path
from typing import Iterator, Any

from .models import Attachment, FolderInfo, MessageLocator, MessageMetadata
from .content import (
    decode_text_bytes,
    message_addresses,
    message_body_text,
    message_participant_names,
)


ITEM_TYPE_TAG = 0x001A
RECIPIENT_TYPE_TAG = 0x0C15
DISPLAY_NAME_TAG = 0x3001
EMAIL_ADDRESS_TAG = 0x3003
SMTP_ADDRESS_TAG = 0x39FE
ATTACH_FILENAME_TAGS = (0x3707, 0x3704)
ATTACH_CONTENT_TYPE_TAG = 0x370E
RECIPIENT_TYPES = {1: "To", 2: "Cc", 3: "Bcc"}
SENDER_EMAIL_TAG = 0x0C1F
SENDER_SMTP_TAG = 0x5D01
SENT_REPRESENTING_EMAIL_TAG = 0x0065
SENT_REPRESENTING_SMTP_TAG = 0x5D02
INTERNET_MESSAGE_ID_TAG = 0x1035
IN_REPLY_TO_ID_TAG = 0x1042
INTERNET_REFERENCES_TAG = 0x1039
INTERNET_CODEPAGE_TAG = 0x3FDE
MESSAGE_TAGS = (
    ITEM_TYPE_TAG,
    SENDER_EMAIL_TAG,
    SENDER_SMTP_TAG,
    SENT_REPRESENTING_EMAIL_TAG,
    SENT_REPRESENTING_SMTP_TAG,
    INTERNET_MESSAGE_ID_TAG,
    IN_REPLY_TO_ID_TAG,
    INTERNET_REFERENCES_TAG,
    INTERNET_CODEPAGE_TAG,
)
ITEM_TYPE_NAMES = (
    ("IPM.CONTACT", "contact"),
    ("IPM.APPOINTMENT", "calendar"),
    ("IPM.SCHEDULE.MEETING", "calendar"),
    ("IPM.TASK", "task"),
    ("IPM.STICKYNOTE", "note"),
)
_HTML_CHARSET = re.compile(rb"""charset\s*=\s*["']?([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Mailbox:
    """One MAPI sender or recipient with its SMTP and Exchange addresses."""

    role: str
    name: str
    smtp: str
    legacy: str

    @property
    def address(self) -> str:
        """Prefer SMTP; fall back to the Exchange distinguished name."""
        return self.smtp or self.legacy


class PstReader:
    """Read Outlook store items without extracting them to disk."""

    def __init__(self, path: str | Path) -> None:
        """Validate the path and require the optional libpff binding."""
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        try:
            pypff = importlib.import_module("pypff")
        except ImportError as error:
            raise RuntimeError(
                "PST/OST olvasásához telepítse a libpff pypff kötését."
            ) from error
        self._pypff = pypff
        self._cached_store: Any | None = None
        self._cached_folders: dict[str, Any] = {}

    def close(self) -> None:
        """Release the store handle kept open for on-demand message reads."""
        if self._cached_store is not None:
            try:
                self._cached_store.close()
            finally:
                self._cached_store = None
                self._cached_folders = {}

    def iter_folders(self) -> list[FolderInfo]:
        """Enumerate the mailbox folder tree while keeping the source read-only."""
        store = self._open_store()
        try:
            return [
                FolderInfo(path=folder_path, name=folder_path.rsplit("/", 1)[-1])
                for folder_path, _ in self._folders(store.get_root_folder())
            ]
        finally:
            store.close()

    def iter_messages(
        self,
        folder: str | None = None,
        after_locator: MessageLocator | None = None,
    ) -> Iterator[MessageMetadata]:
        """Yield message metadata with stable folder and item locators."""
        store = self._open_store()
        try:
            folders = list(self._folders(store.get_root_folder()))
            resume_folder_index = 0
            first_item_index = 0
            if after_locator is not None:
                if after_locator.folder_path is None:
                    raise ValueError("PST/OST resume locator has no folder path")
                folder_paths = [folder_path for folder_path, _ in folders]
                if after_locator.folder_path not in folder_paths:
                    raise ValueError(
                        f"Outlook resume folder no longer exists: "
                        f"{after_locator.folder_path}"
                    )
                resume_folder_index = folder_paths.index(after_locator.folder_path)
                if after_locator.item_index is not None:
                    first_item_index = after_locator.item_index + 1

            for folder_position, (folder_path, folder_object) in enumerate(folders):
                if folder_position < resume_folder_index:
                    continue
                if folder is not None and folder != folder_path:
                    continue
                folder_first_item = (
                    first_item_index
                    if folder_position == resume_folder_index
                    else 0
                )
                try:
                    item_count = folder_object.get_number_of_sub_messages()
                except Exception as error:
                    logger.warning(
                        "Unable to count PST/OST items in %s: %s", folder_path, error
                    )
                    continue
                for item_index in range(folder_first_item, item_count):
                    try:
                        item = folder_object.get_sub_message(item_index)
                        locator = MessageLocator(
                            offset=0,
                            length=0,
                            folder_path=folder_path,
                            item_index=item_index,
                            item_id=_optional_text(item, "identifier"),
                        )
                        metadata = self._metadata(item, locator, folder_path)
                    except Exception as error:
                        logger.warning(
                            "Unable to read PST/OST item %d in %s: %s",
                            item_index,
                            folder_path,
                            error,
                        )
                        metadata = MessageMetadata(
                            locator=MessageLocator(
                                offset=0,
                                length=0,
                                folder_path=folder_path,
                                item_index=item_index,
                            ),
                            folder=folder_path,
                            subject="",
                            from_addresses=(),
                            to_addresses=(),
                            cc_addresses=(),
                            bcc_addresses=(),
                            date=None,
                            message_id=None,
                            in_reply_to=None,
                            references=None,
                            attachment_names=(),
                            error=f"{type(error).__name__}: {error}",
                        )
                    yield metadata
        finally:
            store.close()

    def open_message(self, locator: MessageLocator) -> EmailMessage:
        """Load one Outlook item by its folder path and item index."""
        with self._resolve_item(locator) as item:
            return self._as_email_message(item)

    def open_message_bytes(self, locator: MessageLocator) -> bytes:
        """Build an RFC 5322 representation of a PST item, including attachments."""
        with self._resolve_item(locator) as item:
            message = self._as_email_message(item, include_attachments=True)
            return message.as_bytes(policy=policy.SMTP)

    def open_attachment(self, locator: MessageLocator, index: int) -> Attachment:
        """Read a selected attachment into memory only when requested."""
        if index < 0:
            raise IndexError("Attachment index must be non-negative")

        with self._resolve_item(locator) as item:
            if index >= _attachment_count(item):
                raise IndexError(f"No attachment at index {index}")
            return _read_attachment(item.get_attachment(index), index)

    def open_attachments(self, locator: MessageLocator) -> list[Attachment]:
        """Load all attachments of one item into memory; unreadable ones carry an error."""
        attachments: list[Attachment] = []
        with self._resolve_item(locator) as item:
            names = _attachment_metadata(item)
            for index in range(_attachment_count(item)):
                try:
                    attachments.append(_read_attachment(item.get_attachment(index), index))
                except Exception as error:
                    filename, content_type = names[index] if index < len(names) else (
                        f"attachment-{index + 1}", "application/octet-stream"
                    )
                    attachments.append(
                        Attachment(filename, content_type, b"", error=f"{type(error).__name__}: {error}")
                    )
        return attachments

    def _open_store(self) -> Any:
        """Open one read-only libpff file handle for the duration of an operation."""
        store = self._pypff.file()
        store.open(str(self.path))
        return store

    @staticmethod
    def _folders(folder: Any, parent: str = "") -> Iterator[tuple[str, Any]]:
        """Walk subfolders recursively and yield their slash-separated paths."""
        for index in range(folder.get_number_of_sub_folders()):
            child = folder.get_sub_folder(index)
            name = _optional_text(child, "name") or f"Folder {index + 1}"
            path = f"{parent}/{name}" if parent else name
            yield path, child
            yield from PstReader._folders(child, path)

    def _resolve_item(self, locator: MessageLocator) -> "_ScopedItem":
        """Resolve an indexed item locator in the cached read-only store."""
        if locator.folder_path is None or locator.item_index is None:
            raise ValueError("Invalid PST/OST message locator")

        if self._cached_store is None:
            store = self._open_store()
            try:
                self._cached_folders = dict(self._folders(store.get_root_folder()))
            except Exception:
                store.close()
                raise
            self._cached_store = store
        folder = self._cached_folders.get(locator.folder_path)
        if folder is None:
            raise ValueError(
                f"Outlook folder no longer exists: {locator.folder_path}"
            )
        if (
            locator.item_index < 0
            or locator.item_index >= folder.get_number_of_sub_messages()
        ):
            raise ValueError("PST/OST message locator is out of range")
        item = folder.get_sub_message(locator.item_index)
        if locator.item_id is not None:
            actual_id = _optional_text(item, "identifier")
            if actual_id != locator.item_id:
                raise ValueError("PST/OST item changed since it was indexed")
        return _ScopedItem(item)

    def _metadata(
        self, item: Any, locator: MessageLocator, folder: str
    ) -> MessageMetadata:
        """Normalize an Outlook item while retaining its original headers."""
        properties = _record_strings(item, MESSAGE_TAGS)
        message = self._as_email_message(item, properties=properties)
        sender = _mapi_sender(item, properties)
        recipients = _mapi_recipients(item)
        addresses = {
            role: list(message_addresses(message, header))
            for role, header in (
                ("from", "From"),
                ("to", "To"),
                ("cc", "Cc"),
                ("bcc", "Bcc"),
            )
        }
        participant_names = list(message_participant_names(message))
        # Exchange-only mailboxes cannot travel through RFC 5322 headers
        # intact, so they are added to the metadata directly.
        transport_headers = _optional_text(item, "transport_headers")
        transport_roles = {
            name.casefold()
            for name in BytesParser(policy=policy.default)
            .parsebytes(transport_headers.encode("utf-8", errors="replace"), headersonly=True)
            .keys()
        }
        legacy_only = [
            mailbox
            for mailbox in ([sender] if sender is not None else []) + recipients
            if mailbox.legacy and not mailbox.smtp
        ]
        for mailbox in legacy_only:
            if mailbox.role in transport_roles or (
                mailbox.role == "from" and addresses["from"]
            ):
                continue
            addresses[mailbox.role].append(mailbox.legacy)
            participant_names.append((mailbox.role, mailbox.legacy, mailbox.name))
        aliases = tuple(
            sorted(
                {
                    (mailbox.legacy.casefold(), mailbox.smtp.casefold())
                    for mailbox in ([sender] if sender is not None else []) + recipients
                    if mailbox.legacy and mailbox.smtp
                }
            )
        )

        message_class = properties.get(ITEM_TYPE_TAG, "").upper()
        item_type = next(
            (
                item_type
                for prefix, item_type in ITEM_TYPE_NAMES
                if message_class.startswith(prefix)
            ),
            "mail" if not message_class or message_class.startswith("IPM.NOTE") else "other",
        )
        attachments = _attachment_metadata(item)
        return MessageMetadata(
            locator=locator,
            folder=folder,
            subject=str(message.get("Subject", "")),
            from_addresses=tuple(addresses["from"]),
            to_addresses=tuple(addresses["to"]),
            cc_addresses=tuple(addresses["cc"]),
            bcc_addresses=tuple(addresses["bcc"]),
            date=message.get("Date"),
            message_id=message.get("Message-ID"),
            in_reply_to=message.get("In-Reply-To"),
            references=message.get("References"),
            attachment_names=tuple(name for name, _ in attachments),
            item_type=item_type,
            text_body=message_body_text(message),
            attachment_content_types=tuple(content_type for _, content_type in attachments),
            participant_names=tuple(participant_names),
            address_aliases=aliases,
        )

    @staticmethod
    def _as_email_message(
        item: Any,
        *,
        include_attachments: bool = False,
        properties: dict[int, str] | None = None,
    ) -> EmailMessage:
        """Parse transport headers and fill missing fields from MAPI properties."""
        if properties is None:
            properties = _record_strings(item, MESSAGE_TAGS)
        headers = _optional_text(item, "transport_headers")
        raw_headers = headers.encode("utf-8", errors="replace") if headers else b""
        message = BytesParser(policy=policy.default).parsebytes(raw_headers)
        _set_header_if_missing(message, "Subject", _optional_text(item, "subject"))
        sender = _mapi_sender(item, properties)
        if sender is not None and sender.smtp and message.get("From") is None:
            message["From"] = (
                formataddr((sender.name, sender.smtp))
                if sender.name and sender.name != sender.smtp
                else sender.smtp
            )

        _set_header_if_missing(message, "Date", _mapi_date(item))
        _set_header_if_missing(
            message, "Message-ID", properties.get(INTERNET_MESSAGE_ID_TAG, "")
        )
        _set_header_if_missing(
            message, "In-Reply-To", properties.get(IN_REPLY_TO_ID_TAG, "")
        )
        _set_header_if_missing(
            message, "References", properties.get(INTERNET_REFERENCES_TAG, "")
        )

        recipients_by_header: dict[str, list[str]] = {"To": [], "Cc": [], "Bcc": []}
        for mailbox in _mapi_recipients(item):
            if mailbox.smtp:
                recipients_by_header[mailbox.role.capitalize()].append(
                    formataddr((mailbox.name, mailbox.smtp))
                    if mailbox.name and mailbox.name != mailbox.smtp
                    else mailbox.smtp
                )
        for header, values in recipients_by_header.items():
            _set_header_if_missing(message, header, ", ".join(values))

        codepage_charset = _codepage_charset(properties.get(INTERNET_CODEPAGE_TAG, ""))
        body = _body_text(item, "plain_text_body", codepage_charset)
        html_body = _body_text(item, "html_body", codepage_charset, html=True)
        if message.get_payload() in (None, "", []):
            _clear_content_headers(message)
            if body:
                message.set_content(body)
                if html_body and include_attachments:
                    message.add_alternative(html_body, subtype="html")
            elif html_body:
                message.set_content(html_body, subtype="html")
            elif include_attachments:
                message.set_content("")
        if include_attachments:
            for index in range(_attachment_count(item)):
                try:
                    attachment = _read_attachment(item.get_attachment(index), index)
                except Exception as error:
                    logger.warning(
                        "PST/OST attachment %d could not be read for export: %s",
                        index,
                        error,
                    )
                    message["X-Mail-Scope-Unreadable-Attachment"] = (
                        f"{index + 1}: {type(error).__name__}"
                    )
                    continue
                maintype, separator, subtype = attachment.content_type.partition("/")
                subtype = subtype.split(";", 1)[0].strip()
                maintype = maintype.strip()
                if not separator or not maintype or not subtype:
                    maintype, subtype = "application", "octet-stream"
                message.add_attachment(
                    attachment.data,
                    maintype=maintype,
                    subtype=subtype,
                    filename=attachment.filename,
                )
        return message


class _ScopedItem:
    """Expose a resolved item through a context manager without closing the store."""

    def __init__(self, item: Any) -> None:
        """Retain the selected item."""
        self._item = item

    def __getattr__(self, name: str) -> Any:
        """Delegate item API calls to libpff."""
        return getattr(self._item, name)

    def __enter__(self) -> Any:
        """Allow callers to scope a PST item read."""
        return self

    def __exit__(self, *_: object) -> None:
        """The cached store stays open until the reader is closed."""


def _optional_text(item: Any, name: str, charset: str = "") -> str:
    """Read a pypff string property while tolerating missing properties."""
    try:
        value = getattr(item, name)
    except AttributeError:
        getter = getattr(item, f"get_{name}", None)
        if getter is None:
            return ""
        value = getter()
    except (OSError, RuntimeError) as error:
        logger.debug("PST/OST property %s is unreadable: %s", name, error)
        return ""
    if callable(value):
        value = value()
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return decode_text_bytes(bytes(value), charset)
    return str(value).rstrip("\x00")


def _body_text(item: Any, name: str, charset: str, *, html: bool = False) -> str:
    """Decode a body property using its declared HTML charset or the item code page."""
    try:
        value = getattr(item, name)
    except AttributeError:
        return ""
    except (OSError, RuntimeError) as error:
        logger.debug("PST/OST body %s is unreadable: %s", name, error)
        return ""
    if callable(value):
        value = value()
    if not value:
        return ""
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return str(value).rstrip("\x00")
    data = bytes(value)
    if html:
        declared = _HTML_CHARSET.search(data[:4096])
        if declared is not None:
            charset = declared.group(1).decode("ascii", errors="ignore") or charset
    return decode_text_bytes(data, charset)


def _codepage_charset(value: str) -> str:
    """Map a Windows code page identifier to a Python codec name."""
    try:
        codepage = int(value)
    except (TypeError, ValueError):
        return ""
    if codepage == 65001:
        return "utf-8"
    if codepage == 20127:
        return "ascii"
    if 28591 <= codepage <= 28599:
        return f"iso-8859-{codepage - 28590}"
    if codepage == 28605:
        return "iso-8859-15"
    return f"cp{codepage}"


def _record_strings(item: Any, tags: tuple[int, ...]) -> dict[int, str]:
    """Read several MAPI properties in one pass over the item's record sets."""
    wanted = set(tags)
    values: dict[int, str] = {}
    try:
        record_set_count = item.get_number_of_record_sets()
    except Exception as error:
        logger.debug("PST/OST record sets are unreadable: %s", error)
        return values
    for record_index in range(record_set_count):
        try:
            record_set = item.get_record_set(record_index)
            entry_count = record_set.get_number_of_entries()
        except Exception as error:
            logger.debug("PST/OST record set %d is unreadable: %s", record_index, error)
            continue
        for entry_index in range(entry_count):
            try:
                entry = record_set.get_entry(entry_index)
                entry_type = entry.get_entry_type()
                if entry_type not in wanted or entry_type in values:
                    continue
                if entry_type == INTERNET_CODEPAGE_TAG:
                    values[entry_type] = str(entry.get_data_as_integer())
                else:
                    values[entry_type] = _optional_text_value(entry.get_data_as_string())
            except Exception as error:
                logger.debug("PST/OST property entry is unreadable: %s", error)
    return values


def _record_string(item: Any, entry_type: int) -> str:
    """Look up one MAPI record-set value, returning empty when absent."""
    return _record_strings(item, (entry_type,)).get(entry_type, "")


def _split_address(value: str) -> tuple[str, str]:
    """Classify a MAPI address as SMTP or Exchange legacy distinguished name."""
    value = value.strip()
    if not value:
        return "", ""
    if value.casefold().startswith("/o="):
        return "", value
    if "@" in value:
        return value, ""
    return "", ""


def _mapi_sender(item: Any, properties: dict[int, str]) -> _Mailbox | None:
    """Read the sender's display name, SMTP address, and Exchange DN."""
    name = _optional_text(item, "sender_name")
    smtp = ""
    legacy = ""
    for tag in (
        SENDER_SMTP_TAG,
        SENDER_EMAIL_TAG,
        SENT_REPRESENTING_SMTP_TAG,
        SENT_REPRESENTING_EMAIL_TAG,
    ):
        candidate_smtp, candidate_legacy = _split_address(properties.get(tag, ""))
        smtp = smtp or candidate_smtp
        legacy = legacy or candidate_legacy
    if "@" in name and not smtp:
        smtp = name
        name = ""
    if not smtp and not legacy:
        return None
    return _Mailbox(role="from", name=name, smtp=smtp, legacy=legacy)


def _mapi_recipients(item: Any) -> list[_Mailbox]:
    """Read To/Cc/Bcc entries from recipient-table record sets when present."""
    recipients: list[_Mailbox] = []
    try:
        recipient_table = getattr(item, "recipients", None)
        if callable(recipient_table):
            recipient_table = recipient_table()
    except Exception as error:
        logger.debug("PST/OST recipient table is unreadable: %s", error)
        recipient_table = None
    record_owner = recipient_table if recipient_table is not None else item
    try:
        record_set_count = record_owner.get_number_of_record_sets()
    except Exception as error:
        logger.debug("PST/OST recipient records are unreadable: %s", error)
        return recipients
    for record_index in range(record_set_count):
        try:
            record_set = record_owner.get_record_set(record_index)
            values: dict[int, Any] = {}
            for entry_index in range(record_set.get_number_of_entries()):
                entry = record_set.get_entry(entry_index)
                entry_type = entry.get_entry_type()
                if entry_type in {
                    RECIPIENT_TYPE_TAG,
                    DISPLAY_NAME_TAG,
                    EMAIL_ADDRESS_TAG,
                    SMTP_ADDRESS_TAG,
                }:
                    values.setdefault(entry_type, entry)
            recipient_type_entry = values.get(RECIPIENT_TYPE_TAG)
            if recipient_type_entry is None:
                continue
            header = RECIPIENT_TYPES.get(recipient_type_entry.get_data_as_integer())
            if header is None:
                continue
            smtp, legacy = "", ""
            for tag in (SMTP_ADDRESS_TAG, EMAIL_ADDRESS_TAG):
                entry = values.get(tag)
                if entry is None:
                    continue
                candidate_smtp, candidate_legacy = _split_address(
                    _optional_text_value(entry.get_data_as_string())
                )
                smtp = smtp or candidate_smtp
                legacy = legacy or candidate_legacy
            name_entry = values.get(DISPLAY_NAME_TAG)
            name = (
                _optional_text_value(name_entry.get_data_as_string())
                if name_entry is not None
                else ""
            )
        except Exception as error:
            logger.debug("PST/OST recipient %d is unreadable: %s", record_index, error)
            continue
        if smtp or legacy:
            recipients.append(
                _Mailbox(role=header.casefold(), name=name, smtp=smtp, legacy=legacy)
            )
    return recipients


def _attachment_count(item: Any) -> int:
    """Count attachments; libpff raises for items without an attachment table."""
    try:
        return int(item.get_number_of_attachments() or 0)
    except Exception as error:
        logger.debug("PST/OST attachment count is unavailable: %s", error)
        return 0


def _attachment_metadata(item: Any) -> list[tuple[str, str]]:
    """Read attachment filenames and MIME types without loading content."""
    attachments: list[tuple[str, str]] = []
    for index in range(_attachment_count(item)):
        filename = f"attachment-{index + 1}"
        content_type = "application/octet-stream"
        try:
            attachment = item.get_attachment(index)
            properties = _record_strings(
                attachment, (*ATTACH_FILENAME_TAGS, ATTACH_CONTENT_TYPE_TAG)
            )
            filename = next(
                (properties[tag] for tag in ATTACH_FILENAME_TAGS if properties.get(tag)),
                filename,
            )
            content_type = (
                properties.get(ATTACH_CONTENT_TYPE_TAG)
                or _optional_text(attachment, "content_type")
                or content_type
            )
        except Exception as error:
            logger.warning("Unable to read PST/OST attachment %d: %s", index, error)
        attachments.append((filename, content_type))
    return attachments


def _read_attachment(attachment: Any, index: int) -> Attachment:
    """Load one attachment's bytes, name, and MIME type into memory."""
    attachment_size = attachment.get_size()
    if attachment_size is None:
        raise ValueError(f"PST/OST attachment {index} size is unavailable")
    attachment.seek_offset(0, 0)
    data = _as_bytes(attachment.read_buffer(int(attachment_size)))
    if len(data) != int(attachment_size):
        raise ValueError(f"PST/OST attachment {index} data is truncated")
    properties = _record_strings(
        attachment, (*ATTACH_FILENAME_TAGS, ATTACH_CONTENT_TYPE_TAG)
    )
    filename = next(
        (properties[tag] for tag in ATTACH_FILENAME_TAGS if properties.get(tag)),
        f"attachment-{index + 1}",
    )
    content_type = (
        properties.get(ATTACH_CONTENT_TYPE_TAG)
        or _optional_text(attachment, "content_type")
        or "application/octet-stream"
    )
    return Attachment(filename=filename, content_type=content_type, data=data)


def _optional_text_value(value: Any) -> str:
    """Convert a libpff string result to ordinary Python text."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return decode_text_bytes(bytes(value))
    return str(value).rstrip("\x00")


def _as_bytes(value: Any) -> bytes:
    """Normalize libpff attachment data to bytes without writing it to disk."""
    if value is None:
        return b""
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value)


def _set_header_if_missing(
    message: EmailMessage, name: str, value: str
) -> None:
    """Set a fallback header only when transport headers omitted it."""
    if not value or message.get(name) is not None:
        return
    try:
        message[name] = value
    except (ValueError, TypeError):
        message[name] = " ".join(value.split())


def _mapi_date(item: Any) -> str:
    """Use Outlook's submit/delivery/creation time when Date is absent."""
    for name in ("client_submit_time", "delivery_time", "creation_time"):
        try:
            value = getattr(item, name)
        except AttributeError:
            getter = getattr(item, f"get_{name}", None)
            if getter is None:
                continue
            value = getter
        except (OSError, RuntimeError):
            continue
        if callable(value):
            try:
                value = value()
            except (OSError, RuntimeError):
                continue
        if isinstance(value, datetime):
            # libpff converts FILETIME to naive UTC datetimes.
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return format_datetime(value)
        if value:
            return str(value)
    return ""


def _clear_content_headers(message: EmailMessage) -> None:
    """Remove stale MIME structure before installing a property-based body."""
    for name in (
        "Content-Type",
        "Content-Disposition",
        "Content-Transfer-Encoding",
        "MIME-Version",
    ):
        if name in message:
            del message[name]
