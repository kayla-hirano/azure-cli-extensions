# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
"""Register host setup, image creation, and local lifecycle arguments."""


def load_arguments(self, _):
    """Attach command-specific setup, build, and lifecycle options to the command loader."""
    with self.argument_context('ace setup') as context:
        context.argument('yes', options_list=['--yes', '-y'], action='store_true',
                         help='Approve installation of the supported OpenVMM package without prompting.')
    with self.argument_context('ace create') as context:
        context.argument('dependency_bundle', required=False,
                         help='Override the packaged dependency bundle with a trusted measured-pcie-v1 '
                              'tar/tar.gz archive or bundle directory.')
        context.argument('init_script', required=False,
                         help='LF-only guest /init override. Defaults to the packaged SKR BVT script, '
                              'which mounts guest filesystems and starts /bin/enclave_app serve-vsock 4050.')
        context.argument('workspace', required=True,
                         help='Parent directory for a private build workspace; may already exist.')
        context.argument('output_file', options_list=['--output-file'], required=True,
                         help='Destination IGVM filename. Existing files are never overwritten.')
        context.argument('signing_key',
                         help='Optional unencrypted PEM ECDSA P-384 private key for SNP ID-block signing.')
        context.argument('keep_intermediates', action='store_true',
                         help='Retain the private build workspace and stage logs on success or failure.')
    for command in ('ace launch', 'ace destroy'):
        with self.argument_context(command) as context:
            context.argument('name', options_list=['--name', '-n'], required=True,
                             help='Local instance name (1-32 letters, digits, underscores or hyphens).')
            context.argument('state_dir', help='Private local instance directory root (mode 0700).')
            context.argument('timeout', type=float, help='Operation timeout in seconds.')
    with self.argument_context('ace launch') as context:
        context.argument('igvm', required=True, help='Existing SKR BVT IGVM file on Linux L1.')
        context.argument('openvmm', help='OpenVMM executable path or command name.')
        context.argument('allow_untested_runtime', action='store_true',
                         help='Allow a runtime other than the expected Azure Linux 0.2.0-2.azl3 package. '
                              'Requires equivalent SNP IGVM RPC and low-ECAM support.')
