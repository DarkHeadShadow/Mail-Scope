# Mail-Scope

**Creator: Bálint Szabolcs**

Mail-Scope is an offline, local-first mail archive browser and analysis tool. It reads mail archives directly, indexes message metadata and searchable text in a case-local SQLite database, and opens the original message from its source when requested.

> **Development status:** version `0.1.0`. This project is under active development and is not yet a signed or released Windows application. The project license has not been selected.

## Features

- Discover and index MBOX, MBX, MBS, EML, PST, and OST sources, including supported archives inside folders.
- Read PST/OST files using the optional `libpff-python-windows` binding.
- Search message text, subject, participants, dates, attachments, folders, sources, attachment types, and case-local labels.
- Combine detailed-search conditions in AND/OR groups; save and reload searches.
- Merge duplicate messages in search results while retaining source occurrences and forensic locators.
- Assign labels to all indexed source copies of a selected message.
- Browse conversations by people and export original EML messages with SHA-256 manifests.
- Export detailed-search results to CSV or EML ZIP; print a bounded search report to PDF using the browser's print dialog.
- Keep indexes, labels, saved searches, and logs inside the case's `.mailscope` directory.

## Privacy and source integrity

- Mail-Scope does not upload data or use external services, CDNs, or network resources.
- The local server binds to `127.0.0.1` only.
- Source archives are opened read-only. Their SHA-256 is checked before a message is read or exported; changed sources are refused.
- Indexing stores locators and searchable metadata rather than extracting complete mailboxes to disk. Original EML is written only when explicitly exported.
- Use only fictional data in tests and screenshots. Never add real mail, mailbox files, credentials, or personal data to this repository.

## Requirements

- Windows 10/11 x64
- Python 3.13 x64
- SQLite with FTS5 (included with supported Python distributions)
- `libpff-python-windows` for PST/OST support

## Run from source

From PowerShell in the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\run_dev.bat
```

The UI opens automatically in the default browser (by default `http://127.0.0.1:8765`). When running from source, stop the server with `Ctrl+C` in the terminal.

To run the tests:

```powershell
py -3.13 -m pip install -r requirements-dev.txt
py -3.13 -m unittest discover -v
```

## Build a Windows application

`build_windows.bat` creates or reuses `.venv`, installs the pinned build dependencies, and creates an onedir PyInstaller package:

```bat
build_windows.bat
```

The executable is `dist\Mail-Scope\Mail-Scope.exe`. Keep the complete `Mail-Scope` folder when running it; it contains the local UI and third-party notices. The build requires the pinned PST binding to be installed and collected by PyInstaller.

The executable opens the local UI in the default browser automatically and runs without a terminal window. Use the **Leállítás** button in the upper-right corner to stop Mail-Scope. If indexing is active, the app asks for confirmation, cancels at a safe checkpoint, and keeps the SQLite index so indexing can resume on the next launch.

## Use

1. Start Mail-Scope and open or create a case directory.
2. Enter a source file or folder and review the discovered sources.
3. Start indexing and wait for completion.
4. Browse the archive, create people and aliases for conversation analysis, or use detailed search.
5. Export only the results you need. CSV exports are limited to 10,000 results, EML ZIP exports to 500 messages and 256 MiB of original message data, and printable PDF reports to 2,000 results.

Detailed search supports subject, full-text, sender, recipient, inclusive date boundaries, attachment presence, source path, folder, attachment media type, and label conditions. Conditions may be grouped with AND/OR; groups can also be combined with AND/OR.

## Known limitations

- PST/OST parsing depends on the optional native `pypff` binding and its libpff capabilities. Damaged or unsupported Outlook items may not be readable.
- Search indexes do not store complete message bodies or attachment contents for export; originals are reopened from their unchanged source.
- A PDF is produced through the browser's print dialog, not by a bundled PDF library.
- Search and export limits are enforced to keep local responses bounded.
- Project-level license selection and public-release preparation remain pending.

## Third-party notices

PST/OST support uses LGPL-3.0-or-later `libpff`/`pypff`. See [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) and [LICENSES/NOTICE_libpff.txt](./LICENSES/NOTICE_libpff.txt). Mail-Scope itself does not yet declare a project license.

## Screenshots

The detailed-search screen below uses only fictional mailbox data:

![Mail-Scope detailed search with synthetic results](./docs/screenshots/detailed-search.png)
