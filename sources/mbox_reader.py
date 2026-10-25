"""Read-only MBOX reader with byte-range locators."""

__author__ = "Bálint Szabolcs"

import csv
import logging
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Iterator

from .models import Attachment, FolderInfo, MessageLocator, MessageMetadata
from .content import (
    message_addresses,
    message_body_text,
    message_participant_names,
)


logger = logging.getLogger(__name__)


class MboxReader:
    """Read messages from an MBOX file without extracting them to disk."""

    def __init__(self, path: str | Path, folder_name: str | None = None) -> None:
        """Open a mailbox path and optionally assign its archive folder path."""
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.folder_name = folder_name

    def iter_folders(self) -> list[FolderInfo]:
        """Return the single logical folder represented by this MBOX file."""
        name = self.path.stem or self.path.name
        if self.folder_name is not None:
            name = self.folder_name
        return [FolderInfo(path=name, name=name)]

    def iter_messages(
        self,
        folder: str | None = None,
        after_locator: MessageLocator | None = None,
    ) -> Iterator[MessageMetadata]:
        folder_info = self.iter_folders()[0]
        if folder is not None and folder != folder_info.path:
            return

        for locator in self._message_ranges(after_locator):
            try:
                message = self._parse_message(locator)
                yield self._metadata(message, locator, folder_info.path)
            except (OSError, ValueError) as error:
                logger.warning(
                    "Unable to read MBOX message at byte %d in %s: %s",
                    locator.offset,
                    self.path,
                    error,
                )
                yield MessageMetadata(
                    locator=locator,
                    folder=folder_info.path,
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

    def open_message(self, locator: MessageLocator) -> EmailMessage:
        """Parse one message by its source-file byte range."""
        return self._parse_message(locator)

    def open_attachment(self, locator: MessageLocator, index: int) -> Attachment:
        """Load one selected attachment without extracting it to disk."""
        if index < 0:
            raise IndexError("Attachment index must be non-negative")

        message = self.open_message(locator)
        attachments = (
            part
            for part in message.walk()
            if part.get_content_disposition() == "attachment" or part.get_filename()
        )
        for attachment_index, part in enumerate(attachments):
            if attachment_index == index:
                filename = part.get_filename() or f"attachment-{index + 1}"
                return Attachment(
                    filename=filename,
                    content_type=part.get_content_type(),
                    data=part.get_payload(decode=True) or b"",
                )
        raise IndexError(f"No attachment at index {index}")

    def _message_ranges(
        self, after_locator: MessageLocator | None = None
    ) -> Iterator[MessageLocator]:
        """Locate envelope lines and derive message byte ranges."""
        starts: list[int] = []
        with self.path.open("rb") as source:
            if after_locator is not None:
                source.seek(after_locator.offset + after_locator.length)
            while True:
                offset = source.tell()
                line = source.readline()
                if not line:
                    break
                if line.startswith(b"From "):
                    starts.append(offset)

            source_size = source.tell()

        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else source_size
            yield MessageLocator(offset=start, length=end - start)

    def _parse_message(self, locator: MessageLocator) -> EmailMessage:
        """Read and parse one MBOX range, excluding its envelope line."""
        if locator.offset < 0 or locator.length <= 0:
            raise ValueError("Invalid MBOX message locator")

        with self.path.open("rb") as source:
            source.seek(locator.offset)
            raw = source.read(locator.length)

        if len(raw) != locator.length:
            raise ValueError("MBOX message range is truncated")

        envelope, separator, content = raw.partition(b"\n")
        if not separator or not envelope.startswith(b"From "):
            raise ValueError("MBOX message range has no valid envelope line")

        return BytesParser(policy=policy.default).parsebytes(content)

    @staticmethod
    def _metadata(
        message: EmailMessage, locator: MessageLocator, folder: str
    ) -> MessageMetadata:
        """Normalize message headers into indexable metadata."""
        from_addresses = message_addresses(message, "From")
        to_addresses = MboxReader._addresses(message, "To")
        cc_addresses = MboxReader._addresses(message, "Cc")
        bcc_addresses = MboxReader._addresses(message, "Bcc")
        status = (message.get("X-Mozilla-Status") or "").strip()
        try:
            deleted = bool(int(status, 16) & 0x0008)
        except ValueError:
            deleted = False

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
            attachment_names=tuple(
                part.get_filename() or "attachment"
                for part in message.walk()
                if part.get_content_disposition() == "attachment"
                or part.get_filename()
            ),
            attachment_content_types=tuple(
                part.get_content_type()
                for part in message.walk()
                if part.get_content_disposition() == "attachment"
                or part.get_filename()
            ),
            labels=MboxReader._gmail_labels(message),
            deleted=deleted,
            text_body=message_body_text(message),
            participant_names=message_participant_names(message),
        )

    @staticmethod
    def _addresses(message: EmailMessage, header: str) -> tuple[str, ...]:
        """Extract mailbox addresses from one recipient header."""
        return message_addresses(message, header)

    @staticmethod
    def _gmail_labels(message: EmailMessage) -> tuple[str, ...]:
        """Parse Gmail's comma-separated label header with CSV quoting."""
        labels: list[str] = []
        for header in message.get_all("X-Gmail-Labels", []):
            try:
                labels.extend(label.strip() for label in next(csv.reader([header])))
            except (csv.Error, StopIteration):
                labels.append(str(header).strip())
        return tuple(label for label in labels if label)
