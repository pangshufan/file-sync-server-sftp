"""Synthetic SQLite index/incremental queue benchmark; does not create source files."""
import argparse
import hashlib
import json
import resource
import sys
import tempfile
import time
from pathlib import Path

from sync_server.config import User
from sync_server.index import IndexStore
from sync_server.sync_api import SyncAPI


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=100000)
    args = parser.parse_args()
    if not 1 <= args.files <= 10000000:
        parser.error("files must be between 1 and 10000000")
    with tempfile.TemporaryDirectory(prefix="simple-sync-benchmark-") as temporary:
        folder = Path(temporary)
        root = folder / "source"
        root.mkdir()
        store = IndexStore(folder / "state")
        identifier = store.register(str(root))
        data = ("file", 4, 1, 1, 1, hashlib.sha256(b"data").hexdigest(), time.time())
        started = time.monotonic()
        for first in range(0, args.files, 1000):
            with store.connect() as db:
                for number in range(first, min(first + 1000, args.files)):
                    store.record(db, identifier, f"/group-{number // 1000:06d}/file-{number:09d}.bin", data, "benchmark")
        indexing = time.monotonic() - started
        with store.connect() as db:
            db.execute("UPDATE roots SET ready=1 WHERE id=?", (identifier,))
        api = SyncAPI(store)
        user = User("bench", "unused", str(root))
        started = time.monotonic()
        page = api.dispatch(user, "list", {"userid": user.userid, "limit": 200})
        manifest = time.monotonic() - started
        started = time.monotonic()
        api.dispatch(user, "list", {"userid": user.userid, "limit": 200, "page_token": page["next_page_token"]})
        next_page = time.monotonic() - started
        with store.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform != "darwin":
            memory *= 1024
        print(json.dumps({"synthetic_files": args.files, "index_seconds": round(indexing, 3),
                          "initial_queue_seconds": round(manifest, 3), "next_page_seconds": round(next_page, 4),
                          "database_mib": round(store.path.stat().st_size / 1024**2, 2),
                          "process_peak_mib": round(memory / 1024**2, 2)}, indent=2))


if __name__ == "__main__":
    main()
