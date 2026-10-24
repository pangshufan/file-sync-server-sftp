"""Bounded asynchronous JSONL runtime log, with private rotating files."""
import json
import logging
import logging.handlers
import os
import queue
from datetime import datetime, timezone


class PrivateRotatingHandler(logging.handlers.RotatingFileHandler):
    errors = 0

    def handleError(self, record):
        self.errors += 1

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(self.baseFilename, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")


class BlockingStopListener(logging.handlers.QueueListener):
    def enqueue_sentinel(self):
        self.queue.put(self._sentinel)


class AuditLog:
    def __init__(self, directory, *, max_bytes=5 * 1024 * 1024, backups=5):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.handler = PrivateRotatingHandler(directory / "server.jsonl", maxBytes=max_bytes,
                                              backupCount=backups, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.queue = queue.Queue(maxsize=2048)
        self.listener = BlockingStopListener(self.queue, self.handler)
        self.dropped = 0
        self.closed = False
        self.listener.start()

    def record(self, event, **fields):
        if self.closed:
            return
        allowed = {"userid", "username", "ip", "connection", "path", "operation", "result", "bytes", "version", "detail", "port"}
        item = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event}
        item.update({key: value[:2000] if isinstance(value, str) else value
                     for key, value in fields.items() if key in allowed})
        record = logging.LogRecord("simple-sync", logging.INFO, "", 0,
                                   json.dumps(item, ensure_ascii=False), (), None)
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def close(self):
        if not self.closed:
            self.record("log_shutdown", detail=f"dropped_records={self.dropped}")
            self.closed = True
            self.listener.stop()
            self.handler.close()

    def metrics(self):
        return {"dropped_records": self.dropped, "write_errors": self.handler.errors}
