"""Shared models for source readers."""

__author__ = "Bálint Szabolcs"

from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class MessageLocator:
    """A stable position in an original file or folder-based mailbox store."""

    offset: int
    length: int
    folder_path: str | None = None
    item_index: int | None = None
    item_id: str | None = None


@dataclass(frozen=True)
class FolderInfo:
    """A folder exposed by a source reader."""

    path: str
    name: str


@dataclass(frozen=True)
class Attachment:
    """Attachment metadata and content, loaded only when requested."""

    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class MessageMetadata:
    """Searchable message metadata and its source location."""

    locator: MessageLocator
    folder: str
    subject: str
    from_addresses: tuple[str, ...]
    to_addresses: tuple[str, ...]
    cc_addresses: tuple[str, ...]
    bcc_addresses: tuple[str, ...]
    date: str | None
    message_id: str | None
    in_reply_to: str | None
    references: str | None
    attachment_names: tuple[str, ...]
    text_body: str = ""
    attachment_content_types: tuple[str, ...] = ()
    participant_names: tuple[tuple[str, str, str], ...] = ()
    labels: tuple[str, ...] = ()
    deleted: bool = False
    item_type: str = "mail"
    error: str | None = None
    address_aliases: tuple[tuple[str, str], ...] = ()

    @property
    def recipient_addresses(self) -> tuple[str, ...]:
        """Return all recipient addresses while preserving the To/Cc/Bcc roles."""
        return self.to_addresses + self.cc_addresses + self.bcc_addresses


@runtime_checkable
class SourceReader(Protocol):
    """Common interface implemented by mailbox source readers."""

    path: Path

    def iter_folders(self) -> list[FolderInfo]: ...

    def iter_messages(
        self,
        folder: str | None = None,
        after_locator: MessageLocator | None = None,
    ) -> Iterator[MessageMetadata]: ...

    def open_message(self, locator: MessageLocator) -> EmailMessage: ...

    def open_message_bytes(self, locator: MessageLocator) -> bytes: ...

    def open_attachment(self, locator: MessageLocator, index: int) -> Attachment: ...

    def close(self) -> None: ...
