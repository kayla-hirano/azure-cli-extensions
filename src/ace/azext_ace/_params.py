# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
"""Register local instance, image, runtime, and timeout arguments for ACE lifecycle commands."""


def load_arguments(self, _):
    """Attach shared lifecycle arguments and launch-only boot/runtime options to the command loader."""
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
