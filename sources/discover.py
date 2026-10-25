"""Content-based discovery for supported mailbox files."""

__author__ = "Bálint Szabolcs"

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


PST_SIGNATURE = b"!BDN"
HASH_CHUNK_SIZE = 1024 * 1024
MBOX_SUFFIXES = {".mbox", ".mbx", ".mbs"}
EML_HEADER = re.compile(rb"(?im)^[!-9;-~]+:")


@dataclass(frozen=True)
class DiscoveredSource:
    """A detected source file and its integrity metadata."""

    path: Path
    kind: str
    size: int
    sha256: str
    folder: str


def discover_sources(path: str | Path) -> list[DiscoveredSource]:
    """Find supported sources recursively, identifying files by their contents."""
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(root)

    candidates = [root] if root.is_file() else sorted(
        candidate for candidate in root.rglob("*") if candidate.is_file()
    )
    discovered: list[DiscoveredSource] = []
    for candidate in candidates:
        kind = _detect_kind(candidate)
        if kind is not None:
            discovered.append(
                DiscoveredSource(
                    path=candidate,
                    kind=kind,
                    size=candidate.stat().st_size,
                    sha256=sha256_file(candidate),
                    folder=_folder_path(candidate, root, kind),
                )
            )
    return discovered


def _detect_kind(path: Path) -> str | None:
    """Recognize mailbox formats from their signatures, not just extensions."""
    with path.open("rb") as source:
        prefix = source.read(8192)

    if prefix.startswith(PST_SIGNATURE):
        return "ost" if path.suffix.casefold() == ".ost" else "pst"
    if prefix.startswith(b"From "):
        return "mbox"
    if _looks_like_eml(prefix):
        return "eml"
    return None


def sha256_file(path: str | Path) -> str:
    """Hash a file in bounded memory without writing to it."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _looks_like_eml(prefix: bytes) -> bool:
    """Accept EML only when a plausible header block is present."""
    parts = re.split(rb"\r?\n\r?\n", prefix, maxsplit=1)
    return len(parts) == 2 and bool(EML_HEADER.search(parts[0]))


def _folder_path(candidate: Path, root: Path, kind: str) -> str:
    """Map Thunderbird and Apple Mail filesystem layouts to folder names."""
    parent_parts = list(candidate.parent.parts)
    if root.is_file():
        return candidate.stem if kind in {"mbox", "eml"} else ""

    try:
        relative_parent = candidate.parent.relative_to(root)
        parent_parts = list(relative_parent.parts)
    except ValueError:
        parent_parts = list(candidate.parent.parts)

    folder_parts: list[str] = []
    for part in parent_parts:
        if part.casefold().endswith(".sbd"):
            folder_parts.append(part[:-4])
        elif part.casefold().endswith(".mbox"):
            folder_parts.append(part[:-5])

    if kind == "mbox":
        if candidate.name.casefold() == "mbox":
            if not folder_parts and candidate.parent.name.casefold().endswith(".mbox"):
                folder_parts.append(candidate.parent.name[:-5])
        elif candidate.suffix.casefold() in MBOX_SUFFIXES:
            folder_parts.append(candidate.stem)
        else:
            folder_parts.append(candidate.name)
    elif kind == "eml" and not folder_parts:
        folder_parts.append(candidate.parent.name)

    return "/".join(part for part in folder_parts if part)
