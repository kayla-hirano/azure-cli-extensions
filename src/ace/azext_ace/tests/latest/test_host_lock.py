# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Verify the host-wide lifetime lease independently of VM startup and RPC."""

import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from knack.util import CLIError

from azext_ace import _instance


class HostLockTest(unittest.TestCase):
    """Keep all state roots in one slot and retain it across process handoff."""

    def test_guard_uses_fixed_host_location(self) -> None:
        directory = Path('/run/ace/enclave')
        with patch('azext_ace._instance.instance_path', return_value=directory) as path, \
                patch('azext_ace._instance.instance_lock', return_value=nullcontext(47)) as lock:
            with _instance.host_lock() as descriptor:
                self.assertEqual(descriptor, 47)
        path.assert_called_once_with('/run/ace', 'enclave')
        self.assertEqual(lock.call_args.args[0], directory)
        self.assertIn('Destroy the existing instance', lock.call_args.args[1])

    @unittest.skipUnless(sys.platform == 'linux', 'Requires Linux flock and descriptor inheritance.')
    def test_different_names_and_state_roots_share_one_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch('azext_ace._instance.HOST_STATE_DIR', str(base / 'host')):
                first = _instance.instance_path(str(base / 'first-root'), 'first')
                second = _instance.instance_path(str(base / 'second-root'), 'second')
                with _instance.host_lock(), _instance.instance_lock(first):
                    # Per-instance locks are independent; the host guard must reject the second launch.
                    with _instance.instance_lock(second):
                        with self.assertRaisesRegex(CLIError, 'already starting or running'):
                            with _instance.host_lock():
                                self.fail('A second host slot must not be acquired.')
                with _instance.host_lock():
                    pass

    @unittest.skipUnless(sys.platform == 'linux', 'Requires Linux flock and descriptor inheritance.')
    def test_child_retains_slot_after_launcher_closes_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch('azext_ace._instance.HOST_STATE_DIR', temporary):
                with _instance.host_lock() as descriptor:
                    child = subprocess.Popen(
                        [sys.executable, '-c', 'import sys; sys.stdin.buffer.read(1)'],
                        stdin=subprocess.PIPE, pass_fds=(descriptor,))
                try:
                    with self.assertRaisesRegex(CLIError, 'already starting or running'):
                        with _instance.host_lock():
                            self.fail('The runtime must retain the lease after the launcher exits.')
                finally:
                    child.communicate(b'x', timeout=5)
                self.assertEqual(child.returncode, 0)
                # Exit releases the lease without deleting a lock file or editing instance evidence.
                with _instance.host_lock():
                    pass
