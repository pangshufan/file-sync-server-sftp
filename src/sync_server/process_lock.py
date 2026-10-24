"""Prevent GUI/headless processes from serving the same state directory together."""

import os
from .i18n import tr


class ProcessLock:
    def __init__(self, path):
        self.file = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(self.file.fileno()).st_size == 0:
                    self.file.write(b"0")
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError(tr('此数据目录已由其他服务进程使用')) from None

    def close(self):
        if not self.file.closed:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
