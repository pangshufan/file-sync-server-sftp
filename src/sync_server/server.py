from __future__ import annotations


import asyncio
import copy
import os
import posixpath
import secrets
from datetime import datetime
from pathlib import Path
from typing import Callable

import asyncssh

from .i18n import tr
from .config import Config, ConfigStore, User, policy_path, verify_password
from .auth_limits import AuthLimits
from .audit import AuditLog
from .process_lock import ProcessLock
from .file_state import descriptor_signature


class RestrictedSFTP(asyncssh.SFTPServer):
    """All filesystem access goes through map_path; links are deliberately disabled."""

    def __init__(self, channel, user: User, emit: Callable, sync_api=None):
        self.user = user
        self.emit = emit
        self.sync_api = sync_api
        self.root = Path(user.root).resolve()
        self.downloads = {}
        self.normal_closes = set()
        super().__init__(channel, chroot=os.fsencode(self.root))

    def normalized(self, path: bytes) -> str:
        value = os.fsdecode(path)
        if "\x00" in value or "\\" in value:
            raise asyncssh.SFTPPermissionDenied(tr('禁止越界路径'))
        parts = []
        for component in value.split("/"):
            if component in ("", "."):
                continue
            if component == "..":
                if not parts:
                    raise asyncssh.SFTPPermissionDenied(tr('禁止越界路径'))
                parts.pop()
            else:
                parts.append(component)
        return "/" + "/".join(parts)

    def map_path(self, path: bytes) -> bytes:
        virtual = self.normalized(path)
        local = self.root
        for component in virtual.strip("/").split("/"):
            if not component:
                continue
            local = local / component
            if local.is_symlink():
                raise asyncssh.SFTPPermissionDenied(tr('不允许访问符号链接'))
        if not local.resolve().is_relative_to(self.root):
            raise asyncssh.SFTPPermissionDenied(tr('路径超出共享目录'))
        if local.exists() and local.is_file() and local.stat().st_nlink > 1:
            raise asyncssh.SFTPPermissionDenied(tr('不允许访问具有多个硬链接的文件'))
        if local.exists() and not (local.is_file() or local.is_dir()):
            raise asyncssh.SFTPPermissionDenied(tr('只允许访问普通文件和目录'))
        return os.fsencode(local)

    def check(self, path: bytes, action: str):
        self.map_path(path)
        virtual = self.normalized(path)
        if not self.user.allows(virtual, action):
            self.emit("log", tr('拒绝操作 · {value0} · {value1} · {value2}', value0=self.user.username, value1=action, value2=virtual))
            raise asyncssh.SFTPPermissionDenied(tr('账号没有此目录的操作权限'))

    async def open(self, path, pflags, attrs):
        if pflags & (asyncssh.FXF_WRITE | asyncssh.FXF_APPEND | asyncssh.FXF_CREAT | asyncssh.FXF_TRUNC):
            self.check(path, "upload")
        if pflags & asyncssh.FXF_READ or not pflags & asyncssh.FXF_WRITE:
            self.check(path, "download")
        # File mode/ownership changes are never delegated to remote users.
        attrs.permissions = None
        attrs.uid = attrs.gid = None
        self.emit("log", tr('打开文件 · {value0} · {value1}', value0=self.user.username, value1=self.normalized(path)))
        file_obj = super().open(path, pflags, attrs)
        writing = pflags & (asyncssh.FXF_WRITE | asyncssh.FXF_APPEND | asyncssh.FXF_CREAT | asyncssh.FXF_TRUNC)
        if not writing and self.sync_api and self.user.confirmation == "auto":
            opened = os.fstat(file_obj.fileno())
            signature = self.sync_api.store.signature(opened)
            try:
                item = await asyncio.to_thread(self.sync_api.auto_candidate, self.user, self.normalized(path), signature)
                if item:
                    self.downloads[file_obj] = {"item": item, "signature": signature,
                                                "descriptor_signature": descriptor_signature(opened),
                                                "path": path, "ranges": [], "failed": False}
            except Exception as exc:
                self.emit("log", tr('自动确认准备失败，文件仍可正常下载 · {value0}', value0=exc))
        return file_obj

    def read_download(self, file_obj, offset, size):
        tracked = self.downloads.get(file_obj)
        try:
            data = super().read(file_obj, offset, size)
        except Exception:
            if tracked:
                tracked["failed"] = True
            raise
        if tracked and data:
            intervals = [*tracked["ranges"], (offset, offset + len(data))]
            intervals.sort()
            merged = []
            for start, end in intervals:
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            tracked["ranges"] = merged
            if len(merged) > 4096:
                tracked["failed"] = True
                tracked["ranges"] = []
        return data

    async def close_explicit(self, file_obj):
        self.normal_closes.add(file_obj)
        try:
            await self.close(file_obj)
        finally:
            self.normal_closes.discard(file_obj)

    async def close(self, file_obj):
        tracked = self.downloads.pop(file_obj, None)
        complete = False
        if tracked and file_obj in self.normal_closes and not tracked["failed"]:
            try:
                size = tracked["signature"][0]
                covered = size == 0 or tracked["ranges"] == [(0, size)]
                signature = self.sync_api.store.signature
                complete = (covered and descriptor_signature(os.fstat(file_obj.fileno())) == tracked["descriptor_signature"]
                            and signature(os.stat(self.map_path(tracked["path"]))) == tracked["signature"])
            except (OSError, asyncssh.SFTPError):
                pass
        super().close(file_obj)
        if complete:
            try:
                peer = self.connection.get_extra_info("peername")
                await asyncio.to_thread(self.sync_api.auto_confirm, self.user, tracked["item"], str(peer[0]))
                self.emit("log", tr('自动确认完成 · {value0} · {value1}', value0=self.user.username, value1=tracked['item']['path']))
            except Exception as exc:
                self.emit("log", tr('自动确认失败，待同步状态保留 · {value0}', value0=exc))

    async def scandir(self, path):
        self.check(path, "list")
        with os.scandir(self.map_path(path)) as entries:
            for entry in entries:
                child = posixpath.join(path, entry.name)
                try:
                    self.map_path(child)
                    virtual = self.normalized(child)
                    if not any(self.user.allows(virtual, p) for p in ("list", "download")):
                        continue
                    attrs = asyncssh.SFTPAttrs.from_local(entry.stat(follow_symlinks=False))
                except (asyncssh.SFTPPermissionDenied, FileNotFoundError):
                    continue
                yield asyncssh.SFTPName(entry.name, attrs=attrs)

    def metadata(self, path):
        self.map_path(path)
        if not any(self.user.allows(self.normalized(path), p) for p in ("list", "download", "upload")):
            raise asyncssh.SFTPPermissionDenied(tr('无权查看目录信息'))

    def stat(self, path):
        self.metadata(path)
        return super().stat(path)

    def lstat(self, path):
        self.metadata(path)
        return super().lstat(path)

    def realpath(self, path):
        self.map_path(path)
        return os.fsencode(self.normalized(path))

    def setstat(self, path, attrs):
        self.check(path, "upload")
        self.check_attrs(attrs)
        return super().setstat(path, attrs)

    def lsetstat(self, path, attrs):
        self.check(path, "upload")
        self.check_attrs(attrs)
        return super().lsetstat(path, attrs)

    def open56(self, path, desired_access, flags, attrs):
        raise asyncssh.SFTPOpUnsupported(tr('仅支持 SFTP v3'))

    def fsetstat(self, file_obj, attrs):
        self.check_attrs(attrs)
        mode = getattr(file_obj, "mode", "")
        if not any(char in mode for char in ("w", "a", "+")):
            raise asyncssh.SFTPPermissionDenied(tr('文件未以可写方式打开'))
        return super().fsetstat(file_obj, attrs)

    @staticmethod
    def check_attrs(attrs):
        if any(getattr(attrs, key, None) is not None for key in ("uid", "gid", "owner", "group", "permissions")):
            raise asyncssh.SFTPPermissionDenied(tr('不允许修改系统权限或文件所有者'))

    def mkdir(self, path, attrs):
        self.check(path, "mkdir")
        attrs.permissions = None
        return super().mkdir(path, attrs)

    def remove(self, path):
        self.check(path, "delete")
        return super().remove(path)

    def rmdir(self, path):
        self.check(path, "delete")
        if self.normalized(path) == "/":
            raise asyncssh.SFTPPermissionDenied(tr('不能删除共享根目录'))
        return super().rmdir(path)

    def check_rename(self, oldpath, newpath, overwrite=False):
        self.check(oldpath, "rename")
        self.check(newpath, "rename")
        self.check(newpath, "upload")
        old, new = policy_path(self.normalized(oldpath)), policy_path(self.normalized(newpath))
        if old == "/" or new == "/":
            raise asyncssh.SFTPPermissionDenied(tr('不能移动共享根目录'))
        for action in ("list", "download"):
            if self.user.allows(new, action) and not self.user.allows(old, action):
                raise asyncssh.SFTPPermissionDenied(tr('移动不能扩大文件的可见或下载权限'))
        if overwrite and Path(os.fsdecode(self.map_path(newpath))).exists():
            self.check(newpath, "delete")
        if Path(os.fsdecode(self.map_path(oldpath))).is_dir():
            for rule in self.user.rules:
                rule_path = policy_path(rule.path)
                if rule_path.startswith(old + "/") or rule_path.startswith(new + "/"):
                    raise asyncssh.SFTPPermissionDenied(tr('含子目录权限规则的目录不可整体移动'))

    def rename(self, oldpath, newpath):
        self.check_rename(oldpath, newpath)
        return super().rename(oldpath, newpath)

    def posix_rename(self, oldpath, newpath):
        self.check_rename(oldpath, newpath, overwrite=True)
        return super().posix_rename(oldpath, newpath)

    def symlink(self, oldpath, newpath):
        raise asyncssh.SFTPPermissionDenied(tr('不允许创建符号链接'))

    def readlink(self, path):
        raise asyncssh.SFTPPermissionDenied(tr('不允许访问符号链接'))

    def link(self, oldpath, newpath):
        raise asyncssh.SFTPPermissionDenied(tr('不允许创建硬链接'))


class AuthServer(asyncssh.SSHServer):
    def __init__(self, service):
        self.service = service
        self.identifier = secrets.token_hex(8)
        self.connection = None
        self.user = None
        self.failures = 0
        self.key_user = None
        self.key_epoch = None

    def connection_made(self, connection):
        self.connection = connection
        if len(self.service.connections) >= self.service.config.auth_max_connections:
            self.service.audit_event("connection_rejected", connection=self.identifier, result="connection_limit")
            connection.close()
            return
        self.service.connections[self.identifier] = self

    def connection_lost(self, exc):
        self.service.connections.pop(self.identifier, None)
        if self.user and self.service.active_users.get(self.user.userid) is self:
            self.service.active_users.pop(self.user.userid, None)
        if self.user:
            self.service.emit("disconnected", self.identifier)
            self.service.emit("log", tr('用户断开 · {value0}', value0=self.user.username))

    def begin_auth(self, username):
        return True

    def session_requested(self):
        if self.user is None:
            return False
        from .transport import SyncSession
        return SyncSession(lambda channel: RestrictedSFTP(channel, self.user, self.service.emit, self.service.sync_api))

    def password_auth_supported(self):
        return True

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        user = next((u for u in self.service.config.users if u.enabled and u.username == username), None)
        peer = str(self.connection.get_extra_info("peername")[0])
        reason = self.service.auth_limits.begin(peer, user.userid if user else "unknown")
        if reason:
            self.service.audit_event("auth_rejected", username=username[:64], ip=peer, result=reason,
                                     connection=self.identifier)
            self.connection.close()
            return False
        self.service.auth_limits.finish()
        public = " ".join(key.export_public_key().decode().split()[:2])
        valid = user and user.ip_allowed(peer) and public in user.authorized_keys
        if valid and user.single_connection and user.userid in self.service.active_users:
            valid = False
        if valid:
            # Probes do not reserve an account; AsyncSSH verifies possession first.
            self.key_user, self.key_epoch = user, self.service.config_epoch
            return True
        self.service.audit_event("auth_failed", username=username[:64], ip=peer,
                                 connection=self.identifier, detail="public_key")
        self.failures += 1
        if self.failures >= 3:
            self.connection.close()
        return False

    async def validate_password(self, username, password):
        epoch = self.service.config_epoch
        user = next((u for u in self.service.config.users if u.enabled and u.username == username), None)
        peer = str(self.connection.get_extra_info("peername")[0])
        reason = self.service.auth_limits.begin(peer, user.userid if user else "unknown")
        if reason:
            self.service.audit_event("auth_rejected", username=username[:64], ip=peer,
                                     connection=self.identifier, result=reason)
            self.connection.close()
            return False
        # Hash work stays outside the network event loop, including unknown usernames.
        encoded = user.password_hash if user else self.service.dummy_hash
        limits = self.service.auth_limits
        work = asyncio.create_task(asyncio.to_thread(verify_password, password, encoded))
        self.service.auth_tasks.add(work)
        def finished(task):
            limits.finish()
            self.service.auth_tasks.discard(task)
        work.add_done_callback(finished)
        # Cancellation must not release the budget while the native hash still runs.
        valid = await asyncio.shield(work)
        if epoch != self.service.config_epoch:
            return False
        if user and user.password_hash and valid and user.ip_allowed(peer):
            if user.single_connection and user.userid in self.service.active_users:
                self.service.audit_event("auth_rejected", userid=user.userid, username=username,
                                         ip=peer, connection=self.identifier, result="single_connection")
                self.service.emit("log", tr('拒绝重复登录 · {value0}', value0=username))
                return False
            # Reserve without awaiting: concurrent password checks cannot both win.
            if self.connection.is_closed():
                return False
            self.user = user
            self.service.active_users[user.userid] = self
            return True
        self.service.emit("log", tr('登录失败 · {value0} · {value1}', value0=username[:64], value1=self.connection.get_extra_info('peername')))
        self.service.audit_event("auth_failed", username=username[:64], ip=peer, connection=self.identifier)
        self.failures += 1
        if self.failures >= 3:
            self.connection.close()
        return False

    def auth_completed(self):
        if self.user is None:
            user = self.key_user
            if (not user or self.key_epoch != self.service.config_epoch
                    or (user.single_connection and user.userid in self.service.active_users)):
                self.service.audit_event("auth_rejected", connection=self.identifier,
                                         result="settings_changed_or_single_connection")
                self.connection.close()
                return
            self.user = user
            self.service.active_users[user.userid] = self
        peer = self.connection.get_extra_info("peername")
        self.connection.set_extra_info(account=self.user)
        self.service.emit("connected", {
            "id": self.identifier, "username": self.user.username, "userid": self.user.userid,
            "ip": str(peer[0]), "port": peer[1], "root": self.user.root,
            "since": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        self.service.emit("log", tr('登录成功 · {value0} · {value1}', value0=self.user.username, value1=peer[0]))


class SFTPService:
    def __init__(self, data_dir: Path, emit: Callable = lambda *_: None):
        from .config import hash_password
        self.data_dir = data_dir
        self._emit = emit
        self.audit = None
        self.auth_limits = None
        self.auth_tasks = set()
        self.config_epoch = 0
        self._reload_lock = asyncio.Lock()
        self.process_lock = None
        self.config = Config()
        self.listener = None
        self.connections: dict[str, AuthServer] = {}
        self.active_users = {}
        self.dummy_hash = hash_password(secrets.token_urlsafe(24))
        self.index = self.sync_api = self.monitor = None

    def audit_event(self, event, **fields):
        if self.audit:
            self.audit.record(event, **fields)

    def emit(self, event, value):
        if event == "log":
            self.audit_event("message", detail=str(value))
        elif event == "connected":
            self.audit_event("login", userid=value["userid"], username=value["username"],
                             ip=value["ip"], port=value["port"], connection=value["id"])
        elif event == "disconnected":
            self.audit_event("disconnect", connection=value)
        self._emit(event, value)

    async def start(self, config: Config):
        if self.listener:
            raise ValueError(tr('服务已在运行'))
        try:
            return await self._start(config)
        except Exception as exc:
            self.audit_event("startup_failed", detail=str(exc))
            await self.stop()
            raise

    async def _start(self, config: Config):
        config = copy.deepcopy(config)
        config.validate(self.data_dir)
        if not any(u.enabled for u in config.users):
            raise ValueError(tr('请先添加至少一个已启用的用户'))
        ConfigStore(self.data_dir).prepare()
        self.process_lock = ProcessLock(self.data_dir / "server.lock")
        if self.audit:
            await asyncio.to_thread(self.audit.close)
        self.audit = AuditLog(self.data_dir / "logs")
        self.auth_limits = AuthLimits(config)
        key_path = self.data_dir / "ssh_host_ed25519_key"
        if not key_path.exists():
            key = asyncssh.generate_private_key("ssh-ed25519")
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(key.export_private_key())
        key_path.chmod(0o600)
        self.config = config
        self.config_epoch += 1
        from .index import IndexStore
        from .monitor import Monitor
        from .sync_api import SyncAPI
        self.index = await asyncio.to_thread(IndexStore, self.data_dir)
        self.sync_api = await asyncio.to_thread(SyncAPI, self.index)
        self.sync_api.current_users = {user.userid: user for user in config.users}
        for user in config.users:
            await asyncio.to_thread(self.sync_api.ensure_user, user)
        self.listener = await asyncssh.create_server(
            lambda: AuthServer(self), config.host, config.port,
            server_host_keys=[str(key_path)],
            allow_scp=False, login_timeout=30, sftp_version=3,
            encoding=None,
        )
        self.monitor = Monitor(self.index, config, self.emit, self.sync_api)
        await self.monitor.start()
        fingerprint = asyncssh.read_private_key(key_path).get_fingerprint()
        self.emit("log", tr('服务已启动 · {value0}:{value1} · 主机指纹 {value2}', value0=config.host, value1=config.port, value2=fingerprint))
        return fingerprint

    async def reload(self, config: Config):
        """Apply non-listener settings; disconnect changed accounts only."""
        async with self._reload_lock:
            if not self.listener:
                raise ValueError(tr('服务未启动'))
            updated = copy.deepcopy(config)
            updated.validate(self.data_dir)
            if (updated.host, updated.port) != (self.config.host, self.config.port):
                raise ValueError(tr('监听 IP 或端口改变，需要停止后重新启动'))
            roots = {user.root for user in updated.users if user.enabled}
            previous = {user.userid: user for user in self.config.users}
            current = {user.userid: user for user in updated.users}
            if (not roots.issubset(set(self.monitor.roots)) or any(
                    userid in previous and previous[userid].root != user.root for userid, user in current.items())):
                raise ValueError(tr('新增监控根目录或变更账号根目录，需要停止后重新启动'))
            changed = {userid for userid in previous.keys() | current.keys() if previous.get(userid) != current.get(userid)}
            # Persist validation succeeds before disconnecting anyone. Runtime takes
            # the same snapshot; expensive reconciliation is lazy and resumable.
            await asyncio.to_thread(ConfigStore(self.data_dir).save, updated)
            self.config_epoch += 1
            self.config = updated
            self.sync_api.current_users = current
            self.monitor.config = updated
            self.auth_limits.config = updated
            for auth in list(self.connections.values()):
                if auth.user and auth.user.userid in changed:
                    await self.disconnect(auth.identifier)
            self.audit_event("config_reloaded", detail=f"changed_accounts={len(changed)}")
            self.emit("log", tr('配置已热加载 · {value0} 个账号设置变化；其在线连接已断开', value0=len(changed)))
            return {"changed_accounts": len(changed)}

    async def disconnect(self, identifier):
        auth = self.connections.get(identifier)
        if auth:
            auth.connection.close()
            await auth.connection.wait_closed()

    async def stop(self):
        listener, self.listener = self.listener, None
        if listener:
            listener.close()
        connections = [auth.connection for auth in list(self.connections.values())]
        for connection in connections:
            connection.close()
        if connections:
            await asyncio.gather(*(c.wait_closed() for c in connections))
        if self.auth_tasks:
            await asyncio.gather(*list(self.auth_tasks), return_exceptions=True)
        if listener:
            await listener.wait_closed()
        if self.monitor:
            await self.monitor.stop()
            self.monitor = None
        if self.index:
            await asyncio.to_thread(self.index.close)
        self.connections.clear()
        self.active_users.clear()
        self.emit("log", tr('服务已停止'))
        if self.audit:
            await asyncio.to_thread(self.audit.close)
        if self.process_lock:
            self.process_lock.close()
            self.process_lock = None

    async def sync_status(self):
        if not self.index or self.index._closed:
            return {"roots": [], "users": []}
        result = await asyncio.to_thread(self.index.summary)
        result["logging"] = self.audit.metrics() if self.audit else {}
        result["authentication"] = {"hashes_inflight": self.auth_limits.inflight,
                                     "rate_limit_buckets": len(self.auth_limits.buckets)}
        return result

    async def sync_details(self, userid, status="pending", after=""):
        rows = await asyncio.to_thread(self.sync_api.details, userid, status, after)
        return {"userid": userid, "status": status, "after": after, "files": rows}

    async def scan_now(self):
        if self.monitor:
            from .index import root_id
            for root in self.monitor.roots:
                self.monitor.dirty(root_id(root), "/")
