# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Resolve and verify the OpenVMM runtime shared by host setup and launch."""

import shutil
import subprocess
from pathlib import Path

from knack.log import get_logger
from knack.util import CLIError

logger = get_logger(__name__)
RUNTIME_VERSION = '0.2.0-2.azl3'
RUNTIME_PACKAGE = 'openvmm-' + RUNTIME_VERSION
RUNTIME_PATH = '/usr/bin/openvmm'


def resolve_runtime(openvmm: str, allow_untested_runtime: bool = False) -> Path:
    """Require expected RPM provenance unless explicitly bypassed."""
    selected = shutil.which(openvmm)
    if selected is None:
        raise CLIError('OpenVMM executable not found: {}'.format(openvmm))
    executable = Path(selected).resolve()
    rpm = shutil.which('rpm')
    package = None
    if rpm:
        try:
            result = subprocess.run(
                [rpm, '-qf', '--queryformat', '%{VERSION}-%{RELEASE}', str(executable)],
                capture_output=True, text=True, timeout=10, check=False)
            if result.returncode == 0:
                package = result.stdout.strip()
                verification = subprocess.run(
                    [rpm, '-Vf', str(executable)], capture_output=True, text=True, timeout=10, check=False)
                if verification.returncode != 0:
                    package = None
        except subprocess.TimeoutExpired as error:
            raise CLIError('Timed out verifying the OpenVMM RPM package.') from error
    if package != RUNTIME_VERSION:
        if not allow_untested_runtime:
            raise CLIError(
                'Expected Azure Linux OpenVMM {} with SNP RPC and low-ECAM patch; '
                'found {}. Use --allow-untested-runtime only after checking equivalent support.'
                .format(RUNTIME_VERSION, package or 'an unrecognized runtime'))
        logger.warning('Using untested OpenVMM runtime. Verify SNP IGVM RPC and below-4-GiB ECAM support.')
    return executable
