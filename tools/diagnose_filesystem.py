"""Report native stat/fstat differences using a temporary file, without Qt."""
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

from sync_server.file_state import file_signature


def main():
    fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_birthtime_ns", "st_ino", "st_dev")
    with tempfile.TemporaryDirectory(prefix="simple-sync-stat-") as temporary:
        path = Path(temporary) / "sample.txt"
        path.write_bytes(b"before")
        path.write_bytes(b"after")
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        with path.open("rb") as source:
            by_path, by_fd = path.lstat(), os.fstat(source.fileno())
        print(json.dumps({"platform": platform.platform(), "python": sys.version,
                          "path": {field: getattr(by_path, field, None) for field in fields},
                          "descriptor": {field: getattr(by_fd, field, None) for field in fields},
                          "catalog_signatures_match": file_signature(by_path) == file_signature(by_fd)}, indent=2))


if __name__ == "__main__":
    main()
