"""Reproduce Windows path/descriptor ctime disagreement on any CI platform."""
import asyncio
import hashlib
import os

import asyncssh
import pytest

from sync_server import file_state
from sync_server.index import IndexStore
from sync_server.server import RestrictedSFTP
from test_server import running
from test_sync import account, ready


class WindowsMetadata:
    def __init__(self, original, *, descriptor=False):
        self.original = original
        self.st_birthtime_ns = 1_000_000_000
        self.st_ctime_ns = original.st_ctime_ns + 1 if descriptor else self.st_birthtime_ns

    def __getattr__(self, name):
        return getattr(self.original, name)

    def __getitem__(self, key):
        return self.original[key]


@pytest.fixture
def windows_metadata(monkeypatch):
    native_stat, native_lstat, native_fstat = os.stat, os.lstat, os.fstat
    monkeypatch.setattr(file_state, "_WINDOWS", True)
    monkeypatch.setattr(os, "stat", lambda *args, **kwargs: WindowsMetadata(native_stat(*args, **kwargs)))
    monkeypatch.setattr(os, "lstat", lambda *args, **kwargs: WindowsMetadata(native_lstat(*args, **kwargs)))
    monkeypatch.setattr(os, "fstat", lambda *args, **kwargs: WindowsMetadata(native_fstat(*args, **kwargs), descriptor=True))


def test_native_path_and_descriptor_signatures_after_rewrite(tmp_path):
    path = tmp_path / "rewritten.txt"
    path.write_bytes(b"before")
    path.write_bytes(b"after")
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    with path.open("rb") as stream:
        by_path, by_fd = path.lstat(), os.fstat(stream.fileno())
        assert file_state.file_signature(by_path) == file_state.file_signature(by_fd), (by_path, by_fd)
    store = IndexStore(tmp_path / "state")
    try:
        result = store.inspect(path, None)
        assert result[5] == hashlib.sha256(b"after").hexdigest()
    finally:
        store.close()


def test_windows_ctime_disagreement_does_not_prevent_indexing(tmp_path, windows_metadata):
    root = tmp_path / "root"
    root.mkdir()
    path = root / "stable.txt"
    path.write_bytes(b"stable contents")
    with path.open("rb") as stream:
        by_path, by_fd = path.lstat(), os.fstat(stream.fileno())
        assert by_path.st_ctime_ns != by_fd.st_ctime_ns
        assert file_state.file_signature(by_path) == file_state.file_signature(by_fd)
    store = IndexStore(tmp_path / "state")
    try:
        identifier = store.register(str(root))
        result = store.scan(identifier)
        assert result["ok"], result
        assert store.summary()["roots"][0]["ready"]
        with store.connect(readonly=True) as db:
            row = db.execute("SELECT * FROM files WHERE path='/stable.txt'").fetchone()
        assert row["digest"] == hashlib.sha256(b"stable contents").hexdigest()
    finally:
        store.close()


def test_windows_handle_ctime_change_still_rejects_hash(tmp_path, windows_metadata, monkeypatch):
    path = tmp_path / "file.txt"
    path.write_bytes(b"contents")
    store = IndexStore(tmp_path / "state")
    original_fstat = os.fstat
    calls = 0
    def changed_fstat(fd):
        nonlocal calls
        info = original_fstat(fd)
        calls += 1
        if calls == 2:
            # Only handle ChangeTime changes: size, mtime, birthtime and ID stay fixed.
            info.st_ctime_ns += 1
        return info
    monkeypatch.setattr(os, "fstat", changed_fstat)
    try:
        with pytest.raises(OSError, match="文件仍在写入"):
            store.inspect(path, None)
    finally:
        store.close()


@pytest.mark.parametrize("changed", [False, True])
async def test_windows_auto_confirmation_keeps_handle_change_guard(running, monkeypatch, changed):
    service, root, connect, config, *_ = running
    await service.stop()
    # Apply the metadata adapter only after stopping the fixture's first scan.
    native_stat, native_lstat, native_fstat = os.stat, os.lstat, os.fstat
    monkeypatch.setattr(file_state, "_WINDOWS", True)
    monkeypatch.setattr(os, "stat", lambda *args, **kwargs: WindowsMetadata(native_stat(*args, **kwargs)))
    monkeypatch.setattr(os, "lstat", lambda *args, **kwargs: WindowsMetadata(native_lstat(*args, **kwargs)))
    monkeypatch.setattr(os, "fstat", lambda *args, **kwargs: WindowsMetadata(native_fstat(*args, **kwargs), descriptor=True))
    # AsyncSSH's reply encoder accepts stat_result or SFTPAttrs, not our
    # cross-platform proxy. Keep the test's metadata adapter off the wire.
    def protocol_fstat(self, file_obj):
        file_obj.flush()
        return asyncssh.SFTPAttrs.from_local(os.fstat(file_obj.fileno()))
    monkeypatch.setattr(RestrictedSFTP, "fstat", protocol_fstat)
    config.users[0].confirmation = "auto"
    await service.start(config)
    await ready(service)
    confirmations = []
    confirm = service.sync_api.auto_confirm
    def track(*args):
        confirmations.append(args)
        return confirm(*args)
    monkeypatch.setattr(service.sync_api, "auto_confirm", track)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        async with sftp.open("/hello.txt", "rb") as remote:
            assert await remote.read() == b"hello"
            if changed:
                # Restore mtime and preserve size; ChangeTime must still veto auto ACK.
                before = (root / "hello.txt").stat()
                await asyncio.sleep(.02)
                (root / "hello.txt").write_bytes(b"other")
                os.utime(root / "hello.txt", ns=(before.st_atime_ns, before.st_mtime_ns))
        assert bool(confirmations) is (not changed)
    with service.index.connect(readonly=True) as db:
        row = db.execute("SELECT * FROM user_files WHERE userid=? AND path='/hello.txt'", (account(service).userid,)).fetchone()
    assert (row["status"] == "done") is (not changed)
