# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import call, patch

from knack.util import CLIError

from azext_ace import _bvt as bvt


class BvtTest(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name).resolve()
        self.source = self.work / 'ACC-CVM-Tooling'
        self.write(self.work / 'setup-inputs.sha256')

    def write(self, path, content='test'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def test_rejects_non_linux_and_non_x64(self):
        for system, machine in (('Windows', 'AMD64'), ('Linux', 'aarch64')):
            with self.subTest(system=system), patch.object(bvt.platform, 'system', return_value=system), \
                    patch.object(bvt.platform, 'machine', return_value=machine):
                with self.assertRaisesRegex(CLIError, 'x86-64 Azure Linux'):
                    bvt._require_platform()

    def test_distribution_check(self):
        with patch.object(bvt.platform, 'system', return_value='Linux'), \
                patch.object(bvt.platform, 'machine', return_value='x86_64'), \
                patch.object(Path, 'read_text', return_value='ID=ubuntu\n'):
            with self.assertRaisesRegex(CLIError, 'only on Azure Linux'):
                bvt._require_platform()
        with patch.object(bvt.platform, 'system', return_value='Linux'), \
                patch.object(bvt.platform, 'machine', return_value='x86_64'), \
                patch.object(Path, 'read_text', return_value='ID="azurelinux"\n'):
            bvt._require_platform()

    def test_default_paths(self):
        work, source = bvt._paths(None, None)
        self.assertEqual(work, (Path.home() / '.azure' / 'ace' / 'bvt').resolve())
        self.assertEqual(source, work / 'ACC-CVM-Tooling')

    def test_run_executes_without_shell_and_keeps_output_separate(self):
        marker = 'literal; not a shell command'
        result = bvt._run([sys.executable, '-c', 'import sys; print(sys.argv[1])', marker], capture=True)
        self.assertEqual(result, marker)

    def test_run_failure_is_not_success_shaped(self):
        with self.assertRaisesRegex(CLIError, 'exit 7'):
            bvt._run([sys.executable, '-c', 'raise SystemExit(7)'])

    def test_missing_executable_is_actionable(self):
        with patch.object(bvt.subprocess, 'run', side_effect=FileNotFoundError('missing')):
            with self.assertRaisesRegex(CLIError, 'Cannot execute'):
                bvt._run(['missing'])

    def test_missing_tools(self):
        with patch.object(bvt.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(CLIError, 'Missing dependencies: cargo, git'):
                bvt._require_tools(['git', 'cargo'])

    def test_environment_fixes_demo_target(self):
        with patch.dict(os.environ, {'TARGET': 'wrong', 'DOCKER_BUILDKIT': '0', 'RUSTUP_TOOLCHAIN': 'old'}):
            env = bvt._environment()
        self.assertEqual(env['TARGET'], 'x86_64-unknown-linux-musl')
        self.assertEqual(env['DOCKER_BUILDKIT'], '1')
        self.assertEqual(env['RUSTUP_TOOLCHAIN'], bvt.RUST_TOOLCHAIN)

    def test_install_missing_packages_and_start_docker(self):
        with patch.object(bvt, '_require_tools'), \
                patch.object(bvt.shutil, 'which', return_value=None), \
                patch.object(Path, 'is_file', return_value=False), \
                patch.object(bvt, '_privileged', side_effect=lambda args: ['sudo'] + args), \
                patch.object(bvt, '_run') as run:
            bvt._install_tools()
        install = run.call_args_list[0].args[0]
        self.assertEqual(install[:4], ['sudo', 'tdnf', 'install', '-y'])
        for package in ('docker-cli', 'docker-buildx', 'moby-engine', 'openssl-devel',
                        'openvmm-' + bvt.OPENVMM_PACKAGE):
            self.assertIn(package, install)
        self.assertIn(call(['sudo', 'systemctl', 'start', 'docker']), run.call_args_list)
        self.assertIn(call(['docker', 'info']), run.call_args_list)
        self.assertIn(call(['docker', 'buildx', 'version']), run.call_args_list)

    def test_installed_packages_are_not_reinstalled(self):
        with patch.object(bvt, '_require_tools'), patch.object(bvt.shutil, 'which', return_value='/bin/tool'), \
                patch.object(Path, 'is_file', return_value=True), \
                patch.object(bvt, '_privileged', side_effect=lambda args: args), \
                patch.object(bvt, '_run') as run:
            bvt._install_tools()
        self.assertFalse(any('tdnf' in item.args[0] for item in run.call_args_list))

    def test_docker_access_failure_is_explicit(self):
        with patch.object(bvt, '_require_tools'), patch.object(bvt.shutil, 'which', return_value='/bin/tool'), \
                patch.object(Path, 'is_file', return_value=True), \
                patch.object(bvt, '_privileged', side_effect=lambda args: args), \
                patch.object(bvt, '_run', side_effect=[None, CLIError('permission denied')]):
            with self.assertRaisesRegex(CLIError, 'Docker is not accessible'):
                bvt._install_tools()

    def test_rustup_install_is_temporary_and_reuses_existing(self):
        with patch.object(bvt.shutil, 'which', return_value=None), \
                patch.object(bvt, '_require_tools'), patch.object(bvt, '_run') as run:
            bvt._prepare_rust()
        self.assertEqual(run.call_args_list[0].args[0][0], 'curl')
        installer = run.call_args_list[1].args[0][1]
        self.assertFalse(installer.exists())
        with patch.object(bvt.shutil, 'which', return_value='/home/demo/.cargo/bin/rustup'), \
                patch.object(bvt, '_require_tools'), patch.object(bvt, '_run') as run:
            bvt._prepare_rust()
        run.assert_called_once_with([
            'rustup', 'toolchain', 'install', bvt.RUST_TOOLCHAIN, '--profile', 'minimal',
            '--target', 'x86_64-unknown-none', '--target', 'x86_64-unknown-linux-musl'])

    def test_checkout_pins_new_repository(self):
        repository = self.work / 'repo'
        with patch.object(bvt, '_run', return_value=bvt.DEPS_REVISION) as run, \
                patch.object(Path, 'exists', side_effect=[False, True]):
            bvt._checkout(repository, 'https://example.invalid/repo', bvt.DEPS_REVISION)
        self.assertEqual(run.call_args_list[0].args[0], [
            'git', 'clone', 'https://example.invalid/repo', repository])
        self.assertEqual(run.call_args_list[1].args[0], [
            'git', '-C', repository, 'checkout', '--detach', bvt.DEPS_REVISION])

    def test_existing_wrong_revision_is_not_reset(self):
        (self.work / '.git').mkdir()
        with patch.object(bvt, '_run', return_value='wrong') as run:
            with self.assertRaisesRegex(CLIError, 'Existing checkouts are not reset'):
                bvt._checkout(self.work, 'https://example.invalid/repo', bvt.DEPS_REVISION)
        self.assertEqual(run.call_count, 1)

    def test_missing_or_empty_input(self):
        missing = self.work / 'missing'
        for exists in (False, True):
            if exists:
                missing.touch()
            with self.assertRaisesRegex(CLIError, 'Missing or empty BVT input'):
                bvt._require_files([missing])

    def test_host_version_and_device_checks(self):
        with patch.object(bvt.platform, 'release', return_value=bvt.KERNEL_RELEASE), \
                patch.object(bvt, '_run', return_value='openvmm-' + bvt.OPENVMM_PACKAGE) as run, \
                patch.object(bvt, '_require_tools'), \
                patch.object(bvt.shutil, 'which', return_value='/usr/bin/openvmm'), \
                patch.object(Path, 'is_char_device', return_value=True), \
                patch.object(bvt.os, 'access', return_value=True):
            bvt._validate_host()
        run.assert_called_once_with(
            ['rpm', '-qf', '--qf', '%{NAME}-%{VERSION}-%{RELEASE}', '/usr/bin/openvmm'], capture=True)
        with patch.object(bvt.platform, 'release', return_value=bvt.KERNEL_RELEASE), \
                patch.object(bvt, '_run', return_value='wrong'), \
                patch.object(bvt, '_require_tools'), \
                patch.object(bvt.shutil, 'which', return_value='/usr/local/bin/openvmm'), \
                patch.object(Path, 'is_char_device', return_value=True), \
                patch.object(bvt.os, 'access', return_value=True):
            with self.assertRaisesRegex(CLIError, 'Expected packaged OpenVMM'):
                bvt._validate_host()
        with patch.object(bvt.platform, 'release', return_value=bvt.KERNEL_RELEASE), \
                patch.object(bvt, '_run', return_value=bvt.OPENVMM_PACKAGE), \
                patch.object(Path, 'is_char_device', return_value=False):
            with self.assertRaisesRegex(CLIError, '/dev/mshv'):
                bvt._validate_host()

    def test_setup_rejects_wrong_kernel_before_installing(self):
        with patch.object(bvt, '_require_platform'), \
                patch.object(bvt.platform, 'release', return_value='wrong'), \
                patch.object(bvt, '_install_tools') as install:
            with self.assertRaisesRegex(CLIError, 'validated L1 image'):
                bvt.setup_bvt(str(self.work))
        install.assert_not_called()

    def test_setup_checks_mshv_before_installing(self):
        with patch.object(bvt, '_require_platform'), \
                patch.object(bvt.platform, 'release', return_value=bvt.KERNEL_RELEASE), \
                patch.object(Path, 'is_char_device', return_value=False), \
                patch.object(bvt, '_install_tools') as install:
            with self.assertRaisesRegex(CLIError, '/dev/mshv'):
                bvt.setup_bvt(str(self.work))
        install.assert_not_called()

    def test_setup_orchestrates_documented_builds(self):
        scripts = self.source / 'src' / 'ace' / 'scripts'
        with patch.object(bvt, '_require_platform'), \
                patch.object(bvt.platform, 'release', return_value=bvt.KERNEL_RELEASE), \
                patch.object(bvt, '_install_tools'), patch.object(bvt, '_validate_host'), \
                patch.object(bvt, '_prepare_rust'), patch.object(bvt, '_checkout') as checkout, \
                patch.object(bvt, '_scripts', return_value=scripts), \
                patch.object(bvt, '_validate_inputs') as validate, \
                patch.object(bvt, '_run', return_value='checksums') as run:
            result = bvt.setup_bvt(str(self.work))
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(checkout.call_args_list, [
            call(self.source, bvt.ACE_URL, revision=bvt.ACE_REVISION, branch=bvt.ACE_BRANCH),
            call(self.work / 'openvmm', 'https://github.com/microsoft/openvmm.git', bvt.OPENVMM_REVISION),
            call(self.work / 'openvmm-deps', 'https://github.com/microsoft/openvmm-deps.git', bvt.DEPS_REVISION)])
        commands = [item.args[0] for item in run.call_args_list]
        self.assertIn(['cargo', 'xflowey', 'restore-packages'], commands)
        boot = next(item for item in run.call_args_list if 'snp_bootshim' in item.args[0])
        self.assertEqual(boot.kwargs['env']['MINIMAL_RT_BUILD'], '1')
        builds = [command for command in commands if command[:2] == ['docker', 'build']]
        self.assertEqual(len(builds), 2)
        self.assertIn('result-linux-snp-guest', builds[0])
        self.assertIn('result-initrd', builds[1])
        validate.assert_called_once_with(self.work, self.source)
        self.assertEqual((self.work / 'setup-inputs.sha256').read_text(encoding='utf-8'), 'checksums\n')

    def test_setup_accepts_local_source_without_cloning_it(self):
        with patch.object(bvt, '_require_platform'), \
                patch.object(bvt.platform, 'release', return_value=bvt.KERNEL_RELEASE), \
                patch.object(bvt, '_install_tools'), patch.object(bvt, '_validate_host'), \
                patch.object(bvt, '_prepare_rust'), patch.object(bvt, '_checkout') as checkout, \
                patch.object(bvt, '_scripts'), patch.object(bvt, '_validate_inputs'), \
                patch.object(bvt, '_run', return_value='checksums'):
            bvt.setup_bvt(str(self.work), str(self.source))
        self.assertEqual(checkout.call_count, 2)

    def test_validate_inputs_checks_patch_and_clean_dependency_tree(self):
        with patch.object(bvt, '_scripts'), patch.object(bvt, '_validate_revision'), \
                patch.object(bvt, '_require_files'), patch.object(bvt.os, 'access', return_value=True), \
                patch.object(bvt, '_run', return_value='') as run:
            bvt._validate_inputs(self.work, self.source)
        patch_command = run.call_args_list[1].args[0]
        self.assertIn('--reverse', patch_command)
        self.assertIn('--check', patch_command)
        with patch.object(bvt, '_scripts'), patch.object(bvt, '_validate_revision'), \
                patch.object(bvt, '_run', return_value=' M Dockerfile'):
            with self.assertRaisesRegex(CLIError, 'must be clean'):
                bvt._validate_inputs(self.work, self.source)

    def test_create_never_overwrites_existing_output(self):
        with patch.object(bvt, '_require_platform'), patch.object(bvt, '_run') as run:
            with self.assertRaisesRegex(CLIError, 'must not already exist'):
                bvt.create_bvt(str(self.work), output_dir=str(self.work))
        run.assert_not_called()

    def test_patched_tree_validation_preserves_git_index_and_rejects_extra_edits(self):
        repository = self.work / 'openvmm'
        repository.mkdir()
        bvt._run(['git', 'init', repository])
        bvt._run(['git', 'config', 'core.autocrlf', 'false'], cwd=repository)
        fixture = repository / 'fixture.txt'
        self.write(fixture, 'original\n')
        bvt._run(['git', 'add', 'fixture.txt'], cwd=repository)
        bvt._run(['git', '-c', 'core.hooksPath=' + str(repository / '.git' / 'no-hooks'),
                  '-c', 'commit.gpgsign=false', '-c', 'user.name=ACE Test', '-c', 'user.email=ace-test@example.invalid',
                  'commit', '-m', 'Test fixture\n\nCo-authored-by: Copilot '
                  '<223556219+Copilot@users.noreply.github.com>'], cwd=repository)
        self.write(fixture, 'patched\n')
        patch_file = self.source / 'src' / 'ace' / 'patches' / 'openvmm-igvmfilegen-measured-pcie.patch'
        self.write(patch_file, bvt._run(['git', 'diff'], cwd=repository, capture=True) + '\n')
        index = repository / '.git' / 'index'
        original_index = index.read_bytes()
        execute = bvt._run

        def run(args, **kwargs):
            if 'status' in args:
                return ''
            return execute(args, **kwargs)

        with patch.object(bvt, '_scripts'), patch.object(bvt, '_validate_revision'), \
                patch.object(bvt, '_require_files'), patch.object(bvt.os, 'access', return_value=True), \
                patch.object(bvt, '_run', side_effect=run):
            bvt._validate_inputs(self.work, self.source)
            self.assertEqual(index.read_bytes(), original_index)
            self.write(fixture, 'patched\nextra change\n')
            with self.assertRaises(CLIError):
                bvt._validate_inputs(self.work, self.source)
            self.assertEqual(index.read_bytes(), original_index)

    def test_create_revalidates_before_building(self):
        with patch.object(bvt, '_require_platform'), patch.object(bvt, '_require_tools'), \
                patch.object(bvt, '_validate_inputs', side_effect=CLIError('stale inputs')), \
                patch.object(bvt, '_run') as run:
            with self.assertRaisesRegex(CLIError, 'stale inputs'):
                bvt.create_bvt(str(self.work))
        run.assert_not_called()

    def test_create_checks_rust_target(self):
        with patch.object(bvt, '_require_platform'), patch.object(bvt, '_require_tools'), \
                patch.object(bvt, '_validate_inputs'), patch.object(bvt, '_run', return_value='wrong'):
            with self.assertRaisesRegex(CLIError, 'Missing Rust target'):
                bvt.create_bvt(str(self.work))

    def test_create_passes_exact_builder_inputs_and_verifies_bundle(self):
        output = self.work / 'bundle with spaces'
        scripts = self.source / 'src' / 'ace' / 'scripts'

        def execute(args, **kwargs):
            if args[0] == 'rustup':
                return 'x86_64-unknown-linux-musl'
            if args[0] == 'bash':
                for name in bvt.BUNDLE_FILES + ('identity-sidecar.json',):
                    self.write(output / name)
            return None

        with patch.object(bvt, '_require_platform'), patch.object(bvt, '_require_tools'), \
                patch.object(bvt, '_validate_inputs'), patch.object(bvt, '_scripts', return_value=scripts), \
                patch.object(bvt, '_run', side_effect=execute) as run:
            result = bvt.create_bvt(str(self.work), output_dir=str(output))
        self.assertEqual(result['status'], 'created')
        self.assertEqual(result['igvm'], str(output / 'skr-bvt.igvm'))
        self.assertIn(str(output / 'identity-sidecar.json'), result['artifacts'])
        builder = next(item for item in run.call_args_list if item.args[0][0] == 'bash')
        self.assertEqual(builder.args[0], [
            'bash', scripts / 'build-skr-bvt.sh', self.work / 'openvmm'] + bvt._inputs(self.work) + [output])
        self.assertEqual(run.call_args_list[-1], call(
            ['sha256sum', '--strict', '--check', 'SHA256SUMS'], cwd=output))

    def test_failed_build_or_checksum_is_not_reported_as_created(self):
        for failure in ('builder', 'checksum'):
            with self.subTest(failure=failure):
                output = self.work / failure

                def execute(args, **kwargs):
                    if args[0] == 'rustup':
                        return 'x86_64-unknown-linux-musl'
                    if args[0] == 'bash':
                        if failure == 'builder':
                            raise CLIError('builder failed')
                        for name in bvt.BUNDLE_FILES:
                            self.write(output / name)
                    if args[-1] == 'SHA256SUMS':
                        raise CLIError('checksum failed')
                    return None

                with patch.object(bvt, '_require_platform'), patch.object(bvt, '_require_tools'), \
                        patch.object(bvt, '_validate_inputs'), patch.object(bvt, '_scripts'), \
                        patch.object(bvt, '_run', side_effect=execute):
                    with self.assertRaisesRegex(CLIError, '{} failed'.format(failure)):
                        bvt.create_bvt(str(self.work), output_dir=str(output))

    def test_create_rejects_missing_or_changed_setup_inputs_before_building(self):
        with patch.object(bvt, '_require_platform'), patch.object(bvt, '_require_tools'), \
                patch.object(bvt, '_validate_inputs'), \
                patch.object(bvt, '_run', side_effect=CLIError('input checksum failed')) as run:
            with self.assertRaisesRegex(CLIError, 'input checksum failed'):
                bvt.create_bvt(str(self.work))
        run.assert_called_once_with(
            ['sha256sum', '--strict', '--check', 'setup-inputs.sha256'], cwd=self.work)
        (self.work / 'setup-inputs.sha256').unlink()
        with patch.object(bvt, '_require_platform'), patch.object(bvt, '_require_tools'), \
                patch.object(bvt, '_validate_inputs'), patch.object(bvt, '_run') as run:
            with self.assertRaisesRegex(CLIError, 'setup-inputs.sha256'):
                bvt.create_bvt(str(self.work))
        run.assert_not_called()

    def test_create_requires_map_identity_and_runner_outputs(self):
        for missing in ('skr-bvt.igvm.map', 'skr-bvt-snp.json', 'skr-bvt-snp.cbor',
                        'skr-bvt-snp.idblock', 'run-skr-bvt.sh', 'skr-bvt-client.py'):
            with self.subTest(missing=missing):
                output = self.work / missing

                def execute(args, **kwargs):
                    if args[0] == 'rustup':
                        return 'x86_64-unknown-linux-musl'
                    if args[0] == 'bash':
                        for name in bvt.BUNDLE_FILES:
                            if name != missing:
                                self.write(output / name)
                    return None

                with patch.object(bvt, '_require_platform'), patch.object(bvt, '_require_tools'), \
                        patch.object(bvt, '_validate_inputs'), patch.object(bvt, '_scripts'), \
                        patch.object(bvt, '_run', side_effect=execute):
                    with self.assertRaisesRegex(CLIError, 'Missing or empty BVT input'):
                        bvt.create_bvt(str(self.work), output_dir=str(output))

    def test_subprocess_failure_retains_exit_code(self):
        with patch.object(bvt.subprocess, 'run',
                          side_effect=subprocess.CalledProcessError(128, ['git'])):
            with self.assertRaisesRegex(CLIError, 'exit 128'):
                bvt._run(['git', 'clone', bvt.ACE_URL, self.work / 'repo'])
