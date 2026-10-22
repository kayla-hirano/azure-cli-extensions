# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Manage private instance records, exclusive command locks, and Linux process ownership."""

import hashlib
import json
import math
import os
import re
import signal
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

from knack.util import CLIError

DEFAULT_STATE_DIR = '/var/lib/ace/instances'


def require_host(timeout: float) -> None:
    """Require the Linux/root execution environment used by the BVT and a valid timeout."""
    if sys.platform != 'linux':
        raise CLIError('ACE launch/destroy must run locally on Linux L1.')
    if os.geteuid() != 0:
        raise CLIError('Run ACE launch/destroy as root on L1, as with run-skr-bvt.sh.')
    if not math.isfinite(timeout) or timeout <= 0:
        raise CLIError('--timeout must be a finite positive number of seconds.')


def instance_path(state_dir: str, name: str) -> Path:
    """Validate the instance name and private state root before constructing socket paths."""
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}', name):
        raise CLIError('--name must be 1-32 letters, digits, underscores or hyphens.')
    base = Path(state_dir).expanduser().absolute()
    # Never resolve through a symlink into another instance or an untrusted state directory.
    for path in list(reversed(base.parents)) + [base]:
        if path.is_symlink():
            raise CLIError('State directory must not contain symlinks: {}'.format(path))
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    check_private(base)
    directory = base / name
    if len(os.fsencode(str(directory / 'serial.sock'))) >= 108:
        raise CLIError('--state-dir is too long for Unix socket paths.')
    if directory.is_symlink():
        raise CLIError('Instance directory must not be a symlink.')
    return directory


def check_private(path: Path) -> None:
    """Reject directories with the wrong owner, type, or group/other permissions."""
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise CLIError('ACE state directory must be owned by the caller with mode 0700: {}'.format(path))


@contextmanager
def instance_lock(directory: Path) -> Iterator[None]:
    """Hold a nonblocking per-instance flock so launch and destroy cannot modify state concurrently."""
    import fcntl
    lock_path = directory.parent / (directory.name + '.lock')
    descriptor = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise CLIError('Unsafe ACE instance lock: {}'.format(lock_path))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise CLIError('Another command is managing this ACE instance.') from error
        yield
    finally:
        os.close(descriptor)


def write_record(path: Path, value: dict) -> None:
    """Write a private JSON record via a flushed temporary file and atomic replacement."""
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def read_record(path: Path) -> dict:
    """Load a JSON record and reject non-object contents before callers inspect its fields."""
    with path.open(encoding='utf-8') as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise CLIError('Invalid ACE record: {}'.format(path))
    return value


def identity(pid: int) -> Optional[Dict[str, object]]:
    """Capture boot/start/command identity from /proc, returning None for exited or zombie processes."""
    try:
        proc = Path('/proc') / str(pid)
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return {
            'pid': pid,
            'start': fields[19],
            'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'command': hashlib.sha256((proc / 'cmdline').read_bytes()).hexdigest(),
        }
    except (FileNotFoundError, ProcessLookupError):
        return None


def alive(record: dict) -> bool:
    """Check that the recorded PID still belongs to the same live process."""
    pid = record.get('pid')
    return isinstance(pid, int) and pid > 1 and identity(pid) == record


def stop_owned(record: dict, timeout: float = 10) -> None:
    """Apply bounded TERM/KILL recovery only while the recorded process identity matches."""
    # PID plus boot/start/command identity prevents cleanup from signaling a reused PID.
    if not alive(record):
        return
    try:
        os.kill(record['pid'], signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while alive(record) and time.monotonic() < deadline:
        time.sleep(0.1)
    if alive(record):
        try:
            os.kill(record['pid'], signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 5
        while alive(record) and time.monotonic() < deadline:
            time.sleep(0.1)
    if alive(record):
        raise CLIError('Owned process did not exit: {}'.format(record['pid']))


def cleanup_sockets(directory: Path) -> None:
    """Remove known runtime sockets without deleting logs or unexpected filesystem entries."""
    for name in ('rpc.sock', 'serial.sock', 'vsock.sock'):
        path = directory / name
        if path.is_socket():
            path.unlink()
        elif path.exists() or path.is_symlink():
            raise CLIError('Refusing to remove a non-socket runtime path: {}'.format(path))
