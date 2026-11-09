# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Exercise launch, destroy, and failure recovery without a real L2 boot."""

import os
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import grpc
from google.protobuf.empty_pb2 import Empty
from knack.util import CLIError

from azext_enclave import _instance, _lifecycle
from azext_enclave.rpc import vmservice_pb2 as vm
from ._helpers import LinuxFilesystemTest


class LifecycleTest(LinuxFilesystemTest):
    """Test launch/destroy state transitions and rollback using isolated records and mocked RPC."""

    def setUp(self):
        """Create temporary artifacts and replace host/process/RPC boundaries with controlled doubles."""
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.directory = self.base / 'demo'
        self.image = self.base / 'enclave.igvm'
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
            patcher = patch('azext_enclave._lifecycle.' + name, return_value=result)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('azext_enclave._lifecycle.VmClient')
        self.client = patcher.start().return_value.__enter__.return_value
        self.addCleanup(patcher.stop)

    @contextmanager
    def mock_launch_runtime(self):
        """Supply the device and process boundaries; callers inject failures on the returned mocks."""
        original_exists = Path.exists
        original_stat = Path.stat
        device = Path('/dev/mshv')
        with patch('azext_enclave._lifecycle.Path.exists', autospec=True,
                   side_effect=lambda path: path == device or original_exists(path)), \
                patch('azext_enclave._lifecycle.Path.stat', autospec=True,
                      side_effect=lambda path, **kwargs: SimpleNamespace(st_mode=0o020600)
                      if path == device else original_stat(path, **kwargs)), \
                patch('azext_enclave._lifecycle.subprocess.Popen') as popen, \
                patch('azext_enclave._lifecycle.identity', return_value={'pid': 123}), \
                patch('azext_enclave._lifecycle._wait_runtime') as wait_runtime, \
                patch('azext_enclave._lifecycle.probe_guest') as probe:
            yield SimpleNamespace(popen=popen, wait_runtime=wait_runtime, probe=probe)

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
                patch('azext_enclave._lifecycle.subprocess.Popen') as popen, \
                patch('azext_enclave._lifecycle.identity', return_value={'pid': 123}):
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
        with patch('azext_enclave._lifecycle.subprocess.Popen') as popen, \
                patch('azext_enclave._lifecycle.identity', return_value=None):
            with self.assertRaisesRegex(CLIError, 'Supervisor failed to start'):
                _lifecycle._start_supervisor(self.directory, {}, 47)
        popen.return_value.wait.assert_called_once_with(timeout=5)
        self.assertFalse((self.directory / 'instance.json').exists())

    def test_rollback_retains_original_error_when_cleanup_fails(self) -> None:
        """Recover after failed Quit and record cleanup errors without hiding the launch error."""
        self.directory.mkdir()
        state = {'status': 'paused', 'supervisor': {'pid': 123}}
        self.client.call.side_effect = CLIError('Quit rejected')
        with patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_enclave._lifecycle._stop_runtime', side_effect=CLIError('still alive')) as stop, \
                patch('azext_enclave._lifecycle.logger.warning') as warning, \
                patch('azext_enclave._lifecycle.logger.error') as log_error:
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
        with self.mock_launch_runtime() as runtime:
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
            runtime.probe.assert_called_once()
            self.assertTrue(runtime.popen.call_args.kwargs['start_new_session'])
            self.assertEqual(runtime.popen.call_args.kwargs['pass_fds'], (47,))
        self.assertEqual(_instance.read_record(self.directory / 'instance.json'), result)

    def test_destroy_teardown_then_quit_and_preserve_image(self):
        """Release the VM before quitting its server while retaining the reusable IGVM."""
        self.prepare_destroy()
        self.client.call.return_value = vm.PropertiesVMResponse(state=vm.Running)
        with patch('azext_enclave._lifecycle.alive', return_value=True), \
                patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_enclave._lifecycle.host_lock') as host_guard, \
                patch('azext_enclave._lifecycle._stop_runtime') as stop:
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
        """A different name must not bypass the single-enclave policy."""
        with self.mock_launch_runtime() as runtime, \
                patch('azext_enclave._lifecycle.host_lock',
                      side_effect=CLIError('An enclave is already starting or running on this L1.')):
            with self.assertRaisesRegex(CLIError, 'already starting or running'):
                _lifecycle.launch('another-demo', str(self.image))
            runtime.popen.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_destroy_rpc_failure_preserves_state_for_retry(self):
        """Keep failed destruction explicit and retryable instead of claiming cleanup succeeded."""
        self.prepare_destroy()
        self.client.call.side_effect = CLIError('RPC unavailable')
        with patch('azext_enclave._lifecycle.alive', return_value=True), \
                patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_enclave._lifecycle._stop_runtime') as stop:
            with self.assertRaisesRegex(CLIError, 'retained for retry'):
                _lifecycle.destroy('demo')
            stop.assert_not_called()
        self.assertEqual(_instance.read_record(self.directory / 'instance.json')['status'], 'destroy-failed')

    def quit_error(self, status: grpc.StatusCode = grpc.StatusCode.UNAVAILABLE) -> CLIError:
        class TransportError(grpc.RpcError):
            def code(self) -> grpc.StatusCode:
                return status

        error = CLIError('OpenVMM Quit failed ({}): Stream removed (Socket closed)'.format(status.name))
        error.__cause__ = TransportError()
        return error

    def test_destroy_quit_disconnect_succeeds_only_after_owned_server_exits(self) -> None:
        state = self.prepare_destroy()
        state.update(status='destroy-failed', error='previous failure', cleanup_error='previous cleanup')
        for checks in ([True, True, False], [True, True, True, False]):
            with self.subTest(checks=checks):
                _instance.write_record(self.directory / 'instance.json', state)
                self.client.call.reset_mock()
                self.client.call.side_effect = [
                    vm.PropertiesVMResponse(state=vm.Running), Empty(), self.quit_error(),
                ]
                with patch('azext_enclave._lifecycle.alive', side_effect=checks), \
                        patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                        patch('azext_enclave._lifecycle.time.monotonic', return_value=0), \
                        patch('azext_enclave._lifecycle.time.sleep') as sleep, \
                        patch('azext_enclave._lifecycle._stop_runtime') as stop:
                    result = _lifecycle.destroy('demo')
                    self.assertEqual(result['status'], 'destroyed')
                    self.assertNotIn('error', result)
                    self.assertNotIn('cleanup_error', result)
                    self.assertEqual(result, _instance.read_record(self.directory / 'instance.json'))
                    stop.assert_called_once_with(self.directory, result)
                    self.assertEqual(sleep.call_count, len(checks) - 3)
                self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                                 ['PropertiesVM', 'TeardownVM', 'Quit'])

    def test_destroy_quit_disconnect_with_live_server_remains_retryable(self) -> None:
        self.prepare_destroy()
        self.client.call.side_effect = [
            vm.PropertiesVMResponse(state=vm.Running), Empty(), self.quit_error(),
        ]
        with patch('azext_enclave._lifecycle.alive', return_value=True), \
                patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_enclave._lifecycle.time.monotonic', side_effect=[0, 0, 1]), \
                patch('azext_enclave._lifecycle.time.sleep') as sleep, \
                patch('azext_enclave._lifecycle._stop_runtime') as stop:
            with self.assertRaisesRegex(CLIError, 'retained for retry.*UNAVAILABLE'):
                _lifecycle.destroy('demo', timeout=1)
            stop.assert_not_called()
            sleep.assert_called_once_with(0.1)
        state = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(state['status'], 'destroy-failed')
        self.assertIn('Stream removed (Socket closed)', state['error'])

    def test_destroy_other_quit_errors_are_not_suppressed(self) -> None:
        self.prepare_destroy()
        for error in (self.quit_error(grpc.StatusCode.INTERNAL),
                      self.quit_error(grpc.StatusCode.DEADLINE_EXCEEDED), CLIError('Quit rejected')):
            with self.subTest(error=str(error)):
                self.client.call.side_effect = [
                    vm.PropertiesVMResponse(state=vm.Uninitialized), error,
                ]
                with patch('azext_enclave._lifecycle.alive', side_effect=[True, True]) as alive, \
                        patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                        patch('azext_enclave._lifecycle._stop_runtime') as stop:
                    with self.assertRaisesRegex(CLIError, 'retained for retry'):
                        _lifecycle.destroy('demo')
                    self.assertEqual(alive.call_count, 2)
                    stop.assert_not_called()
                self.assertEqual(_instance.read_record(
                    self.directory / 'instance.json')['status'], 'destroy-failed')

    def test_destroy_disconnect_before_quit_is_not_suppressed(self) -> None:
        self.prepare_destroy()
        for responses in ([self.quit_error()],
                          [vm.PropertiesVMResponse(state=vm.Running), self.quit_error()]):
            with self.subTest(failed_call=len(responses)):
                self.client.call.side_effect = responses
                with patch('azext_enclave._lifecycle.alive', side_effect=[True, True]) as alive, \
                        patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                        patch('azext_enclave._lifecycle._stop_runtime') as stop:
                    with self.assertRaisesRegex(CLIError, 'retained for retry'):
                        _lifecycle.destroy('demo')
                    self.assertEqual(alive.call_count, 2)
                    stop.assert_not_called()
                self.assertEqual(_instance.read_record(
                    self.directory / 'instance.json')['status'], 'destroy-failed')

    def test_destroy_missing_socket_does_not_claim_success(self):
        """Distinguish an unreachable management endpoint from a stopped owned server."""
        self.prepare_destroy()
        with patch('azext_enclave._lifecycle.alive', return_value=True):
            with self.assertRaisesRegex(CLIError, 'destruction is unconfirmed'):
                _lifecycle.destroy('demo')
        self.assertEqual(_instance.read_record(self.directory / 'instance.json')['status'], 'destroy-failed')

    def test_destroy_cleanup_failure_is_persisted_for_retry(self) -> None:
        self.prepare_destroy()
        for error in (CLIError('still alive'), OSError('socket removal denied'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with patch('azext_enclave._lifecycle.alive', return_value=False), \
                        patch('azext_enclave._lifecycle._stop_runtime', side_effect=error):
                    with self.assertRaisesRegex(CLIError, 'cleanup incomplete.*retained for retry'):
                        _lifecycle.destroy('demo')
                state = _instance.read_record(self.directory / 'instance.json')
                self.assertEqual(state['status'], 'destroy-failed')
                self.assertEqual(state['cleanup_error'], str(error) or 'Cleanup interrupted.')
                self.assertTrue(self.image.exists())
        with patch('azext_enclave._lifecycle.alive', return_value=False), \
                patch('azext_enclave._lifecycle._stop_runtime'):
            result = _lifecycle.destroy('demo')
        self.assertEqual(result['status'], 'destroyed')
        self.assertNotIn('cleanup_error', result)

    def test_interrupted_shutdown_does_not_force_process_cleanup(self) -> None:
        self.prepare_destroy()
        with patch('azext_enclave._lifecycle.alive', return_value=True), \
                patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_enclave._lifecycle._shutdown_runtime', side_effect=KeyboardInterrupt()), \
                patch('azext_enclave._lifecycle._stop_runtime') as stop:
            with self.assertRaisesRegex(CLIError, 'retained for retry.*Shutdown interrupted'):
                _lifecycle.destroy('demo')
        stop.assert_not_called()
        state = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(state['status'], 'destroy-failed')
        self.assertEqual(state['error'], 'Shutdown interrupted.')

    def test_repeated_destroy_reconciles_dead_processes(self):
        """Allow repeated cleanup of a recorded instance whose processes have already exited."""
        self.prepare_destroy()
        with patch('azext_enclave._lifecycle.alive', return_value=False), \
                patch('azext_enclave._lifecycle._stop_runtime'):
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
        with patch('azext_enclave._lifecycle.alive', return_value=True):
            with self.assertRaisesRegex(CLIError, 'ownership is inconsistent'):
                _lifecycle.destroy('demo')
        self.client.call.assert_not_called()

    def test_destroy_uninitialized_server_only_quits(self):
        """Avoid tearing down a nonexistent VM while still exiting its owned server."""
        self.prepare_destroy()
        self.client.call.return_value = vm.PropertiesVMResponse(state=vm.Uninitialized)
        with patch('azext_enclave._lifecycle.alive', return_value=True), \
                patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_enclave._lifecycle._stop_runtime'):
            _lifecycle.destroy('demo')
        self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                         ['PropertiesVM', 'Quit'])

    def test_destroy_reconciles_server_after_supervisor_crash(self):
        """Use RPC to stop an owned server even if its matching supervisor has died."""
        self.prepare_destroy()
        self.client.call.return_value = vm.PropertiesVMResponse(state=vm.Running)
        with patch('azext_enclave._lifecycle.alive', side_effect=lambda record: record['pid'] == 456), \
                patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_enclave._lifecycle._stop_runtime'), \
                patch('azext_enclave._lifecycle.logger.warning') as warning:
            self.assertEqual(_lifecycle.destroy('demo')['status'], 'destroyed')
            warning.assert_called_once()

    def test_launch_startup_failure_rolls_back_and_retains_evidence(self):
        """Recover from startup failure and retain failed state plus input identities for diagnosis."""
        with self.mock_launch_runtime() as runtime, \
                patch('azext_enclave._lifecycle._stop_runtime') as stop:
            runtime.wait_runtime.side_effect = CLIError('startup timeout')
            with self.assertRaisesRegex(CLIError, 'Launch failed.*startup timeout'):
                _lifecycle.launch('demo', str(self.image))
            stop.assert_called_once()
        state = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(len(state['igvm_sha256']), 64)
        self.assertFalse(state['attestation_verified'])

    def test_resume_failure_quits_created_vm_and_recovers_owned_processes(self):
        """Quit and clean up a created VM when resume fails, without probing an unstarted guest."""
        def rpc_call(method, *_args):
            """Simulate successful creation/quit but an explicit ResumeVM failure."""
            if method == 'ResumeVM':
                raise CLIError('resume rejected')
            return Empty()

        self.client.call.side_effect = rpc_call
        with self.mock_launch_runtime() as runtime, \
                patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_enclave._lifecycle._stop_runtime') as stop:
            with self.assertRaisesRegex(CLIError, 'Launch failed.*resume rejected'):
                _lifecycle.launch('demo', str(self.image))
            self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                             ['CreateVM', 'ResumeVM', 'Quit'])
            stop.assert_called_once()
            runtime.probe.assert_not_called()
        self.assertEqual(_instance.read_record(self.directory / 'instance.json')['status'], 'failed')
