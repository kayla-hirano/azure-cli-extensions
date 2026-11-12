# coding=utf-8
# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Define Enclave command help, image-build requirements, and local-L1 lifecycle examples."""

from knack.help_files import helps  # pylint: disable=unused-import


helps['enclave'] = """
    type: group
    short-summary: Prepare a host, build enclave images, and run local enclaves.
    long-summary: Start with setup, create an image, then launch and destroy an enclave.
"""

helps['enclave setup'] = """
    type: command
    short-summary: Check host requirements and install the enclave runtime if needed.
    long-summary: >
        Run as root on Azure Linux 3.0 L1 with a compatible kernel exposing /dev/mshv.
        Checks the host and the supported OpenVMM runtime, asking before installing it if missing.
        Does not update the kernel or reboot the host.
    examples:
      - name: Check host prerequisites and install OpenVMM if needed.
        text: sudo az enclave setup
      - name: Approve package installation without an interactive prompt.
        text: sudo az enclave setup --yes
"""

helps['enclave create'] = """
    type: command
    short-summary: Build an enclave image using packaged defaults or your own application.
    long-summary: >
        Run in a Linux environment (including WSL) with GNU cpio and gzip. Root and Azure login are not required.
        Saves an IGVM image and returns expected SNP launch measurements; does not launch an enclave.
        Existing output files are never overwritten.
        Failed or interrupted builds retain temporary files and logs automatically and report their location.
        Successful builds delete them unless --keep-intermediates is set.
        Use --verbose for progress. --debug also enables guest debugging; do not use it for production images.
    examples:
      - name: Create an image with the packaged application and startup script.
        text: az enclave create --output-file ./enclave.igvm
      - name: Use a different directory for temporary build files.
        text: >
            az enclave create --output-file ./enclave.igvm --workspace /mnt/build
      - name: Package your own enclave application with a matching startup script.
        text: >
            az enclave create --application ./my-app --init-script ./my-init
            --output-file ./custom.igvm
"""

helps['enclave launch'] = """
    type: command
    short-summary: Start a local enclave from an existing image.
    long-summary: >
        Run as root on Linux L1 after setup. Choose an unused name.
        The enclave keeps running after this command exits; destroy it before launching another.
        Returns status and log paths under /var/lib/enclave/instances.
        Checks that the guest responds, but does not verify attestation or release keys.
    examples:
      - name: Launch an image created with enclave create.
        text: sudo az enclave launch --name demo --igvm ./enclave.igvm
"""

helps['enclave destroy'] = """
    type: command
    short-summary: Stop a local enclave and its runtime.
    long-summary: >
        Run as root on the same Linux L1 used for launch.
        Stops the enclave and preserves its image, logs, and instance record.
        Retained records reserve their names; use a new name for the next launch.
        If destruction fails, inspect the logs and retry; failure does not confirm the enclave stopped.
    examples:
      - name: Destroy a local instance.
        text: sudo az enclave destroy --name demo
"""
