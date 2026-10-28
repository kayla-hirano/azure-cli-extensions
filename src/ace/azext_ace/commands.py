# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

def load_command_table(self, _):
    with self.command_group('ace') as g:
        g.custom_command('setup', 'setup_ace')
        g.custom_command('create', 'create_ace')
        g.custom_command('launch', 'launch_ace')
        g.custom_command('destroy', 'destroy_ace')
