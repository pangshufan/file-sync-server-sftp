# Validation records

[简体中文](../VALIDATION.md) | [English home](../../README.en.md)

## 0.5.1 Windows build compatibility fix (2026-10-25)

The supplied Windows Actions log reported **17 failed, 51 passed, 1 skipped** during pytest, before PyInstaller ran. Main failures were false pre-hash signature changes/unready initial indexes and comparing Windows st_mode with POSIX 0600.

After the fix, local macOS arm64 regression reported **74 passed in 61.52 seconds**, producing build/test-results/pytest.xml. The five added cases separately reported **5 passed in 4.34 seconds**. They cover rewritten native files, simulated Windows ctime disagreement and index readiness, rejected hashing after handle metadata changes, and both valid automatic confirmation and rejection of changes with restored size/mtime. Translation, document links and compileall checks passed.

Cross-API comparisons use explicit Windows birthtime while POSIX retains ctime. Same-handle raw ctime and device IDs still guard hashing/confirmation. Windows permission tests verify actual config usability, without claiming chmod implements ACL isolation; POSIX 0600 assertions remain. Database/config/protocol schemas are unchanged.

CI now reports native metadata, attempts to retain JUnit diagnostics even on failure, and includes scan errors in assertions. Changes have not been pushed to GitHub, and no post-fix native Windows runner result is available. Local simulation is not a substitute for that rerun.

0.5.1 macOS arm64 packaging and bilingual GUI/client/headless startup checks passed, confirming the new shared signature module loads in bundled entry points. This is not Windows packaging acceptance.

References: [CPython timestamp discrepancy report](https://github.com/python/cpython/issues/157671), [Python 3.12 path stat implementation](https://github.com/python/cpython/blob/3.12/Modules/posixmodule.c), [Windows chmod semantics](https://docs.python.org/3.12/library/os.html#os.chmod).

## 0.5.0 localization (2026-10-24)

- Full regression: **69 passed in 56.88 seconds**. After display-name/resource/checker adjustments, localization tests: **12 passed in 0.69 seconds**.
- All 277 English catalog entries passed checks for empty/Chinese leftovers, format placeholders, unwrapped Chinese messages, document counterparts and local links. Coverage includes windows/settings/details, permissions/status, validation/runtime text and CLI help.
- Cases include Chinese/English preferences, regional variants, ordered matching, unsupported/C locale fallback, overrides, Qt Chinese buttons and display translation without changing manual/pending protocol values.
- English CLI help/errors and Qt-free headless imports passed. English main/account/sync/rule/login-protection views were rendered and inspected; both appearances remained readable without observed clipping. Chinese UI remains available.
- README.en.md and every docs document have English versions, including historical protocol v1 and the new localization guide. Config schema 4, database schema 3 and protocol v2 are unchanged.
- Tests used macOS offscreen rendering and supplied language preferences, not native setting changes or Windows/Linux acceptance. Language is chosen at startup and requires restart after an OS language change.
- macOS arm64 0.5.0 packaging passed both GUI language smoke checks and both console help checks in each language. Chinese smoke additionally requires the Qt translator. Native en/zh-Hans display-name/network descriptions, Qt qm, JSON catalog and every English document were verified in release outputs. Console bundles still exclude Qt. Wheel build and dependency checks passed.

## 0.4.0 operations and concurrency (2026-10-24)

Environment: macOS arm64, Python 3.12.15, AsyncSSH 2.24.1, project Conda environment. Final regression: **57 passed in 56.06 seconds**. pip check found no dependency conflicts.

New coverage included committed-snapshot reads while a writer holds a transaction, read-only rejection, connection reuse/rollback, paged policy recovery, v2→v3 backup/legacy-table removal with completion preservation, login windows/IPv4-mapped normalization, cancellation-safe native hash budgets, cross-connection rate limits, private log rotation/field filtering, public-key/IP/single-connection auth, Qt-free imports, process locks, headless SIGTERM, selective reload disconnects, desktop key settings/closing during reload, and local-content reuse with invalid-candidate fallback.

Loopback workload: 2,000 actual 1 KiB files, four users, ten seconds:

| Operation | Samples | P50 | P95 | P99 | Maximum |
| --- | --- | --- | --- | --- | --- |
| list | 596 | 10.153 ms | 81.592 ms | 143.799 ms | 407.748 ms |
| ack | 596 | 11.499 ms | 67.622 ms | 96.933 ms | 147.572 ms |
| status | 475 | 0.289 ms | 1.874 ms | 2.749 ms | 5.345 ms |

Maximum write transaction was 25.51 ms; maximum write-lock wait 88.68 ms. This measured the first improvement batch, before all later features, not a before/after comparison. Queues were prepared before measurement, so this is not cold backlog performance. ack measured metadata without downloading files; results cannot predict million-file or large-file throughput.

The updated 100,000-row synthetic test took 3.107 seconds for indexing, 4.442 seconds for initial queue generation and 1.3 ms for the next 200-item page; database 194.10 MiB, process peak 24.64 MiB. Million real files were not retested; the million-row values below are historical 0.3 synthetic measurements.

The new main window/key tab were rendered and inspected in light/dark offscreen mode. macOS 0.4 packages contain GUI, client and headless server. Other-platform CI adds functional tests, but remote CI was not triggered and services were not installed locally. Native service installation, OS theme switching, Windows/Linux operation and formal signing remain deployment acceptance tasks.

Packaged checks also passed: GUI offscreen launch; both CLI help commands; public-key authentication and an actual verified download between packaged headless/client executables using temporary loopback state. Client exit code was 0; server SIGTERM exit code was 0. Persistent logs included login/shutdown. The headless bundle excluded PySide6. A Python wheel built successfully.

The following records are historical 0.3.0 validation.

## System light/dark appearance (2026-10-24)

- Startup reads Qt color scheme and listens for colorSchemeChanged. Applying after native palette updates refreshes open windows/dialogs.
- Themes cover body/headings/help/placeholders, active/inactive selection, disabled text, dates, tables, dropdowns and tooltips. Unchecked dark checkboxes receive visible borders.
- Main window, permissions and sync dialogs were rendered offscreen and inspected. Scheme-notification tests passed. Designed body/help/placeholder/selection/primary/disabled color pairs passed contrast checks of at least 4.5:1.
- Full regression: **42 passed in 44.78 seconds**. Final placeholder/checkbox adjustments: **3 theme tests passed in 0.52 seconds**.
- These were macOS offscreen renders and simulated Qt notifications, not real OS setting-panel transitions on all platforms.

## Icon and packaging (2026-10-24)

- macOS arm64, Python 3.12.15, PyInstaller 6.22.3 built the .app, standalone client and approximately 49 MiB simple-sync-0.3.0-darwin-arm64.tar.gz.
- Packaged GUI loaded its icon and created/closed the window offscreen using temporary state; CLI --help succeeded.
- The ICNS referenced by Info.plist existed. The wheel contained SVG/PNG, and PNG artwork was inspected.
- Existing regression: **39 passed in 44.08 seconds**. The first sandbox run could not bind loopback ports; the authorized unrestricted rerun passed.
- Windows/Linux spec/CI configurations were supplied but not run. Checks did not include notarization, manual Finder/Dock acceptance or packaged network end-to-end transfer at that stage.

See [Packaging](PACKAGING.md).

Historical baseline date: 2026-10-23, macOS arm64, Python 3.12.15, Conda environment /Volumes/T9/sync/env/sync-env.

## Historical automated tests

`python -m pytest -q --tb=short`: **39 passed in 44.22 seconds**, no skips.

| Area | Coverage |
| --- | --- |
| Standard SFTP | File operations, read-only/subdirectory permissions, path/link restrictions, disabled shell/forwarding, restart and port conflicts. |
| Login | Per-userid single connection, authentication races, independent users, disabling the restriction, IPv4/CIDR/IPv6 allowlists. |
| userid listings | Shared progress, server scope/patterns, ownership rejection, disabling sync without disabling SFTP. |
| Time/version | Preserve/restart behavior, server time separate from mtime, pagination during updates, old ACK preserving newer failures. |
| Confirmation | Manual download alone does not confirm, checksum rejection, idempotent duplicate acknowledgement. |
| Automatic mode | Complete/empty success; no success for partial/gapped/EOF/multi-handle/disconnect/changed/copy-data cases. |
| Persistence/cleanup | Restart, completion compaction, log expiry and transactional counters. |
| Filesystem reliability | Native watching, directory/file type replacement, resume, local symlinks, no mass deletion on incomplete scans/missing roots. |
| Interoperability | Invalid/oversized JSON, obsolete extensions rejected, Paramiko extension plus standard get. |
| Migration | Consistent backups, permanent userid, repeated startup, no merging of device completion. |
| Desktop | User settings, reset confirmation, status/detail views, disconnection and close-to-stop behavior. |

pip check found no conflicts. compileall checked source/tests/examples/tools. CLI help had no obsolete name/scope/reset switches.

User and sync settings pages were inspected through Qt offscreen rendering without observed clipping. Tests, previews and benchmarks used temporary directories rather than production state.

## Historical synthetic SQLite scale tests

```bash
python tools/benchmark_index.py --files 100000
python tools/benchmark_index.py --files 1000000
```

| Synthetic entries | Index write | Initial pending queue | Next 200-item page | Database | Process peak |
| --- | --- | --- | --- | --- | --- |
| 100,000 | 3.105 s | 4.335 s | 0.0031 s | 194.09 MiB | 31.72 MiB |
| 1,000,000 | 52.844 s | 79.076 s | 0.0031 s | 1941.98 MiB | 35.98 MiB |

One root/user and fixed-length paths were used. The database included indexes, change records, latest catalog and pending state. At that historical version, initial queue transactions used 500 items. Later pages reused processed position/pending indexes. Memory belongs to the independent benchmark process, excluding Qt.

This is not end-to-end validation of a million real files. It excludes actual traversal/hashing/network transfer, multi-user contention and long-term cleanup pressure. Measure according to disk, file sizes and users. Per-user state costs extra storage. SQLite reuses freed pages after compaction but does not necessarily shrink the database file.

## Known boundaries

- Automatic confirmation means the server judged a complete transfer; directories/deletions still need explicit acknowledgement.
- Only latest contents are provided, without snapshots or block-level deltas.
- Clients sharing a userid share progress, even with single-connection restriction disabled.
- Path isolation does not defend against malicious concurrent local path replacement.
- The temporary simple-sync.invalid namespace must move to a project-owned domain before public release.
- Windows/Linux and million-real-file scanning/transfers remain unvalidated.
