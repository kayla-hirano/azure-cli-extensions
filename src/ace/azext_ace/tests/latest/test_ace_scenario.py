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
        """Keep setup approval and lifecycle options scoped to the appropriate commands."""
        expected = {
            'ace setup': {'yes'},
            'ace create': {'cmd', 'dependency_bundle', 'application', 'init_script', 'workspace',
                           'output_file', 'signing_key', 'keep_intermediates'},
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

    def test_command_help(self):
        """Distinguish unprivileged image building from the local-L1 lifecycle requirements."""
        self.assertIn('Root and Azure login are not required.', helps['ace create'])
        self.assertIn('--output-file', helps['ace create'])
        self.assertIn('--keep-intermediates', helps['ace create'])
        self.assertIn('--debug enables SNP guest debugging and diagnostic logs.', helps['ace create'])
        self.assertIn('expected SNP launch measurements', helps['ace create'])
        self.assertIn('Azure Linux 3.0 L1', helps['ace setup'])
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

    def test_setup_delegation(self) -> None:
        """Keep host preparation separate from Python dependency installation."""
        self.assertIn('setup.py', helps['ace setup'])
        with patch('azext_ace._setup.setup_host', return_value={'status': 'ready'}) as setup:
            self.assertEqual(custom.setup_ace(), {'status': 'ready'})
            setup.assert_called_once_with(False)
        with patch('azext_ace._setup.setup_host', return_value={'status': 'ready'}) as setup:
            self.assertEqual(custom.setup_ace(yes=True), {'status': 'ready'})
            setup.assert_called_once_with(True)
        with patch('azext_ace._setup.setup_host', side_effect=OSError('cannot read os-release')):
            with self.assertRaisesRegex(CLIError, 'Unable to set up ACE host.*cannot read os-release'):
                custom.setup_ace()

    def test_lifecycle_results_hide_internal_state_without_mutating_it(self) -> None:
        for operation, status in (('launch', 'running'), ('destroy', 'destroyed')):
            with self.subTest(operation=operation):
                supervisor = {'pid': 123, 'start': '456', 'boot': 'boot-id', 'command': 'command-hash'}
                public = {
                    'schema': 1, 'name': 'demo', 'status': status, 'igvm': 'demo.igvm',
                    'igvm_sha256': 'image-hash',
                    'vsock': {'port': 4050, 'socket': 'vsock.sock'},
                    'rpc_socket': 'rpc.sock',
                    'logs': {'openvmm': 'openvmm.log', 'supervisor': 'supervisor.log', 'com1': 'com1.log'},
                    'instance_file': 'instance.json', 'response_file': 'response.json',
                }
                state = dict(public, attestation_verified=False, directory='instance-directory',
                             openvmm='openvmm', openvmm_sha256='runtime-hash', supervisor=supervisor)
                original = state.copy()
                with patch('azext_ace._lifecycle.' + operation, return_value=state):
                    result = (custom.launch_ace('demo', 'demo.igvm') if operation == 'launch'
                              else custom.destroy_ace('demo'))
                self.assertEqual(result, dict(public, supervisor_pid=123))
                self.assertEqual(state, original)
                self.assertIsNot(result, state)
                self.assertEqual(state['supervisor'], supervisor)

    def test_instance_result_without_supervisor(self) -> None:
        for supervisor in ({}, {'supervisor': None}):
            with self.subTest(supervisor=supervisor):
                state = dict(supervisor, name='demo', status='destroyed')
                self.assertEqual(custom._instance_result(state), {'name': 'demo', 'status': 'destroyed'})

    def test_setup_preserves_cli_errors(self) -> None:
        """Do not obscure explicit host setup failures with a generic wrapper."""
        with patch('azext_ace._setup.setup_host', side_effect=CLIError('Operation cancelled.')):
            with self.assertRaisesRegex(CLIError, '^Operation cancelled\\.$'):
                custom.setup_ace()
