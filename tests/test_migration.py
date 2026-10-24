import hashlib
import json
import sqlite3
import time
from dataclasses import asdict

import pytest

from sync_server.config import Config, ConfigStore, User, hash_password
from sync_server.index import IndexStore
from sync_server.sync_api import SyncAPI


def test_legacy_migration_backups_and_stable_userid(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'legacy.txt').write_text('legacy')
    folder = tmp_path / 'state'
    store = IndexStore(folder)
    identifier = store.register(str(root))
    assert store.scan(identifier)['ok']
    with store.connect() as db:
        version = db.execute('SELECT version FROM files').fetchone()[0]
        db.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY,username TEXT,device TEXT,name TEXT,scope TEXT,root TEXT,policy TEXT,created REAL,last_seen REAL)")
        db.execute("CREATE TABLE task_files(task TEXT,path TEXT,version INTEGER,operation TEXT,kind TEXT,updated REAL)")
        db.execute("INSERT INTO tasks(id,username,device,name,scope,root,policy,created,last_seen) VALUES('old','alice','device','name','/',?,'legacy',1,1)", (identifier,))
        db.execute("INSERT INTO task_files VALUES('old','/legacy.txt',?,'upsert','file',1)", (version,))
        db.execute('DROP TABLE catalog')
        db.execute('PRAGMA user_version=1')
    legacy = {'host':'127.0.0.1', 'port':2222, 'users':[
        {'username':'alice', 'password_hash':hash_password('password'), 'root':str(root)}]}
    (folder/'config.json').write_text(json.dumps(legacy))
    config = ConfigStore(folder).load()
    userid = config.users[0].userid
    assert len(userid) == 32
    assert ConfigStore(folder).load().users[0].userid == userid
    restored = IndexStore(folder)
    api = SyncAPI(restored)
    files = api.dispatch(config.users[0], 'list', {'userid':userid})['files']
    assert len(files) == 1  # old device's success was deliberately not merged
    assert files[0]['version'] == str(version)
    assert files[0]['sha256'] == hashlib.sha256(b'legacy').hexdigest()
    backups = list((folder/'backups').iterdir())
    assert len(backups) == 2
    old_config = json.loads(next((folder/'backups').glob('*config-v4/config.json')).read_text())
    assert 'userid' not in old_config['users'][0]
    old_database = next((folder/'backups').glob('*database-v3/sync.sqlite3'))
    with sqlite3.connect(old_database) as db:
        assert db.execute("SELECT id FROM tasks").fetchone()[0] == 'old'
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
    IndexStore(folder)
    assert len(list((folder/'backups').iterdir())) == 2
    config.users[0].username = 'alice-renamed'
    ConfigStore(folder).save(config)
    assert ConfigStore(folder).load().users[0].userid == userid


def test_configuration_defaults_and_validation(tmp_path):
    root = tmp_path/'root'
    root.mkdir()
    user = User('test', hash_password('password'), str(root))
    config = Config(users=[user])
    config.validate()
    assert user.single_connection and user.confirmation == 'manual'
    user.allowed_ips = ['not-an-ip']
    with pytest.raises(ValueError, match='IP'):
        config.validate()
    user.allowed_ips = []
    user.sync_start = float('nan')
    with pytest.raises(ValueError, match='时间'):
        config.validate()
    user.sync_start = 0
    user.sync_directories = ['/../escape']
    with pytest.raises(ValueError):
        config.validate()


def test_v2_upgrade_removes_legacy_tables_with_backup_and_preserves_progress(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    folder = tmp_path / "state"
    store = IndexStore(folder)
    with store.connect() as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='tasks'").fetchone()
        db.execute("CREATE TABLE tasks(id TEXT)")
        db.execute("INSERT INTO tasks VALUES('legacy')")
        db.execute("INSERT INTO sync_completed VALUES('user','file',7,1)")
        db.execute("PRAGMA user_version=2")
    store.close()
    upgraded = IndexStore(folder)
    with upgraded.connect(readonly=True) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT version FROM sync_completed").fetchone()[0] == 7
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='tasks'").fetchone()
    backup = next((folder / "backups").glob("*database-v3/sync.sqlite3"))
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT id FROM tasks").fetchone()[0] == "legacy"
    upgraded.close()
