# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Verify Azure CLI registration, help, arguments, and delegation without launching a VM."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from knack.util import CLIError
from azure.cli.core import get_default_cli

from azext_ace import AceCommandsLoader
from azext_ace import custom
from azext_ace._help import helps


class AceCommandTest(unittest.TestCase):
    """Exercise the extension loader and public command-handler boundary."""

    def test_command_registration(self):
        """Keep the advertised command set stable while lifecycle implementations evolve."""
        loader = AceCommandsLoader(cli_ctx=get_default_cli())
        command_table = loader.load_command_table([])
        self.assertEqual(set(command_table), {
            'ace setup', 'ace create', 'ace launch', 'ace destroy'
        })

    def test_command_arguments(self):
        """Check that only launch/destroy expose the new instance and runtime options."""
        expected = {
            'ace setup': set(),
            'ace create': set(),
            'ace launch': {'name', 'igvm', 'openvmm', 'state_dir', 'timeout', 'allow_untested_runtime'},
            'ace destroy': {'name', 'state_dir', 'timeout'},
        }
        for command in ('ace setup', 'ace create', 'ace launch', 'ace destroy'):
            with self.subTest(command=command):
                cli = get_default_cli()
                cli.invocation = SimpleNamespace(data={'command_string': command})
                loader = AceCommandsLoader(cli_ctx=cli)
                table = loader.load_command_table([])
                loader.load_arguments(command)
                table[command].load_arguments()
                self.assertEqual(set(table[command].arguments), expected[command])

    def test_placeholder_commands(self):
        """Ensure unfinished provisioning/image-building commands fail explicitly."""
        for command in ('setup', 'create'):
            with self.subTest(command=command):
                handler = getattr(custom, '{}_ace'.format(command))
                with self.assertRaisesRegex(
                        CLIError, '`az ace {}` is not implemented yet'.format(command)):
                    handler()

    def test_command_help(self):
        """Distinguish placeholder help from the local-L1 lifecycle requirements."""
        for command in ('setup', 'create'):
            with self.subTest(command=command):
                self.assertIn('Not implemented yet.', helps['ace {}'.format(command)])
        for command in ('launch', 'destroy'):
            self.assertIn('Linux L1', helps['ace {}'.format(command)])

    def test_lifecycle_delegation(self):
        """Verify CLI defaults are passed unchanged to the lifecycle implementation."""
        with patch('azext_ace._lifecycle.launch', return_value={'status': 'running'}) as launch:
            self.assertEqual(custom.launch_ace('demo', 'demo.igvm'), {'status': 'running'})
            launch.assert_called_once_with(
                'demo', 'demo.igvm', 'openvmm', '/var/lib/ace/instances', 120, False)
        with patch('azext_ace._lifecycle.destroy', return_value={'status': 'destroyed'}) as destroy:
            self.assertEqual(custom.destroy_ace('demo'), {'status': 'destroyed'})
            destroy.assert_called_once_with('demo', '/var/lib/ace/instances', 30)

    def test_setup_future_removal_note(self):
        """Preserve the existing explanation of setup's possible replacement by installation."""
        self.assertIn('may be removed', helps['ace setup'])
        self.assertIn('setup.py', helps['ace setup'])
        with self.assertRaisesRegex(CLIError, 'may be removed.*setup.py'):
            custom.setup_ace()
