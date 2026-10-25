"""Shared models for source readers."""

from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class MessageLocator:
    """A byte range in the original source file."""

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
    recipient_addresses: tuple[str, ...]
    date: str | None
    message_id: str | None
    in_reply_to: str | None
    references: str | None
    attachment_names: tuple[str, ...]
    labels: tuple[str, ...] = ()
    deleted: bool = False
    item_type: str = "mail"
    error: str | None = None


@runtime_checkable
class SourceReader(Protocol):
    """Common interface implemented by mailbox source readers."""

    path: Path

    def iter_folders(self) -> list[FolderInfo]: ...

    def iter_messages(
        self, folder: str | None = None
    ) -> Iterator[MessageMetadata]: ...

    def open_message(self, locator: MessageLocator) -> EmailMessage: ...

    def open_attachment(self, locator: MessageLocator, index: int) -> Attachment: ...
