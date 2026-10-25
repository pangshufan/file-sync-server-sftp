# Simple Sync · SFTP Server

[简体中文](README.md) | English

A Python + PySide6 desktop server, version **0.5.2**. It provides multi-user SFTP, directory permissions, login restrictions, and **server-managed file-level incremental synchronization with shared progress per userid**.

## Startup and environment

On the current development machine:

```bash
source /opt/anaconda3/etc/profile.d/conda.sh
conda activate /Volumes/T9/sync/env/sync-env
cd /Volumes/T9/sync/file-sync-server
python -m sync_server
```

You can also double-click [start.command](start.command) or run `simple-sync-server`. The script first uses `SIMPLE_SYNC_PYTHON`, then `env/sync-env/bin/python` beside the project directory, and finally python3 from PATH.

Create or update the environment:

```bash
conda env update --prefix /Volumes/T9/sync/env/sync-env --file environment.yml
conda activate /Volumes/T9/sync/env/sync-env
```

Install the locked dependencies:

```bash
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
```

Validated dependencies: Python 3.12.15, AsyncSSH 2.24.1, PySide6 Essentials 6.12.0, watchdog 6.0.0 and Paramiko 4.0.0. Local validation uses macOS arm64; Windows/Linux runtime acceptance remains pending.

## First use

1. Set the listening IP and port. The default is `127.0.0.1:2222`. For LAN access, use the host's LAN IP or `0.0.0.0` for all IPv4 interfaces. Clients connect to the actual host IP.
2. Add a user, choose a shared root directory and grant the required permissions. These accounts are separate from operating-system accounts.
3. In the user's **Incremental sync** tab, configure directories, start time, patterns and confirmation mode. The default start is 0. Newly indexed files use the server's first-observation time.
4. Save settings and start the server. Indexing runs in the background; incremental requests become available after the first scan.
5. Connect with an SFTP client and verify the SHA256 host fingerprint shown in the desktop app. Allow the listening port through the firewall.

The **Connections** tab shows username, source IP/port, connection time, duration and shared directory, and allows disconnection. Closing the main window stops the server and closes its connections. Stop the server before editing users in the desktop editor. While running, you can edit config.json in the data directory and click **Reload config file**. Only changed accounts are disconnected. Listener changes and new monitored roots require a restart; see [Operations](docs/en/OPERATIONS.md).

The app follows the system's light/dark appearance and updates open windows without restarting. Themes cover dialogs, inputs, tables, selection and disabled states, including text colors. If no color scheme is reported, the startup system palette is used. Linux notifications depend on the desktop environment and Qt platform plugin.

The interface follows system UI language preferences at startup. Chinese preferences select Simplified Chinese, English preferences select English; unsupported preferences fall back to English if no supported preference is found. Restart after changing the system language. `SIMPLE_SYNC_LANGUAGE=en` or `zh` overrides detection for testing/deployment. See [Localization](docs/en/LOCALIZATION.md).

## Users, login and directory permissions

A userid is permanent and survives username changes. Each userid has one shared incremental progress record. Using the same account on two machines shares results rather than creating separate device tasks.

| Setting | Behavior |
| --- | --- |
| Single connection | Enabled by default. Rejects a new login when that userid is already connected; does not evict the existing connection. Different users can connect concurrently. |
| Login IP / CIDR | Multiple IPs/networks, separated by semicolons in the UI. Empty means unrestricted. IPv4 and IPv6 are supported. |
| Account enabled | Disabled accounts cannot log in. |
| Root directory | Client `/` maps to the selected local directory. |
| Subdirectory rules | Longest path match, fully replacing parent permissions. Clearing all permissions denies access. |

Root permissions control listing, download, upload/overwrite, directory creation, rename/move and deletion. New accounts default to read-only. A move destination also requires upload permission; overwrite rename additionally requires delete permission at the destination. Moving cannot expand visibility or bypass subdirectory rules.

Permission matching normalizes Unicode and ignores case on macOS/Windows; Linux is case-sensitive. Do not assign different permissions to names that differ only by case. Each user has one physical root but can configure multiple synchronization directories within it.

## Incremental synchronization settings

| Field | Meaning |
| --- | --- |
| Sync enabled | Disables only the incremental interface when off; standard SFTP remains available. |
| Sync directories | One virtual path per line, such as `/reports`, subject to existing permissions. |
| Incremental start | Inclusive lower bound in server Unix time; displayed in the server's local time zone. |
| Include patterns | One wildcard per line, default `*`. An empty list includes no files. |
| Exclude patterns | Take precedence over includes, e.g. `*.tmp`, `private/**`. |
| Confirmation | Manual or automatic; manual by default. |
| Restart from this time | Requires confirmation. Clears this user's pending, completed and archived state before selecting again. |

Patterns match the full path without its leading `/`, or the basename, using Python fnmatch semantics. `*` can match `/`. Directory entries are not filtered by file include patterns, but remain subject to scope, exclusions and permissions.

There is no full-sync mode in the incremental interface. Use standard SFTP to download complete directories:

```bash
sftp -P 2222 alice@127.0.0.1
# Use get or get -r in the interactive session as needed.
```

The server uses size, nanosecond mtime, ctime and inode for quick change detection, then SHA-256 to confirm content changes. Timestamp-only changes with identical content do not create a new business version. Update time is when the server confirms a change, not historical file mtime. History unknown before indexing is not reconstructed as an earlier business timestamp.

## Manual and automatic confirmation

**Manual:** After downloading, verifying and saving, the client explicitly acknowledges file_id, version and checksum through JSON. Standard SFTP download alone does not change sync progress.

**Automatic:** The server tracks actual read ranges for one read-only handle. It requires full coverage, an unchanged file signature, no errors and a normal CLOSE request. Partial reads, gaps despite repeated reads, EOF-only reads, disconnect cleanup, multiple handles, resuming from a nonzero offset and server-side copy-data do not confirm completion. Empty files can be confirmed on normal close.

Automatic confirmation means the complete content was sent; it cannot prove client verification or durable storage. Directories and deletion events still need explicit acknowledgement, so they can remain pending after ordinary SFTP file downloads. The SDK explicitly confirms them and also confirms resumed downloads. Explicit confirmation is allowed in automatic mode.

If a new version appears during transfer, only the version actually downloaded may be confirmed; the new version remains pending. Another file's successful timestamp cannot skip a failed file.

## Resetting time and long-running operation

The processed-change position and completed file versions are stored independently. The server processes new changes and queries a durable pending set. Repeated changes to one file collapse to its latest pending version, without repeatedly traversing all successful records.

Changing the start time preserves unfinished items by default. Select **Restart from this time** explicitly to discard old backlog. Moving the start backward reevaluates that range while retaining completed-version checks. Last success time is display-only and cannot safely replace the filtering boundary.

Expired completion details are compacted in batches to userid/file_id/completed version/completion time. Necessary versions remain and counts are maintained transactionally. The latest catalog includes deletion markers, so recovering offline users does not require every historical change record.

Full metadata scan defaults depend on size: up to 10,000 entries every 10 minutes; up to 100,000 every hour; larger roots every 4 hours. An explicit interval in seconds is supported. Native watchdog events debounce for about 2 seconds, with continuous changes coalesced for at most about 10 seconds. Hash audits rotate in batches, default 7 days; change records and completion details default to 30-day retention. Disk speed and file size affect audit coverage. Failed scans or disconnected disks do not directly publish mass deletions.

## Python SDK and command line

First verify the fingerprint through standard SFTP and establish trust in `~/.ssh/known_hosts`:

```bash
conda activate /Volumes/T9/sync/env/sync-env
simple-sync-pull 127.0.0.1 ./downloads --username alice --interval 30
```

The default prompts for a password. Unattended use supports `--identity /path/to/private-key --batch` or `--agent --batch`. Configure public keys in the user's **SSH public keys** tab; public-key-only accounts are supported. Without interval, the client runs one round and exits. Continuous mode reconnects after disconnects. By default, server deletions retain local copies. `--delete` deletes matching files or empty directories only, without recursively removing extra local content.

Other arguments: `--port`, `--known-hosts`, `--state`, `--userid`. userid can be discovered through info; an explicit value must match the account. There are no `--name`, `--scope` or `--reset` switches; those settings belong to the server.

```python
from pathlib import Path
import asyncssh
from sync_server.client import PullClient

async def pull(password):
    async with asyncssh.connect('127.0.0.1', 2222,
                                username='alice', password=password) as connection:
        async with connection.start_sftp_client() as sftp:
            client = PullClient(sftp, Path('downloads'), Path('client-state'))
            try:
                return await client.run_once()
            finally:
                client.close()
```

The client state directory must be outside the destination. By default it is a hidden sibling directory, holding checksum caches, staged listings and per-version resume files. It does not represent independent server progress. Do not let multiple processes use the same state directory concurrently. Separate destinations may use separate caches, but still share server completion state for the same userid.

Downloads go through temporary files, size/checksum verification and destination replacement before batch acknowledgement. Renamed/new paths can reuse completed local content by checksum and size, verified and staged before old paths are deleted. No hard links are created; missing or invalid candidates fall back to normal download. Each entry gets at most three attempts per round; unfinished entries continue next round. Scope paths retain their prefix: `/reports/a.csv` becomes `downloads/reports/a.csv`. Use a dedicated destination: files included in synchronization will be updated.

The client does not watch local modifications. Local deletion or tampering is not automatically repaired without a new server change. Use standard SFTP when a complete copy is needed, rather than treating a userid reset as a full-sync task.

## Desktop synchronization status

The **Incremental sync** tab shows roots, userid, start time, pending, failed, completed including archived, processed position and last success. Pending includes failed items. Detail views paginate 200 items at a time and show target/completed version, server time, confirmation mode, IP and errors. Compacted archival records retain only essential versions and are not shown in the detail list.

## Data, migration and boundaries

The data directory is displayed at the bottom of the window. Configuration is `config.json`, database `sync.sqlite3`, host key `ssh_host_ed25519_key`. Passwords use randomly salted scrypt hashes. POSIX modes are 0700 for the data directory and 0600 for configuration, database and keys. Windows uses ACLs; chmod does not implement equivalent permission-bit semantics. WAL files reside in the protected directory. Do not put the database on a network share or inside a synchronization target.

[Migration and rollback](docs/en/MIGRATION.md) explains backups, non-merging of v1 progress, userid generation and client state migration. Stop the old program before upgrading. First-time migration backs up configuration and a consistent SQLite snapshot.

[Protocol v2](docs/en/sync-protocol-v2.md) is the current external contract. [Protocol v1](docs/en/sync-protocol-v1.md) is historical only. The [standalone Paramiko example](examples/paramiko_pull.py) demonstrates v2 requests, standard get and explicit acknowledgement without this SDK.

The server provides only SFTP v3. Shell, command execution, SCP and forwarding are disabled. Symbolic links, multiple hard links, devices and FIFOs are rejected; changing system ownership/permissions is prohibited. Disable “preserve original permissions” in clients. Path isolation is application-level; shared local directories must be managed by trusted processes. It is not an OS sandbox against malicious concurrent local path replacement.

Portable builds support macOS, Windows and Linux. A Qt-free headless entry and systemd/launchd/Windows WinSW templates are included, requiring deployment-specific configuration and manual installation. There are no historical content snapshots, block-level deltas or multi-writer conflict merging. Runtime logs persist as rotating JSONL; the UI retains the latest 2,000 lines. Passwords, private keys and full configuration are not logged. See [Operations](docs/en/OPERATIONS.md).

## Icon and packaging

The app uses a folder and sync-arrow icon, provided as SVG, PNG, Windows ICO and macOS ICNS. Editable source: `src/sync_server/assets/app-icon.svg`.

```bash
python -m pip install ".[build]"
python tools/build_app.py
```

Build natively on each target OS. `dist/` contains desktop server, headless server, standalone client and a release archive; target machines do not need Conda. GitHub Actions provides a three-platform build workflow. [Packaging](docs/en/PACKAGING.md) covers paths, icon generation, deployment, signing and acceptance boundaries.

## Validation and development

```bash
python -m pytest -q
python tools/check_translations.py
python tools/benchmark_concurrency.py --files 2000 --users 4 --seconds 10
python tools/benchmark_index.py --files 100000
python tools/benchmark_index.py --files 1000000
```

Integration tests use temporary directories, loopback ports and Qt offscreen, without replacing production data. Coverage includes migration, permissions, login restrictions, incremental times, acknowledgement, recovery, native watching, SDK, Paramiko, desktop and localization flows.

The concurrency benchmark uses actual temporary files and loopback SFTP to measure list/ack/status latency during scans, not download throughput. The synthetic benchmark creates SQLite metadata only, measuring indexing, initial pending generation and subsequent pages. It cannot predict actual disk scanning, hashing or transfer time; per-user queues consume additional disk space. See [Validation](docs/en/VALIDATION.md).

Code map: `config.py` settings; `migration.py` backups; `index.py` catalog; `sync_api.py` user queues/counters; `server.py` SFTP/login/automatic confirmation; `transport.py` protocol adaptation; `monitor.py` watching/scans; `ui.py` desktop; `client.py` SDK/CLI; `headless.py` server CLI; `auth_limits.py` login budgets; `audit.py` persistent runtime logs; `i18n.py` messages; `qt_i18n.py` GUI language initialization.

## English documentation

- [Operations](docs/en/OPERATIONS.md)
- [Migration and rollback](docs/en/MIGRATION.md)
- [Packaging](docs/en/PACKAGING.md)
- [Localization](docs/en/LOCALIZATION.md)
- [Implementation checklist](docs/en/IMPLEMENTATION_CHECKLIST.md)
- [Validation records](docs/en/VALIDATION.md)
- [Protocol v2](docs/en/sync-protocol-v2.md)
- [Historical protocol v1](docs/en/sync-protocol-v1.md)
