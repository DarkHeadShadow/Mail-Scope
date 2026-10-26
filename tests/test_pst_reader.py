__author__ = "Bálint Szabolcs"

import tempfile
import unittest
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

from sources.pst_reader import PstReader
from sources.models import MessageLocator


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

    def __enter__(self) -> "FakeMessage":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class HeaderlessFakeMessage(FakeMessage):
    """Provide MAPI-only metadata typical of Outlook Sent Items."""

    transport_headers = ""
    sender_name = "Teszt Antal"
    client_submit_time = datetime(2023, 5, 12, 9, 30)

    def get_record_set(self, index: int) -> FakeRecordSet:
        base = super().get_record_set(index)
        return FakeRecordSet(
            base._entries
            + [
                FakeEntry(0x5D01, "antal@pelda.hu"),
                FakeEntry(0x1035, "<sent@example.test>"),
                FakeEntry(0x1042, "<parent@example.test>"),
                FakeEntry(0x1039, "<root@example.test> <parent@example.test>"),
            ]
        )


class MultipartHeaderFakeMessage(FakeMessage):
    """Declare MIME multipart headers while supplying body through MAPI."""

    transport_headers = (
        "From: antal@pelda.hu\r\n"
        "Subject: HTML multipart PST message\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/alternative; boundary="mapi-body"\r\n\r\n'
    )


class NoAttachmentTableFakeMessage(FakeMessage):
    """Mimic libpff raising when an item has no attachment table."""

    def get_number_of_attachments(self) -> int:
        raise OSError("pypff_message_get_number_of_attachments: unable to retrieve")


class Latin2HtmlFakeMessage(FakeMessage):
    """Supply an ISO-8859-2 HTML body without a plain-text body."""

    transport_headers = ""
    plain_text_body = b""
    html_body = (
        '<html><head><meta http-equiv="Content-Type" '
        'content="text/html; charset=iso-8859-2"></head>'
        "<body><p>Kérem a szerződést.</p></body></html>"
    ).encode("iso-8859-2")


class ExchangeOnlyFakeMessage(HeaderlessFakeMessage):
    """Sent item whose sender has only an Exchange DN and recipient has both forms."""

    sender_name = "Teszt Antal"
    legacy = "/O=PELDA/OU=EXCHANGE ADMINISTRATIVE GROUP (FYDIBOHF23SPDLT)/CN=RECIPIENTS/CN=ANNA"

    def get_record_set(self, index: int) -> FakeRecordSet:
        if index != 0:
            raise IndexError(index)
        return FakeRecordSet(
            [
                FakeEntry(0x0C15, 1),
                FakeEntry(0x3001, "Teszt Anna"),
                FakeEntry(0x3003, self.legacy),
                FakeEntry(0x39FE, "anna@pelda.hu"),
                FakeEntry(0x0C1F, "/O=PELDA/OU=EXCHANGE/CN=RECIPIENTS/CN=ANTAL"),
                FakeEntry(0x1035, "<sent-ex@example.test>"),
            ]
        )


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

    def test_body_properties_replace_empty_multipart_transport_headers(self) -> None:
        message = PstReader._as_email_message(MultipartHeaderFakeMessage())

        self.assertEqual(message.get_content_type(), "text/plain")
        self.assertEqual(message.get_content(), "Kitalált tartalom.\n")

    def test_sent_item_metadata_falls_back_to_mapi_properties(self) -> None:
        item = HeaderlessFakeMessage()
        locator = MessageLocator(
            offset=0,
            length=0,
            folder_path="Sent Items",
            item_index=0,
        )
        reader = PstReader.__new__(PstReader)

        metadata = reader._metadata(item, locator, "Sent Items")

        self.assertEqual(metadata.from_addresses, ("antal@pelda.hu",))
        self.assertEqual(metadata.date, "Fri, 12 May 2023 09:30:00 +0000")
        self.assertEqual(metadata.message_id, "<sent@example.test>")
        self.assertEqual(metadata.in_reply_to, "<parent@example.test>")
        self.assertEqual(
            PstReader._as_email_message(item)["From"],
            "Teszt Antal <antal@pelda.hu>",
        )
        self.assertEqual(
            metadata.references,
            "<root@example.test> <parent@example.test>",
        )

    def test_missing_attachment_table_does_not_mark_item_as_failed(self) -> None:
        reader = PstReader.__new__(PstReader)
        locator = MessageLocator(offset=0, length=0, folder_path="Inbox", item_index=0)

        metadata = reader._metadata(NoAttachmentTableFakeMessage(), locator, "Inbox")

        self.assertIsNone(metadata.error)
        self.assertEqual(metadata.attachment_names, ())
        self.assertEqual(metadata.text_body, "Kitalált tartalom.\n")

    def test_html_body_uses_declared_charset(self) -> None:
        reader = PstReader.__new__(PstReader)
        locator = MessageLocator(offset=0, length=0, folder_path="Inbox", item_index=0)

        metadata = reader._metadata(Latin2HtmlFakeMessage(), locator, "Inbox")

        self.assertIn("Kérem a szerződést.", metadata.text_body)

    def test_exchange_only_sender_is_kept_and_smtp_pairs_become_aliases(self) -> None:
        reader = PstReader.__new__(PstReader)
        locator = MessageLocator(offset=0, length=0, folder_path="Sent Items", item_index=0)
        item = ExchangeOnlyFakeMessage()

        metadata = reader._metadata(item, locator, "Sent Items")

        self.assertEqual(
            metadata.from_addresses, ("/O=PELDA/OU=EXCHANGE/CN=RECIPIENTS/CN=ANTAL",)
        )
        self.assertEqual(metadata.to_addresses, ("anna@pelda.hu",))
        self.assertIn((item.legacy.casefold(), "anna@pelda.hu"), metadata.address_aliases)
        self.assertNotIn("Teszt Antal", " ".join(metadata.from_addresses))
        self.assertEqual(metadata.message_id, "<sent-ex@example.test>")
        self.assertEqual(metadata.date, "Fri, 12 May 2023 09:30:00 +0000")

    def test_reconstructed_pst_eml_contains_attachment_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "synthetic.pst"
            path.write_bytes(b"!BDN" + b"\0" * 16)
            reader = PstReader(path)
            item = FakeMessage()
            reader._resolve_item = lambda _locator: item

            raw = reader.open_message_bytes(
                MessageLocator(
                    offset=0,
                    length=0,
                    folder_path="Inbox",
                    item_index=0,
                    item_id="24",
                )
            )
            reconstructed = BytesParser(policy=policy.default).parsebytes(raw)

            self.assertEqual(reconstructed.get_content_type(), "multipart/mixed")
            attachments = [
                part
                for part in reconstructed.walk()
                if part.get_content_disposition() == "attachment"
            ]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0].get_filename(), "minta.txt")
            self.assertEqual(attachments[0].get_payload(decode=True), b"csak tesztadat")


if __name__ == "__main__":
    unittest.main()
