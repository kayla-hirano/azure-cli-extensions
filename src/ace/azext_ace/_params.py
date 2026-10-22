# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------
def load_arguments(self, _):
    for command in ('ace setup', 'ace create'):
        with self.argument_context(command) as context:
            context.argument('workspace', help='BVT source/input cache. Default: ~/.azure/ace/bvt.')
            context.argument(
                'source_dir',
                help='Existing ACC-CVM-Tooling checkout. Default: the handoff checkout inside the workspace.')
    with self.argument_context('ace create') as context:
        context.argument('output_dir', help='New bundle directory. Default: <workspace>/skr-bvt. Must not exist.')
