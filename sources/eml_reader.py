"""Read individual RFC 5322 EML files without changing the source."""

__author__ = "Bálint Szabolcs"

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

        try:
            locator = MessageLocator(offset=0, length=self.path.stat().st_size)
            message = self.open_message(locator)
            yield self._metadata(message, locator, folder_info.path)
        except (OSError, ValueError) as error:
            logger.warning("Unable to read EML source %s: %s", self.path, error)
            yield MessageMetadata(
                locator=MessageLocator(offset=0, length=0),
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
        """Parse the requested byte range from this EML source."""
        if locator.offset != 0 or locator.length <= 0:
            raise ValueError("Invalid EML message locator")

        with self.path.open("rb") as source:
            source.seek(locator.offset)
            raw = source.read(locator.length)
        if len(raw) != locator.length:
            raise ValueError("EML message is truncated")
        return BytesParser(policy=policy.default).parsebytes(raw)

    def open_attachment(self, locator: MessageLocator, index: int) -> Attachment:
        """Load a single attachment from the EML file on demand."""
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
                return Attachment(
                    filename=part.get_filename() or f"attachment-{index + 1}",
                    content_type=part.get_content_type(),
                    data=part.get_payload(decode=True) or b"",
                )
        raise IndexError(f"No attachment at index {index}")

    @staticmethod
    def _metadata(
        message: EmailMessage, locator: MessageLocator, folder: str
    ) -> MessageMetadata:
        """Build normalized search metadata from the parsed EML headers."""
        from_addresses = message_addresses(message, "From")
        to_addresses = _addresses(message, "To")
        cc_addresses = _addresses(message, "Cc")
        bcc_addresses = _addresses(message, "Bcc")
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
            text_body=message_body_text(message),
            attachment_content_types=tuple(
                part.get_content_type()
                for part in message.walk()
                if part.get_content_disposition() == "attachment"
                or part.get_filename()
            ),
            participant_names=message_participant_names(message),
        )


def _addresses(message: EmailMessage, header: str) -> tuple[str, ...]:
    """Extract mailbox addresses from one recipient header."""
    return message_addresses(message, header)
