"""Convert Office documents to PDF with an installed LibreOffice ("Pontos nézet").

LibreOffice is optional and never bundled. Each conversion runs headless
with its own throwaway user profile in the case's ``.mailscope\\tmp`` folder:
the profile locks macros off (security level "very high" and macro
execution disabled), so a macro document is only drawn, never run. The
folder, with the input copy and the profile, is removed after use.
"""

__author__ = "Bálint Szabolcs"

import os
import shutil
import subprocess
from pathlib import Path


CONVERT_TIMEOUT_SECONDS = 60.0
SOFFICE_NAMES = {"soffice.exe", "soffice.com", "soffice"}

# Real kinds LibreOffice can draw; the input copy gets the matching extension
# so LibreOffice picks the right import filter even for a disguised name.
_KIND_EXTENSIONS = {
    "docx": "docx", "xlsx": "xlsx", "pptx": "pptx", "odf": "odt", "ole": "doc", "rtf": "rtf",
}
_KEEP_EXTENSIONS = {
    "docx": {"docx", "docm", "dotx", "dotm"},
    "xlsx": {"xlsx", "xlsm", "xltx", "xltm"},
    "pptx": {"pptx", "pptm", "ppsx"},
    "odf": {"odt", "ods", "odp", "odg"},
    "ole": {"doc", "dot", "xls", "xlt", "ppt", "pps"},
    "rtf": {"rtf"},
}
EXACT_KINDS = frozenset(_KIND_EXTENSIONS)

_PROFILE_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>
</oor:items>
"""


class ConversionError(RuntimeError):
    """LibreOffice could not convert the document (failure or time limit)."""


def soffice_path(configured: str | None = None) -> str | None:
    """Find LibreOffice: the configured path, the usual install folders, then PATH."""
    candidates = []
    if configured:
        candidates.append(Path(configured))
    for variable, default in (("ProgramFiles", r"C:\Program Files"), ("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        candidates.append(Path(os.environ.get(variable, default)) / "LibreOffice" / "program" / "soffice.exe")
    for candidate in candidates:
        if candidate.is_file() and candidate.name.casefold() in SOFFICE_NAMES:
            return str(candidate)
    return shutil.which("soffice")


def input_extension(real_kind: str, filename: str) -> str:
    """Keep a matching original extension, otherwise use the one the content calls for."""
    extension = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
    if extension in _KEEP_EXTENSIONS.get(real_kind, set()):
        return extension
    return _KIND_EXTENSIONS[real_kind]


def convert_to_pdf(
    soffice: str, data: bytes, real_kind: str, filename: str, work_dir: Path,
    timeout: float = CONVERT_TIMEOUT_SECONDS,
) -> bytes:
    """Convert a document to PDF in ``work_dir`` and return the PDF bytes.

    ``work_dir`` is created here and always removed afterwards.
    """
    if real_kind not in EXACT_KINDS:
        raise ValueError("Ez a melléklet nem jeleníthető meg Pontos nézetben.")
    profile = work_dir / "profile"
    output = work_dir / "out"
    source = work_dir / f"dokumentum.{input_extension(real_kind, filename)}"
    try:
        (profile / "user").mkdir(parents=True)
        output.mkdir()
        (profile / "user" / "registrymodifications.xcu").write_text(_PROFILE_SETTINGS, encoding="utf-8")
        source.write_bytes(data)
        arguments = [
            soffice,
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--headless", "--invisible", "--norestore", "--nologo", "--nodefault",
            "--nolockcheck", "--nofirststartwizard",
            "--convert-to", "pdf", "--outdir", str(output), str(source),
        ]
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            _, errors = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            _kill_tree(process)
            raise ConversionError(
                f"A LibreOffice-átalakítás túllépte az időkorlátot ({int(timeout)} s), ezért leállítottuk."
            ) from error
        result = output / f"{source.stem}.pdf"
        if not result.is_file() or result.stat().st_size == 0:
            last_line = (errors or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
            detail = f"; {last_line[0]}" if last_line else ""
            raise ConversionError(
                f"A LibreOffice nem tudta PDF-be alakítani a mellékletet (kilépési kód: {process.returncode}{detail})."
            )
        return result.read_bytes()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _kill_tree(process: subprocess.Popen) -> None:
    """Stop soffice and the soffice.bin it started."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
    process.kill()
    process.communicate()
