"""Qt-free SFTP server entry point for foreground supervisors."""

import argparse
import asyncio
import json
import signal
from pathlib import Path

from .i18n import tr
from .config import ConfigStore
from .server import SFTPService


async def serve(directory):
    store = ConfigStore(directory)
    config = store.load()
    stopping = asyncio.Event()
    reloading = asyncio.Event()
    loop = asyncio.get_running_loop()
    callbacks = {}
    for signum, callback in ((signal.SIGINT, stopping.set), (signal.SIGTERM, stopping.set)):
        try:
            loop.add_signal_handler(signum, callback)
        except NotImplementedError:
            callbacks[signum] = signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stopping.set))
    if hasattr(signal, "SIGHUP"):
        loop.add_signal_handler(signal.SIGHUP, reloading.set)
    def emit(event, value):
        if event == "log":
            print(json.dumps({"event": event, "message": value}, ensure_ascii=False), flush=True)
    service = SFTPService(directory, emit)
    stopper = reloader = None
    try:
        await service.start(config)
        while not stopping.is_set():
            stopper = asyncio.create_task(stopping.wait())
            reloader = asyncio.create_task(reloading.wait())
            await asyncio.wait((stopper, reloader), return_when=asyncio.FIRST_COMPLETED)
            for task in (stopper, reloader):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stopper, reloader, return_exceptions=True)
            if stopping.is_set():
                break
            reloading.clear()
            try:
                await service.reload(await asyncio.to_thread(store.load))
            except (ValueError, OSError) as exc:
                service.emit("log", tr('配置热加载失败，运行配置保持原值 · {value0}', value0=exc))
    finally:
        for task in (stopper, reloader):
            if task and not task.done():
                task.cancel()
        await service.stop()
        for signum, old in callbacks.items():
            signal.signal(signum, old)
        if not callbacks:
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(signum)
        if hasattr(signal, "SIGHUP"):
            loop.remove_signal_handler(signal.SIGHUP)


def main():
    parser = argparse.ArgumentParser(description=tr('Simple Sync 无头 SFTP 服务（无 Qt 依赖）'))
    parser.add_argument("--data-dir", type=Path, required=True, help=tr('包含 config.json 的专用应用数据目录'))
    parser.add_argument("--check-config", action="store_true", help=tr('验证配置后退出，不监听端口'))
    args = parser.parse_args()
    directory = args.data_dir.expanduser().resolve()
    try:
        if not (directory / "config.json").is_file():
            raise ValueError(tr('数据目录内缺少 config.json，请先通过桌面配置或复制配置模板'))
        if args.check_config:
            config = ConfigStore(directory).load()
            print(json.dumps({"valid": True, "enabled_users": sum(user.enabled for user in config.users)}))
            return 0
        asyncio.run(serve(directory))
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError) as exc:
        parser.exit(1, tr('服务失败: {value0}\n', value0=exc))


if __name__ == "__main__":
    raise SystemExit(main())
