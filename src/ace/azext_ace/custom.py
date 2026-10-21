# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from knack.util import CLIError


def setup_ace():
    raise CLIError('`az ace setup` is not implemented yet. It will install and validate all dependencies.')


def create_ace():
    raise CLIError(
        '`az ace create` is not implemented yet. '
        'It will pack the workload into the initrd and create the enclave IGVM file.')


def launch_ace():
    raise CLIError(
        '`az ace launch` is not implemented yet. '
        'It will use OpenVMM RPC to create and launch the enclave VM from the IGVM file.')


def destroy_ace():
    raise CLIError(
        '`az ace destroy` is not implemented yet. '
        'It will use OpenVMM RPC to destroy the enclave.')