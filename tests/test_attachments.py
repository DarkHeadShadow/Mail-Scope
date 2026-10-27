__author__ = "Bálint Szabolcs"

import io
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from attachments import containers, libreoffice, render
from attachments.detect import detect, is_suspicious_name
from attachments.sandbox import AttachmentProcessingError, run_isolated
from attachments.service import describe, expand
from sources.models import Attachment


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


class ContainerTests(unittest.TestCase):
    """Archives and attached e-mails, unpacked in memory within the limits."""

    def fixture(self, name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    def test_containers_are_recognized(self) -> None:
        for name, kind, view in (
            ("archive.zip", "zip", "archive"), ("archive.7z", "7z", "archive"),
            ("message.eml", "email", "email"), ("message.msg", "msg", "email"),
        ):
            info = detect(self.fixture(name), name)
            self.assertEqual((info.kind, info.view, info.warnings), (kind, view, ()), name)
        # An OLE file is only an Outlook item when it carries MAPI property streams.
        self.assertEqual(detect(self.fixture("sample.xls"), "a.msg").kind, "ole")

    def test_zip_lists_and_unpacks_nested_files(self) -> None:
        result = containers.expand_archive(self.fixture("archive.zip"), "zip", "archive.zip")
        self.assertIsNone(result["stopped"])
        names = [entry["inner_path"] for entry in result["entries"]]
        self.assertEqual(names, ["szamlak/szamla-2023-05.pdf", "szamlak/Kotberszamitas.xlsx",
                                 "belso/jegyzokonyv.zip", "fizetes_reszletek.pdf.exe"])
        self.assertEqual(result["entries"][0]["data"], self.fixture("sample.pdf"))
        disguised = detect(result["entries"][3]["data"], result["entries"][3]["name"])
        self.assertTrue(disguised.executable)
        self.assertTrue(any("Kettős kiterjesztés" in warning for warning in disguised.warnings))
        inner = containers.expand_archive(result["entries"][2]["data"], "zip", "jegyzokonyv.zip")
        self.assertIn("Átvette: Teszt Béla", inner["entries"][0]["data"].decode("utf-8"))

    def test_zip_bomb_is_stopped_before_unpacking(self) -> None:
        result = containers.expand_archive(self.fixture("bomb.zip"), "zip", "bomb.zip")
        self.assertIn("Lehetséges ZIP-bomba", result["stopped"])
        self.assertIsNone(result["entries"][0]["data"])
        with mock.patch.object(containers, "MAX_ENTRIES", 2):
            crowded = containers.expand_archive(self.fixture("archive.zip"), "zip", "archive.zip")
        self.assertIn("bejegyzést tartalmaz", crowded["stopped"])

    def test_password_archives(self) -> None:
        for name, kind in (("password.zip", "zip"), ("password.7z", "7z")):
            locked = containers.expand_archive(self.fixture(name), kind, name)
            self.assertTrue(locked["needs_password"], name)
            self.assertTrue(all(entry["data"] is None for entry in locked["entries"]), name)
            wrong = containers.expand_archive(self.fixture(name), kind, name, "rossz")
            self.assertTrue(wrong["password_error"], name)
            opened = containers.expand_archive(self.fixture(name), kind, name, "pelda123")
            self.assertFalse(opened["password_error"], name)
            self.assertTrue(all(entry["data"] for entry in opened["entries"]), name)
        plain = containers.expand_archive(self.fixture("archive.7z"), "7z", "archive.7z")
        self.assertEqual([entry["name"] for entry in plain["entries"]], ["szamla-2023-05.pdf", "feljegyzes.txt"])

    def test_rar_needs_seven_zip_and_listing_is_parsed(self) -> None:
        result = containers.expand_archive(b"Rar!\x1a\x07\x01\x00", "rar", "a.rar", seven_zip=None)
        self.assertIn("7-Zip", result["stopped"])
        listing = (
            "Path = a.rar\nType = Rar5\n\n----------\n"
            "Path = mappa\nFolder = +\nSize = 0\n\n"
            "Path = mappa\\szamla.pdf\nFolder = -\nSize = 1310\nPacked Size = 900\n"
            "Modified = 2023-05-10 16:58:00\nEncrypted = +\n\n"
        )
        entries = containers.parse_seven_zip_listing(listing)
        self.assertEqual(
            [(entry["inner_path"], entry["size"], entry["compressed"], entry["encrypted"], entry["modified"]) for entry in entries],
            [("mappa/szamla.pdf", 1310, 900, True, "2023-05-10 16:58")],
        )

    def test_msg_reads_headers_body_and_attachments(self) -> None:
        preview = containers.render_email(self.fixture("message.msg"), "msg", "message.msg")
        headers = dict(preview["headers"])
        self.assertEqual(headers["From"], "Teszt Anna <teszt.anna@pelda.hu>")
        self.assertEqual(headers["To"], "Teszt Béla <teszt.bela@pelda.hu>")
        self.assertEqual(headers["Subject"], "Szerződéstervezet – Outlook-levélként")
        self.assertIn("Csatolom a szerződéstervezetet", preview["text"])
        self.assertTrue(preview["converted"])
        children = containers.expand_email(self.fixture("message.msg"), "msg")["entries"]
        self.assertEqual(children[0]["data"], self.fixture("sample.pdf"))
        attached = children[1]
        self.assertEqual(attached["name"], "Egyeztetés a helyszíni bejárásról.eml")
        inner = containers.render_email(attached["data"], "email", attached["name"])
        self.assertEqual(dict(inner["headers"])["From"], "Teszt Béla <teszt.bela@pelda.hu>")
        self.assertIn("A bejárás jövő kedden", inner["text"])

    def test_eml_lists_only_its_own_attachments_and_sanitizes_html(self) -> None:
        preview = containers.render_email(self.fixture("message.eml"), "email", "message.eml")
        self.assertEqual(preview["attachments"], ["szamlak.zip", "szerzodes.msg"])
        self.assertNotIn("<script", preview["document"])
        self.assertIn("<b>számlákat</b>", preview["document"])
        children = containers.expand_email(self.fixture("message.eml"), "email")["entries"]
        self.assertEqual(detect(children[1]["data"], children[1]["name"]).kind, "msg")

    def test_expansion_runs_in_the_isolated_worker(self) -> None:
        entry = describe([Attachment("archive.zip", "application/zip", self.fixture("archive.zip"))])[0]
        result = expand(entry, self.fixture("archive.zip"), None, None)
        self.assertEqual([child["path"] for child in result["entries"]], ["0/0", "0/1", "0/2", "0/3"])
        self.assertEqual(result["entries"][2]["container"], "archive")
        self.assertEqual(result["entries"][0]["trail"], "1. melléklet › szamlak/szamla-2023-05.pdf")
        self.assertTrue(result["entries"][3]["executable"])


class LibreOfficeTests(unittest.TestCase):
    """Pontos nézet: detection and the conversion call (mocked), plus a real run when installed."""

    def fixture(self, name: str) -> bytes:
        return (Path(__file__).parent / "fixtures" / "attachments" / name).read_bytes()

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_detection_prefers_configured_then_install_folders_then_path(self) -> None:
        program_files = self.root / "Program Files"
        installed = program_files / "LibreOffice" / "program" / "soffice.exe"
        installed.parent.mkdir(parents=True)
        installed.write_bytes(b"MZ")
        chosen = self.root / "Hordozhato" / "program" / "soffice.exe"
        chosen.parent.mkdir(parents=True)
        chosen.write_bytes(b"MZ")
        environment = {"ProgramFiles": str(program_files), "ProgramFiles(x86)": str(self.root / "nincs")}
        with mock.patch.dict(os.environ, environment), mock.patch("shutil.which", return_value=None):
            self.assertEqual(libreoffice.soffice_path(str(chosen)), str(chosen))
            # A configured path that is not soffice falls back to the installed one.
            self.assertEqual(libreoffice.soffice_path(str(self.root / "notepad.exe")), str(installed))
            installed.unlink()
            self.assertIsNone(libreoffice.soffice_path())
        with mock.patch.dict(os.environ, environment), mock.patch("shutil.which", return_value=r"D:\LO\soffice.exe"):
            self.assertEqual(libreoffice.soffice_path(), r"D:\LO\soffice.exe")

    def test_input_extension_follows_the_content(self) -> None:
        self.assertEqual(libreoffice.input_extension("docx", "Szerzodes.DOCM"), "docm")
        self.assertEqual(libreoffice.input_extension("docx", "szamla.pdf"), "docx")
        self.assertEqual(libreoffice.input_extension("ole", "regi.xls"), "xls")
        self.assertEqual(libreoffice.input_extension("rtf", "level"), "rtf")
        entries = describe([
            Attachment("a.docm", "", self.fixture("macro.docm")),
            Attachment("b.odt", "", self.fixture("macro-onload.odt")),
            Attachment("c.pdf", "", self.fixture("sample.pdf")),
            Attachment("d.doc.exe", "", b"MZ" + b"\x00" * 60),
        ])
        self.assertEqual([entry["exact"] for entry in entries], [True, True, False, False])

    def test_conversion_runs_headless_with_a_macro_locked_throwaway_profile(self) -> None:
        work_dir = self.root / "tmp" / "munka"
        seen: dict[str, object] = {}

        class FakeProcess:
            returncode = 0

            def __init__(self, arguments: list[str], **options: object) -> None:
                seen["arguments"] = arguments
                seen["options"] = options
                seen["profile"] = (work_dir / "profile" / "user" / "registrymodifications.xcu").read_text(encoding="utf-8")
                seen["input"] = Path(arguments[-1]).read_bytes()
                outdir = Path(arguments[arguments.index("--outdir") + 1])
                (outdir / f"{Path(arguments[-1]).stem}.pdf").write_bytes(b"%PDF-1.7 pontos")

            def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
                seen["timeout"] = timeout
                return b"", b""

        with mock.patch("subprocess.Popen", FakeProcess):
            pdf = libreoffice.convert_to_pdf(r"C:\LO\soffice.exe", b"docx-bajtok", "docx", "szamla.pdf", work_dir)
        self.assertEqual(pdf, b"%PDF-1.7 pontos")
        arguments = seen["arguments"]
        self.assertEqual(arguments[0], r"C:\LO\soffice.exe")
        self.assertTrue(arguments[1].startswith("-env:UserInstallation=file:///"))
        for flag in ("--headless", "--norestore", "--nolockcheck"):
            self.assertIn(flag, arguments)
        self.assertEqual(arguments[arguments.index("--convert-to") + 1], "pdf")
        self.assertTrue(arguments[-1].endswith("dokumentum.docx"))
        self.assertEqual(seen["input"], b"docx-bajtok")
        self.assertEqual(seen["timeout"], 60.0)
        self.assertIn('oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value>', seen["profile"])
        self.assertIn('oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value>', seen["profile"])
        self.assertFalse(work_dir.exists())

    def test_timeout_kills_libreoffice_and_failure_is_reported(self) -> None:
        work_dir = self.root / "tmp" / "lassu"

        class HangingProcess:
            pid = 4242
            returncode = None
            killed = False

            def __init__(self, arguments: list[str], **options: object) -> None:
                pass

            def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
                if timeout is not None and not HangingProcess.killed:
                    raise subprocess.TimeoutExpired("soffice", timeout)
                return b"", b""

            def kill(self) -> None:
                HangingProcess.killed = True

        with mock.patch("subprocess.Popen", HangingProcess), mock.patch("subprocess.run") as run:
            with self.assertRaisesRegex(libreoffice.ConversionError, "időkorlát"):
                libreoffice.convert_to_pdf("soffice", b"x", "docx", "a.docx", work_dir, timeout=0.1)
        self.assertTrue(HangingProcess.killed)
        if os.name == "nt":
            self.assertEqual(run.call_args.args[0][:2], ["taskkill", "/PID"])
        self.assertFalse(work_dir.exists())

        class FailingProcess:
            returncode = 1

            def __init__(self, arguments: list[str], **options: object) -> None:
                pass

            def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
                return b"", b"Error: source file could not be loaded\n"

        with mock.patch("subprocess.Popen", FailingProcess):
            with self.assertRaisesRegex(libreoffice.ConversionError, "could not be loaded"):
                libreoffice.convert_to_pdf("soffice", b"x", "rtf", "a.rtf", self.root / "tmp" / "hibas")
        with self.assertRaises(ValueError):
            libreoffice.convert_to_pdf("soffice", b"x", "pdf", "a.pdf", self.root / "tmp" / "pdf")

    @unittest.skipUnless(libreoffice.soffice_path(), "LibreOffice is not installed")
    def test_real_libreoffice_draws_documents_without_running_macros(self) -> None:
        marker = self.root / "makro-lefutott.txt"
        with mock.patch.dict(os.environ, {"MAILSCOPE_MACRO_MARKER": str(marker)}):
            pdf = libreoffice.convert_to_pdf(
                libreoffice.soffice_path(), self.fixture("macro-onload.odt"), "odf", "makro.odt", self.root / "tmp" / "odt"
            )
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertIn("Kitalalt dokumentum", render.render_pdf(pdf)["text"])
        self.assertFalse(marker.exists(), "the document's Basic macro ran during conversion")
        docm = libreoffice.convert_to_pdf(
            libreoffice.soffice_path(), self.fixture("macro.docm"), "docx", "macro.docm", self.root / "tmp" / "docm"
        )
        self.assertTrue(docm.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
