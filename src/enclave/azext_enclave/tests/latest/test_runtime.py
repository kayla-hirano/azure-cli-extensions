# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Verify runtime package provenance and explicit custom-runtime opt-in."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from knack.util import CLIError

from azext_enclave import _runtime


class RuntimeTest(unittest.TestCase):
    """Verify runtime provenance before executing OpenVMM."""

    def test_runtime_requires_known_package_or_explicit_opt_in(self):
        """Require expected package provenance unless the caller knowingly permits a custom runtime."""
        with patch('azext_enclave._runtime.shutil.which', side_effect=['/usr/bin/openvmm', '/usr/bin/rpm']), \
                patch('azext_enclave._runtime.subprocess.run',
                      return_value=SimpleNamespace(returncode=0, stdout='0.2.0-2.azl3')):
            self.assertEqual(_runtime.resolve_runtime('openvmm', False).name, 'openvmm')
        with patch('azext_enclave._runtime.shutil.which', side_effect=['/custom/openvmm', None]):
            with self.assertRaisesRegex(CLIError, 'low-ECAM'):
                _runtime.resolve_runtime('openvmm', False)
        with patch('azext_enclave._runtime.shutil.which', side_effect=['/custom/openvmm', None]), \
                patch('azext_enclave._runtime.logger.warning') as warning:
            _runtime.resolve_runtime('openvmm', True)
            warning.assert_called_once()

    def test_modified_package_requires_explicit_opt_in(self):
        """Treat failed RPM integrity verification as an unrecognized runtime."""
        with patch('azext_enclave._runtime.shutil.which', side_effect=['/usr/bin/openvmm', '/usr/bin/rpm']), \
                patch('azext_enclave._runtime.subprocess.run', side_effect=[
                    SimpleNamespace(returncode=0, stdout='0.2.0-2.azl3'),
                    SimpleNamespace(returncode=1, stdout='modified executable')]):
            with self.assertRaisesRegex(CLIError, 'unrecognized runtime'):
                _runtime.resolve_runtime('openvmm', False)

    def test_package_verification_timeout_is_actionable(self):
        """Translate a stalled RPM check into a clear CLI error."""
        import subprocess
        with patch('azext_enclave._runtime.shutil.which', side_effect=['/usr/bin/openvmm', '/usr/bin/rpm']), \
                patch('azext_enclave._runtime.subprocess.run',
                      side_effect=subprocess.TimeoutExpired('rpm', 10)):
            with self.assertRaisesRegex(CLIError, 'Timed out verifying'):
                _runtime.resolve_runtime('openvmm', False)
