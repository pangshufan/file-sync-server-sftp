import asyncio
import os
import socket
import sys

import asyncssh
import pytest
import pytest_asyncio

from sync_server.config import Config, ConfigStore, DirectoryRule, PERMISSIONS, User, hash_password, verify_password
from sync_server.server import SFTPService


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest_asyncio.fixture
async def running(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    (root / "hello.txt").write_text("hello")
    (root / "private").mkdir()
    (root / "private/secret.txt").write_text("secret")
    (root / "drop").mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "only.txt").write_text("second user")
    encoded = hash_password("test-password")
    users = [
        User("admin", encoded, str(root)),
        User("reader", encoded, str(root), permissions=["list", "download"], rules=[DirectoryRule("/private", [])]),
        User("other", encoded, str(other)),
        User("disabled", encoded, str(root), enabled=False),
        User("dropbox", encoded, str(root), permissions=list(PERMISSIONS), rules=[DirectoryRule("/private", ["rename", "upload"])]),
    ]
    config = Config(port=free_port(), users=users)
    events = []
    service = SFTPService(tmp_path / "state", lambda *args: events.append(args))
    fingerprint = await service.start(config)
    public_key = asyncssh.read_private_key(tmp_path / "state/ssh_host_ed25519_key").convert_to_public()

    async def connect(username="admin", password="test-password"):
        return await asyncssh.connect("127.0.0.1", config.port, username=username, password=password,
                                      known_hosts=([public_key], [], []), client_keys=[], agent_path=None)

    try:
        yield service, root, connect, config, events, fingerprint
    finally:
        await service.stop()


async def test_file_operations_and_persistent_key(running):
    service, root, connect, config, events, fingerprint = running
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        assert "hello.txt" in await sftp.listdir("/")
        assert await sftp.realpath(".") == "/"
        assert await sftp.realpath("/private/..") == "/"
        async with sftp.open("/new.txt", "w") as file:
            await file.write("new data")
        assert (root / "new.txt").read_text() == "new data"
        async with sftp.open("/hello.txt", "r") as file:
            assert await file.read() == "hello"
        await sftp.mkdir("/created")
        await sftp.rename("/new.txt", "/created/renamed.txt")
        await sftp.remove("/created/renamed.txt")
        await sftp.rmdir("/created")
        with pytest.raises(asyncssh.ChannelOpenError):
            await conn.run("echo forbidden")
        with pytest.raises(asyncssh.ChannelOpenError):
            await conn.open_connection("127.0.0.1", config.port)
    await service.stop()
    assert fingerprint == await service.start(config)
    assert any(event[0] == "connected" for event in events)


async def test_read_only_and_directory_override(running):
    _, root, connect, *_ = running
    async with await connect("reader") as conn, conn.start_sftp_client() as sftp:
        assert "private" not in await sftp.listdir("/")
        async with sftp.open("/hello.txt") as file:
            assert await file.read() == "hello"
        for path, mode in [("/new.txt", "w"), ("/hello.txt", "w"), ("/private/secret.txt", "r")]:
            with pytest.raises(asyncssh.SFTPPermissionDenied):
                await sftp.open(path, mode)
        for operation in [sftp.mkdir("/no"), sftp.remove("/hello.txt"), sftp.rename("/hello.txt", "/no.txt"), sftp.truncate("/hello.txt", 0), sftp.listdir("/private")]:
            with pytest.raises(asyncssh.SFTPPermissionDenied):
                await operation
        with pytest.raises(asyncssh.SFTPPermissionDenied):
            await sftp.setstat("/hello.txt", asyncssh.SFTPAttrs(size=0), follow_symlinks=False)
        async with sftp.open("/hello.txt", "r") as file:
            with pytest.raises(asyncssh.SFTPPermissionDenied):
                await file.setstat(asyncssh.SFTPAttrs(size=0))
        if sys.platform == "darwin":
            with pytest.raises(asyncssh.SFTPPermissionDenied):
                await sftp.open("/PRIVATE/secret.txt")
    assert (root / "hello.txt").read_text() == "hello"


async def test_accounts_and_disconnect(running):
    service, _, connect, _, events, _ = running
    for user, password in [("admin", "wrong"), ("unknown", "test-password"), ("disabled", "test-password")]:
        with pytest.raises(asyncssh.PermissionDenied):
            await connect(user, password)
    async with await connect("admin") as first, await connect("other") as second:
        async with second.start_sftp_client() as sftp:
            assert "only.txt" in await sftp.listdir("/")
            assert "hello.txt" not in await sftp.listdir("/")
        connected = [value for event, value in events if event == "connected"]
        assert {entry["username"] for entry in connected} == {"admin", "other"}
        await service.disconnect(next(entry["id"] for entry in connected if entry["username"] == "admin"))
        await asyncio.wait_for(first.wait_closed(), timeout=3)
        assert not second.is_closed()


async def test_escape_and_link_restrictions(running, tmp_path):
    _, root, connect, *_ = running
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (root / "escape").symlink_to(outside)
    os.link(outside, root / "hardlink")
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        for path in ["/../outside.txt", "/escape", "/hardlink", "/private/../../outside.txt"]:
            with pytest.raises(asyncssh.SFTPPermissionDenied):
                await sftp.open(path)
        with pytest.raises(asyncssh.SFTPPermissionDenied):
            await sftp.symlink("/hello.txt", "/link")
        with pytest.raises(asyncssh.SFTPPermissionDenied):
            await sftp.link("/hello.txt", "/hard")
        with pytest.raises(asyncssh.SFTPPermissionDenied):
            await sftp.chmod("/hello.txt", 0o777)
        assert "escape" not in await sftp.listdir("/")
    assert outside.read_text() == "outside"


async def test_move_does_not_bypass_directory_policy(running):
    _, root, connect, *_ = running
    async with await connect("dropbox") as conn, conn.start_sftp_client() as sftp:
        with pytest.raises(asyncssh.SFTPPermissionDenied):
            await sftp.rename("/private/secret.txt", "/secret.txt")
    async with await connect("reader") as conn, conn.start_sftp_client() as sftp:
        with pytest.raises(asyncssh.SFTPPermissionDenied):
            await sftp.posix_rename("/hello.txt", "/private/secret.txt")


async def test_directory_move_with_nested_policy(running):
    service, root, connect, config, *_ = running
    await service.stop()
    config.users[0].rules = [DirectoryRule("/private/secret.txt", [])]
    await service.start(config)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        with pytest.raises(asyncssh.SFTPPermissionDenied):
            await sftp.rename("/private", "/renamed")


async def test_stop_disconnects_all_clients_and_releases_port(running):
    service, _, connect, config, *_ = running
    async with await connect() as conn:
        await service.stop()
        await asyncio.wait_for(conn.wait_closed(), 3)
        assert not service.connections
    await service.start(config)
    async with await connect():
        pass


def test_password_and_configuration(tmp_path):
    encoded = hash_password("password")
    assert encoded != hash_password("password")
    assert verify_password("password", encoded)
    assert not verify_password("wrong", encoded)
    root = tmp_path / "root"
    root.mkdir()
    store = ConfigStore(tmp_path / "state")
    config = Config(users=[User("alice", encoded, str(root), rules=[DirectoryRule("/read-only", ["list"])])])
    store.save(config)
    assert store.load() == config
    assert '"password"' not in store.path.read_text()
    assert store.path.stat().st_mode & 0o777 == 0o600
    config.users.append(config.users[0])
    with pytest.raises(ValueError, match="用户名重复"):
        store.save(config)
    config.users.pop()
    config.users[0].root = str(tmp_path)
    with pytest.raises(ValueError, match="配置或主机密钥"):
        store.save(config)


async def test_port_conflict_does_not_start_listener(running, tmp_path):
    _, _, _, config, *_ = running
    service = SFTPService(tmp_path / "second-state")
    with pytest.raises(OSError):
        await service.start(config)
    assert service.listener is None


def test_longest_directory_rule():
    user = User("test", "unused", "/tmp", permissions=["list"], rules=[
        DirectoryRule("/data", ["list", "download"]),
        DirectoryRule("/data/private", []),
        DirectoryRule("/data/private/public", ["download"]),
    ])
    assert user.allows("/data/a.txt", "download")
    assert not user.allows("/data/private/a.txt", "download")
    assert user.allows("/data/private/public/a.txt", "download")
    assert not user.allows("/database/a.txt", "download")
