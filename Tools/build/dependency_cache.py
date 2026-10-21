"""Install managed build dependencies with a completion receipt."""

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path

RECEIPT = ".cpython-dependencies.json"


@contextlib.contextmanager
def _prefix_lock(prefix):
    # Keep the lock file: unlinking it could let two processes lock different
    # files for the same prefix. Closing the handle releases the lock on exit.
    with (prefix.parent / f".{prefix.name}.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError(f"Dependency prefix is in use: {prefix}") from exc
        else:
            import fcntl

            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"Dependency prefix is in use: {prefix}") from exc
        yield


def install_prefix(prefix, inputs, install, *, use_existing=False):
    """Run install(staging_prefix), then publish its files and input receipt.

    A matching receipt reuses a completed installation. Unmanaged prefixes
    require an explicit override, so local dependency builds are not replaced.
    """
    prefix = Path(prefix).absolute()
    if use_existing:
        if not prefix.is_dir():
            raise ValueError(f"Dependency prefix does not exist: {prefix}")
        return False
    if prefix.is_symlink():
        raise ValueError(f"Cannot replace a symlinked dependency prefix: {prefix}")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    expected = json.loads(json.dumps({"schema": 1, "inputs": inputs}))
    with _prefix_lock(prefix):
        if prefix.exists():
            try:
                receipt = json.loads((prefix / RECEIPT).read_text())
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"No valid dependency receipt in {prefix}; refusing to replace "
                    "the existing prefix"
                ) from exc
            if receipt == expected:
                return False
        with tempfile.TemporaryDirectory(
            dir=prefix.parent, prefix=f".{prefix.name}-"
        ) as tmp:
            staging = Path(tmp) / "prefix"
            staging.mkdir()
            install(staging)
            (staging / RECEIPT).write_text(
                json.dumps(expected, indent=2, sort_keys=True) + "\n"
            )
            previous = None
            if prefix.exists():
                previous = Path(tempfile.mkdtemp(
                    dir=prefix.parent, prefix=f".{prefix.name}-previous-"
                )) / "prefix"
                prefix.rename(previous)
            try:
                staging.rename(prefix)
            except BaseException:
                if previous is not None:
                    # A failed restore leaves the old installation outside the
                    # temporary directory, available for manual recovery.
                    previous.rename(prefix)
                    previous.parent.rmdir()
                raise
            if previous is not None:
                shutil.rmtree(previous.parent)
    return True
