# Third-party notices

Project creator: Bálint Szabolcs

Mail-Scope itself is licensed under the MIT License; see [LICENSE](./LICENSE).
Third-party components remain under their respective licenses.

## libpff / pypff

PST/OST support uses the unmodified `pypff` bindings from
`libpff-python-windows` (version `20231205`). The library is licensed under
LGPL-3.0-or-later. See [the libpff notice](./LICENSES/NOTICE_libpff.txt) and
the [upstream project](https://github.com/libyal/libpff) for license details.

## Pillow

The attachment viewer uses `Pillow` (version `12.3.0`) to re-encode image
attachments to PNG and to read EXIF metadata, inside an isolated worker
process. Pillow is licensed under the MIT-CMU license (HPND); see the
[upstream project](https://github.com/python-pillow/Pillow) for license details.

## Attachment viewer document libraries

These run only inside the isolated attachment worker process:

| Package | Version | License | Use |
| --- | --- | --- | --- |
| `pypdfium2` (with PDFium) | 5.13.0 | Apache-2.0 or BSD-3-Clause; PDFium BSD-3-Clause | PDF page rendering, text layer, metadata |
| `mammoth` | 1.12.2 | BSD-2-Clause | DOCX to HTML and text |
| `cobble` | 0.1.4 | BSD-2-Clause | dependency of mammoth |
| `openpyxl` | 3.1.5 | MIT | XLSX/XLSM sheets, formulas, comments |
| `et-xmlfile` | 2.0.0 | MIT | dependency of openpyxl |
| `xlrd` | 2.0.2 | BSD | legacy XLS sheets |
| `nh3` (ammonia) | 0.3.6 | MIT | HTML sanitizing |

The `xlwt` package (BSD) is used only by the development script that generates
the fictional `.xls` test fixture; it is not shipped or required at run time.

## Archive and Outlook MSG attachments

These also run only inside the isolated attachment worker process:

| Package | Version | License | Use |
| --- | --- | --- | --- |
| `py7zr` | 1.1.4 | LGPL-2.1-or-later | 7z archive listing and unpacking in memory |
| `pyppmd` | 1.3.1 | LGPL-2.1-or-later | dependency of py7zr (PPMd) |
| `pybcj` | 1.0.8 | LGPL-2.1-or-later | dependency of py7zr (BCJ filters) |
| `inflate64` | 1.0.4 | LGPL-2.1-or-later | dependency of py7zr (Deflate64) |
| `multivolumefile` | 0.2.3 | LGPL-2.1-or-later | dependency of py7zr |
| `pycryptodomex` | 3.24.1 | BSD-2-Clause / Public Domain | dependency of py7zr (AES) |
| `backports.zstd` | 1.8.0 | PSF-2.0 | dependency of py7zr (Zstandard) |
| `brotli` | 1.2.1 | MIT | dependency of py7zr |
| `texttable` | 1.7.2 | MIT | dependency of py7zr |
| `psutil` | 7.2.2 | BSD-3-Clause | dependency of py7zr |
| `olefile` | 0.47 | BSD-2-Clause | reading Outlook MSG (OLE) files; the MAPI reader is Mail-Scope's own |

The LGPL packages are used unmodified, as separate packages, like libpff; see
[the py7zr notice](./LICENSES/NOTICE_py7zr.txt). `extract-msg` is not used
because its GPL-3.0 license is not compatible with Mail-Scope's MIT License.

RAR archives (and ZIP files with AES encryption) are unpacked only when the
user has installed 7-Zip; Mail-Scope calls the installed `7z.exe` without a
window and does not ship it.

The optional “Pontos nézet” (exact view) of Office attachments uses LibreOffice
(MPL-2.0) only when the user has installed it; Mail-Scope runs the installed
`soffice.exe` as a separate program to convert a copy to PDF and does not ship,
link or modify LibreOffice.

The Mail-Scope installer does not contain 7-Zip or LibreOffice. When the user
selects them during setup, it downloads the official, unmodified installers
from their publishers (7-Zip from github.com/ip7z/7zip, LibreOffice from
download.documentfoundation.org) and runs them; they are installed as separate
programs under their own licenses and are not removed with Mail-Scope.
