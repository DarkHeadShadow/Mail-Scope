"""Exercise the local server, case workflow, and original-source reading."""

__author__ = "Bálint Szabolcs"

import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from email import policy
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from server import ApiError, MailScopeApplication, create_server
from sources.discover import sha256_file


def _fixture_mbox() -> bytes:
    """Create a fictional mailbox message without any real personal data."""
    message = EmailMessage()
    message["From"] = "Teszt Antal <antal@pelda.hu>"
    message["To"] = "Teszt Anna <anna@pelda.hu>"
    message["Subject"] = "Kitalált levél a helyi felület tesztjéhez"
    message["Message-ID"] = "<fixture@example.test>"
    message["Date"] = "Sat, 1 Jan 2022 00:00:00 +0000"
    message.set_content("Ez egy kitalált üzenettörzs.")
    return b"From sender@example.test Sat Jan  1 00:00:00 2022\n" + message.as_bytes(
        policy=policy.SMTP
    )


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


if __name__ == "__main__":
    unittest.main()
