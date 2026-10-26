# Changelog

**Project creator: Bálint Szabolcs**

Project changes are recorded here. This project has not made a public release yet.

## [0.1.0] - Development snapshot

- Added direct MBOX, EML, and optional PST/OST source readers with content-based discovery.
- Added a case-local SQLite/FTS5 index with resumable background indexing and source SHA-256 verification.
- Added people, aliases, duplicate merging, and message threading.
- Added a loopback-only Hungarian archive and conversation UI.
- Added detailed AND/OR search groups, case-local labels, and saved searches.
- Added conversation EML ZIP export with SHA-256 manifest and browser-print PDF workflows.
- Added detailed-search CSV and EML ZIP exports.
- Added Windows PyInstaller build and English/Hungarian usage documentation.
- Added Windows/Python 3.13 GitHub Actions CI and tagged release packaging.
- Added browser auto-start, hidden-console Windows packaging, and graceful indexed-state shutdown.
- Fixed PST multipart body recovery and MAPI metadata fallback for Outlook-native sent messages.
- Preserved MBOX/EML source bytes in ZIP exports, reconstructed PST/OST EML with attachments, and guarded MBOX parsing against ordinary `From ` body lines.
- Fixed archive ordering to use parsed timestamps and restored HTML text following hidden elements.
- Batched indexing into SQLite transactions of 500 messages and enabled WAL for case databases.
- Added an index for normalized-subject thread fallback lookups.
- Fixed PST/OST items being marked as failed when libpff reports no attachment table, decoded HTML bodies by their declared charset or code page, and used the HTML body when the plain-text part is empty.
- Kept Exchange-only senders and recipients as Exchange addresses and resolved them to SMTP when the mailbox reveals the pair.
- Continued indexing the remaining sources after one source fails, and treated unexpected parser errors as per-message failures.
- Restricted subject-based thread fallback to nearby replies with a shared participant, and split threads that only share an absent notification parent.
- Tightened EML detection and grouped the EML files of a directory into a single source.
- Added prefix full-text search with Hungarian error messages, stored message bodies only once (in FTS), excluded notes to self from the two-person conversation mode, and paged archive and search results without window functions.
- Moved source hashing out of the application lock, reused recorded hashes while a source's file signature is unchanged, and kept PST/OST stores open between message reads.
- Added a rotating application log, a per-case log, and a provenance `MANIFEST.txt` in EML ZIP exports.
- Split the workspace into five pages with hash routes (`#/ugy`, `#/archivum`, `#/beszelgetes`, `#/kereses`, `#/szemelyek`), Back/Forward and reload support, `Ctrl+1`…`Ctrl+5`, preserved page state, disabled pages without a case, a sidebar indexing indicator, and a horizontal tab bar on narrow screens; jumps between views now switch pages, and the reading pane gained a “Teljes beszélgetés” button.
- Added a welcome screen with a CSS-animated 3D circuit-board background (fixed seed, paused when hidden, removed after a case opens, static with reduced motion) and a recent-case list stored in `%APPDATA%\Mail-Scope\recent.json` (`GET /api/recent-cases`).
- Served UI files with explicit MIME types and refreshed the README screenshots with fictional data.
- Fixed `database is locked` failures during indexing: messages are parsed outside the write lock and stored in `BEGIN IMMEDIATE` batches of at most 500 messages or about one second, retried with growing waits on lock contention; user writes (people, labels, saved searches) also take the lock up front and report a Hungarian “database busy” message after 5 seconds.
- Added background **Teljes újraellenőrzés** per source with progress, recorded result and time (case database and case log), `source_changed` on mismatch, “Utolsó teljes ellenőrzés” in the source list, and the check mode and last full check time in `MANIFEST.txt`; documented when full hashing and when signature-only checks happen.
- Added a per-case decision between fast (signature) and strict (full SHA-256 before every open, once per source and export) source verification, with the consequences and a duration estimate shown before choosing (`POST /api/settings/verification`).
- Showed message dates as local `YYYY-MM-DD HH:MM` (the original `Date` header stays in the forensic block), kept the page-heading focus ring for keyboard switches only, rejected searches without a searchable word, described the MBOX envelope line in `MANIFEST.txt`, darkened the welcome-screen footer, and made a Windows-only test path portable.
- Created people automatically after indexing and on case open (addresses sharing a normalized display name form one “automatic” person) and added a person editor: rename, merge, add or move an address, split an address into a new person, remove an address, and delete a person. Hand-edited people are never changed automatically again, and removed addresses are not re-grouped. Added filters for the people list and conversation participants, and Hungarian user guide `docs/HASZNALATI_UTMUTATO.md`.
- Made the folder picker open in front of the browser, refused to share the server port with another running instance on Windows, and fixed oversized checkboxes in the observed-address list.
- Selected the MIT License for Mail-Scope; third-party components retain their own licenses.

This is a development snapshot, not a public release.
