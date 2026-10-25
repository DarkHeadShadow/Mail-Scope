"""Construct the source reader that matches a discovered source."""

from .discover import DiscoveredSource
from .eml_reader import EmlReader
from .mbox_reader import MboxReader
from .models import SourceReader
from .pst_reader import PstReader


def open_source(source: DiscoveredSource) -> SourceReader:
    """Create a reader while preserving the discovered archive folder path."""
    if source.kind == "mbox":
        return MboxReader(source.path, folder_name=source.folder)
    if source.kind == "eml":
        return EmlReader(source.path, folder_name=source.folder)
    if source.kind in {"pst", "ost"}:
        return PstReader(source.path)
    raise ValueError(f"Unsupported source kind: {source.kind}")
