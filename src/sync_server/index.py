"""Disk-backed shared catalog. No per-scan collection of all paths in RAM."""
from __future__ import annotations


import hashlib
import os
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from .i18n import tr

BATCH_SIZE = 200


def root_id(path: str) -> str:
    return hashlib.sha256(os.fsencode(Path(path).resolve())).hexdigest()


class IndexStore:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.path = directory / "sync.sqlite3"
        if self.path.exists():
            with sqlite3.connect(self.path) as existing:
                version = existing.execute("PRAGMA user_version").fetchone()[0]
                if version > 3:
                    raise ValueError(tr('数据库来自更高版本，请使用对应版本的软件'))
                legacy = version < 3
            if legacy:
                from .migration import backup
                backup(directory, "database-v3")
        self.lock = threading.RLock()
        self._readers = threading.Condition()
        self._idle_readers = []
        self._reader_count = 0
        self._active_readers = 0
        self._writer = None
        self._closed = False
        self._metrics_lock = threading.Lock()
        self._metrics = {"read_operations": 0, "write_operations": 0,
                         "read_wait_seconds": 0.0, "write_wait_seconds": 0.0,
                         "max_write_seconds": 0.0, "max_write_wait_seconds": 0.0}
        self.scan_lock = threading.Lock()
        self.cancel = threading.Event()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS roots(
              id TEXT PRIMARY KEY, path TEXT NOT NULL, ready INTEGER NOT NULL DEFAULT 0,
              head INTEGER NOT NULL DEFAULT 0, floor INTEGER NOT NULL DEFAULT 0,
              last_scan REAL NOT NULL DEFAULT 0, duration REAL NOT NULL DEFAULT 0, error TEXT,
              entries INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS files(
              root TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL,
              mtime INTEGER NOT NULL, ctime INTEGER NOT NULL, inode INTEGER NOT NULL,
              digest TEXT, version INTEGER NOT NULL, hash_at REAL NOT NULL, seen TEXT NOT NULL,
              PRIMARY KEY(root,path));
            CREATE INDEX IF NOT EXISTS files_audit ON files(root,hash_at);
            CREATE TABLE IF NOT EXISTS changes(
              seq INTEGER PRIMARY KEY AUTOINCREMENT, root TEXT NOT NULL, path TEXT NOT NULL,
              operation TEXT NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL,
              mtime INTEGER NOT NULL, digest TEXT, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS changes_root_seq ON changes(root,seq);
            CREATE INDEX IF NOT EXISTS changes_retention ON changes(created);
            """)
            if "entries" not in [row[1] for row in db.execute("PRAGMA table_info(roots)")]:
                db.execute("ALTER TABLE roots ADD COLUMN entries INTEGER NOT NULL DEFAULT 0")
                db.execute("UPDATE roots SET entries=(SELECT count(*) FROM files WHERE root=roots.id)")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS catalog(
              file_id TEXT PRIMARY KEY,root TEXT NOT NULL,path TEXT NOT NULL,
              version INTEGER NOT NULL,updated_at REAL NOT NULL,operation TEXT NOT NULL,
              kind TEXT NOT NULL,size INTEGER NOT NULL,mtime INTEGER NOT NULL,digest TEXT,
              UNIQUE(root,path));
            CREATE INDEX IF NOT EXISTS catalog_versions ON catalog(root,version);
            CREATE INDEX IF NOT EXISTS catalog_time ON catalog(root,updated_at);
            CREATE INDEX IF NOT EXISTS changes_path ON changes(root,path,seq);
            CREATE TABLE IF NOT EXISTS sync_users(
              userid TEXT PRIMARY KEY,username TEXT NOT NULL,root TEXT NOT NULL,policy TEXT NOT NULL,
              start_time REAL NOT NULL,generation INTEGER NOT NULL,processed INTEGER NOT NULL DEFAULT 0,
              revision INTEGER NOT NULL DEFAULT 1,pending_count INTEGER NOT NULL DEFAULT 0,
              failed_count INTEGER NOT NULL DEFAULT 0,done_count INTEGER NOT NULL DEFAULT 0,
              archived_count INTEGER NOT NULL DEFAULT 0,last_sync REAL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS user_files(
              userid TEXT NOT NULL,file_id TEXT NOT NULL,path TEXT NOT NULL,version INTEGER NOT NULL,
              updated_at REAL NOT NULL,operation TEXT NOT NULL,kind TEXT NOT NULL,size INTEGER NOT NULL,
              mtime INTEGER NOT NULL,digest TEXT,synced_version INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'pending',error TEXT,confirmed_at REAL,confirmation TEXT,ip TEXT,
              PRIMARY KEY(userid,file_id));
            CREATE INDEX IF NOT EXISTS pending_files ON user_files(userid,file_id)
              WHERE status IN ('pending','failed');
            CREATE INDEX IF NOT EXISTS completed_files ON user_files(userid,confirmed_at)
              WHERE status='done';
            CREATE INDEX IF NOT EXISTS failed_files ON user_files(userid,file_id) WHERE status='failed';
            CREATE INDEX IF NOT EXISTS done_files ON user_files(userid,file_id) WHERE status='done';
            CREATE TABLE IF NOT EXISTS sync_completed(
              userid TEXT NOT NULL,file_id TEXT NOT NULL,version INTEGER NOT NULL,
              completed_at REAL NOT NULL,PRIMARY KEY(userid,file_id));
            CREATE TABLE IF NOT EXISTS sync_offers(
              userid TEXT NOT NULL,file_id TEXT NOT NULL,version INTEGER NOT NULL,digest TEXT,
              operation TEXT NOT NULL,kind TEXT NOT NULL,issued_at REAL NOT NULL,
              PRIMARY KEY(userid,file_id,version));
            CREATE INDEX IF NOT EXISTS offers_time ON sync_offers(issued_at);
            """)
            if db.execute("PRAGMA user_version").fetchone()[0] < 2:
                db.create_function("file_key", 2, file_id, deterministic=True)
                db.execute("""INSERT OR IGNORE INTO catalog SELECT file_key(f.root,f.path),f.root,f.path,
                  f.version,coalesce(c.created,?),'upsert',f.kind,f.size,f.mtime,f.digest
                  FROM files f LEFT JOIN changes c ON c.seq=f.version""", (time.time(),))
                db.execute("""INSERT OR IGNORE INTO catalog SELECT file_key(c.root,c.path),c.root,c.path,
                  c.seq,c.created,c.operation,c.kind,c.size,c.mtime,c.digest FROM changes c
                  WHERE c.operation='delete' AND c.seq=(SELECT max(c2.seq) FROM changes c2
                  WHERE c2.root=c.root AND c2.path=c.path)
                  AND NOT EXISTS(SELECT 1 FROM files f WHERE f.root=c.root AND f.path=c.path)""")
                db.execute("PRAGMA user_version=2")
            for table in ("deliveries", "rounds", "task_files", "tasks"):
                db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute("PRAGMA user_version=3")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self, *, readonly=False):
        """One reusable serialized writer, up to four independent WAL readers."""
        requested = time.monotonic()
        if readonly:
            with self._readers:
                while not self._idle_readers and self._reader_count >= 4 and not self._closed:
                    self._readers.wait()
                if self._closed:
                    raise RuntimeError(tr('数据库已关闭'))
                if self._idle_readers:
                    db = self._idle_readers.pop()
                else:
                    db = self._new_connection(readonly=True)
                    self._reader_count += 1
                self._active_readers += 1
            acquired = time.monotonic()
            try:
                with db:
                    db.execute("BEGIN")
                    yield db
            finally:
                with self._readers:
                    self._active_readers -= 1
                    self._idle_readers.append(db)
                    self._readers.notify_all()
                self._observe(True, acquired - requested, time.monotonic() - acquired)
        else:
            with self.lock:
                if self._closed:
                    raise RuntimeError(tr('数据库已关闭'))
                if self._writer is None:
                    self._writer = self._new_connection()
                acquired = time.monotonic()
                try:
                    with self._writer:
                        yield self._writer
                finally:
                    self._observe(False, acquired - requested, time.monotonic() - acquired)

    def _new_connection(self, readonly=False):
        database = self.path.resolve().as_uri() + "?mode=ro" if readonly else str(self.path)
        db = sqlite3.connect(database, timeout=10, check_same_thread=False, uri=readonly)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        if readonly:
            db.execute("PRAGMA query_only=ON")
        return db

    def _observe(self, readonly, wait, duration):
        with self._metrics_lock:
            prefix = "read" if readonly else "write"
            self._metrics[prefix + "_operations"] += 1
            self._metrics[prefix + "_wait_seconds"] += wait
            if not readonly:
                self._metrics["max_write_seconds"] = max(self._metrics["max_write_seconds"], duration)
                self._metrics["max_write_wait_seconds"] = max(self._metrics["max_write_wait_seconds"], wait)

    def metrics(self):
        with self._metrics_lock:
            return dict(self._metrics)

    def close(self):
        with self.lock, self._readers:
            self._closed = True
            self._readers.notify_all()
            while self._active_readers:
                self._readers.wait()
            for db in self._idle_readers:
                db.close()
            self._idle_readers.clear()
            if self._writer is not None:
                self._writer.close()
                self._writer = None

    def register(self, path: str):
        identifier = root_id(path)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO roots(id,path) VALUES(?,?)", (identifier, path))
            db.execute("UPDATE roots SET ready=0 WHERE id=?", (identifier,))
        return identifier

    @staticmethod
    def signature(info):
        return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino

    def inspect(self, path: Path, old, force=False):
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or (stat.S_ISREG(before.st_mode) and before.st_nlink > 1):
            return None
        if stat.S_ISDIR(before.st_mode):
            return ("directory", 0, before.st_mtime_ns, before.st_ctime_ns, before.st_ino, None, time.time())
        if not stat.S_ISREG(before.st_mode):
            return None
        signature = self.signature(before)
        if old and not force and signature == (old["size"], old["mtime"], old["ctime"], old["inode"]):
            return ("file", *signature, old["digest"], old["hash_at"])
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            if self.signature(os.fstat(stream.fileno())) != signature:
                raise OSError(tr('文件在摘要计算前发生变化'))
            while block := stream.read(1024 * 1024):
                if self.cancel.is_set():
                    raise InterruptedError(tr('扫描已停止'))
                digest.update(block)
            if self.signature(os.fstat(stream.fileno())) != signature:
                raise OSError(tr('文件仍在写入，等待下次扫描'))
        if self.signature(path.lstat()) != signature:
            raise OSError(tr('文件在摘要计算期间被替换'))
        return ("file", *signature, digest.hexdigest(), time.time())

    def record(self, db, identifier, path, data, generation):
        old = db.execute("SELECT * FROM files WHERE root=? AND path=?", (identifier, path)).fetchone()
        kind, size, mtime, ctime, inode, digest, hash_at = data
        changed = not old or (old["kind"], old["digest"]) != (kind, digest)
        if not old:
            db.execute("UPDATE roots SET entries=entries+1 WHERE id=?", (identifier,))
        version = old["version"] if old else 0
        if changed:
            published = time.time()
            version = db.execute("INSERT INTO changes(root,path,operation,kind,size,mtime,digest,created) VALUES(?,?,'upsert',?,?,?,?,?)",
                                 (identifier, path, kind, size, mtime, digest, published)).lastrowid
            db.execute("UPDATE roots SET head=? WHERE id=?", (version, identifier))
            self.publish(db, identifier, path, version, published, "upsert", kind, size, mtime, digest)
        db.execute("""INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(root,path) DO UPDATE SET kind=excluded.kind,size=excluded.size,mtime=excluded.mtime,
          ctime=excluded.ctime,inode=excluded.inode,digest=excluded.digest,version=excluded.version,
          hash_at=excluded.hash_at,seen=excluded.seen""",
                   (identifier, path, kind, size, mtime, ctime, inode, digest, version, hash_at, generation))

    @staticmethod
    def publish(db, root, path, version, timestamp, operation, kind, size, mtime, digest):
        db.execute("""INSERT INTO catalog VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(root,path) DO UPDATE SET
          version=excluded.version,updated_at=excluded.updated_at,operation=excluded.operation,
          kind=excluded.kind,size=excluded.size,mtime=excluded.mtime,digest=excluded.digest""",
                   (file_id(root, path), root, path, version, timestamp, operation, kind, size, mtime, digest))

    def scan(self, identifier: str, paths: list[str] | None = None, force=False):
        """Publish observed files in bounded batches; prune only a fully traversed scope."""
        import uuid
        with self.scan_lock:
            started = time.time()
            with self.connect(readonly=True) as db:
                root_row = db.execute("SELECT * FROM roots WHERE id=?", (identifier,)).fetchone()
            root = Path(root_row["path"])
            try:
                identity = root.stat()
                if not root.is_dir():
                    raise OSError(tr('共享根目录不可用'))
                prefixes = paths if paths is not None else ["/"]
                for prefix in prefixes:
                    if self.cancel.is_set():
                        raise InterruptedError(tr('扫描已停止'))
                    generation = uuid.uuid4().hex
                    target = root / prefix.lstrip("/")
                    # Never follow an ancestor link, including for watcher-driven updates.
                    for ancestor in (() if target == root else target.parents):
                        if ancestor == root:
                            break
                        if ancestor.is_symlink():
                            raise OSError(tr('扫描路径包含符号链接'))
                    pending = []
                    walker = self.walk(target)
                    reader = sqlite3.connect(self.path)
                    reader.row_factory = sqlite3.Row
                    try:
                        for local in walker:
                            if self.cancel.is_set():
                                raise InterruptedError(tr('扫描已停止'))
                            virtual = "/" + local.relative_to(root).as_posix() if local != root else "/"
                            old = reader.execute("SELECT * FROM files WHERE root=? AND path=?", (identifier, virtual)).fetchone()
                            data = self.inspect(local, old, force)
                            if data is None:
                                continue
                            if virtual != "/":
                                pending.append((virtual, data))
                            if len(pending) >= BATCH_SIZE:
                                with self.connect() as db:
                                    for virtual, data in pending:
                                        self.record(db, identifier, virtual, data, generation)
                                pending.clear()
                    finally:
                        walker.close()
                        reader.close()
                    with self.connect() as db:
                        for virtual, data in pending:
                            self.record(db, identifier, virtual, data, generation)
                    now_identity = root.stat()
                    if (identity.st_dev, identity.st_ino) != (now_identity.st_dev, now_identity.st_ino):
                        raise OSError(tr('共享根目录在扫描期间被替换'))
                    # Keyset chunks prevent a million-path deletion list in memory.
                    last = ""
                    while True:
                        with self.connect() as db:
                            rows = db.execute("""SELECT * FROM files WHERE root=? AND seen<>? AND path>?
                              AND path>=? AND path<?
                              AND (?='/' OR path=? OR substr(path,1,length(?)+1)=?||'/')
                              ORDER BY path LIMIT ?""", (identifier, generation, last, prefix, "0" if prefix == "/" else prefix + "0", prefix, prefix, prefix, prefix, BATCH_SIZE)).fetchall()
                            for row in rows:
                                published = time.time()
                                version = db.execute("INSERT INTO changes(root,path,operation,kind,size,mtime,digest,created) VALUES(?,?,'delete',?,0,0,NULL,?)",
                                                     (identifier, row["path"], row["kind"], published)).lastrowid
                                self.publish(db, identifier, row["path"], version, published, "delete", row["kind"], 0, 0, None)
                                db.execute("UPDATE roots SET head=? WHERE id=?", (version, identifier))
                                db.execute("DELETE FROM files WHERE root=? AND path=?", (identifier, row["path"]))
                                db.execute("UPDATE roots SET entries=entries-1 WHERE id=?", (identifier,))
                        if not rows:
                            break
                        last = rows[-1]["path"]
                with self.connect() as db:
                    if paths is None:
                        db.execute("UPDATE roots SET ready=1,last_scan=?,duration=?,error=NULL WHERE id=?", (time.time(), time.time() - started, identifier))
                    else:
                        db.execute("UPDATE roots SET error=NULL WHERE id=?", (identifier,))
                return {"ok": True, "seconds": time.time() - started}
            except OSError as exc:
                with self.connect() as db:
                    db.execute("UPDATE roots SET error=? WHERE id=?", (str(exc), identifier))
                return {"ok": False, "error": str(exc)}

    def walk(self, target):
        """Keep only one directory iterator per depth, not every path in a directory."""
        if not target.exists() and not target.is_symlink():
            with os.scandir(target.parent) as entries:
                next(entries, None)
            return
        yield target
        if target.is_symlink() or not target.is_dir():
            return
        stack = [os.scandir(target)]
        try:
            while stack:
                entry = next(stack[-1], None)
                if entry is None:
                    stack.pop().close()
                    continue
                local = Path(entry.path)
                yield local
                if entry.is_dir(follow_symlinks=False):
                    stack.append(os.scandir(local))
        finally:
            for iterator in stack:
                iterator.close()

    def audit(self, identifier, days):
        with self.connect(readonly=True) as db:
            import math
            entries = db.execute("SELECT entries FROM roots WHERE id=?", (identifier,)).fetchone()[0]
            limit = max(32, min(512, math.ceil(entries / (days * 1440))))
            paths = [row[0] for row in db.execute("SELECT path FROM files WHERE root=? AND kind='file' AND hash_at<? ORDER BY hash_at LIMIT ?",
                                                (identifier, time.time() - days * 86400, limit))]
        if paths:
            self.scan(identifier, paths, force=True)

    def prune(self, days):
        cutoff = time.time() - days * 86400
        deadline = time.monotonic() + 1
        while not self.cancel.is_set() and time.monotonic() < deadline:
            with self.connect() as db:
                rows = db.execute("SELECT seq,root FROM changes WHERE created<? ORDER BY created LIMIT ?", (cutoff, BATCH_SIZE)).fetchall()
                for row in rows:
                    db.execute("UPDATE roots SET floor=max(floor,?) WHERE id=?", (row["seq"], row["root"]))
                db.executemany("DELETE FROM changes WHERE seq=?", [(row["seq"],) for row in rows])
            if len(rows) < BATCH_SIZE:
                break

    def summary(self):
        with self.connect(readonly=True) as db:
            roots = [dict(row) for row in db.execute("SELECT roots.*,entries AS files FROM roots")]
            users = [dict(row) for row in db.execute("SELECT * FROM sync_users ORDER BY username LIMIT 500")]
        return {"roots": roots, "users": users, "database": self.metrics()}


def file_id(root: str, path: str) -> str:
    return hashlib.sha256((root + "\0" + path).encode("utf-8", "surrogatepass")).hexdigest()
