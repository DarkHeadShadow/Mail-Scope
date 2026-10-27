__author__ = "Bálint Szabolcs"

import io
import time
import unittest
from pathlib import Path

from attachments import render
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


FIXTURES = Path(__file__).parent / "fixtures" / "attachments"


class DocumentRendererTests(unittest.TestCase):
    """Render the fictional fixtures exactly as the isolated worker would."""

    def fixture(self, name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    def test_views_are_recognized(self) -> None:
        self.assertEqual(detect(self.fixture("sample.pdf"), "a.pdf").view, "pdf")
        self.assertEqual(detect(self.fixture("sample.docx"), "a.docx").view, "docx")
        self.assertEqual(detect(self.fixture("macro.docm"), "a.docm").view, "docx")
        self.assertEqual(detect(self.fixture("sample.xlsx"), "a.xlsx").view, "xlsx")
        self.assertEqual(detect(self.fixture("sample.xls"), "a.xls").view, "xls")
        self.assertEqual(detect(self.fixture("page.html"), "hirlevel.html").view, "html")

    def test_pdf_pages_text_metadata_and_signals(self) -> None:
        page = render.render_pdf(self.fixture("sample.pdf"), 1)
        self.assertEqual((page["pages"], page["page"]), (2, 1))
        self.assertTrue(page["png"].startswith(b"\x89PNG"))
        self.assertIn("Vegszamla", page["text"])
        analysis = render.analyze(self.fixture("sample.pdf"), "pdf")
        self.assertEqual(analysis["metadata"]["Szerző"], "Teszt Anna")
        self.assertEqual(analysis["metadata"]["Létrehozva"], "2023-05-10 16:58")
        self.assertEqual(analysis["signals"], [])
        self.assertTrue(analysis["text_layer"])
        self.assertIn("kotber", analysis["pages_text"][0])

        scripted = render.analyze(self.fixture("javascript.pdf"), "pdf")
        self.assertEqual(scripted["security"]["Beágyazott JavaScript"], "van")
        self.assertEqual(scripted["security"]["Külső hivatkozás"], "van")
        self.assertEqual(scripted["security"]["Beágyazott fájl"], "nincs")
        image_only = render.analyze(self.fixture("image-only.pdf"), "pdf")
        self.assertFalse(image_only["text_layer"])
        self.assertTrue(any("OCR" in signal for signal in image_only["signals"]))

    def test_docx_is_sanitized_html_with_properties_and_macro_flag(self) -> None:
        converted = render.render_docx(self.fixture("sample.docx"), "sample.docx")
        self.assertIn("<h1>Fizetési ütemezés és késedelmi kötbér</h1>", converted["document"])
        self.assertIn("<table>", converted["document"])
        self.assertNotIn("href", converted["document"])
        self.assertIn("Kötbér: napi 0,2%.", converted["text"])
        properties = render.analyze(self.fixture("sample.docx"), "docx")
        self.assertEqual(properties["metadata"]["Szerző"], "Teszt Anna")
        self.assertEqual(properties["metadata"]["Utolsó mentő"], "Teszt Béla")
        self.assertTrue(any("Külső hivatkozás" in signal for signal in properties["signals"]))
        macro = render.analyze(self.fixture("macro.docm"), "docx")
        self.assertTrue(any("Makrót tartalmaz" in signal for signal in macro["signals"]))

    def test_spreadsheets_show_hidden_parts_formulas_and_comments(self) -> None:
        sheet = render.render_xlsx(self.fixture("sample.xlsx"))
        self.assertEqual(
            [(item["name"], item["state"]) for item in sheet["sheets"]],
            [("Számítás", "visible"), ("Rejtett lap", "hidden"), ("Nagyon rejtett", "veryHidden")],
        )
        self.assertEqual(sheet["formulas"], {"D6": "=SUM(D2:D5)"})
        self.assertIn("D6", sheet["comments"])
        self.assertEqual((sheet["hidden_rows"], sheet["hidden_columns"]), ([4], ["F"]))
        self.assertEqual(sheet["rows"][1][:2], ["Előleg", "1000000"])
        hidden = render.render_xlsx(self.fixture("sample.xlsx"), 2)
        self.assertEqual(hidden["rows"], [["nagyon rejtett adat"]])
        analysis = render.analyze(self.fixture("sample.xlsx"), "xlsx")
        self.assertTrue(any("Nagyon rejtett munkalap" in signal for signal in analysis["signals"]))
        self.assertEqual(analysis["metadata"]["Képletek"], "1")
        self.assertTrue(any("Makrót" in s for s in render.analyze(self.fixture("macro.xlsm"), "xlsx")["signals"]))

        legacy = render.render_xls(self.fixture("sample.xls"))
        self.assertEqual([item["state"] for item in legacy["sheets"]], ["visible", "hidden"])
        self.assertEqual(legacy["rows"][1], ["Előleg", "200000"])

    def test_html_document_drops_scripts_handlers_and_remote_content(self) -> None:
        document = render.render_html_document(self.fixture("page.html"), "page.html")["document"]
        for forbidden in ("<script", "onclick", "javascript:", "https://pelda.hu", "mailScopeHacked"):
            self.assertNotIn(forbidden, document)
        self.assertIn("<h1>Kitalált hírlevél</h1>", document)

    def test_pdf_renders_in_the_isolated_worker(self) -> None:
        page = run_isolated("attachments.render:render_pdf", self.fixture("sample.pdf"), 0)
        self.assertEqual(page["pages"], 2)


if __name__ == "__main__":
    unittest.main()
