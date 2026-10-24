"""Protocol v2: one durable incremental queue per authenticated userid."""
from __future__ import annotations


import base64
import fnmatch
import hashlib
import hmac
import json
import os
import secrets
import time
import threading
from functools import wraps

from .i18n import tr
from .config import User, policy_path
from .index import BATCH_SIZE, IndexStore, root_id, file_id

NAMESPACE = "simple-sync.invalid"
OPERATIONS = ("info", "list", "ack")


class SyncError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def within(path, scope):
    path, scope = policy_path(path), policy_path(scope)
    return scope == "/" or path == scope or path.startswith(scope + "/")


def eligible(user, row):
    path = row["path"]
    if not any(within(path, scope) for scope in user.sync_directories):
        return False
    value = policy_path(path.lstrip("/"))
    basename = value.rsplit("/", 1)[-1]
    def matches(pattern):
        pattern = policy_path(pattern.lstrip("/"))
        return (fnmatch.fnmatchcase(value, pattern) or fnmatch.fnmatchcase(basename, pattern)
                or (pattern.endswith("/**") and within(path, "/" + pattern[:-3])))
    if any(matches(pattern) for pattern in user.exclude_patterns):
        return False
    if row["kind"] == "file":
        return user.allows(path, "download") and any(matches(pattern) for pattern in user.include_patterns)
    return user.allows(path, "list") or user.allows(path, "download")


def wire_item(row):
    return {"file_id": row["file_id"], "path": row["path"], "version": str(row["version"]),
            "updated_at": row["updated_at"], "operation": row["operation"], "kind": row["kind"],
            "size_bytes": str(row["size"]), "sha256": row["digest"],
            "status": row["status"], "error": row["error"]}


def user_serialized(method):
    @wraps(method)
    def wrapped(self, user, *args, **kwargs):
        current = getattr(self, "current_users", None)
        if current is not None and current.get(user.userid) != user:
            raise SyncError("USER_SETTINGS_CHANGED", tr('用户设置已变化，请重新登录'))
        with self._locks_guard:
            lock = self._user_locks.setdefault(user.userid, threading.RLock())
        with lock:
            if current is not None and getattr(self, "current_users").get(user.userid) != user:
                raise SyncError("USER_SETTINGS_CHANGED", tr('用户设置已变化，请重新登录'))
            return method(self, user, *args, **kwargs)
    return wrapped


class SyncAPI:
    def __init__(self, store: IndexStore):
        self.store = store
        self._locks_guard = threading.Lock()
        self._user_locks = {}
        self._offers_cleanup_after = (0.0, 0)
        secret_path = store.directory / "sync_token_key"
        if not secret_path.exists():
            fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32))
        secret_path.chmod(0o600)
        self.secret = secret_path.read_bytes()

    def token(self, data):
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw + hmac.digest(self.secret, raw, "sha256")).decode()

    def untoken(self, value):
        try:
            if not isinstance(value, str) or len(value) > 16384:
                raise ValueError()
            decoded = base64.b64decode(value, altchars=b"-_", validate=True)
            raw, signature = decoded[:-32], decoded[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self.secret, raw, "sha256")):
                raise ValueError()
            token = json.loads(raw)
            if not isinstance(token, dict):
                raise ValueError()
            return token
        except (ValueError, TypeError):
            raise SyncError("BAD_TOKEN", tr('无效分页令牌')) from None

    @staticmethod
    def policy(user):
        raw = [user.root, user.permissions, [(r.path, r.permissions) for r in user.rules],
               user.sync_directories, user.include_patterns, user.exclude_patterns]
        return hashlib.sha256(json.dumps(raw, ensure_ascii=True).encode()).hexdigest()

    @staticmethod
    def counts(db, userid, old, new):
        def pending(status):
            return int(status in ("pending", "failed"))
        db.execute("""UPDATE sync_users SET pending_count=pending_count+?,failed_count=failed_count+?,
          done_count=done_count+? WHERE userid=?""",
                   (pending(new) - pending(old), int(new == "failed") - int(old == "failed"),
                    int(new == "done") - int(old == "done"), userid))

    @user_serialized
    def ensure_user(self, user):
        policy, identifier = self.policy(user), root_id(user.root)
        with self.store.connect(readonly=True) as db:
            state = db.execute("SELECT * FROM sync_users WHERE userid=?", (user.userid,)).fetchone()
        if state is not None and (state["policy"], state["start_time"], state["generation"], state["username"]) == (policy, user.sync_start, user.sync_generation, user.username):
            return dict(state)
        if state is None:
            with self.store.connect() as db:
                db.execute("""INSERT INTO sync_users(userid,username,root,policy,start_time,generation,created)
                  VALUES(?,?,?,?,?,?,?)""", (user.userid, user.username, identifier, policy, user.sync_start,
                                             user.sync_generation, time.time()))
        elif state["policy"] != policy or state["start_time"] != user.sync_start or state["generation"] != user.sync_generation:
            discard = state["generation"] != user.sync_generation or state["root"] != identifier
            # The old policy remains persisted until all pages finish. An interrupted
            # settings reconciliation is safe to repeat on the next startup.
            if discard or state["policy"] != policy:
                after = ""
                while True:
                    with self.store.connect() as db:
                        rows = db.execute("SELECT * FROM user_files WHERE userid=? AND file_id>? ORDER BY file_id LIMIT ?", (user.userid, after, BATCH_SIZE)).fetchall()
                        for row in rows:
                            if discard or not eligible(user, row):
                                self.counts(db, user.userid, row["status"], None)
                                db.execute("DELETE FROM user_files WHERE userid=? AND file_id=?", (user.userid, row["file_id"]))
                    if not rows:
                        break
                    after = rows[-1]["file_id"]
                after = ""
                while True:
                    with self.store.connect() as db:
                        rows = db.execute("""SELECT s.file_id,c.path,c.kind FROM sync_completed s
                          LEFT JOIN catalog c ON s.file_id=c.file_id
                          WHERE s.userid=? AND s.file_id>? ORDER BY s.file_id LIMIT ?""", (user.userid, after, BATCH_SIZE)).fetchall()
                        for row in rows:
                            if discard or row["path"] is None or not eligible(user, row):
                                db.execute("DELETE FROM sync_completed WHERE userid=? AND file_id=?", (user.userid, row["file_id"]))
                                db.execute("UPDATE sync_users SET archived_count=archived_count-1 WHERE userid=?", (user.userid,))
                    if not rows:
                        break
                    after = rows[-1]["file_id"]
            while True:
                with self.store.connect() as db:
                    rows = db.execute("SELECT rowid FROM sync_offers WHERE userid=? LIMIT ?", (user.userid, BATCH_SIZE)).fetchall()
                    db.executemany("DELETE FROM sync_offers WHERE rowid=?", [(row[0],) for row in rows])
                if len(rows) < BATCH_SIZE:
                    break
            with self.store.connect() as db:
                db.execute("""UPDATE sync_users SET root=?,policy=?,start_time=?,generation=?,processed=0,
                  revision=revision+1 WHERE userid=?""", (identifier, policy, user.sync_start, user.sync_generation, user.userid))
        with self.store.connect() as db:
            db.execute("UPDATE sync_users SET username=? WHERE userid=?", (user.username, user.userid))
            return dict(db.execute("SELECT * FROM sync_users WHERE userid=?", (user.userid,)).fetchone())

    @user_serialized
    def refresh(self, user):
        self.ensure_user(user)
        with self.store.connect(readonly=True) as db:
            root = db.execute("SELECT * FROM roots WHERE id=?", (root_id(user.root),)).fetchone()
            if not root or not root["ready"]:
                raise SyncError("INDEX_NOT_READY", tr('首次目录索引尚未完成，请稍后重试'))
            upper = root["head"]
        while True:
            if self.store.cancel.is_set():
                raise SyncError("SERVER_STOPPING", tr('服务正在停止'))
            with self.store.connect() as db:
                state = db.execute("SELECT * FROM sync_users WHERE userid=?", (user.userid,)).fetchone()
                rows = db.execute("""SELECT * FROM catalog WHERE root=? AND version>? AND version<=?
                  AND updated_at>=? ORDER BY version LIMIT ?""",
                                  (state["root"], state["processed"], upper, state["start_time"], BATCH_SIZE)).fetchall()
                for row in rows:
                    if not eligible(user, row):
                        continue
                    old = db.execute("SELECT * FROM user_files WHERE userid=? AND file_id=?", (user.userid, row["file_id"])).fetchone()
                    if old and old["version"] >= row["version"]:
                        continue
                    archived = db.execute("SELECT * FROM sync_completed WHERE userid=? AND file_id=?", (user.userid, row["file_id"])).fetchone()
                    synced = old["synced_version"] if old else archived["version"] if archived else 0
                    if archived:
                        if synced >= row["version"]:
                            continue
                        db.execute("DELETE FROM sync_completed WHERE userid=? AND file_id=?", (user.userid, row["file_id"]))
                        db.execute("UPDATE sync_users SET archived_count=archived_count-1 WHERE userid=?", (user.userid,))
                    status = "done" if synced >= row["version"] else "pending"
                    self.counts(db, user.userid, old["status"] if old else None, status)
                    db.execute("""INSERT INTO user_files(userid,file_id,path,version,updated_at,operation,kind,size,mtime,digest,synced_version,status)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(userid,file_id) DO UPDATE SET
                      path=excluded.path,version=excluded.version,updated_at=excluded.updated_at,operation=excluded.operation,
                      kind=excluded.kind,size=excluded.size,mtime=excluded.mtime,digest=excluded.digest,status=excluded.status,error=NULL""",
                               (user.userid, row["file_id"], row["path"], row["version"], row["updated_at"], row["operation"], row["kind"], row["size"], row["mtime"], row["digest"], synced, status))
                position = rows[-1]["version"] if len(rows) == BATCH_SIZE else upper
                db.execute("UPDATE sync_users SET processed=max(processed,?) WHERE userid=?", (position, user.userid))
                if len(rows) < BATCH_SIZE:
                    return dict(db.execute("SELECT * FROM sync_users WHERE userid=?", (user.userid,)).fetchone())

    @user_serialized
    def dispatch(self, user: User, operation: str, args: dict, ip=""):
        if not isinstance(args, dict):
            raise SyncError("BAD_REQUEST", tr('请求必须为 JSON 对象'))
        if operation == "info":
            return {"protocol": "2", "namespace": NAMESPACE, "userid": user.userid,
                    "hash": "sha256", "confirmation": user.confirmation, "sync_enabled": user.sync_enabled,
                    "incremental_start": user.sync_start, "directories": user.sync_directories,
                    "max_page_size": 200, "operations": list(OPERATIONS), "server_time": time.time()}
        if args.get("userid") != user.userid:
            raise SyncError("USER_MISMATCH", tr('userid 与当前登录账号不匹配'))
        if not user.sync_enabled:
            raise SyncError("SYNC_DISABLED", tr('该用户未启用增量同步'))
        self.refresh(user)
        if operation == "list":
            return self.list_files(user, args)
        if operation == "ack":
            return self.ack(user, args, ip)
        raise SyncError("BAD_REQUEST", tr('未知同步操作'))

    def list_files(self, user, args):
        limit = args.get("limit", 100)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise SyncError("BAD_REQUEST", tr('分页大小须为 1–200'))
        with self.store.connect() as db:
            state = db.execute("SELECT * FROM sync_users WHERE userid=?", (user.userid,)).fetchone()
            upper, after = state["processed"], ""
            if args.get("page_token"):
                token = self.untoken(args["page_token"])
                if token.get("userid") != user.userid or token.get("revision") != state["revision"]:
                    raise SyncError("BAD_TOKEN", tr('用户或同步设置已变化，请从第一页重新查询'))
                upper, after = token["upper"], token["after"]
            identifiers = args.get("file_ids")
            extra = ""
            parameters = []
            if identifiers is not None:
                if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 200 or not all(isinstance(value, str) and len(value) == 64 for value in identifiers) or args.get("page_token"):
                    raise SyncError("BAD_REQUEST", tr('file_ids 须包含 1–200 个标识，不能同时使用分页令牌'))
                extra = " AND file_id IN (" + ",".join("?" for _ in identifiers) + ")"
                parameters = identifiers
            rows = db.execute("""SELECT * FROM user_files WHERE userid=? AND status IN ('pending','failed')
              AND file_id>? AND version<=?""" + extra + " ORDER BY file_id LIMIT ?", (user.userid, after, upper, *parameters, limit + 1)).fetchall()
            more, rows = len(rows) > limit, rows[:limit]
            for row in rows:
                self.offer(db, user.userid, row)
            token = self.token({"userid": user.userid, "revision": state["revision"], "upper": upper,
                                "after": rows[-1]["file_id"]}) if more else None
            return {"userid": user.userid, "server_time": time.time(), "processed": str(state["processed"]),
                    "pending": state["pending_count"], "failed": state["failed_count"],
                    "synced": state["done_count"] + state["archived_count"],
                    "next_page_token": token, "has_more": more, "files": [wire_item(row) for row in rows]}

    @staticmethod
    def offer(db, userid, row):
        db.execute("INSERT OR IGNORE INTO sync_offers VALUES(?,?,?,?,?,?,?)", (userid, row["file_id"], row["version"], row["digest"], row["operation"], row["kind"], time.time()))

    def confirm(self, db, user, item, mode, ip):
        identifier, version = item.get("file_id"), item.get("version")
        if not isinstance(identifier, str) or not isinstance(version, str) or not version.isascii() or not version.isdigit() or len(version) > 18:
            raise SyncError("BAD_REQUEST", tr('file_id 和版本号格式不正确'))
        version = int(version)
        row = db.execute("SELECT * FROM user_files WHERE userid=? AND file_id=?", (user.userid, identifier)).fetchone()
        offer = db.execute("SELECT * FROM sync_offers WHERE userid=? AND file_id=? AND version=?", (user.userid, identifier, version)).fetchone()
        if row and row["synced_version"] >= version:
            return
        if not row or not offer:
            archived = db.execute("SELECT version FROM sync_completed WHERE userid=? AND file_id=?", (user.userid, identifier)).fetchone()
            if archived and archived["version"] >= version:
                return
            raise SyncError("VERSION_MISMATCH", tr('未提供此文件版本，请重新查询'))
        status = item.get("status")
        if status == "success":
            if offer["kind"] == "file" and offer["operation"] == "upsert" and item.get("sha256") != offer["digest"]:
                raise SyncError("CHECKSUM_MISMATCH", tr('摘要与该文件版本不一致'))
            current = "done" if version >= row["version"] else row["status"]
            self.counts(db, user.userid, row["status"], current)
            now = time.time()
            db.execute("""UPDATE user_files SET synced_version=max(synced_version,?),status=?,error=CASE WHEN version=? THEN NULL ELSE error END,
              confirmed_at=?,confirmation=?,ip=? WHERE userid=? AND file_id=?""", (version, current, version, now, mode, ip, user.userid, identifier))
            db.execute("UPDATE sync_users SET last_sync=? WHERE userid=?", (now, user.userid))
        elif status == "failed":
            if version == row["version"]:
                self.counts(db, user.userid, row["status"], "failed")
                db.execute("UPDATE user_files SET status='failed',error=? WHERE userid=? AND file_id=?", (str(item.get("error", tr('下载失败')))[:500], user.userid, identifier))
        else:
            raise SyncError("BAD_REQUEST", tr('状态须为 success 或 failed'))

    def ack(self, user, args, ip):
        items = args.get("files")
        if not isinstance(items, list) or not 1 <= len(items) <= 200 or not all(isinstance(item, dict) for item in items):
            raise SyncError("BAD_REQUEST", tr('每批须确认 1–200 个文件'))
        with self.store.connect() as db:
            for item in items:
                self.confirm(db, user, item, "manual", ip)
            state = db.execute("SELECT * FROM sync_users WHERE userid=?", (user.userid,)).fetchone()
            return {"userid": user.userid, "complete": state["pending_count"] == 0,
                    "pending": state["pending_count"], "failed": state["failed_count"],
                    "synced": state["done_count"] + state["archived_count"], "server_time": time.time()}

    @user_serialized
    def auto_candidate(self, user, path, signature):
        if not user.sync_enabled or user.confirmation != "auto":
            return None
        try:
            self.refresh(user)
        except SyncError:
            return None
        with self.store.connect() as db:
            row = db.execute("""SELECT u.* FROM user_files u JOIN files f ON f.root=? AND f.path=u.path
              AND f.version=u.version WHERE u.userid=? AND u.file_id=? AND u.operation='upsert'
              AND u.kind='file' AND u.status IN ('pending','failed')
              AND f.size=? AND f.mtime=? AND f.ctime=? AND f.inode=?""",
                             (root_id(user.root), user.userid, file_id(root_id(user.root), path), *signature)).fetchone()
            if not row:
                return None
            self.offer(db, user.userid, row)
            return wire_item(row)

    @user_serialized
    def auto_confirm(self, user, item, ip):
        self.refresh(user)
        with self.store.connect() as db:
            self.confirm(db, user, {**item, "status": "success"}, "auto", ip)

    def details(self, userid, status="pending", after="", limit=200):
        if status not in ("pending", "failed", "done"):
            raise ValueError(tr('未知状态'))
        with self.store.connect(readonly=True) as db:
            if status == "pending":
                rows = db.execute("SELECT * FROM user_files WHERE userid=? AND status IN ('pending','failed') AND file_id>? ORDER BY file_id LIMIT ?", (userid, after, limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM user_files WHERE userid=? AND status=? AND file_id>? ORDER BY file_id LIMIT ?", (userid, status, after, limit)).fetchall()
            return [dict(row) for row in rows]

    def cleanup(self, days):
        cutoff = time.time() - days * 86400
        deadline = time.monotonic() + 5
        with self.store.connect(readonly=True) as db:
            users = [row[0] for row in db.execute("SELECT userid FROM sync_users")]
        for userid in users:
            while time.monotonic() < deadline and not self.store.cancel.is_set():
                with self.store.connect() as db:
                    rows = db.execute("SELECT * FROM user_files WHERE userid=? AND status='done' AND confirmed_at<? LIMIT ?", (userid, cutoff, BATCH_SIZE)).fetchall()
                    for row in rows:
                        db.execute("INSERT OR REPLACE INTO sync_completed VALUES(?,?,?,?)", (row["userid"], row["file_id"], row["synced_version"], row["confirmed_at"]))
                        self.counts(db, row["userid"], "done", None)
                        db.execute("UPDATE sync_users SET archived_count=archived_count+1 WHERE userid=?", (row["userid"],))
                        db.execute("DELETE FROM user_files WHERE userid=? AND file_id=?", (row["userid"], row["file_id"]))
                if len(rows) < BATCH_SIZE:
                    break
        # Expiry is bounded too; pending offers remain valid until superseded.
        while time.monotonic() < deadline and not self.store.cancel.is_set():
            with self.store.connect() as db:
                rows = db.execute("""SELECT rowid AS offer_id,* FROM sync_offers WHERE
                  (issued_at,rowid)>(?,?) AND issued_at<? ORDER BY issued_at,rowid LIMIT ?""",
                                  (*self._offers_cleanup_after, cutoff, BATCH_SIZE)).fetchall()
                for row in rows:
                    active = db.execute("SELECT version,synced_version FROM user_files WHERE userid=? AND file_id=?", (row["userid"], row["file_id"])).fetchone()
                    done = db.execute("SELECT version FROM sync_completed WHERE userid=? AND file_id=?", (row["userid"], row["file_id"])).fetchone()
                    if ((active and (active["version"] > row["version"] or active["synced_version"] >= row["version"]))
                            or (done and done["version"] >= row["version"])):
                        db.execute("DELETE FROM sync_offers WHERE rowid=?", (row["offer_id"],))
            if rows:
                self._offers_cleanup_after = (rows[-1]["issued_at"], rows[-1]["offer_id"])
            if len(rows) < BATCH_SIZE:
                self._offers_cleanup_after = (0.0, 0)
                break
