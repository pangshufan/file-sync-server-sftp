"""Native events + debounced bounded work queue + periodic reconciliation."""

import asyncio
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .i18n import tr
from .index import root_id


class Events(FileSystemEventHandler):
    def __init__(self, monitor, root):
        self.monitor, self.root = monitor, Path(root)

    def on_any_event(self, event):
        if event.event_type in ("opened", "closed_no_write") or (event.is_directory and event.event_type == "modified"):
            return
        for value in (event.src_path, getattr(event, "dest_path", None)):
            if value:
                try:
                    relative = Path(value).relative_to(self.root).as_posix()
                    self.monitor.dirty(root_id(str(self.root)), "/" + relative if relative != "." else "/")
                except ValueError:
                    pass


class Monitor:
    def __init__(self, store, config, emit, sync_api=None):
        self.store, self.config, self.emit = store, config, emit
        self.sync_api = sync_api
        self.roots = list(dict.fromkeys(user.root for user in config.users if user.enabled))
        self.observer = Observer()
        self.lock = threading.Lock()
        self.queue = {}
        self.last_event = {}
        self.first_event = {}
        self.task = None
        self.last_full = {}
        self.last_audit = 0
        self.last_prune = 0

    def dirty(self, identifier, path):
        with self.lock:
            paths = self.queue.setdefault(identifier, set())
            if len(paths) > 10000 or path == "/":
                paths.clear()
                paths.add("/")
            elif "/" not in paths:
                paths.add(path)
            self.last_event[identifier] = time.monotonic()
            self.first_event.setdefault(identifier, time.monotonic())

    async def start(self):
        for root in self.roots:
            await asyncio.to_thread(self.store.register, root)
            try:
                self.observer.schedule(Events(self, root), root, recursive=True)
            except OSError as exc:
                self.emit("log", tr('文件监听不可用，将使用定时巡检 · {value0}', value0=exc))
        try:
            await asyncio.to_thread(self.observer.start)
        except OSError as exc:
            self.emit("log", tr('文件监听启动失败，将使用定时巡检 · {value0}', value0=exc))
        self.task = asyncio.create_task(self.run())

    async def run(self):
        while not self.store.cancel.is_set():
            try:
                summary = await asyncio.to_thread(self.store.summary)
                for root in self.roots:
                    if self.store.cancel.is_set():
                        break
                    identifier = root_id(root)
                    row = next((r for r in summary["roots"] if r["id"] == identifier), {})
                    count = row.get("files", 0)
                    interval = self.config.scan_interval or (600 if count <= 10000 else 3600 if count <= 100000 else 14400)
                    now = time.monotonic()
                    full = identifier not in self.last_full or now - self.last_full[identifier] >= interval
                    paths = None
                    if not full:
                        with self.lock:
                            if identifier in self.queue and (now - self.last_event[identifier] >= 2 or now - self.first_event[identifier] >= 10):
                                paths = sorted(self.queue.pop(identifier))
                                self.first_event.pop(identifier, None)
                        if not paths:
                            continue
                        if "/" in paths:
                            full = True
                    result = await asyncio.to_thread(self.store.scan, identifier, None if full else paths)
                    if full:
                        self.last_full[identifier] = time.monotonic()
                        self.emit("log", tr('目录巡检{value0} · {value1} · {value2}', value0=tr('完成') if result['ok'] else tr('待重试'), value1=root, value2=result.get('error', '')))
                        if not result["ok"]:
                            self.last_full[identifier] = time.monotonic() - interval + min(30, interval)
                    if not result["ok"]:
                        self.dirty(identifier, "/")
                    elif self.sync_api:
                        for user in self.config.users:
                            if user.enabled and user.sync_enabled and user.root == root:
                                await asyncio.to_thread(self.sync_api.refresh, user)
                if time.monotonic() - self.last_audit > 60:
                    for root in self.roots:
                        if not self.store.cancel.is_set():
                            await asyncio.to_thread(self.store.audit, root_id(root), self.config.audit_days)
                    self.last_audit = time.monotonic()
                if time.monotonic() - self.last_prune > 3600:
                    await asyncio.to_thread(self.store.prune, self.config.retention_days)
                    if self.sync_api:
                        await asyncio.to_thread(self.sync_api.cleanup, self.config.retention_days)
                    self.last_prune = time.monotonic()
            except Exception as exc:
                self.emit("log", tr('索引后台任务错误，将重试 · {value0}', value0=exc))
            await asyncio.sleep(1)

    async def stop(self):
        self.store.cancel.set()
        self.observer.stop()
        if self.observer.is_alive():
            await asyncio.to_thread(self.observer.join, 5)
        if self.task:
            # Do not cancel a to_thread scan: wait until it sees the cancellation flag.
            await self.task
