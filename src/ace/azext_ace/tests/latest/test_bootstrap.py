# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Run developer bootstrap against fake tools, without installing any dependencies."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BASH = os.environ.get('ACE_TEST_BASH') or (shutil.which('bash') if sys.platform == 'linux' else None)
SCRIPT = Path(__file__).resolve().parents[3] / 'scripts' / 'bootstrap-dev.sh'


def shell_path(path: Path) -> str:
    """Translate fixture paths when explicitly running these tests with Git Bash on Windows."""
    if sys.platform == 'win32':
        return '/' + path.drive[0].lower() + path.as_posix()[2:]
    return str(path)


@unittest.skipUnless(BASH, 'Requires bash; ACE_TEST_BASH can select Git Bash on Windows.')
class BootstrapTest(unittest.TestCase):
    """Verify environment creation, reuse, quoting, and explicit failure paths."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.tools = self.base / 'tools'
        self.tools.mkdir()
        self.repo = self.base / 'checkout with spaces'
        (self.repo / 'src' / 'ace').mkdir(parents=True)
        (self.repo / 'src' / 'ace' / 'setup.py').touch()
        self.venv = self.base / 'venv with spaces'
        self.log = self.base / 'calls.log'
        self.environment = os.environ.copy()
        self.environment.update({
            'ACE_REPO': shell_path(self.repo),
            'ACE_VENV': shell_path(self.venv),
            'AZURE_CONFIG_DIR': 'do-not-use-normal-config',
            'AZURE_EXTENSION_DIR': 'do-not-use-normal-extensions',
            'FAKE_TOOLS': shell_path(self.tools),
            'FAKE_LOG': shell_path(self.log),
            'PATH': str(self.tools) + os.pathsep + os.environ['PATH'],
        })
        tools = {
            'id': 'printf "%s\\n" "${FAKE_UID:-1000}"',
            'git': 'exit 0',
            'sudo': 'printf "sudo must not run\\n" >&2; exit 99',
            'python3.12': '''
printf 'venv\\n' >> "$FAKE_LOG"
mkdir -p -- "$3/bin"
touch "$3/pyvenv.cfg"
cp "$FAKE_TOOLS/python" "$3/bin/python"
cp "$FAKE_TOOLS/azdev" "$3/bin/azdev"
printf 'export PATH=%q:$PATH\\n' "$3/bin" > "$3/bin/activate"
''',
            'python': '''
printf 'python %s\\n' "$*" >> "$FAKE_LOG"
if [[ "${FAKE_PIP_FAIL:-0}" == 1 && "$*" == '-m pip install azdev setuptools wheel' ]]; then
    printf 'pip failed\\n' >&2
    exit 13
fi
''',
            'azdev': 'printf "azdev %s\\n" "$*" >> "$FAKE_LOG"',
        }
        for name, body in tools.items():
            path = self.tools / name
            path.write_text('#!/usr/bin/env bash\nset -euo pipefail\n' + body + '\n', encoding='utf-8')
            path.chmod(0o755)

    def run_bootstrap(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, '-c', 'export PATH="$FAKE_TOOLS:$PATH"; source "$1"', 'bootstrap-test', shell_path(SCRIPT)],
            env=self.environment,
            capture_output=True, text=True, timeout=30, check=False)

    def test_bootstrap_creates_reusable_isolated_environment(self) -> None:
        for _ in range(2):
            result = self.run_bootstrap()
            self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text()
        self.assertEqual(calls.splitlines().count('venv'), 1)
        self.assertIn('azdev setup --cli pypi --repo ' + shell_path(self.repo), calls)
        self.assertIn('azdev extension add ace', calls)
        self.assertIn('python -m pip install azdev setuptools wheel', calls)
        self.assertIn('python -m pip install --no-build-isolation --config-settings editable_mode=compat -e ', calls)
        self.assertIn('python -c import grpc; import google.protobuf; import azext_ace', calls)
        self.assertTrue((self.venv / 'azure-config').is_dir())
        self.assertTrue((self.venv / 'extensions').is_dir())
        result = subprocess.run(
            [BASH, '-c', 'source "$ACE_VENV/activate-ace.sh"; '
             'printf "%s\\n" "$ACE_REPO" "$ACE_VENV" "$AZURE_CONFIG_DIR" "$AZURE_EXTENSION_DIR"'],
            env=self.environment, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            shell_path(self.repo), shell_path(self.venv),
            shell_path(self.venv / 'azure-config'), shell_path(self.venv / 'extensions'),
        ])

    def test_refuses_root_before_creating_environment(self) -> None:
        self.environment['FAKE_UID'] = '0'
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('normal user, not root', result.stderr)
        self.assertFalse(self.venv.exists())

    def test_refuses_non_virtual_environment_directory(self) -> None:
        self.venv.mkdir()
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not a usable virtual environment', result.stderr)
        self.assertFalse((self.venv / 'azure-config').exists())

    def test_refuses_missing_checkout(self) -> None:
        (self.repo / 'src' / 'ace' / 'setup.py').unlink()
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ACE checkout not found', result.stderr)
        self.assertFalse(self.venv.exists())

    def test_install_failure_does_not_report_ready(self) -> None:
        self.environment['FAKE_PIP_FAIL'] = '1'
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 13)
        self.assertIn('pip failed', result.stderr)
        self.assertNotIn('environment ready', result.stdout)
        self.assertFalse((self.venv / 'activate-ace.sh').exists())
