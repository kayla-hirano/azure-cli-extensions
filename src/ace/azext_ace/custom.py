# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Expose ACE command handlers while keeping runtime lifecycle logic out of CLI registration."""

from knack.util import CLIError


def setup_ace(yes: bool = False) -> dict:
    """Prepare supported host dependencies without modifying the developer environment."""
    from ._setup import setup_host
    try:
        return setup_host(yes)
    except (OSError, ValueError) as error:
        raise CLIError('Unable to set up ACE host: {}'.format(error)) from error


def create_ace():
    """Report that workload packaging and IGVM generation are not implemented by this command."""
    raise CLIError(
        '`az ace create` is not implemented yet. '
        'It will pack the workload into the initrd and create the enclave IGVM file.')


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
