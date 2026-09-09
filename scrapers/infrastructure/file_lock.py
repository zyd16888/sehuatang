"""同一共享 data 目录上的跨进程锁；锁文件保留，操作系统释放持有权。"""
import os
import time
from pathlib import Path


class FileLock:
    def __init__(self, path, *, blocking=True):
        self.path = Path(path)
        self.blocking = blocking
        self._file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._file = handle
                return True
            except OSError as exc:
                import errno
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    handle.close()
                    raise
                if not self.blocking:
                    handle.close()
                    return False
                time.sleep(0.05)

    def release(self):
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self):
        if not self.acquire():
            raise BlockingIOError(str(self.path))
        return self

    def __exit__(self, *exc):
        self.release()


def source_lock(source):
    import hashlib
    return FileLock(Path(__file__).resolve().parents[2] / "data" / "locks" /
                    ("source-" + hashlib.sha256(source.encode()).hexdigest() + ".lock"), blocking=False)


def locked_json(method):
    from functools import wraps
    @wraps(method)
    def call(self, *args, **kwargs):
        with FileLock(str(self.path) + ".lock"):
            return method(self, *args, **kwargs)
    return call
