# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from knack.util import CLIError


def setup_ace(workspace=None, source_dir=None):
    from ._bvt import setup_bvt
    return setup_bvt(workspace, source_dir)


def create_ace(workspace=None, source_dir=None, output_dir=None):
    from ._bvt import create_bvt
    return create_bvt(workspace, source_dir, output_dir)


def launch_ace():
    raise CLIError(
        '`az ace launch` is not implemented yet. '
        'It will use OpenVMM RPC to create and launch the enclave VM from the IGVM file.')


def destroy_ace():
    raise CLIError(
        '`az ace destroy` is not implemented yet. '
        'It will use OpenVMM RPC to destroy the enclave.')
