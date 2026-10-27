# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Test BVT request parity, gRPC transport, guest probing, and lifecycle recovery without real L2 boot."""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import grpc
from google.protobuf.empty_pb2 import Empty
from knack.util import CLIError

from azext_ace import _instance, _lifecycle, _rpc, _runtime
from azext_ace import _worker
from azext_ace.rpc import vmservice_pb2 as vm


class RpcContractTest(unittest.TestCase):
    """Check protobuf mapping and the wire contract independently of OpenVMM execution."""

    def test_request_preserves_bvt_configuration(self):
        """Round-trip the request and compare boot/resources/devices with the existing BVT."""
        directory = Path('/var/lib/ace/instances/demo')
        request = _rpc.create_request(Path('/images/bvt.igvm'), directory, 'demo')
        decoded = vm.CreateVMRequest.FromString(request.SerializeToString())
        config = decoded.config
        self.assertEqual(decoded.log_id, 'demo')
        self.assertEqual(config.WhichOneof('BootConfig'), 'igvm')
        self.assertEqual(config.igvm.personality, vm.IgvmBoot.PERSONALITY_LINUX_DIRECT)
        self.assertEqual(config.isolation_config.WhichOneof('isolation_type'), 'snp')
        self.assertEqual(config.igvm.igvm_path, str(Path('/images/bvt.igvm')))
        self.assertEqual(config.memory_config.memory_mb, 160)
        self.assertEqual(config.processor_config.processor_count, 1)
        self.assertTrue(config.disable_vmbus)
        self.assertFalse(config.disable_hv)
        serial = config.serial_config.ports[0]
        self.assertEqual(serial.port, 0)
        self.assertTrue(serial.connect)
        self.assertEqual(serial.socket_path, str(directory / 'serial.sock'))
        root = config.pcie.root_complexes[0]
        self.assertEqual((root.name, root.start_bus, root.end_bus), ('rc0', 0, 1))
        self.assertEqual((root.low_mmio, root.low_mmio_base), (16 * 1024 * 1024, 0xf7000000))
        self.assertEqual((root.high_mmio, root.high_mmio_base), (1024 * 1024, 0x100000000))
        port = root.root_ports[0]
        self.assertEqual(port.name, 'vsock')
        self.assertEqual(port.attached.device.virtio.vsock.socket_path, str(directory / 'vsock.sock'))

    def test_unix_channel_uses_valid_http2_authority(self) -> None:
        """Keep the socket path out of the authority header rejected by OpenVMM's HTTP/2 server."""
        path = Path('/var/lib/ace/instances/demo/rpc.sock')
        with patch('azext_ace._rpc.grpc.insecure_channel') as channel:
            with _rpc.VmClient(path, time.monotonic() + 5):
                channel.assert_called_once_with(
                    'unix:' + str(path),
                    options=(('grpc.default_authority', 'localhost'),))
            channel.return_value.close.assert_called_once_with()

    def test_real_grpc_round_trip(self):
        """Exercise actual serialization and status handling against a localhost test service."""
        received = []

        def create(request, _context):
            """Record the decoded creation request and acknowledge it like the upstream service."""
            received.append(request)
            return Empty()

        server = grpc.server(ThreadPoolExecutor(max_workers=2))
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler('vmservice.VM', {
            'CreateVM': grpc.unary_unary_rpc_method_handler(
                create, request_deserializer=vm.CreateVMRequest.FromString,
                response_serializer=Empty.SerializeToString),
            'PropertiesVM': grpc.unary_unary_rpc_method_handler(
                lambda _r, _c: vm.PropertiesVMResponse(state=vm.Running),
                request_deserializer=vm.PropertiesVMRequest.FromString,
                response_serializer=vm.PropertiesVMResponse.SerializeToString),
        }),))
        port = server.add_insecure_port('127.0.0.1:0')
        server.start()
        channel = grpc.insecure_channel('127.0.0.1:{}'.format(port))
        try:
            with patch('azext_ace._rpc.grpc.insecure_channel', return_value=channel):
                with _rpc.VmClient(Path('unused'), time.monotonic() + 5) as client:
                    client.ready()
                    client.call('CreateVM', _rpc.create_request(Path('bvt'), Path('instance'), 'demo'))
                    self.assertEqual(client.call('PropertiesVM').state, vm.Running)
                    with self.assertRaisesRegex(CLIError, 'UNIMPLEMENTED'):
                        client.call('ResumeVM')
            self.assertEqual(received[0].log_id, 'demo')
        finally:
            channel.close()
            server.stop(0).wait()

    def test_rpc_deadline_and_readiness_errors(self):
        """Surface expired operation budgets and endpoints that never become ready."""
        with _rpc.VmClient(Path('unused'), time.monotonic() - 1) as client:
            with self.assertRaisesRegex(CLIError, 'timed out'):
                client.call('Quit')
        with patch('azext_ace._rpc.grpc.channel_ready_future') as ready:
            ready.return_value.result.side_effect = grpc.FutureTimeoutError()
            with _rpc.VmClient(Path('unused'), time.monotonic() + 1) as client:
                with self.assertRaisesRegex(CLIError, 'did not become ready'):
                    client.ready()


class LinuxFilesystemTest(unittest.TestCase):
    """Share test-only compatibility shims without changing Linux production guards."""

    def setUp(self):
        """Provide missing Linux constants when mocked filesystem/socket tests run on Windows."""
        if sys.platform != 'linux':
            # Unit tests can exercise records on Windows without weakening Linux production flags.
            patcher = patch('azext_ace._instance.os.O_NOFOLLOW', 0, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
            patcher = patch('socket.AF_UNIX', 1, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)


class InstanceTest(LinuxFilesystemTest):
    """Exercise record safety, process identity, host prerequisites, and runtime provenance checks."""

    def test_line_limits(self):
        """Retry empty connections but reject oversized or incomplete relay lines."""
        self.assertEqual(_lifecycle.read_line(io.BytesIO(b'OK 4050\n')), b'OK 4050\n')
        with self.assertRaisesRegex(ConnectionError, 'closed before'):
            _lifecycle.read_line(io.BytesIO(b''))
        for line in (b'unterminated', b'x' * (_lifecycle.MAX_LINE_BYTES + 1) + b'\n'):
            with self.subTest(line_size=len(line)), self.assertRaises(CLIError):
                _lifecycle.read_line(io.BytesIO(line))

    def test_name_validation(self):
        """Reject instance names that are empty, too long, or could escape their state root."""
        for name in ('../other', '', 'a/b', 'a' * 33):
            with self.subTest(name=name), self.assertRaisesRegex(CLIError, '--name'):
                _instance.instance_path('unused', name)

    def test_host_validation(self):
        """Reject unsupported hosts and invalid timeout values before lifecycle work starts."""
        with patch('azext_ace._instance.sys.platform', 'win32'):
            with self.assertRaisesRegex(CLIError, 'Linux L1'):
                _instance.require_host(10)
        with patch('azext_ace._instance.sys.platform', 'linux'), \
                patch('azext_ace._instance.os.geteuid', return_value=0, create=True):
            for value in (0, -1, float('inf'), float('nan')):
                with self.subTest(value=value), self.assertRaisesRegex(CLIError, '--timeout'):
                    _instance.require_host(value)

    def test_pid_reuse_is_not_signaled(self):
        """Ensure cleanup does not signal a different process that reused a recorded PID."""
        record = {'pid': 321, 'start': '100'}
        with patch('azext_ace._instance.identity', return_value={'pid': 321, 'start': '101'}), \
                patch('azext_ace._instance.os.kill') as kill:
            _instance.stop_owned(record)
            kill.assert_not_called()

    def test_refuses_to_remove_regular_runtime_file(self):
        """Retain unexpected regular files rather than deleting them as runtime sockets."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'rpc.sock').write_text('not a socket')
            with self.assertRaisesRegex(CLIError, 'non-socket'):
                _instance.cleanup_sockets(directory)
            self.assertTrue((directory / 'rpc.sock').exists())

    def test_record_round_trip(self):
        """Verify atomic record replacement exposes the newest state and leaves no temporary file."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'instance.json'
            _instance.write_record(path, {'status': 'starting'})
            _instance.write_record(path, {'status': 'running'})
            self.assertEqual(_instance.read_record(path), {'status': 'running'})
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_runtime_requires_known_package_or_explicit_opt_in(self):
        """Require expected package provenance unless the caller knowingly permits a custom runtime."""
        with patch('azext_ace._runtime.shutil.which', side_effect=['/usr/bin/openvmm', '/usr/bin/rpm']), \
                patch('azext_ace._runtime.subprocess.run',
                      return_value=SimpleNamespace(returncode=0, stdout='0.2.0-2.azl3')):
            self.assertEqual(_runtime.resolve_runtime('openvmm', False).name, 'openvmm')
        with patch('azext_ace._runtime.shutil.which', side_effect=['/custom/openvmm', None]):
            with self.assertRaisesRegex(CLIError, 'low-ECAM'):
                _runtime.resolve_runtime('openvmm', False)
        with patch('azext_ace._runtime.shutil.which', side_effect=['/custom/openvmm', None]), \
                patch('azext_ace._runtime.logger.warning') as warning:
            _runtime.resolve_runtime('openvmm', True)
            warning.assert_called_once()

    def test_modified_package_requires_explicit_opt_in(self):
        """Treat failed RPM integrity verification as an unrecognized runtime."""
        with patch('azext_ace._runtime.shutil.which', side_effect=['/usr/bin/openvmm', '/usr/bin/rpm']), \
                patch('azext_ace._runtime.subprocess.run', side_effect=[
                    SimpleNamespace(returncode=0, stdout='0.2.0-2.azl3'),
                    SimpleNamespace(returncode=1, stdout='modified executable')]):
            with self.assertRaisesRegex(CLIError, 'unrecognized runtime'):
                _runtime.resolve_runtime('openvmm', False)

    def test_package_verification_timeout_is_actionable(self):
        """Translate a stalled RPM check into a clear CLI error."""
        import subprocess
        with patch('azext_ace._runtime.shutil.which', side_effect=['/usr/bin/openvmm', '/usr/bin/rpm']), \
                patch('azext_ace._runtime.subprocess.run',
                      side_effect=subprocess.TimeoutExpired('rpm', 10)):
            with self.assertRaisesRegex(CLIError, 'Timed out verifying'):
                _runtime.resolve_runtime('openvmm', False)

    def test_slow_guest_response_has_total_deadline(self):
        """Prevent a slow response from extending the caller's absolute readiness deadline."""
        with self.assertRaisesRegex(CLIError, 'timed out'):
            _lifecycle.read_line(io.BytesIO(b'OK\n'), MagicMock(), time.monotonic() - 1)

    @unittest.skipUnless(sys.platform == 'linux', 'Requires Linux /proc and flock')
    def test_linux_identity_and_exclusive_lock(self):
        """Use real Linux process/lock primitives and reject symlinked state-directory paths."""
        current = _instance.identity(os.getpid())
        self.assertTrue(_instance.alive(current))
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            base.chmod(0o700)
            directory = _instance.instance_path(str(base), 'demo')
            with _instance.instance_lock(directory):
                with self.assertRaisesRegex(CLIError, 'Another command'):
                    with _instance.instance_lock(directory):
                        self.fail('Second lock should not be acquired')
            target = base / 'target'
            target.mkdir(mode=0o700)
            (base / 'link').symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(CLIError, 'symlink'):
                _instance.instance_path(str(base / 'link'), 'demo')


class GuestProbeTest(LinuxFilesystemTest):
    """Test the existing relay protocol and the stronger SNP-shape readiness check."""

    def test_probe_retries_empty_handshake_or_response(self) -> None:
        """Allow early relay closures during boot without accepting an empty guest response."""
        response = json.dumps({'evidence': {'tee': 'snp', 'report': '00' * 1184}}).encode() + b'\n'
        for initial in (b'', b'OK 4050\n'):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                _instance.write_record(directory / 'runtime.json', {'openvmm': {}, 'supervisor': {}})
                streams = []
                for data in (initial, b'OK 4050\n' + response):
                    stream = MagicMock()
                    stream.read.side_effect = io.BytesIO(data).read
                    streams.append(stream)
                with patch('azext_ace._lifecycle.alive', return_value=True), \
                        patch('azext_ace._lifecycle.socket.socket') as factory, \
                        patch('azext_ace._lifecycle.time.sleep') as sleep, \
                        patch('azext_ace._lifecycle.logger.debug') as debug:
                    connection = factory.return_value.__enter__.return_value
                    connection.makefile.return_value.__enter__.side_effect = streams
                    _lifecycle.probe_guest(directory, time.monotonic() + 5)
                    self.assertEqual(connection.connect.call_count, 2)
                    sleep.assert_called_once()
                    debug.assert_called_once()
                self.assertEqual((directory / 'response.json').read_bytes(), response)

    def test_probe_empty_connections_respect_deadline(self) -> None:
        """Keep repeated early closures bounded by the original readiness budget."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _instance.write_record(directory / 'runtime.json', {'openvmm': {}, 'supervisor': {}})
            clock = [0.0]

            def advance(seconds: float) -> None:
                """Advance the test clock instead of waiting for another boot-time retry."""
                clock[0] += seconds

            with patch('azext_ace._lifecycle.alive', return_value=True), \
                    patch('azext_ace._lifecycle.socket.socket') as factory, \
                    patch('azext_ace._lifecycle.time.monotonic', side_effect=lambda: clock[0]), \
                    patch('azext_ace._lifecycle.time.sleep', side_effect=advance):
                connection = factory.return_value.__enter__.return_value
                stream = connection.makefile.return_value.__enter__.return_value
                stream.read.return_value = b''
                with self.assertRaisesRegex(CLIError, 'readiness timed out: Guest relay closed'):
                    _lifecycle.probe_guest(directory, 1.0)
                self.assertEqual(connection.connect.call_count, 2)
                self.assertFalse((directory / 'response.json').exists())

    def test_probe_accepts_only_snp_report_shape(self):
        """Preserve the BVT request while rejecting mock, malformed, or incorrectly sized reports."""
        for evidence in (
                {'tee': 'snp', 'report': '00' * 1184},
                {'tee': 'mock', 'report': None},
                {'tee': 'snp', 'report': 'zz' * 1184},
                {'tee': 'snp', 'report': '00'},
                {'tee': 'mock', 'report': '00' * 1184},
                None,
                'snp',
                []):
            with self.subTest(evidence_type=type(evidence).__name__), \
                    tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                _instance.write_record(directory / 'runtime.json', {'openvmm': {}, 'supervisor': {}})
                response = json.dumps({'evidence': evidence}).encode() + b'\n'
                stream = MagicMock()
                incoming = io.BytesIO(b'OK 4050\n' + response)
                stream.read.side_effect = incoming.read
                with patch('azext_ace._lifecycle.alive', return_value=True), \
                        patch('azext_ace._lifecycle.socket.socket') as factory:
                    connection = factory.return_value.__enter__.return_value
                    connection.makefile.return_value.__enter__.return_value = stream
                    if evidence == {'tee': 'snp', 'report': '00' * 1184}:
                        _lifecycle.probe_guest(directory, time.monotonic() + 5)
                        self.assertEqual((directory / 'response.json').read_bytes(), response)
                        self.assertEqual(stream.write.call_args_list[0].args, (b'CONNECT 4050\n',))
                        request = json.loads(stream.write.call_args_list[1].args[0])
                        self.assertEqual(request['payload'], 'hello world')
                        self.assertEqual(len(request['challenge']), 64)
                    else:
                        with self.assertRaises(CLIError):
                            _lifecycle.probe_guest(directory, time.monotonic() + 5)
                        self.assertFalse((directory / 'response.json').exists())

    def test_probe_detects_exited_runtime(self):
        """Stop retrying guest readiness when the recorded runtime has already exited."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _instance.write_record(directory / 'runtime.json', {'openvmm': {}, 'supervisor': {}})
            with patch('azext_ace._lifecycle.alive', return_value=False):
                with self.assertRaisesRegex(CLIError, 'exited'):
                    _lifecycle.probe_guest(directory, time.monotonic() + 1)


class WorkerTest(LinuxFilesystemTest):
    """Exercise the persistent supervisor with simulated server and serial connections."""

    def test_supervisor_captures_com1_and_reaps_server(self):
        """Capture guest serial bytes and record a clean child exit without unnecessary signals."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _instance.write_record(directory / 'instance.json', {'openvmm': '/usr/bin/openvmm'})
            with patch('azext_ace._worker.socket.socket') as sockets, \
                    patch('azext_ace._worker.selectors.DefaultSelector') as selectors, \
                    patch('azext_ace._worker.subprocess.Popen') as popen, \
                    patch('azext_ace._worker.identity', return_value={'pid': 123}), \
                    patch('azext_ace._worker.time.monotonic', side_effect=[0, 2]), \
                    patch('azext_ace._worker.signal.signal'):
                listener = sockets.return_value.__enter__.return_value
                connection = MagicMock()
                listener.accept.return_value = (connection, None)
                connection.recv.return_value = b'guest serial output\n'
                selector = selectors.return_value.__enter__.return_value
                selector.select.side_effect = [
                    [(SimpleNamespace(fileobj=listener), 1)],
                    [(SimpleNamespace(fileobj=connection), 1)],
                ]
                child = popen.return_value
                child.poll.side_effect = [None, None, 0, 0]
                child.returncode = 0
                _worker.supervise(directory, 47)
                arguments = popen.call_args.args[0]
                self.assertEqual(arguments[:2], ['/usr/bin/openvmm', '--rpc'])
                self.assertIn('transport=grpc', arguments[2])
                self.assertEqual(popen.call_args.kwargs['pass_fds'], (47,))
                self.assertEqual((directory / 'com1.log').read_bytes(), b'guest serial output\n')
                self.assertEqual(_instance.read_record(directory / 'exit.json')['returncode'], 0)
                child.terminate.assert_not_called()


class LifecycleTest(LinuxFilesystemTest):
    """Test launch/destroy state transitions and rollback using isolated records and mocked RPC."""

    def setUp(self):
        """Create temporary artifacts and replace host/process/RPC boundaries with controlled doubles."""
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.directory = self.base / 'demo'
        self.image = self.base / 'bvt.igvm'
        self.image.write_bytes(b'test image')
        self.executable = self.base / 'openvmm'
        self.executable.write_bytes(b'test executable')
        patches = {
            'require_host': None,
            'instance_path': self.directory,
            'instance_lock': nullcontext(),
            'host_lock': nullcontext(47),
            'check_private': None,
            'resolve_runtime': self.executable,
        }
        for name, result in patches.items():
            patcher = patch('azext_ace._lifecycle.' + name, return_value=result)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('azext_ace._lifecycle.VmClient')
        self.client = patcher.start().return_value.__enter__.return_value
        self.addCleanup(patcher.stop)

    def prepare_destroy(self):
        """Seed matching instance/runtime records for destruction and crash-recovery scenarios."""
        self.directory.mkdir()
        state = {'schema': 1, 'name': 'demo', 'status': 'running', 'supervisor': {'pid': 123}}
        _instance.write_record(self.directory / 'instance.json', state)
        _instance.write_record(self.directory / 'runtime.json', {
            'supervisor': {'pid': 123}, 'openvmm': {'pid': 456},
        })
        return state

    def test_supervisor_start_preserves_environment_and_records_identity(self) -> None:
        """Keep source discovery and detached process ownership when extracting startup."""
        self.directory.mkdir()
        state = {'status': 'starting'}
        with patch.dict(os.environ, {'PYTHONPATH': 'existing-source'}), \
                patch('azext_ace._lifecycle.subprocess.Popen') as popen, \
                patch('azext_ace._lifecycle.identity', return_value={'pid': 123}):
            _lifecycle._start_supervisor(self.directory, state, 47)
        options = popen.call_args.kwargs
        self.assertTrue(options['start_new_session'])
        self.assertTrue(options['close_fds'])
        self.assertEqual(options['pass_fds'], (47,))
        self.assertEqual(popen.call_args.args[0][-1], '47')
        self.assertEqual(options['stdin'], _lifecycle.subprocess.DEVNULL)
        self.assertEqual(
            options['env']['PYTHONPATH'],
            str(Path(_lifecycle.__file__).resolve().parent.parent) + os.pathsep + 'existing-source')
        self.assertEqual(_instance.read_record(self.directory / 'instance.json'), {
            'status': 'starting', 'supervisor': {'pid': 123},
        })

    def test_supervisor_early_exit_is_reported(self) -> None:
        """Reap an exited worker without publishing a successful supervisor record."""
        self.directory.mkdir()
        with patch('azext_ace._lifecycle.subprocess.Popen') as popen, \
                patch('azext_ace._lifecycle.identity', return_value=None):
            with self.assertRaisesRegex(CLIError, 'Supervisor failed to start'):
                _lifecycle._start_supervisor(self.directory, {}, 47)
        popen.return_value.wait.assert_called_once_with(timeout=5)
        self.assertFalse((self.directory / 'instance.json').exists())

    def test_rollback_retains_original_error_when_cleanup_fails(self) -> None:
        """Recover after failed Quit and record cleanup errors without hiding the launch error."""
        self.directory.mkdir()
        state = {'status': 'paused', 'supervisor': {'pid': 123}}
        self.client.call.side_effect = CLIError('Quit rejected')
        with patch('azext_ace._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_ace._lifecycle._stop_runtime', side_effect=CLIError('still alive')) as stop, \
                patch('azext_ace._lifecycle.logger.warning') as warning, \
                patch('azext_ace._lifecycle.logger.error') as log_error:
            _lifecycle._rollback_launch(self.directory, state, CLIError('resume rejected'))
        self.client.call.assert_called_once_with('Quit')
        stop.assert_called_once_with(self.directory, state)
        warning.assert_called_once()
        log_error.assert_called_once()
        saved = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['error'], 'resume rejected')
        self.assertEqual(saved['cleanup_error'], 'still alive')

    def test_launch_create_then_resume_and_persist(self):
        """Check creation precedes resume and successful readiness produces persistent running state."""
        device = MagicMock()
        device.st_mode = 0o020600
        original_exists = Path.exists
        original_stat = Path.stat
        with patch('azext_ace._lifecycle.stat.S_ISCHR', return_value=True), \
                patch('azext_ace._lifecycle.Path.exists', autospec=True,
                      side_effect=lambda path: True if str(path) == str(Path('/dev/mshv'))
                      else original_exists(path)), \
                patch('azext_ace._lifecycle.Path.stat', autospec=True,
                      side_effect=lambda path, **kw: device if str(path) == str(Path('/dev/mshv'))
                      else original_stat(path, **kw)), \
                patch('azext_ace._lifecycle.subprocess.Popen') as popen, \
                patch('azext_ace._lifecycle.identity', return_value={'pid': 123}), \
                patch('azext_ace._lifecycle._wait_runtime'), \
                patch('azext_ace._lifecycle.probe_guest') as probe:
            result = _lifecycle.launch('demo', str(self.image))
            self.assertEqual(result['status'], 'running')
            self.assertEqual(result['name'], 'demo')
            self.assertEqual(result['vsock'], {
                'port': 4050, 'socket': str(self.directory / 'vsock.sock'),
            })
            self.assertEqual(result['rpc_socket'], str(self.directory / 'rpc.sock'))
            self.assertEqual(result['logs'], {
                name: str(self.directory / (name + '.log'))
                for name in ('openvmm', 'supervisor', 'com1')
            })
            self.assertEqual(result['instance_file'], str(self.directory / 'instance.json'))
            self.assertEqual(result['response_file'], str(self.directory / 'response.json'))
            self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                             ['CreateVM', 'ResumeVM'])
            probe.assert_called_once()
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
            self.assertEqual(popen.call_args.kwargs['pass_fds'], (47,))
        self.assertEqual(_instance.read_record(self.directory / 'instance.json'), result)

    def test_destroy_teardown_then_quit_and_preserve_image(self):
        """Release the VM before quitting its server while retaining the reusable IGVM."""
        self.prepare_destroy()
        self.client.call.return_value = vm.PropertiesVMResponse(state=vm.Running)
        with patch('azext_ace._lifecycle.alive', return_value=True), \
                patch('azext_ace._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_ace._lifecycle.host_lock') as host_guard, \
                patch('azext_ace._lifecycle._stop_runtime') as stop:
            result = _lifecycle.destroy('demo')
            self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                             ['PropertiesVM', 'TeardownVM', 'Quit'])
            stop.assert_called_once()
            host_guard.assert_not_called()
        self.assertEqual(result['status'], 'destroyed')
        self.assertEqual(result['name'], 'demo')
        self.assertEqual(result['vsock']['port'], 4050)
        self.assertEqual(result['logs']['com1'], str(self.directory / 'com1.log'))
        self.assertEqual(result['response_file'], str(self.directory / 'response.json'))
        self.assertEqual(_instance.read_record(self.directory / 'instance.json'), result)
        self.assertTrue(self.image.exists())

    def test_launch_rejects_busy_host_before_starting_processes(self) -> None:
        """A different name or custom state root must not bypass the single-enclave policy."""
        original_exists = Path.exists
        original_stat = Path.stat
        device = SimpleNamespace(st_mode=0o020600)
        with patch('azext_ace._lifecycle.Path.exists', autospec=True,
                   side_effect=lambda path: True if str(path) == str(Path('/dev/mshv'))
                   else original_exists(path)), \
                patch('azext_ace._lifecycle.Path.stat', autospec=True,
                      side_effect=lambda path, **kw: device if str(path) == str(Path('/dev/mshv'))
                      else original_stat(path, **kw)), \
                patch('azext_ace._lifecycle.host_lock',
                      side_effect=CLIError('An ACE enclave is already starting or running on this L1.')), \
                patch('azext_ace._lifecycle.subprocess.Popen') as popen:
            with self.assertRaisesRegex(CLIError, 'already starting or running'):
                _lifecycle.launch('another-demo', str(self.image), state_dir=str(self.base / 'other-root'))
            popen.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_destroy_rpc_failure_preserves_state_for_retry(self):
        """Keep failed destruction explicit and retryable instead of claiming cleanup succeeded."""
        self.prepare_destroy()
        self.client.call.side_effect = CLIError('RPC unavailable')
        with patch('azext_ace._lifecycle.alive', return_value=True), \
                patch('azext_ace._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_ace._lifecycle._stop_runtime') as stop:
            with self.assertRaisesRegex(CLIError, 'retained for retry'):
                _lifecycle.destroy('demo')
            stop.assert_not_called()
        self.assertEqual(_instance.read_record(self.directory / 'instance.json')['status'], 'destroy-failed')

    def test_destroy_missing_socket_does_not_claim_success(self):
        """Distinguish an unreachable management endpoint from a stopped owned server."""
        self.prepare_destroy()
        with patch('azext_ace._lifecycle.alive', return_value=True):
            with self.assertRaisesRegex(CLIError, 'destruction is unconfirmed'):
                _lifecycle.destroy('demo')

    def test_repeated_destroy_reconciles_dead_processes(self):
        """Allow repeated cleanup of a recorded instance whose processes have already exited."""
        self.prepare_destroy()
        with patch('azext_ace._lifecycle.alive', return_value=False), \
                patch('azext_ace._lifecycle._stop_runtime'):
            first = _lifecycle.destroy('demo')
            second = _lifecycle.destroy('demo')
            self.assertEqual(first, second)
            self.assertEqual(second['status'], 'destroyed')
            self.assertEqual(second['vsock']['port'], 4050)
            self.assertEqual(second['logs']['openvmm'], str(self.directory / 'openvmm.log'))
        self.client.call.assert_not_called()

    def test_destroy_refuses_mismatched_ownership(self):
        """Refuse RPC access when persisted supervisor identities disagree."""
        state = self.prepare_destroy()
        state['supervisor'] = {'pid': 999}
        _instance.write_record(self.directory / 'instance.json', state)
        with patch('azext_ace._lifecycle.alive', return_value=True):
            with self.assertRaisesRegex(CLIError, 'ownership is inconsistent'):
                _lifecycle.destroy('demo')
        self.client.call.assert_not_called()

    def test_destroy_uninitialized_server_only_quits(self):
        """Avoid tearing down a nonexistent VM while still exiting its owned server."""
        self.prepare_destroy()
        self.client.call.return_value = vm.PropertiesVMResponse(state=vm.Uninitialized)
        with patch('azext_ace._lifecycle.alive', return_value=True), \
                patch('azext_ace._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_ace._lifecycle._stop_runtime'):
            _lifecycle.destroy('demo')
        self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                         ['PropertiesVM', 'Quit'])

    def test_destroy_reconciles_server_after_supervisor_crash(self):
        """Use RPC to stop an owned server even if its matching supervisor has died."""
        self.prepare_destroy()
        self.client.call.return_value = vm.PropertiesVMResponse(state=vm.Running)
        with patch('azext_ace._lifecycle.alive', side_effect=lambda record: record['pid'] == 456), \
                patch('azext_ace._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_ace._lifecycle._stop_runtime'), \
                patch('azext_ace._lifecycle.logger.warning') as warning:
            self.assertEqual(_lifecycle.destroy('demo')['status'], 'destroyed')
            warning.assert_called_once()

    def test_launch_startup_failure_rolls_back_and_retains_evidence(self):
        """Recover from startup failure and retain failed state plus input identities for diagnosis."""
        original_exists = Path.exists
        original_stat = Path.stat
        device = SimpleNamespace(st_mode=0o020600)
        with patch('azext_ace._lifecycle.Path.exists', autospec=True,
                   side_effect=lambda path: True if str(path) == str(Path('/dev/mshv'))
                   else original_exists(path)), \
                patch('azext_ace._lifecycle.Path.stat', autospec=True,
                      side_effect=lambda path, **kw: device if str(path) == str(Path('/dev/mshv'))
                      else original_stat(path, **kw)), \
                patch('azext_ace._lifecycle.subprocess.Popen'), \
                patch('azext_ace._lifecycle.identity', return_value={'pid': 123}), \
                patch('azext_ace._lifecycle._wait_runtime', side_effect=CLIError('startup timeout')), \
                patch('azext_ace._lifecycle._stop_runtime') as stop:
            with self.assertRaisesRegex(CLIError, 'Launch failed.*startup timeout'):
                _lifecycle.launch('demo', str(self.image))
            stop.assert_called_once()
        state = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(len(state['igvm_sha256']), 64)
        self.assertFalse(state['attestation_verified'])

    def test_resume_failure_quits_created_vm_and_recovers_owned_processes(self):
        """Quit and clean up a created VM when resume fails, without probing an unstarted guest."""
        original_exists = Path.exists
        original_stat = Path.stat
        device = SimpleNamespace(st_mode=0o020600)

        def rpc_call(method, *_args):
            """Simulate successful creation/quit but an explicit ResumeVM failure."""
            if method == 'ResumeVM':
                raise CLIError('resume rejected')
            return Empty()

        self.client.call.side_effect = rpc_call
        with patch('azext_ace._lifecycle.Path.exists', autospec=True,
                   side_effect=lambda path: True if str(path) == str(Path('/dev/mshv'))
                   else original_exists(path)), \
                patch('azext_ace._lifecycle.Path.stat', autospec=True,
                      side_effect=lambda path, **kw: device if str(path) == str(Path('/dev/mshv'))
                      else original_stat(path, **kw)), \
                patch('azext_ace._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_ace._lifecycle.subprocess.Popen'), \
                patch('azext_ace._lifecycle.identity', return_value={'pid': 123}), \
                patch('azext_ace._lifecycle._wait_runtime'), \
                patch('azext_ace._lifecycle._stop_runtime') as stop, \
                patch('azext_ace._lifecycle.probe_guest') as probe:
            with self.assertRaisesRegex(CLIError, 'Launch failed.*resume rejected'):
                _lifecycle.launch('demo', str(self.image))
            self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                             ['CreateVM', 'ResumeVM', 'Quit'])
            stop.assert_called_once()
            probe.assert_not_called()
        self.assertEqual(_instance.read_record(self.directory / 'instance.json')['status'], 'failed')
