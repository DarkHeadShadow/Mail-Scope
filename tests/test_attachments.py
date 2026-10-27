__author__ = "Bálint Szabolcs"

import io
import time
import unittest

from attachments.detect import detect, is_suspicious_name
from attachments.sandbox import AttachmentProcessingError, run_isolated


def _png() -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (4, 3), (40, 200, 120)).save(output, format="PNG")
    return output.getvalue()


class DetectTests(unittest.TestCase):
    def test_real_type_and_warnings(self) -> None:
        pdf = detect(b"%PDF-1.7\n...", "szerzodes.pdf", "application/pdf")
        self.assertEqual((pdf.kind, pdf.warnings, pdf.executable), ("pdf", (), False))

        disguised = detect(b"MZ\x90\x00" + b"\x00" * 60, "fizetes_reszletek.pdf", "application/pdf")
        self.assertEqual(disguised.kind, "exe")
        self.assertTrue(disguised.executable)
        self.assertEqual(disguised.view, "none")
        self.assertTrue(any("álcázott" in warning for warning in disguised.warnings))

        double = detect(b"MZ" + b"\x00" * 60, "fizetes_reszletek.pdf.exe", "application/octet-stream")
        self.assertTrue(any("Kettős kiterjesztés" in warning for warning in double.warnings))

        zip_named_pdf = detect(b"PK\x03\x04" + b"\x00" * 40, "szamla.pdf", "application/pdf")
        self.assertEqual(zip_named_pdf.kind, "zip")
        self.assertTrue(zip_named_pdf.warnings)

        self.assertEqual(detect(_png(), "kep.png").view, "image")
        self.assertEqual(detect("árvíztűrő;tükör\n1;2\n".encode("cp1250"), "tabla.csv").view, "table")
        self.assertEqual(detect(b"Hello\nworld\n", "jegyzet.txt").view, "text")
        self.assertTrue(is_suspicious_name("szamla.pdf.exe"))
        self.assertTrue(is_suspicious_name("telepito.MSI"))
        self.assertFalse(is_suspicious_name("szerzodes.pdf"))


class SandboxTests(unittest.TestCase):
    def test_result_errors_crash_and_timeout_do_not_affect_the_caller(self) -> None:
        self.assertEqual(run_isolated("tests.sandbox_targets:echo", b"abc"), 3)
        with self.assertRaisesRegex(AttachmentProcessingError, "sérült fájl"):
            run_isolated("tests.sandbox_targets:fail")
        with self.assertRaisesRegex(AttachmentProcessingError, "váratlanul leállt"):
            run_isolated("tests.sandbox_targets:crash")
        started = time.monotonic()
        with self.assertRaisesRegex(AttachmentProcessingError, "időkorlát"):
            run_isolated("tests.sandbox_targets:hang", timeout=2)
        self.assertLess(time.monotonic() - started, 15)
        with self.assertRaises(AttachmentProcessingError):
            run_isolated("tests.sandbox_targets:hog", memory_mb=256)
        # The caller keeps working after all of the above.
        self.assertEqual(run_isolated("tests.sandbox_targets:echo", b""), 0)


if __name__ == "__main__":
    unittest.main()
