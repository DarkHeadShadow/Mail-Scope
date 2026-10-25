"""Exercise the local server, case workflow, and original-source reading."""

__author__ = "Bálint Szabolcs"

import hashlib
import http.client
import json
import tempfile
import threading
import unittest
import zipfile
from email import policy
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from indexer import IndexDatabase, IndexingService, PersonDirectory
from server import ApiError, MailScopeApplication, create_server
from sources.discover import discover_sources, sha256_file


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
        self.root = Path(self.temporary_directory.name)
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
        with patch("server.app.sha256_file", wraps=sha256_file) as hash_source:
            opened = self.application.open_message(int(archive_rows[0]["message_id"]))
            self.application.open_message(int(archive_rows[0]["message_id"]))
            self.assertEqual(hash_source.call_count, 1)
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
            self.assertEqual(hash_source.call_count, 2)
        self.assertTrue((self.case_directory / ".mailscope" / "index.sqlite3").is_file())

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

    def test_binds_only_to_loopback(self) -> None:
        """Reject attempts to expose this local mailbox API on a network interface."""
        with self.assertRaises(ValueError):
            create_server(host="0.0.0.0", port=8765)

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
            self.assertEqual(len(names), 3)
            self.assertIn("SHA256SUMS.txt", names)
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
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))

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
            self.assertEqual(export.namelist(), ["SHA256SUMS.txt"])

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


if __name__ == "__main__":
    unittest.main()
