# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Expose Enclave command handlers while keeping build and lifecycle logic out of CLI registration."""

from pathlib import Path
from typing import Optional

from azure.cli.core.commands import AzCliCommand
from knack.log import get_logger
from knack.util import CLIError


logger = get_logger(__name__)
DEPENDENCIES_DIR = Path(__file__).parent / 'dependencies'
DEFAULT_DEPENDENCY_BUNDLE = DEPENDENCIES_DIR / 'dependencies.tar.gz'
DEFAULT_APPLICATION = DEPENDENCIES_DIR / 'enclave_app'


def setup_enclave(yes: bool = False) -> dict:
    """Prepare supported host dependencies without modifying the developer environment."""
    from ._setup import setup_host
    try:
        return setup_host(yes)
    except (OSError, ValueError) as error:
        raise CLIError('Unable to set up Enclave host: {}'.format(error)) from error


def create_enclave(cmd: AzCliCommand, output_file: str, workspace: Optional[str] = None,
                   dependency_bundle: Optional[str] = None, init_script: Optional[str] = None,
                   signing_key: Optional[str] = None,
                   application: Optional[str] = None, keep_intermediates: bool = False) -> dict:
    """Compose an IGVM from trusted dependencies and a packaged or caller-supplied application."""
    from ._image_builder import IgvmBuildError, build_igvm
    bundle = Path(dependency_bundle).expanduser() if dependency_bundle is not None else DEFAULT_DEPENDENCY_BUNDLE
    if dependency_bundle is None and not bundle.is_file():
        raise CLIError(
            'The packaged dependency bundle is missing at {}. Reinstall the extension, '
            'or supply --dependency-bundle with a trusted archive '
            'or bundle directory.'.format(bundle))
    enable_debug = '--debug' in cmd.cli_ctx.data['safe_params']
    if enable_debug:
        logger.warning(
            'Debug mode is enabled. Secure Key Release (SKR) policies that require a non-debug enclave '
            'will deny key release. For testing, ensure your SKR policy explicitly allows debug-enabled '
            'enclaves; do not use debug mode in production.')
    try:
        result = build_igvm(
            dependency_bundle=bundle,
            application=Path(application).expanduser() if application is not None else DEFAULT_APPLICATION,
            init_script=(Path(init_script).expanduser() if init_script is not None
                         else DEPENDENCIES_DIR / 'initramfs' / 'enclave-init'),
            workspace=Path(workspace).expanduser() if workspace is not None else None,
            output=Path(output_file).expanduser(),
            signing_key=Path(signing_key).expanduser() if signing_key is not None else None,
            keep_intermediates=keep_intermediates,
            enable_debug=enable_debug,
            logger=logger)
    except IgvmBuildError as error:
        detail = 'Unable to create Enclave image: {}'.format(error)
        if error.workspace is not None:
            detail += '\nBuild workspace available at: {}'.format(error.workspace)
        raise CLIError(detail) from error
    return {'igvm': str(result.igvm), 'measurements': result.measurements}


def _instance_result(state: dict) -> dict:
    """Keep process ownership records internal while exposing the supervisor PID."""
    excluded = {'attestation_verified', 'directory', 'openvmm', 'openvmm_sha256', 'supervisor'}
    result = {key: value for key, value in state.items() if key not in excluded}
    if state.get('supervisor') is not None:
        result['supervisor_pid'] = state['supervisor']['pid']
    return result


def launch_enclave(name: str, igvm: str, openvmm: str = 'openvmm',
                   timeout: float = 120,
                   allow_untested_runtime: bool = False) -> dict:
    """Delegate local RPC launch and translate filesystem/record errors for Azure CLI users."""
    from ._lifecycle import launch
    try:
        return _instance_result(launch(name, igvm, openvmm, timeout, allow_untested_runtime))
    except (OSError, ValueError, KeyError) as error:
        raise CLIError('Unable to launch Enclave instance: {}'.format(error)) from error


def destroy_enclave(name: str, timeout: float = 30) -> dict:
    """Delegate instance teardown and translate filesystem/record errors for Azure CLI users."""
    from ._lifecycle import destroy
    try:
        return _instance_result(destroy(name, timeout))
    except (OSError, ValueError, KeyError) as error:
        raise CLIError('Unable to destroy Enclave instance: {}'.format(error)) from error
