__author__ = "Bálint Szabolcs"

import hashlib
import tempfile
import unittest
from pathlib import Path

from sources.discover import discover_sources
from sources.reader_factory import open_source


class DiscoverTests(unittest.TestCase):
    def test_discovers_mbox_by_content_and_hashes_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mbox = root / "mailbox-without-extension"
            mbox.write_bytes(
                b"From sender@example.test Sat Jan  1 00:00:00 2022\n"
                b"Subject: Minta\n\nTeszt.\n"
            )
            other = root / "not-a-mailbox.txt"
            other.write_text("not a mailbox", encoding="utf-8")

            sources = discover_sources(root)

            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0].path, mbox)
            self.assertEqual(sources[0].kind, "mbox")
            self.assertEqual(sources[0].size, mbox.stat().st_size)
            self.assertEqual(sources[0].sha256, hashlib.sha256(mbox.read_bytes()).hexdigest())
            reader = open_source(sources[0])
            self.assertEqual(reader.iter_folders()[0].path, "mailbox-without-extension")

    def test_discovers_pst_signature_independent_of_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "archive.bin"
            path.write_bytes(b"!BDN" + b"\0" * 20)

            sources = discover_sources(path)

            self.assertEqual([source.kind for source in sources], ["pst"])

    def test_discovers_eml_and_maps_thunderbird_subfolders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "Inbox"
            inbox.write_bytes(
                b"From sender@example.test Sat Jan  1 00:00:00 2022\n"
                b"Subject: Minta\n\nTeszt.\n"
            )
            child_folder = root / "Inbox.sbd"
            child_folder.mkdir()
            nested = child_folder / "Munka"
            nested.write_bytes(
                b"From sender@example.test Sat Jan  1 00:00:00 2022\n"
                b"Subject: Minta\n\nTeszt.\n"
            )
            eml = root / "sample.eml"
            eml.write_bytes(
                b"From: antal@pelda.hu\r\nSubject: EML\r\n\r\nTeszt.\r\n"
            )
            invalid = root / "not-eml.bin"
            invalid.write_bytes(b"Subject: not an EML header block")
            apple_mail = root / "Apple" / "Inbox.mbox"
            apple_mail.mkdir(parents=True)
            (apple_mail / "mbox").write_bytes(
                b"From sender@example.test Sat Jan  1 00:00:00 2022\n"
                b"Subject: Apple\n\nTeszt.\n"
            )

            sources = discover_sources(root)

            self.assertEqual(
                [(source.kind, source.folder) for source in sources],
                [
                    ("mbox", "Inbox"),
                    ("mbox", "Inbox"),
                    ("mbox", "Inbox/Munka"),
                    ("eml", root.name),
                ],
            )


if __name__ == "__main__":
    unittest.main()
