# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Expose ACE command handlers while keeping build and lifecycle logic out of CLI registration."""

from pathlib import Path
from typing import Optional

from knack.log import get_logger
from knack.util import CLIError


logger = get_logger(__name__)


def setup_ace(yes: bool = False) -> dict:
    """Prepare supported host dependencies without modifying the developer environment."""
    from ._setup import setup_host
    try:
        return setup_host(yes)
    except (OSError, ValueError) as error:
        raise CLIError('Unable to set up ACE host: {}'.format(error)) from error


def create_ace(dependency_bundle: str, application: str, workspace: str, output_file: str,
               init_script: Optional[str] = None, signing_key: Optional[str] = None,
               keep_intermediates: bool = False) -> dict:
    """Compose an IGVM from trusted dependencies and an already-compiled workload."""
    from ._image_builder import IgvmBuildError, build_igvm
    try:
        image = build_igvm(
            dependency_bundle=Path(dependency_bundle),
            application=Path(application),
            init_script=(Path(init_script) if init_script is not None
                         else Path(__file__).parent / 'initramfs' / 'skr-bvt-init'),
            workspace=Path(workspace),
            output=Path(output_file),
            signing_key=Path(signing_key) if signing_key is not None else None,
            debug=keep_intermediates,
            logger=logger)
    except IgvmBuildError as error:
        detail = 'Unable to create ACE image: {}'.format(error)
        if error.workspace is not None:
            detail += '\nBuild workspace retained at: {}'.format(error.workspace)
        raise CLIError(detail) from error
    return {'igvm': str(image)}


def launch_ace(name: str, igvm: str, openvmm: str = 'openvmm',
               state_dir: str = '/var/lib/ace/instances', timeout: float = 120,
               allow_untested_runtime: bool = False) -> dict:
    """Delegate local RPC launch and translate filesystem/record errors for Azure CLI users."""
    from ._lifecycle import launch
    try:
        return launch(name, igvm, openvmm, state_dir, timeout, allow_untested_runtime)
    except (OSError, ValueError, KeyError) as error:
        raise CLIError('Unable to launch ACE instance: {}'.format(error)) from error


def destroy_ace(name: str, state_dir: str = '/var/lib/ace/instances', timeout: float = 30) -> dict:
    """Delegate instance teardown and translate filesystem/record errors for Azure CLI users."""
    from ._lifecycle import destroy
    try:
        return destroy(name, state_dir, timeout)
    except (OSError, ValueError, KeyError) as error:
        raise CLIError('Unable to destroy ACE instance: {}'.format(error)) from error
