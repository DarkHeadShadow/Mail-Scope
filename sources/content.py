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
        is_hidden = any(
            name.casefold() == "hidden" for name, _ in _attrs
        ) and tag not in self._VOID_TAGS
        if tag in self._IGNORED_TAGS or is_hidden:
            self.ignored_tags.append(tag)
        elif not self.ignored_tags and tag in self._BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Stop ignoring hidden content when its element closes."""
        if tag in self._IGNORED_TAGS and tag in self.ignored_tags:
            self.ignored_tags.remove(tag)
        elif not self.ignored_tags and tag in self._BLOCK_TAGS:
            self.fragments.append("\n")

    def handle_data(self, data: str) -> None:
        """Keep text only when it is not inside a hidden element."""
        if not self.ignored_tags:
            self.fragments.append(data)


def message_body_text(message: EmailMessage) -> str:
    """Return searchable plain text from the preferred message body part."""
    body_part = message.get_body(preferencelist=("plain", "html"))
    if body_part is None:
        return ""

    try:
        content = body_part.get_content()
    except (LookupError, UnicodeError) as error:
        raise ValueError(f"Unable to decode message body: {error}") from error
    if not isinstance(content, str):
        return ""
    if body_part.get_content_subtype().casefold() != "html":
        return content

    parser = _HtmlTextParser()
    parser.feed(content)
    parser.close()
    return "".join(parser.fragments)


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
