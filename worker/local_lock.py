"""
Cross-process local file lock.

Used to prevent concurrent local automation runs from multiple processes
(e.g. worker.main + worker.cli register).
"""

from __future__ import annotations

import os
from typing import Optional


class LocalFileLock:
    def __init__(self, path: str):
        self.path = path
        self._fh: Optional[object] = None

    def acquire(self, blocking: bool = False) -> bool:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        acquired = False
        try:
            fh.seek(0)
            if os.name == "nt":
                import msvcrt
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(fh.fileno(), mode, 1)
            else:
                import fcntl
                flags = fcntl.LOCK_EX
                if not blocking:
                    flags |= fcntl.LOCK_NB
                fcntl.flock(fh.fileno(), flags)
            acquired = True

            fh.seek(0)
            fh.truncate()
            fh.write(str(os.getpid()))
            fh.flush()
            self._fh = fh
            return True
        except OSError:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                fh.close()
            except OSError:
                pass
            return False

    def release(self) -> None:
        fh = self._fh
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                fh.close()
            except OSError:
                pass
            self._fh = None
