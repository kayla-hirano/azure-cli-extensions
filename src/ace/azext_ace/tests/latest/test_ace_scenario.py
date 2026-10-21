# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import unittest

from knack.util import CLIError
from azure.cli.core import get_default_cli

from azext_ace import AceCommandsLoader
from azext_ace import custom
from azext_ace._help import helps


class AceCommandTest(unittest.TestCase):

    def test_command_registration(self):
        loader = AceCommandsLoader(cli_ctx=get_default_cli())
        command_table = loader.load_command_table([])
        self.assertEqual(set(command_table), {
            'ace setup', 'ace create', 'ace launch', 'ace destroy'
        })

    def test_placeholder_commands(self):
        for command in ('setup', 'create', 'launch', 'destroy'):
            with self.subTest(command=command):
                handler = getattr(custom, '{}_ace'.format(command))
                with self.assertRaisesRegex(
                        CLIError, '`az ace {}` is not implemented yet'.format(command)):
                    handler()

    def test_command_help(self):
        for command in ('setup', 'create', 'launch', 'destroy'):
            with self.subTest(command=command):
                self.assertIn('Not implemented yet.', helps['ace {}'.format(command)])