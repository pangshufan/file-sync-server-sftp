from __future__ import annotations


import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import tempfile
import sys
import unicodedata
import uuid
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from .i18n import tr

PERMISSIONS = ("list", "download", "upload", "mkdir", "rename", "delete")
LABELS = {"list": "浏览", "download": "下载", "upload": "上传/覆盖", "mkdir": "创建目录", "rename": "重命名/移动", "delete": "删除"}


def hash_password(password: str) -> str:
    if not password:
        raise ValueError(tr('密码不能为空'))
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, salt, expected = encoded.split("$")
        if kind != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt, validate=True), n=16384, r=8, p=1)
        return hmac.compare_digest(actual, base64.b64decode(expected, validate=True))
    except (ValueError, TypeError):
        return False


def virtual_path(value: str) -> str:
    if not value.startswith("/") or ".." in PurePosixPath(value).parts or "\\" in value or "\x00" in value:
        raise ValueError(tr('目录规则必须是以 / 开头的相对共享根目录路径，且不能包含 .. 或反斜杠'))
    return "/" + "/".join(p for p in PurePosixPath(value).parts if p not in ("/", "//"))


def policy_path(value: str) -> str:
    # Default macOS/Windows filesystems treat case aliases as the same path.
    return unicodedata.normalize("NFC", value).casefold() if sys.platform in ("darwin", "win32") else value


@dataclass
class DirectoryRule:
    path: str
    permissions: list[str] = field(default_factory=list)


@dataclass
class User:
    username: str
    password_hash: str
    root: str
    enabled: bool = True
    permissions: list[str] = field(default_factory=lambda: list(PERMISSIONS))
    rules: list[DirectoryRule] = field(default_factory=list)
    userid: str = field(default_factory=lambda: uuid.uuid4().hex)
    single_connection: bool = True
    allowed_ips: list[str] = field(default_factory=list)
    sync_enabled: bool = True
    sync_directories: list[str] = field(default_factory=lambda: ["/"])
    sync_start: float = 0.0
    include_patterns: list[str] = field(default_factory=lambda: ["*"])
    exclude_patterns: list[str] = field(default_factory=list)
    confirmation: str = "manual"
    sync_generation: int = 0
    authorized_keys: list[str] = field(default_factory=list)

    def ip_allowed(self, address: str) -> bool:
        peer = ipaddress.ip_address(address.split("%", 1)[0])
        if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped:
            peer = peer.ipv4_mapped
        return not self.allowed_ips or any(peer in ipaddress.ip_network(value, strict=False) for value in self.allowed_ips)

    def allows(self, path: str, action: str) -> bool:
        path = policy_path(path)
        selected = self.permissions
        length = -1
        for rule in self.rules:
            rule_path = policy_path(rule.path)
            if (path == rule_path or path.startswith(rule_path.rstrip("/") + "/")) and len(rule_path) > length:
                selected, length = rule.permissions, len(rule_path)
        return action in selected


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 2222
    users: list[User] = field(default_factory=list)
    scan_interval: int = 0
    audit_days: int = 7
    retention_days: int = 30
    auth_max_inflight: int = 4
    auth_ip_attempts: int = 20
    auth_user_attempts: int = 10
    auth_window_seconds: int = 60
    auth_max_connections: int = 128

    def validate(self, data_dir: Path | None = None) -> None:
        for name, upper in (("auth_max_inflight", 64), ("auth_ip_attempts", 10000),
                            ("auth_user_attempts", 10000), ("auth_window_seconds", 86400),
                            ("auth_max_connections", 10000)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(tr('{value0} 须为 1–{value1} 的整数', value0=name, value1=upper))
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            raise ValueError(tr('监听 IP 必须是有效的 IPv4 或 IPv6 地址')) from None
        if not 1 <= self.port <= 65535:
            raise ValueError(tr('端口范围为 1–65535'))
        if not 0 <= self.scan_interval <= 86400 or not 1 <= self.audit_days <= 365 or not 1 <= self.retention_days <= 3650:
            raise ValueError(tr('巡检间隔须为 0–86400 秒，摘要复核为 1–365 天，日志保留为 1–3650 天'))
        names: set[str] = set()
        userids: set[str] = set()
        for user in self.users:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user.username):
                raise ValueError(tr('用户名应为 1–64 位字母、数字、下划线、点或短横线'))
            if user.username in names:
                raise ValueError(tr('用户名重复：{value0}', value0=user.username))
            names.add(user.username)
            if not re.fullmatch(r"[a-f0-9]{32}", user.userid) or user.userid in userids:
                raise ValueError(tr('userid 必须为唯一的 32 位小写十六进制标识'))
            userids.add(user.userid)
            for value in user.allowed_ips:
                try:
                    ipaddress.ip_network(value, strict=False)
                except ValueError:
                    raise ValueError(tr('无效登录 IP 或网段：{value0}', value0=value)) from None
            if user.confirmation not in ("manual", "auto"):
                raise ValueError(tr('成功确认方式须为 manual 或 auto'))
            if not isinstance(user.sync_start, (int, float)) or not math.isfinite(user.sync_start) or user.sync_start < 0:
                raise ValueError(tr('增量起始时间必须是非负的 server Unix 时间'))
            if not isinstance(user.sync_generation, int) or user.sync_generation < 0:
                raise ValueError(tr('无效同步重置代数'))
            user.sync_directories = list(dict.fromkeys(virtual_path(path) for path in user.sync_directories))
            if not user.sync_directories:
                raise ValueError(tr('至少配置一个同步目录'))
            for pattern in [*user.include_patterns, *user.exclude_patterns]:
                if not isinstance(pattern, str) or not pattern or "\x00" in pattern:
                    raise ValueError(tr('文件匹配规则不能为空或包含空字符'))
            if not user.password_hash and not user.authorized_keys:
                raise ValueError(tr('请为 {value0} 设置密码或 SSH 公钥', value0=user.username))
            if user.password_hash and not user.password_hash.startswith("scrypt$"):
                raise ValueError(tr('{value0} 的密码格式无效', value0=user.username))
            import asyncssh
            if not isinstance(user.authorized_keys, list) or len(user.authorized_keys) > 32:
                raise ValueError(tr('每个用户最多配置 32 个 SSH 公钥'))
            normalized_keys = []
            for value in user.authorized_keys:
                try:
                    key = asyncssh.import_public_key(value)
                    normalized_keys.append(" ".join(key.export_public_key().decode().split()[:2]))
                except (ValueError, TypeError, asyncssh.KeyImportError):
                    raise ValueError(tr('{value0} 包含无效 SSH 公钥（每行填写一个 OpenSSH 公钥，不含选项）', value0=user.username)) from None
            user.authorized_keys = list(dict.fromkeys(normalized_keys))
            root = Path(user.root).expanduser()
            if not root.is_absolute() or not root.is_dir():
                raise ValueError(tr('用户 {value0} 的根目录必须是已存在的绝对目录', value0=user.username))
            root = root.resolve()
            if data_dir:
                data_key = policy_path(str(data_dir.resolve()))
                root_key = policy_path(str(root)).rstrip(os.sep)
                if data_key == root_key or data_key.startswith(root_key + os.sep):
                    raise ValueError(tr('共享目录不能包含本软件的配置或主机密钥目录'))
            if set(user.permissions) - set(PERMISSIONS):
                raise ValueError(tr('存在未知权限'))
            seen = set()
            for rule in user.rules:
                rule.path = virtual_path(rule.path)
                if policy_path(rule.path) in seen:
                    raise ValueError(tr('目录规则重复：{value0}', value0=rule.path))
                seen.add(policy_path(rule.path))
                if set(rule.permissions) - set(PERMISSIONS):
                    raise ValueError(tr('存在未知目录权限'))
            user.root = str(root)


class ConfigStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "config.json"

    def prepare(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)

    def load(self) -> Config:
        if not self.path.exists():
            return Config()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema_version", 1) > 4:
            raise ValueError(tr('配置来自更高版本，请使用对应版本的软件'))
        users = []
        for entry in data.get("users", []):
            entry = dict(entry)
            entry["rules"] = [DirectoryRule(**r) for r in entry.get("rules", [])]
            users.append(User(**entry))
        config = Config(host=data["host"], port=data["port"], users=users,
                        scan_interval=data.get("scan_interval", 0), audit_days=data.get("audit_days", 7),
                        retention_days=data.get("retention_days", 30),
                        **{name: data.get(name, field.default) for name, field in Config.__dataclass_fields__.items()
                           if name.startswith("auth_")})
        config.validate(self.directory)
        if data.get("schema_version", 1) < 4:
            from .migration import backup
            backup(self.directory, "config-v4")
            self.save(config)
        return config

    def save(self, config: Config):
        config.validate(self.directory)
        self.prepare()
        fd, name = tempfile.mkstemp(dir=self.directory, prefix="config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"schema_version": 4, **asdict(config)}, stream, ensure_ascii=False, indent=2)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
