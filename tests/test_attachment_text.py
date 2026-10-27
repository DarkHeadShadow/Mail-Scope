"""Searchable attachment text: extraction, background processing and search."""

__author__ = "Bálint Szabolcs"

import hashlib
import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from pathlib import Path

from attachments.textextract import NO_OCR_NOTE, extract_message
from indexer import PersonDirectory
from server import MailScopeApplication


FIXTURES = Path(__file__).parent / "fixtures" / "attachments"


def _mail(message_id: str, subject: str, attachments: list[tuple[str, str, str]]) -> bytes:
    """A fictional message with fixture attachments (name, MIME type, fixture file)."""
    message = EmailMessage()
    message["From"] = "Teszt Antal <antal@pelda.hu>"
    message["To"] = "Teszt Anna <anna@pelda.hu>"
    message["Subject"] = subject
    message["Date"] = "Sat, 1 Jan 2022 00:00:00 +0000"
    message["Message-ID"] = message_id
    message.set_content("Kitalált levél mellékletekkel.")
    for name, content_type, fixture in attachments:
        maintype, subtype = content_type.split("/")
        message.add_attachment(
            (FIXTURES / fixture).read_bytes(), maintype=maintype, subtype=subtype, filename=name
        )
    return b"From sender@example.test Sat Jan  1 00:00:00 2022\n" + message.as_bytes(policy=policy.SMTP) + b"\n"


class ExtractTests(unittest.TestCase):
    """The worker function, called directly."""

    def test_pages_sheets_nested_files_and_image_pdf(self) -> None:
        """Label PDF pages and sheets, walk archives, and flag a PDF without text."""
        items = [
            (name, "", (FIXTURES / name).read_bytes())
            for name in ("archive.zip", "sample.pdf", "image-only.pdf")
        ]
        results = {item["path"]: item for item in extract_message(items, [])}
        self.assertEqual(results["0"]["status"], "container")
        self.assertEqual(results["0/0"]["name"], "szamla-2023-05.pdf")
        self.assertEqual([segment[:2] for segment in results["0/0"]["segments"]], [["1. oldal", 0], ["2. oldal", 1]])
        self.assertEqual(results["0/2/0"]["name"], "jegyzokonyv.txt")
        self.assertIn("jegyzőkönyv", results["0/2/0"]["segments"][0][2])
        self.assertEqual(results["0/3"]["status"], "unsupported")
        self.assertTrue(results["0/3"]["suspicious"])
        # The same PDF as 0/0: its text is extracted only once per call.
        self.assertEqual(results["1"]["sha256"], results["0/0"]["sha256"])
        self.assertEqual((results["1"]["status"], results["1"]["segments"]), ("known", []))
        self.assertEqual(
            [segment[0] for segment in results["0/1"]["segments"]],
            ["Számítás lap", "Rejtett lap", "Nagyon rejtett lap"],
        )
        self.assertEqual((results["2"]["status"], results["2"]["note"]), ("no_ocr", NO_OCR_NOTE))

    def test_known_files_and_unreadable_attachments_keep_their_place(self) -> None:
        """A known SHA-256 is not extracted again; a missing file keeps the next path."""
        data = (FIXTURES / "sample.docx").read_bytes()
        results = extract_message(
            [("serult.bin", "", None), ("szerzodes.docx", "", data)],
            [hashlib.sha256(data).hexdigest()],
        )
        self.assertEqual([(item["path"], item["status"]) for item in results], [("1", "known")])


class SearchTests(unittest.TestCase):
    """Background extraction and the three searches through the application."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "mellekletek.mbox"
        self.source.write_bytes(
            _mail("<a@example.test>", "Számlák csomagban", [("szamlak.zip", "application/zip", "archive.zip")])
            + _mail("<b@example.test>", "Kötbértábla", [("Kotberszamitas.xlsx", "application/octet-stream", "sample.xlsx")])
            + _mail("<c@example.test>", "Ugyanaz a tábla", [("tabla.xlsx", "application/octet-stream", "sample.xlsx")])
            + _mail("<d@example.test>", "Beszkennelt", [("szkennelt.pdf", "application/pdf", "image-only.pdf")])
            + _mail("<e@example.test>", "Melléklet nélkül", [])
        )
        self.application = MailScopeApplication()
        self.application.select_case(str(self.root / "Ugy"))
        source = self.application.discover(str(self.source))[0]
        self.application.start_index([source["path"]])
        assert self.application.job is not None
        self.assertTrue(self.application.job.wait(30))
        self.addCleanup(self.application.close)
        self.ids = {
            row["subject"]: int(row["message_id"])
            for row in self.application.archive(
                query="", source_id=None, folder_id=None, has_attachments=False, deleted_only=False,
                item_type=None, include_duplicates=False, limit=10, offset=0,
            )
        }

    def _archive(self, query: str) -> dict[str, dict]:
        rows = self.application.archive(
            query=query, source_id=None, folder_id=None, has_attachments=False, deleted_only=False,
            item_type=None, include_duplicates=False, limit=10, offset=0,
        )
        return {row["subject"]: row for row in rows}

    def _enable_and_wait(self) -> dict:
        self.application.set_attachment_text_mode("on")
        job = self.application.attachment_job
        self.assertIsNotNone(job)
        assert job is not None
        self.assertTrue(job.wait(120))
        return self.application.status()["attachment_text"]

    def test_words_in_a_workbook_and_a_zipped_pdf_are_found_with_their_place(self) -> None:
        """Search finds attachment text after the case enables it, and says where."""
        self.assertIsNone(self.application.status()["attachment_text"]["mode"])
        self.assertEqual(self._archive("kesedelmi"), {})
        self.assertIsNone(self.application.attachment_job)

        status = self._enable_and_wait()
        self.assertEqual((status["mode"], status["running"]), ("on", False))
        self.assertEqual((status["total"], status["done"], status["failed"], status["no_ocr"]), (4, 4, 0, 1))

        zipped = self._archive("kesedelmi")
        self.assertEqual(list(zipped), ["Számlák csomagban"])
        self.assertEqual(
            zipped["Számlák csomagban"]["attachment_hit"],
            {"path": "0/0", "filename": "szamla-2023-05.pdf", "location": "1. oldal", "page": 0, "query": "kesedelmi"},
        )
        sheet = self._archive("részszámla")
        self.assertEqual(set(sheet), {"Számlák csomagban", "Kötbértábla", "Ugyanaz a tábla"})
        self.assertEqual(
            sheet["Ugyanaz a tábla"]["attachment_hit"],
            {"path": "0", "filename": "tabla.xlsx", "location": "Számítás lap", "page": 0, "query": "részszámla"},
        )
        self.assertEqual(sheet["Számlák csomagban"]["attachment_hit"]["path"], "0/1")
        # Hidden sheets are searchable too.
        self.assertEqual(self._archive("rejtett")["Kötbértábla"]["attachment_hit"]["location"], "Rejtett lap")
        # A message found by its own text carries no attachment hit.
        self.assertIsNone(self._archive("Kitalált")["Melléklet nélkül"]["attachment_hit"])

        # The workbook is stored once, although three messages carry it.
        workbook = hashlib.sha256((FIXTURES / "sample.xlsx").read_bytes()).hexdigest()
        database = self.application.database
        assert database is not None
        with database.connection() as connection:
            stored = connection.execute(
                "SELECT count(*) FROM attachments_fts WHERE sha256 = ?", (workbook,)
            ).fetchone()[0]
        self.assertEqual(stored, 3)

        detailed = self.application.detailed_search(
            ({"field": "attachment_text", "value": "részszámla"},), operator="AND", limit=10, offset=0,
        )
        self.assertEqual(detailed["total"], 3)
        self.assertTrue(all(row["attachment_hit"] for row in detailed["results"]))
        combined = self.application.detailed_search(
            ({"field": "text", "value": "kesedelmi"},), operator="AND", limit=10, offset=0,
        )
        self.assertEqual([row["subject"] for row in combined["results"]], ["Számlák csomagban"])

        people = [person.id for person in PersonDirectory(database).people()]
        conversation = self.application.conversation(
            person_ids=tuple(people[:2]), mode="between", include_cc_bcc=False,
            date_from="", date_to="", query="kesedelmi", thread_key=None,
        )
        self.assertEqual([message["subject"] for message in conversation["messages"]], ["Számlák csomagban"])
        self.assertEqual(conversation["messages"][0]["attachment_hit"]["location"], "1. oldal")

    def test_switching_off_hides_attachment_text_and_on_does_not_redo_work(self) -> None:
        """Off keeps the stored text but stops searching it; on again needs no new run."""
        self._enable_and_wait()
        first_job = self.application.attachment_job
        self.application.set_attachment_text_mode("off")
        self.assertEqual(self._archive("kesedelmi"), {})
        with self.assertRaisesRegex(ValueError, "ki van kapcsolva"):
            self.application.detailed_search(
                ({"field": "attachment_text", "value": "kesedelmi"},), operator="AND", limit=10, offset=0,
            )
        self.application.set_attachment_text_mode("on")
        self.assertIs(self.application.attachment_job, first_job)
        self.assertEqual(list(self._archive("kesedelmi")), ["Számlák csomagban"])

    def test_messages_indexed_later_are_processed_after_indexing(self) -> None:
        """A finished indexing job starts the extraction for the new messages."""
        self._enable_and_wait()
        later = self.root / "kesobbi.mbox"
        later.write_bytes(_mail("<f@example.test>", "Később érkezett", [("jegyzet.zip", "application/zip", "archive.zip")]))
        source = self.application.discover(str(later))[0]
        self.application.start_index([source["path"]])
        assert self.application.job is not None
        self.assertTrue(self.application.job.wait(30))
        first_job = self.application.attachment_job
        self.assertTrue(self.application.status()["attachment_text"]["running"])
        job = self.application.attachment_job
        assert job is not None
        self.assertIsNot(job, first_job)
        self.assertTrue(job.wait(120))
        self.assertEqual(self.application.status()["attachment_text"]["done"], 5)
        self.assertIn("Később érkezett", self._archive("kesedelmi"))


if __name__ == "__main__":
    unittest.main()
