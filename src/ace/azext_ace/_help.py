# coding=utf-8
# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Define ACE command help, local-L1 requirements, and launch/destroy usage examples."""

from knack.help_files import helps  # pylint: disable=unused-import


helps['ace'] = """
    type: group
    short-summary: Set up dependencies and manage enclave artifacts and virtual machines.
    long-summary: Launch and destroy local SNP IGVM instances on Linux L1. Setup and create remain placeholders.
"""

helps['ace setup'] = """
    type: command
    short-summary: Set up enclave dependencies.
    long-summary: >
        Not implemented yet. This command may be removed in favor of extension
        installation through setup.py. Host provisioning remains separate.
"""

helps['ace create'] = """
    type: command
    short-summary: Pack the workload into the initrd and create the enclave IGVM file.
    long-summary: Not implemented yet.
"""

helps['ace launch'] = """
    type: command
    short-summary: Create and launch the enclave VM from the IGVM file using OpenVMM RPC.
    long-summary: >
        Run as root on Linux L1. Uses the SKR BVT's fixed CPU, memory, PCIe and vsock configuration.
        Starts a persistent local OpenVMM gRPC server, creates an SNP IGVM VM and resumes it.
        Waits for an SNP-shaped guest response, but does not verify attestation or release keys.
        Logs and instance state remain under the state directory.
    examples:
      - name: Launch an existing SKR BVT image on L1.
        text: az ace launch --name demo --igvm /home/user/skr-bvt/skr-bvt.igvm --openvmm /usr/bin/openvmm
"""

helps['ace destroy'] = """
    type: command
    short-summary: Destroy the enclave using OpenVMM RPC.
    long-summary: >
        Run as root on the same Linux L1 and use the same state directory as launch.
        Tears down the VM through RPC, quits OpenVMM and cleans up owned sockets.
        Preserves the IGVM, logs and instance record. A confirmed destroyed instance can be destroyed again.
    examples:
      - name: Destroy a local instance.
        text: az ace destroy --name demo
"""
