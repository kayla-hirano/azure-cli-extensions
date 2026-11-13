# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Exercise host setup without installing packages or changing the host."""

import subprocess
from dataclasses import replace
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from knack.util import CLIError

from azext_enclave import _setup
from azext_enclave._isolation import load_isolation
from azext_enclave._runtime import RUNTIME_PACKAGE, RUNTIME_PATH


class HostSetupTest(unittest.TestCase):
    """Verify validation, explicit approval, and post-install runtime verification."""

    def setUp(self) -> None:
        patches = {
            'sys.platform': 'linux',
            'os.geteuid': 0,
            'Path.read_text': 'ID=azurelinux\nVERSION_ID="3.0"\n',
            'Path.exists': None,
            'Path.stat': SimpleNamespace(st_mode=0o020600),
            'shutil.which': '/usr/bin/rpm',
            'user_confirmation': None,
            'resolve_runtime': Path(RUNTIME_PATH),
            'subprocess.run': SimpleNamespace(returncode=0, stdout='', stderr=''),
        }
        self.mocks = {}
        for target, value in patches.items():
            kwargs = {'new': value} if target == 'sys.platform' else {'return_value': value}
            if target == 'os.geteuid':
                kwargs['create'] = True
            patcher = patch('azext_enclave._setup.' + target, **kwargs)
            self.mocks[target] = patcher.start()
            self.addCleanup(patcher.stop)
        self.mocks['Path.exists'].side_effect = [True, False]
        self.mocks['shutil.which'].side_effect = ['/usr/bin/rpm', '/usr/bin/tdnf']

    def test_installs_exact_package_after_confirmation(self) -> None:
        """Install the supported OpenVMM package only after confirmation and verify the resulting runtime."""
        result = _setup.setup_host()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['openvmm'], str(Path(RUNTIME_PATH)))
        self.assertIn(RUNTIME_PACKAGE, self.mocks['user_confirmation'].call_args.args[0])
        self.assertIs(self.mocks['user_confirmation'].call_args.args[1], False)
        self.mocks['subprocess.run'].assert_called_once_with(
            ['/usr/bin/tdnf', 'install', '-y', RUNTIME_PACKAGE],
            capture_output=True, text=True, timeout=300, check=False)
        self.mocks['resolve_runtime'].assert_called_once_with(RUNTIME_PATH)

    def test_invalid_configuration_precedes_host_checks_and_installation(self) -> None:
        """Reject invalid isolation metadata before checking host privileges or attempting installation."""
        with patch.object(_setup, 'load_isolation', side_effect=ValueError('bad manifest')):
            with self.assertRaisesRegex(ValueError, 'bad manifest'):
                _setup.setup_host()
        self.mocks['os.geteuid'].assert_not_called()
        self.mocks['subprocess.run'].assert_not_called()
        self.mocks['user_confirmation'].assert_not_called()

    def test_unimplemented_backend_precedes_host_changes(self) -> None:
        """Reject backends without a setup implementation before host checks or package changes."""
        configuration = load_isolation()
        configuration = replace(configuration, runtime=replace(configuration.runtime, backend='kvm'))
        with patch.object(_setup, 'load_isolation', return_value=configuration):
            with self.assertRaisesRegex(ValueError, 'Host setup is not implemented for backend: kvm'):
                _setup.setup_host()
        self.mocks['os.geteuid'].assert_not_called()
        self.mocks['subprocess.run'].assert_not_called()

    def test_all_manifest_devices_are_checked_before_installation(self) -> None:
        """Require every declared character device before prompting for or installing runtime packages."""
        configuration = replace(load_isolation(), character_devices=('/dev/mshv', '/dev/required'))
        self.mocks['Path.exists'].side_effect = [True, False]
        with patch.object(_setup, 'load_isolation', return_value=configuration):
            with self.assertRaisesRegex(CLIError, '/dev/required character device'):
                _setup.setup_host()
        self.mocks['subprocess.run'].assert_not_called()
        self.mocks['user_confirmation'].assert_not_called()

    def test_yes_is_forwarded_to_cli_confirmation(self) -> None:
        """Pass explicit noninteractive approval through to the package-installation confirmation."""
        _setup.setup_host(yes=True)
        self.assertIs(self.mocks['user_confirmation'].call_args.args[1], True)

    def test_existing_runtime_is_verified_without_installation(self) -> None:
        """Verify an existing runtime on every setup invocation without reinstalling it."""
        self.mocks['Path.exists'].side_effect = None
        self.mocks['Path.exists'].return_value = True
        for _ in range(2):
            self.assertEqual(_setup.setup_host()['status'], 'ready')
        self.assertEqual(self.mocks['resolve_runtime'].call_count, 2)
        self.mocks['user_confirmation'].assert_not_called()
        self.mocks['subprocess.run'].assert_not_called()
        self.assertEqual(self.mocks['shutil.which'].call_count, 2)

    def test_existing_invalid_runtime_is_not_repaired_implicitly(self) -> None:
        """Report an invalid installed runtime without silently replacing or repairing it."""
        self.mocks['Path.exists'].side_effect = None
        self.mocks['Path.exists'].return_value = True
        self.mocks['resolve_runtime'].side_effect = CLIError('Unrecognized runtime; repair the package.')
        with self.assertRaisesRegex(CLIError, 'repair the package'):
            _setup.setup_host()
        self.mocks['user_confirmation'].assert_not_called()
        self.mocks['subprocess.run'].assert_not_called()

    def test_refuses_non_linux_or_non_root_hosts(self) -> None:
        """Require Linux and root privileges before reading host configuration or changing packages."""
        with patch('azext_enclave._setup.sys.platform', 'win32'):
            with self.assertRaisesRegex(CLIError, 'Azure Linux 3 L1'):
                _setup.setup_host()
        self.mocks['os.geteuid'].return_value = 1000
        with self.assertRaisesRegex(CLIError, 'as root'):
            _setup.setup_host()
        self.mocks['Path.read_text'].assert_not_called()
        self.mocks['subprocess.run'].assert_not_called()

    def test_refuses_unsupported_distributions(self) -> None:
        """Reject non-Azure-Linux-3.0 hosts before device checks or package installation."""
        for release in ('ID=ubuntu\nVERSION_ID="24.04"', 'ID=azurelinux\nVERSION_ID="2.0"', ''):
            with self.subTest(release=release):
                self.mocks['Path.read_text'].return_value = release
                with self.assertRaisesRegex(CLIError, 'Azure Linux 3.0 only'):
                    _setup.setup_host()
        self.mocks['Path.exists'].assert_not_called()
        self.mocks['subprocess.run'].assert_not_called()

    def test_requires_kernel_device_before_installing(self) -> None:
        """Require the kernel-provided character device without attempting kernel changes or runtime installation."""
        self.mocks['Path.exists'].side_effect = None
        self.mocks['Path.exists'].return_value = False
        with self.assertRaisesRegex(CLIError, 'does not change kernels or reboot'):
            _setup.setup_host()
        self.mocks['Path.exists'].return_value = True
        self.mocks['Path.stat'].return_value = SimpleNamespace(st_mode=0o100600)
        with self.assertRaisesRegex(CLIError, 'character device'):
            _setup.setup_host()
        self.mocks['subprocess.run'].assert_not_called()
        self.mocks['user_confirmation'].assert_not_called()

    def test_requires_package_tools(self) -> None:
        """Require RPM verification and an available installer before running any package transaction."""
        self.mocks['shutil.which'].side_effect = [None]
        with self.assertRaisesRegex(CLIError, 'RPM is required'):
            _setup.setup_host()
        self.mocks['Path.exists'].side_effect = [True, False]
        self.mocks['shutil.which'].side_effect = ['/usr/bin/rpm', None]
        with self.assertRaisesRegex(CLIError, 'tdnf is required'):
            _setup.setup_host()
        self.mocks['subprocess.run'].assert_not_called()

    def test_declined_or_noninteractive_confirmation_does_not_install(self) -> None:
        """Honor rejected installation confirmation without installing packages or claiming runtime readiness."""
        self.mocks['user_confirmation'].side_effect = CLIError('Operation cancelled.')
        with self.assertRaisesRegex(CLIError, 'Operation cancelled'):
            _setup.setup_host()
        self.mocks['subprocess.run'].assert_not_called()
        self.mocks['resolve_runtime'].assert_not_called()

    def test_package_install_failure_is_explicit(self) -> None:
        """Surface package-manager failures and skip runtime verification after an unsuccessful installation."""
        self.mocks['subprocess.run'].return_value = SimpleNamespace(
            returncode=1, stdout='', stderr='No matching package')
        with self.assertRaisesRegex(CLIError, 'installation failed.*No matching package'):
            _setup.setup_host()
        self.mocks['resolve_runtime'].assert_not_called()

    def test_package_install_timeout_is_not_success(self) -> None:
        """Report timed-out installation as an unconfirmed package state rather than a successful setup."""
        self.mocks['subprocess.run'].side_effect = subprocess.TimeoutExpired('tdnf', 300)
        with self.assertRaisesRegex(CLIError, 'Package state is unconfirmed'):
            _setup.setup_host()
        self.mocks['resolve_runtime'].assert_not_called()

    def test_install_requires_successful_runtime_verification(self) -> None:
        """Require runtime verification to succeed after installation before reporting setup readiness."""
        self.mocks['resolve_runtime'].side_effect = CLIError('RPM verification failed')
        with self.assertRaisesRegex(CLIError, 'RPM verification failed'):
            _setup.setup_host()
        self.mocks['subprocess.run'].assert_called_once()
