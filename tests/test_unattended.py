import asyncio
import copy
import json
import os
import signal
import subprocess
import sys

import asyncssh
import pytest

from sync_server.config import ConfigStore
from sync_server.process_lock import ProcessLock
from sync_server.sync_api import SyncError
from test_server import running


async def test_public_key_only_auth_single_connection_and_ip_policy(running):
    service, _, connect, config, *_ = running
    await service.stop()
    key = asyncssh.generate_private_key("ssh-ed25519")
    key.set_comment("automation@example")
    user = config.users[0]
    user.password_hash = ""
    user.authorized_keys = [key.export_public_key().decode()]
    await service.start(config)
    host_key = asyncssh.read_private_key(service.data_dir / "ssh_host_ed25519_key").convert_to_public()
    async def login(key):
        return await asyncssh.connect("127.0.0.1", config.port, username="admin", client_keys=[key],
                                      agent_path=None, known_hosts=([host_key], [], []))
    with pytest.raises(asyncssh.PermissionDenied):
        await connect()
    with pytest.raises(asyncssh.PermissionDenied):
        await login(asyncssh.generate_private_key("ssh-ed25519"))
    async with await login(key) as conn:
        async with conn.start_sftp_client() as sftp:
            assert "hello.txt" in await sftp.listdir("/")
        with pytest.raises((asyncssh.Error, OSError)):
            await login(key)
    await service.stop()
    user.allowed_ips = ["192.0.2.1"]
    await service.start(config)
    with pytest.raises(asyncssh.PermissionDenied):
        await login(key)


async def test_reload_disconnects_changed_account_only_and_rejects_listener_change(running):
    service, root, connect, config, *_ = running
    first, second = await connect(), await connect("other")
    original = service.config.users[0]
    try:
        updated = copy.deepcopy(service.config)
        updated.users[0].permissions = ["list"]
        updated.scan_interval = 77
        result = await service.reload(updated)
        assert result["changed_accounts"] == 1
        await asyncio.wait_for(first.wait_closed(), 3)
        assert not second.is_closed()
        assert service.monitor.config.scan_interval == 77
        assert ConfigStore(service.data_dir).load().scan_interval == 77
        with pytest.raises(SyncError, match="设置已变化"):
            service.sync_api.ensure_user(original)
        async with second.start_sftp_client() as sftp:
            assert "only.txt" in await sftp.listdir("/")
        async with await connect() as replacement, replacement.start_sftp_client() as sftp:
            with pytest.raises(asyncssh.SFTPPermissionDenied):
                await sftp.open("/hello.txt")
        invalid = copy.deepcopy(updated)
        invalid.port += 1
        with pytest.raises(ValueError, match="端口"):
            await service.reload(invalid)
        assert service.config.port == config.port
        assert not second.is_closed()
    finally:
        first.close()
        second.close()
        await asyncio.gather(first.wait_closed(), second.wait_closed())


def test_single_state_process_lock_and_qt_free_entry(tmp_path):
    first = ProcessLock(tmp_path / "server.lock")
    try:
        with pytest.raises(ValueError, match="其他服务进程"):
            ProcessLock(tmp_path / "server.lock")
    finally:
        first.close()
    ProcessLock(tmp_path / "server.lock").close()
    result = subprocess.run([sys.executable, "-c", "import sys; import sync_server.headless; assert 'PySide6' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


async def test_headless_process_sigterm_and_persistent_logs(running, tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX signal path; Windows supervisor requires native acceptance")
    service, _, _, config, *_ = running
    await service.stop()
    folder = tmp_path / "headless"
    ConfigStore(folder).save(config)
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "sync_server.headless",
                    "--data-dir", str(folder), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), 10)
            assert line, (await process.stderr.read()).decode()
            if "服务已启动" in line.decode():
                break
        process.send_signal(signal.SIGTERM)
        assert await asyncio.wait_for(process.wait(), 10) == 0
        assert "服务已停止" in (folder / "logs/server.jsonl").read_text()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
