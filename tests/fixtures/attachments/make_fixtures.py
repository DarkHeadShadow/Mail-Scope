"""Generate the fictional attachment fixtures used by the attachment-viewer tests.

All content is invented (Teszt Anna, Teszt Béla, @pelda.hu). Run from the
repository root:

    .venv\\Scripts\\python.exe tests\\fixtures\\attachments\\make_fixtures.py

The .xls fixture additionally needs ``xlwt`` (development-only, not a
Mail-Scope dependency); without it the existing sample.xls is kept.
"""

__author__ = "Bálint Szabolcs"

import io
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent


def _pdf(pages: list[str], extra_catalog: str = "", extra_objects: list[str] | None = None,
         info: str = "/Title (Fizetesi utemezes) /Author (Teszt Anna) /Creator (Kitalalt szovegszerkeszto) "
                     "/Producer (Mail-Scope teszt) /CreationDate (D:20230510165800+02'00') "
                     "/ModDate (D:20230512142000+02'00')") -> bytes:
    """Build a small, valid PDF with one Helvetica text stream per page."""
    objects: list[str] = []
    page_ids = []
    first_page_id = 4
    for index, text in enumerate(pages):
        page_id = first_page_id + index * 2
        content_id = page_id + 1
        page_ids.append(page_id)
        if text:
            lines = "".join(
                f"BT /F1 12 Tf 72 {720 - 18 * number} Td ({line}) Tj ET\n"
                for number, line in enumerate(text.split("\n"))
            )
        else:
            lines = "0.2 0.6 0.4 rg 72 500 300 200 re f\n"
        objects.append((page_id, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                                 f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"))
        objects.append((content_id, f"<< /Length {len(lines.encode('latin-1'))} >>\nstream\n{lines}endstream"))
    next_id = first_page_id + len(pages) * 2
    extra = []
    for body in extra_objects or []:
        extra.append((next_id, body))
        next_id += 1
    info_id = next_id
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    all_objects = [
        (1, f"<< /Type /Catalog /Pages 2 0 R {extra_catalog} >>"),
        (2, f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>"),
        (3, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
        *objects,
        *extra,
        (info_id, f"<< {info} >>"),
    ]
    output = io.BytesIO()
    output.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for object_id, body in sorted(all_objects):
        offsets[object_id] = output.tell()
        output.write(f"{object_id} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = output.tell()
    count = max(offsets) + 1
    output.write(f"xref\n0 {count}\n0000000000 65535 f \n".encode("ascii"))
    for object_id in range(1, count):
        output.write(f"{offsets[object_id]:010d} 00000 n \n".encode("ascii"))
    output.write(f"trailer\n<< /Size {count} /Root 1 0 R /Info {info_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return output.getvalue()


def _docx(macro: bool = False) -> bytes:
    """Build a small DOCX (or DOCM) with headings, a table, a link and properties."""
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    main_type = (
        "application/vnd.ms-word.document.macroEnabled.main+xml" if macro
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    )
    files = {
        "[Content_Types].xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/>
<Override PartName="/word/document.xml" ContentType="{main_type}"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        "word/_rels/document.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://pelda.hu/szerzodes" TargetMode="External"/>
</Relationships>""",
        "word/styles.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {w}>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>
</w:styles>""",
        "word/document.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document {w}><w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Fizetési ütemezés és késedelmi kötbér</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">A Megrendelő a részszámlák ellenértékét 30 napon belül egyenlíti ki. </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>Kötbér: napi 0,2%.</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Részszámlák</w:t></w:r></w:p>
<w:tbl>
<w:tr><w:tc><w:p><w:r><w:t>Részszámla</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Arány</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>Előleg</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>20%</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>Végszámla</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>40%</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
<w:p><w:hyperlink r:id="rId9"><w:r><w:t>Szerződés a pelda.hu oldalon</w:t></w:r></w:hyperlink></w:p>
</w:body></w:document>""",
        "docProps/core.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>Szerződés-tervezet</dc:title><dc:creator>Teszt Anna</dc:creator><cp:lastModifiedBy>Teszt Béla</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">2023-05-08T14:12:00Z</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">2023-05-12T11:58:00Z</dcterms:modified>
</cp:coreProperties>""",
        "docProps/app.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Kitalált szövegszerkesztő</Application></Properties>""",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        if macro:
            archive.writestr("word/vbaProject.bin", b"\xd0\xcf\x11\xe0 kitalalt makro, nem futtathato")
    return output.getvalue()


def _xlsx(macro: bool = False) -> bytes:
    """Build an XLSX with hidden sheets, rows, columns, a formula and a comment."""
    from openpyxl import Workbook
    from openpyxl.comments import Comment

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Számítás"
    rows = [
        ("Tétel", "Nettó", "Kötbér %", "Összeg", "Megjegyzés", "Belső"),
        ("Előleg", 1000000, 0.2, 200000, "", "titkos 1"),
        ("1. részszámla", 2000000, 0.2, 400000, "", "titkos 2"),
        ("2. részszámla", 3000000, 0.2, 600000, "", "titkos 3"),
        ("Végszámla", 2198750, 0.2, 439750, "", "titkos 4"),
    ]
    for row in rows:
        sheet.append(row)
    sheet["D6"] = "=SUM(D2:D5)"
    sheet["A6"] = "Összesen"
    sheet["D6"].comment = Comment("Kitalált ellenőrzés: Teszt Béla", "Teszt Béla")
    sheet.row_dimensions[4].hidden = True
    sheet.column_dimensions["F"].hidden = True
    hidden = workbook.create_sheet("Rejtett lap")
    hidden["A1"] = "rejtett adat"
    hidden.sheet_state = "hidden"
    very_hidden = workbook.create_sheet("Nagyon rejtett")
    very_hidden["A1"] = "nagyon rejtett adat"
    very_hidden.sheet_state = "veryHidden"
    workbook.properties.creator = "Teszt Anna"
    workbook.properties.lastModifiedBy = "Teszt Béla"
    workbook.properties.title = "Kötbérszámítás"
    output = io.BytesIO()
    workbook.save(output)
    data = output.getvalue()
    if not macro:
        return data
    source = zipfile.ZipFile(io.BytesIO(data))
    target_bytes = io.BytesIO()
    with zipfile.ZipFile(target_bytes, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        target.writestr("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0 kitalalt makro, nem futtathato")
    return target_bytes.getvalue()


def _xls() -> bytes | None:
    """Build an XLS with one visible and one hidden sheet (needs xlwt)."""
    try:
        import xlwt
    except ImportError:
        return None
    workbook = xlwt.Workbook(encoding="utf-8")
    sheet = workbook.add_sheet("Régi számítás")
    for row, values in enumerate((("Tétel", "Összeg"), ("Előleg", 200000), ("Végszámla", 439750))):
        for column, value in enumerate(values):
            sheet.write(row, column, value)
    hidden = workbook.add_sheet("Rejtett")
    hidden.write(0, 0, "rejtett adat")
    hidden.visibility = 1
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def main() -> None:
    """Write every fixture next to this script."""
    text_pages = [
        "A. szamu melleklet\nFizetesi utemezes es kesedelmi kotber\nA kotber napi 0,2 szazalek.",
        "Masodik oldal\nVegszamla: sikeres atadas-atvetel utan.",
    ]
    files = {
        "sample.pdf": _pdf(text_pages),
        "javascript.pdf": _pdf(
            ["Ez a kitalalt PDF JavaScriptet tartalmaz.\nAz eloneztet nem futtatja."],
            extra_catalog="/OpenAction 6 0 R /AcroForm << /Fields [] >> /Names << /EmbeddedFiles << /Names [] >> >>",
            extra_objects=["<< /S /JavaScript /JS (app.alert\\('kitalalt'\\);) >>",
                           "<< /Type /Annot /Subtype /Link /A << /S /URI /URI (https://pelda.hu) >> >>"],
        ),
        "image-only.pdf": _pdf([""]),
        "sample.docx": _docx(),
        "macro.docm": _docx(macro=True),
        "sample.xlsx": _xlsx(),
        "macro.xlsm": _xlsx(macro=True),
        "page.html": (
            "<!doctype html><html><head><title>Kitalált hírlevél</title>"
            "<style>h1{color:#2a7}</style></head><body>"
            "<h1>Kitalált hírlevél</h1><p onclick=\"steal()\">Kedves Teszt Anna!</p>"
            "<script>window.parent.mailScopeHacked = 1; document.title = 'HACKED';</script>"
            "<img src=\"https://pelda.hu/kovetes.png\" alt=\"követőpixel\">"
            "<a href=\"javascript:alert(1)\">Kattintson ide</a></body></html>"
        ).encode("utf-8"),
    }
    xls = _xls()
    if xls is not None:
        files["sample.xls"] = xls
    for name, data in files.items():
        (HERE / name).write_bytes(data)
        print(f"{name}: {len(data)} bájt")


if __name__ == "__main__":
    main()
