# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Coordinate local L1 launch/destroy, RPC calls, and failure recovery."""

import hashlib
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import grpc
from knack.log import get_logger
from knack.util import CLIError

from . import _instance
from ._instance import (alive, check_private, cleanup_sockets, host_lock, identity,
                        instance_lock, instance_path, new_name, read_record, require_host, stop_owned,
                        sync_directory, write_record)
from ._cleanup import (LOG_NAMES, archive_path, finalize, finalization_error, read_private_record,
                       staging_path)
from ._isolation import load_isolation
from ._rpc import VmClient, create_request, validate_launch_configuration
from ._runtime import resolve_runtime
from .rpc import vmservice_pb2 as vm

logger = get_logger(__name__)
GUEST_VSOCK_PORT = 4050


def _instance_info(directory: Path) -> dict:
    """Describe instance endpoints and logs for both lifecycle commands."""
    return {
        'directory': str(directory),
        'vsock': {'port': GUEST_VSOCK_PORT, 'socket': str(directory / 'vsock.sock')},
        'rpc_socket': str(directory / 'rpc.sock'),
        'logs': {
            'openvmm': str(directory / 'openvmm.log'),
            'supervisor': str(directory / 'supervisor.log'),
            'com1': str(directory / 'com1.log'),
        },
        'instance_file': str(directory / 'instance.json'),
    }


def _wait_runtime(directory: Path, supervisor: dict, deadline: float) -> dict:
    """Wait for the supervisor to publish matching, live OpenVMM process ownership."""
    while time.monotonic() < deadline:
        if not alive(supervisor):
            raise CLIError('OpenVMM supervisor exited; inspect supervisor.log and openvmm.log.')
        if (directory / 'runtime.json').exists():
            runtime = read_record(directory / 'runtime.json')
            if runtime['supervisor'] != supervisor or not alive(runtime['openvmm']):
                raise CLIError('Invalid or exited OpenVMM runtime.')
            return runtime
        time.sleep(0.1)
    raise CLIError('Timed out starting the OpenVMM supervisor.')


def _stop_runtime(directory: Path, state: dict) -> None:
    """Finish owned-process cleanup after RPC shutdown or launch rollback, retaining evidence."""
    # Recovery only: prefer TeardownVM/Quit; the worker terminates and reaps its own OpenVMM child.
    if state.get('supervisor'):
        deadline = time.monotonic() + 2
        while alive(state['supervisor']) and time.monotonic() < deadline:
            time.sleep(0.1)
        if alive(state['supervisor']):
            logger.warning('Stopping the owned supervisor with signals after its exit grace period.')
        stop_owned(state['supervisor'], timeout=15)
    if (directory / 'runtime.json').exists():
        runtime = read_record(directory / 'runtime.json')
        if alive(runtime['openvmm']):
            logger.warning('Recovering the still-running owned OpenVMM process with signals.')
        stop_owned(runtime['openvmm'])
    cleanup_sockets(directory)


def _file_digest(path: Path) -> str:
    """Hash an input in bounded chunks so instance records identify the selected artifacts."""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _start_supervisor(directory: Path, state: dict, host_lock_fd: int) -> None:
    """Create expected logs, then start the persistent OpenVMM owner and save its identity."""
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent) + (
        os.pathsep + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
    for name in LOG_NAMES:
        descriptor = os.open(str(directory / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
    state['expected_logs'] = list(LOG_NAMES)
    write_record(directory / 'instance.json', state)
    with (directory / 'supervisor.log').open('ab') as log:
        worker = subprocess.Popen(
            [sys.executable, '-m', 'azext_enclave._worker', str(directory), str(host_lock_fd)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            env=environment, start_new_session=True, close_fds=True, pass_fds=(host_lock_fd,))
    state['supervisor'] = identity(worker.pid)
    if state['supervisor'] is None:
        worker.wait(timeout=5)
        raise CLIError('Supervisor failed to start; inspect supervisor.log.')
    write_record(directory / 'instance.json', state)


def _rollback_launch(directory: Path, state: dict, error: BaseException) -> None:
    """Attempt RPC rollback and owned-process recovery, retaining failure evidence."""
    state.update(status='failed', error=str(error))
    # A timed-out CreateVM may have succeeded, so request server exit before signal recovery.
    try:
        if (directory / 'rpc.sock').is_socket():
            with VmClient(directory / 'rpc.sock', time.monotonic() + 5) as client:
                client.call('Quit')
    except CLIError as cleanup_error:
        logger.warning('RPC rollback failed; attempting owned-process recovery: %s', cleanup_error)
    try:
        _stop_runtime(directory, state)
    except (CLIError, OSError) as cleanup_error:
        state['cleanup_error'] = str(cleanup_error)
        logger.error('Launch cleanup incomplete: %s', cleanup_error)
    write_record(directory / 'instance.json', state)


def launch(igvm: str, name_prefix: str = 'enclave', openvmm: str = 'openvmm',
           timeout: float = 120,
           allow_untested_runtime: bool = False) -> dict:
    """Create and resume an instance."""
    name = new_name(name_prefix)
    configuration = load_isolation()
    validate_launch_configuration(configuration)
    require_host(timeout)
    image = Path(igvm).expanduser().resolve()
    if not image.is_file():
        raise CLIError('IGVM file not found: {}'.format(image))
    for required in configuration.character_devices:
        device = Path(required)
        if not device.exists() or not stat.S_ISCHR(device.stat().st_mode):
            raise CLIError('L1 requires the {} character device.'.format(required))
    executable = resolve_runtime(openvmm, allow_untested_runtime)
    executable_digest = _file_digest(executable)
    image_digest = _file_digest(image)
    directory = instance_path(name)
    deadline = time.monotonic() + timeout
    with host_lock() as host_lock_fd, instance_lock(directory):
        if directory.exists() or directory.is_symlink():
            raise CLIError('Generated instance name already exists; retry launch. Existing state is retained.')
        archive = archive_path(name)
        if archive.exists() or archive.is_symlink():
            raise CLIError('Generated name already has an archive; retry launch. Archive retained: {}'.format(archive))
        staging = staging_path(archive)
        if staging.exists() or staging.is_symlink():
            raise CLIError('Generated name has unfinished log staging; retry launch. Staging retained: {}'.format(
                staging))
        directory.mkdir(mode=0o700)
        sync_directory(directory.parent)
        state = {
            'schema': 1, 'name': name, 'status': 'starting', 'igvm': str(image),
            'openvmm': str(executable), 'directory': str(directory),
            'openvmm_sha256': executable_digest, 'igvm_sha256': image_digest,
            'expected_logs': [],
        }
        state.update(_instance_info(directory))
        write_record(directory / 'instance.json', state)
        try:
            _start_supervisor(directory, state, host_lock_fd)
            # Process ownership must be published before waiting for the gRPC endpoint to be usable.
            _wait_runtime(directory, state['supervisor'], deadline)
            with VmClient(directory / 'rpc.sock', deadline) as client:
                client.ready()
                # CreateVM allocates a paused guest; ResumeVM starts guest execution.
                client.call('CreateVM', create_request(image, directory, name, configuration))
                state['status'] = 'paused'
                write_record(directory / 'instance.json', state)
                client.call('ResumeVM')
            state['status'] = 'running'
            write_record(directory / 'instance.json', state)
            return state
        except (CLIError, OSError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            try:
                _rollback_launch(directory, state, error)
            except (CLIError, OSError, ValueError, KeyError, subprocess.SubprocessError) as cleanup_error:
                logger.error('Failed to finish launch rollback for %s; retain %s for recovery: %s',
                             name, directory, cleanup_error)
            raise CLIError('Launch failed for {}: {}. State and logs retained in {}. '
                           'Retry cleanup with az enclave destroy --name {}.'.format(
                               name, error, directory, name)) from error


def _validate_process(record: object) -> None:
    """Validate saved process information before destroy uses it for shutdown and cleanup."""
    if (not isinstance(record, dict) or type(record.get('pid')) is not int or record['pid'] <= 1
            or set(record) != {'pid', 'boot', 'start', 'command'}
            or any(not isinstance(record.get(key), str) or not record[key]
                   for key in ('boot', 'start', 'command'))
            or not re.fullmatch(r'[0-9]+', record['start'])
            or not re.fullmatch(r'[0-9a-f]{64}', record['command'])
            or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', record['boot'])):
        raise CLIError('Invalid process identity; shutdown cannot be confirmed. Retain state for manual recovery.')


def _validate_ownership(supervisor: Optional[dict], runtime: dict) -> None:
    """Check that instance and runtime records identify the same supervisor before destroy uses the runtime."""
    if supervisor is not None:
        _validate_process(supervisor)
    if runtime:
        _validate_process(runtime.get('openvmm'))
        _validate_process(runtime.get('supervisor'))
        if runtime['supervisor'] != supervisor:
            raise CLIError('Runtime ownership is inconsistent; refusing to use its endpoint.')


def _shutdown_runtime(directory: Path, process: dict, deadline: float) -> None:
    """Tear down the guest and quit its owned server, accepting a lost reply only after exit."""
    with VmClient(directory / 'rpc.sock', deadline) as client:
        properties = client.call('PropertiesVM')
        if properties.state != vm.Uninitialized:
            # TeardownVM releases the guest's resources but does not stop the RPC server.
            client.call('TeardownVM')
        try:
            client.call('Quit')
        except CLIError as error:
            cause = error.__cause__
            if not isinstance(cause, grpc.RpcError) or cause.code() != grpc.StatusCode.UNAVAILABLE:
                raise
            # OpenVMM can exit before acknowledging Quit; verify exit without sending signals.
            while alive(process):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.1, remaining))
            logger.debug('OpenVMM exited before acknowledging Quit: %s', error)


def _prepare_destroy(directory: Path, name: str, no_logs: bool) -> Tuple[dict, dict]:
    """Load validated ownership and persist the log policy before attempting shutdown."""
    check_private(directory)
    try:
        state = read_private_record(directory / 'instance.json')
    except (CLIError, OSError, ValueError) as error:
        raise finalization_error(directory, {}, error) from error
    if state.get('schema') != 1 or state.get('name') != name:
        raise CLIError('Unsupported or mismatched Enclave instance record: {}. '
                       'Manual cleanup is required; inspect this record before removing any files.'.format(
                           directory / 'instance.json'))
    if 'cleanup_phase' in state:
        raise finalization_error(directory, state, CLIError('An earlier cleanup attempt did not complete.'))
    # Match full process identities, not just PIDs, before trusting the management endpoint.
    runtime_path = directory / 'runtime.json'
    runtime = {}
    if runtime_path.exists() or runtime_path.is_symlink():
        runtime = read_private_record(runtime_path)
        if not runtime:
            raise CLIError('Empty runtime ownership record; shutdown cannot be confirmed: {}'.format(runtime_path))
    recover_supervisor = bool(runtime) and state.get('supervisor') is None
    supervisor = runtime.get('supervisor') if recover_supervisor else state.get('supervisor')
    _validate_ownership(supervisor, runtime)
    if recover_supervisor:
        logger.warning('Recovering the missing supervisor identity from the owned runtime record: %s', runtime_path)
        state['supervisor'] = supervisor
        write_record(directory / 'instance.json', state)
    policy = 'discard' if no_logs else state.get('log_policy', 'retain')
    if policy not in ('retain', 'discard'):
        raise CLIError('Invalid persisted log policy; retain state for manual recovery: {}'.format(directory))
    state['log_policy'] = policy
    write_record(directory / 'instance.json', state)
    return state, runtime


def _shutdown_instance(directory: Path, state: dict, runtime: dict, timeout: float) -> None:
    """Stop through RPC before owned-process recovery, retaining state on either failure."""
    server_alive = bool(runtime.get('openvmm')) and alive(runtime['openvmm'])
    worker_alive = bool(state.get('supervisor')) and alive(state['supervisor'])
    if server_alive:
        if not worker_alive:
            logger.warning('Supervisor exited; reconciling the verified owned OpenVMM server via RPC.')
        try:
            if not (directory / 'rpc.sock').is_socket():
                raise CLIError('Owned OpenVMM is alive but RPC socket is missing; destruction is unconfirmed.')
            _shutdown_runtime(directory, runtime['openvmm'], time.monotonic() + timeout)
        except (CLIError, OSError, KeyboardInterrupt) as error:
            # Preserve a retryable failure rather than forcing shutdown after unconfirmed RPC.
            state['status'] = 'destroy-failed'
            state['error'] = str(error) or 'Shutdown interrupted.'
            write_record(directory / 'instance.json', state)
            raise CLIError('Destroy RPC failed; instance retained for retry: {}. Name: {}. State/logs: {}.'.format(
                state['error'], state['name'], directory)) from error
    # Also reconcile a crashed server/supervisor rather than treating a missing socket as success.
    try:
        _stop_runtime(directory, state)
    except (CLIError, OSError, KeyboardInterrupt) as error:
        state.update(status='destroy-failed', cleanup_error=str(error) or 'Cleanup interrupted.')
        write_record(directory / 'instance.json', state)
        raise CLIError('Destroy cleanup incomplete; instance retained for retry: {}. Name: {}. State/logs: {}.'.format(
            state['cleanup_error'], state['name'], directory)) from error
    if (directory / 'worker-error.json').exists():
        logger.warning('Supervisor reported an error; resources are stopped. Inspect supervisor.log.')
    state['status'] = 'cleanup-pending'
    state.pop('error', None)
    state.pop('cleanup_error', None)
    write_record(directory / 'instance.json', state)


def destroy(name: str, timeout: float = 30, no_logs: bool = False) -> dict:
    """Confirm shutdown, retain logs unless explicitly discarded, and release instance state."""
    require_host(timeout)
    root = _instance._private_root(_instance.DEFAULT_STATE_DIR, create=False)
    directory = next((entry for entry in root.iterdir() if entry.name == name), None) if root.exists() else None
    if directory is None:
        raise CLIError('Enclave instance not found: {}'.format(name))
    if directory.is_symlink():
        raise CLIError('Instance directory must not be a symlink.')
    with instance_lock(directory):
        if not directory.exists():
            raise CLIError('Enclave instance not found: {}'.format(name))
        state, runtime = _prepare_destroy(directory, name, no_logs)
        _shutdown_instance(directory, state, runtime, timeout)
        try:
            with host_lock():
                return finalize(directory, state)
        except (CLIError, OSError, KeyboardInterrupt) as error:
            raise finalization_error(directory, state, error) from error
