# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Expose ACE command handlers while keeping build and lifecycle logic out of CLI registration."""

from pathlib import Path
from typing import Optional

from azure.cli.core.commands import AzCliCommand
from knack.log import get_logger
from knack.util import CLIError


logger = get_logger(__name__)
DEPENDENCIES_DIR = Path(__file__).parent / 'dependencies'
DEFAULT_DEPENDENCY_BUNDLE = DEPENDENCIES_DIR / 'dependencies.tar.gz'
DEFAULT_APPLICATION = DEPENDENCIES_DIR / 'enclave_app'


def setup_ace(yes: bool = False) -> dict:
    """Prepare supported host dependencies without modifying the developer environment."""
    from ._setup import setup_host
    try:
        return setup_host(yes)
    except (OSError, ValueError) as error:
        raise CLIError('Unable to set up ACE host: {}'.format(error)) from error


def create_ace(cmd: AzCliCommand, workspace: str, output_file: str,
               dependency_bundle: Optional[str] = None, init_script: Optional[str] = None,
               signing_key: Optional[str] = None,
               keep_intermediates: bool = False, application: Optional[str] = None) -> dict:
    """Compose an IGVM from trusted dependencies and a packaged or caller-supplied application."""
    from ._image_builder import IgvmBuildError, build_igvm
    bundle = Path(dependency_bundle).expanduser() if dependency_bundle is not None else DEFAULT_DEPENDENCY_BUNDLE
    if dependency_bundle is None and not bundle.is_file():
        raise CLIError(
            'The packaged dependency bundle is missing at {}. Reinstall the extension, '
            'or supply --dependency-bundle with a trusted archive '
            'or bundle directory.'.format(bundle))
    try:
        result = build_igvm(
            dependency_bundle=bundle,
            application=Path(application).expanduser() if application is not None else DEFAULT_APPLICATION,
            init_script=(Path(init_script) if init_script is not None
                         else DEPENDENCIES_DIR / 'initramfs' / 'skr-bvt-init'),
            workspace=Path(workspace),
            output=Path(output_file),
            signing_key=Path(signing_key) if signing_key is not None else None,
            debug=keep_intermediates,
            enable_debug='--debug' in cmd.cli_ctx.data['safe_params'],
            logger=logger)
    except IgvmBuildError as error:
        detail = 'Unable to create ACE image: {}'.format(error)
        if error.workspace is not None:
            detail += '\nBuild workspace retained at: {}'.format(error.workspace)
        raise CLIError(detail) from error
    return {'igvm': str(result.igvm), 'measurements': result.measurements}


def _instance_result(state: dict) -> dict:
    """Keep process ownership records internal while exposing the supervisor PID."""
    excluded = {'attestation_verified', 'directory', 'openvmm', 'openvmm_sha256', 'supervisor'}
    result = {key: value for key, value in state.items() if key not in excluded}
    if state.get('supervisor') is not None:
        result['supervisor_pid'] = state['supervisor']['pid']
    return result


def launch_ace(name: str, igvm: str, openvmm: str = 'openvmm',
               state_dir: str = '/var/lib/ace/instances', timeout: float = 120,
               allow_untested_runtime: bool = False) -> dict:
    """Delegate local RPC launch and translate filesystem/record errors for Azure CLI users."""
    from ._lifecycle import launch
    try:
        return _instance_result(launch(name, igvm, openvmm, state_dir, timeout, allow_untested_runtime))
    except (OSError, ValueError, KeyError) as error:
        raise CLIError('Unable to launch ACE instance: {}'.format(error)) from error


def destroy_ace(name: str, state_dir: str = '/var/lib/ace/instances', timeout: float = 30) -> dict:
    """Delegate instance teardown and translate filesystem/record errors for Azure CLI users."""
    from ._lifecycle import destroy
    try:
        return _instance_result(destroy(name, state_dir, timeout))
    except (OSError, ValueError, KeyError) as error:
        raise CLIError('Unable to destroy ACE instance: {}'.format(error)) from error
