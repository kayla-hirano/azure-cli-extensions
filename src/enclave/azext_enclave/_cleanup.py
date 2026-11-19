# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Archive stopped-instance logs before removal, assuming no concurrent privileged filesystem writers."""

import errno
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Tuple

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
    """Return the unpublished staging directory for an archive."""
    return archive.with_name('.' + archive.name + '.staging')


def _regular_file(path: Path) -> os.stat_result:
    """Reject files that are not private, owned regular files without hard links."""
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise CLIError('Expected a private, owned regular file without links: {}'.format(path))
    return info


def read_private_record(path: Path) -> dict:
    """Reject symlinked or untrusted state/ownership records before parsing them."""
    _regular_file(path)
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, encoding='utf-8') as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise CLIError('Invalid Enclave record: {}'.format(path))
    return value


def _publish(source: Path, destination: Path) -> None:
    """Publish staging after checking for collisions; callers exclude concurrent privileged writers."""
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(errno.EEXIST, 'Archive collision; refusing to adopt or overwrite', str(destination))
    source.rename(destination)


def _copy_log(source: Path, target: Path) -> None:
    """Copy an inventoried log to a private file and flush its contents."""
    source_descriptor = os.open(str(source), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(source_descriptor, 'rb') as stream:
        descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            shutil.copyfileobj(stream, output)
            output.flush()
            os.fsync(output.fileno())


def _archive(directory: Path, logs: Tuple[str, ...]) -> Path:
    """Copy an instance's logs into staging before publishing the archive."""
    archive = archive_path(directory.name, create=True)
    staging = staging_path(archive)
    if archive.exists() or archive.is_symlink():
        raise CLIError('Archive collision; refusing to adopt or overwrite: {}'.format(archive))
    staging.mkdir(mode=0o700)
    for name in logs:
        _copy_log(directory / name, staging / name)
    sync_directory(staging)
    _publish(staging, archive)
    sync_directory(archive.parent)
    return archive


def _inventory(directory: Path, expected: object) -> Tuple[str, ...]:
    """Preflight log validation and readability before archival starts, without reading contents."""
    logs = []
    if not isinstance(expected, list) or not all(isinstance(name, str) and name in LOG_NAMES for name in expected):
        raise CLIError('Missing or invalid expected log inventory: {}'.format(directory / 'instance.json'))
    for name in LOG_NAMES:
        path = directory / name
        try:
            _regular_file(path)
            descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            os.close(descriptor)
        except FileNotFoundError as error:
            if name in expected:
                raise CLIError('Expected log is missing: {}. Restore it or explicitly retry with --no-logs.'.format(
                    path)) from error
            logger.warning('Log was not created during this launch: %s', path)
        else:
            logs.append(name)
    return tuple(logs)


def _remove_instance(directory: Path) -> None:
    """Remove only known private files, preserving the instance record until last."""
    check_private(directory)
    names = [entry.name for entry in directory.iterdir()]
    if 'instance.json' not in names:
        raise CLIError('Instance state is missing; refusing removal: {}'.format(directory))
    for name in names:
        if name not in INSTANCE_FILES:
            raise CLIError('Unexpected instance entry; retain for manual recovery: {}'.format(directory / name))
        _regular_file(directory / name)
    names.remove('instance.json')
    names.append('instance.json')
    for name in names:
        (directory / name).unlink()
    directory.rmdir()
    sync_directory(directory.parent)


def _record_failure(directory: Path, state: dict, error: BaseException) -> None:
    """Record a cleanup failure when private instance state remains available."""
    state.update(status='cleanup-failed', cleanup_error=str(error) or 'Cleanup interrupted.')
    try:
        check_private(directory)
        _regular_file(directory / 'instance.json')
        write_record(directory / 'instance.json', state)
    except (CLIError, OSError) as record_error:
        logger.error('Unable to save cleanup failure in %s: %s. Manual cleanup is required.',
                     directory / 'instance.json', record_error)


def finalization_error(directory: Path, state: dict, error: BaseException) -> CLIError:
    """Describe incomplete cleanup and the paths needed for recovery."""
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
    check_private(directory)
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
        _remove_instance(directory)
    except (CLIError, OSError, KeyboardInterrupt) as error:
        _record_failure(directory, state, error)
        raise
    return {
        'name': directory.name, 'status': 'destroyed',
        'log_disposition': 'archived' if archive is not None else 'discarded',
        'archive_path': str(archive) if archive is not None else None,
        'removal_outcome': 'removed',
    }
