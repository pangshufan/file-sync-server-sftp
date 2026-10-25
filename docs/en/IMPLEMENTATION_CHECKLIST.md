# Implementation checklist

[简体中文](../IMPLEMENTATION_CHECKLIST.md) | [English home](../../README.en.md)

## 0.5.1 Windows compatibility fix (2026-10-25)

- [x] Normalize path/descriptor signatures with Windows birthtime rather than inconsistent ctime semantics.
- [x] Keep same-handle raw ctime checks for hashing and automatic confirmation to reject actual changes.
- [x] Do not assert POSIX 0600 on Windows; retain strict POSIX permission checks elsewhere.
- [x] Add simulated-Windows, native-file and automatic-confirmation regression cases.
- [x] Report native metadata in CI, retain failure JUnit artifacts and document diagnostics in both languages.
- [ ] Native Windows Actions rerun after the fix remains pending.

## 0.5.0 localization (2026-10-24)

- [x] Match system UI language preferences; Simplified Chinese/English with English fallback.
- [x] Shared Qt-free application catalog for desktop, workers, validation and command-line messages.
- [x] Translate permissions/status for display without changing protocol identifiers or persisted progress.
- [x] Include Qt stock Chinese dialog translations and application catalogs in packaging.
- [x] English README plus English counterparts for every document, including historical protocol v1.
- [x] Add catalog/placeholder/document checks and bilingual GUI/CLI tests.
- [ ] Native Windows/Linux language acceptance: requires target-system validation.

## 0.4.0 operational improvements (2026-10-24)

- [x] Batch 1: reusable writer/read-only pool; status reads independent WAL snapshots.
- [x] Batch 1: bounded scan/reconciliation/reset/cleanup commits with consistent recovery counters.
- [x] Batch 1: actual-file loopback SFTP concurrency benchmark with latency percentiles and transaction metrics.
- [x] Batch 1: bounded asynchronous JSONL logging, private files, rotation and error/drop metrics.
- [x] Batch 1: cross-connection account/IP budgets, total connection/hash limits, cancellation-safe computation budgets.
- [x] Batch 2: multiple/public-key-only accounts, desktop settings, CLI identity/agent/batch.
- [x] Batch 2: Qt-free headless entry, config validation, SIGINT/SIGTERM and process lock.
- [x] Batch 2: systemd/launchd/WinSW templates and documentation; no automatic installation.
- [x] Batch 2: classified reload, changed-account disconnect only, desktop file reload and Unix SIGHUP.
- [x] Batch 3: verified local content reuse, staging before deletion and network fallback.
- [x] Batch 3: atomic large-file publishing/rotation strategy; functional tests added to three-platform CI.
- [x] Maintenance: database v3 legacy-table cleanup, config v4 backup/rollback, portable startup script and documentation.
- [ ] Native Windows/Linux runtime and service-install/boot acceptance remain pending.
- [ ] Formal protocol namespace awaits a project-owned domain; existing names remain for compatibility.
- [ ] Block-level deltas and database replacement remain measurement-driven options, not implemented features.

See [Validation](VALIDATION.md) and [Operations](OPERATIONS.md).

## Historical 0.3.0 checklist

This records the userid incremental design agreed on 2026-10-23. See [Protocol v2](sync-protocol-v2.md) and [Migration](MIGRATION.md).

- [x] Permanent userid, preserved through username edits, one progress record per user.
- [x] Optional single-connection restriction enabled by default; IP/CIDR allowlist; administrator disconnect.
- [x] Server-managed sync switch, multiple directories, patterns, start time and confirmation mode.
- [x] Remove full/device/task incremental interfaces; use standard SFTP for full downloads.
- [x] Shared index/latest catalog, SHA-256, server update time and internal increasing versions.
- [x] Persistent pending/failed/completed versions; collapse repeated changes without losing unfinished entries.
- [x] Preserve unfinished entries when changing time; explicit confirmed restart persists and executes once.
- [x] Compact completion details, retain necessary versions, maintain transactional counters and indexed pagination.
- [x] userid JSON listing, batch confirmation, ownership checks, idempotency and older-version protection.
- [x] Automatic confirmation tracks actual ranges, signature and explicit CLOSE; no false success for partial/disconnected reads.
- [x] Desktop settings, counts, details/errors; edit settings while stopped.
- [x] Pre-migration config/SQLite backups; preserve index/keys; never merge old device completion state.
- [x] SDK, CLI and standalone Paramiko example use v2; v1 documentation is explicitly historical.
- [x] Update README, protocol, migration/rollback, validation and checklist.
- [x] Complete 39 tests, dependency checks, UI rendering and 100,000/million-row synthetic SQLite validation.

Directory entries and deletions cannot be inferred from file transfers and still need explicit confirmation. Ordinary SFTP automatic mode confirms only eligible actual files. This boundary is documented in the settings and guides.
