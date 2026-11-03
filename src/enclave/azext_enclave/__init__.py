# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from azure.cli.core import AzCommandsLoader

from azext_enclave._help import helps  # pylint: disable=unused-import


class EnclaveCommandsLoader(AzCommandsLoader):

    def __init__(self, cli_ctx=None):
        from azure.cli.core.commands import CliCommandType
        enclave_custom = CliCommandType(
            operations_tmpl='azext_enclave.custom#{}')
        super(EnclaveCommandsLoader, self).__init__(
            cli_ctx=cli_ctx, custom_command_type=enclave_custom)

    def load_command_table(self, args):
        from azext_enclave.commands import load_command_table
        load_command_table(self, args)
        return self.command_table

    def load_arguments(self, command):
        from azext_enclave._params import load_arguments
        load_arguments(self, command)


COMMAND_LOADER_CLS = EnclaveCommandsLoader
