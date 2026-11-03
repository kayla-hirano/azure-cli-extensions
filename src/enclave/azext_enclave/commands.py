# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

def load_command_table(self, _):
    with self.command_group('enclave') as g:
        g.custom_command('setup', 'setup_enclave')
        g.custom_command('create', 'create_enclave')
        g.custom_command('launch', 'launch_enclave')
        g.custom_command('destroy', 'destroy_enclave')
