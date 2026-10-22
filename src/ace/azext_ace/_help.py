# coding=utf-8
# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from knack.help_files import helps  # pylint: disable=unused-import


helps['ace'] = """
    type: group
    short-summary: Set up dependencies and manage enclave artifacts and virtual machines.
    long-summary: Setup and create implement the fixed SKR BVT demo on Azure Linux L1. Launch and destroy are not implemented.
"""

helps['ace setup'] = """
    type: command
    short-summary: Install dependencies and build pinned SKR BVT inputs on Azure Linux L1.
    long-summary: >
        Uses tdnf and sudo as needed, starts Docker, installs rustup if missing,
        clones the handoff and pinned upstream sources, and builds the kernel, initrd,
        bootshim and image generator. Requires existing Git authentication and Docker access.
        Does not change the host kernel, Docker permissions, or launch an enclave.
    examples:
      - name: Prepare the BVT demo.
        text: az ace setup --workspace ~/ace-demo
"""

helps['ace create'] = """
    type: command
    short-summary: Build the fixed sample workload and create a checksummed SKR BVT bundle.
    long-summary: >
        Revalidates setup inputs and invokes the ACC-CVM-Tooling BVT builder.
        Always builds the sample BVT workload; arbitrary workloads are not supported.
        Does not launch the enclave or verify attestation.
    examples:
      - name: Build a new bundle after setup.
        text: az ace create --workspace ~/ace-demo --output-dir ~/ace-demo/bundle
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
