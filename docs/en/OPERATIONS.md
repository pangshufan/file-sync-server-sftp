# Operations and performance (0.4 and later)

[简体中文](../OPERATIONS.md) | [English home](../../README.en.md)

## Database access and transactions

Each IndexStore reuses one write connection protected by a write lock. Up to four independent read-only connections serve status/details and queue-boundary reads. They use query_only and hold snapshots for the query scope. PRAGMAs are set when connections are created rather than for every operation. WAL readers see committed snapshots, never uncommitted updates.

Scanning, queue refresh, policy reconciliation/reset and cleanup commit batches of at most 200 entries. A changed policy is recorded only after all reconciliation pages finish, allowing interrupted work to resume. A per-userid lock prevents reconciliation and acknowledgement for the same user from interleaving. Read-only status queries do not compete for the write lock. Batching reduces lock duration; it does not remove the total cost of traversing large policy changes.

Change-log cleanup has an approximately one-second budget per run; completion/offer cleanup approximately five seconds. Budgets are checked between transactions, so one SQL operation or transaction can exceed the budget. Remaining work continues next run without breaking recovery from the latest catalog. list/ack still synchronously catch up the current user's new catalog entries. Large initial backlogs may be slow; the protocol has not become an asynchronous job API.

`sync_status()` exposes database operation counts, cumulative waits, maximum write transaction duration and maximum write-lock wait. These include initialization, scanning and maintenance rather than SQL execution time alone. The dedicated per-file scan reader is not included. Stopping the server closes the pool.

0.5.1 fixes Windows ctime differences between stat/lstat and fstat. Cross-API signatures use size, mtime, birthtime and file ID on Windows, while POSIX retains ctime. Hashing and automatic confirmation also compare raw ctime/device-ID snapshots from the same open handle to reject real changes; simply dropping ctime checks would weaken protection. Windows birthtime is creation time and does not prove unchanged contents. Checksums, read coverage and handle checks still apply, and periodic hash audits remain enabled.

## Persistent runtime logs

Logs reside in `logs/server.jsonl` inside the data directory: UTF-8 JSONL with UTC timestamps. Each file is approximately limited to 5 MiB, with five rotated backups, approximately 30 MiB total. POSIX directory/file modes are 0700/0600. On Windows, access is determined by OS ACLs.

The network thread submits to a bounded queue; a background thread writes to disk. A full queue drops new records rather than blocking SFTP. The logging section of `sync_status()` provides dropped_records/write_errors, and the desktop sync page displays nonzero values. Shutdown waits for queued records to flush.

Logs include startup/shutdown, existing file-open/denial messages, and structured login, disconnect, rate-limit rejection and reload events. They exclude passwords, private keys and full configuration, but include usernames, paths and source IPs. This is a rotating runtime log, not a tamper-proof audit trail or complete record of all transfer results. New human-readable messages follow the server language; stored logs are not rewritten.

## Login protection

Stop the desktop server before changing **Login protection…**, or edit config.json. Defaults:

| Parameter | Default | Meaning |
| --- | --- | --- |
| auth_max_inflight | 4 | Concurrent password checks; rejects when busy rather than queuing without bound. |
| auth_ip_attempts | 20 | Authentication checks per IP within the window. |
| auth_user_attempts | 10 | Checks per account within the window. |
| auth_window_seconds | 60 | Sliding-window duration in seconds. |
| auth_max_connections | 128 | Total SSH connections, authenticated or not. |

Successful and failed checks count toward budgets. Public-key probes/signing can invoke callbacks multiple times; all count. Unknown accounts share one account bucket. IPv4 and its IPv4-mapped IPv6 representation share an IP bucket. Limits live in process memory and reset at restart. At most 4,096 active buckets are retained; new buckets are rejected when full instead of evicting existing protection. Shorter windows or higher budgets can accommodate multi-device automation.

Existing single-connection/IP restrictions, three failures per connection and the 30-second login timeout still apply. Cancelling an authentication coroutine does not release the budget while its native hash computation is still running.

## Public keys and unattended clients

Paste OpenSSH public keys in the user's **SSH public keys** tab: one per line, up to 32, without authorized_keys options. Keep password login or select **Public key login only**. Private keys stay on the client. Public-key accounts retain userid, permissions, IP and connection restrictions.

```bash
simple-sync-pull server.example ./downloads --username alice --identity /path/to/key --batch
simple-sync-pull server.example ./downloads --username alice --agent --batch --interval 30
```

identity accepts an unencrypted private key. Load encrypted keys into SSH Agent and use agent mode. batch disables password prompts and requires identity/agent. Verify the host fingerprint first and populate known_hosts; host verification remains enabled.

## Headless server

Configure the desktop app, stop it, then copy the complete data directory, including host key, sync database and token key, to a location accessible to the service account. Alternatively, prepare config.json in a dedicated directory. Copying only configuration creates new indexing/key state and does not preserve the original host identity and progress.

```bash
python -m sync_server.headless --data-dir /absolute/path/to/state --check-config
python -m sync_server.headless --data-dir /absolute/path/to/state
# After installation, simple-sync-serve is also available.
```

The entry does not import Qt. It runs in the foreground under a process supervisor. SIGINT/SIGTERM stop listening, disconnect clients, wait for scanning, and close database/logs. A process lock prevents GUI and headless servers from sharing the same state directory concurrently. The OS releases the lock when its handle closes; server.lock need not be deleted after a crash.

Templates are in packaging/services, or services in release archives:

- **Linux:** Edit User/Group, ExecStart and data-dir in simple-sync.service. Create an account with appropriate data/shared-directory access. An administrator installs the unit into /etc/systemd/system, runs daemon-reload and enable --now. ExecReload sends SIGHUP.
- **macOS:** Edit executable and data paths in org.simplesync.sftpserver.plist. Install as a user LaunchAgent or a LaunchDaemon with a configured execution identity. RunAtLoad starts it; failures trigger restart. Validate directory access during actual login/reboot scenarios.
- **Windows:** simple-sync-winsw.xml requires a separately provided WinSW executable, placed with matching XML naming. Edit data-dir, then have an administrator run the wrapper's install/start commands. Configure its account and ACLs. The program is not a native SCM service registerable directly with sc.exe.

Service installation/boot behavior remains unvalidated on target systems. Local tests cover macOS headless subprocess startup and clean SIGTERM exit. No service or startup setting is installed automatically.

## Windows build diagnostics

If the log reaches Build complete but Chinese --help fails with a cp1252 UnicodeEncodeError, the failure is the frozen program's standard-output encoding. 0.5.2 fixes it through stream initialization and PyInstaller UTF-8 runtime options, without disabling Chinese checks. `python -m pytest tests/test_console.py -q` forces CP1252 on any host; the build script additionally checks actual bundled help output in both languages.

An Actions failure during pytest is a test failure, before PyInstaller packaging. CI now runs `python tools/diagnose_filesystem.py` first, reporting a temporary file's path/descriptor metadata and normalized signatures. It attempts to upload a tests-<OS>-<arch> JUnit report even after test failure. Scan assertions include errors and root readiness state.

Commit and push the fix, including new modules/scripts, then build that new revision manually or create a tag matching pyproject.toml, such as v0.5.1. Rerunning an old failed run uses its original revision rather than fetching subsequent fixes. The supplied Windows log reported 17 failed, 51 passed and 1 skipped before this fix; it is not post-fix Windows acceptance.

## Classified configuration reload

After editing config.json, click **Reload config file** in the desktop app. Send SIGHUP to a Unix headless process, or run systemctl reload simple-sync under systemd.

- Scan/retention intervals and login budgets use their new values without disconnecting unchanged accounts.
- Password, public keys, permissions, enabled state, sync rules/time or username changes disconnect all connections for that userid only. New logins use the new snapshot. Old-snapshot incremental requests are rejected; policies reconcile on subsequent queue refresh.
- New accounts may use already monitored roots. Disabling/deleting an account does not stop other roots' connections. Removed roots remain watched until restart to avoid rebuilding all watchers.
- Listener IP/port changes, new monitored roots and changes to an existing account's root are rejected during reload and require restart.

Validation failure leaves the running configuration unchanged. Operators must repair any externally edited disk file. Desktop editors still edit while stopped; live reload applies a separately prepared disk configuration. Do not allow multiple processes to write config.json concurrently.

## Content reuse and continuously written files

The SDK finds up to 16 local completed-file candidates by SHA-256 and size, verifies the actual content again, and copies it to a temporary state file. It still explicitly acknowledges after saving the destination. Staging before deletion supports renames with delete=true. Hard links are not used, so local changes cannot propagate through linked copies. Missing, modified or symbolic-link candidates fall back to download. Reduced network traffic costs local hashing, copies and staging space.

Continuously appended large files still use file-level synchronization. Producers should write to an excluded temporary name, flush/fsync and close it, then atomically replace the published path on the same filesystem. Use a fresh temporary file for the next update instead of modifying published content. Publish closed, rotated log files and exclude the active log directory. Cross-filesystem moves are not guaranteed atomic. Increasing the scan interval alone does not guarantee a consistent file version.

There are no historical content snapshots, cross-version block reuse or cross-handle automatic confirmation. Ordinary clients need explicit confirmation after resumed downloads; the SDK already handles this. Block-level deltas and a database replacement remain measurement-driven options.

## Concurrency benchmark

```bash
python tools/benchmark_concurrency.py --files 2000 --users 4 --seconds 10
```

The benchmark creates actual temporary small files, prepares user queues, then repeatedly rewrites/scans. Real SFTP connections send list/ack while another task polls status. It reports P50/P95/P99/max latency and database metrics. Acknowledgements are metadata workload without file downloads, so results are not download throughput or complete sync verification.

File count, size, user count and duration are configurable. The script listens only on 127.0.0.1 and removes temporary data afterward. Disk use grows with content and per-user queues; begin small. benchmark_index.py remains the separate synthetic metadata benchmark.
