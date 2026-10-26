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
- Selected the MIT License for Mail-Scope; third-party components retain their own licenses.

This is a development snapshot, not a public release.
