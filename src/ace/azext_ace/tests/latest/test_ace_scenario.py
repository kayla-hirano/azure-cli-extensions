# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_command_arguments(self):
        for command, expected in (
                ('ace setup', {'workspace', 'source_dir'}),
                ('ace create', {'workspace', 'source_dir', 'output_dir'})):
            with self.subTest(command=command):
                cli = get_default_cli()
                cli.invocation = SimpleNamespace(data={'command_string': command})
                loader = AceCommandsLoader(cli_ctx=cli)
                table = loader.load_command_table([])
                loader.load_arguments(command)
                table[command].load_arguments()
                self.assertEqual(set(table[command].arguments), expected)

    def test_placeholder_commands(self):
        for command in ('launch', 'destroy'):
            with self.subTest(command=command):
                handler = getattr(custom, '{}_ace'.format(command))
                with self.assertRaisesRegex(
                        CLIError, '`az ace {}` is not implemented yet'.format(command)):
                    handler()

    def test_command_help(self):
        for command in ('launch', 'destroy'):
            with self.subTest(command=command):
                self.assertIn('Not implemented yet.', helps['ace {}'.format(command)])
        for command in ('setup', 'create'):
            self.assertIn('BVT', helps['ace {}'.format(command)])
            self.assertNotIn('Not implemented yet.', helps['ace {}'.format(command)])

    def test_setup_dispatch(self):
        with patch('azext_ace._bvt.setup_bvt', return_value={'status': 'ready'}) as setup:
            self.assertEqual(custom.setup_ace('work', 'source'), {'status': 'ready'})
        setup.assert_called_once_with('work', 'source')

    def test_create_dispatch(self):
        with patch('azext_ace._bvt.create_bvt', return_value={'status': 'created'}) as create:
            self.assertEqual(custom.create_ace('work', 'source', 'output'), {'status': 'created'})
        create.assert_called_once_with('work', 'source', 'output')