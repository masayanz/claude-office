"""Cross-process ownership lock for a file-backed AI Office Viewer database."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TextIO
from urllib.parse import unquote


def sqlite_path_from_url(url: str) -> Path | None:
    """Return the file path for a SQLite URL, or ``None`` for memory DBs."""
    if not url.startswith("sqlite") or ":memory:" in url:
        return None
    raw = url.rsplit("///", 1)[-1]
    if not raw:
        return None
    return Path(unquote(raw)).resolve()


class DatabaseProcessLock:
    """Hold one non-blocking OS lock for the lifetime of a Backend process."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.lock_path = database_path.with_name(f"{database_path.name}.lock")
        self._stream: TextIO | None = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+", encoding="utf-8")
        try:
            if self.lock_path.stat().st_size == 0:
                stream.seek(0)
                stream.write("0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            stream.close()
            raise RuntimeError(
                "AI Office Viewer Backend is already using this database"
            ) from None
        self._stream = stream

    def release(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            stream.close()


def acquire_database_process_lock(database_url: str) -> DatabaseProcessLock | None:
    path = sqlite_path_from_url(database_url)
    if path is None:
        return None
    lock = DatabaseProcessLock(path)
    lock.acquire()
    return lock
