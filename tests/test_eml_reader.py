import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from pathlib import Path

from sources.eml_reader import EmlReader


class EmlReaderTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
