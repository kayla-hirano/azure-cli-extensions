# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
"""Register host setup, image creation, and local lifecycle arguments."""


def load_arguments(self, _):
    """Attach command-specific setup, build, and lifecycle options to the command loader."""
    with self.argument_context('enclave setup') as context:
        context.argument('yes', options_list=['--yes', '-y'], action='store_true',
                         help='Approve installation of the supported OpenVMM package without prompting.')
    with self.argument_context('enclave create') as context:
        context.argument('dependency_bundle', required=False,
                         help='Path to a trusted compatible dependency bundle (tar/tar.gz or directory). '
                              'Defaults to the packaged bundle.')
        context.argument('application', required=False,
                         help='Path to a prebuilt static x86-64 ELF application. Defaults to the packaged application. '
                              'Use --init-script to customize how it starts.')
        context.argument('init_script', required=False,
                         help='Path to a guest startup script with a shebang and LF line endings. '
                              'Replaces the default script; the application is installed at /bin/enclave_app.')
        context.argument('workspace', required=False,
                         help='Parent directory for temporary build files; defaults to the system temporary location '
                              '(honoring TMPDIR). Needs space and execution support.')
        context.argument('output_file', options_list=['--output-file'], required=True,
                         help='Destination IGVM filename. Existing files are never overwritten.')
        context.argument('signing_key',
                         help='Optional unencrypted PEM ECDSA P-384 private key for SNP ID-block signing.')
        context.argument('keep_intermediates', action='store_true',
                         help='Keep the private build directory and stage logs on success or failure. '
                              'The retained path is reported after the build.')
    for command in ('enclave launch', 'enclave destroy'):
        with self.argument_context(command) as context:
            context.argument('name', options_list=['--name', '-n'], required=True,
                             help='Local instance name: 1-32 letters, digits, underscores or hyphens, '
                                  'starting with a letter or digit.')
            context.argument('timeout', type=float, help='Operation timeout in seconds.')
    with self.argument_context('enclave launch') as context:
        context.argument('igvm', required=True, help='Path to an existing compatible SNP enclave image (IGVM).')
        context.argument('openvmm', help='OpenVMM executable path or command name.')
        context.argument('allow_untested_runtime', action='store_true',
                         help='Skip the supported runtime package check. For testing only; '
                              'you must ensure the runtime is compatible.')
