# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Exercise instance records, host guards, socket safety, and process ownership."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knack.util import CLIError

from azext_enclave import _instance
from ._helpers import LinuxFilesystemTest


class InstanceTest(LinuxFilesystemTest):
    """Exercise record safety, process identity, and host prerequisites."""

    def test_name_validation(self):
        """Reject instance names that are empty, too long, or could escape their state root."""
        for name in ('../other', '', 'a/b', 'a' * 33):
            with self.subTest(name=name), self.assertRaisesRegex(CLIError, '--name'):
                _instance.instance_path(name)

    def test_host_validation(self):
        """Reject unsupported hosts and invalid timeout values before lifecycle work starts."""
        with patch('azext_enclave._instance.sys.platform', 'win32'):
            with self.assertRaisesRegex(CLIError, 'Linux L1'):
                _instance.require_host(10)
        with patch('azext_enclave._instance.sys.platform', 'linux'), \
                patch('azext_enclave._instance.os.geteuid', return_value=0, create=True):
            for value in (0, -1, float('inf'), float('nan')):
                with self.subTest(value=value), self.assertRaisesRegex(CLIError, '--timeout'):
                    _instance.require_host(value)

    def test_pid_reuse_is_not_signaled(self):
        """Ensure cleanup does not signal a different process that reused a recorded PID."""
        record = {'pid': 321, 'start': '100'}
        with patch('azext_enclave._instance.identity', return_value={'pid': 321, 'start': '101'}), \
                patch('azext_enclave._instance.os.kill') as kill:
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

    def test_refuses_symlink_before_following_socket_target(self) -> None:
        with patch.object(Path, 'is_symlink', return_value=True), \
                patch.object(Path, 'is_socket') as is_socket, patch.object(Path, 'unlink') as unlink:
            with self.assertRaisesRegex(CLIError, 'symlink runtime path'):
                _instance.cleanup_sockets(Path('instance'))
        is_socket.assert_not_called()
        unlink.assert_not_called()

    def test_record_round_trip(self):
        """Verify atomic record replacement exposes the newest state and leaves no temporary file."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'instance.json'
            _instance.write_record(path, {'status': 'starting'})
            _instance.write_record(path, {'status': 'running'})
            self.assertEqual(_instance.read_record(path), {'status': 'running'})
            self.assertFalse(path.with_suffix('.tmp').exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Requires Linux /proc and flock')
    def test_linux_identity_and_exclusive_lock(self):
        """Use real Linux process/lock primitives and reject symlinked state-directory paths."""
        current = _instance.identity(os.getpid())
        self.assertTrue(_instance.alive(current))
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            base.chmod(0o700)
            with patch('azext_enclave._instance.DEFAULT_STATE_DIR', str(base)):
                directory = _instance.instance_path('demo')
            with _instance.instance_lock(directory):
                with self.assertRaisesRegex(CLIError, 'Another command'):
                    with _instance.instance_lock(directory):
                        self.fail('Second lock should not be acquired')
            target = base / 'target'
            target.mkdir(mode=0o700)
            (base / 'link').symlink_to(target, target_is_directory=True)
            with patch('azext_enclave._instance.DEFAULT_STATE_DIR', str(base / 'link')), \
                    self.assertRaisesRegex(CLIError, 'symlink'):
                _instance.instance_path('demo')
