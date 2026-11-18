# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Exercise archive-before-removal, explicit discard, and manual handling of ambiguous cleanup."""

import errno
import os
import stat
import sys
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from knack.util import CLIError

from azext_enclave import _cleanup, _instance, _lifecycle
from ._helpers import LinuxFilesystemTest


class CleanupTest(LinuxFilesystemTest):
    """Use real temporary files; shim only unavailable POSIX boundaries on Windows."""

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.name = _instance.new_name('demo')
        self.directory = self.base / self.name
        self.directory.mkdir(mode=0o700)
        self.archive_root = self.base / 'archives'
        self.archive = self.archive_root / self.name
        supervisor = {'pid': 123, 'boot': '00000000-0000-0000-0000-000000000001', 'start': '1', 'command': 'a' * 64}
        self.state = {'schema': 1, 'name': self.name, 'status': 'failed', 'supervisor': supervisor,
                      'expected_logs': list(_cleanup.LOG_NAMES), 'log_policy': 'retain'}
        runtime = {'supervisor': supervisor, 'openvmm': dict(supervisor, pid=456)}
        for name in _cleanup.LOG_NAMES:
            self.write(name, (name + '\n').encode())
        for name in _cleanup.RECORD_NAMES:
            value = self.state if name == 'instance.json' else runtime if name == 'runtime.json' else {}
            _instance.write_record(self.directory / name, value)
        self.external = self.base / 'external.igvm'
        self.external.write_bytes(b'preserve this image')
        patcher = patch.object(_cleanup, 'ARCHIVE_ROOT', str(self.archive_root))
        patcher.start()
        self.addCleanup(patcher.stop)
        if sys.platform != 'linux':
            for name in ('_instance', '_cleanup'):
                patcher = patch('azext_enclave.' + name + '.check_private', side_effect=self.check_directory)
                patcher.start()
                self.addCleanup(patcher.stop)
            for name, replacement in (
                    ('_regular_file', self.regular_file), ('_publish', os.rename),
                    ('_remove_instance', self.portable_remove),
                    ('_validate_file_info', lambda _path, _info: None)):
                patcher = patch.object(_cleanup, name, side_effect=replacement)
                patcher.start()
                self.addCleanup(patcher.stop)

    @staticmethod
    def check_directory(path):
        if path.is_symlink() or not path.is_dir():
            raise CLIError('Unsafe directory: ' + str(path))

    @staticmethod
    def regular_file(path, directory_fd=None):
        del directory_fd
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CLIError('Unsafe regular file: ' + str(path))
        return info

    @staticmethod
    def portable_remove(directory, expected_identity):
        """Windows lacks dirfd unlink; Linux runs the production descriptor-relative implementation."""
        if _cleanup._directory_identity(directory) != expected_identity:
            raise CLIError('Instance directory was replaced')
        entries = list(directory.iterdir())
        for entry in entries:
            if entry.name not in _cleanup.INSTANCE_FILES:
                raise CLIError('Unexpected instance entry')
            _cleanup._regular_file(entry)
        for entry in sorted(entries, key=lambda path: path.name == 'instance.json'):
            entry.unlink()
        directory.rmdir()

    def write(self, name, data):
        path = self.directory / name
        path.write_bytes(data)
        path.chmod(0o600)

    def finalize(self, no_logs=False):
        if no_logs:
            self.state['log_policy'] = 'discard'
        return _cleanup.finalize(self.directory, self.state)

    @contextmanager
    def mock_destroy(self):
        with patch.object(_lifecycle, 'require_host'), \
                patch.object(_instance, 'DEFAULT_STATE_DIR', str(self.base)), \
                patch.object(_lifecycle, 'check_private', side_effect=_instance.check_private), \
                patch.object(_lifecycle, 'instance_lock', return_value=nullcontext()), \
                patch.object(_lifecycle, 'host_lock', return_value=nullcontext(47)), \
                patch.object(_lifecycle, 'alive', return_value=False), \
                patch.object(_lifecycle, 'stop_owned'), \
                patch.object(_lifecycle, 'VmClient') as client:
            yield client

    def test_archive_only_logs_and_remove_state_and_evidence(self):
        self.archive_root.mkdir(mode=0o700)
        previous = self.archive_root / _instance.new_name('demo')
        previous.mkdir(mode=0o700)
        (previous / 'com1.log').write_bytes(b'historical logs')
        self.write('instance.tmp', b'interrupted record update')
        result = self.finalize()
        self.assertEqual(result, {
            'name': self.name, 'status': 'destroyed', 'log_disposition': 'archived',
            'archive_path': str(self.archive), 'removal_outcome': 'removed',
        })
        self.assertEqual({entry.name for entry in self.archive.iterdir()}, set(_cleanup.LOG_NAMES))
        for name in _cleanup.LOG_NAMES:
            self.assertEqual((self.archive / name).read_bytes(), (name + '\n').encode())
        self.assertFalse(self.directory.exists())
        self.assertFalse(list(self.base.glob('*.cleanup.json')))
        self.assertEqual((previous / 'com1.log').read_bytes(), b'historical logs')
        self.assertEqual(self.external.read_bytes(), b'preserve this image')
        self.assertFalse(list(self.archive_root.glob('*.staging')))
        if sys.platform == 'linux':
            self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o700)
            for path in self.archive.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(path.stat().st_uid, os.geteuid())

    def test_discard_needs_no_archive_access_and_removes_evidence(self):
        with patch.object(_cleanup, 'archive_path', side_effect=AssertionError('Archive storage must not be accessed')):
            result = self.finalize(no_logs=True)
        self.assertEqual(result['log_disposition'], 'discarded')
        self.assertIsNone(result['archive_path'])
        self.assertFalse(self.archive_root.exists())
        self.assertFalse(self.directory.exists())
        self.assertFalse(list(self.base.glob('*.cleanup.json')))
        self.assertTrue(self.external.exists())

    def test_missing_expected_log_retains_state_until_explicit_discard(self):
        (self.directory / 'com1.log').unlink()
        with self.assertRaisesRegex(CLIError, 'Expected log is missing.*--no-logs'):
            self.finalize()
        self.assertTrue(self.directory.exists())
        self.assertFalse(self.archive_root.exists())
        self.assertEqual(self.finalize(no_logs=True)['log_disposition'], 'discarded')

    def test_failed_launch_legitimate_absence_and_empty_logs(self):
        self.state['expected_logs'] = ['supervisor.log']
        (self.directory / 'com1.log').unlink()
        (self.directory / 'openvmm.log').unlink()
        self.write('supervisor.log', b'')
        with patch.object(_cleanup.logger, 'warning') as warning:
            self.finalize()
        self.assertEqual(warning.call_count, 2)
        self.assertEqual(list(self.archive.iterdir()), [self.archive / 'supervisor.log'])
        self.assertEqual((self.archive / 'supervisor.log').stat().st_size, 0)

    def test_publication_failure_preserves_originals_and_requires_manual_cleanup(self):
        with patch.object(_cleanup, '_publish', side_effect=OSError(errno.ENOSPC, 'disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.finalize()
        saved = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(saved['status'], 'cleanup-failed')
        self.assertEqual(saved['cleanup_phase'], 'archiving')
        self.assertIn('disk full', saved['cleanup_error'])
        for name in _cleanup.LOG_NAMES:
            self.assertTrue((self.directory / name).exists())
        self.assertFalse(self.archive.exists())
        staging = _cleanup.staging_path(self.archive)
        self.assertTrue(staging.is_dir())
        for no_logs in (False, True):
            with self.subTest(no_logs=no_logs), self.mock_destroy() as client:
                with self.assertRaisesRegex(CLIError, 'ambiguous.*Manual cleanup is required') as raised:
                    _lifecycle.destroy(self.name, no_logs=no_logs)
                client.assert_not_called()
                for detail in ('archiving', 'disk full', str(self.directory), str(self.archive), str(staging)):
                    self.assertIn(detail, str(raised.exception))
        self.assertEqual(_instance.read_record(self.directory / 'instance.json'), saved)
        self.assertTrue(staging.is_dir())

    def test_crash_after_archive_rename_is_not_automatically_adopted(self):
        write_record = _cleanup.write_record

        def interrupt_after_publication(path, state):
            if state.get('cleanup_phase') == 'removing':
                raise SystemExit('Interrupted after atomic archive rename')
            write_record(path, state)

        with patch.object(_cleanup, 'write_record', side_effect=interrupt_after_publication):
            with self.assertRaisesRegex(SystemExit, 'Interrupted'):
                self.finalize()
        self.assertEqual(_instance.read_record(self.directory / 'instance.json')['cleanup_phase'], 'archiving')
        self.assertTrue(self.archive.is_dir())
        original_inode = self.archive.stat().st_ino
        with self.mock_destroy():
            with self.assertRaisesRegex(CLIError, 'Manual cleanup is required'):
                _lifecycle.destroy(self.name)
        self.assertEqual(self.archive.stat().st_ino, original_inode)
        self.assertTrue((self.directory / 'com1.log').exists())

    def test_archive_directory_sync_failure_preserves_originals_and_reports_ambiguity(self):
        sync_directory = _cleanup.sync_directory

        def fail_archive_sync(path):
            if path == self.archive_root:
                raise OSError('Archive publication could not be flushed')
            sync_directory(path)

        with patch.object(_cleanup, 'sync_directory', side_effect=fail_archive_sync), \
                patch.object(_cleanup, '_remove_instance') as remove, self.mock_destroy():
            with self.assertRaisesRegex(
                    CLIError, 'could not be flushed.*ambiguous.*Manual cleanup is required') as raised:
                _lifecycle.destroy(self.name)
        self.assertEqual(str(raised.exception).count('Archive publication could not be flushed'), 1)
        self.assertNotIn('Previous cleanup error:', str(raised.exception))
        remove.assert_not_called()
        self.assertTrue(self.archive.is_dir())
        for name in _cleanup.LOG_NAMES:
            self.assertTrue((self.directory / name).exists())
        saved = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(saved['cleanup_phase'], 'archiving')
        self.assertNotEqual(saved.get('log_disposition'), 'archived')

    def test_partial_removal_keeps_state_and_archive_and_requires_manual_cleanup(self):
        def fail_removal(directory, _identity):
            (directory / 'com1.log').unlink()
            raise PermissionError('Removal interrupted')

        with patch.object(_cleanup, '_remove_instance', side_effect=fail_removal):
            with self.assertRaisesRegex(PermissionError, 'Removal interrupted'):
                self.finalize()
        saved = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(saved['cleanup_phase'], 'removing')
        self.assertEqual(saved['status'], 'cleanup-failed')
        self.assertEqual(saved['cleanup_error'], 'Removal interrupted')
        self.assertEqual(saved['log_disposition'], 'archived')
        self.assertEqual(saved['archive_path'], str(self.archive))
        self.assertEqual((self.archive / 'com1.log').read_bytes(), b'com1.log\n')
        with self.mock_destroy():
            with self.assertRaisesRegex(CLIError, 'log disposition: archived.*Manual cleanup is required'):
                _lifecycle.destroy(self.name, no_logs=True)
        self.assertEqual((self.archive / 'com1.log').read_bytes(), b'com1.log\n')
        self.assertTrue((self.directory / 'instance.json').exists())

    def test_interrupted_discard_preserves_policy_and_requires_manual_cleanup(self):
        with patch.object(_cleanup, '_remove_instance', side_effect=PermissionError('Cannot remove yet')):
            with self.assertRaises(PermissionError):
                self.finalize(no_logs=True)
        saved = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(saved['log_policy'], 'discard')
        self.assertEqual(saved['log_disposition'], 'discard-requested')
        with self.mock_destroy(), \
                patch.object(_cleanup, 'archive_path', side_effect=AssertionError('Do not reverse explicit discard')):
            with self.assertRaisesRegex(CLIError, 'Manual cleanup is required'):
                _lifecycle.destroy(self.name)
        self.assertFalse(self.archive_root.exists())

    def test_discard_policy_from_failed_shutdown_does_not_need_flag_again(self):
        self.state['log_policy'] = 'discard'
        with patch.object(_cleanup, 'archive_path', side_effect=AssertionError('Respect persisted discard')):
            self.assertEqual(self.finalize()['log_disposition'], 'discarded')

    def test_finalization_rejects_missing_or_invalid_persisted_policy(self):
        for policy in (None, 'invalid'):
            with self.subTest(policy=policy):
                if policy is None:
                    self.state.pop('log_policy')
                else:
                    self.state['log_policy'] = policy
                with patch.object(_cleanup, '_archive') as archive, \
                        patch.object(_cleanup, '_remove_instance') as remove:
                    with self.assertRaisesRegex(CLIError, 'Invalid persisted log policy'):
                        _cleanup.finalize(self.directory, self.state)
                archive.assert_not_called()
                remove.assert_not_called()
                self.assertTrue((self.directory / 'com1.log').exists())

    def test_destroy_no_logs_resolves_policy_before_finalization(self):
        with self.mock_destroy(), \
                patch.object(_cleanup, 'archive_path', side_effect=AssertionError('Archive storage must not be accessed')):
            result = _lifecycle.destroy(self.name, no_logs=True)
        self.assertEqual(result['log_disposition'], 'discarded')
        self.assertFalse(self.archive_root.exists())
        self.assertFalse(self.directory.exists())

    def test_busy_host_lock_preserves_logs_and_state(self):
        for no_logs in (False, True):
            with self.subTest(no_logs=no_logs), self.mock_destroy(), \
                    patch.object(_lifecycle, 'host_lock', side_effect=CLIError('Host lock is busy')):
                with self.assertRaisesRegex(CLIError, 'Host lock is busy'):
                    _lifecycle.destroy(self.name, no_logs=no_logs)
                self.assertFalse(self.archive_root.exists())
                for name in _cleanup.LOG_NAMES:
                    self.assertEqual((self.directory / name).read_bytes(), (name + '\n').encode())
                saved = _instance.read_record(self.directory / 'instance.json')
                self.assertNotIn('cleanup_phase', saved)

    def test_failure_after_state_deletion_does_not_recreate_record(self):
        def fail_rmdir(directory, _identity):
            for entry in sorted(directory.iterdir(), key=lambda path: path.name == 'instance.json'):
                entry.unlink()
            raise PermissionError('Directory removal interrupted')

        with patch.object(_cleanup, '_remove_instance', side_effect=fail_rmdir), \
                patch.object(_cleanup.logger, 'error') as log_error:
            with self.assertRaisesRegex(PermissionError, 'Directory removal interrupted'):
                self.finalize()
        log_error.assert_called_once()
        self.assertEqual(list(self.directory.iterdir()), [])
        with self.mock_destroy():
            with self.assertRaisesRegex(CLIError, 'ambiguous.*Manual cleanup is required'):
                _lifecycle.destroy(self.name)
        self.assertTrue(self.archive.is_dir())

    def test_archive_collision_is_never_adopted_even_if_empty(self):
        self.archive.mkdir(mode=0o700, parents=True)
        with self.assertRaisesRegex(CLIError, 'collision'):
            self.finalize()
        self.assertEqual(list(self.archive.iterdir()), [])
        self.assertTrue((self.directory / 'response.json').exists())

    def test_replaced_instance_is_not_removed_or_updated(self):
        original = self.base / 'original'
        remove_instance = _cleanup._remove_instance

        def replace_before_removal(directory, expected_identity):
            directory.rename(original)
            directory.mkdir(mode=0o700)
            self.write('com1.log', b'replacement must survive')
            remove_instance(directory, expected_identity)

        with patch.object(_cleanup, '_remove_instance', side_effect=replace_before_removal), \
                patch.object(_cleanup.logger, 'error') as log_error:
            with self.assertRaisesRegex(CLIError, 'replaced'):
                self.finalize()
        log_error.assert_called_once()
        self.assertEqual((self.directory / 'com1.log').read_bytes(), b'replacement must survive')
        self.assertFalse((self.directory / 'instance.json').exists())
        self.assertTrue((original / 'response.json').exists())

    def test_archive_integrity_failure_does_not_remove_originals(self):
        copy_log = _cleanup._copy_log

        def corrupt_copy(source, target, expected):
            copy_log(source, target, expected)
            if target.name == 'com1.log':
                target.write_bytes(b'corrupted')

        with patch.object(_cleanup, '_copy_log', side_effect=corrupt_copy):
            with self.assertRaisesRegex(CLIError, 'integrity validation'):
                self.finalize()
        self.assertEqual((self.directory / 'com1.log').read_bytes(), b'com1.log\n')
        self.assertFalse(self.archive.exists())

    def test_unexpected_files_block_removal_without_deleting_them(self):
        self.write('unexpected.txt', b'preserve')
        with self.assertRaisesRegex(CLIError, 'Unexpected instance entry'):
            self.finalize(no_logs=True)
        self.assertTrue((self.directory / 'response.json').exists())
        self.assertTrue((self.directory / 'unexpected.txt').exists())

    def test_destroy_archives_removes_and_then_reports_not_found_without_rpc(self):
        with self.mock_destroy() as client:
            result = _lifecycle.destroy(self.name)
            with self.assertRaisesRegex(CLIError, 'Enclave instance not found'):
                _lifecycle.destroy(self.name, no_logs=True)
        client.assert_not_called()
        self.assertEqual(result['removal_outcome'], 'removed')
        self.assertFalse(self.directory.exists())
        self.assertEqual((self.archive / 'com1.log').read_bytes(), b'com1.log\n')

    def test_missing_or_corrupt_state_requires_manual_cleanup_without_shutdown(self):
        state_path = self.directory / 'instance.json'
        for contents in (None, b'not JSON', b'[]'):
            with self.subTest(contents=contents):
                if contents is None:
                    state_path.unlink()
                else:
                    state_path.write_bytes(contents)
                with self.mock_destroy() as client, patch.object(_lifecycle, '_shutdown_instance') as shutdown:
                    with self.assertRaisesRegex(CLIError, 'ambiguous.*Manual cleanup is required'):
                        _lifecycle.destroy(self.name, no_logs=True)
                    shutdown.assert_not_called()
                    client.assert_not_called()
                self.assertTrue((self.directory / 'com1.log').exists())
                self.assertFalse(self.archive_root.exists())

    def test_orphan_staging_is_preserved_when_instance_is_not_found(self):
        self.portable_remove(self.directory, _cleanup._directory_identity(self.directory))
        staging = _cleanup.staging_path(self.archive)
        staging.mkdir(mode=0o700, parents=True)
        with self.mock_destroy() as client, patch.object(_lifecycle, '_shutdown_instance') as shutdown:
            with self.assertRaisesRegex(CLIError, 'Enclave instance not found'):
                _lifecycle.destroy(self.name, no_logs=True)
        shutdown.assert_not_called()
        client.assert_not_called()
        self.assertTrue(staging.is_dir())

    def test_unreadable_log_preserves_originals_and_reports_failure(self):
        open_file = os.open

        def unreadable(path, flags, *args, **kwargs):
            if Path(path) == self.directory / 'com1.log':
                raise PermissionError('Log cannot be read')
            return open_file(path, flags, *args, **kwargs)

        with patch.object(_cleanup.os, 'open', side_effect=unreadable):
            with self.assertRaisesRegex(PermissionError, 'Log cannot be read'):
                self.finalize()
        self.assertTrue((self.directory / 'com1.log').exists())
        self.assertFalse(self.archive.exists())
        self.assertEqual(self.finalize(no_logs=True)['log_disposition'], 'discarded')

    def test_copy_failure_preserves_originals_and_reports_failure(self):
        with patch.object(_cleanup, '_copy_log', side_effect=PermissionError('Cannot copy logs')), \
                patch.object(_cleanup, '_remove_instance') as remove:
            with self.assertRaisesRegex(PermissionError, 'Cannot copy logs'):
                self.finalize()
        remove.assert_not_called()
        self.assertFalse(self.archive.exists())
        for name in _cleanup.LOG_NAMES:
            self.assertTrue((self.directory / name).exists())
        saved = _instance.read_record(self.directory / 'instance.json')
        self.assertEqual(saved['cleanup_phase'], 'archiving')
        self.assertEqual(saved['cleanup_error'], 'Cannot copy logs')


class CleanupOwnershipTest(LinuxFilesystemTest):
    """Verify process-record validation and filesystem safety independently of orchestration."""

    def test_invalid_process_identity_does_not_prove_shutdown(self):
        process = {'pid': 123, 'boot': '00000000-0000-0000-0000-000000000001', 'start': '1', 'command': 'a' * 64}
        for record in ({'pid': 123}, {'pid': '123'}, {'pid': -1}, None,
                       dict(process, unexpected=True), dict(process, command='invalid'),
                       dict(process, boot='invalid'), dict(process, start='invalid')):
            with self.subTest(record=record), self.assertRaisesRegex(CLIError, 'Invalid process identity'):
                _lifecycle._validate_process(record)

    def test_instance_record_is_deleted_last(self):
        directory = Path('instance')
        expected_identity = [1, 2]
        with patch.object(_cleanup, '_directory_identity', return_value=expected_identity), \
                patch.object(_cleanup, '_regular_file'), \
                patch.object(_cleanup.os, 'O_DIRECTORY', 0, create=True), \
                patch.object(_cleanup.os, 'open', return_value=47), \
                patch.object(_cleanup.os, 'fstat', return_value=SimpleNamespace(st_dev=1, st_ino=2)), \
                patch.object(_cleanup.os, 'listdir', return_value=['instance.json', 'com1.log', 'runtime.json']), \
                patch.object(_cleanup.os, 'unlink') as unlink, \
                patch.object(_cleanup.os, 'close'), \
                patch.object(Path, 'rmdir'), \
                patch.object(_cleanup, 'sync_directory'):
            _cleanup._remove_instance(directory, expected_identity)
        self.assertEqual([call.args[0] for call in unlink.call_args_list],
                         ['com1.log', 'runtime.json', 'instance.json'])

    @unittest.skipUnless(sys.platform == 'linux', 'Requires Linux renameat2 no-replace semantics')
    def test_publication_never_replaces_a_racing_empty_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = Path(temporary) / 'staging', Path(temporary) / 'archive'
            source.mkdir(mode=0o700)
            destination.mkdir(mode=0o700)
            original_inode = destination.stat().st_ino
            with self.assertRaises(FileExistsError):
                _cleanup._publish(source, destination)
            self.assertTrue(source.is_dir())
            self.assertEqual(destination.stat().st_ino, original_inode)

    def test_private_regular_file_validation(self):
        path = Path('unsafe.log')
        for mode, uid, links in (
                (stat.S_IFLNK | 0o600, 0, 1), (stat.S_IFIFO | 0o600, 0, 1),
                (stat.S_IFREG | 0o644, 0, 1), (stat.S_IFREG | 0o600, 1, 1),
                (stat.S_IFREG | 0o600, 0, 2)):
            with self.subTest(mode=mode, uid=uid, links=links), \
                    patch.object(Path, 'lstat', return_value=SimpleNamespace(
                        st_mode=mode, st_uid=uid, st_nlink=links)), \
                    patch('os.geteuid', return_value=0, create=True):
                with self.assertRaisesRegex(CLIError, 'private, owned regular file'):
                    _cleanup._regular_file(path)
