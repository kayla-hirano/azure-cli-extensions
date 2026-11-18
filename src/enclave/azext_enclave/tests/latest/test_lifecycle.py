# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Exercise launch, destroy, and failure recovery without a real L2 boot."""

import os
import tempfile
from dataclasses import replace
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import grpc
from google.protobuf.empty_pb2 import Empty
from knack.util import CLIError

from azext_enclave import _instance, _lifecycle
from azext_enclave._isolation import load_isolation
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
        patcher = patch.object(_instance, '_private_root', return_value=self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        patches = {
            'require_host': None,
            'instance_path': self.directory,
            'instance_lock': nullcontext(),
            'host_lock': nullcontext(47),
            'check_private': None,
            'resolve_runtime': self.executable,
            'new_name': 'demo',
            'archive_path': self.base / 'archives' / 'demo',
            'finalize': {'name': 'demo', 'status': 'destroyed', 'log_disposition': 'archived',
                         'archive_path': str(self.base / 'archives' / 'demo'), 'removal_outcome': 'removed'},
        }
        for name, result in patches.items():
            patcher = patch('azext_enclave._lifecycle.' + name, return_value=result)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('azext_enclave._lifecycle.read_private_record', side_effect=_instance.read_record)
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
        supervisor = {'pid': 123, 'boot': '00000000-0000-0000-0000-000000000001', 'start': '1', 'command': 'a' * 64}
        state = {'schema': 1, 'name': 'demo', 'status': 'running', 'supervisor': supervisor,
                 'expected_logs': []}
        _instance.write_record(self.directory / 'instance.json', state)
        _instance.write_record(self.directory / 'runtime.json', {
            'supervisor': supervisor, 'openvmm': dict(supervisor, pid=456, start='2', command='b' * 64),
        })
        return state

    def test_supervisor_start_preserves_environment_and_records_identity(self) -> None:
        """Keep source discovery and detached process ownership when extracting startup."""
        self.directory.mkdir()
        state = {'status': 'starting', 'expected_logs': []}
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
            'status': 'starting', 'supervisor': {'pid': 123}, 'expected_logs': list(_lifecycle.LOG_NAMES),
        })

    def test_supervisor_early_exit_is_reported(self) -> None:
        """Reap an exited worker without publishing a successful supervisor record."""
        self.directory.mkdir()
        with patch('azext_enclave._lifecycle.subprocess.Popen') as popen, \
                patch('azext_enclave._lifecycle.identity', return_value=None):
            with self.assertRaisesRegex(CLIError, 'Supervisor failed to start'):
                _lifecycle._start_supervisor(self.directory, {'expected_logs': []}, 47)
        popen.return_value.wait.assert_called_once_with(timeout=5)
        self.assertNotIn('supervisor', _instance.read_record(self.directory / 'instance.json'))

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
            result = _lifecycle.launch(str(self.image), 'demo')
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

    def test_launch_uses_manifest_and_preserves_attestation_disclaimer(self) -> None:
        """Apply manifest resources and preserve the evidence-verification disclaimer."""
        configuration = load_isolation()
        configuration = replace(configuration, defaults=replace(
            configuration.defaults, processor_count=2, memory_mib=256))
        with self.mock_launch_runtime() as runtime, \
                patch.object(_lifecycle, 'load_isolation', return_value=configuration), \
                patch.object(_lifecycle.logger, 'warning') as warning:
            result = _lifecycle.launch(str(self.image), 'demo')
        request = self.client.call.call_args_list[0].args[1]
        self.assertEqual(request.config.processor_config.processor_count, 2)
        self.assertEqual(request.config.memory_config.memory_mb, 256)
        self.assertEqual(result['schema'], 1)
        self.assertNotIn('isolation_type', result)
        runtime.probe.assert_called_once_with(self.directory, runtime.probe.call_args.args[1], 'snp')
        warning.assert_called_once_with(
            '%s report received; its signature, measurement and trust chain are NOT verified.',
            configuration.isolation_type)

    def test_invalid_manifest_fails_before_instance_creation(self) -> None:
        """Reject invalid isolation metadata before resolving the runtime or creating instance state."""
        with self.mock_launch_runtime() as runtime, \
                patch.object(_lifecycle, 'load_isolation', side_effect=ValueError('bad manifest')), \
                patch.object(_lifecycle, 'resolve_runtime') as resolve:
            with self.assertRaisesRegex(ValueError, 'bad manifest'):
                _lifecycle.launch(str(self.image), 'demo')
        runtime.popen.assert_not_called()
        resolve.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_missing_launch_implementations_fail_before_host_or_instance_changes(self) -> None:
        """Require both RPC and guest-readiness implementations before performing host or instance operations."""
        configuration = replace(load_isolation(), isolation_type='tdx')
        for rpc_supported, message in (
                (False, 'RPC launch is not implemented'),
                (True, 'Guest readiness is not implemented')):
            with self.subTest(rpc_supported=rpc_supported), \
                    patch.object(_lifecycle, 'load_isolation', return_value=configuration), \
                    patch.object(_lifecycle, 'require_host') as host, \
                    patch.object(_lifecycle, 'resolve_runtime') as resolve, \
                    patch.object(_lifecycle, 'instance_path') as instance:
                rpc_check = (patch.object(_lifecycle, 'validate_launch_configuration')
                             if rpc_supported else nullcontext())
                with rpc_check, self.assertRaisesRegex((ValueError, CLIError), message):
                    _lifecycle.launch(str(self.image), 'demo')
                host.assert_not_called()
                resolve.assert_not_called()
                instance.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_manifest_host_requirements_fail_before_instance_creation(self) -> None:
        """Check every manifest-required host device before starting a supervisor or creating instance state."""
        configuration = replace(load_isolation(), character_devices=('/dev/mshv', '/dev/required'))
        with self.mock_launch_runtime() as runtime, \
                patch.object(_lifecycle, 'load_isolation', return_value=configuration):
            with self.assertRaisesRegex(CLIError, '/dev/required character device'):
                _lifecycle.launch(str(self.image), 'demo')
        runtime.popen.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_destroy_only_matches_immediate_instance_names(self):
        self.prepare_destroy()
        for name in ('missing', '', '.', '..', '../demo', 'demo/child', 'demo\\child',
                     str(self.directory), 'demo\x00'):
            with self.subTest(name=name), patch.object(_lifecycle, '_prepare_destroy') as prepare:
                with self.assertRaisesRegex(CLIError, 'Enclave instance not found'):
                    _lifecycle.destroy(name)
                prepare.assert_not_called()
        _lifecycle.finalize.assert_not_called()

    def test_destroy_rejects_symlink_entry(self):
        self.directory.mkdir()
        with patch.object(Path, 'is_symlink', autospec=True,
                          side_effect=lambda path: path == self.directory), \
                patch.object(_lifecycle, '_prepare_destroy') as prepare:
            with self.assertRaisesRegex(CLIError, 'must not be a symlink'):
                _lifecycle.destroy('demo')
        prepare.assert_not_called()

    def test_destroy_accepts_existing_name_without_launch_format(self):
        self.prepare_destroy()
        name = '_legacy instance'
        self.directory = self.directory.rename(self.base / name)
        state = _instance.read_record(self.directory / 'instance.json')
        state['name'] = name
        _instance.write_record(self.directory / 'instance.json', state)
        with patch.object(_lifecycle, 'alive', return_value=False), \
                patch.object(_lifecycle, '_stop_runtime'):
            _lifecycle.destroy(name)
        self.assertEqual(_lifecycle.finalize.call_args.args[0], self.directory)

    def test_destroy_missing_root_reports_not_found_without_creating_it(self):
        root = self.base / 'missing-root'
        with patch.object(_instance, '_private_root', return_value=root) as private_root:
            with self.assertRaisesRegex(CLIError, 'Enclave instance not found'):
                _lifecycle.destroy('demo')
        private_root.assert_called_once_with(_instance.DEFAULT_STATE_DIR, create=False)
        self.assertFalse(root.exists())

    def test_destroy_teardown_then_quit_and_preserve_image(self):
        """Release the VM before quitting its server while retaining the reusable IGVM."""
        state = self.prepare_destroy()
        state['logs'] = {'com1': 'saved log metadata'}
        _instance.write_record(self.directory / 'instance.json', state)
        self.client.call.return_value = vm.PropertiesVMResponse(state=vm.Running)
        with patch('azext_enclave._lifecycle.alive', return_value=True), \
                patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                patch('azext_enclave._lifecycle.host_lock') as host_guard, \
                patch.object(_lifecycle, '_instance_info', side_effect=AssertionError('Do not rebuild launch metadata')), \
                patch('azext_enclave._lifecycle._stop_runtime') as stop:
            stages = Mock()
            for name, stage in (
                    ('stop', stop), ('acquire', host_guard.return_value.__enter__),
                    ('finalize', _lifecycle.finalize), ('release', host_guard.return_value.__exit__)):
                stages.attach_mock(stage, name)
            result = _lifecycle.destroy('demo')
            self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                             ['PropertiesVM', 'TeardownVM', 'Quit'])
            stop.assert_called_once()
            host_guard.assert_called_once_with()
            self.assertEqual([call[0] for call in stages.mock_calls], ['stop', 'acquire', 'finalize', 'release'])
        self.assertEqual(result['status'], 'destroyed')
        self.assertEqual(result['name'], 'demo')
        self.assertEqual(result['removal_outcome'], 'removed')
        self.assertEqual(result['log_disposition'], 'archived')
        saved = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(saved['status'], 'cleanup-pending')
        self.assertEqual(saved['logs'], state['logs'])
        self.assertTrue(self.image.exists())

    def test_launch_rejects_busy_host_before_starting_processes(self) -> None:
        """A different name must not bypass the single-enclave policy."""
        with self.mock_launch_runtime() as runtime, \
                patch('azext_enclave._lifecycle.host_lock',
                      side_effect=CLIError('An enclave is already starting or running on this L1.')):
            with self.assertRaisesRegex(CLIError, 'already starting or running'):
                _lifecycle.launch(str(self.image), 'another-demo')
            runtime.popen.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_launch_rejects_unfinished_log_staging(self):
        staging = _lifecycle.staging_path(self.base / 'archives' / 'demo')
        staging.mkdir(parents=True)
        with self.mock_launch_runtime() as runtime:
            with self.assertRaisesRegex(CLIError, 'unfinished log staging'):
                _lifecycle.launch(str(self.image), 'demo')
        runtime.popen.assert_not_called()
        self.assertFalse(self.directory.exists())
        self.assertTrue(staging.is_dir())

    def test_destroy_rpc_failure_preserves_state_for_retry(self):
        """Keep failed destruction explicit and retryable instead of claiming cleanup succeeded."""
        self.prepare_destroy()
        self.client.call.side_effect = CLIError('RPC unavailable')
        for no_logs in (False, True):
            with self.subTest(no_logs=no_logs), \
                    patch('azext_enclave._lifecycle.alive', return_value=True), \
                    patch('azext_enclave._lifecycle.Path.is_socket', return_value=True), \
                    patch('azext_enclave._lifecycle._stop_runtime') as stop:
                with self.assertRaisesRegex(CLIError, 'retained for retry'):
                    _lifecycle.destroy('demo', no_logs=no_logs)
                stop.assert_not_called()
        _lifecycle.finalize.assert_not_called()
        _lifecycle.host_lock.assert_not_called()
        saved = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(saved['status'], 'destroy-failed')
        self.assertEqual(saved['log_policy'], 'discard')

    def test_busy_host_lock_blocks_finalization(self):
        self.prepare_destroy()
        for no_logs in (False, True):
            with self.subTest(no_logs=no_logs), \
                    patch.object(_lifecycle, 'alive', return_value=False), \
                    patch.object(_lifecycle, '_stop_runtime'), \
                    patch.object(_lifecycle, 'host_lock', side_effect=CLIError('Host-wide lock is busy')):
                with self.assertRaisesRegex(CLIError, 'finalization incomplete.*Host-wide lock is busy'):
                    _lifecycle.destroy('demo', no_logs=no_logs)
        _lifecycle.finalize.assert_not_called()
        self.assertTrue((self.directory / 'instance.json').exists())

    def quit_error(self, status: grpc.StatusCode = grpc.StatusCode.UNAVAILABLE) -> CLIError:
        class TransportError(grpc.RpcError):
            def code(self) -> grpc.StatusCode:
                return status

        error = CLIError('OpenVMM Quit failed ({}): Stream removed (Socket closed)'.format(status.name))
        error.__cause__ = TransportError()
        return error

    def test_destroy_quit_disconnect_succeeds_only_after_owned_server_exits(self) -> None:
        """Treat a Quit disconnect as success only after confirming the owned server has exited."""
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
                    self.assertEqual(
                        _instance.read_record(self.directory / 'instance.json')['status'], 'cleanup-pending')
                    self.assertEqual(stop.call_args.args[0], self.directory)
                    self.assertEqual(sleep.call_count, len(checks) - 3)
                self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                                 ['PropertiesVM', 'TeardownVM', 'Quit'])

    def test_destroy_quit_disconnect_with_live_server_remains_retryable(self) -> None:
        """Retain a retryable destruction failure when Quit disconnects but the owned server remains alive."""
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
        """Preserve non-disconnect Quit failures instead of claiming successful destruction."""
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
        """Preserve management disconnects during state queries or teardown rather than treating them as Quit."""
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
        """Persist cleanup errors and interruptions so a later destroy can reconcile the retained instance."""
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
        """Retain an interrupted graceful shutdown for retry without forcing subsequent process cleanup."""
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

    def test_destroy_reconciles_dead_processes_without_rpc(self):
        """Finalize stopped instances without requiring a live management endpoint."""
        self.prepare_destroy()
        with patch('azext_enclave._lifecycle.alive', return_value=False), \
                patch('azext_enclave._lifecycle._stop_runtime'):
            result = _lifecycle.destroy('demo')
            self.assertEqual(result['status'], 'destroyed')
            self.assertEqual(result['log_disposition'], 'archived')
        _lifecycle.finalize.assert_called_once()
        self.client.call.assert_not_called()

    def test_destroy_refuses_mismatched_ownership(self):
        """Refuse RPC access when persisted supervisor identities disagree."""
        state = self.prepare_destroy()
        state['supervisor']['pid'] = 999
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
                _lifecycle.launch(str(self.image), 'demo')
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
                _lifecycle.launch(str(self.image), 'demo')
            self.assertEqual([call.args[0] for call in self.client.call.call_args_list],
                             ['CreateVM', 'ResumeVM', 'Quit'])
            stop.assert_called_once()
            runtime.probe.assert_not_called()
        self.assertEqual(_instance.read_record(self.directory / 'instance.json')['status'], 'failed')

    def test_unsupported_or_mismatched_records_are_not_adopted(self):
        state = self.prepare_destroy()
        for changes in ({'schema': 2}, {'name': 'other'}):
            with self.subTest(changes=changes):
                _instance.write_record(self.directory / 'instance.json', dict(state, **changes))
                with self.assertRaisesRegex(CLIError, 'Unsupported or mismatched.*Manual cleanup is required'):
                    _lifecycle.destroy('demo')
        self.client.call.assert_not_called()
        _lifecycle.finalize.assert_not_called()

    def test_corrupt_runtime_is_not_treated_as_shutdown(self):
        self.prepare_destroy()
        _instance.write_record(self.directory / 'runtime.json', {})
        with self.assertRaisesRegex(CLIError, 'shutdown cannot be confirmed'):
            _lifecycle.destroy('demo', no_logs=True)
        self.client.call.assert_not_called()
        _lifecycle.finalize.assert_not_called()

    def test_destroy_recovers_identity_published_before_launcher_record(self):
        """Recover a missing launcher identity from a valid runtime record."""
        state = self.prepare_destroy()
        state.pop('supervisor')
        _instance.write_record(self.directory / 'instance.json', state)
        with patch.object(_lifecycle, 'alive', return_value=False), \
                patch.object(_lifecycle, '_stop_runtime'), patch.object(_lifecycle.logger, 'warning') as warning:
            result = _lifecycle.destroy('demo')
        warning.assert_called_once()
        self.assertEqual(result['removal_outcome'], 'removed')
        self.assertEqual(_instance.read_record(self.directory / 'instance.json')['supervisor']['pid'], 123)

    def test_destroy_does_not_recover_invalid_runtime_identity(self):
        """Leave state untouched if either recovered process identity is invalid."""
        state = self.prepare_destroy()
        state.pop('supervisor')
        _instance.write_record(self.directory / 'instance.json', state)
        runtime = _instance.read_record(self.directory / 'runtime.json')
        for field in ('supervisor', 'openvmm'):
            with self.subTest(field=field):
                _instance.write_record(self.directory / 'runtime.json', dict(runtime, **{field: None}))
                with patch.object(_lifecycle, '_shutdown_instance') as shutdown:
                    with self.assertRaisesRegex(CLIError, 'Invalid process identity'):
                        _lifecycle.destroy('demo')
                    shutdown.assert_not_called()
                self.assertEqual(_instance.read_record(self.directory / 'instance.json'), state)

    def test_new_launches_generate_distinct_persistent_full_names(self):
        with self.mock_launch_runtime(), \
                patch.object(_lifecycle, 'new_name', side_effect=_instance.new_name), \
                patch.object(_lifecycle, 'instance_path', side_effect=lambda name: self.base / name):
            first = _lifecycle.launch(str(self.image))
            second = _lifecycle.launch(str(self.image))
        self.assertNotEqual(first['name'], second['name'])
        for state in (first, second):
            self.assertTrue(state['name'].startswith('enclave-'))
            saved = _instance.read_record(self.base / state['name'] / 'instance.json')
            self.assertEqual(saved['name'], state['name'])

    def test_launch_failure_reports_name_even_when_rollback_persistence_fails(self):
        with self.mock_launch_runtime() as runtime, \
                patch.object(_lifecycle, '_rollback_launch', side_effect=OSError('Cannot save rollback')):
            runtime.wait_runtime.side_effect = CLIError('Startup failed')
            with self.assertRaisesRegex(CLIError, 'Launch failed for demo: Startup failed.*destroy --name demo'):
                _lifecycle.launch(str(self.image))
        self.assertTrue((self.directory / 'instance.json').exists())
