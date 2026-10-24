"""Local SFTP list/ack + status latency while real files are rescanned; temporary data only."""
import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path

import asyncssh

from sync_server.config import Config, User, hash_password
from sync_server.index import root_id
from sync_server.server import SFTPService
from sync_server.transport import request


def summary(values):
    values = sorted(values)
    def percentile(p):
        return round(values[min(len(values) - 1, int((len(values) - 1) * p))] * 1000, 3)
    return {"count": len(values), "p50_ms": percentile(.5), "p95_ms": percentile(.95),
            "p99_ms": percentile(.99), "max_ms": round(max(values) * 1000, 3)} if values else {"count": 0}


async def benchmark(args):
    with tempfile.TemporaryDirectory(prefix="simple-sync-concurrency-") as temporary:
        folder = Path(temporary)
        root = folder / "source"
        root.mkdir()
        # Actual file IO, unlike benchmark_index.py's synthetic catalog rows.
        payload = b"a" * args.file_bytes
        for number in range(args.files):
            (root / f"file-{number:08d}.bin").write_bytes(payload)
        users = [User(f"bench{n}", hash_password("temporary-benchmark-password"), str(root)) for n in range(args.users)]
        config = Config(port=2222, users=users, auth_max_inflight=min(64, args.users))
        # Reserve an available loopback port; all state stays in this temporary tree.
        service = SFTPService(folder / "state")
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            config.port = sock.getsockname()[1]
        timings = {key: [] for key in ("list", "ack", "status")}
        try:
            await service.start(config)
            deadline = time.monotonic() + args.ready_timeout
            while True:
                state = await service.sync_status()
                if state["roots"] and all(row["ready"] for row in state["roots"]):
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("initial index timed out")
                await asyncio.sleep(.05)
            # Materialize queues before measuring contention, not cold bootstrap.
            for user in users:
                await asyncio.to_thread(service.sync_api.refresh, user)
            key = asyncssh.read_private_key(service.data_dir / "ssh_host_ed25519_key").convert_to_public()
            finish = time.monotonic() + args.seconds

            async def scanner():
                turn = 0
                while time.monotonic() < finish:
                    turn += 1
                    def modify():
                        for n in range(args.files):
                            (root / f"file-{n:08d}.bin").write_bytes(bytes([turn % 255]) * args.file_bytes)
                    await asyncio.to_thread(modify)
                    await asyncio.to_thread(service.index.scan, root_id(str(root)))

            async def poll():
                while time.monotonic() < finish:
                    before = time.monotonic()
                    await service.sync_status()
                    timings["status"].append(time.monotonic() - before)
                    await asyncio.sleep(.02)

            async def client(user):
                async with asyncssh.connect("127.0.0.1", config.port, username=user.username,
                        password="temporary-benchmark-password", known_hosts=([key], [], []),
                        client_keys=[], agent_path=None) as conn, conn.start_sftp_client() as sftp:
                    while time.monotonic() < finish:
                        before = time.monotonic()
                        page = await request(sftp, "list", {"userid": user.userid, "limit": 50})
                        timings["list"].append(time.monotonic() - before)
                        if page["files"]:
                            items = [{"file_id": row["file_id"], "version": row["version"], "status": "success", "sha256": row["sha256"]} for row in page["files"]]
                            before = time.monotonic()
                            # Metadata acknowledgement workload, not a download throughput test.
                            await request(sftp, "ack", {"userid": user.userid, "files": items})
                            timings["ack"].append(time.monotonic() - before)
                        await asyncio.sleep(.02)
            await asyncio.gather(scanner(), poll(), *(client(user) for user in users))
            result = {"files": args.files, "file_bytes": args.file_bytes, "users": args.users,
                      "measurement_seconds": args.seconds, "latency": {key: summary(values) for key, values in timings.items()},
                      "database": service.index.metrics()}
        finally:
            await service.stop()
        print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=2000)
    parser.add_argument("--users", type=int, default=4)
    parser.add_argument("--file-bytes", type=int, default=1024)
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--ready-timeout", type=int, default=300)
    args = parser.parse_args()
    if not (1 <= args.files <= 1000000 and 1 <= args.users <= 64 and 1 <= args.seconds <= 3600
            and 1 <= args.file_bytes <= 16 * 1024 * 1024 and args.ready_timeout > 0):
        parser.error("invalid workload size")
    asyncio.run(benchmark(args))


if __name__ == "__main__":
    main()
