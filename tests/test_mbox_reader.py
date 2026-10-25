__author__ = "Bálint Szabolcs"

import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from pathlib import Path

from sources.mbox_reader import MboxReader


class MboxReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "Inbox"
        self.path.write_bytes(
            (
                "From sender1@example.test Sat Jan  1 00:00:00 2022\n"
                "From: Teszt Antal <antal@pelda.hu>\n"
                "To: Teszt Anna <anna@pelda.hu>\n"
                "Cc: Teszt Béla <bela@pelda.hu>\n"
                "Bcc: Teszt Csilla <csilla@pelda.hu>\n"
                "Subject: Első levél\n"
                "Message-ID: <one@example.test>\n"
                "X-Gmail-Labels: Fontos, Munka\n"
                "X-Mozilla-Status: 0008\n"
                "\n"
                "Szia Anna!\n"
                "From sender2@example.test Sun Jan  2 00:00:00 2022\n"
                "From: Teszt Anna <anna@pelda.hu>\n"
                "To: Teszt Antal <antal@pelda.hu>\n"
                "Subject: Re: Első levél\n"
                "Message-ID: <two@example.test>\n"
                "In-Reply-To: <one@example.test>\n"
                "\n"
                "Szia Antal!\n"
            ).encode("utf-8")
        )
        self.reader = MboxReader(self.path)

    def test_reads_metadata_and_locators_without_changing_source(self) -> None:
        original = self.path.read_bytes()

        messages = list(self.reader.iter_messages())

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].subject, "Első levél")
        self.assertEqual(messages[0].from_addresses, ("antal@pelda.hu",))
        self.assertEqual(
            messages[0].recipient_addresses,
            ("anna@pelda.hu", "bela@pelda.hu", "csilla@pelda.hu"),
        )
        self.assertEqual(messages[0].cc_addresses, ("bela@pelda.hu",))
        self.assertEqual(messages[0].bcc_addresses, ("csilla@pelda.hu",))
        self.assertEqual(messages[0].attachment_content_types, ())
        self.assertIn("Szia Anna!", messages[0].text_body)
        self.assertEqual(messages[0].labels, ("Fontos", "Munka"))
        self.assertTrue(messages[0].deleted)
        self.assertEqual(messages[1].in_reply_to, "<one@example.test>")
        self.assertEqual(self.reader.open_message(messages[1].locator)["Subject"], "Re: Első levél")
        self.assertEqual(self.path.read_bytes(), original)

    def test_folder_filter_and_out_of_range_folder(self) -> None:
        folder = self.reader.iter_folders()[0]

        self.assertEqual(folder.path, "Inbox")
        self.assertEqual(len(list(self.reader.iter_messages(folder.path))), 2)
        self.assertEqual(list(self.reader.iter_messages("Other")), [])

    def test_opens_attachment_on_demand(self) -> None:
        message = EmailMessage()
        message["From"] = "antal@pelda.hu"
        message["To"] = "anna@pelda.hu"
        message["Subject"] = "Melléklet"
        message.set_content("Minta levél.")
        message.add_attachment(
            b"csak tesztadat",
            maintype="text",
            subtype="plain",
            filename="minta.txt",
        )
        self.path.write_bytes(
            b"From sender@example.test Sat Jan  1 00:00:00 2022\n"
            + message.as_bytes(policy=policy.SMTP)
        )

        metadata = list(self.reader.iter_messages())[0]
        attachment = self.reader.open_attachment(metadata.locator, 0)

        self.assertEqual(metadata.attachment_names, ("minta.txt",))
        self.assertEqual(attachment.filename, "minta.txt")
        self.assertEqual(attachment.data, b"csak tesztadat")


if __name__ == "__main__":
    unittest.main()
