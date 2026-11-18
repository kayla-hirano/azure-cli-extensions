# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Archive stopped-instance logs before removal and report incomplete cleanup for manual inspection."""

import ctypes
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Optional

from knack.log import get_logger
from knack.util import CLIError

from ._instance import _private_root, check_private, sync_directory, write_record

logger = get_logger(__name__)
ARCHIVE_ROOT = '/var/log/enclave'
LOG_NAMES = ('com1.log', 'openvmm.log', 'supervisor.log')
RECORD_NAMES = ('instance.json', 'runtime.json', 'exit.json', 'worker-error.json', 'response.json')
INSTANCE_FILES = frozenset(LOG_NAMES + RECORD_NAMES + tuple(
    str(Path(name).with_suffix('.tmp')) for name in RECORD_NAMES))


def archive_path(name: str, create: bool = False) -> Path:
    """Resolve the fixed private archive root without creating it in discard mode."""
    return _private_root(ARCHIVE_ROOT, create=create) / name


def staging_path(archive: Path) -> Path:
    return archive.with_name('.' + archive.name + '.staging')


def _directory_identity(directory: Path) -> list:
    check_private(directory)
    info = directory.lstat()
    return [info.st_dev, info.st_ino]


def _regular_file(path: Path, directory_fd: Optional[int] = None) -> os.stat_result:
    info = path.lstat() if directory_fd is None else os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
    _validate_file_info(path, info)
    return info


def _validate_file_info(path: Path, info: os.stat_result) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise CLIError('Expected a private, owned regular file without links: {}'.format(path))


def read_private_record(path: Path) -> dict:
    """Reject symlinked or untrusted state/ownership records before parsing them."""
    before = _regular_file(path)
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, encoding='utf-8') as stream:
        opened = os.fstat(stream.fileno())
        _validate_file_info(path, opened)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise CLIError('Record changed while opening: {}'.format(path))
        value = json.load(stream)
    if not isinstance(value, dict):
        raise CLIError('Invalid Enclave record: {}'.format(path))
    return value


def _digest(path: Path) -> dict:
    before = _regular_file(path)
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest = hashlib.sha256()
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        _validate_file_info(path, opened)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise CLIError('Log changed while opening: {}'.format(path))
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        after = os.fstat(stream.fileno())
    if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise CLIError('Log changed after shutdown: {}'.format(path))
    return {'size': after.st_size, 'sha256': digest.hexdigest()}


def _validate_archive(path: Path, expected: dict) -> None:
    check_private(path)
    if {entry.name for entry in path.iterdir()} != set(expected):
        raise CLIError('Archive contents do not match the captured log inventory: {}'.format(path))
    for name, metadata in expected.items():
        if _digest(path / name) != metadata:
            raise CLIError('Archive log failed integrity validation: {}'.format(path / name))


def _publish(source: Path, destination: Path) -> None:
    # renameat2 keeps publication atomic without replacing even an empty colliding directory.
    try:
        rename = ctypes.CDLL('libc.so.6', use_errno=True).renameat2
    except AttributeError as error:
        raise CLIError('Log archival requires Linux renameat2 no-replace support. '
                       'Manual cleanup is required before retrying on a supported host.') from error
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def _copy_log(source: Path, target: Path, expected: dict) -> None:
    _regular_file(source)
    descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        source_descriptor = os.open(str(source), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(source_descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            observed = _regular_file(source)
            if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
                raise CLIError('Log changed while opening: {}'.format(source))
            digest = hashlib.sha256()
            size = 0
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                output.write(block)
                digest.update(block)
                size += len(block)
            after = os.fstat(stream.fileno())
            if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise CLIError('Log changed after shutdown: {}'.format(source))
            output.flush()
            os.fsync(output.fileno())
    if {'size': size, 'sha256': digest.hexdigest()} != expected:
        raise CLIError('Log copy failed integrity validation: {}'.format(source))


def _archive(directory: Path, logs: dict) -> Path:
    archive = archive_path(directory.name, create=True)
    staging = staging_path(archive)
    if archive.exists() or archive.is_symlink():
        raise CLIError('Archive collision; refusing to adopt or overwrite: {}'.format(archive))
    staging.mkdir(mode=0o700)
    for name, expected in logs.items():
        _copy_log(directory / name, staging / name, expected)
    _validate_archive(staging, logs)
    sync_directory(staging)
    _publish(staging, archive)
    sync_directory(archive.parent)
    return archive


def _inventory(directory: Path, expected: object) -> dict:
    logs = {}
    if not isinstance(expected, list) or not all(isinstance(name, str) and name in LOG_NAMES for name in expected):
        raise CLIError('Missing or invalid expected log inventory: {}'.format(directory / 'instance.json'))
    for name in LOG_NAMES:
        path = directory / name
        try:
            logs[name] = _digest(path)
        except FileNotFoundError as error:
            if name in expected:
                raise CLIError('Expected log is missing: {}. Restore it or explicitly retry with --no-logs.'.format(
                    path)) from error
            logger.warning('Log was not created during this launch: %s', path)
    return logs


def _remove_instance(directory: Path, expected_identity: list) -> None:
    if _directory_identity(directory) != expected_identity:
        raise CLIError('Instance directory was replaced; refusing removal: {}'.format(directory))
    descriptor = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if [opened.st_dev, opened.st_ino] != expected_identity:
            raise CLIError('Instance directory changed while opening: {}'.format(directory))
        names = os.listdir(descriptor)
        if 'instance.json' not in names:
            raise CLIError('Instance state is missing; refusing removal: {}'.format(directory))
        for name in names:
            if name not in INSTANCE_FILES:
                raise CLIError('Unexpected instance entry; retain for manual recovery: {}'.format(directory / name))
            _regular_file(directory / name, directory_fd=descriptor)
        # Preserve status and failure details until all other instance files are removed.
        names.remove('instance.json')
        names.append('instance.json')
        for name in names:
            if _directory_identity(directory) != expected_identity:
                raise CLIError('Instance directory changed during removal: {}'.format(directory))
            _regular_file(directory / name, directory_fd=descriptor)
            os.unlink(name, dir_fd=descriptor)
        if _directory_identity(directory) != expected_identity:
            raise CLIError('Instance directory changed before removal: {}'.format(directory))
        directory.rmdir()
    finally:
        os.close(descriptor)
    sync_directory(directory.parent)


def _record_failure(directory: Path, state: dict, expected_identity: list, error: BaseException) -> None:
    state.update(status='cleanup-failed', cleanup_error=str(error) or 'Cleanup interrupted.')
    try:
        if _directory_identity(directory) != expected_identity:
            raise CLIError('Instance directory was replaced.')
        _regular_file(directory / 'instance.json')
        write_record(directory / 'instance.json', state)
    except (CLIError, OSError) as record_error:
        logger.error('Unable to save cleanup failure in %s: %s. Manual cleanup is required.',
                     directory / 'instance.json', record_error)


def finalization_error(directory: Path, state: dict, error: BaseException) -> CLIError:
    archive = Path(ARCHIVE_ROOT) / directory.name
    reason = str(error) or 'Cleanup interrupted.'
    previous_error = state.get('cleanup_error')
    previous_detail = (' Previous cleanup error: {}.'.format(previous_error)
                       if previous_error and previous_error != reason else '')
    if not state or 'cleanup_phase' in state:
        action = 'Cleanup progress is ambiguous. Manual cleanup is required; inspect these paths before deleting anything.'
    else:
        action = ('Resolve the error and retry az enclave destroy --name {}; '
                  'use --no-logs only to explicitly discard unarchived logs.').format(directory.name)
    return CLIError(
        'Destroy finalization incomplete for {}: {}. Shutdown/removal must not be inferred from this failure. '
        'Last known status: {}; cleanup phase: {}; log disposition: {}.{} '
        'Instance path: {}. Archive path: {}. Staging path: {}. {}'.format(
            directory.name, reason, state.get('status', 'unknown'),
            state.get('cleanup_phase', 'not started' if state else 'unknown'),
            state.get('log_disposition', 'unconfirmed'), previous_detail,
            directory, archive, staging_path(archive), action))


def finalize(directory: Path, state: dict) -> dict:
    """Archive before removal after confirmed shutdown; interrupted phases require manual cleanup."""
    if 'cleanup_phase' in state:
        raise finalization_error(directory, state, CLIError('An earlier cleanup attempt did not complete.'))
    expected_identity = _directory_identity(directory)
    archive = None
    try:
        policy = state.get('log_policy')
        if policy not in ('retain', 'discard'):
            raise CLIError('Invalid persisted log policy: {}'.format(directory / 'instance.json'))
        if policy == 'retain':
            logs = _inventory(directory, state.get('expected_logs'))
            state.update(status='cleanup-pending', cleanup_phase='archiving')
            write_record(directory / 'instance.json', state)
            archive = _archive(directory, logs)
        state.update(status='cleanup-pending', cleanup_phase='removing',
                     log_disposition='archived' if archive is not None else 'discard-requested',
                     archive_path=str(archive) if archive is not None else None)
        write_record(directory / 'instance.json', state)
        _remove_instance(directory, expected_identity)
    except (CLIError, OSError, KeyboardInterrupt) as error:
        _record_failure(directory, state, expected_identity, error)
        raise
    return {
        'name': directory.name, 'status': 'destroyed',
        'log_disposition': 'archived' if archive is not None else 'discarded',
        'archive_path': str(archive) if archive is not None else None,
        'removal_outcome': 'removed',
    }
