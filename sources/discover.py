"""Content-based discovery for supported mailbox files."""

__author__ = "Bálint Szabolcs"

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PST_SIGNATURE = b"!BDN"
HASH_CHUNK_SIZE = 1024 * 1024
MBOX_SUFFIXES = {".mbox", ".mbx", ".mbs"}
EML_HEADER_LINE = re.compile(rb"^[!-9;-~]+:")
EML_KNOWN_HEADERS = {
    b"from",
    b"to",
    b"cc",
    b"date",
    b"subject",
    b"message-id",
    b"received",
    b"return-path",
    b"mime-version",
    b"content-type",
    b"delivered-to",
    b"reply-to",
    b"in-reply-to",
    b"references",
}
EML_REQUIRED_HEADERS = {b"from", b"date", b"subject", b"message-id", b"received"}

FileSignature = tuple[int, ...]
HashProgress = Callable[[int, int], None]


@dataclass(frozen=True)
class DiscoveredSource:
    """A detected source file and its integrity metadata."""

    path: Path
    kind: str
    size: int
    sha256: str
    folder: str
    signature: FileSignature | None = None


def discover_sources(path: str | Path) -> list[DiscoveredSource]:
    """Find supported sources recursively, identifying files by their contents.

    EML files in one directory form a single source, so that a large export
    folder does not become thousands of separately hashed sources.
    """
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(root)

    candidates = [root] if root.is_file() else sorted(
        candidate for candidate in root.rglob("*") if candidate.is_file()
    )
    discovered: list[DiscoveredSource] = []
    eml_directories: dict[Path, list[Path]] = {}
    for candidate in candidates:
        kind = detect_kind(candidate)
        if kind is None:
            continue
        if kind == "eml" and not root.is_file():
            eml_directories.setdefault(candidate.parent, []).append(candidate)
            continue
        sha256, signature = hash_source(candidate, kind)
        discovered.append(
            DiscoveredSource(
                path=candidate,
                kind=kind,
                size=candidate.stat().st_size,
                sha256=sha256,
                folder=_folder_path(candidate, root, kind),
                signature=signature,
            )
        )
    for directory, files in sorted(eml_directories.items()):
        sha256, signature = hash_source(directory, "emldir")
        relative = directory.relative_to(root)
        discovered.append(
            DiscoveredSource(
                path=directory,
                kind="emldir",
                size=sum(file.stat().st_size for file in files),
                sha256=sha256,
                folder=relative.as_posix() if relative.parts else root.name,
                signature=signature,
            )
        )
    return discovered


def detect_kind(path: Path) -> str | None:
    """Recognize mailbox formats from their signatures, not just extensions."""
    try:
        with path.open("rb") as source:
            prefix = source.read(8192)
    except OSError:
        return None

    if prefix.startswith(PST_SIGNATURE):
        return "ost" if path.suffix.casefold() == ".ost" else "pst"
    if prefix.startswith(b"From "):
        return "mbox"
    if _looks_like_eml(prefix):
        return "eml"
    return None


def eml_files(directory: Path) -> list[Path]:
    """Return the EML messages directly inside one directory in stable order."""
    return sorted(
        candidate
        for candidate in directory.iterdir()
        if candidate.is_file() and detect_kind(candidate) == "eml"
    )


def sha256_file(path: str | Path, progress: Callable[[int], None] | None = None) -> str:
    """Hash a file in bounded memory without writing to it."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
            if progress is not None:
                progress(len(chunk))
    return digest.hexdigest()


def source_signature(path: str | Path, kind: str) -> FileSignature:
    """Identify a source's current on-disk state without reading its content."""
    source_path = Path(path)
    if kind != "emldir":
        return _stat_signature(source_path.stat())
    signature: list[int] = []
    for file in eml_files(source_path):
        signature.extend(_stat_signature(file.stat()))
    return (len(signature), *signature)


def sha256_source(
    path: str | Path, kind: str, progress: HashProgress | None = None
) -> str:
    """Hash a source file, or an EML directory through a manifest of file hashes.

    ``progress`` receives the hashed and the total byte count.
    """
    source_path = Path(path)
    files = [source_path] if kind != "emldir" else eml_files(source_path)
    total = sum(file.stat().st_size for file in files)
    done = 0

    def advance(size: int) -> None:
        nonlocal done
        done += size
        if progress is not None:
            progress(done, total)

    if kind != "emldir":
        return sha256_file(source_path, advance)
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.name.encode("utf-8"))
        digest.update(b"\t")
        digest.update(sha256_file(file, advance).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def hash_source(
    path: str | Path, kind: str, progress: HashProgress | None = None
) -> tuple[str, FileSignature]:
    """Hash a source and return the signature that was stable during hashing."""
    before = source_signature(path, kind)
    sha256 = sha256_source(path, kind, progress)
    after = source_signature(path, kind)
    if before != after:
        raise OSError(f"A forrás a hash-elés közben megváltozott: {path}")
    return sha256, after


def _stat_signature(file_stat: os.stat_result) -> FileSignature:
    """Build a local file identity from size and change metadata."""
    return (
        int(file_stat.st_dev),
        int(file_stat.st_ino),
        int(file_stat.st_size),
        int(file_stat.st_mtime_ns),
    )


def _looks_like_eml(prefix: bytes) -> bool:
    """Accept EML only when the file starts with a plausible RFC 5322 header block."""
    parts = re.split(rb"\r?\n\r?\n", prefix, maxsplit=1)
    header_block = parts[0]
    lines = header_block.splitlines()
    if not lines or not EML_HEADER_LINE.match(lines[0]):
        return False
    names: set[bytes] = set()
    for line in lines:
        if line[:1] in (b" ", b"\t"):
            continue
        if not EML_HEADER_LINE.match(line):
            return False
        names.add(line.split(b":", 1)[0].strip().lower())
    # A complete header block within the sample, or a long one that fills it.
    if len(parts) != 2 and len(prefix) < 8192:
        return False
    return len(names & EML_KNOWN_HEADERS) >= 2 and bool(names & EML_REQUIRED_HEADERS)


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
