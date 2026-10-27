# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Coordinate local L1 launch/destroy, RPC calls, guest readiness, and failure recovery."""

import hashlib
from io import BufferedReader, BufferedRWPair
import json
import os
import secrets
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Union

import grpc
from knack.log import get_logger
from knack.util import CLIError

from ._instance import (DEFAULT_STATE_DIR, alive, check_private, cleanup_sockets, host_lock, identity,
                        instance_lock, instance_path, read_record, require_host, stop_owned,
                        write_record)
from ._rpc import VmClient, create_request
from ._runtime import resolve_runtime
from .rpc import vmservice_pb2 as vm

logger = get_logger(__name__)
MAX_LINE_BYTES = 4 * 1024 * 1024
GUEST_VSOCK_PORT = 4050


def _instance_info(directory: Path) -> dict:
    """Describe instance endpoints and retained evidence for both lifecycle commands."""
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
        'response_file': str(directory / 'response.json'),
    }


def read_line(stream: Union[BufferedReader, BufferedRWPair], connection: Optional[socket.socket] = None,
              deadline: Optional[float] = None) -> bytes:
    """Read a buffered relay line in chunks without consuming bytes from the next line."""
    line = bytearray()
    while len(line) <= MAX_LINE_BYTES:
        if connection is not None and deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CLIError('Guest response timed out.')
            connection.settimeout(min(5, remaining))
        available = stream.peek(1)
        if not available:
            break
        newline = available.find(b'\n')
        size = len(available) if newline < 0 else newline + 1
        chunk = stream.read(min(size, MAX_LINE_BYTES + 1 - len(line)))
        line.extend(chunk)
        if chunk.endswith(b'\n'):
            break
    if not line:
        # The relay can close before the guest listener is ready; let the readiness loop retry.
        raise ConnectionError('Guest relay closed before returning a response.')
    if not line.endswith(b'\n') or len(line) > MAX_LINE_BYTES:
        raise CLIError('Guest relay returned an oversized or unterminated response.')
    return bytes(line)


def probe_guest(directory: Path, deadline: float) -> None:
    """Retry the BVT guest exchange and save an SNP-shaped response without verifying attestation."""
    last_error = 'Guest relay has not become available.'
    challenge = secrets.token_hex(32)
    while time.monotonic() < deadline:
        runtime = read_record(directory / 'runtime.json')
        if not alive(runtime['openvmm']) or not alive(runtime['supervisor']):
            raise CLIError('OpenVMM or its supervisor exited; inspect the instance logs.')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(min(5, max(0.01, deadline - time.monotonic())))
                connection.connect(str(directory / 'vsock.sock'))
                with connection.makefile('rwb') as stream:
                    # Same CONNECT handshake and JSON request as skr-bvt-client.py.
                    stream.write('CONNECT {}\n'.format(GUEST_VSOCK_PORT).encode('ascii'))
                    stream.flush()
                    if not read_line(stream, connection, deadline).startswith(b'OK '):
                        raise CLIError('OpenVMM rejected the guest relay connection.')
                    stream.write(json.dumps({
                        'challenge': challenge, 'payload': 'hello world',
                    }).encode('utf-8') + b'\n')
                    stream.flush()
                    response = read_line(stream, connection, deadline)
            value = json.loads(response)
            evidence = value.get('evidence', {}) if isinstance(value, dict) else {}
            report = evidence.get('report') if isinstance(evidence, dict) and evidence.get('tee') == 'snp' else None
            if not isinstance(report, str) or len(report) != 2368:
                raise CLIError('Guest did not return a 1184-byte SNP report; mock evidence is rejected.')
            try:
                bytes.fromhex(report)
            except ValueError as error:
                raise CLIError('Guest SNP report is not valid hexadecimal.') from error
            (directory / 'response.json').write_bytes(response)
            return
        except OSError as error:
            last_error = str(error)
            logger.debug('Waiting for enclave application: %s', error)
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        except (ValueError, TypeError) as error:
            raise CLIError('Invalid guest JSON response: {}'.format(error)) from error
    raise CLIError('Guest readiness timed out: {}'.format(last_error))


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
    """Start the persistent OpenVMM owner and save its process identity."""
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent) + (
        os.pathsep + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
    with (directory / 'supervisor.log').open('ab') as log:
        worker = subprocess.Popen(
            [sys.executable, '-m', 'azext_ace._worker', str(directory), str(host_lock_fd)],
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


def launch(name: str, igvm: str, openvmm: str = 'openvmm',
           state_dir: str = DEFAULT_STATE_DIR, timeout: float = 120,
           allow_untested_runtime: bool = False) -> dict:
    """Create and resume a supervised SNP instance, persisting state and rolling back failures."""
    require_host(timeout)
    image = Path(igvm).expanduser().resolve()
    if not image.is_file():
        raise CLIError('IGVM file not found: {}'.format(image))
    device = Path('/dev/mshv')
    if not device.exists() or not stat.S_ISCHR(device.stat().st_mode):
        raise CLIError('L1 requires the /dev/mshv character device.')
    executable = resolve_runtime(openvmm, allow_untested_runtime)
    executable_digest = _file_digest(executable)
    image_digest = _file_digest(image)
    directory = instance_path(state_dir, name)
    deadline = time.monotonic() + timeout
    with host_lock() as host_lock_fd, instance_lock(directory):
        if directory.exists():
            raise CLIError('Instance already exists. Use a new --name; existing evidence is retained.')
        directory.mkdir(mode=0o700)
        state = {
            'schema': 1, 'name': name, 'status': 'starting', 'igvm': str(image),
            'openvmm': str(executable), 'directory': str(directory),
            'openvmm_sha256': executable_digest, 'igvm_sha256': image_digest,
            'attestation_verified': False,
        }
        state.update(_instance_info(directory))
        write_record(directory / 'instance.json', state)
        try:
            _start_supervisor(directory, state, host_lock_fd)
            # Process ownership must be published before waiting for the gRPC endpoint to be usable.
            _wait_runtime(directory, state['supervisor'], deadline)
            with VmClient(directory / 'rpc.sock', deadline) as client:
                client.ready()
                # CreateVM allocates a paused SNP guest; ResumeVM starts guest execution.
                client.call('CreateVM', create_request(image, directory, name))
                state['status'] = 'paused'
                write_record(directory / 'instance.json', state)
                client.call('ResumeVM')
            # Closing the client channel leaves OpenVMM running. Probe the guest via vsock, not gRPC.
            probe_guest(directory, deadline)
            state['status'] = 'running'
            write_record(directory / 'instance.json', state)
            logger.warning('SNP report received; its signature, measurement and trust chain are NOT verified.')
            return state
        except (CLIError, OSError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            _rollback_launch(directory, state, error)
            raise CLIError('Launch failed: {}. Evidence retained in {}'.format(error, directory)) from error


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


def destroy(name: str, state_dir: str = DEFAULT_STATE_DIR, timeout: float = 30) -> dict:
    """Reconcile an instance, tear down its VM through RPC, and preserve logs and image files."""
    require_host(timeout)
    directory = instance_path(state_dir, name)
    with instance_lock(directory):
        if not directory.exists():
            raise CLIError('ACE instance not found: {}'.format(name))
        check_private(directory)
        state = read_record(directory / 'instance.json')
        if state.get('schema') != 1 or state.get('name') != name:
            raise CLIError('Unsupported or mismatched ACE instance record.')
        state.update(_instance_info(directory))
        # Match full process identities, not just PIDs, before trusting the management endpoint.
        runtime_path = directory / 'runtime.json'
        runtime = read_record(runtime_path) if runtime_path.exists() else {}
        server_alive = bool(runtime.get('openvmm')) and alive(runtime['openvmm'])
        worker_alive = bool(state.get('supervisor')) and alive(state['supervisor'])
        if server_alive and runtime['supervisor'] != state.get('supervisor'):
            raise CLIError('Runtime ownership is inconsistent; refusing to use its endpoint.')
        if server_alive:
            if not worker_alive:
                logger.warning('Supervisor exited; reconciling the verified owned OpenVMM server via RPC.')
            if not (directory / 'rpc.sock').is_socket():
                raise CLIError('Owned OpenVMM is alive but RPC socket is missing; destruction is unconfirmed.')
            try:
                _shutdown_runtime(directory, runtime['openvmm'], time.monotonic() + timeout)
            except CLIError as error:
                # Preserve a retryable failure rather than forcing shutdown after unconfirmed RPC.
                state['status'] = 'destroy-failed'
                state['error'] = str(error)
                write_record(directory / 'instance.json', state)
                raise CLIError('Destroy RPC failed; instance retained for retry: {}'.format(error)) from error
        # Also reconcile a crashed server/supervisor rather than treating a missing socket as success.
        _stop_runtime(directory, state)
        if (directory / 'worker-error.json').exists():
            logger.warning('Supervisor reported an error; resources are stopped. Inspect supervisor.log.')
        state['status'] = 'destroyed'
        state.pop('error', None)
        state.pop('cleanup_error', None)
        write_record(directory / 'instance.json', state)
        return state
