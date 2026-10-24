import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import asyncssh
import pytest

from sync_server.audit import AuditLog
from sync_server.auth_limits import AuthLimits
from sync_server.config import Config, ConfigStore
from sync_server.index import BATCH_SIZE, IndexStore
from test_server import running


def test_wal_reader_does_not_wait_for_writer_and_connections_reuse(tmp_path):
    store = IndexStore(tmp_path / "state")
    root = tmp_path / "root"
    root.mkdir()
    identifier = store.register(str(root))
    entered, release = threading.Event(), threading.Event()
    def writer():
        with store.connect() as db:
            db.execute("UPDATE roots SET entries=99 WHERE id=?", (identifier,))
            entered.set()
            assert release.wait(5)
    try:
        with ThreadPoolExecutor(2) as executor:
            job = executor.submit(writer)
            try:
                assert entered.wait(2)
                # Read sees committed data while the writer's transaction stays open.
                state = executor.submit(store.summary).result(2)
                assert state["roots"][0]["entries"] == 0
                with store.connect(readonly=True) as db:
                    with pytest.raises(sqlite3.OperationalError, match="readonly"):
                        db.execute("DELETE FROM roots")
            finally:
                release.set()
            job.result(2)
        assert store.summary()["roots"][0]["entries"] == 99
        with store.connect() as first:
            connection = id(first)
        with pytest.raises(RuntimeError, match="rollback"):
            with store.connect() as db:
                assert id(db) == connection
                db.execute("UPDATE roots SET entries=123")
                raise RuntimeError("rollback")
        assert store.summary()["roots"][0]["entries"] == 99
    finally:
        store.close()


def test_auth_budgets_expire_and_do_not_evict_active_keys():
    clock = [0.0]
    config = Config(auth_max_inflight=1, auth_ip_attempts=2, auth_user_attempts=2, auth_window_seconds=10)
    guard = AuthLimits(config, clock=lambda: clock[0])
    assert guard.begin("127.0.0.1", "alice") is None
    assert guard.begin("127.0.0.2", "bob") == "busy"
    guard.finish()
    assert guard.begin("::ffff:127.0.0.1", "alice") is None
    guard.finish()
    assert guard.begin("127.0.0.1", "bob") == "rate_limit"
    assert guard.begin("127.0.0.3", "alice") == "rate_limit"
    clock[0] = 10.1
    assert guard.begin("127.0.0.1", "alice") is None
    guard.finish()


def test_private_rotating_json_logs_and_field_filter(tmp_path):
    log = AuditLog(tmp_path / "logs", max_bytes=400, backups=2)
    for number in range(30):
        log.record("test", username="alice\nextra", detail=f"entry {number}", password="SECRET")
    log.close()
    paths = list((tmp_path / "logs").glob("server.jsonl*"))
    assert 1 <= len(paths) <= 3
    text = "".join(path.read_text() for path in paths)
    assert "SECRET" not in text
    records = [json.loads(line) for line in text.splitlines()]
    assert records and all("timestamp" in row for row in records)
    import os
    if os.name != "nt":
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)


def test_auth_settings_persist_and_validate(tmp_path):
    store = ConfigStore(tmp_path)
    config = Config(auth_ip_attempts=7, auth_user_attempts=3)
    store.save(config)
    assert store.load().auth_ip_attempts == 7
    config.auth_max_inflight = 0
    with pytest.raises(ValueError, match="auth_max_inflight"):
        config.validate()


async def test_auth_limits_apply_across_connections_and_logs_survive_stop(running):
    service, _, connect, config, *_ = running
    await service.stop()
    config.auth_user_attempts = 1
    config.auth_window_seconds = 1
    await service.start(config)
    with pytest.raises(asyncssh.PermissionDenied):
        await connect(password="wrong")
    with pytest.raises((asyncssh.Error, OSError)):
        await connect()
    await asyncio.sleep(1.05)
    async with await connect():
        pass
    await service.stop()
    rows = [json.loads(line) for line in (service.data_dir / "logs/server.jsonl").read_text().splitlines()]
    assert any(row["event"] == "auth_rejected" and row["result"] == "rate_limit" for row in rows)
    assert any(row["event"] == "login" and row["username"] == "admin" for row in rows)
    assert "test-password" not in json.dumps(rows)


async def test_cancelled_auth_keeps_hash_budget_until_worker_finishes(monkeypatch):
    from types import SimpleNamespace
    from sync_server.server import AuthServer
    entered, release = threading.Event(), threading.Event()
    def slow_verify(*_):
        entered.set()
        release.wait(5)
        return False
    monkeypatch.setattr("sync_server.server.verify_password", slow_verify)
    config = Config(auth_max_inflight=1)
    service = SimpleNamespace(config=config, config_epoch=0, auth_limits=AuthLimits(config),
                              auth_tasks=set(), dummy_hash="dummy", audit_event=lambda *_args, **_kw: None,
                              emit=lambda *_args: None)
    connection = SimpleNamespace(get_extra_info=lambda *_: ("127.0.0.1", 1), close=lambda: None)
    auth = AuthServer(service)
    auth.connection = connection
    pending = asyncio.create_task(auth.validate_password("missing", "password"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert service.auth_limits.inflight == 1
        assert not await auth.validate_password("missing", "password")
    finally:
        release.set()
        await asyncio.gather(*list(service.auth_tasks))
        await asyncio.sleep(0)
    assert service.auth_limits.inflight == 0


def test_settings_reconciliation_commits_pages_and_resumes(tmp_path):
    import hashlib
    import time
    from sync_server.config import User
    from sync_server.sync_api import SyncAPI
    root = tmp_path / "source"
    root.mkdir()
    store = IndexStore(tmp_path / "state")
    identifier = store.register(str(root))
    user = User("alice", "unused", str(root))
    api = SyncAPI(store)
    data = ("file", 1, 1, 1, 1, hashlib.sha256(b"a").hexdigest(), time.time())
    with store.connect() as db:
        for number in range(BATCH_SIZE * 3):
            store.record(db, identifier, f"/{number}.txt", data, "g")
        db.execute("UPDATE roots SET ready=1")
    api.refresh(user)
    user.sync_generation += 1
    before = store.metrics()["write_operations"]
    # Fail after a committed page, before the new policy is recorded.
    original = api.counts
    calls = [0]
    def interrupted(*args):
        calls[0] += 1
        if calls[0] == BATCH_SIZE + 1:
            raise RuntimeError("interrupted")
        return original(*args)
    api.counts = interrupted
    with pytest.raises(RuntimeError, match="interrupted"):
        api.ensure_user(user)
    with store.connect(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM user_files").fetchone()[0] == BATCH_SIZE * 2
        assert db.execute("SELECT generation FROM sync_users").fetchone()[0] == 0
    api.counts = original
    api.refresh(user)
    assert store.metrics()["write_operations"] - before > 3
    with store.connect(readonly=True) as db:
        row = db.execute("SELECT * FROM sync_users").fetchone()
        assert row["generation"] == 1
        assert row["pending_count"] == BATCH_SIZE * 3
        assert db.execute("SELECT count(*) FROM user_files").fetchone()[0] == row["pending_count"]
    store.close()
