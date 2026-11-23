import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from sync_server.config import ConfigStore, User
from sync_server.index import BATCH_SIZE, IndexStore
from sync_server.sync_api import SyncAPI, SyncError
from test_server import running
from test_sync import ready


TABLES = ("sync_users", "user_files", "sync_completed", "sync_offers")


def assert_removed(store, userid):
    with store.connect(readonly=True) as db:
        for table in TABLES:
            assert db.execute(f"SELECT count(*) FROM {table} WHERE userid=?", (userid,)).fetchone()[0] == 0


def test_deleted_identity_cleanup_and_same_name_fresh_progress(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep.txt").write_text("shared file")
    store = IndexStore(tmp_path / "state")
    api = SyncAPI(store)
    old = User("temp", "unused", str(root))
    disabled = User("disabled", "unused", str(root), enabled=False, sync_enabled=False)
    try:
        api.configure_users([old, disabled])
        assert store.scan(store.register(str(root)))["ok"]
        listed = api.dispatch(old, "list", {"userid": old.userid})
        assert listed["files"]
        api.ensure_user(disabled)
        with store.connect() as db:
            # More than one deletion batch, including orphan child rows.
            db.executemany("INSERT INTO sync_completed VALUES(?,?,?,?)",
                           [(old.userid, f"archived-{n}", 1, 1) for n in range(BATCH_SIZE + 1)])
            db.execute("INSERT INTO sync_completed VALUES('orphan','file',1,1)")
            db.execute("INSERT INTO sync_completed VALUES(?,?,1,1)", (disabled.userid, "retained"))
        new = User("temp", "unused", str(root))
        assert new.userid != old.userid
        api.configure_users([new, disabled])
        assert_removed(store, old.userid)
        assert_removed(store, "orphan")
        with store.connect(readonly=True) as db:
            assert db.execute("SELECT count(*) FROM sync_completed WHERE userid=?", (disabled.userid,)).fetchone()[0] == 1
        for operation in (lambda: api.ensure_user(old),
                          lambda: api.dispatch(old, "ack", {"userid": old.userid, "files": []}),
                          lambda: api.auto_confirm(old, {}, "127.0.0.1")):
            with pytest.raises(SyncError) as error:
                operation()
            assert error.value.code == "USER_SETTINGS_CHANGED"
        fresh = api.dispatch(new, "list", {"userid": new.userid})
        assert fresh["files"] == listed["files"]
        assert {u["userid"] for u in store.summary()["users"]} == {new.userid, disabled.userid}
        assert (root / "keep.txt").read_text() == "shared file"
        # Reconciliation is idempotent and preserves current progress.
        api.configure_users([new, disabled])
        assert api.dispatch(new, "list", {"userid": new.userid})["files"] == fresh["files"]
    finally:
        store.close()


def test_removal_waits_for_user_work_and_revokes_stale_worker(tmp_path, monkeypatch):
    store = IndexStore(tmp_path / "state")
    api = SyncAPI(store)
    user = User("temp", "unused", str(tmp_path))
    api.configure_users([user])
    api.ensure_user(user)
    entered = threading.Event()
    original = store.prune_users
    def observed(*args):
        entered.set()
        return original(*args)
    monkeypatch.setattr(store, "prune_users", observed)
    try:
        with ThreadPoolExecutor(1) as executor:
            lock = api._user_lock(user.userid)
            with lock:
                pending = executor.submit(api.configure_users, [])
                assert entered.wait(2)
                assert not pending.done()
                with pytest.raises(SyncError):
                    api.ensure_user(user)
            pending.result(5)
        assert_removed(store, user.userid)
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["restart", "reload", "stopped_cleanup"])
async def test_service_reconciles_removed_user(running, mode):
    service, root, connect, *_ = running
    await ready(service)
    old = service.config.users[0]
    await asyncio.to_thread(service.sync_api.dispatch, old, "list", {"userid": old.userid})
    updated = copy.deepcopy(service.config)
    replacement = copy.deepcopy(old)
    replacement.userid = User("unused", "unused", str(root)).userid
    updated.users[0] = replacement
    if mode == "reload":
        connection = await connect()
        try:
            await service.reload(updated)
            await asyncio.wait_for(connection.wait_closed(), 3)
        finally:
            connection.close()
            await connection.wait_closed()
    else:
        await service.stop()
        ConfigStore(service.data_dir).save(updated)
        if mode == "stopped_cleanup":
            result = await service.cleanup_users()
            assert old.userid not in {u["userid"] for u in result["users"]}
            store = IndexStore(service.data_dir)
            try:
                assert_removed(store, old.userid)
            finally:
                store.close()
        await service.start(updated)
    assert_removed(service.index, old.userid)
    with pytest.raises(SyncError):
        service.sync_api.ensure_user(old)
    await ready(service)
    async with await connect() as connection, connection.start_sftp_client() as sftp:
        async with sftp.open("/hello.txt", "rb") as file:
            assert await file.read() == b"hello"
    fresh = await asyncio.to_thread(service.sync_api.dispatch, service.config.users[0], "list", {"userid": replacement.userid})
    assert any(item["path"] == "/hello.txt" for item in fresh["files"])

