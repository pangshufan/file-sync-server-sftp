"""Consistent, private pre-migration backups (including SQLite WAL contents)."""
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup(directory: Path, reason: str) -> Path:
    target = directory / "backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + reason)
    target.mkdir(parents=True, mode=0o700)
    target.parent.chmod(0o700)
    config = directory / "config.json"
    if config.exists():
        shutil.copyfile(config, target / "config.json")
        (target / "config.json").chmod(0o600)
    database = directory / "sync.sqlite3"
    if database.exists():
        with sqlite3.connect(database) as source, sqlite3.connect(target / "sync.sqlite3") as destination:
            source.backup(destination)
        (target / "sync.sqlite3").chmod(0o600)
    (target / "reason.json").write_text(json.dumps({"reason": reason, "format": 2}), encoding="utf-8")
    (target / "reason.json").chmod(0o600)
    return target
