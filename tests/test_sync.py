import asyncio
import hashlib
import importlib.util
import os
import sqlite3
import time
from pathlib import Path

import asyncssh
import pytest

from test_server import running
from sync_server.client import PullClient
from sync_server.config import ConfigStore, User, DirectoryRule
from sync_server.index import IndexStore, root_id
from sync_server.sync_api import SyncError
from sync_server.transport import request


async def ready(service):
    for _ in range(120):
        state = await service.sync_status()
        if state['roots'] and all(row['ready'] for row in state['roots']):
            return
        await asyncio.sleep(.05)
    pytest.fail('index did not become ready')


async def page(sftp, **args):
    info = await request(sftp, 'info', {})
    return await request(sftp, 'list', {'userid': info['userid'], **args})


async def rescan(service, root):
    assert (await asyncio.to_thread(service.index.scan, root_id(str(root))))['ok']
    for user in service.config.users:
        if user.enabled and user.sync_enabled:
            await asyncio.to_thread(service.sync_api.refresh, user)


def account(service, name='admin'):
    return next(user for user in service.config.users if user.username == name)


def state(service, user):
    with service.index.connect() as db:
        return dict(db.execute('SELECT * FROM sync_users WHERE userid=?', (user.userid,)).fetchone())


async def confirm(sftp, userid, entries, status='success'):
    return await request(sftp, 'ack', {'userid': userid, 'files': [
        {'file_id': e['file_id'], 'version': e['version'], 'sha256': e['sha256'], 'status': status,
         'error': 'simulated failure'} for e in entries]})


async def test_userid_sdk_incremental_and_shared_progress(running, tmp_path):
    service, root, connect, *_ = running
    await ready(service)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        info = await request(sftp, 'info', {})
        assert info['protocol'] == '2'
        assert info['userid'] == account(service).userid
        one = PullClient(sftp, tmp_path/'dest1', tmp_path/'state1', report=lambda *_: None)
        two = PullClient(sftp, tmp_path/'dest2', tmp_path/'state2', report=lambda *_: None)
        try:
            assert (await one.run_once(page_size=1))['complete']
            assert (tmp_path/'dest1/hello.txt').read_text() == 'hello'
            assert (await two.run_once())['succeeded'] == 0  # same userid = shared progress
            (root/'hello.txt').write_text('new content')
            (root/'added.txt').write_text('added')
            await rescan(service, root)
            result = await one.run_once(page_size=1)
            assert result['complete'], result
            assert (tmp_path/'dest1/hello.txt').read_text() == 'new content'
            assert (await one.run_once())['succeeded'] == 0
        finally:
            one.close()
            two.close()


async def test_single_connection_and_independent_users(running):
    service, _, connect, *_ = running
    async with await connect() as first:
        with pytest.raises(asyncssh.PermissionDenied):
            await connect()
        async with await connect('other') as second:
            assert not second.is_closed() and not first.is_closed()
    await asyncio.sleep(.05)
    results = await asyncio.gather(connect(), connect(), return_exceptions=True)
    connections = [r for r in results if not isinstance(r, Exception)]
    assert len(connections) == 1
    for conn in connections:
        conn.close()
        await conn.wait_closed()


async def test_ip_allowlist_and_multi_connection_option(running):
    service, _, connect, *_ = running
    user = account(service)
    user.allowed_ips = ['192.0.2.0/24']
    with pytest.raises(asyncssh.PermissionDenied):
        await connect()
    user.allowed_ips = ['127.0.0.0/8']
    user.single_connection = False
    async with await connect() as one, await connect() as two:
        assert not one.is_closed() and not two.is_closed()
    assert user.ip_allowed('::ffff:127.0.0.1')
    user.allowed_ips = ['2001:db8::/32']
    assert user.ip_allowed('2001:db8::1')
    assert not user.ip_allowed('::1')


async def test_scope_patterns_permissions_and_userid_ownership(running):
    service, root, connect, *_ = running
    user = account(service, 'reader')
    user.sync_directories = ['/private', '/']
    user.include_patterns = ['*.txt']
    user.exclude_patterns = ['drop/**']
    await ready(service)
    async with await connect('reader') as conn, conn.start_sftp_client() as sftp:
        result = await page(sftp)
        assert any(e['path'] == '/hello.txt' for e in result['files'])
        assert not any(e['path'].startswith('/private') or e['path'].startswith('/drop') for e in result['files'])
        with pytest.raises(SyncError) as error:
            await request(sftp, 'list', {'userid': account(service).userid})
        assert error.value.code == 'USER_MISMATCH'
        user.sync_enabled = False
        with pytest.raises(SyncError) as error:
            await page(sftp)
        assert error.value.code == 'SYNC_DISABLED'
        async with sftp.open('/hello.txt') as file:
            assert await file.read() == 'hello'  # regular SFTP unaffected


async def test_manual_download_does_not_confirm_and_ack_is_idempotent(running):
    service, _, connect, *_ = running
    await ready(service)
    user = account(service)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        original = await page(sftp)
        async with sftp.open('/hello.txt') as file:
            await file.read()
        assert state(service, user)['done_count'] == 0
        item = next(e for e in original['files'] if e['path'] == '/hello.txt')
        bad = {**item, 'sha256': 'wrong'}
        with pytest.raises(SyncError) as error:
            await confirm(sftp, user.userid, [bad])
        assert error.value.code == 'CHECKSUM_MISMATCH'
        await confirm(sftp, user.userid, [item])
        before = state(service, user)
        await confirm(sftp, user.userid, [item])
        assert state(service, user)['done_count'] == before['done_count'] == 1


async def test_old_ack_keeps_new_version_pending_and_failed_entries_survive(running):
    service, root, connect, *_ = running
    await ready(service)
    user = account(service)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        old = next(e for e in (await page(sftp))['files'] if e['path'] == '/hello.txt')
        await confirm(sftp, user.userid, [old], 'failed')
        old_processed = state(service, user)['processed']
        (root/'hello.txt').write_text('updated')
        await rescan(service, root)
        latest = next(e for e in (await page(sftp))['files'] if e['path'] == '/hello.txt')
        assert int(latest['version']) > int(old['version'])
        await confirm(sftp, user.userid, [latest], 'failed')
        await confirm(sftp, user.userid, [old])
        assert state(service, user)['failed_count'] == 1
        assert any(e['version'] == latest['version'] for e in (await page(sftp))['files'])
        assert state(service, user)['processed'] > old_processed
        await confirm(sftp, user.userid, [latest])
        assert not any(e['path'] == '/hello.txt' for e in (await page(sftp))['files'])


async def test_start_time_preserve_restart_and_server_timestamps(running):
    service, root, connect, *_ = running
    await ready(service)
    user = account(service)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        original = await page(sftp)
        count = original['pending']
        user.sync_start = time.time() + 3600
        assert (await page(sftp))['pending'] == count  # preserve unfinished entries
        user.sync_generation += 1
        assert (await page(sftp))['pending'] == 0
        user.sync_start = 0
        assert (await page(sftp))['pending'] == count
        earliest = time.time()
        (root/'hello.txt').write_text('mtime is not business time')
        os.utime(root/'hello.txt', (1, 1))
        await rescan(service, root)
        item = next(e for e in (await page(sftp))['files'] if e['path'] == '/hello.txt')
        assert item['updated_at'] >= earliest
        assert 'mtime_ns' not in item


async def test_pagination_token_binding_and_changes_during_paging(running):
    service, root, connect, *_ = running
    await ready(service)
    async with await connect() as c1, c1.start_sftp_client() as s1:
        first = await page(s1, limit=1)
        assert first['has_more']
        token = first['next_page_token']
        (root/'new.txt').write_text('after first page')
        await rescan(service, root)
        seen = {e['file_id'] for e in first['files']}
        while token:
            next_page = await page(s1, limit=1, page_token=token)
            seen.update(e['file_id'] for e in next_page['files'])
            token = next_page['next_page_token']
        assert any(e['path'] == '/new.txt' for e in (await page(s1))['files'])
        async with await connect('other') as c2, c2.start_sftp_client() as s2:
            with pytest.raises(SyncError) as error:
                await page(s2, page_token=first['next_page_token'])
            assert error.value.code == 'BAD_TOKEN'
        account(service).sync_start = time.time()
        with pytest.raises(SyncError):
            await page(s1, page_token=first['next_page_token'])


@pytest.mark.parametrize('action', ['partial', 'overlap', 'eof', 'two_handles', 'disconnect', 'changed', 'copy_data', 'complete', 'empty'])
async def test_auto_confirmation_requires_full_normal_single_handle(running, tmp_path, action):
    service, root, connect, *_ = running
    await ready(service)
    user = account(service)
    user.confirmation = 'auto'
    if action == 'empty':
        (root/'hello.txt').write_bytes(b'')
        await rescan(service, root)
    conn = await connect()
    sftp = await conn.start_sftp_client()
    try:
        file = await sftp.open('/hello.txt', 'rb')
        if action == 'partial':
            await file.read(2)
        elif action == 'overlap':
            await file.read(3)
            await file.seek(0)
            await file.read(3)
        elif action == 'eof':
            await file.seek(5)
            await file.read()
        elif action == 'two_handles':
            await file.read(3)
            async with sftp.open('/hello.txt', 'rb') as second:
                await second.seek(3)
                await second.read()
        elif action == 'changed':
            await file.read()
            (root/'hello.txt').write_text('changed during transfer')
            await rescan(service, root)
        elif action == 'copy_data':
            async with sftp.open('/server-copy.txt', 'wb') as destination:
                await sftp._handler.copy_data(file._handle, 0, 0, destination._handle, 0)
        else:
            await file.read()
        if action == 'disconnect':
            conn.abort()
            await conn.wait_closed()
            await asyncio.sleep(.1)
        else:
            await file.close()
        with service.index.connect() as db:
            row = db.execute("SELECT * FROM user_files WHERE userid=? AND path='/hello.txt'", (user.userid,)).fetchone()
        assert (row['status'] == 'done') == (action in ('complete', 'empty'))
        if action in ('complete', 'empty'):
            assert row['confirmation'] == 'auto' and row['ip'] == '127.0.0.1'
    finally:
        conn.close()
        await conn.wait_closed()


async def test_delete_events_require_explicit_confirmation_and_sdk_resume(running, tmp_path):
    service, root, connect, *_ = running
    await ready(service)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        client = PullClient(sftp, tmp_path/'dest', tmp_path/'state', delete=True, report=lambda *_: None)
        try:
            assert (await client.run_once())['complete']
            (root/'hello.txt').unlink()
            await rescan(service, root)
            deleted = next(e for e in (await page(sftp))['files'] if e['path'] == '/hello.txt')
            assert deleted['operation'] == 'delete'
            assert (await client.run_once())['complete']
            assert not (tmp_path/'dest/hello.txt').exists()
            content = b'resume-content-' * 100000
            (root/'large.bin').write_bytes(content)
            await rescan(service, root)
            entry = next(e for e in (await page(sftp))['files'] if e['path'] == '/large.bin')
            key = hashlib.sha256((entry['path'] + ':' + entry['version']).encode()).hexdigest()
            (tmp_path/'state'/f'{key}.part').write_bytes(content[:500000])
            assert (await client.run_once())['complete']
            assert (tmp_path/'dest/large.bin').read_bytes() == content
        finally:
            client.close()


async def test_restart_cleanup_and_expired_change_log_do_not_lose_user_states(running, tmp_path):
    service, root, connect, config, *_ = running
    await ready(service)
    user = account(service)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        listing = await page(sftp)
        await confirm(sftp, user.userid, listing['files'])
    with service.index.connect() as db:
        db.execute('UPDATE user_files SET confirmed_at=1')
        db.execute('UPDATE changes SET created=1')
    service.sync_api.cleanup(30)
    assert state(service, user)['archived_count'] == len(listing['files'])
    await asyncio.to_thread(service.index.prune, 30)
    await service.stop()
    await service.start(config)
    await ready(service)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        assert not (await page(sftp))['files']
        (root/'hello.txt').write_text('after restart and archive')
        await rescan(service, root)
        result = await page(sftp)
        assert len(result['files']) == 1 and result['files'][0]['path'] == '/hello.txt'
        assert state(service, account(service))['pending_count'] == 1


async def test_watcher_updates_catalog(running):
    service, root, _, *_ = running
    await ready(service)
    (root/'watcher.txt').write_text('native change')
    for _ in range(160):
        with service.index.connect() as db:
            row = db.execute("SELECT * FROM catalog WHERE root=? AND path='/watcher.txt'", (root_id(str(root)),)).fetchone()
        if row:
            assert row['digest'] == hashlib.sha256(b'native change').hexdigest()
            return
        await asyncio.sleep(.05)
    pytest.fail('watcher did not update catalog')


async def test_paramiko_standard_get_with_v2_extensions(running, tmp_path):
    paramiko = pytest.importorskip('paramiko')
    import base64
    service, _, _, config, *_ = running
    await ready(service)
    spec = importlib.util.spec_from_file_location('example', Path(__file__).parents[1]/'examples/paramiko_pull.py')
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    public = asyncssh.read_private_key(service.data_dir/'ssh_host_ed25519_key').export_public_key().split()[1]
    hostkey = paramiko.Ed25519Key(data=base64.b64decode(public))
    def third_party():
        with paramiko.Transport(('127.0.0.1', config.port)) as transport:
            transport.connect(hostkey=hostkey, username='reader', password='test-password')
            with paramiko.SFTPClient.from_transport(transport) as sftp:
                return example.pull(sftp, tmp_path/'third-party')
    assert (await asyncio.to_thread(third_party))['complete']
    assert (tmp_path/'third-party/hello.txt').read_text() == 'hello'
    assert not (tmp_path/'third-party/private').exists()


def test_missing_or_incomplete_root_scan_never_publishes_deletions(tmp_path, monkeypatch):
    root = tmp_path/'root'
    root.mkdir()
    (root/'a.txt').write_text('a')
    (root/'b.txt').write_text('b')
    store = IndexStore(tmp_path/'state')
    identifier = store.register(str(root))
    assert store.scan(identifier)['ok']
    inspect = store.inspect
    def inaccessible(path, old, force=False):
        if path.name == 'b.txt':
            raise PermissionError('denied')
        return inspect(path, old, force)
    monkeypatch.setattr(store, 'inspect', inaccessible)
    assert not store.scan(identifier)['ok']
    root.rename(tmp_path/'offline')
    assert not store.scan(identifier)['ok']
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM catalog WHERE operation='delete'").fetchone()


async def test_malformed_requests_and_disabled_legacy_extensions(running):
    from asyncssh.packet import String
    from asyncssh.constants import FXP_EXTENDED_REPLY
    from sync_server.transport import extension_name
    import json
    service, _, connect, *_ = running
    await ready(service)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        uid = account(service).userid
        for args in ({'userid':uid,'limit':201}, {'userid':uid,'file_ids':[]}, {'userid':uid,'page_token':'bad'}):
            with pytest.raises(SyncError):
                await request(sftp, 'list', args)
        with pytest.raises(SyncError):
            await request(sftp, 'ack', {'userid':uid,'files':[{'file_id':[], 'version':'x'}]})
        handler = sftp._handler
        name = extension_name('info')
        handler._return_types = {**handler._return_types, name: FXP_EXTENDED_REPLY}
        for raw in (b'{bad-json', b'x' * 65537):
            response = await handler._make_request(name, String(raw))
            assert json.loads(response.get_string())['error']['code'] == 'BAD_REQUEST'
        with pytest.raises(asyncssh.SFTPOpUnsupported):
            await handler._make_request(b'sync-info@simple-sync.invalid', String(b'{}'))
        assert 'hello.txt' in await sftp.listdir('/')


async def test_type_changes_and_local_symlink_protection(running, tmp_path):
    service, root, connect, *_ = running
    await ready(service)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        client = PullClient(sftp, tmp_path/'dest', tmp_path/'state', delete=True, report=lambda *_: None)
        try:
            assert (await client.run_once())['complete']
            (root/'private/secret.txt').unlink()
            (root/'private').rmdir()
            (root/'private').write_text('directory became file')
            (root/'hello.txt').unlink()
            (root/'hello.txt').mkdir()
            (root/'hello.txt/inside').write_text('file became directory')
            await rescan(service, root)
            result = await client.run_once(page_size=1)
            assert result['complete'], result
            assert (tmp_path/'dest/private').read_text() == 'directory became file'
            assert (tmp_path/'dest/hello.txt/inside').read_text() == 'file became directory'
            outside = tmp_path/'outside'
            outside.mkdir()
            (tmp_path/'dest/link').symlink_to(outside)
            with pytest.raises(ValueError):
                client.local_path('/link/escape')
        finally:
            client.close()


async def test_status_counters_match_rows_after_transitions(running):
    service, root, connect, *_ = running
    await ready(service)
    user = account(service)
    def check():
        with service.index.connect() as db:
            counts = dict(db.execute('SELECT status,count(*) FROM user_files WHERE userid=? GROUP BY status', (user.userid,)))
            cached = state(service, user)
            assert cached['pending_count'] == counts.get('pending',0) + counts.get('failed',0)
            assert cached['failed_count'] == counts.get('failed',0)
            assert cached['done_count'] == counts.get('done',0)
            assert cached['archived_count'] == db.execute('SELECT count(*) FROM sync_completed WHERE userid=?', (user.userid,)).fetchone()[0]
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        items = (await page(sftp))['files']
        check()
        await confirm(sftp, user.userid, items, 'failed')
        check()
        await confirm(sftp, user.userid, items)
        check()
        with service.index.connect() as db:
            db.execute('UPDATE user_files SET confirmed_at=1')
        service.sync_api.cleanup(30)
        check()
        (root/'hello.txt').write_text('updated')
        await rescan(service, root)
        await page(sftp)
        check()
        user.sync_start = time.time()+100
        user.sync_generation += 1
        await page(sftp)
        check()
