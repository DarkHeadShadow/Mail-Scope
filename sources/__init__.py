"""Read-only source readers for Mail-Scope."""

from .mbox_reader import MboxReader
from .eml_reader import EmlReader
from .pst_reader import PstReader
from .models import Attachment, FolderInfo, MessageLocator, MessageMetadata
from .reader_factory import open_source

__all__ = [
    "Attachment",
    "EmlReader",
    "FolderInfo",
    "MboxReader",
    "MessageLocator",
    "MessageMetadata",
    "PstReader",
    "open_source",
]
