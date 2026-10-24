# userid incremental synchronization protocol v2

[简体中文](../sync-protocol-v2.md) | [English home](../../README.en.md)

Applies to application 0.3.0 and later. One server publishes files; each userid has one shared progress record. There are no device/task identities, full-list mode, client-side time reset or client-selected scopes. Standard SFTP transfers remain compatible.

## 1. Discovery, framing and authentication

The SFTP v3 SSH_FXP_VERSION response advertises these extensions with ASCII version `2`:

```text
sync-v2-info@simple-sync.invalid
sync-v2-list@simple-sync.invalid
sync-v2-ack@simple-sync.invalid
```

.invalid is the project's temporary private namespace. Before public release, choose a project-owned domain and provide compatibility aliases. v1 open/manifest/changes/ack are no longer served. Do not mix generations. Libraries exposing handshake extensions can inspect them; otherwise try info and handle SSH_FX_OP_UNSUPPORTED.

Request, excluding outer packet length:

```text
byte    SSH_FXP_EXTENDED = 200
uint32  request_id
string  extension_name
string  UTF-8 JSON object
```

Response:

```text
byte    SSH_FXP_EXTENDED_REPLY = 201
uint32  request_id
string  UTF-8 JSON object
```

string is a four-byte big-endian length followed by that many bytes. Request JSON is limited to 64 KiB; trailing SSH fields are forbidden. Filenames must be addressable as UTF-8 SFTP paths.

Success: `{"ok":true,"result":{...}}`.

Business error: `{"ok":false,"error":{"code":"USER_MISMATCH","message":"..."}}`.

Unknown extensions and malformed SSH framing still return standard SFTP STATUS. Handle both standard and JSON errors.

userid comes from the authenticated SSH identity. list/ack must carry that same userid. Knowing another userid grants no access. Scope and file filtering also enforce existing account permissions.

## 2. info

Request: `{}`.

```json
{
  "protocol":"2",
  "namespace":"simple-sync.invalid",
  "userid":"32-character-permanent-id",
  "hash":"sha256",
  "confirmation":"manual",
  "sync_enabled":true,
  "incremental_start":0,
  "directories":["/reports"],
  "max_page_size":200,
  "operations":["info","list","ack"],
  "server_time":1792742400.125
}
```

Validate protocol and hash. Discover userid from the server or verify a supplied value against info. Business times are server-generated Unix seconds; convert time zones only for display. Versions, processed positions and byte sizes use decimal strings to avoid cross-language integer precision loss. Descriptive identifiers/checksums in examples are placeholders, not valid IDs to send.

## 3. list: paginated incremental pending files

```json
{"userid":"...","limit":100,"page_token":null}
```

limit is 1–200. The server uses configured directories, includes/exclusions, permissions and start time. Clients cannot request full mode, expand scope or change the start boundary.

```json
{
  "userid":"...",
  "server_time":1792742400.125,
  "processed":"1050",
  "pending":12,
  "failed":2,
  "synced":80,
  "has_more":true,
  "next_page_token":"opaque-signed-token",
  "files":[
    {
      "file_id":"64-character-permanent-id",
      "path":"/reports/a.csv",
      "version":"1048",
      "updated_at":1792742390.25,
      "operation":"upsert",
      "kind":"file",
      "size_bytes":"20480",
      "sha256":"hexadecimal-SHA-256",
      "status":"pending",
      "error":null
    }
  ]
}
```

- pending includes pending and failed entries; failed is their failed subset.
- synced counts current completion details plus compacted completions, including directories.
- processed is an internal processed-change position, not a downloaded position. Advancing it never discards failures.
- operation is upsert/delete; kind is file/directory. Directory/deletion sha256 is null.
- Paths start with `/` relative to the account root. Scope `/reports` still returns `/reports/a.csv` with its prefix.
- Files require download permission; directories require list or download. Filtering never bypasses ordinary SFTP authorization.
- A path has a permanent file_id; content changes advance its version. Rename is deletion of the old path plus creation of the new one.
- Repeated updates collapse to the latest pending version. Historical content is not promised.

Pages sort by file_id. Tokens bind userid, settings revision, upper bound and previous position, and must be returned unchanged. Changing time/scope/patterns/permissions invalidates old tokens; restart from the first page.

Changes during pagination may not appear in that pagination run. They stay pending and can be found by querying from the beginning again. Pagination is not confirmation. The first pending count is not a fixed number that must complete in the round.

Query current pending versions for particular files:

```json
{"userid":"...","file_ids":["..."],"limit":100}
```

Supply 1–200 identifiers, without page_token. Completed files are not returned. This supports retry after source changes, not historical version retrieval.

## 4. Standard SFTP downloads

Use normal OPEN/READ/CLOSE or the library's get(); there is no separate download extension.

1. List pending entries.
2. Download to a temporary file, resuming by version if needed.
3. Verify size/SHA-256 and persist at the destination.
4. Explicitly acknowledge that version.

The SDK stages the listing in local SQLite, then deletes files, deletes directories deepest-first, creates directories shallowest-first and downloads files. This handles type changes/dependencies. Default retention leaves local copies of deleted server files. --delete removes matching files/empty directories only, never extra local content recursively. The newer SDK can stage verified reusable local contents before deletion.

Source changes during transfer may cause a checksum mismatch. Never confirm an old version using the latest version's checksum. Query again by file_id and retry. There are no historical content snapshots.

## 5. ack: explicit success or failure

```json
{
  "userid":"...",
  "files":[
    {"file_id":"...","version":"1048","status":"success","sha256":"..."},
    {"file_id":"...","version":"1049","status":"failed","error":"disk full"}
  ]
}
```

Each batch holds 1–200 entries and must fit 64 KiB. The SDK normally batches about 50 and splits by byte size. File upsert success requires the checksum of the offered version; directories/deletions require no checksum.

```json
{
  "userid":"...",
  "complete":false,
  "pending":1,
  "failed":1,
  "synced":81,
  "server_time":1792742500.5
}
```

- Only versions offered to that user can normally be confirmed. Repeated success is idempotent.
- If version 1048 is acknowledged after update 1055, 1048 is recorded completed and 1055 stays pending. Confirmation time cannot replace version checks.
- Older success does not clear a newer failure.
- The server sets confirmation time and ignores extra client timestamps.
- There is no finish/round-commit interface; each file result persists independently.
- Ordinary complete SFTP download does not alter manual-mode progress.
- Automatic mode also accepts explicit ack for resume, directories, deletion and client verification/storage.
- Deletion acknowledgement means the client handled the event according to its policy; retention mode may keep a local copy and acknowledge it.

## 6. Automatic confirmation boundaries

Only pending regular files for automatic-mode users are tracked. Requirements:

- Actual ranges read through one read-only handle cover `[0, size)`; duplicates count once.
- Content matches the indexed version; size/mtime/ctime/inode remain unchanged at open/close, with no path replacement.
- No read error, and a normal client SSH_FXP_CLOSE request.

Empty files can confirm on normal close. Excessive fragmentation disables confirmation; at most 4,096 ranges are tracked.

No automatic success for partial/EOF-only reads, cross-handle assembly, nonzero-offset resume, disconnect cleanup, source changes, directory browsing or server-side copy-data. Only actual FXP_READ response ranges count. Directories/deletions require explicit ack and may remain pending after ordinary SFTP transfers; the full SDK confirms these explicitly.

The guarantee is that the server judged the complete content sent, not proof of client durable storage or checksum verification. Choose manual confirmation when client saving must be confirmed.

## 7. Start time, progress and compaction

Candidates match scope/patterns/permissions, have server updated_at >= configured start and lack success for the current version. First observation sets server update time; unknown history cannot be reconstructed from file mtime.

Three state layers:

1. Shared catalog: latest upsert/deletion per path, version and server time.
2. Per-user processed: position whose new changes have entered that user's queue.
3. Per-user file state: target/completed versions, failures and confirmation source.

Normal queries process only new catalog positions, then read indexed pending state rather than comparing all successful history. Latest catalog and durable pending state recover offline users even after change-log expiry.

When the server changes the boundary:

- Default behavior preserves unfinished entries, reselects the time range and continues skipping completed versions.
- Explicit restart clears pending/completed/archived state; earlier missing files may not be offered again.
- Moving backward reevaluates that range without exposing a separate full-sync mode.

Expired completion details compact in batches to userid/file_id/version/completed_at, retaining necessary success versions. New changes can reactivate files. Counts are transactionally cached rather than recalculated from all history each time.

Historical offered-version credentials may expire; latest pending-version credentials do not disappear due to retention. An old acknowledgement returning VERSION_MISMATCH should query the newest version. Failed/unfinished entries are not removed by completion compaction.

## 8. Errors and adaptation

| Code | Client action |
| --- | --- |
| USER_MISMATCH | Check authenticated SSH account and userid. |
| USER_SETTINGS_CHANGED | Log in again after server account changes. |
| SYNC_DISABLED | Ask the administrator to enable incremental synchronization. |
| INDEX_NOT_READY | Wait for initial indexing. |
| BAD_REQUEST | Correct JSON, fields or page size. |
| BAD_TOKEN | Restart at the first page; never edit tokens. |
| VERSION_MISMATCH | Query the latest pending version. |
| CHECKSUM_MISMATCH | Verify downloaded content; do not advance state. |
| SERVER_STOPPING | Reconnect and continue. |

Login/IP restrictions reject authentication before this JSON interface. Native component exceptions represent disconnections and standard SFTP errors.

AsyncSSH 2.24.1 private APIs are isolated in transport.py without global monkey patches; upgrades require regression tests. The standalone examples/paramiko_pull.py is validated with Paramiko 4.0.0 and standard get, without the SDK. It retains deleted files and does not replace the full SDK's directory ordering/resume logic.

From 0.5, human-readable error messages can be Chinese or English, according to server language. Wire extension names, field names, operation/status values and error codes are unchanged. Branch on codes rather than message text. There is no per-SFTP-client language negotiation.
