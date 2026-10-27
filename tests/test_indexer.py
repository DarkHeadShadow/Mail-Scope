__author__ = "Bálint Szabolcs"

import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from email import policy
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

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
    sent_date: str = "Sat, 1 Jan 2022 00:00:00 +0000",
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
    message["Date"] = sent_date
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

    def test_archive_orders_by_parsed_timestamp_not_date_header_text(self) -> None:
        """Use normalized timestamps when weekday text conflicts with date order."""
        source = self.case_directory / "date-order.mbox"
        source.write_bytes(
            _mail(
                message_id="<jan-three@example.test>",
                subject="Jan 3",
                body="Monday message",
                sent_date="Mon, 3 Jan 2022 00:00:00 +0000",
            )
            + _mail(
                message_id="<jan-two@example.test>",
                subject="Jan 2",
                body="Sunday message",
                sent_date="Sun, 2 Jan 2022 00:00:00 +0000",
            )
            + _mail(
                message_id="<jan-five@example.test>",
                subject="Jan 5",
                body="Wednesday message",
                sent_date="Wed, 5 Jan 2022 00:00:00 +0000",
            )
        )
        self.service.index_sources(discover_sources(source))

        messages = self.database.archive_messages()

        self.assertEqual([row["subject"] for row in messages], ["Jan 5", "Jan 3", "Jan 2"])

    def test_message_index_uses_one_database_connection_and_wal(self) -> None:
        source = self.case_directory / "single-message.mbox"
        source.write_bytes(
            _mail(
                message_id="<single@example.test>",
                subject="Single message",
                body="Single-message transaction",
            )
        )

        larger = self.case_directory / "three-messages.mbox"
        larger.write_bytes(
            b"".join(
                _mail(
                    message_id=f"<batch-{index}@example.test>",
                    subject=f"Batch {index}",
                    body="Batched transaction",
                )
                for index in range(3)
            )
        )
        call_counts = []
        for mailbox in (source, larger):
            with patch.object(
                self.database,
                "connection",
                wraps=self.database.connection,
            ) as connection:
                summary = self.service.index_sources(discover_sources(mailbox))
            call_counts.append(connection.call_count)

        self.assertEqual(summary.messages_indexed, 3)
        # Messages share one batched transaction, so the number of database
        # connections does not depend on the number of messages.
        self.assertEqual(call_counts[0], call_counts[1])
        with self.database.connection() as database_connection:
            journal_mode = database_connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]
        self.assertEqual(journal_mode.casefold(), "wal")

    def test_subject_thread_fallback_uses_normalized_subject_index(self) -> None:
        with self.database.connection() as connection:
            query_plan = connection.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT thread_key FROM messages
                WHERE normalized_subject = ?
                ORDER BY id LIMIT 1
                """,
                ("project subject",),
            ).fetchall()

        self.assertTrue(
            any(
                "idx_messages_normalized_subject" in str(row["detail"])
                for row in query_plan
            ),
            [row["detail"] for row in query_plan],
        )

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

        # Indexing grouped the two addresses sharing a display name automatically.
        automatic = {person.display_name: person for person in directory.people()}
        self.assertEqual(automatic["Teszt Antal"].origin, "auto")
        self.assertEqual(
            automatic["Teszt Antal"].addresses,
            ("antal.masik@pelda.hu", "antal@pelda.hu"),
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
        # One hand-made person remains; the recipient got an automatic person.
        self.assertEqual(
            sorted((p.display_name, p.origin) for p in directory.people()),
            [("Antal Teszt", "manual"), ("Teszt Anna", "auto")],
        )

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

    def test_subject_fallback_needs_reply_prefix_and_shared_person(self) -> None:
        """Join a reply to the closest-dated same-subject thread it shares a person with."""
        mailbox = self.case_directory / "recurring.mbox"
        mailbox.write_bytes(
            _mail(
                message_id="<szamla-januar@example.test>",
                subject="Számla",
                body="Januári számla.",
                sent_date="Mon, 10 Jan 2022 09:00:00 +0000",
            )
            + _mail(
                message_id="<szamla-julius@example.test>",
                subject="Számla",
                body="Júliusi számla.",
                sent_date="Mon, 11 Jul 2022 09:00:00 +0000",
            )
            + _mail(
                message_id="<re-szamla-julius@example.test>",
                subject="Re: Számla",
                body="Köszönöm, megkaptam.",
                from_address="anna@pelda.hu",
                to_address="antal@pelda.hu",
                sent_date="Tue, 12 Jul 2022 09:00:00 +0000",
            )
            + _mail(
                message_id="<re-szamla-idegen@example.test>",
                subject="Re: Számla",
                body="Egészen más ügy.",
                from_address="cecil@pelda.hu",
                to_address="dora@pelda.hu",
                sent_date="Wed, 13 Jul 2022 09:00:00 +0000",
            )
            + _mail(
                message_id=None,
                subject="",
                body="Tárgy nélküli első levél.",
            )
            + _mail(
                message_id=None,
                subject="",
                body="Tárgy nélküli második levél.",
            )
            + _mail(
                message_id="<re-szamla-kesoi@example.test>",
                subject="Re: Számla",
                body="Fél év után is erre a számlára válaszolok.",
                from_address="anna@pelda.hu",
                to_address="antal@pelda.hu",
                sent_date="Mon, 30 Jan 2023 09:00:00 +0000",
            )
        )
        self.service.index_sources(discover_sources(mailbox))

        with self.database.connection() as connection:
            keys = [
                row["thread_key"]
                for row in connection.execute("SELECT thread_key FROM messages ORDER BY id")
            ]

        self.assertEqual(len(keys), 7)
        self.assertNotEqual(keys[0], keys[1])
        self.assertEqual(keys[2], keys[1])
        self.assertNotIn(keys[3], {keys[0], keys[1]})
        self.assertNotEqual(keys[4], keys[5])
        # More than 60 days later, still the closest July thread.
        self.assertEqual(keys[6], keys[1])

    def test_fixed_in_reply_to_of_notifications_does_not_form_one_thread(self) -> None:
        """Split messages that only share an absent parent but not their subject."""
        notifications = b"".join(
            _mail(
                message_id=f"<ertesites-{index}@example.test>",
                subject=f"Értesítés {index}. ügyiratról",
                body="Automatikus értesítés.",
                from_address="ertesito@pelda.hu",
                in_reply_to="<allando@pelda.hu>",
            )
            for index in range(4)
        )
        replies = _mail(
            message_id="<valasz-1@example.test>",
            subject="Re: Hiányzó első levél",
            body="Válasz.",
            in_reply_to="<hianyzo@example.test>",
        ) + _mail(
            message_id="<valasz-2@example.test>",
            subject="Re: Re: Hiányzó első levél",
            body="Második válasz.",
            references="<hianyzo@example.test> <valasz-1@example.test>",
        )
        mailbox = self.case_directory / "notifications.mbox"
        mailbox.write_bytes(notifications + replies)

        self.service.index_sources(discover_sources(mailbox))

        with self.database.connection() as connection:
            keys = [
                row["thread_key"]
                for row in connection.execute("SELECT thread_key FROM messages ORDER BY id")
            ]
        self.assertEqual(len(set(keys[:4])), 4)
        self.assertEqual(keys[4], keys[5])

    def test_user_writes_during_background_indexing_neither_fail_nor_wait(self) -> None:
        """Index 5000 messages while people and labels are written concurrently."""
        message_count = 5000
        lines = []
        for index in range(message_count):
            lines.append(
                (
                    f"From sender@example.test Sat Jan  1 00:00:00 2022\n"
                    f"From: Teszt Antal <antal@pelda.hu>\n"
                    f"To: Teszt Anna <anna@pelda.hu>\n"
                    f"Subject: Terheléses teszt {index}\n"
                    f"Date: Sat, 1 Jan 2022 00:00:00 +0000\n"
                    f"Message-ID: <terheles-{index}@pelda.hu>\n"
                    f"\n"
                    f"Kitalált törzs a {index}. levélhez, szerződés és egyeztetés.\n"
                    f"{'Ismételt kitalált szöveg. ' * 20}\n"
                ).encode("utf-8")
            )
        mailbox = self.case_directory / "terheles.mbox"
        mailbox.write_bytes(b"".join(lines))
        job = self.service.start(discover_sources(mailbox))

        durations: list[float] = []
        people_during_indexing = 0
        labels_during_indexing = 0
        person_number = 0
        while not job.wait(0.05):
            person_number += 1
            started = time.perf_counter()
            self.database.create_person(
                f"Teszt Személy {person_number}", (f"szemely{person_number}@pelda.hu",)
            )
            durations.append(time.perf_counter() - started)
            if not job.wait(0):
                people_during_indexing += 1
            with self.database.connection() as connection:
                message_ids = tuple(
                    int(row["id"])
                    for row in connection.execute("SELECT id FROM messages ORDER BY id LIMIT 5")
                )
            if message_ids:
                started = time.perf_counter()
                self.database.set_message_label(message_ids, "Fontos", enabled=True)
                durations.append(time.perf_counter() - started)
                if not job.wait(0):
                    labels_during_indexing += 1

        summary = job.result()
        self.assertEqual(summary.status, "completed")
        self.assertEqual(summary.messages_indexed, message_count)
        self.assertEqual(self.database.message_count(), message_count)
        self.assertGreaterEqual(people_during_indexing, 10)
        self.assertGreaterEqual(labels_during_indexing, 1)
        self.assertLess(max(durations), 2.0)

    def test_user_write_reports_a_busy_database_in_hungarian(self) -> None:
        """Give up after the user-write timeout with a readable message."""
        from indexer import DatabaseBusyError

        with patch("indexer.database.USER_WRITE_TIMEOUT_SECONDS", 0.2):
            with self.database.connection(write=True):
                started = time.perf_counter()
                with self.assertRaisesRegex(DatabaseBusyError, "Az indexelés éppen ír"):
                    self.database.create_person("Teszt Antal", ("antal@pelda.hu",))
                self.assertLess(time.perf_counter() - started, 2.0)

    def test_one_failing_source_does_not_stop_the_others(self) -> None:
        """Record an unreadable source as failed and continue with the next one."""
        broken = self.case_directory / "a-broken.mbox"
        broken.write_bytes(_mail(message_id="<broken@example.test>", subject="A", body="A"))
        healthy = self.case_directory / "b-healthy.mbox"
        healthy.write_bytes(_mail(message_id="<healthy@example.test>", subject="B", body="B"))

        from sources.reader_factory import open_source

        def reader_factory(source):
            if source.path == broken:
                raise IndexError("synthetic parser failure")
            return open_source(source)

        summary = self.service.index_sources(
            discover_sources(self.case_directory), reader_factory=reader_factory
        )

        self.assertEqual(summary.status, "completed_with_errors")
        self.assertEqual(summary.sources_failed, 1)
        self.assertEqual(summary.sources_completed, 1)
        self.assertEqual(self.database.message_count(), 1)
        statuses = {Path(row["path"]).name: row["status"] for row in self.database.sources()}
        self.assertEqual(statuses, {"a-broken.mbox": "failed", "b-healthy.mbox": "completed"})

    def test_reuses_discovery_hash_when_file_signature_is_unchanged(self) -> None:
        """Hash a source once at discovery and once after indexing, not three times."""
        mailbox = self.case_directory / "hash-count.mbox"
        mailbox.write_bytes(_mail(message_id="<hash@example.test>", subject="H", body="H"))
        sources = discover_sources(mailbox)

        with patch("indexer.service.hash_source", wraps=__import__(
            "sources.discover", fromlist=["hash_source"]
        ).hash_source) as hashing:
            summary = self.service.index_sources(sources)

        self.assertEqual(summary.status, "completed")
        self.assertEqual(hashing.call_count, 1)
        self.assertIsNotNone(self.database.verified_source(str(mailbox)))

    def test_prefix_search_and_hungarian_query_errors(self) -> None:
        """Find inflected Hungarian words and never surface raw FTS5 syntax errors."""
        mailbox = self.case_directory / "search.mbox"
        mailbox.write_bytes(
            _mail(
                message_id="<szerzodes@example.test>",
                subject="Megállapodás",
                body="Kérem, küldje el a szerződést aláírva.",
            )
        )
        self.service.index_sources(discover_sources(mailbox))

        self.assertEqual(len(self.database.archive_messages("szerződés")), 1)
        self.assertEqual(len(self.database.archive_messages('"a szerződést"')), 1)
        self.assertEqual(len(self.database.archive_messages("szerződés NOT aláírva")), 0)
        # Text without any searchable word is rejected instead of matching everything.
        for meaningless in ("((( OR", '"('):
            with self.assertRaisesRegex(ValueError, "nincs kereshető szó"):
                self.database.archive_messages(meaningless)
            with self.assertRaisesRegex(ValueError, "nincs kereshető szó"):
                self.database.search(meaningless)
        self.assertEqual(len(self.database.archive_messages("   ")), 1)
        self.assertEqual(len(self.database.archive_messages('szerz* AND "')), 1)
        rows, total = self.database.detailed_search(
            ({"field": "text", "value": "szerződés"},), operator="AND"
        )
        self.assertEqual(total, 1)

        from indexer.database import fts_query

        self.assertEqual(fts_query("szerződés"), '"szerződés"*')
        self.assertEqual(fts_query("anna@pelda.hu"), '"anna pelda hu"*')
        self.assertEqual(fts_query('OR "pontos kifejezés" AND'), '"pontos kifejezés"')
        self.assertEqual(fts_query("(a OR b"), '"a"* OR "b"*')
        self.assertEqual(fts_query("NEAR(x y) ^z"), '"NEAR"* ( "x"* "y"* ) "z"*')
        self.assertEqual(fts_query("   "), "")

    def test_body_is_stored_once_in_the_full_text_index(self) -> None:
        """Keep message bodies only in FTS while details still return the body."""
        mailbox = self.case_directory / "body.mbox"
        mailbox.write_bytes(
            _mail(message_id="<body@example.test>", subject="Törzs", body="Egyszer tárolt törzs.")
        )
        self.service.index_sources(discover_sources(mailbox))

        with self.database.connection() as connection:
            stored = connection.execute("SELECT id, body FROM messages").fetchone()
        self.assertEqual(stored["body"], "")
        details = self.database.message_details(int(stored["id"]))
        assert details is not None
        self.assertIn("Egyszer tárolt törzs.", details["body"])

    def test_between_mode_excludes_notes_to_self(self) -> None:
        """A message from a person to themselves is not part of a two-person conversation."""
        mailbox = self.case_directory / "self.mbox"
        mailbox.write_bytes(
            _mail(
                message_id="<self@example.test>",
                subject="Saját jegyzet",
                body="Emlékeztető.",
                to_address="antal@pelda.hu",
            )
            + _mail(
                message_id="<pair@example.test>",
                subject="Kettőnk között",
                body="Valódi levélváltás.",
            )
        )
        self.service.index_sources(discover_sources(mailbox))
        directory = PersonDirectory(self.database)
        antal = directory.create_person("Teszt Antal", ("antal@pelda.hu",))
        anna = directory.create_person("Teszt Anna", ("anna@pelda.hu",))

        rows = self.database.conversation_messages(
            (antal.id, anna.id), mode="between", include_cc_bcc=False
        )

        self.assertEqual([row["subject"] for row in rows], ["Kettőnk között"])

    def test_exchange_legacy_addresses_resolve_to_smtp(self) -> None:
        """Map Exchange DN participants to SMTP once any message reveals the pair."""
        from indexer.service import _message_keys
        from sources.models import MessageLocator, MessageMetadata

        source_id, _ = self.database.register_source(
            path=str(self.case_directory / "synthetic.pst"),
            kind="pst",
            size=1,
            discovered_sha256="0" * 64,
            sha256_before="0" * 64,
        )
        folder_id = self.database.add_folder(source_id, "Sent Items", "Sent Items")
        legacy = "/O=PELDA/OU=EXCHANGE ADMINISTRATIVE GROUP (FYDIBOHF23SPDLT)/CN=RECIPIENTS/CN=ANTAL"
        sent_only_dn = MessageMetadata(
            locator=MessageLocator(0, 0, "Sent Items", 0, "1"),
            folder="Sent Items",
            subject="Elküldött",
            from_addresses=(legacy,),
            to_addresses=("anna@pelda.hu",),
            cc_addresses=(),
            bcc_addresses=(),
            date="Sat, 1 Jan 2022 00:00:00 +0000",
            message_id="<sent-dn@example.test>",
            in_reply_to=None,
            references=None,
            attachment_names=(),
            participant_names=(("from", legacy, "Teszt Antal"),),
        )
        revealing = MessageMetadata(
            locator=MessageLocator(0, 0, "Sent Items", 1, "2"),
            folder="Sent Items",
            subject="Másik",
            from_addresses=("antal@pelda.hu",),
            to_addresses=("anna@pelda.hu",),
            cc_addresses=(),
            bcc_addresses=(),
            date="Sun, 2 Jan 2022 00:00:00 +0000",
            message_id="<reveal@example.test>",
            in_reply_to=None,
            references=None,
            attachment_names=(),
            address_aliases=((legacy, "antal@pelda.hu"),),
        )
        for metadata in (sent_only_dn, revealing):
            dedup_key, thread_key, normalized_subject, content_hash = _message_keys(
                source_id, metadata
            )
            self.database.store_message(
                source_id=source_id,
                folder_id=folder_id,
                metadata=metadata,
                dedup_key=dedup_key,
                thread_key=thread_key,
                normalized_subject=normalized_subject,
                content_hash=content_hash,
                message_hash=content_hash,
            )

        self.database.resolve_address_aliases()

        with self.database.connection() as connection:
            senders = {
                row["address"]
                for row in connection.execute(
                    "SELECT address FROM participants WHERE role = 'from'"
                )
            }
        self.assertEqual(senders, {"antal@pelda.hu"})

    def test_automatic_people_are_editable_and_respect_manual_edits(self) -> None:
        """Group by display name, keep hand edits, and never re-add removed aliases."""
        mailbox = self.case_directory / "auto-people.mbox"
        mailbox.write_bytes(
            # The fixture names every To address "Teszt Anna", so all mails go to Anna.
            _mail(message_id="<a1@example.test>", subject="A1", body="x", from_address="anna@pelda.hu",
                  from_display_name="Teszt Anna", to_address="anna@pelda.hu")
            + _mail(message_id="<a2@example.test>", subject="A2", body="x", from_address="anna.magan@pelda.hu",
                    from_display_name='"Teszt  Anna"', to_address="anna@pelda.hu")
            + _mail(message_id="<a3@example.test>", subject="A3", body="x", from_address="noname@pelda.hu",
                    from_display_name="", to_address="anna@pelda.hu")
        )
        self.service.index_sources(discover_sources(mailbox))
        directory = PersonDirectory(self.database)

        def by_name() -> dict[str, object]:
            return {person.display_name: person for person in directory.people()}

        people = by_name()
        self.assertEqual(people["Teszt Anna"].addresses, ("anna.magan@pelda.hu", "anna@pelda.hu"))
        self.assertEqual(people["Teszt Anna"].origin, "auto")
        self.assertEqual(people["noname@pelda.hu"].addresses, ("noname@pelda.hu",))

        anna = people["Teszt Anna"]
        split = directory.split_address(anna.id, "anna.magan@pelda.hu")
        self.assertEqual(split.addresses, ("anna.magan@pelda.hu",))
        self.assertEqual(split.origin, "manual")
        anna = by_name()["Teszt Anna"]
        self.assertEqual(anna.origin, "manual")
        self.assertEqual(anna.addresses, ("anna@pelda.hu",))

        noname = by_name()["noname@pelda.hu"]
        self.assertTrue(directory.remove_address(noname.id, "noname@pelda.hu"))
        directory.auto_assign()
        self.assertFalse(any("noname@pelda.hu" in p.addresses for p in directory.people()))

        renamed = directory.rename(split.id, "Anna magáncím")
        merged = directory.merge(anna.id, renamed.id)
        self.assertEqual(merged.addresses, ("anna.magan@pelda.hu", "anna@pelda.hu"))
        directory.delete_person(merged.id)
        directory.auto_assign()
        self.assertFalse(any("anna@pelda.hu" in p.addresses for p in directory.people()))

        moved_to = directory.create_person("Kézi Anna", ())
        directory.assign_address(moved_to.id, "anna@pelda.hu")
        holder = directory.create_person("Másik", ())
        with self.assertRaises(ValueError):
            directory.assign_address(holder.id, "anna@pelda.hu")
        moved = directory.assign_address(holder.id, "anna@pelda.hu", move=True)
        self.assertIn("anna@pelda.hu", moved.addresses)
        self.assertNotIn("Kézi Anna", by_name())

    def test_attachment_counts_and_attachment_only_filter(self) -> None:
        """Expose attachment counts and restrict whole queries to mails with attachments."""
        mailbox = self.case_directory / "attachments.mbox"
        mailbox.write_bytes(
            _mail(message_id="<att@example.test>", subject="Mellékletes szerződés",
                  body="szerződés", attachment=True)
            + _mail(message_id="<plain@example.test>", subject="Sima szerződés",
                    body="szerződés")
            + _mail(message_id="<other@example.test>", subject="Számla",
                    body="számla")
        )
        self.service.index_sources(discover_sources(mailbox))

        rows, _ = self.database.detailed_search(
            ({"field": "text", "value": "szerződés"},), operator="AND"
        )
        counts = {row["subject"]: (row["attachment_count"], row["attachment_names"]) for row in rows}
        self.assertEqual(counts["Mellékletes szerződés"], (1, "bizonyitek.txt"))
        self.assertEqual(counts["Sima szerződés"], (0, None))
        archive = {row["subject"]: row["attachment_count"] for row in self.database.archive_messages()}
        self.assertEqual(archive["Mellékletes szerződés"], 1)
        self.assertEqual(archive["Számla"], 0)

        rows, total = self.database.detailed_search(
            ({"field": "text", "value": "szerződés"},), operator="AND", has_attachment=True
        )
        self.assertEqual((total, [row["subject"] for row in rows]), (1, ["Mellékletes szerződés"]))
        groups = (
            {"operator": "AND", "conditions": [{"field": "text", "value": "szerződés"}]},
            {"operator": "AND", "conditions": [{"field": "subject", "value": "Számla"}]},
        )
        _, without_filter = self.database.detailed_search(groups, operator="OR")
        rows, with_filter = self.database.detailed_search(groups, operator="OR", has_attachment=True)
        self.assertEqual(without_filter, 3)
        self.assertEqual(with_filter, 1)
        self.assertEqual(rows[0]["subject"], "Mellékletes szerződés")

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
            has_saved_searches = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'saved_searches'
                """
            ).fetchone()

        self.assertIn("display_name", columns)
        self.assertEqual(participant["address"], "antal@pelda.hu")
        self.assertEqual(participant["display_name"], "")
        self.assertEqual(normalized_id, "legacy@example.test")
        with upgraded.connection() as connection:
            timestamp = connection.execute(
                "SELECT sent_timestamp FROM messages WHERE id = 1"
            ).fetchone()["sent_timestamp"]
        self.assertEqual(timestamp, 1640995200)
        self.assertEqual(upgraded.metadata_value("schema_version"), "6")
        self.assertIsNotNone(has_saved_searches)

    def test_detailed_search_labels_and_saved_searches(self) -> None:
        """Search metadata, deduplicate hits, tag every copy, and persist queries."""
        root_id = "<detailed-root@example.test>"
        first = self.case_directory / "detailed-first.mbox"
        first.write_bytes(
            _mail(
                message_id=root_id,
                subject="Kitalált bizonyíték",
                body="Kitalált részletes keresési kifejezés.",
                attachment=True,
            )
            + _mail(
                message_id="<detailed-next@example.test>",
                subject="Másik üzenet",
                body="Másik kitalált tartalom.",
            )
        )
        duplicate = self.case_directory / "detailed-copy.mbox"
        duplicate.write_bytes(
            _mail(
                message_id=root_id,
                subject="Kitalált bizonyíték",
                body="Kitalált részletes keresési kifejezés.",
                attachment=True,
            )
        )
        self.service.index_sources(discover_sources(self.case_directory))
        root_message_id = next(
            int(row["id"])
            for row in self.database.search("bizonyíték")
            if row["message_id"] == root_id
        )

        text_rows, text_total = self.database.detailed_search(
            ({"field": "text", "value": "részletes keresési kifejezés"},),
            operator="AND",
        )
        self.assertEqual(text_total, 1)
        self.assertEqual(len(text_rows), 1)
        self.assertEqual(text_rows[0]["message_id"], root_message_id)
        unicode_case_rows, unicode_case_total = self.database.detailed_search(
            ({"field": "subject", "value": "BIZONYÍTÉK"},),
            operator="AND",
        )
        self.assertEqual(unicode_case_total, 1)
        self.assertEqual(unicode_case_rows[0]["message_id"], root_message_id)

        attachment_rows, attachment_total = self.database.detailed_search(
            (
                {"field": "has_attachment", "value": "yes"},
                {"field": "sent_after", "value": "2022-01-01"},
                {"field": "sent_before", "value": "2022-01-01"},
            ),
            operator="AND",
        )
        self.assertEqual(attachment_total, 1)
        self.assertEqual(attachment_rows[0]["message_id"], root_message_id)
        self.assertEqual(len(attachment_rows), 1)

        grouped_rows, grouped_total = self.database.detailed_search(
            (
                {
                    "operator": "OR",
                    "conditions": [
                        {"field": "subject", "value": "bizonyíték"},
                        {"field": "from", "value": "ismeretlen@pelda.hu"},
                    ],
                },
                {
                    "operator": "AND",
                    "conditions": [
                        {"field": "has_attachment", "value": "yes"},
                        {"field": "sent_after", "value": "2022-01-01"},
                    ],
                },
            ),
            operator="AND",
        )
        self.assertEqual(grouped_total, 1)
        self.assertEqual(grouped_rows[0]["message_id"], root_message_id)

        or_rows, or_total = self.database.detailed_search(
            (
                {"field": "subject", "value": "bizonyíték"},
                {"field": "from", "value": "ismeretlen@pelda.hu"},
            ),
            operator="OR",
        )
        self.assertEqual(or_total, 1)
        self.assertEqual(len(or_rows), 1)

        self.assertEqual(
            self.database.set_message_label((root_message_id,), "Ellenőrzendő", enabled=True),
            2,
        )
        labeled_rows, labeled_total = self.database.detailed_search(
            ({"field": "label", "value": "ellenőrzendő"},),
            operator="AND",
        )
        self.assertEqual(labeled_total, 1)
        self.assertEqual(labeled_rows[0]["message_id"], root_message_id)
        self.assertEqual(
            self.database.set_message_label((root_message_id,), "Ellenőrzendő", enabled=False),
            2,
        )
        self.assertEqual(
            self.database.detailed_search(
                ({"field": "label", "value": "ellenőrzendő"},),
                operator="AND",
            )[1],
            0,
        )

        saved_query = {
            "conditions": [{"field": "subject", "value": "bizonyíték"}],
            "operator": "AND",
        }
        self.database.save_search("Kitalált keresés", saved_query)
        self.database.save_search("Kitalált keresés", {"conditions": [], "operator": "OR"})
        saved_rows = self.database.saved_searches()
        self.assertEqual(len(saved_rows), 1)
        self.assertEqual(
            json.loads(saved_rows[0]["query_json"]),
            {"conditions": [], "operator": "OR"},
        )
        self.assertTrue(self.database.delete_saved_search("Kitalált keresés"))
        self.assertFalse(self.database.delete_saved_search("Kitalált keresés"))

        with self.assertRaises(ValueError):
            self.database.detailed_search(
                ({"field": "sent_after", "value": "2022-02-30"},),
                operator="AND",
            )
        with self.assertRaises(ValueError):
            self.database.detailed_search(
                ({"field": "subject", "value": "x"},),
                operator="XOR",
            )

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
