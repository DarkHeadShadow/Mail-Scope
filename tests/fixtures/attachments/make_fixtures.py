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


def _zipcrypto(entries: dict[str, bytes], password: bytes) -> bytes:
    """Build a ZIP with traditional PKWARE (ZipCrypto) encryption, stored entries."""
    import binascii
    import struct

    def crc_byte(crc: int, byte: int) -> int:
        return binascii.crc32(bytes([byte]), crc ^ 0xFFFFFFFF) ^ 0xFFFFFFFF

    def keys_for(secret: bytes) -> list[int]:
        keys = [0x12345678, 0x23456789, 0x34567890]
        for byte in secret:
            update(keys, byte)
        return keys

    def update(keys: list[int], byte: int) -> None:
        keys[0] = crc_byte(keys[0], byte)
        keys[1] = ((keys[1] + (keys[0] & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        keys[2] = crc_byte(keys[2], keys[1] >> 24)

    def encrypt(keys: list[int], data: bytes) -> bytes:
        output = bytearray()
        for byte in data:
            temp = (keys[2] | 2) & 0xFFFF
            output.append(byte ^ (((temp * (temp ^ 1)) >> 8) & 0xFF))
            update(keys, byte)
        return bytes(output)

    local = io.BytesIO()
    central = io.BytesIO()
    for index, (name, data) in enumerate(entries.items()):
        crc = binascii.crc32(data)
        keys = keys_for(password)
        header = bytes((17 + index * 7 + i) & 0xFF for i in range(11)) + bytes([crc >> 24])
        payload = encrypt(keys, header + data)
        encoded = name.encode("utf-8")
        offset = local.tell()
        fields = (20, 0x0801, 0, 0x6000, 0x5A21, crc, len(payload), len(data), len(encoded), 0)
        local.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, *fields) + encoded + payload)
        central.write(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, *fields, 0, 0, 0, 0, offset) + encoded)
    directory = central.getvalue()
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(directory), local.tell(), 0)
    return local.getvalue() + directory + end


def _cfb(tree: dict) -> bytes:
    """Write a minimal OLE compound file (version 3) from nested {name: bytes | dict}.

    Streams under 4096 bytes go into the mini stream, as the format requires.
    Only used to build the fictional MSG fixture.
    """
    import struct

    sector = 512
    free, end_chain, fat_mark, no_stream = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF
    nodes: list[dict] = [{"name": "Root Entry", "type": 5, "children": []}]

    def add(parent: int, children: dict) -> None:
        for name, value in children.items():
            node = {"name": name, "type": 1 if isinstance(value, dict) else 2, "children": [], "data": value}
            nodes.append(node)
            nodes[parent]["children"].append(len(nodes) - 1)
            if isinstance(value, dict):
                add(len(nodes) - 1, value)

    add(0, tree)
    mini = bytearray()
    minifat: list[int] = []
    big: list[tuple[int, bytes]] = []
    for index, node in enumerate(nodes):
        if node["type"] != 2:
            continue
        data = node["data"]
        node["size"] = len(data)
        if len(data) < 4096:
            count = max(1, -(-len(data) // 64)) if data else 0
            node["start"] = len(mini) // 64 if data else end_chain
            for i in range(count):
                minifat.append(len(minifat) + 1 if i < count - 1 else end_chain)
            mini += data + b"\x00" * (count * 64 - len(data))
        else:
            big.append((index, data))

    def sectors(length: int) -> int:
        return -(-length // sector)

    directory_sectors = sectors(len(nodes) * 128)
    minifat_sectors = sectors(len(minifat) * 4)
    mini_sectors = sectors(len(mini))
    big_sectors = sum(sectors(len(data)) for _, data in big)
    fat_sectors = 1
    while True:
        total = fat_sectors + minifat_sectors + directory_sectors + mini_sectors + big_sectors
        if sectors(total * 4) <= fat_sectors:
            break
        fat_sectors += 1
    fat = [free] * (fat_sectors * sector // 4)
    position = 0

    def chain(count: int) -> int:
        nonlocal position
        if count == 0:
            return end_chain
        start = position
        for i in range(count):
            fat[position] = position + 1 if i < count - 1 else end_chain
            position += 1
        return start

    for i in range(fat_sectors):
        fat[position] = fat_mark
        position += 1
    minifat_start = chain(minifat_sectors)
    directory_start = chain(directory_sectors)
    mini_start = chain(mini_sectors)
    for index, data in big:
        nodes[index]["start"] = chain(sectors(len(data)))
    nodes[0]["start"], nodes[0]["size"] = mini_start, len(mini)

    for node in nodes:
        # Siblings as a simple right-leaning chain; all nodes black.
        kids = node["children"]
        node["child"] = kids[0] if kids else no_stream
        for left, right in zip(kids, kids[1:] + [None]):
            nodes[left]["right"] = right if right is not None else no_stream
    entries = bytearray()
    for node in nodes:
        name = node["name"].encode("utf-16-le") + b"\x00\x00"
        entries += struct.pack(
            "<64sHBBIII16sIQQIII", name, len(name), node["type"], 1, no_stream,
            node.get("right", no_stream), node.get("child", no_stream) if node["type"] != 2 else no_stream,
            b"\x00" * 16, 0, 0, 0, node.get("start", end_chain) if node["type"] != 1 else 0,
            node.get("size", 0), 0,
        )
    entries += b"\x00" * (directory_sectors * sector - len(entries))
    difat = list(range(fat_sectors)) + [free] * (109 - fat_sectors)
    header = struct.pack(
        "<8s16sHHHHH6sIIIIIIIII", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"\x00" * 16, 0x3E, 3, 0xFFFE, 9, 6,
        b"\x00" * 6, 0, fat_sectors, directory_start, 0, 4096, minifat_start if minifat_sectors else end_chain,
        minifat_sectors, end_chain, 0,
    ) + struct.pack("<109I", *difat)
    body = bytearray()
    body += struct.pack(f"<{len(fat)}I", *fat)
    body += struct.pack(f"<{len(minifat)}I", *minifat) + b"\xff" * (minifat_sectors * sector - len(minifat) * 4)
    body += entries
    body += mini + b"\x00" * (mini_sectors * sector - len(mini))
    for _, data in big:
        body += data + b"\x00" * (sectors(len(data)) * sector - len(data))
    return header + bytes(body)


def _msg(attachment_pdf: bytes) -> bytes:
    """Build a fictional Outlook MSG with an attached PDF and an attached Outlook item."""
    import struct

    def text(prop: int, value: str) -> tuple[str, bytes]:
        return f"__substg1.0_{prop:04X}001F", value.encode("utf-16-le")

    def properties(header: int, values: list[tuple[int, int, bytes]]) -> bytes:
        body = b"".join(struct.pack("<HHI", kind, prop, 6) + value.ljust(8, b"\x00") for prop, kind, value in values)
        return b"\x00" * header + body

    filetime = struct.pack("<Q", (1683727080 + 11644473600) * 10_000_000)  # 2023-05-10 13:58 UTC
    inner = dict([
        text(0x0037, "Egyeztetés a helyszíni bejárásról"),
        text(0x0C1A, "Teszt Béla"),
        text(0x5D01, "teszt.bela@pelda.hu"),
        text(0x0E04, "Teszt Anna"),
        text(0x1000, "Kedves Anna!\r\nA bejárás jövő kedden 10 órakor lesz.\r\nÜdvözlettel: Béla"),
        ("__properties_version1.0", properties(24, [(0x0E06, 0x0040, filetime)])),
    ])
    tree = dict([
        text(0x0037, "Szerződéstervezet – Outlook-levélként"),
        text(0x0C1A, "Teszt Anna"),
        text(0x5D01, "teszt.anna@pelda.hu"),
        text(0x1000, "Kedves Béla!\r\nCsatolom a szerződéstervezetet és az előző egyeztetést.\r\nÜdv: Anna"),
        text(0x1035, "<kitalalt-msg-1@pelda.hu>"),
        ("__properties_version1.0", properties(32, [(0x0E06, 0x0040, filetime), (0x0039, 0x0040, filetime)])),
        ("__recip_version1.0_#00000000", dict([
            text(0x3001, "Teszt Béla"),
            text(0x39FE, "teszt.bela@pelda.hu"),
            ("__properties_version1.0", properties(8, [(0x0C15, 0x0003, struct.pack("<i", 1))])),
        ])),
        ("__attach_version1.0_#00000000", dict([
            text(0x3707, "szerzodes-tervezet.pdf"),
            text(0x370E, "application/pdf"),
            ("__substg1.0_37010102", attachment_pdf),
            ("__properties_version1.0", properties(8, [(0x3705, 0x0003, struct.pack("<i", 1))])),
        ])),
        ("__attach_version1.0_#00000001", dict([
            text(0x3001, "Egyeztetés a helyszíni bejárásról"),
            ("__substg1.0_3701000D", inner),
            ("__properties_version1.0", properties(8, [(0x3705, 0x0003, struct.pack("<i", 5))])),
        ])),
    ])
    return _cfb(tree)


def _eml(attachments: list[tuple[str, str, bytes]]) -> bytes:
    """Build a fictional EML with plain and HTML bodies and the given attachments."""
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = "Teszt Anna <teszt.anna@pelda.hu>"
    message["To"] = "Teszt Béla <teszt.bela@pelda.hu>"
    message["Date"] = "Wed, 10 May 2023 16:58:00 +0200"
    message["Subject"] = "Továbbított: számlák és jegyzőkönyv"
    message["Message-ID"] = "<kitalalt-eml-1@pelda.hu>"
    message.set_content("Kedves Béla!\nMellékelem a számlákat tartalmazó archívumot.\nÜdv: Anna\n")
    message.add_alternative(
        "<p>Kedves Béla!</p><p>Mellékelem a <b>számlákat</b> tartalmazó archívumot.</p>"
        "<script>document.title='HACKED'</script><p>Üdv: Anna</p>",
        subtype="html",
    )
    for name, mime, data in attachments:
        maintype, subtype = mime.split("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    for index, part in enumerate(part for part in message.walk() if part.is_multipart()):
        part.set_boundary(f"===kitalalt-hatar-{index}===")
    return message.as_bytes()


def _archives(pdf: bytes, xlsx: bytes) -> dict[str, bytes]:
    """ZIP with a nested ZIP and a disguised program, a password ZIP, a bomb-like ZIP, 7z files."""
    def member(name: str) -> zipfile.ZipInfo:
        # A fixed, fictional date keeps the fixtures identical between runs.
        info = zipfile.ZipInfo(name, date_time=(2023, 5, 10, 16, 58, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        return info

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr(member("jegyzokonyv.txt"), "Átadás-átvételi jegyzőkönyv (kitalált)\nÁtvette: Teszt Béla\n")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr(member("szamlak/szamla-2023-05.pdf"), pdf)
        archive.writestr(member("szamlak/Kotberszamitas.xlsx"), xlsx)
        archive.writestr(member("belso/jegyzokonyv.zip"), inner.getvalue())
        # Not a real program: only the "MZ" signature, to show the disguise warning.
        archive.writestr(member("fizetes_reszletek.pdf.exe"), b"MZ" + b"\x00" * 62 + b"Kitalalt, nem futtathato minta.")
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w") as archive:
        archive.writestr(member("nullak.bin"), b"\x00" * (12 * 1024 * 1024), compresslevel=9)
    files = {
        "archive.zip": outer.getvalue(),
        "password.zip": _zipcrypto({"titkos-szamla.txt": "Kitalált titkos számla: 439 750 Ft\n".encode("utf-8")}, b"pelda123"),
        "bomb.zip": bomb.getvalue(),
    }
    try:
        import py7zr
    except ImportError:
        return files
    for name, password in (("archive.7z", None), ("password.7z", "pelda123")):
        output = io.BytesIO()
        with py7zr.SevenZipFile(output, "w", password=password, header_encryption=bool(password)) as archive:
            archive.writestr(pdf, "szamla-2023-05.pdf")
            archive.writestr("Kitalált feljegyzés a 7z-archívumban.\n".encode("utf-8"), "feljegyzes.txt")
        files[name] = output.getvalue()
    return files


def _odt_with_macro() -> bytes:
    """Build an ODT whose Basic macro runs on open and writes $MAILSCOPE_MACRO_MARKER.

    The exact-view test converts it with LibreOffice and checks that the
    marker file never appears, i.e. the macro did not run.
    """
    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="Basic/script-lc.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="Basic/Standard/script-lb.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="Basic/Standard/Module1.xml" manifest:media-type="text/xml"/>
</manifest:manifest>"""
    content = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:script="urn:oasis:names:tc:opendocument:xmlns:script:1.0" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:dom="http://www.w3.org/2001/xml-events" office:version="1.2">
<office:scripts><office:event-listeners><script:event-listener script:language="ooo:script" script:event-name="dom:load" xlink:href="vnd.sun.star.script:Standard.Module1.Marker?language=Basic&amp;location=document" xlink:type="simple"/></office:event-listeners></office:scripts>
<office:body><office:text><text:p>Kitalalt dokumentum makroval. A Pontos nezet nem futtatja.</text:p></office:text></office:body>
</office:document-content>"""
    libraries = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE library:libraries PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "libraries.dtd">
<library:libraries xmlns:library="http://openoffice.org/2000/library" xmlns:xlink="http://www.w3.org/1999/xlink">
 <library:library library:name="Standard" library:link="false"/>
</library:libraries>"""
    library = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE library:library PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "library.dtd">
<library:library xmlns:library="http://openoffice.org/2000/library" library:name="Standard" library:readonly="false" library:passwordprotected="false">
 <library:element library:name="Module1"/>
</library:library>"""
    module = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">
<script:module xmlns:script="http://openoffice.org/2000/script" script:name="Module1" script:language="StarBasic" script:moduleType="normal">Sub Marker
  Dim target As String
  target = Environ(&quot;MAILSCOPE_MACRO_MARKER&quot;)
  If target &lt;&gt; &quot;&quot; Then
    Open target For Output As #1
    Print #1, &quot;ran&quot;
    Close #1
  End If
End Sub
</script:module>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype", (2023, 5, 10, 12, 0, 0)), "application/vnd.oasis.opendocument.text")
        for name, text in (
            ("META-INF/manifest.xml", manifest), ("content.xml", content),
            ("Basic/script-lc.xml", libraries), ("Basic/Standard/script-lb.xml", library),
            ("Basic/Standard/Module1.xml", module),
        ):
            archive.writestr(zipfile.ZipInfo(name, (2023, 5, 10, 12, 0, 0)), text, compress_type=zipfile.ZIP_DEFLATED)
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
        "macro-onload.odt": _odt_with_macro(),
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
    files.update(_archives(files["sample.pdf"], files["sample.xlsx"]))
    files["message.msg"] = _msg(files["sample.pdf"])
    files["message.eml"] = _eml([
        ("szamlak.zip", "application/zip", files["archive.zip"]),
        ("szerzodes.msg", "application/vnd.ms-outlook", files["message.msg"]),
    ])
    for name, data in files.items():
        (HERE / name).write_bytes(data)
        print(f"{name}: {len(data)} bájt")


if __name__ == "__main__":
    main()
