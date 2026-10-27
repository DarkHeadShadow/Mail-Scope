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

