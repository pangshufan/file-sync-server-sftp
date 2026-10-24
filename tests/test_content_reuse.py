from pathlib import Path

from sync_server.client import PullClient
from test_server import running
from test_sync import ready, rescan


async def test_renamed_file_reuses_verified_local_content_before_delete(running, tmp_path):
    service, root, connect, *_ = running
    await ready(service)
    destination, cache = tmp_path / "downloads", tmp_path / "cache"
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        client = PullClient(sftp, destination, cache, delete=True, report=lambda *_: None)
        try:
            assert (await client.run_once())["complete"]
            (root / "hello.txt").rename(root / "renamed.txt")
            await rescan(service, root)
            opened = []
            original = sftp.open
            def track(path, *args, **kwargs):
                opened.append(path)
                return original(path, *args, **kwargs)
            sftp.open = track
            assert (await client.run_once())["complete"]
            assert (destination / "renamed.txt").read_text() == "hello"
            assert not (destination / "hello.txt").exists()
            assert "/renamed.txt" not in opened
        finally:
            client.close()


async def test_modified_local_reuse_candidate_falls_back_to_download(running, tmp_path):
    service, root, connect, *_ = running
    await ready(service)
    async with await connect() as conn, conn.start_sftp_client() as sftp:
        client = PullClient(sftp, tmp_path / "downloads", tmp_path / "cache", delete=True, report=lambda *_: None)
        try:
            assert (await client.run_once())["complete"]
            (client.destination / "hello.txt").write_text("wrong")
            (root / "hello.txt").rename(root / "renamed.txt")
            await rescan(service, root)
            opened = []
            original = sftp.open
            def track(path, *args, **kwargs):
                opened.append(path)
                return original(path, *args, **kwargs)
            sftp.open = track
            assert (await client.run_once())["complete"]
            assert "/renamed.txt" in opened
            assert (client.destination / "renamed.txt").read_text() == "hello"
        finally:
            client.close()
