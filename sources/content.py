"""Shared text extraction helpers for source readers."""

__author__ = "Bálint Szabolcs"

from email.message import EmailMessage
from email.utils import getaddresses
from html.parser import HTMLParser


class _HtmlTextParser(HTMLParser):
    """Collect visible HTML text while ignoring non-visible elements."""

    _IGNORED_TAGS = {"head", "script", "style"}
    _BLOCK_TAGS = {
        "address",
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "li",
        "p",
        "section",
        "tr",
    }
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        """Initialize the parser's text fragments and ignored-element stack."""
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []
        self.ignored_tags: list[str] = []

    def handle_starttag(
        self, tag: str, _attrs: list[tuple[str, str | None]]
    ) -> None:
        """Track hidden elements and separate block-level text."""
        if tag in self._VOID_TAGS:
            if not self.ignored_tags and tag in self._BLOCK_TAGS:
                self.fragments.append("\n")
            return
        is_hidden = any(name.casefold() == "hidden" for name, _ in _attrs)
        if self.ignored_tags or tag in self._IGNORED_TAGS or is_hidden:
            # Track every element opened inside hidden content, so that a
            # nested close tag with the same name does not end hiding early.
            self.ignored_tags.append(tag)
        elif tag in self._BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Stop ignoring hidden content when its element closes."""
        if tag in self.ignored_tags:
            ignored_index = self.ignored_tags[::-1].index(tag)
            ignored_index = len(self.ignored_tags) - ignored_index - 1
            del self.ignored_tags[ignored_index:]
        elif not self.ignored_tags and tag in self._BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_data(self, data: str) -> None:
        """Keep text only when it is not inside a hidden element."""
        if not self.ignored_tags:
            self.fragments.append(data)


def message_body_text(message: EmailMessage) -> str:
    """Return searchable plain text, using HTML when the plain part is empty."""
    for preference in ("plain", "html"):
        body_part = message.get_body(preferencelist=(preference,))
        if body_part is None:
            continue
        try:
            content = body_part.get_content()
        except (LookupError, UnicodeError) as error:
            raise ValueError(f"Unable to decode message body: {error}") from error
        if not isinstance(content, str):
            continue
        if preference == "html":
            content = html_to_text(content)
        if content.strip():
            return content
    return ""


def html_to_text(content: str) -> str:
    """Convert HTML markup to its visible text."""
    parser = _HtmlTextParser()
    parser.feed(content)
    parser.close()
    return "".join(parser.fragments)


def decode_text_bytes(data: bytes, charset: str = "") -> str:
    """Decode mailbox text using BOMs, strict UTF-8, then a declared charset."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace").rstrip("\x00")
    sample = data[:256]
    if sample and sample.count(b"\x00") >= max(2, len(sample) // 6):
        return data.decode("utf-16-le", errors="replace").rstrip("\x00")
    try:
        return data.decode("utf-8").rstrip("\x00")
    except UnicodeDecodeError:
        pass
    for candidate in (charset, "cp1250"):
        if not candidate:
            continue
        try:
            return data.decode(candidate).rstrip("\x00")
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("latin-1").rstrip("\x00")


def message_addresses(message: EmailMessage, header: str) -> tuple[str, ...]:
    """Extract normalized mailbox addresses from one message header."""
    return tuple(
        address
        for _, address in getaddresses(message.get_all(header, []))
        if address
    )


def message_participant_names(
    message: EmailMessage,
) -> tuple[tuple[str, str, str], ...]:
    """Return role/address/display-name tuples from standard message headers."""
    role_headers = (
        ("from", "From"),
        ("to", "To"),
        ("cc", "Cc"),
        ("bcc", "Bcc"),
    )
    participants: list[tuple[str, str, str]] = []
    for role, header in role_headers:
        participants.extend(
            (role, address, display_name)
            for display_name, address in getaddresses(message.get_all(header, []))
            if address
        )
    return tuple(participants)
