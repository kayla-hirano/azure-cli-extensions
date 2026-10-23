# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Run the persistent OpenVMM parent process and collect COM1 after the launching CLI exits."""

import os
import selectors
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

from ._instance import cleanup_sockets, identity, read_record, write_record


def supervise(directory: Path, host_lock_fd: int) -> None:
    """Own the server child and serial listener, publish identities, and clean up when either stops."""
    # The launching CLI saved configuration here; state['openvmm'] is the executable path, not a PID.
    state = read_record(directory / 'instance.json')
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        """Defer signal-triggered shutdown to the main loop so normal child cleanup can run."""
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    child = None
    try:
        # Listen for COM1 before CreateVM connects its serial backend to this supervisor.
        with socket.socket(socket.AF_UNIX) as listener, selectors.DefaultSelector() as selector:
            listener.bind(str(directory / 'serial.sock'))
            listener.listen(1)
            listener.setblocking(False)
            selector.register(listener, selectors.EVENT_READ)
            with (directory / 'openvmm.log').open('ab', buffering=0) as host_log, \
                    (directory / 'com1.log').open('ab', buffering=0) as serial_log:
                # Start OpenVMM's CLI in gRPC-server mode on the local Unix socket rpc.sock.
                # The server survives the launching az command; CreateVM/ResumeVM boot the guest later.
                child = subprocess.Popen(
                    [state['openvmm'], '--rpc', 'path={},transport=grpc'.format(directory / 'rpc.sock')],
                    stdin=subprocess.DEVNULL, stdout=host_log, stderr=subprocess.STDOUT,
                    # Keep the host lease if this supervisor crashes before OpenVMM exits.
                    close_fds=True, pass_fds=(host_lock_fd,))
                child_identity = identity(child.pid)
                if child_identity is None:
                    raise RuntimeError('OpenVMM exited during startup; inspect openvmm.log.')
                # Unlike instance.json's executable path, runtime.json stores owned process identities.
                write_record(directory / 'runtime.json', {
                    'supervisor': identity(os.getpid()),
                    'openvmm': child_identity,
                })
                drain_until = None
                while not stopping:
                    if child.poll() is not None:
                        # After Quit (or a crash), briefly drain serial output before leaving the loop.
                        if drain_until is None:
                            drain_until = time.monotonic() + 1
                        if time.monotonic() >= drain_until:
                            break
                    for key, _ in selector.select(timeout=0.1):
                        if key.fileobj is listener:
                            connection, _ = listener.accept()
                            connection.setblocking(False)
                            selector.register(connection, selectors.EVENT_READ)
                        else:
                            connection = key.fileobj
                            data = connection.recv(65536)
                            if data:
                                serial_log.write(data)
                            else:
                                selector.unregister(connection)
                                connection.close()
                for key in list(selector.get_map().values()):
                    if key.fileobj is not listener:
                        key.fileobj.close()
    finally:
        if child is not None:
            if child.poll() is None:
                # Recovery for signals/errors: stop a server that did not already exit through Quit.
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            write_record(directory / 'exit.json', {'returncode': child.returncode})
        # Remove runtime endpoints only; keep logs and exit evidence for inspection.
        cleanup_sockets(directory)


def main() -> None:
    """Start the detached worker with private file permissions and retain fatal error evidence."""
    os.umask(0o077)
    directory = Path(sys.argv[1])
    try:
        supervise(directory, int(sys.argv[2]))
    except Exception:
        # This is a process boundary: retain the traceback and fail, rather than hiding worker errors.
        traceback.print_exc()
        write_record(directory / 'worker-error.json', {'error': 'Supervisor failed; inspect supervisor.log.'})
        raise


if __name__ == '__main__':
    main()
