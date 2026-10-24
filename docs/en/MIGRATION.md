# Upgrade and rollback: 0.2 / 0.3 → 0.4 / 0.5

[简体中文](../MIGRATION.md) | [English home](../../README.en.md)

## Before upgrading

1. Stop the old server and close the desktop app. Never let two versions operate on one data directory concurrently.
2. Locate the application data directory displayed at the bottom of the window. Do not make it a shared directory.
3. Keep `ssh_host_ed25519_key`. Upgrading preserves the key, so client fingerprints should remain unchanged.
4. Upgrade and start using the existing Conda environment. Initial indexing may take time.

## Automatic backups and migration

- Loading an old config.json first saves its original configuration and a consistent SQLite snapshot in `backups/<UTC timestamp>-config-v4/`, generates permanent userids for accounts that lack one, and writes configuration schema_version 4.
- The database migration also creates `backups/<UTC timestamp>-database-v3/`. SQLite backup API captures committed WAL contents; it does not simply copy a live database file.
- Database user_version becomes 3. File indexes and host keys are preserved. Older catalogs are reconstructed from existing files and available change records; missing business timestamps use server migration time.
- Legacy tasks/rounds/deliveries/task_files are removed from the runtime database but retained in pre-migration backups. Their v1 completion progress is not merged. Upgrading from 0.3 preserves userids, v2 pending/completed state, counters and pagination keys.
- Migration is idempotent: subsequent normal startup does not regenerate userids or repeat migrations. Backup directories use POSIX 0700; configuration/database backups use 0600.
- Defaults are single connection enabled, unrestricted IP, sync enabled, directory `/`, start time 0, include `*`, manual confirmation. Review them before production use, especially start time.

v1 clients must migrate to v2. There are no aliases for old extensions. Standard SFTP downloads remain compatible; automatic tracking occurs only for users configured for automatic confirmation.

Before a new SDK opens v1 client state, it creates `client-v1-backup-<timestamp>.sqlite3`. Old device identity is no longer used. Local checksum caches can be reused; v2 adds server/account/userid binding. A new local destination does not create separate server progress: the same userid may not receive already acknowledged versions. Use standard SFTP for complete directory copies.

0.4 added authentication budgets and authorized_keys (empty by default, preserving password login). Old releases cannot read the new configuration fields directly. Roll back using matching configuration/database backups. The server holds a data-directory process lock, so desktop and headless servers cannot share it simultaneously.

0.5 adds Chinese/English localization without another configuration or database schema change. Userids, paths, permission identifiers, protocol fields, versions and progress are not translated. UI language is not persisted in configuration. Existing error text and runtime logs are retained as originally recorded.

## Changing incremental start time

Stop the server before editing in the desktop editor. Operators may prepare disk configuration and reload it; see [Operations](OPERATIONS.md).

- Without **Restart from this time**, pending/failed entries and completed versions are retained, while candidates in the new time range are reevaluated.
- Selecting restart shows a confirmation dialog. Startup or the queue refresh after reload clears that user's old state. Earlier unfinished files may be skipped; this is not a replacement for normal retries.
- Moving the boundary backward reevaluates that range. Start time refers to server observation, not file mtime.
- A reset invalidates old page tokens. Restart queries from the first page.

The persisted sync_generation executes a reset once rather than clearing progress at every startup.

## Rollback

1. Stop the new version and preserve a copy of its current data directory.
2. Prefer the old configuration and database saved together in a `*-config-v4/` backup. If only the database migrated, use its matching `*-database-v3/` snapshot. Inspect reason.json for origin.
3. With the server fully stopped, restore that matching config.json/sync.sqlite3 pair and remove stale database -wal/-shm files. Do not mix files from separate backup snapshots.
4. Keep the host key unchanged, switch to the environment/source matching the backup, and restart.

Restoring a backup discards subsequent sync records. No automatic rollback button is provided, to avoid replacing a live database. Tests validate backups, index versions, non-merging of old progress and repeat startup in temporary directories; they do not replace production data for you.
