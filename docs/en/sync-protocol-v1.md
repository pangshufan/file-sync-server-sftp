> Historical document: 0.2.x only. Since 0.3.0, use [Protocol v2](sync-protocol-v2.md). Old device/task progress is not automatically merged.

# Simple Sync SFTP extension protocol v1

[简体中文](../sync-protocol-v1.md) | [English home](../../README.en.md)

Status: experimental project protocol. A single server publishes latest files for one-way download by multiple users/devices. There is no bidirectional merge or historical content storage.

## 1. Transport and discovery

Reuse the authenticated SSH sftp subsystem, retaining SFTP v3. Ordinary clients continue standard file operations.

The server advertises these SSH_FXP_VERSION extensions with ASCII version `1`:

```text
sync-info@simple-sync.invalid
sync-open@simple-sync.invalid
sync-manifest@simple-sync.invalid
sync-changes@simple-sync.invalid
sync-ack@simple-sync.invalid
```

simple-sync.invalid is a temporary private namespace, not a claim to any third-party domain. Before public release, choose a project-owned domain and migrate with compatibility aliases. Third-party implementations should centralize this constant.

Request, excluding outer packet length:

```text
byte    SSH_FXP_EXTENDED = 200
uint32  request_id
string  extension_name
string  utf8_json_request
```

Response:

```text
byte    SSH_FXP_EXTENDED_REPLY = 201
uint32  request_id
string  utf8_json_response
```

SSH string encoding is a four-byte big-endian byte count followed by those bytes. No fields may follow the JSON object. Requests are limited to 64 KiB.

Success: `{"ok":true,"result":{...}}`.

Business error: `{"ok":false,"error":{"code":"INDEX_NOT_READY","message":"..."}}`.

Unknown extensions and malformed SSH packets use standard SSH_FXP_STATUS. Not every error is JSON.

Sequences, file versions, byte sizes and nanosecond mtime are decimal strings in JSON. Task/round IDs are opaque. Paths are UTF-8 virtual paths beginning with `/` inside the shared root; they do not expose server-local paths.

## 2. info

Request: `{}`.

```json
{
  "protocol": "1",
  "namespace": "simple-sync.invalid",
  "hash": "sha256",
  "max_page_size": 200,
  "mode": "server-to-client-latest",
  "operations": ["info", "open", "manifest", "changes", "ack"]
}
```

Check version/hash algorithm and do not continue blindly with unknown versions.

## 3. open

```json
{"device_id":"persistent-device-id","name":"default","scope":"/reports","reset":false}
```

- device_id is required; name defaults to default. Both have 1–128 characters.
- scope defaults to `/` and includes descendants; `..` is forbidden.
- Authenticated account/device_id/name uniquely identify a task. Separate local targets need separate device state or task names.
- reset:true forces a new full manifest.
- Request usernames never grant access; the account comes from SSH authentication.

Response:

```json
{
  "task_id":"opaque-task-id",
  "cursor":"1050",
  "mode":"changes",
  "created_at":1792630000.0,
  "last_sync_at":1792631000.0
}
```

Times are Unix seconds; last_sync_at is initially null. The first task returns mode manifest.

Directory/permission/scope changes, or a cursor older than retention, preserve task identity but rebuild its manifest. Old tokens/rounds become invalid. Reopen and use the returned mode.

Indexing starts in the background. Until the first full scan finishes, INDEX_NOT_READY instructs clients to retry later.

## 4. manifest / changes

```json
{"task_id":"...","limit":100,"page_token":null}
```

limit is 1–200. The first request persists a round and fixes upper_cursor. Subsequent requests/reconnections reuse it. manifest copies the current index; changes collapses updates between completed cursor and upper bound to the latest state per path.

```json
{
  "task_id":"...",
  "round_id":"...",
  "upper_cursor":"1080",
  "has_more":true,
  "next_page_token":"opaque-signed-token",
  "changes":[
    {
      "path":"/reports/a.csv",
      "version":"1051",
      "operation":"upsert",
      "kind":"file",
      "size_bytes":"20480",
      "mtime_ns":"1792631000000000000",
      "sha256":"hex-digest",
      "status":"pending",
      "error":null
    }
  ]
}
```

- operation is upsert/delete. Directory moves appear as old-path deletion/new-path creation.
- kind is file/directory. Directory/deletion checksums are null.
- status is pending/failed/done. Resuming may skip done entries.
- Pages sort by path, but execution must honor dependencies: delete files, remove directories deepest-first, create shallowest-first, then download files.
- Files require download permission; directories require list/download. Unauthorized paths/checksums/deletion records are never returned.
- Signed tokens bind task and round; return unchanged rather than constructing them from global sequences.
- Pagination advances enumeration only, not the completed cursor.
- Multiple changes in one round require convergence to latest state, not replay of every version.

Scope retains virtual prefixes: scope /reports with /reports/a.csv maps to reports/a.csv beneath the SDK destination.

## 5. Download and acknowledge

Download using standard OPEN/READ/CLOSE, preferably to a temporary file. Verify size/SHA-256 before atomically replacing the target.

Success:

```json
{
  "task_id":"...",
  "round_id":"...",
  "items":[{"path":"/reports/a.csv","version":"1051","status":"done","sha256":"hex-digest"}]
}
```

Failure:

```json
{
  "task_id":"...",
  "round_id":"...",
  "items":[{"path":"/reports/a.csv","version":"1051","status":"failed","error":"disk full"}]
}
```

Batches have at most 200 items and 64 KiB JSON. The SDK normally batches about 50 and splits by size. Ownership, offered path/version and checksum are validated. Acknowledgement is a client statement; the server cannot independently prove durable client storage.

Deletion policy is client-selected: mirroring deletes matching files/empty directories; retention keeps copies and acknowledges the event. Changing policy does not replay acknowledged deletions; rebuild the manifest when needed.

After all pages are enumerated and every entry acknowledged:

```json
{"task_id":"...","round_id":"...","finish":true}
```

Response:

```json
{"complete":true,"pending":0,"cursor":"1080","refreshed":[]}
```

Only full enumeration and completion advance the task cursor. Later successes remain recorded without skipping failures. Repeating the last finish is safe.

## 6. Source changes after listing

Versions do not imply historical content storage. Ordinary SFTP may read newer content, so verify against the manifest.

If checksums differ or a file disappears, never acknowledge old success. Send:

```json
{
  "task_id":"...",
  "round_id":"...",
  "items":[{"path":"/reports/a.csv","version":"1051","status":"refresh"}]
}
```

The server checks the index, updates the entry to a newer upsert/deletion and returns it in refreshed. Download/verify again. If indexing has not caught up, the same version may be returned; retry later.

Acknowledgement above the round's bound is allowed, while the cursor advances only to its original bound. The next round skips already covered old events using per-file completed versions. This avoids historical content storage without losing other later updates.

## 7. Recovery and lifecycle

- Tasks, rounds and per-file completion persist in SQLite across server restarts.
- Clients must persist device identity. Deleting client state creates a new device identity.
- Logs default to 30-day retention; old clients recheck a full manifest.
- Revoked permissions stop entries from being offered, without remotely erasing downloads.
- Initial manifests/upper bounds are index snapshots, not disk-content snapshots. Stable sources eventually converge.
- Incremental synchronization is file-level, not block-level.
- Two processes cannot consume the same task concurrently; use one executor per device task.

## 8. Business errors

| Code | Client action |
| --- | --- |
| INDEX_NOT_READY | Wait for first indexing. |
| BAD_REQUEST / BAD_TOKEN | Correct requests; do not repeatedly retry invalid parameters. |
| TASK_NOT_FOUND | Check current account and task identity. |
| POLICY_CHANGED / CURSOR_EXPIRED | Reopen and build the manifest using the returned mode. |
| WRONG_MODE | Use the returned mode and finish the existing round. |
| VERSION_MISMATCH | Query again; never bypass version checks. |
| CHECKSUM_MISMATCH | Check downloaded content/checksum. |
| ROUND_NOT_FOUND | Reopen and query the current round. |
| SERVER_STOPPING | Reconnect and resume. |

## 9. Component adaptation

AsyncSSH private interfaces are isolated in transport.py, pinned to 2.24.1, without global monkey patches. Dependency upgrades require interoperability tests.

The historical standalone Paramiko example used _request for extensions and ordinary get for contents, retained local deletions and executed page order. Large-directory ordering, type changes and reliable retries require the full SDK or a complete protocol implementation. **The current examples/paramiko_pull.py now targets v2 and is not a v1 implementation.**

Protocol mechanism reference: [SFTP v3 extensions](https://www.openssh.org/txt/draft-ietf-secsh-filexfer-02.txt).
