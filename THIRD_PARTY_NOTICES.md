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

