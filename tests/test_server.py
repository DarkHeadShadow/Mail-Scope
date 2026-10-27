"""Exercise the local server, case workflow, and original-source reading."""

__author__ = "Bálint Szabolcs"

import hashlib
import http.client
import json
import tempfile
import threading
import time
import unittest
import zipfile
from email import policy
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from attachments.libreoffice import ConversionError
from indexer import IndexDatabase, IndexingService, PersonDirectory
from server import ApiError, MailScopeApplication, create_server
from server.__main__ import main as run_server_main
from sources.discover import discover_sources, hash_source


def _fixture_mail(
    *,
    subject: str = "Kitalált levél a helyi felület tesztjéhez",
    sender: str = "antal@pelda.hu",
    recipient: str = "anna@pelda.hu",
    sent_date: str = "Sat, 1 Jan 2022 00:00:00 +0000",
    message_id: str = "<fixture@example.test>",
    body: str = "Ez egy kitalált üzenettörzs.",
    cc: str | None = None,
    references: str | None = None,
) -> bytes:
    """Create a fictional mailbox message without any real personal data."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    if cc is not None:
        message["Cc"] = cc
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["Date"] = sent_date
    if references is not None:
        message["References"] = references
    message.set_content(body)
    return b"From sender@example.test Sat Jan  1 00:00:00 2022\n" + message.as_bytes(
        policy=policy.SMTP
    )


def _fixture_mbox() -> bytes:
    """Create the one-message synthetic mailbox used by local server tests."""
    return _fixture_mail()


class ServerTests(unittest.TestCase):
    """Verify end-to-end local application behavior with synthetic mail."""

    def setUp(self) -> None:
        """Set up an isolated case, synthetic source, and application state."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        # Resolve 8.3 short names (e.g. C:\Users\RUNNER~1 on CI runners): the
        # application stores resolved case paths and tests compare with them.
        self.root = Path(self.temporary_directory.name).resolve()
        self.case_directory = self.root / "Ugy"
        self.source_path = self.root / "teszt.mbox"
        self.source_bytes = _fixture_mbox()
        self.source_path.write_bytes(self.source_bytes)
        self.application = MailScopeApplication()

    def test_case_workflow_indexes_and_opens_unchanged_source(self) -> None:
        """Open a case, index a detected source, then read its original message."""
        self.application.select_case(str(self.case_directory))
        discovered = self.application.discover(str(self.source_path))
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0]["kind"], "mbox")

        self.application.start_index([discovered[0]["path"]])
        job = self.application.job
        self.assertIsNotNone(job)
        assert job is not None
        self.assertTrue(job.wait(5))
        self.assertEqual(job.result().status, "completed")

        archive_rows = self.application.archive(
            query="",
            source_id=None,
            folder_id=None,
            has_attachments=False,
            deleted_only=False,
            item_type=None,
            include_duplicates=False,
            limit=100,
            offset=0,
        )
        self.assertEqual(len(archive_rows), 1)
        # The full hash recorded after indexing stays valid while the file
        # signature is unchanged, so opening does not re-hash the source.
        with patch("server.app.hash_source", wraps=hash_source) as hashing:
            opened = self.application.open_message(int(archive_rows[0]["message_id"]))
            self.application.open_message(int(archive_rows[0]["message_id"]))
            self.assertEqual(hashing.call_count, 0)
            self.assertEqual(
                opened["headers"]["subject"],
                "Kitalált levél a helyi felület tesztjéhez",
            )
            self.assertIn("kitalált üzenettörzs", opened["body"])
            self.assertEqual(
                hashlib.sha256(self.source_path.read_bytes()).hexdigest(),
                hashlib.sha256(self.source_bytes).hexdigest(),
            )
            self.source_path.write_bytes(self.source_bytes + b"\n")
            with self.assertRaises(ApiError) as raised:
                self.application.open_message(int(archive_rows[0]["message_id"]))
            self.assertEqual(raised.exception.status, 409)
            self.assertEqual(hashing.call_count, 1)
        self.assertTrue((self.case_directory / ".mailscope" / "index.sqlite3").is_file())

    def test_recent_cases_lists_only_existing_folders_from_the_profile_file(self) -> None:
        """Remember opened cases outside the case folder and drop removed folders."""
        recent_file = self.root / "profile" / "Mail-Scope" / "recent.json"
        application = MailScopeApplication(recent_cases_file=recent_file)
        first = self.root / "Elso-ugy"
        second = self.root / "Masodik-ugy"
        application.select_case(str(first))
        application.select_case(str(second))
        missing = self.root / "Torolt-ugy"
        entries = json.loads(recent_file.read_text(encoding="utf-8"))
        entries.append(
            {
                "path": str(missing),
                "name": "Torolt-ugy",
                "source_count": 1,
                "message_count": 2,
                "opened_at": "2026-01-01T00:00:00+00:00",
            }
        )
        recent_file.write_text(json.dumps(entries), encoding="utf-8")

        recent = application.recent_cases()

        self.assertEqual([entry["name"] for entry in recent], ["Masodik-ugy", "Elso-ugy"])
        self.assertEqual(recent[0]["source_count"], 0)
        self.assertEqual(recent[0]["message_count"], 0)
        self.assertFalse(any(path.name == "recent.json" for path in second.rglob("*")))
        self.assertEqual(MailScopeApplication().recent_cases(), [])

        http_server = create_server(application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        port = int(http_server.server_address[1])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/recent-cases")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(
            [entry["path"] for entry in json.loads(response.read())],
            [str(second.resolve()), str(first.resolve())],
        )

    def _index_fixture_source(self) -> int:
        """Open a case, index the fixture MBOX, and return its source ID."""
        self.application.select_case(str(self.case_directory))
        source = self.application.discover(str(self.source_path))[0]
        self.application.start_index([source["path"]])
        assert self.application.job is not None
        self.assertTrue(self.application.job.wait(5))
        return int(self.application.status()["sources"][0]["id"])

    def _wait_for_verification(self, source_id: int) -> dict:
        """Poll status until the background full check of a source finishes."""
        for _ in range(100):
            source = next(
                item for item in self.application.status()["sources"] if item["id"] == source_id
            )
            if source["verification"] and not source["verification"]["running"]:
                return source
            time.sleep(0.05)
        self.fail("Full verification did not finish")

    def test_full_reverification_records_hash_time_and_detects_changes(self) -> None:
        """Re-hash a source in the background and mark a modified file as changed."""
        source_id = self._index_fixture_source()
        expected = hashlib.sha256(self.source_path.read_bytes()).hexdigest()

        started = self.application.start_full_verification(source_id)
        self.assertTrue(started["running"])
        source = self._wait_for_verification(source_id)

        self.assertEqual(source["verification"]["result"], "match")
        self.assertEqual(source["verification"]["sha256"], expected)
        self.assertEqual(source["verification"]["done_bytes"], len(self.source_bytes))
        self.assertEqual(source["last_check_result"], "match")
        self.assertRegex(source["verified_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(source["status"], "completed")
        case_log_text = "Full SHA-256 check matched"
        with self.assertLogs("server.app", level="INFO") as logs:
            self.application.start_full_verification(source_id)
            self._wait_for_verification(source_id)
        self.assertTrue(any(case_log_text in line for line in logs.output))

        self.source_path.write_bytes(self.source_bytes + b"\nmodositott\n")
        self.application.start_full_verification(source_id)
        source = self._wait_for_verification(source_id)

        self.assertEqual(source["verification"]["result"], "mismatch")
        self.assertEqual(source["status"], "source_changed")
        self.assertEqual(source["last_check_result"], "mismatch")
        message_id = int(
            self.application.archive(
                query="", source_id=None, folder_id=None, has_attachments=False,
                deleted_only=False, item_type=None, include_duplicates=False,
                limit=1, offset=0,
            )[0]["message_id"]
        )
        with self.assertRaises(ApiError) as raised:
            self.application.open_message(message_id)
        self.assertEqual(raised.exception.status, 409)

    def test_verification_mode_is_a_recorded_case_decision(self) -> None:
        """Ask per case; strict mode hashes before every open and once per export source."""
        source_id = self._index_fixture_source()
        self.assertIsNone(self.application.status()["verification_mode"])
        with self.assertRaises(ValueError):
            self.application.set_verification_mode("sometimes")
        message_id = int(
            self.application.archive(
                query="", source_id=None, folder_id=None, has_attachments=False,
                deleted_only=False, item_type=None, include_duplicates=False,
                limit=1, offset=0,
            )[0]["message_id"]
        )

        self.assertEqual(
            self.application.set_verification_mode("signature")["verification_mode"], "signature"
        )
        with patch("server.app.hash_source", wraps=hash_source) as hashing:
            self.application.open_message(message_id)
            self.assertEqual(hashing.call_count, 0)

        self.application.set_verification_mode("full")
        reopened = MailScopeApplication()
        reopened.select_case(str(self.case_directory))
        self.assertEqual(reopened.status()["verification_mode"], "full")
        with patch("server.app.hash_source", wraps=hash_source) as hashing:
            self.application.open_message(message_id)
            self.application.open_message(message_id)
            self.assertEqual(hashing.call_count, 2)
            archive = self.application._eml_zip([(message_id, "a"), (message_id, "b")])
            self.assertEqual(hashing.call_count, 3)
        with zipfile.ZipFile(BytesIO(archive)) as exported:
            self.assertIn("teljes SHA-256 újraszámítás", exported.read("MANIFEST.txt").decode("utf-8"))
        self.assertEqual(
            next(s for s in self.application.status()["sources"] if s["id"] == source_id)[
                "last_check_result"
            ],
            "match",
        )

    def test_people_are_created_automatically_and_edited_over_http(self) -> None:
        """Index, find automatic people, and rename one through the POST route."""
        self._index_fixture_source()
        people = self.application.people()["people"]
        self.assertTrue(people)
        self.assertTrue(all(person["origin"] == "auto" for person in people))
        target = people[0]
        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        port = int(http_server.server_address[1])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(connection.close)
        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
        connection.request(
            "POST",
            f"/api/people/{target['id']}/rename",
            body=json.dumps({"display_name": "Átnevezett Személy"}),
            headers=headers,
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        renamed = next(p for p in json.loads(response.read())["people"] if p["id"] == target["id"])
        self.assertEqual((renamed["display_name"], renamed["origin"]), ("Átnevezett Személy", "manual"))
        connection.request("POST", "/api/people/999999/delete", body="{}", headers=headers)
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 400)

    def test_attachment_flag_search_filter_csv_and_saved_search(self) -> None:
        """Return attachment data, filter with has_attachment, and export it to CSV."""
        message = EmailMessage()
        message["From"] = "Teszt Antal <antal@pelda.hu>"
        message["To"] = "Teszt Anna <anna@pelda.hu>"
        message["Subject"] = "Mellékletes levél"
        message["Date"] = "Sat, 1 Jan 2022 00:00:00 +0000"
        message["Message-ID"] = "<att-server@example.test>"
        message.set_content("Szerződés melléklettel.")
        for name in ("szerzodes.pdf", "kotber.xlsx"):
            message.add_attachment(b"kitalalt", maintype="application", subtype="octet-stream", filename=name)
        self.source_path.write_bytes(
            self.source_bytes
            + b"\nFrom sender@example.test Sat Jan  1 00:00:00 2022\n"
            + message.as_bytes(policy=policy.SMTP)
        )
        self.source_bytes = self.source_path.read_bytes()
        self._index_fixture_source()
        groups = (
            {"operator": "AND", "conditions": [{"field": "from", "value": "pelda.hu"}]},
            {"operator": "AND", "conditions": [{"field": "subject", "value": "nincs ilyen"}]},
        )

        everything = self.application.detailed_search(groups, operator="OR", limit=50, offset=0)
        only = self.application.detailed_search(
            groups, operator="OR", limit=50, offset=0, has_attachment=True
        )
        self.assertGreater(everything["total"], only["total"])
        self.assertEqual(only["total"], 1)
        hit = only["results"][0]
        self.assertEqual(hit["attachment_count"], 2)
        self.assertEqual(hit["attachment_names"], ["kotber.xlsx", "szerzodes.pdf"])
        archive = self.application.archive(
            query="", source_id=None, folder_id=None, has_attachments=False,
            deleted_only=False, item_type=None, include_duplicates=False, limit=10, offset=0,
        )
        self.assertEqual(
            sorted(row["attachment_count"] for row in archive), [0] * (len(archive) - 1) + [2]
        )

        csv_bytes, _, _ = self.application.detailed_search_export(
            groups, operator="OR", export_format="csv", has_attachment=True
        )
        lines = csv_bytes.decode("utf-8-sig").splitlines()
        self.assertTrue(lines[0].endswith(",Mellékletek száma,Mellékletek neve"))
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith(",2,kotber.xlsx; szerzodes.pdf"))

        self.application.save_search("Csak mellékletes", groups, "OR", has_attachment=True)
        saved = next(s for s in self.application.saved_searches() if s["name"] == "Csak mellékletes")
        self.assertIs(saved["query"]["has_attachment"], True)

        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        port = int(http_server.server_address[1])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(connection.close)
        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
        for value, status in ((True, 200), ("igen", 400)):
            connection.request(
                "POST",
                "/api/search",
                body=json.dumps({"groups": list(groups), "operator": "OR", "has_attachment": value}),
                headers=headers,
            )
            response = connection.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, status)
        self.assertIn("logikai érték", body["error"])

    def test_attachment_viewer_backend(self) -> None:
        """List, describe, preview, save and save-all attachments from the source."""
        from PIL import Image

        png = BytesIO()
        Image.new("RGB", (6, 4), (30, 160, 90)).save(png, format="PNG")
        files = (
            ("abra.png", "image", "png", png.getvalue()),
            ("jegyzet.txt", "text", "plain", "Árvíztűrő tükörfúrógép.\n".encode("cp1250")),
            ("tabla.csv", "text", "csv", "név;összeg\nTeszt Anna;100\n".encode("utf-8")),
            ("fizetes_reszletek.pdf", "application", "pdf", b"MZ\x90\x00" + b"\x00" * 64),
        )

        def mail(message_id: str, subject: str, attachments: tuple) -> bytes:
            message = EmailMessage()
            message["From"] = "Teszt Antal <antal@pelda.hu>"
            message["To"] = "Teszt Anna <anna@pelda.hu>"
            message["Subject"] = subject
            message["Date"] = "Sat, 1 Jan 2022 00:00:00 +0000"
            message["Message-ID"] = message_id
            message.set_content("Mellékletek.")
            for name, maintype, subtype, data in attachments:
                message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
            return b"From sender@example.test Sat Jan  1 00:00:00 2022\n" + message.as_bytes(policy=policy.SMTP) + b"\n"

        self.source_path.write_bytes(
            mail("<att-a@example.test>", "Négy melléklet", files)
            + mail("<att-b@example.test>", "Továbbítva", files[:1])
        )
        self.source_bytes = self.source_path.read_bytes()
        self._index_fixture_source()
        rows = {
            row["subject"]: row
            for row in self.application.archive(
                query="", source_id=None, folder_id=None, has_attachments=True,
                deleted_only=False, item_type=None, include_duplicates=False, limit=10, offset=0,
            )
        }
        first = int(rows["Négy melléklet"]["message_id"])
        second = int(rows["Továbbítva"]["message_id"])
        # The executable name is flagged before any analysis only by its real
        # content; the name itself looks harmless here.
        self.assertEqual(rows["Négy melléklet"]["suspicious_attachments"], [])

        listing = self.application.message_attachments(first)
        self.assertEqual([a["name"] for a in listing["attachments"]], [f[0] for f in files])
        views = {a["name"]: (a["real_kind"], a["view"], bool(a["warnings"])) for a in listing["attachments"]}
        self.assertEqual(views["abra.png"], ("png", "image", False))
        self.assertEqual(views["jegyzet.txt"], ("text", "text", False))
        self.assertEqual(views["tabla.csv"], ("text", "table", False))
        self.assertEqual(views["fizetes_reszletek.pdf"], ("exe", "none", True))
        rows_after = {
            row["subject"]: row
            for row in self.application.archive(
                query="", source_id=None, folder_id=None, has_attachments=True,
                deleted_only=False, item_type=None, include_duplicates=False, limit=10, offset=0,
            )
        }
        self.assertEqual(rows_after["Négy melléklet"]["suspicious_attachments"], ["fizetes_reszletek.pdf"])

        self.application.message_attachments(second)
        info = self.application.attachment_info(first, "0")
        self.assertEqual(info["sha256"], hashlib.sha256(files[0][3]).hexdigest())
        self.assertEqual([item["message_id"] for item in info["same_elsewhere"]], [second])
        self.assertIn("1. melléklet", info["location"])

        text = self.application.attachment_preview(first, "1", 0)
        self.assertEqual(text["encoding"], "Windows-1250")
        self.assertIn("Árvíztűrő", text["text"])
        table = self.application.attachment_preview(first, "2", 0)
        self.assertEqual(table["rows"], [["név", "összeg"], ["Teszt Anna", "100"]])
        image = self.application.attachment_preview(first, "0", 0)
        self.assertEqual((image["width"], image["height"]), (6, 4))
        self.assertTrue(self.application.attachment_image(first, "0", 0).startswith(b"\x89PNG"))
        none = self.application.attachment_preview(first, "3", 0)
        self.assertEqual(none["kind"], "none")
        self.assertIn("Futtatható", none["reason"])
        cache = self.case_directory / ".mailscope" / "cache" / "attachments" / info["sha256"]
        self.assertTrue(cache.is_dir())

        target = self.root / "mentes" / "kimentett.txt"
        target.parent.mkdir()
        with patch("server.app._save_path_dialog", return_value=str(target)):
            saved = self.application.save_attachment(first, "1")
        self.assertEqual(saved["saved_path"], str(target))
        self.assertEqual(target.read_bytes(), files[1][3])
        self.assertEqual(
            (self.root / "mentes" / "kimentett.txt.sha256").read_text(encoding="utf-8"),
            f"{hashlib.sha256(files[1][3]).hexdigest()}  kimentett.txt\n",
        )
        self.assertIn("Forrás SHA-256", (self.root / "mentes" / "kimentett.txt.eredet.txt").read_text(encoding="utf-8"))
        with patch("server.app._save_path_dialog", return_value=""):
            self.assertEqual(self.application.save_attachment(first, "1"), {"cancelled": True})

        bundle = self.root / "mentes" / "osszes.zip"
        with patch("server.app._save_path_dialog", return_value=str(bundle)):
            self.application.save_all_attachments(first)
        with zipfile.ZipFile(bundle) as archive:
            self.assertEqual(sorted(archive.namelist()), sorted([f[0] for f in files] + ["MANIFEST.txt"]))
            manifest = archive.read("MANIFEST.txt").decode("utf-8")
            self.assertIn("Windows-program (futtatható)", manifest)
            self.assertEqual(archive.read("abra.png"), files[0][3])
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

    def test_document_attachments_over_http(self) -> None:
        """PDF pages, sandboxed DOCX/HTML documents, sheets, text and metadata."""
        fixtures = Path(__file__).parent / "fixtures" / "attachments"
        message = EmailMessage()
        message["From"] = "Teszt Antal <antal@pelda.hu>"
        message["To"] = "Teszt Anna <anna@pelda.hu>"
        message["Subject"] = "Dokumentumok"
        message["Date"] = "Sat, 1 Jan 2022 00:00:00 +0000"
        message["Message-ID"] = "<docs@example.test>"
        message.set_content("Csatolva.")
        for name, maintype, subtype in (
            ("sample.pdf", "application", "pdf"),
            ("sample.docx", "application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("sample.xlsx", "application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("page.html", "text", "html"),
            ("javascript.pdf", "application", "pdf"),
        ):
            message.add_attachment((fixtures / name).read_bytes(), maintype=maintype, subtype=subtype, filename=name)
        self.source_path.write_bytes(
            b"From sender@example.test Sat Jan  1 00:00:00 2022\n" + message.as_bytes(policy=policy.SMTP)
        )
        self.source_bytes = self.source_path.read_bytes()
        self._index_fixture_source()
        message_id = int(
            self.application.archive(
                query="", source_id=None, folder_id=None, has_attachments=True,
                deleted_only=False, item_type=None, include_duplicates=False, limit=5, offset=0,
            )[0]["message_id"]
        )
        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        port = int(http_server.server_address[1])

        def get(path: str) -> tuple[int, dict, bytes]:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read()
            connection.close()
            return response.status, dict(response.getheaders()), body

        listing = json.loads(get(f"/api/messages/{message_id}/attachments")[2])
        self.assertEqual(
            [item["view"] for item in listing["attachments"]], ["pdf", "docx", "xlsx", "html", "pdf"]
        )
        pdf = json.loads(get(f"/api/attachments/{message_id}/0/preview?page=1")[2])
        self.assertEqual((pdf["kind"], pdf["page"], pdf["pages"]), ("pdf", 1, 2))
        status, headers, png = get(pdf["image_url"])
        self.assertEqual((status, headers["Content-Type"]), (200, "image/png"))
        self.assertTrue(png.startswith(b"\x89PNG"))
        pages = json.loads(get(f"/api/attachments/{message_id}/0/text")[2])["pages"]
        self.assertEqual(len(pages), 2)
        info = json.loads(get(f"/api/attachments/{message_id}/0/info")[2])
        self.assertEqual(info["metadata"]["Szerző"], "Teszt Anna")
        self.assertEqual(info["security"]["Beágyazott JavaScript"], "nincs")
        scripted = json.loads(get(f"/api/attachments/{message_id}/4/info")[2])
        self.assertEqual(scripted["security"]["Beágyazott JavaScript"], "van")

        docx = json.loads(get(f"/api/attachments/{message_id}/1/preview")[2])
        self.assertTrue(docx["formatted"])
        status, headers, document = get(docx["document_url"])
        self.assertEqual(status, 200)
        self.assertEqual(
            headers["Content-Security-Policy"],
            "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; frame-ancestors 'self'",
        )
        self.assertIn("Fizetési ütemezés".encode("utf-8"), document)
        html_preview = json.loads(get(f"/api/attachments/{message_id}/3/preview")[2])
        _, _, html_document = get(html_preview["document_url"])
        self.assertNotIn(b"<script", html_document)
        self.assertIn("Kedves Teszt Anna", html_preview["text"])

        sheet = json.loads(get(f"/api/attachments/{message_id}/2/preview?sheet=1")[2])
        self.assertEqual((sheet["kind"], sheet["sheet"], sheet["rows"]), ("sheet", 1, [["rejtett adat"]]))
        xlsx_info = json.loads(get(f"/api/attachments/{message_id}/2/info")[2])
        self.assertTrue(any("Rejtett munkalap" in s for s in xlsx_info["document_signals"]))
        status, headers, _ = get("/")
        self.assertIn("frame-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

    def test_archive_and_attached_mail_tree_over_http(self) -> None:
        """Nested paths, password-only-in-memory, bomb stop, MSG and suspicious nested files."""
        fixtures = Path(__file__).parent / "fixtures" / "attachments"
        message = EmailMessage()
        message["From"] = "Teszt Antal <antal@pelda.hu>"
        message["To"] = "Teszt Anna <anna@pelda.hu>"
        message["Subject"] = "Archívumok"
        message["Date"] = "Sat, 1 Jan 2022 00:00:00 +0000"
        message["Message-ID"] = "<archives@example.test>"
        message.set_content("Csatolva.")
        for name, subtype in (
            ("message.eml", "octet-stream"), ("password.zip", "zip"), ("bomb.zip", "zip"),
        ):
            message.add_attachment((fixtures / name).read_bytes(), maintype="application", subtype=subtype, filename=name)
        self.source_path.write_bytes(
            b"From sender@example.test Sat Jan  1 00:00:00 2022\n" + message.as_bytes(policy=policy.SMTP)
        )
        self.source_bytes = self.source_path.read_bytes()
        self._index_fixture_source()
        message_id = int(
            self.application.archive(
                query="", source_id=None, folder_id=None, has_attachments=True,
                deleted_only=False, item_type=None, include_duplicates=False, limit=5, offset=0,
            )[0]["message_id"]
        )
        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        port = int(http_server.server_address[1])

        def request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
            connection.request(method, path, body=None if payload is None else json.dumps(payload), headers=headers)
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
            return response.status, body

        _, listing = request("GET", f"/api/messages/{message_id}/attachments")
        self.assertEqual([item["container"] for item in listing["attachments"]], ["email", "archive", "archive"])
        _, mail = request("GET", f"/api/attachments/{message_id}/0/preview")
        self.assertEqual((mail["kind"], mail["attachments"]), ("email", ["szamlak.zip", "szerzodes.msg"]))
        self.assertTrue(mail["formatted"])
        # 0/0 is the ZIP inside the attached e-mail; 0/0/3 the disguised program in it.
        _, children = request("GET", f"/api/attachments/{message_id}/0/0/children")
        self.assertEqual(children["entries"][3]["path"], "0/0/3")
        _, exe = request("GET", f"/api/attachments/{message_id}/0/0/3/info")
        self.assertTrue(exe["executable"])
        self.assertIn("szamlak.zip › fizetes_reszletek.pdf.exe", exe["location"])
        _, zip_info = request("GET", f"/api/attachments/{message_id}/0/0/info")
        self.assertEqual(zip_info["metadata"]["Bejegyzések"], "4")
        self.assertTrue(any("fizetes_reszletek.pdf.exe" in s for s in zip_info["document_signals"]))
        _, pdf = request("GET", f"/api/attachments/{message_id}/0/0/0/preview")
        self.assertEqual((pdf["kind"], pdf["pages"]), ("pdf", 2))
        _, msg = request("GET", f"/api/attachments/{message_id}/0/1/preview")
        self.assertEqual(dict(msg["headers"])["Subject"], "Szerződéstervezet – Outlook-levélként")
        _, inner_pdf = request("GET", f"/api/attachments/{message_id}/0/1/0/info")
        self.assertEqual(inner_pdf["real_kind"], "pdf")
        rows = self.application.archive(
            query="", source_id=None, folder_id=None, has_attachments=True,
            deleted_only=False, item_type=None, include_duplicates=False, limit=5, offset=0,
        )
        self.assertIn("fizetes_reszletek.pdf.exe", rows[0]["suspicious_attachments"])

        _, locked = request("GET", f"/api/attachments/{message_id}/1/preview")
        self.assertEqual((locked["kind"], locked["needs_password"]), ("archive", True))
        self.assertEqual(request("GET", f"/api/attachments/{message_id}/1/0/preview")[1]["kind"], "none")
        _, wrong = request("POST", f"/api/attachments/{message_id}/1/password", {"password": "rossz"})
        self.assertTrue(wrong["password_error"])
        _, opened = request("POST", f"/api/attachments/{message_id}/1/password", {"password": "pelda123"})
        self.assertFalse(opened["password_error"])
        _, secret = request("GET", f"/api/attachments/{message_id}/1/0/preview")
        self.assertIn("439 750 Ft", secret["text"])
        log_text = "".join(path.read_text(encoding="utf-8") for path in (self.case_directory / ".mailscope").glob("*.log"))
        self.assertNotIn("pelda123", log_text)
        self.assertFalse(any("pelda123".encode() in path.read_bytes() for path in (self.case_directory / ".mailscope").rglob("*") if path.is_file()))

        _, bomb = request("GET", f"/api/attachments/{message_id}/2/preview")
        self.assertIn("Lehetséges ZIP-bomba", bomb["stopped"])
        status, error = request("GET", f"/api/attachments/{message_id}/2/0/text")
        self.assertEqual(status, 404)
        status, _ = request("POST", f"/api/attachments/{message_id}/2/0/save", {})
        self.assertEqual(status, 409)
        self.assertEqual(request("GET", f"/api/attachments/{message_id}/9/0/info")[0], 404)
        self.assertFalse(any((self.case_directory / ".mailscope" / "tmp").glob("*")))
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

    def test_full_reverification_http_route(self) -> None:
        """Start a full check through the same-origin POST route."""
        source_id = self._index_fixture_source()
        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        port = int(http_server.server_address[1])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            f"/api/sources/{source_id}/verify",
            body="{}",
            headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"},
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.read())["running"])
        source = self._wait_for_verification(source_id)
        self.assertEqual(
            source["verification"]["sha256"],
            hashlib.sha256(self.source_path.read_bytes()).hexdigest(),
        )
        connection.request(
            "POST",
            "/api/sources/999/verify",
            body="{}",
            headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"},
        )
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 404)

    def test_refuses_to_open_a_source_changed_after_indexing(self) -> None:
        """Prevent stale indexed locators from reading a modified source file."""
        self.application.select_case(str(self.case_directory))
        source = self.application.discover(str(self.source_path))[0]
        self.application.start_index([source["path"]])
        assert self.application.job is not None
        self.assertTrue(self.application.job.wait(5))
        message = self.application.archive(
            query="",
            source_id=None,
            folder_id=None,
            has_attachments=False,
            deleted_only=False,
            item_type=None,
            include_duplicates=False,
            limit=100,
            offset=0,
        )[0]
        self.source_path.write_bytes(self.source_bytes + b"\n")

        with self.assertRaises(ApiError) as raised:
            self.application.open_message(int(message["message_id"]))
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(
            hashlib.sha256(self.source_path.read_bytes()).hexdigest(),
            hashlib.sha256(self.source_bytes + b"\n").hexdigest(),
        )

    def test_shutdown_waits_for_the_active_indexing_checkpoint(self) -> None:
        """Request cancellation and wait until the indexer has safely stopped."""
        job = Mock()
        job.wait.side_effect = (False, True)
        self.application.job = job

        self.assertTrue(self.application.stop_indexing_safely())

        job.cancel.assert_called_once_with()
        self.assertEqual(job.wait.call_args_list[0].args, (0,))
        self.assertEqual(job.wait.call_args_list[1].args, ())

    def test_second_instance_cannot_share_the_port(self) -> None:
        """A second server on the same port fails instead of silently co-listening."""
        first = create_server(self.application, port=0)
        self.addCleanup(first.server_close)
        port = int(first.server_address[1])
        with self.assertRaises(OSError):
            second = create_server(MailScopeApplication(), port=port)
            second.server_close()

    def test_binds_only_to_loopback(self) -> None:
        """Reject attempts to expose this local mailbox API on a network interface."""
        with self.assertRaises(ValueError):
            create_server(host="0.0.0.0", port=8765)

    def test_exact_view_converts_once_and_needs_libreoffice(self) -> None:
        """Pontos nézet: LibreOffice PDF pages, text and cache; 409 without LibreOffice; path setting."""
        fixtures = Path(__file__).parent / "fixtures" / "attachments"
        message = EmailMessage()
        message["From"] = "Teszt Antal <antal@pelda.hu>"
        message["To"] = "Teszt Anna <anna@pelda.hu>"
        message["Subject"] = "Pontos nézet"
        message["Date"] = "Sat, 1 Jan 2022 00:00:00 +0000"
        message["Message-ID"] = "<exact@example.test>"
        message.set_content("Csatolva.")
        for name in ("sample.docx", "macro-onload.odt", "sample.pdf"):
            message.add_attachment((fixtures / name).read_bytes(), maintype="application", subtype="octet-stream", filename=name)
        self.source_path.write_bytes(
            b"From sender@example.test Sat Jan  1 00:00:00 2022\n" + message.as_bytes(policy=policy.SMTP)
        )
        self.source_bytes = self.source_path.read_bytes()
        self._index_fixture_source()
        message_id = int(
            self.application.archive(
                query="", source_id=None, folder_id=None, has_attachments=True,
                deleted_only=False, item_type=None, include_duplicates=False, limit=5, offset=0,
            )[0]["message_id"]
        )
        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        port = int(http_server.server_address[1])

        def get(path: str) -> tuple[int, bytes]:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read()
            connection.close()
            return response.status, body

        with patch.object(MailScopeApplication, "libreoffice_path", return_value=None):
            listing = json.loads(get(f"/api/messages/{message_id}/attachments")[1])
            status, body = get(f"/api/attachments/{message_id}/1/preview?exact=1")
        self.assertFalse(listing["exact_available"])
        self.assertEqual([item["exact"] for item in listing["attachments"]], [True, True, False])
        self.assertEqual(listing["attachments"][1]["view"], "none")
        odt_info = json.loads(get(f"/api/attachments/{message_id}/1/info")[1])
        self.assertTrue(any(signal.startswith("Makrót tartalmaz (1 modul") for signal in odt_info["document_signals"]))
        self.assertTrue(any("megnyitáshoz" in signal for signal in odt_info["document_signals"]))
        self.assertEqual(status, 409)
        self.assertIn("LibreOffice", json.loads(body)["error"])

        converted = (fixtures / "sample.pdf").read_bytes()
        with patch.object(MailScopeApplication, "libreoffice_path", return_value=r"C:\LO\program\soffice.exe"), \
                patch("attachments.service.convert_to_pdf", return_value=converted) as convert:
            self.assertTrue(json.loads(get(f"/api/messages/{message_id}/attachments")[1])["exact_available"])
            preview = json.loads(get(f"/api/attachments/{message_id}/0/preview?exact=1&page=1")[1])
            self.assertEqual((preview["kind"], preview["page"], preview["pages"], preview["exact"]), ("pdf", 1, 2, True))
            self.assertIn("exact=1", preview["image_url"])
            status, png = get(preview["image_url"])
            self.assertEqual(status, 200)
            self.assertTrue(png.startswith(b"\x89PNG"))
            text = json.loads(get(f"/api/attachments/{message_id}/0/text?exact=1")[1])
            self.assertIn("Fizetesi utemezes", text["pages"][0])
            # The built-in view stays the default without exact=1.
            self.assertEqual(json.loads(get(f"/api/attachments/{message_id}/0/preview")[1])["kind"], "docx")
            self.assertEqual(get(f"/api/attachments/{message_id}/2/preview?exact=1")[0], 404)
        self.assertEqual(convert.call_count, 1)
        soffice, data, real_kind, name, work_dir = convert.call_args.args
        self.assertEqual((soffice, real_kind, name), (r"C:\LO\program\soffice.exe", "docx", "sample.docx"))
        self.assertEqual(data, (fixtures / "sample.docx").read_bytes())
        self.assertEqual(work_dir.parent, self.case_directory / ".mailscope" / "tmp")
        self.assertTrue(any((self.case_directory / ".mailscope" / "cache" / "attachments").rglob("exact.pdf")))

        with patch("attachments.service.convert_to_pdf", side_effect=ConversionError("A LibreOffice-átalakítás túllépte az időkorlátot (60 s), ezért leállítottuk.")), \
                patch.object(MailScopeApplication, "libreoffice_path", return_value=r"C:\LO\program\soffice.exe"):
            status, body = get(f"/api/attachments/{message_id}/1/preview?exact=1")
        self.assertEqual(status, 422)
        self.assertIn("időkorlát", json.loads(body)["error"])
        self.assertEqual(self.source_path.read_bytes(), self.source_bytes)

        settings_application = MailScopeApplication(recent_cases_file=self.root / "profil" / "recent.json")
        with self.assertRaises(ValueError):
            settings_application.set_libreoffice_path(str(self.source_path))
        soffice = self.root / "LibreOffice" / "program" / "soffice.exe"
        soffice.parent.mkdir(parents=True)
        soffice.write_bytes(b"MZ")
        self.assertEqual(settings_application.set_libreoffice_path(str(soffice))["path"], str(soffice.resolve()))
        self.assertEqual(settings_application.libreoffice_path(), str(soffice.resolve()))
        self.assertIn("libreoffice_path", (self.root / "profil" / "settings.json").read_text(encoding="utf-8"))
        self.assertIsNone(settings_application.set_libreoffice_path("")["configured"])

    def test_application_opens_the_actual_local_server_address(self) -> None:
        """Open the browser automatically using the listener's actual port."""
        http_server = Mock()
        http_server.server_address = ("127.0.0.1", 43210)
        with (
            patch("sys.argv", ["mailscope", "--port", "0"]),
            patch("server.__main__.create_server", return_value=http_server),
            patch("server.__main__.configure_logging", return_value=None),
            patch("server.__main__.webbrowser.open", return_value=True) as open_browser,
        ):
            run_server_main()

        open_browser.assert_called_once_with("http://127.0.0.1:43210", new=2)
        http_server.server_close.assert_called_once_with()

        with (
            patch("sys.argv", ["mailscope", "--port", "0", "--no-browser"]),
            patch("server.__main__.create_server", return_value=http_server),
            patch("server.__main__.configure_logging", return_value=None),
            patch("server.__main__.webbrowser.open", return_value=True) as no_browser,
        ):
            run_server_main()
        no_browser.assert_not_called()

    def test_conversation_modes_dates_threads_and_activity(self) -> None:
        """Apply person modes, CC filtering, time range, thread, and text query."""
        mailbox = self.root / "beszelgetes.mbox"
        mailbox.write_bytes(
            _fixture_mail(
                subject="Kitalált projekt",
                message_id="<projekt-root@example.test>",
                sent_date="Sat, 1 Jan 2022 09:00:00 +0000",
                body="Nyitó levél.",
            )
            + _fixture_mail(
                sender="anna@pelda.hu",
                recipient="antal@pelda.hu",
                subject="Re: Kitalált projekt",
                message_id="<projekt-reply@example.test>",
                references="<projekt-root@example.test>",
                sent_date="Mon, 3 Jan 2022 09:00:00 +0000",
                body="Különleges visszajelzés.",
            )
            + _fixture_mail(
                subject="Re: Kitalált projekt",
                message_id="<projekt-cc@example.test>",
                references="<projekt-root@example.test>",
                cc="bela@pelda.hu",
                sent_date="Wed, 5 Jan 2022 09:00:00 +0000",
                body="Közös egyeztetés.",
            )
            + _fixture_mail(
                sender="bela@pelda.hu",
                recipient="antal@pelda.hu",
                subject="Kitalált továbbítás",
                message_id="<projekt-forward@example.test>",
                cc="anna@pelda.hu",
                sent_date="Sat, 8 Jan 2022 09:00:00 +0000",
                body="Továbbított levél.",
            )
            + _fixture_mail(
                sender="bela@pelda.hu",
                recipient="anna@pelda.hu",
                subject="Kitalált hármas egyeztetés",
                message_id="<projekt-shared@example.test>",
                cc="antal@pelda.hu",
                sent_date="Mon, 10 Jan 2022 09:00:00 +0000",
                body="Három résztvevő egyeztetése.",
            )
        )
        database = IndexDatabase.for_case(self.case_directory)
        IndexingService(database).index_sources(discover_sources(mailbox))
        directory = PersonDirectory(database)
        antal = directory.create_person("Teszt Antal", ("antal@pelda.hu",))
        anna = directory.create_person("Teszt Anna", ("anna@pelda.hu",))
        bela = directory.create_person("Teszt Béla", ("bela@pelda.hu",))
        self.application.select_case(str(self.case_directory))

        between_with_copy = self.application.conversation(
            person_ids=(antal.id, anna.id),
            mode="between",
            include_cc_bcc=True,
            date_from="",
            date_to="",
            query="",
            thread_key=None,
        )
        self.assertEqual(len(between_with_copy["messages"]), 2)
        self.assertEqual(len(between_with_copy["months"]), 1)
        self.assertEqual(between_with_copy["months"][0]["count"], 2)
        self.assertEqual(
            {message["thread_key"] for message in between_with_copy["messages"]},
            {"mid:projekt-root@example.test"},
        )
        self.assertEqual(between_with_copy["messages"][1]["silent_days_before"], 1)

        between_without_copy = self.application.conversation(
            person_ids=(antal.id, anna.id),
            mode="between",
            include_cc_bcc=False,
            date_from="2022-01-03",
            date_to="2022-01-05",
            query="",
            thread_key=None,
        )
        self.assertEqual(len(between_without_copy["messages"]), 2)
        self.assertEqual(
            [message["day"] for message in between_without_copy["messages"]],
            ["2022-01-03", "2022-01-05"],
        )
        thread_results = self.application.conversation(
            person_ids=(antal.id, anna.id),
            mode="between",
            include_cc_bcc=False,
            date_from="",
            date_to="",
            query="",
            thread_key="mid:projekt-root@example.test",
        )
        self.assertEqual(len(thread_results["messages"]), 3)

        shared = self.application.conversation(
            person_ids=(antal.id, anna.id),
            mode="shared",
            include_cc_bcc=True,
            date_from="",
            date_to="",
            query="",
            thread_key=None,
        )
        self.assertEqual(
            [message["internet_message_id"] for message in shared["messages"]],
            [
                "<projekt-root@example.test>",
                "<projekt-reply@example.test>",
                "<projekt-cc@example.test>",
                "<projekt-forward@example.test>",
                "<projekt-shared@example.test>",
            ],
        )
        shared_without_copy = self.application.conversation(
            person_ids=(antal.id, anna.id),
            mode="shared",
            include_cc_bcc=False,
            date_from="",
            date_to="",
            query="",
            thread_key=None,
        )
        self.assertEqual(len(shared_without_copy["messages"]), 3)

        any_selected = self.application.conversation(
            person_ids=(antal.id, anna.id),
            mode="any",
            include_cc_bcc=False,
            date_from="",
            date_to="",
            query="Különleges",
            thread_key=None,
        )
        self.assertEqual(len(any_selected["messages"]), 1)
        self.assertIn("Különleges", any_selected["messages"][0]["body"])

        all_three = self.application.conversation(
            person_ids=(antal.id, anna.id, bela.id),
            mode="shared",
            include_cc_bcc=True,
            date_from="",
            date_to="",
            query="",
            thread_key=None,
        )
        self.assertEqual(len(all_three["messages"]), 3)
        self.assertTrue(
            all(len(message["participants"]) == 3 for message in all_three["messages"])
        )

    def test_conversation_eml_zip_contains_manifest_of_verified_messages(self) -> None:
        """Export selected originals with stable safe entry names and SHA-256s."""
        mailbox = self.root / "export.mbox"
        mailbox.write_bytes(
            _fixture_mail(
                subject="../../unsafe:first",
                message_id="<export-one@example.test>",
                body="Első levél.",
            )
            + _fixture_mail(
                sender="anna@pelda.hu",
                recipient="antal@pelda.hu",
                subject="Második levél",
                message_id="<export-two@example.test>",
                body="Második levél tartalma.",
            )
        )
        database = IndexDatabase.for_case(self.case_directory)
        IndexingService(database).index_sources(discover_sources(mailbox))
        directory = PersonDirectory(database)
        antal = directory.create_person("Teszt Antal", ("antal@pelda.hu",))
        anna = directory.create_person("Teszt Anna", ("anna@pelda.hu",))
        self.application.select_case(str(self.case_directory))

        archive_bytes = self.application.conversation_eml_zip(
            person_ids=(antal.id, anna.id),
            mode="between",
            include_cc_bcc=True,
            date_from="",
            date_to="",
            query="",
            thread_key=None,
        )

        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 4)
            self.assertIn("SHA256SUMS.txt", names)
            self.assertIn("MANIFEST.txt", names)
            eml_names = [name for name in names if name.endswith(".eml")]
            self.assertEqual(len(eml_names), 2)
            self.assertTrue(all("/" not in name and "\\" not in name for name in eml_names))
            manifest = archive.read("SHA256SUMS.txt").decode("utf-8")
            for name in eml_names:
                digest = hashlib.sha256(archive.read(name)).hexdigest()
                self.assertIn(f"{digest}  {name}", manifest)

    def test_http_ui_and_same_origin_case_api(self) -> None:
        """Serve local assets and enforce host and origin checks on writes."""
        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        port = int(http_server.server_address[1])

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", "/")
        response = connection.getresponse()
        html = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("Mail-Scope", html)
        self.assertIn('id="shutdown-button"', html)
        self.assertIn('id="case-browse-button"', html)
        self.assertIn('id="source-file-browse-button"', html)
        self.assertIn('id="source-folder-browse-button"', html)
        self.assertIn('href="/favicon.ico"', html)
        self.assertIn('src="/assets/logo/png/mail-scope-logo-dark-bg.png"', html)
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))

        self.assertIn('src="/pcb-background.js"', html)
        self.assertIn('id="welcome-screen"', html)
        self.assertNotIn("style=", html)
        for route, expected_type in (
            ("/favicon.ico", "image/vnd.microsoft.icon"),
            ("/assets/logo/png/mail-scope-logo-dark-bg.png", "image/png"),
            ("/app.js", "text/javascript; charset=utf-8"),
            ("/pcb-background.js", "text/javascript; charset=utf-8"),
            ("/styles.css", "text/css; charset=utf-8"),
        ):
            connection.request("GET", route)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), expected_type)
            self.assertGreater(len(response.read()), 0)

        with patch("server.app._browse_local_path", return_value=str(self.source_path)) as browse:
            connection.request(
                "POST",
                "/api/browse",
                body=json.dumps({"kind": "file", "initial_path": str(self.root)}),
                headers={
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{port}",
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["path"], str(self.source_path))
            browse.assert_called_once_with("file", str(self.root))

        connection.request("GET", "/api/status", headers={"Host": f"localhost:{port}"})
        self.assertEqual(connection.getresponse().status, 403)

        body = json.dumps({"path": str(self.case_directory)})
        connection.request(
            "POST",
            "/api/case",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://attacker.invalid:{port}",
            },
        )
        self.assertEqual(connection.getresponse().status, 403)

        connection.request(
            "POST",
            "/api/case",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
            },
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["case"]["path"], str(self.case_directory))

        for name in ("Teszt Antal", "Teszt Anna"):
            person_body = json.dumps({"display_name": name, "addresses": []})
            connection.request(
                "POST",
                "/api/people",
                body=person_body,
                headers={
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{port}",
                },
            )
            self.assertEqual(connection.getresponse().status, 200)

        connection.request("GET", "/api/people")
        people_response = connection.getresponse()
        people = json.loads(people_response.read())
        self.assertEqual(len(people["people"]), 2)
        identifiers = ",".join(str(person["id"]) for person in people["people"])
        connection.request(
            "GET",
            f"/api/conversation?people={identifiers}&mode=any&include_cc_bcc=1",
        )
        conversation_response = connection.getresponse()
        self.assertEqual(conversation_response.status, 200)
        self.assertEqual(json.loads(conversation_response.read())["messages"], [])
        connection.request(
            "GET",
            f"/api/conversation/export?people={identifiers}&mode=any&include_cc_bcc=1",
        )
        export_response = connection.getresponse()
        self.assertEqual(export_response.status, 200)
        self.assertIn(".eml.zip", export_response.getheader("Content-Disposition"))
        with zipfile.ZipFile(BytesIO(export_response.read())) as export:
            self.assertEqual(export.namelist(), ["SHA256SUMS.txt", "MANIFEST.txt"])

    def test_shutdown_requires_confirmation_when_indexing_is_active(self) -> None:
        """Require explicit confirmation before cancelling an active index job."""
        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        port = int(http_server.server_address[1])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(connection.close)
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
        }

        with patch.object(
            self.application,
            "status",
            return_value={"job": {"running": True}},
        ), patch.object(http_server, "request_shutdown") as request_shutdown:
            connection.request(
                "POST",
                "/api/shutdown",
                body=json.dumps({"cancel_indexing": False}),
                headers=headers,
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 409)
            self.assertIn("Indexelés fut", json.loads(response.read())["error"])
            request_shutdown.assert_not_called()

            connection.request(
                "POST",
                "/api/shutdown",
                body=json.dumps({"cancel_indexing": True}),
                headers=headers,
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(
                json.loads(response.read()),
                {"stopping": True, "indexing_was_running": True},
            )
            # The handler answers first and requests the shutdown afterwards.
            deadline = time.monotonic() + 2
            while not request_shutdown.called and time.monotonic() < deadline:
                time.sleep(0.01)
            request_shutdown.assert_called_once_with()

    def test_shutdown_endpoint_stops_the_local_server(self) -> None:
        """Return the shutdown response before stopping the HTTP listener."""
        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(
            lambda: http_server.shutdown() if server_thread.is_alive() else None
        )
        port = int(http_server.server_address[1])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            "/api/shutdown",
            body=json.dumps({"cancel_indexing": False}),
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
            },
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(
            json.loads(response.read()),
            {"stopping": True, "indexing_was_running": False},
        )

        server_thread.join(timeout=3)
        self.assertFalse(server_thread.is_alive())

    def test_detailed_search_label_and_saved_search_http_routes(self) -> None:
        """Exercise detailed-search, label, and saved-search endpoints over loopback."""
        source = self.root / "search-api.mbox"
        source.write_bytes(_fixture_mail())
        database = IndexDatabase.for_case(self.case_directory)
        IndexingService(database).index_sources(discover_sources(source))
        self.application.select_case(str(self.case_directory))
        message_id = int(database.archive_messages()[0]["message_id"])

        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        port = int(http_server.server_address[1])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(connection.close)

        def post(path: str, payload: dict[str, object]) -> tuple[int, object]:
            """Send one local JSON request and decode the JSON response."""
            connection.request(
                "POST",
                path,
                body=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())

        search_payload = {
            "conditions": [{"field": "subject", "value": "helyi felület"}],
            "operator": "AND",
            "limit": 50,
            "offset": 0,
        }
        status, search_result = post("/api/search", search_payload)
        self.assertEqual(status, 200)
        self.assertEqual(search_result["total"], 1)
        self.assertEqual(search_result["results"][0]["message_id"], message_id)
        self.assertEqual(
            set(search_result["results"][0]["participant_addresses"].split(",")),
            {"antal@pelda.hu", "anna@pelda.hu"},
        )
        grouped_status, grouped_result = post(
            "/api/search",
            {
                "operator": "AND",
                "groups": [
                    {
                        "operator": "OR",
                        "conditions": [
                            {"field": "subject", "value": "helyi felület"},
                            {"field": "from", "value": "ismeretlen@pelda.hu"},
                        ],
                    }
                ],
            },
        )
        self.assertEqual(grouped_status, 200)
        self.assertEqual(grouped_result["total"], 1)

        status, label_result = post(
            "/api/labels",
            {"message_ids": [message_id], "label": "API-címke", "enabled": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(label_result["occurrences_affected"], 1)
        status, labeled_result = post(
            "/api/search",
            {
                "conditions": [{"field": "label", "value": "API-címke"}],
                "operator": "AND",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(labeled_result["total"], 1)

        saved_payload = {
            "name": "Kitalalt-API-kereses",
            "groups": [
                {
                    "operator": "AND",
                    "conditions": [{"field": "subject", "value": "helyi felület"}],
                }
            ],
            "operator": "OR",
        }
        self.assertEqual(post("/api/searches", saved_payload)[0], 200)
        connection.request("GET", "/api/searches")
        saved_response = connection.getresponse()
        saved_searches = json.loads(saved_response.read())
        self.assertEqual(saved_searches[0]["name"], saved_payload["name"])
        self.assertEqual(saved_searches[0]["query"]["groups"], saved_payload["groups"])
        self.assertEqual(saved_searches[0]["query"]["operator"], saved_payload["operator"])
        self.assertEqual(
            post(
                f"/api/searches/{saved_payload['name']}",
                {},
            )[0],
            200,
        )
        self.assertEqual(post("/api/search", {"conditions": [], "operator": "AND"})[0], 400)

    def test_detailed_search_exports_csv_and_verified_eml_zip(self) -> None:
        """Export detailed-search metadata and originals without changing sources."""
        self.application.select_case(str(self.case_directory))
        raw_eml = (
            b"From: antal@pelda.hu\r\n"
            b"To: anna@pelda.hu\r\n"
            b"Subject: =SUM(1,1)\r\n"
            b"Date: Sat, 1 Jan 2022 00:00:00 +0000\r\n"
            b"Message-ID: <csv-formula@example.test>\r\n"
            b"X-Folded: original\r\n\tcontinued\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Raw body with trailing spaces.  \r\n"
        )
        self.source_path.write_bytes(
            b"From sender@example.test Sat Jan  1 00:00:00 2022\n" + raw_eml
        )
        source = self.application.discover(str(self.source_path))[0]
        self.application.start_index([source["path"]])
        assert self.application.job is not None
        self.assertTrue(self.application.job.wait(5))
        query = ({"field": "subject", "value": "=SUM(1,1)"},)

        original_hash = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        csv_bytes, csv_type, csv_name = self.application.detailed_search_export(
            query,
            operator="AND",
            export_format="csv",
        )
        self.assertEqual(csv_type, "text/csv; charset=utf-8")
        self.assertEqual(csv_name, "Mail-Scope-search.csv")
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
        csv_text = csv_bytes.decode("utf-8-sig")
        self.assertIn("subject,sent_date,sender,participants,source,folder,message_id,sha256,labels,copy_count", csv_text)
        self.assertIn("'=SUM(1,1)", csv_text)

        zip_bytes, zip_type, zip_name = self.application.detailed_search_export(
            query,
            operator="AND",
            export_format="eml_zip",
        )
        self.assertEqual(zip_type, "application/zip")
        self.assertEqual(zip_name, "Mail-Scope-search.eml.zip")
        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 3)
            provenance = archive.read("MANIFEST.txt").decode("utf-8")
            self.assertIn("eredeti forrásbájtok", provenance)
            envelope_length = len(b"From sender@example.test Sat Jan  1 00:00:00 2022\n")
            self.assertIn(
                f"bájt 0, hossz {envelope_length + len(raw_eml)} "
                f"(benne a {envelope_length} bájtos „From ” borítéksor; az EML {len(raw_eml)} bájt)",
                provenance,
            )
            self.assertIn("aláírás-ellenőrzés (méret, módosítási idő, fájlazonosító)", provenance)
            self.assertRegex(provenance, r"\t\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00\n")
            eml_name = next(name for name in names if name.endswith(".eml"))
            exported_message = archive.read(eml_name)
            manifest = archive.read("SHA256SUMS.txt").decode("utf-8")
            self.assertEqual(exported_message, raw_eml)
            self.assertIn(hashlib.sha256(exported_message).hexdigest(), manifest)
            self.assertIn(eml_name, manifest)
            self.assertIn(b"Message-ID: <csv-formula@example.test>", exported_message)

        self.assertEqual(
            hashlib.sha256(self.source_path.read_bytes()).hexdigest(),
            original_hash,
        )

        http_server = create_server(self.application, port=0)
        server_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(http_server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(http_server.shutdown)
        connection = http.client.HTTPConnection(
            "127.0.0.1", int(http_server.server_address[1]), timeout=3
        )
        self.addCleanup(connection.close)
        for export_format, expected_type in (
            ("csv", "text/csv; charset=utf-8"),
            ("eml_zip", "application/zip"),
        ):
            connection.request(
                "POST",
                "/api/search/export",
                body=json.dumps(
                    {
                        "conditions": list(query),
                        "operator": "AND",
                        "format": export_format,
                    }
                ),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            exported_data = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), expected_type)
            self.assertGreater(len(exported_data), 0)
            self.assertIn("attachment;", response.getheader("Content-Disposition"))


if __name__ == "__main__":
    unittest.main()
