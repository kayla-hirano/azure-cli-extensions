# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Validate Azure Linux L1 prerequisites and install the supported OpenVMM package."""

import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from azure.cli.core.util import user_confirmation
from knack.log import get_logger
from knack.util import CLIError

from ._runtime import RUNTIME_PACKAGE, RUNTIME_PATH, resolve_runtime

logger = get_logger(__name__)


def setup_host(yes: bool = False) -> dict:
    """Check the host before offering a bounded, version-specific package installation."""
    if sys.platform != 'linux':
        raise CLIError('ACE setup must run locally on Azure Linux 3 L1.')
    if os.geteuid() != 0:
        raise CLIError('Run ACE setup as root on L1.')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                   if line.startswith(('ID=', 'VERSION_ID=')))
    if release.get('ID', '').strip('"\'') != 'azurelinux' \
            or release.get('VERSION_ID', '').strip('"\'') != '3.0':
        raise CLIError('Automatic ACE setup supports Azure Linux 3.0 only; no system changes were made.')
    device = Path('/dev/mshv')
    if not device.exists() or not stat.S_ISCHR(device.stat().st_mode):
        raise CLIError(
            'A compatible L1 kernel exposing the /dev/mshv character device is required. '
            'Provision the kernel separately; ACE setup does not change kernels or reboot.')
    if not shutil.which('rpm'):
        raise CLIError('RPM is required to verify OpenVMM. Use an Azure Linux host with rpm installed.')
    if not Path(RUNTIME_PATH).exists():
        manager = shutil.which('tdnf')
        if manager is None:
            raise CLIError('tdnf is required to install OpenVMM. No packages were installed.')
        user_confirmation(
            'Install {} and its dependencies from the configured Azure Linux repositories? '
            'ACE will not add repositories, change kernels, or reboot.'.format(RUNTIME_PACKAGE), yes)
        logger.warning('Installing %s; do not interrupt the package transaction.', RUNTIME_PACKAGE)
        try:
            result = subprocess.run(
                [manager, 'install', '-y', RUNTIME_PACKAGE],
                capture_output=True, text=True, timeout=300, check=False)
        except subprocess.TimeoutExpired as error:
            raise CLIError(
                'OpenVMM installation timed out. Package state is unconfirmed; '
                'inspect the package manager before retrying.') from error
        if result.returncode != 0:
            raise CLIError(
                'OpenVMM installation failed: {}. Check package availability in the configured repositories.'
                .format(result.stderr.strip() or result.stdout.strip() or 'tdnf exited {}'.format(result.returncode)))
    # Existing installations must also pass the same version/integrity checks used by launch.
    executable = resolve_runtime(RUNTIME_PATH)
    logger.warning('Host prerequisites verified; an SNP guest boot and attestation have not been verified.')
    return {'status': 'ready', 'os': 'Azure Linux 3.0', 'kernel': platform.release(), 'openvmm': str(executable)}
