# coding=utf-8
# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Define ACE command help, image-build requirements, and local-L1 lifecycle examples."""

from knack.help_files import helps  # pylint: disable=unused-import


helps['ace'] = """
    type: group
    short-summary: Set up dependencies and manage enclave artifacts and virtual machines.
    long-summary: Build SNP IGVM images from existing dependencies, prepare Azure Linux L1 hosts, and manage local instances.
"""

helps['ace setup'] = """
    type: command
    short-summary: Validate Azure Linux 3 L1 and install the supported OpenVMM package.
    long-summary: >
        Run as root on Azure Linux 3.0 L1 with a compatible kernel exposing /dev/mshv.
        Installs OpenVMM 0.2.0-2.azl3 with confirmation if absent, then verifies its RPM provenance.
        Existing incompatible or modified installations fail verification and require manual repair.
        Does not change kernels, add repositories, reboot, or install developer tooling.
        Python runtime dependencies are installed with the extension through setup.py.
    examples:
      - name: Check host prerequisites and install OpenVMM if needed.
        text: az ace setup
      - name: Approve package installation without an interactive prompt.
        text: az ace setup --yes
"""

helps['ace create'] = """
    type: command
    short-summary: Package a static workload and init script into an SNP enclave IGVM.
    long-summary: >
        Run on Linux or WSL with GNU cpio and gzip. Root and Azure login are not required.
        Uses a trusted measured-pcie-v1 dependency bundle and a precompiled static x86-64 application.
        Defaults to the packaged SKR BVT init script; use --init-script to override guest startup.
        Does not compile workloads or dependencies, install tools, or launch a VM.
        Only the final IGVM is published; an existing output is never overwritten.
        The development profile enables confidential debug; creation does not verify boot or attestation.
    examples:
      - name: Create an image using the packaged SKR BVT init script.
        text: >
            az ace create --dependency-bundle /opt/ace/dependencies --application /home/user/enclave_app
            --workspace /home/user/ace-build
            --output-file /home/user/images/enclave.igvm
      - name: Override guest startup, sign the SNP ID block, and retain intermediate files.
        text: >
            az ace create --dependency-bundle /opt/ace/dependencies --application /home/user/enclave_app
            --init-script /home/user/skr-bvt-init --workspace /home/user/ace-build
            --output-file /home/user/images/signed.igvm --signing-key /home/user/test-id-key.pem
            --keep-intermediates
"""

helps['ace launch'] = """
    type: command
    short-summary: Create and launch the enclave VM from the IGVM file using OpenVMM RPC.
    long-summary: >
        Run as root on Linux L1. Uses the SKR BVT's fixed CPU, memory, PCIe and vsock configuration.
        Starts a persistent local OpenVMM gRPC server, creates an SNP IGVM VM and resumes it.
        Allows only one active ACE enclave per L1, regardless of name or state directory.
        Destroy the existing instance before launching another.
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
