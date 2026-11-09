# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Shared CLI wiring and platform shims for isolated Enclave tests."""

import sys
import unittest
from unittest.mock import patch

from azure.cli.core import AzCli, MainCommandsLoader
from azure.cli.core._help import AzCliHelp
from azure.cli.core._output import AzOutputProducer
from azure.cli.core.azlogging import AzCliLogging
from azure.cli.core.commands import AzCliCommandInvoker
from azure.cli.core.parser import AzCliCommandParser

from azext_enclave import EnclaveCommandsLoader


class EnclaveTestCommandsLoader(MainCommandsLoader):
    """Load only Enclave through the normal Azure CLI main-loader argument wiring."""

    def load_command_table(self, args: list) -> dict:
        loader = EnclaveCommandsLoader(cli_ctx=self.cli_ctx)
        self.command_table = loader.load_command_table(args)
        self.cmd_to_loader_map = {name: [loader] for name in self.command_table}
        return self.command_table


def create_cli(config_dir: str) -> AzCli:
    """Create an Enclave-only CLI using the caller's isolated configuration directory."""
    return AzCli(
        cli_name='az', config_dir=config_dir, config_env_var_prefix='AZURE',
        commands_loader_cls=EnclaveTestCommandsLoader,
        invocation_cls=AzCliCommandInvoker, parser_cls=AzCliCommandParser,
        help_cls=AzCliHelp, logging_cls=AzCliLogging, output_cls=AzOutputProducer)


class LinuxFilesystemTest(unittest.TestCase):
    """Share test-only compatibility shims without changing Linux production guards."""

    def setUp(self):
        """Provide missing Linux constants when mocked filesystem/socket tests run on Windows."""
        if sys.platform != 'linux':
            patcher = patch('azext_enclave._instance.os.O_NOFOLLOW', 0, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
            patcher = patch('socket.AF_UNIX', 1, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
