__author__ = "Bálint Szabolcs"

import hashlib
import sqlite3
import tempfile
import threading
import unittest
from email import policy
from email.message import EmailMessage
from pathlib import Path

from indexer import IndexDatabase, IndexingService, PersonDirectory
from sources.discover import discover_sources


def _mail(
    *,
    message_id: str | None,
    subject: str,
    body: str,
    from_address: str = "antal@pelda.hu",
    from_display_name: str = "Teszt Antal",
    to_address: str = "anna@pelda.hu",
    cc_address: str | None = None,
    bcc_address: str | None = None,
    references: str | None = None,
    in_reply_to: str | None = None,
    labels: str | None = None,
    attachment: bool = False,
) -> bytes:
    """Build a fictional RFC 5322 message for indexer tests."""
    message = EmailMessage()
    message["From"] = f"{from_display_name} <{from_address}>"
    message["To"] = f"Teszt Anna <{to_address}>"
    if cc_address is not None:
        message["Cc"] = cc_address
    if bcc_address is not None:
        message["Bcc"] = bcc_address
    message["Subject"] = subject
    if message_id is not None:
        message["Message-ID"] = message_id
    message["Date"] = "Sat, 1 Jan 2022 00:00:00 +0000"
    if references is not None:
        message["References"] = references
    if in_reply_to is not None:
        message["In-Reply-To"] = in_reply_to
    if labels is not None:
        message["X-Gmail-Labels"] = labels
    message.set_content(body)
    if attachment:
        message.add_attachment(
            b"csak kitalalt adat",
            maintype="text",
            subtype="plain",
            filename="bizonyitek.txt",
        )
    return b"From sender@example.test Sat Jan  1 00:00:00 2022\n" + message.as_bytes(
        policy=policy.SMTP
    )


class IndexerTests(unittest.TestCase):
    """Exercise indexing, deduplication, search, resume, and integrity."""

    def setUp(self) -> None:
        """Create an isolated case directory and its SQLite database."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.case_directory = Path(self.temp_dir.name)
        self.database = IndexDatabase.for_case(self.case_directory)
        self.service = IndexingService(self.database)

    def test_indexes_searchable_messages_and_merges_duplicates(self) -> None:
        """Keep source occurrences while exposing one indexed duplicate."""
        root_id = "<root@example.test>"
        first = self.case_directory / "first.mbox"
        first.write_bytes(
            _mail(
                message_id=root_id,
                subject="Kitalált szál",
                body="Kitalált tartalom: bizonyíték.",
                cc_address="bela@pelda.hu",
                bcc_address="csilla@pelda.hu",
                labels="Első, Kiemelt",
                attachment=True,
            )
            + _mail(
                message_id="<reply@example.test>",
                subject="Re: Kitalált szál",
                body="Folytatás szuperkereshető szóval.",
                references=f"{root_id} <reply@example.test>",
                in_reply_to=root_id,
            )
        )
        second = self.case_directory / "second.mbox"
        second.write_bytes(
            _mail(
                message_id=root_id,
                subject="Fwd: Kitalált szál",
                body="Kitalált tartalom: bizonyíték.",
                labels="Második",
                attachment=True,
            )
        )
        source_hashes = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (first, second)
        }

        summary = self.service.index_sources(discover_sources(self.case_directory))

        self.assertEqual(summary.status, "completed")
        self.assertEqual(summary.sources_completed, 2)
        self.assertEqual(summary.messages_indexed, 3)
        self.assertEqual(self.database.message_count(), 2)
        self.assertEqual(self.database.occurrence_count(), 3)
        self.assertEqual(
            [
                hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (first, second)
            ],
            [source_hashes[first], source_hashes[second]],
        )

        results = self.database.search("szuperkereshető")
        self.assertEqual(len(results), 1)
        reply_id = results[0]["id"]
        self.assertEqual(results[0]["thread_key"], f"mid:{root_id[1:-1]}")
        participant_roles = {
            row["role"]: row["address"]
            for row in self.database.participant_rows(reply_id)
        }
        self.assertEqual(participant_roles["from"], "antal@pelda.hu")

        root_result = self.database.search('"bizonyitek.txt"')[0]
        root_message_id = root_result["id"]
        root_participants = {
            row["role"]: row["address"]
            for row in self.database.participant_rows(root_message_id)
        }
        self.assertEqual(root_participants["cc"], "bela@pelda.hu")
        self.assertEqual(root_participants["bcc"], "csilla@pelda.hu")
        self.assertEqual(
            root_result["thread_key"],
            results[0]["thread_key"],
        )
        with self.database.connection() as connection:
            reply_headers = connection.execute(
                "SELECT in_reply_to, references_header FROM messages WHERE id = ?",
                (reply_id,),
            ).fetchone()
        self.assertEqual(reply_headers["in_reply_to"], root_id)
        self.assertEqual(
            reply_headers["references_header"],
            f"{root_id} <reply@example.test>",
        )
        self.assertEqual(
            self.database.labels_for_message(root_message_id),
            ["Első", "Kiemelt", "Második"],
        )
        self.assertEqual(len(self.database.archive_messages()), 2)
        self.assertEqual(len(self.database.archive_messages(include_duplicates=True)), 3)
        self.assertEqual(len(self.database.archive_messages("szuperkereshető")), 1)
        self.assertEqual(len(self.database.archive_messages(has_attachments=True)), 1)
        self.assertEqual(
            sum(folder["message_count"] for folder in self.database.folders()),
            3,
        )
        self.assertEqual(
            len(self.database.occurrences_for_message(root_message_id)),
            2,
        )
        self.assertEqual(
            self.database.attachment_names_for_message(root_message_id),
            ["bizonyitek.txt"],
        )
        attachment = self.database.attachment_rows_for_message(root_message_id)[0]
        self.assertEqual(attachment["content_type"], "text/plain")
        self.assertEqual(
            [source["status"] for source in self.database.sources()],
            ["completed", "completed"],
        )
        self.assertTrue(
            all(
                source["sha256_before"] == source["sha256_after"]
                for source in self.database.sources()
            )
        )
        self.assertEqual(
            self.database.path,
            self.case_directory / ".mailscope" / "index.sqlite3",
        )
        self.assertEqual(self.database.metadata_value("creator"), "Bálint Szabolcs")

        repeated = self.service.index_sources(discover_sources(self.case_directory))
        self.assertEqual(repeated.sources_skipped, 2)
        self.assertEqual(self.database.occurrence_count(), 3)

    def test_cancellation_resumes_from_last_committed_message(self) -> None:
        """Commit each message so cancelling and rerunning resumes without loss."""
        mailbox = self.case_directory / "resume.mbox"
        mailbox.write_bytes(
            _mail(
                message_id="<one@example.test>",
                subject="Első",
                body="Első üzenet.",
            )
            + _mail(
                message_id="<two@example.test>",
                subject="Második",
                body="Második üzenet.",
            )
        )
        discovered = discover_sources(mailbox)
        cancel_event = threading.Event()

        def cancel_after_first_message(progress: object) -> None:
            """Request cancellation after the first durable message update."""
            if getattr(progress, "messages_scanned") == 1:
                cancel_event.set()

        interrupted = self.service.index_sources(
            discovered,
            cancel_event=cancel_event,
            progress_callback=cancel_after_first_message,
        )

        self.assertEqual(interrupted.status, "cancelled")
        self.assertEqual(self.database.occurrence_count(), 1)

        resumed = self.service.index_sources(discovered)

        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed.messages_scanned, 1)
        self.assertEqual(self.database.occurrence_count(), 2)
        self.assertEqual(self.database.message_count(), 2)
        self.assertEqual(len(self.database.search("Második")), 1)

    def test_deduplicates_by_normalized_headers_and_body_when_id_is_missing(self) -> None:
        """Use a normalized header/body fingerprint if Message-ID is absent."""
        first = self.case_directory / "fallback_one.mbox"
        first.write_bytes(
            _mail(
                message_id=None,
                subject="Kitalált téma",
                body="Azonos kitalált tartalom.",
            )
        )
        second = self.case_directory / "fallback_two.mbox"
        second.write_bytes(
            _mail(
                message_id=None,
                subject="Re: Kitalált téma",
                body="Azonos kitalált tartalom.",
            )
        )

        summary = self.service.index_sources(discover_sources(self.case_directory))

        self.assertEqual(summary.status, "completed")
        self.assertEqual(self.database.message_count(), 1)
        self.assertEqual(self.database.occurrence_count(), 2)

    def test_detects_source_changed_after_discovery(self) -> None:
        """Do not index a source whose bytes differ from its discovery hash."""
        mailbox = self.case_directory / "changed.mbox"
        mailbox.write_bytes(
            _mail(
                message_id="<before@example.test>",
                subject="Eredeti",
                body="Eredeti tartalom.",
            )
        )
        discovered = discover_sources(mailbox)
        mailbox.write_bytes(
            _mail(
                message_id="<after@example.test>",
                subject="Módosított",
                body="Módosított tartalom.",
            )
        )

        summary = self.service.index_sources(discovered)

        self.assertEqual(summary.status, "source_changed")
        self.assertEqual(summary.sources_changed, 1)
        self.assertEqual(self.database.occurrence_count(), 0)
        source = self.database.sources()[0]
        self.assertEqual(source["status"], "source_changed")
        self.assertIsNotNone(source["last_error"])
        self.assertNotEqual(source["sha256_before"], source["discovered_sha256"])

    def test_detects_source_changed_during_indexing(self) -> None:
        """Compare the source hash after reading it, not only before indexing."""
        mailbox = self.case_directory / "changed_during.mbox"
        mailbox.write_bytes(
            _mail(
                message_id="<during@example.test>",
                subject="Eredeti",
                body="Tartalom.",
            )
        )
        discovered = discover_sources(mailbox)
        changed = False

        def append_after_first_message(progress: object) -> None:
            """Change the source once an indexing update has been published."""
            nonlocal changed
            if getattr(progress, "subject") and not changed:
                with mailbox.open("ab") as source:
                    source.write(b"changed")
                changed = True

        summary = self.service.index_sources(
            discovered,
            progress_callback=append_after_first_message,
        )

        self.assertEqual(summary.status, "source_changed")
        source = self.database.sources()[0]
        self.assertEqual(source["status"], "source_changed")
        self.assertNotEqual(source["sha256_before"], source["sha256_after"])
        self.assertEqual(source["indexed_count"], 1)

    def test_reindexes_changed_source_and_removes_orphaned_messages(self) -> None:
        """Replace stale occurrences when a completed source's bytes change."""
        mailbox = self.case_directory / "replace.mbox"
        mailbox.write_bytes(
            _mail(
                message_id="<old@example.test>",
                subject="Régi forrás",
                body="Régi tartalom.",
            )
        )
        self.service.index_sources(discover_sources(mailbox))
        mailbox.write_bytes(
            _mail(
                message_id="<new@example.test>",
                subject="Új forrás",
                body="Új tartalom.",
            )
        )

        summary = self.service.index_sources(discover_sources(mailbox))

        self.assertEqual(summary.status, "completed")
        self.assertEqual(self.database.message_count(), 1)
        self.assertEqual(self.database.occurrence_count(), 1)
        self.assertEqual(self.database.search("Régi"), [])
        self.assertEqual(len(self.database.search("tartalom")), 1)

    def test_person_aliases_suggestions_and_person_filtered_search(self) -> None:
        """Suggest shared display names and filter messages through aliases."""
        mailbox = self.case_directory / "people.mbox"
        mailbox.write_bytes(
            _mail(
                message_id="<person-one@example.test>",
                subject="Első személy",
                body="Kereshető levél.",
                from_address="antal@pelda.hu",
            )
            + _mail(
                message_id="<person-two@example.test>",
                subject="Második személy",
                body="Kereshető levél.",
                from_address="antal.masik@pelda.hu",
            )
            + _mail(
                message_id="<other@example.test>",
                subject="Másik résztvevő",
                body="Kereshető levél.",
                from_address="bela@pelda.hu",
                from_display_name="Teszt Béla",
                cc_address="antal@pelda.hu",
            )
        )
        self.service.index_sources(discover_sources(mailbox))
        directory = PersonDirectory(self.database)

        suggestions = directory.suggestions()

        self.assertTrue(
            any(
                suggestion.display_name == "Teszt Antal"
                and suggestion.addresses
                == ("antal.masik@pelda.hu", "antal@pelda.hu")
                for suggestion in suggestions
            )
        )
        person = directory.create_person(
            "Bálint Antal", ("ANTAL@PELDA.HU", "antal.masik@pelda.hu")
        )
        self.assertFalse(
            any(
                suggestion.addresses
                == ("antal.masik@pelda.hu", "antal@pelda.hu")
                for suggestion in directory.suggestions()
            )
        )
        person = directory.assign_address(
            person.id, "antal.third@pelda.hu", "Teszt Antal"
        )
        self.assertIn("antal.third@pelda.hu", person.addresses)
        self.assertTrue(directory.remove_address(person.id, "ANTAL.THIRD@PELDA.HU"))
        person = directory.rename(person.id, "Antal Teszt")
        self.assertEqual(person.display_name, "Antal Teszt")
        self.assertEqual(
            len(directory.search("Kereshető", (person.id,), include_cc_bcc=False)),
            2,
        )
        self.assertEqual(
            len(directory.search("Kereshető", (person.id,), include_cc_bcc=True)),
            3,
        )
        with self.assertRaises(ValueError):
            directory.create_person("Másik Antal", ("antal@pelda.hu",))

        other_person = directory.create_person("Teszt Béla", ("bela@pelda.hu",))
        merged = directory.merge(person.id, other_person.id)
        self.assertEqual(
            merged.addresses,
            ("antal.masik@pelda.hu", "antal@pelda.hu", "bela@pelda.hu"),
        )
        self.assertEqual(
            len(directory.search("Kereshető", (person.id,), include_cc_bcc=True)),
            3,
        )
        self.assertEqual(len(directory.people()), 1)

    def test_groups_reply_chains_by_reference_and_normalized_subject(self) -> None:
        """Use message references first and normalized subjects as fallback."""
        mailbox = self.case_directory / "threads.mbox"
        mailbox.write_bytes(
            _mail(
                message_id="<root@example.test>",
                subject="Kitalált szál",
                body="Szülő.",
            )
            + _mail(
                message_id="<reply-one@example.test>",
                subject="Re: Kitalált szál",
                body="Első válasz.",
                in_reply_to="<root@example.test>",
            )
            + _mail(
                message_id="<reply-two@example.test>",
                subject="Fwd: Re: Kitalált szál",
                body="Második válasz.",
                references="<root@example.test> <reply-one@example.test>",
            )
            + _mail(
                message_id="<reply-three@example.test>",
                subject="VS: Kitalált szál",
                body="Tárgy alapján kapcsolt válasz.",
            )
        )
        self.service.index_sources(discover_sources(mailbox))

        with self.database.connection() as connection:
            keys = [
                row["thread_key"]
                for row in connection.execute(
                    "SELECT thread_key FROM messages ORDER BY id"
                )
            ]

        self.assertEqual(
            keys,
            ["mid:root@example.test"] * 4,
        )
        self.assertEqual(
            len(self.database.thread_messages("mid:root@example.test")),
            4,
        )

    def test_migrates_existing_participant_table(self) -> None:
        """Upgrade a prior schema by adding display-name storage safely."""
        legacy_path = self.case_directory / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE participants (
                    id INTEGER PRIMARY KEY,
                    message_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    address TEXT NOT NULL,
                    UNIQUE(message_id, role, address)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY,
                    dedup_key TEXT NOT NULL UNIQUE,
                    message_id TEXT,
                    thread_key TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    normalized_subject TEXT NOT NULL,
                    sent_date TEXT,
                    in_reply_to TEXT,
                    references_header TEXT,
                    body TEXT NOT NULL,
                    content_hash TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO messages(
                    dedup_key, message_id, thread_key, subject,
                    normalized_subject, sent_date, in_reply_to,
                    references_header, body, content_hash
                ) VALUES (
                    'legacy', '<Legacy@Example.Test>', 'mid:legacy@example.test',
                    'Régi', 'régi', 'Sat, 1 Jan 2022 00:00:00 +0000',
                    NULL, NULL, 'Tartalom', 'hash'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO participants(message_id, role, address)
                VALUES (1, 'from', 'antal@pelda.hu')
                """
            )
            connection.commit()
        finally:
            connection.close()

        upgraded = IndexDatabase(legacy_path)
        with upgraded.connection() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(participants)")
            }
            participant = connection.execute(
                "SELECT address, display_name FROM participants"
            ).fetchone()
            normalized_id = connection.execute(
                "SELECT normalized_message_id FROM messages WHERE id = 1"
            ).fetchone()["normalized_message_id"]

        self.assertIn("display_name", columns)
        self.assertEqual(participant["address"], "antal@pelda.hu")
        self.assertEqual(participant["display_name"], "")
        self.assertEqual(normalized_id, "legacy@example.test")
        with upgraded.connection() as connection:
            timestamp = connection.execute(
                "SELECT sent_timestamp FROM messages WHERE id = 1"
            ).fetchone()["sent_timestamp"]
        self.assertEqual(timestamp, 1640995200)
        self.assertEqual(upgraded.metadata_value("schema_version"), "4")

    def test_background_job_reports_progress_and_result(self) -> None:
        """Run indexing on a worker and expose its final progress and summary."""
        mailbox = self.case_directory / "background.mbox"
        mailbox.write_bytes(
            _mail(
                message_id="<background@example.test>",
                subject="Háttér",
                body="Háttérben indexelt levél.",
            )
        )
        progress_updates: list[object] = []
        job = self.service.start(
            discover_sources(mailbox),
            progress_callback=progress_updates.append,
        )

        summary = job.result(timeout=5)

        self.assertEqual(summary.status, "completed")
        self.assertEqual(job.progress.status, "completed")
        self.assertTrue(progress_updates)
        self.assertEqual(self.database.occurrence_count(), 1)


if __name__ == "__main__":
    unittest.main()
