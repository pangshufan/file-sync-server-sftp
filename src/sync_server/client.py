"""Async Python SDK and single-direction pull CLI."""
from __future__ import annotations


import argparse
import asyncio
import getpass
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path, PurePosixPath

import asyncssh

from .i18n import tr
from .sync_api import SyncError
from .transport import request, server_identity


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class PullClient:
    """One local destination for a server-managed userid. No client-side scope or reset."""
    def __init__(self, sftp, destination: Path, state_dir: Path, *, userid=None, delete=False, report=print):
        self.sftp = sftp
        self.userid = userid
        self.destination = Path(destination).expanduser().resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        if self.state_dir == self.destination or self.state_dir.is_relative_to(self.destination):
            raise ValueError(tr('客户端状态目录必须位于同步目标目录之外'))
        self.destination.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        self.delete = delete
        self.verify_local = False
        self.report = report
        database = self.state_dir / "client.sqlite3"
        if database.exists():
            with sqlite3.connect(database) as existing:
                try:
                    protocol = existing.execute("SELECT value FROM settings WHERE key='protocol'").fetchone()
                except sqlite3.OperationalError:
                    protocol = None
                if not protocol or protocol[0] != "2":
                    with sqlite3.connect(self.state_dir / f"client-v1-backup-{time.time_ns()}.sqlite3") as saved:
                        existing.backup(saved)
        self.db = sqlite3.connect(self.state_dir / "client.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,version TEXT NOT NULL,digest TEXT,
            size INTEGER NOT NULL,mtime INTEGER NOT NULL,status TEXT NOT NULL,error TEXT);
          CREATE TABLE IF NOT EXISTS work(path TEXT PRIMARY KEY,payload TEXT NOT NULL,
            priority INTEGER NOT NULL,depth INTEGER NOT NULL);
          CREATE INDEX IF NOT EXISTS reusable_content ON files(digest,size) WHERE status='done';
        """)
        existing = self.db.execute("SELECT value FROM settings WHERE key='destination'").fetchone()
        if existing and existing[0] != str(self.destination):
            self.db.close()
            raise ValueError(tr('状态目录已经绑定其他同步目标，请使用独立状态目录'))
        self.db.execute("INSERT OR IGNORE INTO settings VALUES('destination',?)", (str(self.destination),))
        self.db.execute("INSERT OR REPLACE INTO settings VALUES('protocol','2')")
        self.db.commit()
        (self.state_dir / "client.sqlite3").chmod(0o600)

    def close(self):
        self.db.close()

    def local_path(self, value):
        if not isinstance(value, str) or not value.startswith("/") or "\\" in value or "\x00" in value:
            raise ValueError(tr('服务器返回了非法路径'))
        if any(part in (".", "..") for part in value.split("/")):
            raise ValueError(tr('服务器返回了越界路径'))
        relative = PurePosixPath(value).parts[1:]
        local = self.destination
        for component in relative:
            local = local / component
            if local.is_symlink():
                raise ValueError(tr('本地目标路径包含符号链接'))
        if not local.resolve().is_relative_to(self.destination):
            raise ValueError(tr('本地目标超出同步目录'))
        return local

    async def apply(self, entry):
        path = self.local_path(entry["path"])
        if path == self.destination:
            raise ValueError(tr('服务器不能替换本地同步根目录'))
        if entry["operation"] == "delete":
            if self.delete and path.exists():
                if path.is_dir():
                    path.rmdir()  # Never recursively remove untracked local content.
                else:
                    path.unlink()
            self.db.execute("DELETE FROM files WHERE path=?", (entry["path"],))
            self.db.commit()
            return
        if entry["kind"] == "directory":
            if path.is_file():
                path.unlink()
            path.mkdir(parents=True, exist_ok=True)
            return
        size = int(entry["size_bytes"])
        old = self.db.execute("SELECT * FROM files WHERE path=?", (entry["path"],)).fetchone()
        if path.is_file():
            info = path.stat()
            if not self.verify_local and old and old["status"] == "done" and old["digest"] == entry["sha256"] and info.st_size == old["size"] and info.st_mtime_ns == old["mtime"]:
                return
            if info.st_size == size and await asyncio.to_thread(file_digest, path) == entry["sha256"]:
                self.remember(entry, path)
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256((entry["path"] + ":" + entry["version"]).encode()).hexdigest()
        temporary = self.state_dir / (key + ".part")
        # State is outside the target: failed/interrupted transfers can resume safely.
        if temporary.is_symlink():
            raise ValueError(tr('下载临时文件不能是符号链接'))
        await self.stage_reuse(entry)
        offset = temporary.stat().st_size if temporary.exists() else 0
        if offset > size:
            temporary.unlink()
            offset = 0
        if offset < size or not temporary.exists():
            async with self.sftp.open(entry["path"], "rb") as remote:
                await remote.seek(offset)
                with temporary.open("ab") as output:
                    while block := await remote.read(256 * 1024):
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
        temporary.chmod(0o600)
        if temporary.stat().st_size != size or await asyncio.to_thread(file_digest, temporary) != entry["sha256"]:
            temporary.unlink(missing_ok=True)
            raise SyncError("CONTENT_CHANGED", tr('文件内容与清单版本不一致，将刷新并重试'))
        # Copy to the destination filesystem before atomic replace (state may be on another disk).
        import shutil
        import tempfile
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".simple-sync-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as output, temporary.open("rb") as source:
                await asyncio.to_thread(shutil.copyfileobj, source, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            self.local_path(entry["path"])
            if path.is_dir():
                path.rmdir()
            os.replace(name, path)
            temporary.unlink(missing_ok=True)
            self.remember(entry, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def remember(self, entry, path):
        info = path.stat()
        self.db.execute("""INSERT INTO files VALUES(?,?,?,?,?,'done',NULL) ON CONFLICT(path) DO UPDATE SET
          version=excluded.version,digest=excluded.digest,size=excluded.size,mtime=excluded.mtime,status='done',error=NULL""",
                        (entry["path"], entry["version"], entry["sha256"], info.st_size, info.st_mtime_ns))
        self.db.commit()

    async def stage_reuse(self, entry):
        """Verify and stage a local copy before old paths can be deleted."""
        if entry["operation"] != "upsert" or entry["kind"] != "file":
            return
        try:
            if self.local_path(entry["path"]).exists():
                return
        except ValueError:
            return
        size = int(entry["size_bytes"])
        key = hashlib.sha256((entry["path"] + ":" + entry["version"]).encode()).hexdigest()
        temporary = self.state_dir / (key + ".part")
        if temporary.exists() or temporary.is_symlink():
            return
        candidates = self.db.execute("SELECT path FROM files WHERE digest=? AND size=? AND status='done' AND path<>? LIMIT 16",
                                     (entry["sha256"], size, entry["path"])).fetchall()
        import shutil
        import tempfile
        for candidate in candidates:
            try:
                source = self.local_path(candidate["path"])
                if not source.is_file() or source.stat().st_size != size:
                    continue
                if await asyncio.to_thread(file_digest, source) != entry["sha256"]:
                    continue
                fd, name = tempfile.mkstemp(dir=self.state_dir, suffix=".reuse")
                os.close(fd)
                try:
                    await asyncio.to_thread(shutil.copyfile, source, name)
                    if Path(name).stat().st_size == size and await asyncio.to_thread(file_digest, name) == entry["sha256"]:
                        os.replace(name, temporary)
                        return
                finally:
                    Path(name).unlink(missing_ok=True)
            except (OSError, ValueError):
                # Cache reuse is optional; ordinary download remains available.
                continue

    async def run_once(self, *, page_size=100):
        capabilities = await request(self.sftp, "info", {})
        if capabilities.get("protocol") != "2" or capabilities.get("hash") != "sha256":
            raise ValueError(tr('服务器增量协议不是 v2，请升级客户端'))
        userid = self.userid or capabilities["userid"]
        if userid != capabilities["userid"]:
            raise ValueError(tr('userid 与当前登录账号不匹配'))
        identity = json.dumps([*server_identity(self.sftp), userid])
        binding = self.db.execute("SELECT value FROM settings WHERE key='v2binding'").fetchone()
        if binding and binding[0] != identity:
            raise ValueError(tr('状态目录已经绑定其他服务器或 userid'))
        self.db.execute("INSERT OR IGNORE INTO settings VALUES('v2binding',?)", (identity,))
        self.db.execute("DELETE FROM work")
        self.db.commit()
        token = None
        success = failures = 0
        while True:
            page = await request(self.sftp, "list", {"userid": userid, "limit": page_size, "page_token": token})
            for entry in page["files"]:
                deleting = entry["operation"] == "delete"
                directory = entry["kind"] == "directory"
                priority = (1 if directory else 0) if deleting else (2 if directory else 3)
                depth = entry["path"].count("/") * (-1 if deleting else 1)
                self.db.execute("INSERT OR REPLACE INTO work VALUES(?,?,?,?)", (entry["path"], json.dumps(entry), priority, depth))
            self.db.commit()
            if not page["has_more"]:
                break
            token = page["next_page_token"]
        acknowledgements = []
        # Preserve reusable content before rename-as-delete/upsert removes sources.
        for row in self.db.execute("SELECT payload FROM work WHERE priority=3"):
            await self.stage_reuse(json.loads(row[0]))
        async def flush():
            if acknowledgements:
                await request(self.sftp, "ack", {"userid": userid, "files": acknowledgements})
                acknowledgements.clear()
        for row in self.db.execute("SELECT payload FROM work ORDER BY priority,depth,path"):
            entry = json.loads(row[0])
            error = None
            for attempt in range(3):
                try:
                    if attempt or entry["status"] == "failed":
                        latest = await request(self.sftp, "list", {"userid": userid, "file_ids": [entry["file_id"]]})
                        if latest["files"]:
                            entry = latest["files"][0]
                    await self.apply(entry)
                    error = None
                    break
                except (OSError, asyncssh.SFTPError, SyncError, ValueError) as exc:
                    error = str(exc)
                    if attempt < 2:
                        await asyncio.sleep(0.2 * (attempt + 1))
            acknowledgement = {"file_id": entry["file_id"], "version": entry["version"],
                               "status": "failed" if error else "success", "sha256": entry["sha256"], "error": error}
            if acknowledgements and len(json.dumps([*acknowledgements, acknowledgement]).encode()) > 48 * 1024:
                await flush()
            acknowledgements.append(acknowledgement)
            if len(acknowledgements) >= 50:
                await flush()
            if error:
                failures += 1
                self.report(tr('失败 {value0}：{value1}', value0=entry['path'], value1=error))
            else:
                success += 1
                self.report(tr('完成 {value0} {value1}', value0=entry['operation'], value1=entry['path']))
        await flush()
        current = await request(self.sftp, "list", {"userid": userid, "limit": 1})
        return {"userid": userid, "complete": current["pending"] == 0, "pending": current["pending"],
                "succeeded": success, "failed": failures}


async def run_cli(args):
    key_mode = args.identity or args.agent
    password = None if key_mode else getpass.getpass(tr('SFTP 密码: '))
    client_keys = [str(Path(args.identity).expanduser())] if args.identity else []
    destination = Path(args.destination).expanduser().resolve()
    state = Path(args.state).expanduser() if args.state else destination.parent / (".simple-sync-" + hashlib.sha256(str(destination).encode()).hexdigest()[:16])
    while True:
        try:
            async with asyncssh.connect(args.host, args.port, username=args.username, password=password,
                                        known_hosts=args.known_hosts, client_keys=client_keys,
                                        agent_path=() if args.agent else None,
                                        connect_timeout=20, keepalive_interval=30, keepalive_count_max=3) as conn:
                async with conn.start_sftp_client() as sftp:
                    client = PullClient(sftp, destination, state, userid=args.userid, delete=args.delete)
                    try:
                        while True:
                            try:
                                result = await client.run_once()
                                print(json.dumps(result, ensure_ascii=False))
                                if not args.interval:
                                    return 0 if result["complete"] else 1
                            except SyncError as exc:
                                if not args.interval or exc.code not in ("INDEX_NOT_READY",):
                                    raise
                                print(f"{exc.code}: {exc}")
                            await asyncio.sleep(args.interval)
                    finally:
                        client.close()
        except (asyncssh.HostKeyNotVerifiable, asyncssh.PermissionDenied):
            raise
        except (OSError, asyncssh.ConnectionLost, asyncssh.DisconnectError) as exc:
            if not args.interval:
                raise
            print(tr('连接中断，{value0} 秒后重连：{value1}', value0=args.interval, value1=exc))
            await asyncio.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description=tr('从 Simple Sync SFTP 服务端增量下载；默认保留本地多余文件'))
    parser.add_argument("host")
    parser.add_argument("destination")
    parser.add_argument("--username", required=True)
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--known-hosts", default=str(Path.home() / ".ssh/known_hosts"))
    parser.add_argument("--state", help=tr('目标目录之外的独立状态目录'))
    parser.add_argument("--userid", help=tr('可选；必须匹配当前登录账号，否则从 info 自动获取'))
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--identity", help=tr('无口令 SSH 私钥文件；加密私钥请通过 Agent 使用'))
    auth.add_argument("--agent", action="store_true", help=tr('使用 SSH Agent'))
    parser.add_argument("--batch", action="store_true", help=tr('禁止交互；需指定 identity 或 agent'))
    parser.add_argument("--delete", action="store_true", help=tr('传播服务器删除；仅删除对应文件或空目录'))
    parser.add_argument("--interval", type=int, default=0, help=tr('查询间隔秒数；0 表示执行一轮后退出'))
    args = parser.parse_args()
    if args.batch and not (args.identity or args.agent):
        parser.error(tr('batch 模式必须指定 --identity 或 --agent'))
    if args.interval < 0:
        parser.error(tr('查询间隔不能小于 0'))
    try:
        return asyncio.run(run_cli(args))
    except KeyboardInterrupt:
        return 130
    except (OSError, asyncssh.Error, SyncError, ValueError) as exc:
        parser.exit(1, tr('同步失败: {value0}\n', value0=exc))


if __name__ == "__main__":
    raise SystemExit(main())
