# coding=utf-8
# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from knack.help_files import helps  # pylint: disable=unused-import


helps['ace'] = """
    type: group
    short-summary: Set up dependencies and manage enclave artifacts and virtual machines.
    long-summary: Command structure only. Execution is not implemented yet.
"""

helps['ace setup'] = """
    type: command
    short-summary: Install and validate all dependencies.
    long-summary: Not implemented yet.
"""

helps['ace create'] = """
    type: command
    short-summary: Pack the workload into the initrd and create the enclave IGVM file.
    long-summary: Not implemented yet.
"""

helps['ace launch'] = """
    type: command
    short-summary: Create and launch the enclave VM from the IGVM file using OpenVMM RPC.
    long-summary: Not implemented yet.
"""

helps['ace destroy'] = """
    type: command
    short-summary: Destroy the enclave using OpenVMM RPC.
    long-summary: Not implemented yet.
"""
