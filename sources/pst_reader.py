"""Read PST and OST files through the optional libpff Python bindings."""

__author__ = "Bálint Szabolcs"

import importlib
import logging
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Iterator, Any

from .models import Attachment, FolderInfo, MessageLocator, MessageMetadata
from .content import (
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
SENDER_SMTP_TAGS = (0x5D01, 0x0C1F)
ITEM_TYPE_NAMES = (
    ("IPM.CONTACT", "contact"),
    ("IPM.APPOINTMENT", "calendar"),
    ("IPM.SCHEDULE.MEETING", "calendar"),
    ("IPM.TASK", "task"),
    ("IPM.STICKYNOTE", "note"),
)
logger = logging.getLogger(__name__)


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
                for item_index in range(
                    folder_first_item, folder_object.get_number_of_sub_messages()
                ):
                    try:
                        item = folder_object.get_sub_message(item_index)
                        locator = MessageLocator(
                            offset=0,
                            length=0,
                            folder_path=folder_path,
                            item_index=item_index,
                            item_id=_optional_text(item, "identifier"),
                        )
                        yield self._metadata(item, locator, folder_path)
                    except (
                        AttributeError,
                        OSError,
                        RuntimeError,
                        TypeError,
                        ValueError,
                    ) as error:
                        logger.warning(
                            "Unable to read PST/OST item %d in %s: %s",
                            item_index,
                            folder_path,
                            error,
                        )
                        yield MessageMetadata(
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
                            error=str(error),
                        )
        finally:
            store.close()

    def open_message(self, locator: MessageLocator) -> EmailMessage:
        """Load one Outlook item by its folder path and item index."""
        with self._resolve_item(locator) as item:
            return self._as_email_message(item)

    def open_attachment(self, locator: MessageLocator, index: int) -> Attachment:
        """Read a selected attachment into memory only when requested."""
        if index < 0:
            raise IndexError("Attachment index must be non-negative")

        with self._resolve_item(locator) as item:
            attachment_count = item.get_number_of_attachments()
            if index >= attachment_count:
                raise IndexError(f"No attachment at index {index}")
            attachment = item.get_attachment(index)
            attachment_size = attachment.get_size()
            if attachment_size is None:
                raise ValueError("PST/OST attachment size is unavailable")
            size = int(attachment_size)
            attachment.seek_offset(0, 0)
            data = attachment.read_buffer(size)
            filename = next(
                (
                    value
                    for tag in ATTACH_FILENAME_TAGS
                    if (value := _record_string(attachment, tag))
                ),
                f"attachment-{index + 1}",
            )
            content_type = (
                _record_string(attachment, ATTACH_CONTENT_TYPE_TAG)
                or _optional_text(attachment, "content_type")
                or "application/octet-stream"
            )
            return Attachment(
                filename=filename,
                content_type=content_type,
                data=_as_bytes(data),
            )

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

    def _resolve_item(self, locator: MessageLocator) -> Any:
        """Reopen a PST/OST and resolve an indexed item locator."""
        if locator.folder_path is None or locator.item_index is None:
            raise ValueError("Invalid PST/OST message locator")

        store = self._open_store()
        try:
            folder = self._find_folder(store.get_root_folder(), locator.folder_path)
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
            attached_item = _AttachedPffItem(store, item)
            store = None
            return attached_item
        finally:
            if store is not None:
                store.close()

    def _find_folder(self, root: Any, requested_path: str) -> Any | None:
        """Find a folder by its indexed relative path."""
        for folder_path, folder in self._folders(root):
            if folder_path == requested_path:
                return folder
        return None

    def _metadata(
        self, item: Any, locator: MessageLocator, folder: str
    ) -> MessageMetadata:
        """Normalize an Outlook item while retaining its original headers."""
        message = self._as_email_message(item)
        from_addresses = message_addresses(message, "From")
        to_addresses = message_addresses(message, "To")
        cc_addresses = message_addresses(message, "Cc")
        bcc_addresses = message_addresses(message, "Bcc")
        header_recipient_addresses = to_addresses + cc_addresses + bcc_addresses
        mapi_recipients = _mapi_recipients(item)
        if not header_recipient_addresses:
            for header, values in mapi_recipients.items():
                if values:
                    message[header] = ", ".join(values)
            to_addresses = message_addresses(message, "To")
            cc_addresses = message_addresses(message, "Cc")
            bcc_addresses = message_addresses(message, "Bcc")
        message_class = _record_string(item, ITEM_TYPE_TAG).upper()
        item_type = next(
            (
                item_type
                for prefix, item_type in ITEM_TYPE_NAMES
                if message_class.startswith(prefix)
            ),
            "mail" if not message_class or message_class.startswith("IPM.NOTE") else "other",
        )
        return MessageMetadata(
            locator=locator,
            folder=folder,
            subject=str(message.get("Subject", "")),
            from_addresses=from_addresses,
            to_addresses=to_addresses,
            cc_addresses=cc_addresses,
            bcc_addresses=bcc_addresses,
            date=message.get("Date"),
            message_id=message.get("Message-ID"),
            in_reply_to=message.get("In-Reply-To"),
            references=message.get("References"),
            attachment_names=_attachment_names(item),
            item_type=item_type,
            text_body=message_body_text(message),
            attachment_content_types=_attachment_types(item),
            participant_names=message_participant_names(message),
        )

    @staticmethod
    def _as_email_message(item: Any) -> EmailMessage:
        """Parse transport headers and fill missing fields from MAPI properties."""
        headers = _optional_text(item, "transport_headers")
        raw_headers = headers.encode("utf-8", errors="replace") if headers else b""
        message = BytesParser(policy=policy.default).parsebytes(raw_headers)
        _set_header_if_missing(message, "Subject", _optional_text(item, "subject"))
        sender = _optional_text(item, "sender_name")
        if "@" not in sender:
            sender = next(
                (
                    address
                    for tag in SENDER_SMTP_TAGS
                    if (address := _record_string(item, tag))
                ),
                sender,
            )
        if sender and message.get("From") is None:
            message["From"] = sender

        for header, values in _mapi_recipients(item).items():
            _set_header_if_missing(message, header, ", ".join(values))

        body = _optional_text(item, "plain_text_body")
        if not body:
            body = _optional_text(item, "html_body")
            if body:
                message.set_content(body, subtype="html")
        elif message.get_content_maintype() == "text" and not message.get_payload():
            message.set_content(body)
        elif message.get("Content-Type") is None:
            message.set_content(body)
        return message


class _AttachedPffItem:
    """Keep the owning libpff handle alive while an item is being read."""

    def __init__(self, store: Any, item: Any) -> None:
        """Retain both the store and selected item."""
        self._store = store
        self._item = item

    def __getattr__(self, name: str) -> Any:
        """Delegate item API calls and close the store after the final access."""
        return getattr(self._item, name)

    def close(self) -> None:
        """Release the file handle after extraction."""
        self._store.close()

    def __enter__(self) -> Any:
        """Allow callers to scope a PST item read."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the store at the end of a scoped item read."""
        self.close()


def _optional_text(item: Any, name: str) -> str:
    """Read a pypff string property while tolerating missing properties."""
    try:
        value = getattr(item, name)
    except AttributeError:
        getter = getattr(item, f"get_{name}", None)
        if getter is None:
            return ""
        value = getter()
    if callable(value):
        value = value()
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").rstrip("\x00")
    return str(value).rstrip("\x00")


def _record_string(item: Any, entry_type: int) -> str:
    """Look up a MAPI record-set value, returning empty when absent."""
    for record_index in range(item.get_number_of_record_sets()):
        record_set = item.get_record_set(record_index)
        for entry_index in range(record_set.get_number_of_entries()):
            entry = record_set.get_entry(entry_index)
            if entry.get_entry_type() == entry_type:
                value = entry.get_data_as_string()
                return _optional_text_value(value)
    return ""


def _mapi_recipients(item: Any) -> dict[str, list[str]]:
    """Read To/Cc/Bcc entries from recipient-table record sets when present."""
    recipients: dict[str, list[str]] = {"To": [], "Cc": [], "Bcc": []}
    recipient_table = getattr(item, "recipients", None)
    if callable(recipient_table):
        recipient_table = recipient_table()
    record_owner = recipient_table if recipient_table is not None else item
    for record_index in range(record_owner.get_number_of_record_sets()):
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
                values[entry_type] = entry
        recipient_type_entry = values.get(RECIPIENT_TYPE_TAG)
        if recipient_type_entry is None:
            continue
        recipient_type = recipient_type_entry.get_data_as_integer()
        header = RECIPIENT_TYPES.get(recipient_type)
        if header is None:
            continue
        address_entry = values.get(SMTP_ADDRESS_TAG) or values.get(EMAIL_ADDRESS_TAG)
        name_entry = values.get(DISPLAY_NAME_TAG)
        address = (
            _optional_text_value(address_entry.get_data_as_string())
            if address_entry is not None
            else ""
        )
        name = (
            _optional_text_value(name_entry.get_data_as_string())
            if name_entry is not None
            else ""
        )
        if address:
            recipients[header].append(
                f"{name} <{address}>" if name and name != address else address
            )
    return recipients


def _attachment_names(item: Any) -> tuple[str, ...]:
    """Read attachment filenames from MAPI metadata without loading content."""
    names: list[str] = []
    for index in range(item.get_number_of_attachments()):
        try:
            attachment = item.get_attachment(index)
            filename = next(
                (
                    value
                    for tag in ATTACH_FILENAME_TAGS
                    if (value := _record_string(attachment, tag))
                ),
                f"attachment-{index + 1}",
            )
            names.append(filename)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            logger.warning("Unable to read PST/OST attachment %d: %s", index, error)
            names.append(f"attachment-{index + 1}")
    return tuple(names)


def _attachment_types(item: Any) -> tuple[str, ...]:
    """Read attachment MIME metadata without loading any attachment content."""
    content_types: list[str] = []
    for index in range(item.get_number_of_attachments()):
        try:
            attachment = item.get_attachment(index)
            content_type = (
                _record_string(attachment, ATTACH_CONTENT_TYPE_TAG)
                or _optional_text(attachment, "content_type")
                or "application/octet-stream"
            )
            content_types.append(content_type)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            logger.warning(
                "Unable to read PST/OST attachment MIME type %d: %s",
                index,
                error,
            )
            content_types.append("application/octet-stream")
    return tuple(content_types)


def _optional_text_value(value: Any) -> str:
    """Convert a libpff string result to ordinary Python text."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").rstrip("\x00")
    return str(value)


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
    if value and message.get(name) is None:
        message[name] = value
