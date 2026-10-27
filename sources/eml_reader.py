"""Read individual RFC 5322 EML files without changing the source."""

__author__ = "Bálint Szabolcs"

import logging
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Iterator

from .discover import eml_files
from .models import Attachment, FolderInfo, MessageLocator, MessageMetadata
from .content import (
    message_attachments,
    message_addresses,
    message_body_text,
    message_participant_names,
)


logger = logging.getLogger(__name__)


class EmlReader:
    """Expose one EML file through the common source-reader interface."""

    def __init__(self, path: str | Path, folder_name: str | None = None) -> None:
        """Remember a path after verifying that it names a regular file."""
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.folder_name = folder_name

    def iter_folders(self) -> list[FolderInfo]:
        """Return the single logical folder represented by this EML file."""
        name = self.path.parent.name or "EML"
        if self.folder_name is not None:
            name = self.folder_name
        return [FolderInfo(path=name, name=name)]

    def iter_messages(
        self,
        folder: str | None = None,
        after_locator: MessageLocator | None = None,
    ) -> Iterator[MessageMetadata]:
        """Yield EML metadata, reporting parse and file errors per message."""
        folder_info = self.iter_folders()[0]
        if (
            (folder is not None and folder != folder_info.path)
            or after_locator is not None
        ):
            return

        yield _file_metadata(
            self.path, MessageLocator(offset=0, length=0), folder_info.path
        )

    def open_message(self, locator: MessageLocator) -> EmailMessage:
        """Parse the requested byte range from this EML source."""
        return BytesParser(policy=policy.default).parsebytes(
            self.open_message_bytes(locator)
        )

    def open_message_bytes(self, locator: MessageLocator) -> bytes:
        """Return the exact original bytes of this EML message."""
        return _read_eml(self.path, locator)

    def open_attachment(self, locator: MessageLocator, index: int) -> Attachment:
        """Load a single attachment from the EML file on demand."""
        return _attachment(self.open_message(locator), index)

    def open_attachments(self, locator: MessageLocator) -> list[Attachment]:
        """Load all attachments of the message into memory, in indexing order."""
        return message_attachments(self.open_message(locator))

    def close(self) -> None:
        """EML reads use short-lived file handles; nothing stays open."""


class EmlDirectoryReader:
    """Expose the EML files of one directory as a single mailbox source."""

    def __init__(self, path: str | Path, folder_name: str | None = None) -> None:
        """Remember a directory of EML files."""
        self.path = Path(path)
        if not self.path.is_dir():
            raise NotADirectoryError(self.path)
        self.folder_name = folder_name

    def iter_folders(self) -> list[FolderInfo]:
        """Return the single logical folder represented by this directory."""
        name = self.folder_name or self.path.name or "EML"
        return [FolderInfo(path=name, name=name.rsplit("/", 1)[-1])]

    def iter_messages(
        self,
        folder: str | None = None,
        after_locator: MessageLocator | None = None,
    ) -> Iterator[MessageMetadata]:
        """Yield each EML file's metadata in stable file-name order."""
        folder_info = self.iter_folders()[0]
        if folder is not None and folder != folder_info.path:
            return
        first_index = 0
        if after_locator is not None and after_locator.item_index is not None:
            first_index = after_locator.item_index + 1
        for index, file in enumerate(eml_files(self.path)):
            if index < first_index:
                continue
            yield _file_metadata(
                file,
                MessageLocator(
                    offset=0,
                    length=0,
                    folder_path=file.name,
                    item_index=index,
                    item_id=file.name,
                ),
                folder_info.path,
            )

    def open_message(self, locator: MessageLocator) -> EmailMessage:
        """Parse one EML file of this directory."""
        return BytesParser(policy=policy.default).parsebytes(
            self.open_message_bytes(locator)
        )

    def open_message_bytes(self, locator: MessageLocator) -> bytes:
        """Return the exact original bytes of one EML file."""
        return _read_eml(self._file(locator), locator)

    def open_attachment(self, locator: MessageLocator, index: int) -> Attachment:
        """Load a single attachment from one EML file on demand."""
        return _attachment(self.open_message(locator), index)

    def open_attachments(self, locator: MessageLocator) -> list[Attachment]:
        """Load all attachments of one EML file into memory, in indexing order."""
        return message_attachments(self.open_message(locator))

    def close(self) -> None:
        """EML reads use short-lived file handles; nothing stays open."""

    def _file(self, locator: MessageLocator) -> Path:
        """Resolve a locator to a file directly inside this directory."""
        name = locator.folder_path or ""
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("Invalid EML directory locator")
        return self.path / name


def _read_eml(path: Path, locator: MessageLocator) -> bytes:
    """Read an EML file and verify its indexed length."""
    if locator.offset != 0 or locator.length <= 0:
        raise ValueError("Invalid EML message locator")
    with path.open("rb") as source:
        raw = source.read(locator.length + 1)
    if len(raw) != locator.length:
        raise ValueError("EML message length differs from the indexed length")
    return raw


def _file_metadata(path: Path, locator: MessageLocator, folder: str) -> MessageMetadata:
    """Read one EML file, reporting a malformed file as an error entry."""
    try:
        locator = MessageLocator(
            offset=0,
            length=path.stat().st_size,
            folder_path=locator.folder_path,
            item_index=locator.item_index,
            item_id=locator.item_id,
        )
        message = BytesParser(policy=policy.default).parsebytes(_read_eml(path, locator))
        return _metadata(message, locator, folder)
    except Exception as error:
        logger.warning("Unable to read EML source %s: %s", path, error)
        return MessageMetadata(
            locator=locator,
            folder=folder,
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


def _attachment(message: EmailMessage, index: int) -> Attachment:
    """Return one attachment part of a parsed message."""
    if index < 0:
        raise IndexError("Attachment index must be non-negative")
    attachments = (
        part
        for part in message.walk()
        if part.get_content_disposition() == "attachment" or part.get_filename()
    )
    for attachment_index, part in enumerate(attachments):
        if attachment_index == index:
            return Attachment(
                filename=part.get_filename() or f"attachment-{index + 1}",
                content_type=part.get_content_type(),
                data=part.get_payload(decode=True) or b"",
            )
    raise IndexError(f"No attachment at index {index}")


def _metadata(
    message: EmailMessage, locator: MessageLocator, folder: str
) -> MessageMetadata:
    """Build normalized search metadata from the parsed EML headers."""
    attachments = [
        part
        for part in message.walk()
        if part.get_content_disposition() == "attachment" or part.get_filename()
    ]
    return MessageMetadata(
        locator=locator,
        folder=folder,
        subject=str(message.get("Subject", "")),
        from_addresses=message_addresses(message, "From"),
        to_addresses=message_addresses(message, "To"),
        cc_addresses=message_addresses(message, "Cc"),
        bcc_addresses=message_addresses(message, "Bcc"),
        date=message.get("Date"),
        message_id=message.get("Message-ID"),
        in_reply_to=message.get("In-Reply-To"),
        references=message.get("References"),
        attachment_names=tuple(part.get_filename() or "attachment" for part in attachments),
        text_body=message_body_text(message),
        attachment_content_types=tuple(part.get_content_type() for part in attachments),
        participant_names=message_participant_names(message),
    )
