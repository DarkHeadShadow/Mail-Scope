__author__ = "Bálint Szabolcs"

import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from pathlib import Path

from sources.content import message_body_text
from sources.discover import discover_sources
from sources.eml_reader import EmlReader
from sources.models import MessageLocator
from sources.reader_factory import open_source


class EmlReaderTests(unittest.TestCase):
    def test_extracts_visible_search_text_from_html_body(self) -> None:
        """Index HTML text but omit hidden head, script, and style content."""
        message = EmailMessage()
        message["From"] = "antal@pelda.hu"
        message["Subject"] = "HTML"
        message.set_content(
            "<html><head><title>Rejtett cím</title></head>"
            "<body><p>Látható tartalom</p>"
            "<script>rejtett script</script>"
            "<style>rejtett stílus</style></body></html>",
            subtype="html",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "html.eml"
            path.write_bytes(message.as_bytes(policy=policy.SMTP))
            metadata = list(EmlReader(path).iter_messages())[0]

        self.assertIn("Látható tartalom", metadata.text_body)
        self.assertNotIn("Rejtett cím", metadata.text_body)
        self.assertNotIn("rejtett script", metadata.text_body)
        self.assertNotIn("rejtett stílus", metadata.text_body)

    def test_hidden_element_does_not_hide_following_html_text(self) -> None:
        message = EmailMessage()
        message["From"] = "antal@pelda.hu"
        message["Subject"] = "Hidden HTML"
        message.set_content(
            "<html><body><p>Előtte látható</p>"
            '<div hidden><span>Elrejtett előnézet</span></div>'
            "<p>Utána is látható</p></body></html>",
            subtype="html",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hidden.html.eml"
            path.write_bytes(message.as_bytes(policy=policy.SMTP))
            metadata = list(EmlReader(path).iter_messages())[0]

        self.assertIn("Előtte látható", metadata.text_body)
        self.assertNotIn("Elrejtett előnézet", metadata.text_body)
        self.assertIn("Utána is látható", metadata.text_body)

    def test_empty_plain_part_falls_back_to_html_and_nested_hidden_stays_hidden(self) -> None:
        message = EmailMessage()
        message["Subject"] = "Hírlevél"
        message.set_content("")
        message.add_alternative(
            "<html><body><div hidden><div>rejtett</div>még rejtett</div>"
            "<p>Látható szerződés.</p></body></html>",
            subtype="html",
        )

        text = message_body_text(message)

        self.assertIn("Látható szerződés.", text)
        self.assertNotIn("rejtett", text)

    def test_directory_of_eml_files_is_one_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            export = root / "Antal_PST_EXTRACT"
            export.mkdir()
            for index in range(3):
                (export / f"{index:03d}.eml").write_bytes(
                    b"From: antal@pelda.hu\r\nTo: anna@pelda.hu\r\n"
                    b"Subject: Teszt " + str(index).encode() + b"\r\n"
                    b"Date: Sat, 1 Jan 2022 00:00:00 +0000\r\n\r\nTorzs.\r\n"
                )
            (root / "notes.json").write_bytes(b'{"from": "x", "date": "y"}\n\n{}')
            (root / "readme.md").write_bytes(b"# Cim\n\nSzoveg: valami\n")
            (root / "note.txt").write_bytes(b"Note: valami\nTodo: mas\n\ntovabbi szoveg\n")

            sources = discover_sources(root)

            self.assertEqual([(source.kind, source.folder) for source in sources], [("emldir", "Antal_PST_EXTRACT")])
            reader = open_source(sources[0])
            messages = list(reader.iter_messages())
            self.assertEqual([message.subject for message in messages], ["Teszt 0", "Teszt 1", "Teszt 2"])
            resumed = list(reader.iter_messages(after_locator=messages[0].locator))
            self.assertEqual([message.subject for message in resumed], ["Teszt 1", "Teszt 2"])
            self.assertEqual(
                reader.open_message_bytes(messages[1].locator),
                (export / "001.eml").read_bytes(),
            )
            with self.assertRaises(ValueError):
                reader.open_message_bytes(
                    # Built with pathlib so the separator matches the running OS.
                    MessageLocator(offset=0, length=10, folder_path=str(Path("..") / "notes.json"))
                )

    def test_reads_headers_and_opens_attachment_on_demand(self) -> None:
        message = EmailMessage()
        message["From"] = "Teszt Antal <antal@pelda.hu>"
        message["To"] = "Teszt Anna <anna@pelda.hu>"
        message["Subject"] = "Kitalált EML"
        message["Message-ID"] = "<sample@example.test>"
        message.set_content("Minta levél.")
        message.add_attachment(
            b"csak tesztadat",
            maintype="text",
            subtype="plain",
            filename="minta.txt",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "minta.eml"
            original = message.as_bytes(policy=policy.SMTP)
            path.write_bytes(original)
            reader = EmlReader(path, folder_name="Példák")

            metadata = list(reader.iter_messages())[0]
            attachment = reader.open_attachment(metadata.locator, 0)

            self.assertEqual(metadata.folder, "Példák")
            self.assertEqual(metadata.subject, "Kitalált EML")
            self.assertEqual(metadata.from_addresses, ("antal@pelda.hu",))
            self.assertEqual(metadata.recipient_addresses, ("anna@pelda.hu",))
            self.assertEqual(metadata.message_id, "<sample@example.test>")
            self.assertEqual(attachment.filename, "minta.txt")
            self.assertEqual(attachment.data, b"csak tesztadat")
            self.assertEqual(path.read_bytes(), original)

    def test_message_bytes_preserve_original_eml_encoding_and_line_endings(self) -> None:
        original = (
            b"From: antal@pelda.hu\r\n"
            b"Subject: =?utf-8?q?Teszt_=C3=A1rnyalat?=\r\n"
            b"X-Folded: first\r\n\tcontinued\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Body with trailing spaces.  \r\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "raw.eml"
            path.write_bytes(original)
            reader = EmlReader(path)
            locator = list(reader.iter_messages())[0].locator

            self.assertEqual(reader.open_message_bytes(locator), original)


if __name__ == "__main__":
    unittest.main()
