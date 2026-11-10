"""One writer at a time: an advisory, process-level lock.

Every mutating verb takes the queue lock for its whole read-modify-write, so
two crossbox processes in one tree (two agents, an agent and a human, parallel
subagents) queue behind each other instead of losing each other's writes. The
rate gate takes a second lock, on a lock file beside its state file, which all
trees on the machine share.

POSIX: ``fcntl.flock`` on a file descriptor of the locked DIRECTORY, so no lock
file is created in the repository. The kernel drops the lock when the process
exits, so a crashed writer can never leave a stale lock behind.

Windows (UNVERIFIED on hardware): an ``O_CREAT | O_EXCL`` lock file inside the
directory holding the owner's pid; a lock file older than the stale age is
taken over.

A lock that cannot be had within the timeout raises NotEvaluable (exit 2):
crossbox never writes without it. ``CROSSBOX_LOCK_TIMEOUT`` (seconds) changes
the wait.
"""
from __future__ import annotations

import contextlib
import math
import os
import time
from pathlib import Path

from .errors import NotEvaluable

DEFAULT_TIMEOUT = 60.0
STALE_AGE = 300.0
LOCK_NAME = ".crossbox.lock"


def timeout() -> float:
    """Seconds a writer waits for the lock. A value that cannot be read is NOT EVALUABLE,
    naming the variable, as the rate gate's are: a setting silently ignored is not set."""
    raw = os.environ.get("CROSSBOX_LOCK_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        t = float(raw)
    except ValueError:
        t = float("nan")
    if not (math.isfinite(t) and t >= 0):
        raise NotEvaluable(f"CROSSBOX_LOCK_TIMEOUT={raw!r} is not a number of seconds (0 or more)")
    return t


@contextlib.contextmanager
def _posix(d: Path, wait: float, what: str, as_file: bool = False):
    import fcntl
    if as_file:
        fd = os.open(str(d), os.O_RDWR | os.O_CREAT, 0o600)
    else:
        fd = os.open(str(d), os.O_RDONLY)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= deadline:
                    raise NotEvaluable(
                        f"{what}: another crossbox process has held the lock on {d} "
                        f"for more than {wait:g} s; nothing written (re-run when it "
                        f"finishes)")
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _lockfile(d: Path, wait: float, what: str, as_file: bool = False):
    p = Path(str(d) + ".excl") if as_file else d / LOCK_NAME
    deadline = time.monotonic() + wait
    while True:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - p.stat().st_mtime > STALE_AGE:
                    p.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise NotEvaluable(f"{what}: lock file {p} is held by another crossbox "
                                   f"process; nothing written (delete it if no crossbox "
                                   f"is running)")
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        try:
            p.unlink()
        except OSError:
            pass


@contextlib.contextmanager
def hold(d: Path, what: str = "lock", wait: float = None):
    """Hold the exclusive lock on the existing directory ``d``."""
    d = Path(d)
    wait = timeout() if wait is None else wait
    impl = _lockfile if os.name == "nt" else _posix
    with impl(d, wait, what):
        yield


@contextlib.contextmanager
def hold_file(p: Path, what: str = "lock", wait: float = None):
    """Hold the exclusive lock on the lock FILE ``p`` (created, never replaced).
    Used where the protected file lives in a directory another lock may hold."""
    p = Path(p)
    wait = timeout() if wait is None else wait
    impl = _lockfile if os.name == "nt" else _posix
    with impl(p, wait, what, as_file=True):
        yield
