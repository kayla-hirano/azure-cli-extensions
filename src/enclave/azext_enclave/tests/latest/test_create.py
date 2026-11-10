# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Exercise CLI image creation and the packaged builder without real SNP dependencies."""

import errno
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
from typing import Callable
import unittest
from unittest.mock import MagicMock, patch

from azure.cli.core import get_default_cli
from knack.util import CLIError

from azext_enclave import EnclaveCommandsLoader
from azext_enclave import _image_builder as builder
from azext_enclave import custom
from ._helpers import create_cli


class CreateCommandTest(unittest.TestCase):
    """Check CLI conversion, logging, result shape, and error translation."""

    def setUp(self) -> None:
        self.cmd = SimpleNamespace(cli_ctx=SimpleNamespace(data={'safe_params': []}))
        self.measurements = {'snp': 'AB' * 48}

    def test_default_delegation(self) -> None:
        image = Path('published.igvm').resolve()
        with patch.object(builder, 'build_igvm',
                          return_value=builder.IgvmBuildResult(image, self.measurements)) as build:
            result = custom.create_enclave(self.cmd, 'image.igvm', workspace='work', dependency_bundle='bundle')
        self.assertEqual(result, {'igvm': str(image), 'measurements': self.measurements})
        self.assertEqual(json.loads(json.dumps(result)), result)
        build.assert_called_once_with(
            dependency_bundle=Path('bundle'), application=custom.DEFAULT_APPLICATION,
            init_script=custom.DEPENDENCIES_DIR / 'initramfs' / 'enclave-init', workspace=Path('work'),
            output=Path('image.igvm'), signing_key=None, enable_debug=False,
            logger=custom.logger)

    def test_signing_and_workspace_override(self) -> None:
        with patch.object(builder, 'build_igvm',
                          return_value=builder.IgvmBuildResult(Path('signed.igvm'), self.measurements)) as build:
            custom.create_enclave(self.cmd, 'signed.igvm', workspace='work', dependency_bundle='bundle',
                                  init_script='init', signing_key='private.pem')
        self.assertEqual(build.call_args.kwargs['signing_key'], Path('private.pem'))
        self.assertEqual(build.call_args.kwargs['init_script'], Path('init'))
        self.assertEqual(build.call_args.kwargs['workspace'], Path('work'))
        self.assertIs(build.call_args.kwargs['enable_debug'], False)

    def test_global_debug_flag_through_cli_invocation(self) -> None:
        for flags, enable_debug in (
                ([], False), (['--verbose'], False), (['--debug'], True)):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as directory:
                cli = create_cli(directory)
                output = io.StringIO()
                packaged = Path(directory) / 'dependency-bundle.tar.gz'
                packaged.write_bytes(b'archive selection fixture')
                bundle_args = ['--dependency-bundle', 'bundle'] if flags else []
                with patch.object(custom, 'DEFAULT_DEPENDENCY_BUNDLE', packaged), \
                        patch.object(custom.logger, 'warning') as warning, \
                        patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                        Path('image.igvm'), self.measurements)) as build:
                    code = cli.invoke(
                        ['enclave', 'create', '--output-file', 'image.igvm',
                         '--output', 'json'] + bundle_args + flags,
                        out_file=output)
                self.assertEqual(code, 0, str(cli.result.error))
                self.assertEqual(json.loads(output.getvalue()),
                                 {'igvm': 'image.igvm', 'measurements': self.measurements})
                self.assertIs(build.call_args.kwargs['enable_debug'], enable_debug)
                self.assertIsNone(build.call_args.kwargs['workspace'])
                if enable_debug:
                    warning.assert_called_once_with(
                        'Debug mode is enabled. Secure Key Release (SKR) policies that require a non-debug enclave '
                        'will deny key release. For testing, ensure your SKR policy explicitly allows debug-enabled '
                        'enclaves; do not use debug mode in production.')
                else:
                    warning.assert_not_called()
                self.assertEqual(build.call_args.kwargs['dependency_bundle'],
                                 Path('bundle') if flags else packaged)
                self.assertEqual(build.call_args.kwargs['application'], custom.DEFAULT_APPLICATION)

    def test_build_errors_include_stage_and_residual_workspace(self) -> None:
        for workspace in (None, Path('residual')):
            with self.subTest(workspace=workspace):
                error = builder.IgvmBuildError('tool failed', stage='generate-igvm', workspace=workspace)
                with patch.object(builder, 'build_igvm', side_effect=error):
                    with self.assertRaises(CLIError) as raised:
                        custom.create_enclave(self.cmd, 'image.igvm', workspace='work', dependency_bundle='bundle')
                self.assertIn('generate-igvm: tool failed', str(raised.exception))
                self.assertIs(raised.exception.__cause__, error)
                self.assertEqual('Build cleanup incomplete; residual workspace:' in str(raised.exception),
                                 workspace is not None)

    def test_required_inputs_and_nonconflicting_options(self) -> None:
        cli = get_default_cli()
        cli.invocation = SimpleNamespace(data={'command_string': 'enclave create'})
        loader = EnclaveCommandsLoader(cli_ctx=cli)
        table = loader.load_command_table([])
        loader.load_arguments('enclave create')
        table['enclave create'].load_arguments()
        arguments = table['enclave create'].arguments
        required = {'output_file'}
        self.assertEqual({name for name, arg in arguments.items()
                          if arg.options.get('required') and name != 'cmd'}, required)
        self.assertEqual(arguments['output_file'].options_list, ['--output-file'])
        self.assertIsNone(arguments['workspace'].options['default'])
        self.assertIsNone(arguments['signing_key'].options['default'])
        self.assertIsNone(arguments['init_script'].options['default'])
        self.assertIsNone(arguments['dependency_bundle'].options['default'])
        self.assertEqual(arguments['application'].options_list, ['--application'])
        self.assertIsNone(arguments['application'].options['default'])
        default_init = custom.DEPENDENCIES_DIR / 'initramfs' / 'enclave-init'
        script = default_init.read_bytes()
        self.assertTrue(script.startswith(b'#!/bin/sh\n'))
        self.assertNotIn(b'\r', script)
        self.assertIn(b'echo "Starting enclave application"\n', script)
        self.assertIn(b'exec /bin/enclave_app serve-vsock 4050\n', script)
        with patch.object(builder, 'build_igvm',
                          return_value=builder.IgvmBuildResult(Path('image.igvm'), self.measurements)) as build:
            custom.create_enclave(self.cmd, 'image.igvm', workspace='work', dependency_bundle='bundle')
        self.assertEqual(build.call_args.kwargs['init_script'], default_init)

    def test_packaged_bundle_is_selected_when_override_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'dependency-bundle.tar.gz'
            archive.write_bytes(b'archive selection is validated by the builder')
            with patch.object(custom, 'DEFAULT_DEPENDENCY_BUNDLE', archive), \
                    patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                        Path('image.igvm'), self.measurements)) as build:
                custom.create_enclave(self.cmd, 'image.igvm', workspace='work')
            self.assertEqual(build.call_args.kwargs['dependency_bundle'], archive)

    def test_missing_packaged_bundle_has_actionable_override_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'dependency-bundle.tar.gz'
            with patch.object(custom, 'DEFAULT_DEPENDENCY_BUNDLE', archive), \
                    patch.object(builder, 'build_igvm') as build:
                with self.assertRaisesRegex(CLIError, 'missing.*--dependency-bundle'):
                    custom.create_enclave(self.cmd, 'image.igvm', workspace='work')
            build.assert_not_called()

    def test_real_packaged_inputs_and_default_selection(self) -> None:
        self.assertEqual(custom.DEFAULT_DEPENDENCY_BUNDLE.name, 'dependencies.tar.gz')
        self.assertTrue(tarfile.is_tarfile(custom.DEFAULT_DEPENDENCY_BUNDLE))
        builder._validate_application(custom.DEFAULT_APPLICATION)
        with tempfile.TemporaryDirectory() as directory:
            extracted = builder._extract_bundle(custom.DEFAULT_DEPENDENCY_BUNDLE, Path(directory) / 'bundle')
            dependencies = builder._dependencies(extracted.resolve())
            self.assertIn('igvmfilegen', dependencies)
        with patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                Path('image.igvm'), self.measurements)) as build:
            custom.create_enclave(self.cmd, 'image.igvm', workspace='work')
        self.assertEqual(build.call_args.kwargs['dependency_bundle'], custom.DEFAULT_DEPENDENCY_BUNDLE)
        self.assertEqual(build.call_args.kwargs['application'], custom.DEFAULT_APPLICATION)

    def test_application_override_through_cli_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cli = create_cli(directory)
            with patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                    Path('image.igvm'), self.measurements)) as build:
                code = cli.invoke(
                    ['enclave', 'create', '--workspace', 'work', '--output-file', 'image.igvm',
                     '--application', 'custom-app', '--init-script', 'custom-init'],
                    out_file=io.StringIO())
            self.assertEqual(code, 0, str(cli.result.error))
            self.assertEqual(build.call_args.kwargs['application'], Path('custom-app'))
            self.assertEqual(build.call_args.kwargs['init_script'], Path('custom-init'))

    def test_application_override_does_not_require_packaged_application(self) -> None:
        with patch.object(custom, 'DEFAULT_APPLICATION', Path('missing-packaged-app')), \
                patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                    Path('image.igvm'), self.measurements)) as build:
            custom.create_enclave(self.cmd, 'image.igvm', workspace='work', dependency_bundle='bundle',
                                  application='custom-app')
        self.assertEqual(build.call_args.kwargs['application'], Path('custom-app'))
        self.assertEqual(build.call_args.kwargs['init_script'],
                         custom.DEPENDENCIES_DIR / 'initramfs' / 'enclave-init')

    def test_archive_override_is_forwarded_without_requiring_packaged_bundle(self) -> None:
        with patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                Path('image.igvm'), self.measurements)) as build:
            custom.create_enclave(self.cmd, 'image.igvm', workspace='work', dependency_bundle='custom.tar.gz')
        self.assertEqual(build.call_args.kwargs['dependency_bundle'], Path('custom.tar.gz'))


class ArchiveExtractionTest(unittest.TestCase):
    """Reject archive metadata that could write outside the private workspace."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive_path = self.root / 'bundle.tar.gz'
        self.destination = self.root / 'extracted'

    def write_archive(self, members: list) -> None:
        with tarfile.open(self.archive_path, 'w:gz') as archive:
            for name, kind in members:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.mode = 0o4755
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = '../outside'
                data = b'{}'
                member.size = len(data) if kind == tarfile.REGTYPE else 0
                archive.addfile(member, io.BytesIO(data) if member.size else None)

    def test_rejects_escaping_and_platform_specific_paths(self) -> None:
        for name in ('../outside', '/outside', 'nested/../../outside',
                     r'..\outside', 'C:/outside', r'C:\outside'):
            with self.subTest(name=name):
                self.write_archive([(name, tarfile.REGTYPE)])
                with self.assertRaisesRegex(ValueError, 'Unsafe dependency archive path'):
                    builder._extract_bundle(self.archive_path, self.destination)
                self.assertFalse((self.root / 'outside').exists())
                self.destination.rmdir()

    def test_rejects_links_and_special_files(self) -> None:
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE,
                     tarfile.CHRTYPE, tarfile.BLKTYPE):
            with self.subTest(kind=kind):
                self.write_archive([('unsafe', kind)])
                with self.assertRaisesRegex(ValueError, 'only regular files and directories'):
                    builder._extract_bundle(self.archive_path, self.destination)
                self.destination.rmdir()

    def test_rejects_duplicate_files_without_overwriting(self) -> None:
        self.write_archive([('same', tarfile.REGTYPE), ('same', tarfile.REGTYPE)])
        with self.assertRaises(FileExistsError):
            builder._extract_bundle(self.archive_path, self.destination)
        self.assertEqual((self.destination / 'same').read_bytes(), b'{}')

    def test_requires_a_manifest_at_supported_archive_root(self) -> None:
        self.write_archive([('other', tarfile.REGTYPE)])
        with self.assertRaisesRegex(ValueError, 'must contain dependency-manifest.v1.json'):
            builder._extract_bundle(self.archive_path, self.destination)

    @unittest.skipUnless(os.name == 'posix', 'Unix executable and setuid mode semantics')
    def test_preserves_executability_but_discards_privileged_bits(self) -> None:
        self.write_archive([('dependency-manifest.v1.json', tarfile.REGTYPE),
                            ('igvmfilegen', tarfile.REGTYPE)])
        builder._extract_bundle(self.archive_path, self.destination)
        self.assertEqual((self.destination / 'igvmfilegen').stat().st_mode & 0o7777, 0o755)


class BuildInputs:
    """Create isolated build inputs without installing mocks or owning test cleanup."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.archive_prefix = ''
        self.application = self.root / 'application'
        self.init = self.root / 'init'
        self.init.write_bytes(b'#!/bin/sh\nexec /bin/enclave_app serve-vsock 4050\n')
        self.key = self.root / 'private.pem'
        self.key.write_bytes(b'caller-owned-test-key')
        header = struct.pack(
            '<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + b'\0' * 9,
            2, 62, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
        program = struct.pack('<IIQQQQQQ', 1, 5, 0, 0, 0, 120, 120, 4096)
        self.application.write_bytes(header + program)
        self.manifest = {
            'schema_version': 1, 'profile': 'measured-pcie-v1', 'guest_arch': 'x86_64',
            'artifacts': {},
        }
        config = {
            'guest_arch': 'x64',
            'guest_configs': [{
                'isolation_type': {'snp': {'enable_debug': True}},
                'image': {'snp_linux_direct': {'pcie_host_bridge': {'segment': 0}}},
            }],
        }
        self.measurement = 'AB' * 48
        self.measurement_document = {'series': [{'reference': {'snp_ld': self.measurement}}]}
        for name, data in (
                ('kernel', b'kernel'), ('base_initrd', b'base-initrd'),
                ('snp_bootshim', b'bootshim'), ('igvmfilegen', b'generator'),
                ('igvm_manifest', json.dumps(config).encode('utf-8'))):
            artifact = self.bundle / name
            artifact.write_bytes(data)
            artifact.chmod(0o755 if name == 'igvmfilegen' else 0o644)
            self.manifest['artifacts'][name] = {
                'path': name, 'sha256': hashlib.sha256(data).hexdigest(),
            }
        self.write_manifest()
        self.workspace = self.root / 'work'
        self.output = self.root / 'images' / 'app.igvm'

    def write_manifest(self) -> None:
        (self.bundle / 'dependency-manifest.v1.json').write_text(
            json.dumps(self.manifest), encoding='utf-8')

    def build(self, **kwargs) -> builder.IgvmBuildResult:
        workspace = kwargs.pop('workspace', self.workspace)
        return builder.build_igvm(
            dependency_bundle=self.bundle, application=self.application,
            init_script=self.init, workspace=workspace, output=self.output, **kwargs)


class ImageBuilderTest(unittest.TestCase):
    """Use real files and ELF/manifest validation, replacing only external build commands."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.inputs = BuildInputs(Path(temporary.name).resolve())
        self.stages = []
        patchers = [
            patch.object(builder.sys, 'platform', 'linux'),
            patch.object(builder.shutil, 'which', return_value='/usr/bin/tool'),
            patch.dict(os.environ, {'SOURCE_DATE_EPOCH': '123'}),
        ]
        if os.utime not in os.supports_follow_symlinks:
            native_utime = os.utime
            patchers.append(patch.object(
                builder.os, 'utime', side_effect=lambda path, times, **kwargs: native_utime(path, times)))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        run_patcher = patch.object(builder, '_run', side_effect=self.run_stage)
        self.run = run_patcher.start()
        self.addCleanup(run_patcher.stop)

    def run_stage(self, argv, stage, work, **kwargs) -> None:
        self.stages.append(stage)
        (work / (stage + '.log')).write_text(stage, encoding='utf-8')
        if stage == 'unpack-gzip':
            kwargs['stdout'].write(b'base cpio')
        elif stage == 'unpack-cpio':
            root = kwargs['cwd']
            (root / 'bin').mkdir()
            (root / 'bin' / 'appliance').write_bytes(b'legacy')
            (root / 'appliance').write_bytes(b'legacy')
            (root / 'base-file').write_bytes(b'preserved')
        elif stage in ('pack-cpio', 'pack-gzip'):
            kwargs['stdout'].write(b'composed initrd')
        elif stage == 'generate-igvm':
            resources = json.loads((work / 'resources.json').read_text(encoding='utf-8'))['resources']
            dependency_root = (self.inputs.bundle if self.inputs.bundle.is_dir()
                               else work / 'dependencies' / self.inputs.archive_prefix)
            self.assertEqual(resources, {
                'linux_kernel': str(dependency_root / 'kernel'),
                'linux_initrd': str(work / 'application-initrd.cpio.gz'),
                'snp_bootshim': str(dependency_root / 'snp_bootshim'),
            })
            (work / 'image.igvm').write_bytes(b'unsigned image')
            (work / 'image-snp.idblock').write_bytes(b'id block')
            (work / 'image-snp.json').write_text(json.dumps(self.inputs.measurement_document), encoding='utf-8')
        elif stage == 'export-id-public-key':
            Path(argv[argv.index('-out') + 1]).write_bytes(b'public key')
        elif stage == 'sign-id-block':
            Path(argv[argv.index('-out') + 1]).write_bytes(b'signature')
        elif stage == 'add-snp-id-block':
            Path(argv[argv.index('--output') + 1]).write_bytes(b'signed image')
        else:
            self.fail('Unexpected stage: ' + stage)

    def inspect_stage(self, target: str, check: Callable[[Path], None]) -> MagicMock:
        """Inspect files at an external-command boundary without changing cleanup behavior."""
        inspection = MagicMock(side_effect=check)

        def run(argv, stage, work, **kwargs):
            self.run_stage(argv, stage, work, **kwargs)
            if stage == target:
                inspection(work)

        self.run.side_effect = run
        return inspection

    def test_unsigned_pipeline_and_cleanup(self) -> None:
        result = self.inputs.build()
        self.assertEqual(result.igvm, self.inputs.output)
        self.assertEqual(result.measurements, {'snp': self.inputs.measurement})
        self.assertEqual(self.inputs.output.read_bytes(), b'unsigned image')
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])
        self.assertEqual(list(self.inputs.output.parent.iterdir()), [self.inputs.output])
        self.assertEqual(self.stages, [
            'unpack-gzip', 'unpack-cpio', 'pack-cpio', 'pack-gzip', 'generate-igvm'])
        self.assertEqual(self.inputs.application.read_bytes()[:4], b'\x7fELF')
        self.assertEqual((self.inputs.bundle / 'base_initrd').read_bytes(), b'base-initrd')

    def test_default_workspace_is_private_and_cleaned(self) -> None:
        parent = self.inputs.root / 'temporary'
        parent.mkdir()

        def inspect(work):
            self.assertEqual(work.parent, parent)
            self.assertTrue(work.name.startswith('.enclave-build-'))
            if os.name == 'posix':
                self.assertEqual(work.stat().st_mode & 0o777, 0o700)
            self.assertTrue((work / 'image.igvm').is_file())

        inspection = self.inspect_stage('generate-igvm', inspect)
        with patch.dict(os.environ, {'TMPDIR': str(parent)}), patch.object(tempfile, 'tempdir', None):
            self.inputs.build(workspace=None)
        inspection.assert_called_once()
        self.assertEqual(list(parent.iterdir()), [])
        self.assertEqual(list(self.inputs.output.parent.iterdir()), [self.inputs.output])

    def test_publication_failure_cleans_staging_and_workspace(self) -> None:
        with patch.object(builder.os, 'link', side_effect=OSError('publication failed')):
            with self.assertRaisesRegex(builder.IgvmBuildError, 'publication failed') as raised:
                self.inputs.build()
        self.assertEqual(raised.exception.stage, 'publish')
        self.assertIsNone(raised.exception.workspace)
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])
        self.assertEqual(list(self.inputs.output.parent.iterdir()), [])

    def test_staging_cleanup_failure_after_publication_is_reported(self) -> None:
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path.name.startswith('.igvm-'):
                raise OSError('staging removal denied')
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', autospec=True, side_effect=unlink):
            with self.assertRaisesRegex(builder.IgvmBuildError, 'staging removal denied') as raised:
                self.inputs.build()
        self.assertEqual(raised.exception.stage, 'cleanup')
        self.assertIsNone(raised.exception.workspace)
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])
        self.assertTrue(self.inputs.output.is_file())
        staging, = (path for path in self.inputs.output.parent.iterdir() if path != self.inputs.output)
        self.assertTrue(staging.name.startswith('.igvm-'))
        self.assertIn(str(staging), str(raised.exception))

    def test_default_temp_location_and_explicit_parent_have_same_results(self) -> None:
        explicit = self.inputs.build()
        image = self.inputs.output.read_bytes()
        self.inputs.output.unlink()
        parent = self.inputs.root / 'temporary'
        parent.mkdir()
        unrelated = parent / 'unrelated'
        unrelated.write_bytes(b'preserved')

        with patch.dict(os.environ, {'TMPDIR': str(parent)}), patch.object(tempfile, 'tempdir', None):
            default = self.inputs.build(workspace=None)
        self.assertEqual(default, explicit)
        self.assertEqual(self.inputs.output.read_bytes(), image)
        self.assertEqual(list(parent.iterdir()), [unrelated])
        self.assertEqual(unrelated.read_bytes(), b'preserved')

    def test_overlapping_builds_use_separate_private_directories(self) -> None:
        active = []
        second_output = self.inputs.root / 'second.igvm'

        def run(argv, stage, work, **kwargs):
            self.run_stage(argv, stage, work, **kwargs)
            if stage == 'generate-igvm' and not active:
                active.append(work)
                builder.build_igvm(
                    dependency_bundle=self.inputs.bundle, application=self.inputs.application, init_script=self.inputs.init,
                    workspace=self.inputs.workspace, output=second_output)
                self.assertTrue(work.is_dir())
                self.assertEqual(list(self.inputs.workspace.iterdir()), [work])
            elif stage == 'generate-igvm':
                self.assertNotEqual(work, active[0])
                self.assertTrue(active[0].is_dir())

        self.run.side_effect = run
        self.inputs.build()
        self.assertEqual(second_output.read_bytes(), self.inputs.output.read_bytes())
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])

    def test_storage_and_execution_errors_suggest_workspace_and_cleanup(self) -> None:
        for code in (errno.ENOSPC, errno.EACCES, errno.ENOEXEC):
            with self.subTest(code=code):
                self.run.side_effect = OSError(code, 'build storage unavailable')
                with self.assertRaisesRegex(builder.IgvmBuildError, '--workspace'):
                    self.inputs.build()
                self.assertEqual(list(self.inputs.workspace.iterdir()), [])
                self.assertFalse(self.inputs.output.exists())

    def test_workspace_paths_use_debug_logging_only(self) -> None:
        logger = MagicMock()
        self.inputs.build(logger=logger)
        work = logger.debug.call_args.args[1]
        logger.debug.assert_called_once_with('Build workspace: %s', work)
        self.assertFalse(any(str(work) in str(call) for call in logger.info.call_args_list))
        logger.warning.assert_not_called()
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])

    def test_cleanup_failure_reports_residual_workspace_and_preserves_stage_error(self) -> None:
        self.run.side_effect = builder.IgvmBuildError('generator failed', stage='generate-igvm')
        with patch.object(builder.shutil, 'rmtree', side_effect=OSError('removal denied')):
            with self.assertRaises(builder.IgvmBuildError) as raised:
                self.inputs.build()
        error = raised.exception
        self.assertEqual(error.stage, 'generate-igvm')
        self.assertIn('generator failed', str(error))
        self.assertIn('Cleanup failed', str(error))
        self.assertIn('removal denied', str(error))
        self.assertEqual(error.workspace.parent, self.inputs.workspace)
        self.assertTrue(error.workspace.is_dir())
        self.assertFalse(self.inputs.output.exists())

    def test_cleanup_failure_prevents_publication(self) -> None:
        with patch.object(builder.shutil, 'rmtree', side_effect=OSError('removal denied')):
            with self.assertRaises(builder.IgvmBuildError) as raised:
                self.inputs.build()
        self.assertEqual(raised.exception.stage, 'cleanup')
        self.assertTrue(raised.exception.workspace.is_dir())
        self.assertFalse(self.inputs.output.exists())
        self.assertEqual(list(self.inputs.output.parent.iterdir()), [])

    def test_interrupted_build_cleans_workspace(self) -> None:
        self.run.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.inputs.build()
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])
        self.assertFalse(self.inputs.output.exists())

    def test_staging_unlink_failure_does_not_skip_workspace_cleanup(self) -> None:
        original_unlink = Path.unlink
        original_chmod = Path.chmod

        def chmod(path, *args, **kwargs):
            if path.name.startswith('.igvm-'):
                raise OSError('publication failed')
            return original_chmod(path, *args, **kwargs)

        def unlink(path, *args, **kwargs):
            if path.name.startswith('.igvm-'):
                raise OSError('staging removal denied')
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, 'chmod', autospec=True, side_effect=chmod), \
                patch.object(Path, 'unlink', autospec=True, side_effect=unlink):
            with self.assertRaises(builder.IgvmBuildError) as raised:
                self.inputs.build()
        self.assertIn('publication failed', str(raised.exception))
        self.assertIn('staging removal denied', str(raised.exception))
        self.assertIn(str(self.inputs.output.parent), str(raised.exception))
        self.assertIsNone(raised.exception.workspace)
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])
        self.assertFalse(self.inputs.output.exists())

    def test_archive_bundles_build_and_cleanup(self) -> None:
        directory = self.inputs.bundle
        for prefix in ('', 'bundle'):
            with self.subTest(prefix=prefix):
                archive_path = self.inputs.root / 'dependencies.tar.gz'
                with tarfile.open(archive_path, 'w:gz') as archive:
                    archive.add(directory, arcname=prefix or '.')
                self.inputs.bundle = archive_path
                self.inputs.archive_prefix = prefix

                def inspect(work):
                    manifest = work / 'dependencies' / prefix / 'dependency-manifest.v1.json'
                    self.assertTrue(manifest.is_file())

                inspection = self.inspect_stage('generate-igvm', inspect)
                result = self.inputs.build()
                inspection.assert_called_once()
                self.assertEqual(result.measurements, {'snp': self.inputs.measurement})
                self.assertEqual(result.igvm, self.inputs.output)
                self.assertEqual(list(self.inputs.workspace.iterdir()), [])
                self.inputs.output.unlink()

    def test_corrupt_archive_fails_before_build_and_cleans_workspace(self) -> None:
        archive_path = self.inputs.root / 'broken.tar.gz'
        archive_path.write_bytes(b'not an archive')
        self.inputs.bundle = archive_path
        with self.assertRaises(builder.IgvmBuildError) as raised:
            self.inputs.build()
        self.assertEqual(raised.exception.stage, 'extract-dependencies')
        self.assertFalse(self.inputs.output.exists())
        self.assertIsNone(raised.exception.workspace)
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])
        self.run.assert_not_called()

    def test_archive_artifact_checksums_are_still_required(self) -> None:
        (self.inputs.bundle / 'kernel').write_bytes(b'tampered kernel')
        archive_path = self.inputs.root / 'tampered.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            archive.add(self.inputs.bundle, arcname='.')
        self.inputs.bundle = archive_path
        with self.assertRaisesRegex(builder.IgvmBuildError, 'validate-dependencies:.*checksum mismatch'):
            self.inputs.build()
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])
        self.run.assert_not_called()

    def test_composed_rootfs_and_normalized_metadata(self) -> None:
        def inspect(work):
            root = work / 'rootfs'
            self.assertEqual((root / 'bin' / 'enclave_app').read_bytes(), self.inputs.application.read_bytes())
            self.assertEqual((root / 'init').read_bytes(), self.inputs.init.read_bytes())
            self.assertEqual((root / 'base-file').read_bytes(), b'preserved')
            self.assertFalse((root / 'bin' / 'appliance').exists())
            self.assertFalse((root / 'appliance').exists())
            self.assertEqual(int((root / 'init').stat().st_mtime), 123)
            paths = (work / 'archive-files').read_bytes().split(b'\0')[:-1]
            self.assertEqual(paths, sorted(paths))
            self.assertTrue((work / 'generate-igvm.log').is_file())

        inspection = self.inspect_stage('generate-igvm', inspect)
        self.inputs.build()
        inspection.assert_called_once()
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])

    def test_signed_image_only_and_private_key_not_copied(self) -> None:
        def inspect(work):
            self.assertEqual((work / 'id-public-key.pem').read_bytes(), b'public key')
            self.assertFalse(any(path.is_file() and path.read_bytes() == self.inputs.key.read_bytes()
                                 for path in work.rglob('*')))

        inspection = self.inspect_stage('add-snp-id-block', inspect)
        result = self.inputs.build(signing_key=self.inputs.key)
        inspection.assert_called_once()
        self.assertEqual(result.measurements, {'snp': self.inputs.measurement})
        self.assertEqual(self.inputs.output.read_bytes(), b'signed image')
        self.assertEqual(self.stages[-3:], [
            'export-id-public-key', 'sign-id-block', 'add-snp-id-block'])
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])
        self.assertEqual(self.inputs.key.read_bytes(), b'caller-owned-test-key')

    def test_guest_debug_is_independent_of_cleanup_and_bundle(self) -> None:
        original = (self.inputs.bundle / 'igvm_manifest').read_bytes()
        for enable_debug in (False, True):
            with self.subTest(enable_debug=enable_debug):
                def inspect(work):
                    config = json.loads((work / 'igvm-manifest.json').read_text(encoding='utf-8'))
                    self.assertIs(config['guest_configs'][0]['isolation_type']['snp']['enable_debug'], enable_debug)

                inspection = self.inspect_stage('generate-igvm', inspect)
                result = self.inputs.build(enable_debug=enable_debug)
                inspection.assert_called_once()
                self.assertEqual(list(self.inputs.workspace.iterdir()), [])
                self.assertEqual((self.inputs.bundle / 'igvm_manifest').read_bytes(), original)
                self.assertEqual(result.measurements, {'snp': self.inputs.measurement})
                self.inputs.output.unlink()

    def test_default_disables_guest_debug_from_bundle(self) -> None:
        def inspect(work):
            config = json.loads((work / 'igvm-manifest.json').read_text(encoding='utf-8'))
            self.assertIs(config['guest_configs'][0]['isolation_type']['snp']['enable_debug'], False)

        inspection = self.inspect_stage('generate-igvm', inspect)
        self.inputs.build()
        inspection.assert_called_once()
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])

    def test_invalid_measurements_fail_before_publication(self) -> None:
        for document in (
                {}, {'series': []}, {'series': [None]}, {'series': [{}, {}]},
                {'series': [{'reference': None}]},
                {'series': [{'reference': {'snp_ld': None}}]},
                {'series': [{'reference': {'snp_ld': 'AB' * 47}}]},
                {'series': [{'reference': {'snp_ld': 'ZZ' * 48}}]},
                {'series': [{'reference': {'snp_ld': 'AB' * 47 + '  '}}]}):
            with self.subTest(document=document):
                self.inputs.measurement_document = document
                with self.assertRaises(builder.IgvmBuildError) as raised:
                    self.inputs.build()
                self.assertEqual(raised.exception.stage, 'read-measurements')
                self.assertFalse(self.inputs.output.exists())
                self.assertEqual(list(self.inputs.workspace.iterdir()), [])

    def test_missing_or_invalid_json_measurements_cleanup(self) -> None:
        for data in (None, b'{invalid json', b'[]'):
            with self.subTest(data=data):
                def generate(argv, stage, work, **kwargs) -> None:
                    self.run_stage(argv, stage, work, **kwargs)
                    if stage == 'generate-igvm':
                        sidecar = work / 'image-snp.json'
                        if data is None:
                            sidecar.unlink()
                        else:
                            sidecar.write_bytes(data)

                self.run.side_effect = generate
                with self.assertRaises(builder.IgvmBuildError) as raised:
                    self.inputs.build()
                self.assertEqual(raised.exception.stage, 'read-measurements')
                self.assertIsNone(raised.exception.workspace)
                self.assertEqual(list(self.inputs.workspace.iterdir()), [])
                self.assertFalse(self.inputs.output.exists())

    def test_failure_cleanup_with_guest_debug(self) -> None:
        for enable_debug in (False, True):
            with self.subTest(enable_debug=enable_debug):
                self.run.side_effect = builder.IgvmBuildError('generator failed', stage='generate-igvm')
                with self.assertRaises(builder.IgvmBuildError) as raised:
                    self.inputs.build(enable_debug=enable_debug)
                self.assertFalse(self.inputs.output.exists())
                self.assertEqual(raised.exception.stage, 'generate-igvm')
                self.assertIsNone(raised.exception.workspace)
                self.assertEqual(list(self.inputs.workspace.iterdir()), [])

    def test_signing_failure_never_publishes_unsigned_image(self) -> None:
        def fail_signing(argv, stage, work, **kwargs) -> None:
            if stage == 'sign-id-block':
                raise builder.IgvmBuildError('signing failed', stage=stage)
            self.run_stage(argv, stage, work, **kwargs)

        self.run.side_effect = fail_signing
        with self.assertRaisesRegex(builder.IgvmBuildError, 'sign-id-block: signing failed'):
            self.inputs.build(signing_key=self.inputs.key)
        self.assertFalse(self.inputs.output.exists())
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])

    def test_existing_output_is_preserved(self) -> None:
        self.inputs.output.parent.mkdir()
        self.inputs.output.write_bytes(b'existing')
        with self.assertRaisesRegex(builder.IgvmBuildError, 'Output already exists'):
            self.inputs.build()
        self.assertEqual(self.inputs.output.read_bytes(), b'existing')
        self.run.assert_not_called()

    def test_checksums_and_artifact_paths_are_validated(self) -> None:
        (self.inputs.bundle / 'kernel').write_bytes(b'modified')
        with self.assertRaisesRegex(builder.IgvmBuildError, 'checksum mismatch: kernel'):
            self.inputs.build()
        self.inputs.manifest['artifacts']['kernel']['path'] = '../application'
        self.inputs.write_manifest()
        with self.assertRaisesRegex(builder.IgvmBuildError, 'must be bundle-relative'):
            self.inputs.build()
        self.run.assert_not_called()

    def test_profile_and_measured_pcie_are_required(self) -> None:
        self.inputs.manifest['profile'] = 'other'
        self.inputs.write_manifest()
        with self.assertRaisesRegex(builder.IgvmBuildError, 'measured-pcie-v1'):
            self.inputs.build()
        self.inputs.manifest['profile'] = 'measured-pcie-v1'
        config = self.inputs.bundle / 'igvm_manifest'
        config.write_bytes(b'{"guest_arch":"x64","guest_configs":[{"image":{"snp_linux_direct":{}}}]}')
        self.inputs.manifest['artifacts']['igvm_manifest']['sha256'] = hashlib.sha256(config.read_bytes()).hexdigest()
        self.inputs.write_manifest()
        with self.assertRaisesRegex(builder.IgvmBuildError, 'missing measured PCIe'):
            self.inputs.build()
        self.run.assert_not_called()

    def test_schema_version_requires_integer_one(self) -> None:
        for version in (None, True, False, 1.0, 0, 2, '1', {}, []):
            with self.subTest(version=version):
                self.inputs.manifest['schema_version'] = version
                self.inputs.write_manifest()
                with self.assertRaisesRegex(builder.IgvmBuildError, 'Expected schema 1'):
                    self.inputs.build()
        self.run.assert_not_called()

    def test_epoch_validation_precedes_workspace_creation(self) -> None:
        for epoch in ('-1', '4294967296', 'not-an-integer'):
            with self.subTest(epoch=epoch), patch.dict(os.environ, {'SOURCE_DATE_EPOCH': epoch}):
                with self.assertRaises(builder.IgvmBuildError):
                    self.inputs.build()
                self.assertFalse(self.inputs.workspace.exists())
        self.run.assert_not_called()

    def test_application_requires_static_x64_elf(self) -> None:
        original = self.inputs.application.read_bytes()
        for data, message in (
                (b'not an executable', 'ELF64'),
                (original[:64] + struct.pack('<IIQQQQQQ', 3, 0, 0, 0, 0, 0, 0, 0), 'PT_INTERP')):
            with self.subTest(message=message):
                self.inputs.application.write_bytes(data)
                with self.assertRaisesRegex(builder.IgvmBuildError, message):
                    self.inputs.build()
        self.run.assert_not_called()

    def test_application_override_validation_never_falls_back_to_packaged_app(self) -> None:
        cmd = SimpleNamespace(cli_ctx=SimpleNamespace(data={'safe_params': []}))
        self.inputs.application.write_bytes(b'not an executable')
        for application in (self.inputs.application, self.inputs.root / 'missing-app'):
            with self.subTest(application=application):
                with self.assertRaisesRegex(CLIError, 'Unable to create Enclave image: validate:'):
                    custom.create_enclave(
                        cmd, str(self.inputs.output), workspace=str(self.inputs.workspace), dependency_bundle=str(self.inputs.bundle),
                        application=str(application), init_script=str(self.inputs.init))
                self.assertFalse(self.inputs.output.exists())
        self.run.assert_not_called()

    def test_init_requires_shebang_and_unix_line_endings(self) -> None:
        for data in (b'exec /bin/enclave_app\n', b'#!/bin/sh\r\nexec /bin/enclave_app\r\n'):
            with self.subTest(data=data):
                self.inputs.init.write_bytes(data)
                with self.assertRaisesRegex(builder.IgvmBuildError, 'shebang script with LF'):
                    self.inputs.build()
        self.run.assert_not_called()

    def test_protected_bundle_and_missing_tools(self) -> None:
        self.inputs.workspace = self.inputs.bundle / 'work'
        with self.assertRaisesRegex(builder.IgvmBuildError, 'overlaps a protected input'):
            self.inputs.build()
        self.inputs.workspace = self.inputs.root / 'work'
        with patch.object(builder.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(builder.IgvmBuildError, 'Required command not found: cpio'):
                self.inputs.build()
        self.run.assert_not_called()

    def test_non_linux_failure_is_a_cli_error(self) -> None:
        with patch.object(builder.sys, 'platform', 'win32'):
            with self.assertRaisesRegex(CLIError, 'validate: IGVM packaging requires Linux'):
                cmd = SimpleNamespace(cli_ctx=SimpleNamespace(data={'safe_params': []}))
                custom.create_enclave(cmd, str(self.inputs.output), workspace=str(self.inputs.workspace),
                                      dependency_bundle=str(self.inputs.bundle), init_script=str(self.inputs.init))
        self.run.assert_not_called()


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('cpio') and shutil.which('gzip'),
                     'Real initrd packaging requires Linux, GNU cpio, and gzip')
class LinuxPackagingTest(unittest.TestCase):
    """Exercise real archive tools and subprocess execution with a stand-in generator."""

    def test_real_packaging_and_generator_execution(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = BuildInputs(Path(temporary.name).resolve())
        root = fixture.root / 'base'
        (root / 'bin').mkdir(parents=True)
        (root / 'bin' / 'appliance').write_bytes(b'legacy')
        archive = subprocess.run(
            ['cpio', '--null', '--create', '--format=newc', '--quiet'],
            input=b'.\0./bin\0./bin/appliance\0', cwd=root, capture_output=True, check=True).stdout
        compressed = subprocess.run(
            ['gzip', '-n', '-c'], input=archive, capture_output=True, check=True).stdout
        (fixture.bundle / 'base_initrd').write_bytes(compressed)
        generator = fixture.bundle / 'igvmfilegen'
        generator.write_text(
            '#!/usr/bin/env python3\n'
            'from pathlib import Path\n'
            'import json\n'
            'import sys\n'
            'image = Path(sys.argv[sys.argv.index("--output") + 1])\n'
            'image.write_bytes(b"test IGVM")\n'
            'image.with_name(image.stem + "-snp.json").write_text(json.dumps({\n'
            '    "series": [{"reference": {"snp_ld": "AB" * 48}}]}))\n',
            encoding='utf-8')
        generator.chmod(0o755)
        for name in ('base_initrd', 'igvmfilegen'):
            fixture.manifest['artifacts'][name]['sha256'] = hashlib.sha256(
                (fixture.bundle / name).read_bytes()).hexdigest()
        fixture.write_manifest()
        result = fixture.build()
        self.assertEqual(result.igvm, fixture.output)
        self.assertEqual(result.measurements, {'snp': fixture.measurement})
        self.assertEqual(fixture.output.read_bytes(), b'test IGVM')
        self.assertEqual(list(fixture.workspace.iterdir()), [])
