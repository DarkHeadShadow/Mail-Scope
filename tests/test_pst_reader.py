__author__ = "Bálint Szabolcs"

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sources.pst_reader import PstReader


class FakeEntry:
    """Represent a synthetic MAPI property entry."""

    def __init__(self, entry_type: int, value: str | int) -> None:
        """Store an entry's property identifier and value."""
        self._entry_type = entry_type
        self._value = value

    def get_entry_type(self) -> int:
        """Return the MAPI property identifier."""
        return self._entry_type

    def get_data_as_string(self) -> str:
        """Return this entry's value as text."""
        return str(self._value)

    def get_data_as_integer(self) -> int:
        """Return this entry's value as an integer."""
        return int(self._value)


class FakeRecordSet:
    """Group recipient properties as libpff record sets do."""

    def __init__(self, entries: list[FakeEntry]) -> None:
        """Keep the synthetic recipient properties in order."""
        self._entries = entries

    def get_number_of_entries(self) -> int:
        """Return the count of recipient properties."""
        return len(self._entries)

    def get_entry(self, index: int) -> FakeEntry:
        """Return the indexed property entry."""
        return self._entries[index]


class FakeMessage:
    """Provide an Outlook message with synthetic transport headers."""

    identifier = 24
    subject = "Kitalált PST-levél"
    sender_name = "antal@pelda.hu"
    transport_headers = (
        "From: antal@pelda.hu\r\n"
        "Subject: Kitalált PST-levél\r\n"
        "Message-ID: <pst@example.test>\r\n\r\n"
    )
    plain_text_body = "Kitalált tartalom."
    html_body = ""

    def get_number_of_record_sets(self) -> int:
        """Report the synthetic recipient table."""
        return 1

    def get_record_set(self, index: int) -> FakeRecordSet:
        """Return the synthetic recipient row."""
        if index != 0:
            raise IndexError(index)
        return FakeRecordSet(
            [
                FakeEntry(0x0C15, 1),
                FakeEntry(0x3001, "Teszt Anna"),
                FakeEntry(0x39FE, "anna@pelda.hu"),
            ]
        )

    def get_number_of_attachments(self) -> int:
        """Report the synthetic attachment."""
        return 1

    def get_attachment(self, index: int) -> "FakeAttachment":
        """Return the synthetic attachment."""
        if index != 0:
            raise IndexError(index)
        return FakeAttachment()


class FakeAttachment:
    """Represent attachment metadata and bytes returned by libpff."""

    def get_size(self) -> int:
        """Return the attachment byte length."""
        return len(b"csak tesztadat")

    def seek_offset(self, offset: int, whence: int) -> None:
        """Accept the requested attachment seek."""
        self.offset = offset
        self.whence = whence

    def read_buffer(self, size: int) -> bytes:
        """Return synthetic attachment bytes."""
        return b"csak tesztadat"[:size]

    def get_number_of_record_sets(self) -> int:
        """Report attachment metadata properties."""
        return 1

    def get_record_set(self, index: int) -> FakeRecordSet:
        """Return the attachment filename and content type."""
        if index != 0:
            raise IndexError(index)
        return FakeRecordSet(
            [
                FakeEntry(0x3707, "minta.txt"),
                FakeEntry(0x370E, "text/plain"),
            ]
        )


class FakeFolder:
    """Represent one folder in the synthetic Outlook store."""

    name = "Inbox"

    def get_number_of_sub_folders(self) -> int:
        """Report the number of child folders."""
        return 0

    def get_sub_folder(self, index: int) -> "FakeFolder":
        """Return a child folder or raise for an invalid index."""
        raise IndexError(index)

    def get_number_of_sub_messages(self) -> int:
        """Report the number of messages in the folder."""
        return 1

    def get_sub_message(self, index: int) -> FakeMessage:
        """Return the indexed synthetic message."""
        if index != 0:
            raise IndexError(index)
        return FakeMessage()


class FakeRoot:
    """Represent the store root with one Inbox folder."""

    def get_number_of_sub_folders(self) -> int:
        """Report the root's child folder count."""
        return 1

    def get_sub_folder(self, index: int) -> FakeFolder:
        """Return the one synthetic Inbox folder."""
        if index != 0:
            raise IndexError(index)
        return FakeFolder()


class FakeStore:
    """Represent a short-lived read-only libpff file handle."""

    def open(self, path: str) -> None:
        """Accept the path passed by the reader."""
        self.path = path

    def close(self) -> None:
        """Close the synthetic file handle."""

    def get_root_folder(self) -> FakeRoot:
        """Return the synthetic mailbox root."""
        return FakeRoot()


class PstReaderTests(unittest.TestCase):
    def test_enumerates_and_opens_a_pst_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "synthetic.pst"
            path.write_bytes(b"!BDN" + b"\0" * 16)
            reader = PstReader(path)
            reader._pypff = SimpleNamespace(file=FakeStore)

            folders = reader.iter_folders()
            metadata = list(reader.iter_messages())[0]
            resumed = list(
                reader.iter_messages(after_locator=metadata.locator)
            )
            message = reader.open_message(metadata.locator)
            attachment = reader.open_attachment(metadata.locator, 0)

            self.assertEqual([(folder.path, folder.name) for folder in folders], [("Inbox", "Inbox")])
            self.assertEqual(metadata.subject, "Kitalált PST-levél")
            self.assertEqual(metadata.from_addresses, ("antal@pelda.hu",))
            self.assertEqual(metadata.recipient_addresses, ("anna@pelda.hu",))
            self.assertEqual(metadata.message_id, "<pst@example.test>")
            self.assertEqual(metadata.attachment_names, ("minta.txt",))
            self.assertEqual(metadata.attachment_content_types, ("text/plain",))
            self.assertEqual(resumed, [])
            self.assertEqual(message["To"], "Teszt Anna <anna@pelda.hu>")
            self.assertEqual(message.get_content(), "Kitalált tartalom.\n")
            self.assertEqual(attachment.filename, "minta.txt")
            self.assertEqual(attachment.content_type, "text/plain")
            self.assertEqual(attachment.data, b"csak tesztadat")


if __name__ == "__main__":
    unittest.main()
