# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Exercise CLI image creation and the packaged builder without real SNP dependencies."""

import errno
from dataclasses import replace
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
from azext_enclave._isolation import load_isolation
from ._helpers import create_cli


class CreateCommandTest(unittest.TestCase):
    """Check CLI conversion, logging, result shape, and error translation."""

    def setUp(self) -> None:
        self.cmd = SimpleNamespace(cli_ctx=SimpleNamespace(data={'safe_params': []}))
        self.measurements = {'snp': 'AB' * 48}

    def test_default_delegation(self) -> None:
        """Map CLI defaults to packaged inputs and normalized isolation options while preserving the result shape."""
        image = Path('published.igvm').resolve()
        with patch.object(builder, 'build_igvm',
                          return_value=builder.IgvmBuildResult(image, self.measurements)) as build, \
                patch.object(builder, 'validate_build_configuration',
                             wraps=builder.validate_build_configuration) as validate:
            result = custom.create_enclave(self.cmd, 'image.igvm', workspace='work', dependency_bundle='bundle')
        self.assertEqual(result, {'igvm': str(image), 'measurements': self.measurements})
        self.assertEqual(json.loads(json.dumps(result)), result)
        validate.assert_called_once_with(load_isolation(), isolation_options=None, enable_guest_debug=False)
        build.assert_called_once_with(
            dependency_bundle=Path('bundle'), application=custom.DEFAULT_APPLICATION,
            init_script=custom.DEPENDENCIES_DIR / 'initramfs' / 'enclave-init', workspace=Path('work'),
            output=Path('image.igvm'), keep_intermediates=False, enable_guest_debug=False,
            isolation_options=builder.SnpBuildOptions(), logger=custom.logger, configuration=load_isolation())

    def test_signing_and_workspace_override(self) -> None:
        """Forward caller signing, init-script, and workspace choices without enabling guest debugging."""
        with patch.object(builder, 'build_igvm',
                          return_value=builder.IgvmBuildResult(Path('signed.igvm'), self.measurements)) as build:
            custom.create_enclave(self.cmd, 'signed.igvm', workspace='work', dependency_bundle='bundle',
                                  init_script='init', signing_key='private.pem')
        self.assertEqual(build.call_args.kwargs['isolation_options'].signing_key, Path('private.pem'))
        self.assertEqual(build.call_args.kwargs['init_script'], Path('init'))
        self.assertEqual(build.call_args.kwargs['workspace'], Path('work'))
        self.assertIs(build.call_args.kwargs['enable_guest_debug'], False)

    def test_global_debug_flag_through_cli_invocation(self) -> None:
        """Map global debug and verbosity flags independently from intermediate-file retention."""
        for flags, enable_guest_debug, keep_intermediates in (
                ([], False, False), (['--verbose'], False, False), (['--debug'], True, False),
                (['--keep-intermediates'], False, True), (['--keep-intermediates', '--debug'], True, True)):
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
                self.assertIs(build.call_args.kwargs['enable_guest_debug'], enable_guest_debug)
                self.assertIs(build.call_args.kwargs['keep_intermediates'], keep_intermediates)
                self.assertIsNone(build.call_args.kwargs['workspace'])
                if enable_guest_debug:
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
        """Expose the failed build stage and any retained workspace through the CLI error."""
        for workspace in (None, Path('residual')):
            with self.subTest(workspace=workspace):
                error = builder.IgvmBuildError('tool failed', stage='generate-igvm', workspace=workspace)
                with patch.object(builder, 'build_igvm', side_effect=error):
                    with self.assertRaises(CLIError) as raised:
                        custom.create_enclave(self.cmd, 'image.igvm', workspace='work', dependency_bundle='bundle')
                self.assertIn('generate-igvm: tool failed', str(raised.exception))
                self.assertIs(raised.exception.__cause__, error)
                self.assertEqual('Build workspace available at:' in str(raised.exception),
                                 workspace is not None)

    def test_required_inputs_and_nonconflicting_options(self) -> None:
        """Register the intended create arguments and supply the packaged startup script by default."""
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
        self.assertIs(arguments['keep_intermediates'].options['default'], False)
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
        """Select the packaged dependency archive when the caller provides no bundle override."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'dependency-bundle.tar.gz'
            archive.write_bytes(b'archive selection is validated by the builder')
            with patch.object(custom, 'DEFAULT_DEPENDENCY_BUNDLE', archive), \
                    patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                        Path('image.igvm'), self.measurements)) as build:
                custom.create_enclave(self.cmd, 'image.igvm', workspace='work')
            self.assertEqual(build.call_args.kwargs['dependency_bundle'], archive)

    def test_missing_packaged_bundle_has_actionable_override_instructions(self) -> None:
        """Explain how to reinstall or override a missing packaged bundle without invoking the builder."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'dependency-bundle.tar.gz'
            with patch.object(custom, 'DEFAULT_DEPENDENCY_BUNDLE', archive), \
                    patch.object(builder, 'build_igvm') as build:
                with self.assertRaisesRegex(CLIError, 'missing.*--dependency-bundle'):
                    custom.create_enclave(self.cmd, 'image.igvm', workspace='work')
            build.assert_not_called()

    def test_invalid_isolation_configuration_precedes_bundle_lookup(self) -> None:
        """Reject invalid isolation metadata before looking up the default bundle or starting a build."""
        with patch('azext_enclave._isolation.load_isolation', side_effect=ValueError('bad manifest')), \
                patch.object(Path, 'is_file') as lookup, patch.object(builder, 'build_igvm') as build:
            with self.assertRaisesRegex(CLIError, 'Unable to create Enclave image.*bad manifest'):
                custom.create_enclave(self.cmd, 'image.igvm')
            lookup.assert_not_called()
            build.assert_not_called()

    def test_unsupported_build_options_precede_bundle_lookup_and_debug_warning(self) -> None:
        """Reject unsupported builds, signing, or debug requests before bundle access or debug warnings."""
        configuration = replace(load_isolation(), isolation_type='tdx')
        for options, message in (
                ({}, 'Image creation is not implemented'),
                ({'signing_key': 'private.pem'}, 'SNP ID-block signing is not supported'),
                ({'debug': True}, 'Guest debug configuration is not implemented')):
            with self.subTest(options=options), \
                    patch('azext_enclave._isolation.load_isolation', return_value=configuration), \
                    patch.object(builder, 'SnpBuildOptions',
                                 side_effect=AssertionError('SNP options must not be constructed')) as snp_options, \
                    patch.object(Path, 'is_file') as lookup, \
                    patch.object(custom.logger, 'warning') as warning, \
                    patch.object(builder, 'build_igvm') as build:
                self.cmd.cli_ctx.data['safe_params'] = ['--debug'] if options.get('debug') else []
                with self.assertRaisesRegex(CLIError, message):
                    custom.create_enclave(self.cmd, 'image.igvm', signing_key=options.get('signing_key'))
                lookup.assert_not_called()
                snp_options.assert_not_called()
                warning.assert_not_called()
                build.assert_not_called()

    def test_real_packaged_inputs_and_default_selection(self) -> None:
        """Validate the shipped application and dependency archive and select both when overrides are absent."""
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
        """Forward application and startup-script overrides through the registered CLI command."""
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
        """Use a caller application even when the packaged application is missing."""
        with patch.object(custom, 'DEFAULT_APPLICATION', Path('missing-packaged-app')), \
                patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                    Path('image.igvm'), self.measurements)) as build:
            custom.create_enclave(self.cmd, 'image.igvm', workspace='work', dependency_bundle='bundle',
                                  application='custom-app')
        self.assertEqual(build.call_args.kwargs['application'], Path('custom-app'))
        self.assertEqual(build.call_args.kwargs['init_script'],
                         custom.DEPENDENCIES_DIR / 'initramfs' / 'enclave-init')

    def test_archive_override_is_forwarded_without_requiring_packaged_bundle(self) -> None:
        """Forward an explicit dependency archive without requiring the default packaged bundle."""
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
        """Reject traversal, absolute, and platform-specific archive paths before they escape extraction."""
        for name in ('../outside', '/outside', 'nested/../../outside',
                     r'..\outside', 'C:/outside', r'C:\outside'):
            with self.subTest(name=name):
                self.write_archive([(name, tarfile.REGTYPE)])
                with self.assertRaisesRegex(ValueError, 'Unsafe dependency archive path'):
                    builder._extract_bundle(self.archive_path, self.destination)
                self.assertFalse((self.root / 'outside').exists())
                self.destination.rmdir()

    def test_rejects_links_and_special_files(self) -> None:
        """Reject archive links, pipes, and device nodes rather than materializing unsafe dependency entries."""
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE,
                     tarfile.CHRTYPE, tarfile.BLKTYPE):
            with self.subTest(kind=kind):
                self.write_archive([('unsafe', kind)])
                with self.assertRaisesRegex(ValueError, 'only regular files and directories'):
                    builder._extract_bundle(self.archive_path, self.destination)
                self.destination.rmdir()

    def test_rejects_duplicate_files_without_overwriting(self) -> None:
        """Reject duplicate archive entries without overwriting the first extracted file."""
        self.write_archive([('same', tarfile.REGTYPE), ('same', tarfile.REGTYPE)])
        with self.assertRaises(FileExistsError):
            builder._extract_bundle(self.archive_path, self.destination)
        self.assertEqual((self.destination / 'same').read_bytes(), b'{}')

    def test_requires_a_manifest_at_supported_archive_root(self) -> None:
        """Require the bundle index at the archive root or inside its supported single top-level directory."""
        self.write_archive([('other', tarfile.REGTYPE)])
        with self.assertRaisesRegex(ValueError, 'must contain dependency-manifest.v1.json'):
            builder._extract_bundle(self.archive_path, self.destination)

    @unittest.skipUnless(os.name == 'posix', 'Unix executable and setuid mode semantics')
    def test_preserves_executability_but_discards_privileged_bits(self) -> None:
        """Preserve executable permissions during extraction while stripping setuid and other privileged bits."""
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
                'image': {'snp_linux_direct': {
                    'processor_count': 1, 'memory_page_count': 40960,
                    'linux': {'use_initrd': True, 'command_line': 'bundle-command-line'},
                    'pcie_host_bridge': {
                        'segment': 0, 'start_bus': 0, 'end_bus': 1, 'ecam_base': 4141875200,
                        'low_mmio_base': 4143972352, 'low_mmio_size': 16777216,
                        'high_mmio_base': 4294967296, 'high_mmio_size': 1048576,
                    },
                }},
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
        """Build an unsigned image without OpenSSL, preserve inputs, and remove successful build intermediates."""
        with patch.object(builder.shutil, 'which', side_effect=lambda command: (
                None if command == 'openssl' else '/usr/bin/tool')):
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

    def test_keep_intermediates_retains_build_on_success_or_failure(self) -> None:
        """Retain requested successful workspaces and all failed or interrupted builds with diagnostic logs."""
        for keep_intermediates, outcome in (
                (True, 'success'), (True, 'stage-error'), (True, 'storage-error'), (True, 'interrupted'),
                (False, 'stage-error'), (False, 'storage-error'), (False, 'interrupted')):
            with self.subTest(keep_intermediates=keep_intermediates, outcome=outcome):
                logger = MagicMock()
                before = set(self.inputs.workspace.iterdir()) if self.inputs.workspace.exists() else set()
                error = {
                    'stage-error': builder.IgvmBuildError('tool failed', stage='generate-igvm'),
                    'storage-error': OSError(errno.ENOSPC, 'no space'),
                    'interrupted': KeyboardInterrupt(),
                }.get(outcome)

                def run(argv, stage, work, **kwargs):
                    self.run_stage(argv, stage, work, **kwargs)
                    if stage == 'generate-igvm' and error is not None:
                        raise error

                self.run.side_effect = run
                with patch.object(builder.shutil, 'rmtree') as remove:
                    if error is None:
                        result = self.inputs.build(keep_intermediates=keep_intermediates, logger=logger)
                        self.assertEqual(result.measurements, {'snp': self.inputs.measurement})
                        self.assertEqual(list(self.inputs.output.parent.iterdir()), [self.inputs.output])
                        self.inputs.output.unlink()
                    else:
                        expected = KeyboardInterrupt if outcome == 'interrupted' else builder.IgvmBuildError
                        with self.assertRaises(expected) as raised:
                            self.inputs.build(keep_intermediates=keep_intermediates, logger=logger)
                        self.assertFalse(self.inputs.output.exists())
                    remove.assert_not_called()
                work, = set(self.inputs.workspace.iterdir()) - before
                self.assertTrue((work / 'generate-igvm.log').is_file())
                self.assertTrue((work / 'rootfs').is_dir())
                logger.warning.assert_called_once_with('Build workspace retained at: %s', work)
                if error is not None and outcome != 'interrupted':
                    self.assertEqual(raised.exception.workspace, work)

    def test_keep_intermediates_with_default_temporary_parent(self) -> None:
        """Honor intermediate retention in a private directory under the default temporary parent."""
        parent = self.inputs.root / 'temporary'
        parent.mkdir()
        with patch.dict(os.environ, {'TMPDIR': str(parent)}), patch.object(tempfile, 'tempdir', None):
            self.inputs.build(workspace=None, keep_intermediates=True)
        work, = parent.iterdir()
        self.assertTrue(work.name.startswith('.enclave-build-'))
        if os.name == 'posix':
            self.assertEqual(work.stat().st_mode & 0o777, 0o700)
        self.assertTrue((work / 'image.igvm').is_file())
        self.assertEqual(list(self.inputs.output.parent.iterdir()), [self.inputs.output])

    def test_failed_cli_build_reports_retained_default_workspace(self) -> None:
        """Report the retained default workspace and generator failure through the CLI."""
        parent = self.inputs.root / 'temporary'
        parent.mkdir()

        def run(argv, stage, work, **kwargs):
            self.run_stage(argv, stage, work, **kwargs)
            if stage == 'generate-igvm':
                raise builder.IgvmBuildError('generator failed', stage=stage)

        self.run.side_effect = run
        cmd = SimpleNamespace(cli_ctx=SimpleNamespace(data={'safe_params': []}))
        with patch.dict(os.environ, {'TMPDIR': str(parent)}), patch.object(tempfile, 'tempdir', None):
            with self.assertRaisesRegex(CLIError, 'generate-igvm: generator failed') as raised:
                custom.create_enclave(
                    cmd, str(self.inputs.output), dependency_bundle=str(self.inputs.bundle),
                    application=str(self.inputs.application), init_script=str(self.inputs.init))
        work, = parent.iterdir()
        self.assertIn('Build workspace available at: {}'.format(work), str(raised.exception))
        self.assertTrue((work / 'generate-igvm.log').is_file())
        self.assertTrue((work / 'image.igvm').is_file())
        self.assertFalse(self.inputs.output.exists())

    def test_keep_intermediates_cleans_staging_after_publication_failure(self) -> None:
        """Remove publication staging after failure while retaining the requested build workspace."""
        with patch.object(builder.os, 'link', side_effect=OSError('publication failed')):
            with self.assertLogs(builder.__name__, level='WARNING') as logs:
                with self.assertRaises(builder.IgvmBuildError) as raised:
                    self.inputs.build(keep_intermediates=True)
        work, = self.inputs.workspace.iterdir()
        self.assertEqual(raised.exception.stage, 'publish')
        self.assertEqual(raised.exception.workspace, work)
        self.assertTrue((work / 'image.igvm').is_file())
        self.assertIn(str(work), logs.output[0])
        self.assertEqual(list(self.inputs.output.parent.iterdir()), [])

    def test_keep_intermediates_reports_workspace_when_staging_cleanup_fails(self) -> None:
        """Report staging-cleanup failure with the retained workspace even when intermediate retention is enabled."""
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path.name.startswith('.igvm-'):
                raise OSError('staging removal denied')
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', autospec=True, side_effect=unlink):
            with self.assertRaisesRegex(builder.IgvmBuildError, 'staging removal denied') as raised:
                self.inputs.build(keep_intermediates=True)
        work, = self.inputs.workspace.iterdir()
        self.assertEqual(raised.exception.stage, 'cleanup')
        self.assertEqual(raised.exception.workspace, work)
        self.assertTrue((work / 'image.igvm').is_file())
        self.assertTrue(self.inputs.output.is_file())

    def test_default_workspace_is_private_and_cleaned(self) -> None:
        """Use a private default temporary workspace and remove it after successful image publication."""
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

    def test_publication_failure_cleans_staging_and_retains_workspace(self) -> None:
        """Remove incomplete publication files but retain the generated image and workspace after publication fails."""
        with patch.object(builder.os, 'link', side_effect=OSError('publication failed')):
            with self.assertRaisesRegex(builder.IgvmBuildError, 'publication failed') as raised:
                self.inputs.build()
        self.assertEqual(raised.exception.stage, 'publish')
        work, = self.inputs.workspace.iterdir()
        self.assertEqual(raised.exception.workspace, work)
        self.assertEqual((work / 'image.igvm').read_bytes(), b'unsigned image')
        self.assertEqual(list(self.inputs.output.parent.iterdir()), [])

    def test_staging_cleanup_failure_after_publication_is_reported(self) -> None:
        """Surface failed staging removal without losing the published image or diagnostic workspace."""
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path.name.startswith('.igvm-'):
                raise OSError('staging removal denied')
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', autospec=True, side_effect=unlink):
            with self.assertRaisesRegex(builder.IgvmBuildError, 'staging removal denied') as raised:
                self.inputs.build()
        self.assertEqual(raised.exception.stage, 'cleanup')
        work, = self.inputs.workspace.iterdir()
        self.assertEqual(raised.exception.workspace, work)
        self.assertTrue((work / 'generate-igvm.log').is_file())
        self.assertTrue(self.inputs.output.is_file())
        staging, = (path for path in self.inputs.output.parent.iterdir() if path != self.inputs.output)
        self.assertTrue(staging.name.startswith('.igvm-'))
        self.assertIn(str(staging), str(raised.exception))

    def test_default_temp_location_and_explicit_parent_have_same_results(self) -> None:
        """Produce identical images with default or explicit workspaces while preserving unrelated temporary files."""
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
        """Isolate overlapping builds so one build cannot remove another build's active workspace."""
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

    def test_storage_and_execution_errors_suggest_workspace_and_retain_it(self) -> None:
        """Retain build evidence and suggest an alternate workspace after storage or execution failures."""
        for code in (errno.ENOSPC, errno.EACCES, errno.ENOEXEC):
            with self.subTest(code=code):
                self.run.side_effect = OSError(code, 'build storage unavailable')
                with self.assertRaisesRegex(builder.IgvmBuildError, '--workspace') as raised:
                    self.inputs.build()
                self.assertEqual(raised.exception.workspace.parent, self.inputs.workspace)
                self.assertTrue(raised.exception.workspace.is_dir())
                self.assertFalse(self.inputs.output.exists())

    def test_workspace_paths_use_debug_logging_only(self) -> None:
        """Keep successful temporary workspace paths in debug logs rather than progress or warning messages."""
        logger = MagicMock()
        self.inputs.build(logger=logger)
        work = logger.debug.call_args.args[1]
        logger.debug.assert_called_once_with('Build workspace: %s', work)
        self.assertFalse(any(str(work) in str(call) for call in logger.info.call_args_list))
        logger.warning.assert_not_called()
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])

    def test_failed_build_skips_workspace_cleanup_and_preserves_stage_error(self) -> None:
        """Preserve the original stage failure and leave its workspace untouched for investigation."""
        self.run.side_effect = builder.IgvmBuildError('generator failed', stage='generate-igvm')
        with patch.object(builder.shutil, 'rmtree') as remove:
            with self.assertRaises(builder.IgvmBuildError) as raised:
                self.inputs.build()
        error = raised.exception
        self.assertEqual(error.stage, 'generate-igvm')
        self.assertIn('generator failed', str(error))
        remove.assert_not_called()
        self.assertEqual(error.workspace.parent, self.inputs.workspace)
        self.assertTrue(error.workspace.is_dir())
        self.assertFalse(self.inputs.output.exists())

    def test_workspace_cleanup_failure_retains_workspace_and_published_image(self) -> None:
        """Report workspace-removal failure while preserving both the published image and remaining intermediates."""
        with patch.object(builder.shutil, 'rmtree', side_effect=OSError('removal denied')):
            with self.assertRaises(builder.IgvmBuildError) as raised:
                self.inputs.build()
        self.assertEqual(raised.exception.stage, 'cleanup')
        self.assertTrue(raised.exception.workspace.is_dir())
        self.assertEqual(self.inputs.output.read_bytes(), b'unsigned image')
        self.assertEqual(list(self.inputs.output.parent.iterdir()), [self.inputs.output])

    def test_interrupted_build_retains_workspace(self) -> None:
        """Retain and report the active workspace after interruption without publishing an image."""
        self.run.side_effect = KeyboardInterrupt()
        with self.assertLogs(builder.__name__, level='WARNING') as logs:
            with self.assertRaises(KeyboardInterrupt):
                self.inputs.build()
        work, = self.inputs.workspace.iterdir()
        self.assertIn(str(work), logs.output[0])
        self.assertFalse(self.inputs.output.exists())

    def test_staging_unlink_failure_preserves_stage_error_and_workspace(self) -> None:
        """Report both publication and staging-removal errors while retaining the original build evidence."""
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
        work, = self.inputs.workspace.iterdir()
        self.assertEqual(raised.exception.workspace, work)
        self.assertTrue((work / 'generate-igvm.log').is_file())
        self.assertFalse(self.inputs.output.exists())

    def test_archive_bundles_build_and_cleanup(self) -> None:
        """Build from flat or single-directory archives and clean extracted dependencies after success."""
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

    def test_corrupt_archive_fails_before_build_and_retains_workspace(self) -> None:
        """Retain extraction diagnostics for a corrupt archive without executing any build commands."""
        archive_path = self.inputs.root / 'broken.tar.gz'
        archive_path.write_bytes(b'not an archive')
        self.inputs.bundle = archive_path
        with self.assertRaises(builder.IgvmBuildError) as raised:
            self.inputs.build()
        self.assertEqual(raised.exception.stage, 'extract-dependencies')
        self.assertFalse(self.inputs.output.exists())
        work, = self.inputs.workspace.iterdir()
        self.assertEqual(raised.exception.workspace, work)
        self.run.assert_not_called()

    def test_archive_artifact_checksums_are_still_required(self) -> None:
        """Reject tampered archived artifacts after extraction and retain the failed validation workspace."""
        (self.inputs.bundle / 'kernel').write_bytes(b'tampered kernel')
        archive_path = self.inputs.root / 'tampered.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            archive.add(self.inputs.bundle, arcname='.')
        self.inputs.bundle = archive_path
        with self.assertRaisesRegex(builder.IgvmBuildError, 'validate-dependencies:.*checksum mismatch') as raised:
            self.inputs.build()
        self.assertTrue(raised.exception.workspace.is_dir())
        self.assertEqual(raised.exception.workspace.parent, self.inputs.workspace)
        self.run.assert_not_called()

    def test_composed_rootfs_and_normalized_metadata(self) -> None:
        """Compose the requested application and init script while removing legacy files and normalizing metadata."""
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

    def test_missing_openssl_for_snp_signing_precedes_workspace_creation(self) -> None:
        """Reject unavailable signing tools before preparing files or running build commands."""
        with patch.object(builder.shutil, 'which', side_effect=lambda command: (
                None if command == 'openssl' else '/usr/bin/tool')):
            with self.assertRaisesRegex(builder.IgvmBuildError, 'validate: Required command not found: openssl') as raised:
                self.inputs.build(isolation_options=builder.SnpBuildOptions(signing_key=self.inputs.key))
        self.assertIsNone(raised.exception.workspace)
        self.assertFalse(self.inputs.workspace.exists())
        self.assertFalse(self.inputs.output.exists())
        self.run.assert_not_called()

    def test_signed_image_only_and_private_key_not_copied(self) -> None:
        """Publish only the signed image and keep the caller's private key out of build intermediates."""
        def inspect(work):
            self.assertEqual((work / 'id-public-key.pem').read_bytes(), b'public key')
            self.assertFalse(any(path.is_file() and path.read_bytes() == self.inputs.key.read_bytes()
                                 for path in work.rglob('*')))

        inspection = self.inspect_stage('add-snp-id-block', inspect)
        result = self.inputs.build(isolation_options=builder.SnpBuildOptions(signing_key=self.inputs.key))
        inspection.assert_called_once()
        self.assertEqual(result.measurements, {'snp': self.inputs.measurement})
        self.assertEqual(self.inputs.output.read_bytes(), b'signed image')
        self.assertEqual(self.stages[-3:], [
            'export-id-public-key', 'sign-id-block', 'add-snp-id-block'])
        self.assertEqual(list(self.inputs.workspace.iterdir()), [])
        self.assertEqual(self.inputs.key.read_bytes(), b'caller-owned-test-key')

    def test_guest_debug_is_independent_of_cleanup_and_bundle(self) -> None:
        """Apply explicit guest-debug choices without modifying the source recipe or changing cleanup behavior."""
        original = (self.inputs.bundle / 'igvm_manifest').read_bytes()
        for enable_guest_debug in (False, True):
            with self.subTest(enable_guest_debug=enable_guest_debug):
                def inspect(work):
                    config = json.loads((work / 'igvm-manifest.json').read_text(encoding='utf-8'))
                    self.assertIs(
                        config['guest_configs'][0]['isolation_type']['snp']['enable_debug'], enable_guest_debug)

                inspection = self.inspect_stage('generate-igvm', inspect)
                result = self.inputs.build(enable_guest_debug=enable_guest_debug)
                inspection.assert_called_once()
                self.assertEqual(list(self.inputs.workspace.iterdir()), [])
                self.assertEqual((self.inputs.bundle / 'igvm_manifest').read_bytes(), original)
                self.assertEqual(result.measurements, {'snp': self.inputs.measurement})
                self.inputs.output.unlink()

    def test_default_disables_guest_debug_from_bundle(self) -> None:
        """Disable guest debugging by default even when the bundle recipe enables it."""
        def inspect(work):
            config = json.loads((work / 'igvm-manifest.json').read_text(encoding='utf-8'))
            self.assertIs(config['guest_configs'][0]['isolation_type']['snp']['enable_debug'], False)

        for options in ({}, {'isolation_options': None}, {'isolation_options': builder.SnpBuildOptions()}):
            with self.subTest(options=options):
                inspection = self.inspect_stage('generate-igvm', inspect)
                self.inputs.build(**options)
                inspection.assert_called_once()
                self.assertEqual(list(self.inputs.workspace.iterdir()), [])
                self.inputs.output.unlink()

    def test_create_preserves_recipe_settings_independent_of_launch_configuration(self) -> None:
        """Preserve recipe-owned CPU, memory, and PCIe settings while applying only the debug override."""
        recipe = self.inputs.bundle / 'igvm_manifest'
        document = json.loads(recipe.read_bytes())
        linux = document['guest_configs'][0]['image']['snp_linux_direct']
        linux['processor_count'] = 2
        linux['memory_page_count'] = 65536
        linux['pcie_host_bridge']['segment'] = 1
        original = json.dumps(document).encode('utf-8')
        recipe.write_bytes(original)
        self.inputs.manifest['artifacts']['igvm_manifest']['sha256'] = hashlib.sha256(original).hexdigest()
        self.inputs.write_manifest()
        document['guest_configs'][0]['isolation_type']['snp']['enable_debug'] = False

        def inspect(work):
            config = json.loads((work / 'igvm-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(config, document)

        inspection = self.inspect_stage('generate-igvm', inspect)
        self.inputs.build()
        inspection.assert_called_once()
        self.assertEqual(recipe.read_bytes(), original)

    def test_unimplemented_isolation_rejects_all_snp_operations_before_inputs(self) -> None:
        """Reject unsupported isolation builds and SNP-only options before creating workspaces or running tools."""
        configuration = replace(load_isolation(), isolation_type='tdx')
        for options, message in (
                ({}, 'Image creation is not implemented'),
                ({'isolation_options': builder.SnpBuildOptions()}, 'SNP build options are not supported'),
                ({'enable_guest_debug': True}, 'Guest debug configuration is not implemented'),
                ({'isolation_options': builder.SnpBuildOptions(), 'enable_guest_debug': True},
                 'Guest debug configuration is not implemented'),
                ({'isolation_options': builder.SnpBuildOptions(signing_key=self.inputs.key)},
                 'SNP ID-block signing is not supported')):
            with self.subTest(options=options):
                with self.assertRaisesRegex(builder.IgvmBuildError, message):
                    self.inputs.build(configuration=configuration, **options)
                self.assertFalse(self.inputs.workspace.exists())
                self.assertFalse(self.inputs.output.exists())
        self.run.assert_not_called()

    def test_invalid_isolation_options_are_rejected_before_inputs(self) -> None:
        """Reject untyped isolation options before reading inputs or creating build artifacts."""
        with patch.object(builder, '_input_file') as read_input:
            for options in ({'signing_key': self.inputs.key}, SimpleNamespace(signing_key=self.inputs.key)):
                with self.subTest(options=options):
                    with self.assertRaisesRegex(builder.IgvmBuildError, 'Unsupported isolation build options'):
                        self.inputs.build(isolation_options=options)
                    self.assertFalse(self.inputs.workspace.exists())
                    self.assertFalse(self.inputs.output.exists())
            read_input.assert_not_called()
        self.run.assert_not_called()

    def test_type_specific_build_boundaries_never_fall_back_to_snp(self) -> None:
        """Reject unsupported recipe, generation, and measurement operations without invoking SNP handlers."""
        configuration = replace(load_isolation(), isolation_type='tdx')
        with patch.object(builder, '_snp_recipe') as recipe, \
                patch.object(builder, '_snp_measurement') as measurement:
            for operation in (
                    lambda: builder._recipe(self.inputs.bundle / 'igvm_manifest', configuration),
                    lambda: builder._generation_inputs(
                        {'igvm_manifest': self.inputs.bundle / 'igvm_manifest'}, Path('initrd'), configuration,
                        False),
                    lambda: builder._measurements(Path('unused'), 'tdx')):
                with self.subTest(operation=operation), self.assertRaisesRegex(
                        ValueError, 'not implemented|not supported'):
                    operation()
            recipe.assert_not_called()
            measurement.assert_not_called()

    def test_dependency_integrity_checks_do_not_require_snp_artifacts(self) -> None:
        """Apply artifact requirements from the selected contract while retaining shared checksum validation."""
        configuration = load_isolation()
        configuration = replace(
            configuration, isolation_type='future',
            dependency_bundle=replace(configuration.dependency_bundle, required_artifacts=(
                'kernel', 'base_initrd', 'igvmfilegen', 'igvm_manifest')))
        del self.inputs.manifest['artifacts']['snp_bootshim']
        self.inputs.write_manifest()
        with patch.object(builder, '_recipe') as recipe:
            dependencies = builder._dependencies(self.inputs.bundle, configuration)
            self.assertNotIn('snp_bootshim', dependencies)
            recipe.assert_called_once_with(self.inputs.bundle / 'igvm_manifest', configuration)
            self.inputs.manifest['artifacts']['kernel']['sha256'] = '0' * 64
            self.inputs.write_manifest()
            with self.assertRaisesRegex(ValueError, 'Dependency checksum mismatch: kernel'):
                builder._dependencies(self.inputs.bundle, configuration)

    def test_invalid_measurements_fail_before_publication(self) -> None:
        """Reject malformed SNP measurement records before publication and retain generator evidence."""
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
                self.assertTrue(raised.exception.workspace.is_dir())
                self.assertTrue((raised.exception.workspace / 'generate-igvm.log').is_file())

    def test_missing_or_invalid_json_measurements_retain_workspace(self) -> None:
        """Retain generator logs when measurement sidecars are missing, malformed, or not JSON objects."""
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
                self.assertTrue(raised.exception.workspace.is_dir())
                self.assertTrue((raised.exception.workspace / 'generate-igvm.log').is_file())
                self.assertFalse(self.inputs.output.exists())

    def test_failure_retention_with_guest_debug(self) -> None:
        """Retain failed build workspaces consistently whether guest debugging is enabled or disabled."""
        for enable_guest_debug in (False, True):
            with self.subTest(enable_guest_debug=enable_guest_debug):
                self.run.side_effect = builder.IgvmBuildError('generator failed', stage='generate-igvm')
                with self.assertRaises(builder.IgvmBuildError) as raised:
                    self.inputs.build(enable_guest_debug=enable_guest_debug)
                self.assertFalse(self.inputs.output.exists())
                self.assertEqual(raised.exception.stage, 'generate-igvm')
                self.assertTrue(raised.exception.workspace.is_dir())
                self.assertEqual(raised.exception.workspace.parent, self.inputs.workspace)

    def test_signing_failure_never_publishes_unsigned_image(self) -> None:
        """Leave output unpublished and retain diagnostics when signing the SNP ID block fails."""
        def fail_signing(argv, stage, work, **kwargs) -> None:
            if stage == 'sign-id-block':
                raise builder.IgvmBuildError('signing failed', stage=stage)
            self.run_stage(argv, stage, work, **kwargs)

        self.run.side_effect = fail_signing
        with self.assertRaisesRegex(builder.IgvmBuildError, 'sign-id-block: signing failed') as raised:
            self.inputs.build(isolation_options=builder.SnpBuildOptions(signing_key=self.inputs.key))
        self.assertFalse(self.inputs.output.exists())
        self.assertTrue((raised.exception.workspace / 'generate-igvm.log').is_file())

    def test_existing_output_is_preserved(self) -> None:
        """Reject an existing output path before build commands run and preserve its original content."""
        self.inputs.output.parent.mkdir()
        self.inputs.output.write_bytes(b'existing')
        with self.assertRaisesRegex(builder.IgvmBuildError, 'Output already exists'):
            self.inputs.build()
        self.assertEqual(self.inputs.output.read_bytes(), b'existing')
        self.run.assert_not_called()

    def test_checksums_and_artifact_paths_are_validated(self) -> None:
        """Reject modified dependency content and artifact paths that escape the bundle."""
        (self.inputs.bundle / 'kernel').write_bytes(b'modified')
        with self.assertRaisesRegex(builder.IgvmBuildError, 'checksum mismatch: kernel'):
            self.inputs.build()
        self.inputs.manifest['artifacts']['kernel']['path'] = '../application'
        self.inputs.write_manifest()
        with self.assertRaisesRegex(builder.IgvmBuildError, 'must be bundle-relative'):
            self.inputs.build()
        self.run.assert_not_called()

    def test_profile_and_measured_pcie_are_required(self) -> None:
        """Require the supported dependency profile and a populated measured-PCIe recipe layout."""
        self.inputs.manifest['profile'] = 'other'
        self.inputs.write_manifest()
        with self.assertRaisesRegex(builder.IgvmBuildError, 'measured-pcie-v1'):
            self.inputs.build()
        self.inputs.manifest['profile'] = 'measured-pcie-v1'
        config = self.inputs.bundle / 'igvm_manifest'
        document = json.loads(config.read_bytes())
        document['guest_configs'][0]['image']['snp_linux_direct']['pcie_host_bridge'] = {}
        config.write_text(json.dumps(document), encoding='utf-8')
        self.inputs.manifest['artifacts']['igvm_manifest']['sha256'] = hashlib.sha256(config.read_bytes()).hexdigest()
        self.inputs.write_manifest()
        with self.assertRaisesRegex(builder.IgvmBuildError, 'measured PCIe'):
            self.inputs.build()
        self.run.assert_not_called()

    def test_incompatible_recipes_fail_without_rewriting_layout(self) -> None:
        """Reject unsupported recipe structures and invalid PCIe fields without rewriting caller inputs."""
        recipe = self.inputs.bundle / 'igvm_manifest'
        original = recipe.read_bytes()
        for change in ('architecture', 'boot_mode', 'isolation', 'missing_layout_field', 'boolean_layout',
                       'boolean_processor_count', 'boolean_memory_page_count'):
            with self.subTest(change=change):
                document = json.loads(original)
                guest = document['guest_configs'][0]
                if change == 'architecture':
                    document['guest_arch'] = 'aarch64'
                elif change == 'boot_mode':
                    guest['image'] = {'uefi': {}}
                elif change == 'isolation':
                    guest['isolation_type'] = {'tdx': {}}
                elif change == 'missing_layout_field':
                    del guest['image']['snp_linux_direct']['pcie_host_bridge']['segment']
                elif change == 'boolean_layout':
                    guest['image']['snp_linux_direct']['pcie_host_bridge']['segment'] = False
                else:
                    guest['image']['snp_linux_direct'][change[len('boolean_'):]] = True
                data = json.dumps(document).encode()
                recipe.write_bytes(data)
                self.inputs.manifest['artifacts']['igvm_manifest']['sha256'] = hashlib.sha256(data).hexdigest()
                self.inputs.write_manifest()
                with self.assertRaises(builder.IgvmBuildError):
                    self.inputs.build()
                self.assertFalse(self.inputs.workspace.exists())
                self.assertEqual(recipe.read_bytes(), data)
        self.run.assert_not_called()

    def test_shared_recipe_validation_precedes_type_specific_handling(self) -> None:
        """Validate the recipe architecture and single-guest envelope before isolation-specific dispatch."""
        recipe = self.inputs.bundle / 'igvm_manifest'
        original = recipe.read_bytes()
        configuration = load_isolation()
        for isolation_type in ('snp', 'future'):
            for field, value, message in (
                    ('guest_arch', None, 'recipe architecture is not implemented'),
                    ('guest_arch', 'aarch64', 'recipe architecture is not implemented'),
                    ('guest_configs', None, 'Expected one IGVM guest configuration'),
                    ('guest_configs', {}, 'Expected one IGVM guest configuration'),
                    ('guest_configs', [], 'Expected one IGVM guest configuration'),
                    ('guest_configs', [None], 'Expected one IGVM guest configuration'),
                    ('guest_configs', [{}, {}], 'Expected one IGVM guest configuration')):
                with self.subTest(isolation_type=isolation_type, field=field, value=value), \
                        patch.object(builder, '_snp_recipe') as snp_recipe:
                    document = json.loads(original)
                    document[field] = value
                    recipe.write_text(json.dumps(document), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, message):
                        builder._recipe(recipe, replace(configuration, isolation_type=isolation_type))
                    snp_recipe.assert_not_called()

    def test_missing_or_invalid_recipe_resources_fail_without_launch_defaults(self) -> None:
        """Reject missing or invalid recipe CPU and memory settings without substituting launch defaults."""
        recipe = self.inputs.bundle / 'igvm_manifest'
        original = recipe.read_bytes()
        for field in ('processor_count', 'memory_page_count'):
            for value in (None, False, 0, -1):
                with self.subTest(field=field, value=value):
                    document = json.loads(original)
                    linux = document['guest_configs'][0]['image']['snp_linux_direct']
                    if value is None:
                        del linux[field]
                    else:
                        linux[field] = value
                    data = json.dumps(document).encode('utf-8')
                    recipe.write_bytes(data)
                    self.inputs.manifest['artifacts']['igvm_manifest']['sha256'] = hashlib.sha256(data).hexdigest()
                    self.inputs.write_manifest()
                    with self.assertRaisesRegex(builder.IgvmBuildError, 'IGVM manifest has invalid ' + field):
                        self.inputs.build()
                    self.assertFalse(self.inputs.workspace.exists())
                    self.assertFalse(self.inputs.output.exists())
        self.run.assert_not_called()

    def test_generation_inputs_validate_the_recipe_before_type_specific_modifications(self) -> None:
        """Reject unsupported recipe architecture before applying isolation-specific generation changes."""
        recipe = self.inputs.bundle / 'igvm_manifest'
        document = json.loads(recipe.read_bytes())
        document['guest_arch'] = 'aarch64'
        recipe.write_text(json.dumps(document), encoding='utf-8')
        configuration = replace(load_isolation(), isolation_type='future')
        with patch.object(builder, '_snp_recipe') as snp_recipe:
            with self.assertRaisesRegex(ValueError, 'recipe architecture is not implemented'):
                builder._generation_inputs(
                    {'igvm_manifest': recipe}, Path('initrd'), configuration, False)
            snp_recipe.assert_not_called()

    def test_bundle_cannot_weaken_required_artifacts(self) -> None:
        """Require every implementation-mandated artifact even when the bundle index omits it."""
        for name in ('kernel', 'base_initrd', 'snp_bootshim', 'igvmfilegen', 'igvm_manifest'):
            with self.subTest(name=name):
                artifact = self.inputs.manifest['artifacts'].pop(name)
                self.inputs.write_manifest()
                with self.assertRaisesRegex(builder.IgvmBuildError, 'missing required artifacts'):
                    self.inputs.build()
                self.assertFalse(self.inputs.workspace.exists())
                self.inputs.manifest['artifacts'][name] = artifact
        self.run.assert_not_called()

    def test_supplied_optional_artifact_hashes_remain_required(self) -> None:
        """Verify supplied optional artifacts rather than bypassing their declared checksums."""
        (self.inputs.bundle / 'extra').write_bytes(b'extra')
        self.inputs.manifest['artifacts']['extra'] = {'path': 'extra', 'sha256': '0' * 64}
        self.inputs.write_manifest()
        with self.assertRaisesRegex(builder.IgvmBuildError, 'checksum mismatch: extra'):
            self.inputs.build()
        self.run.assert_not_called()

    def test_schema_version_requires_integer_one(self) -> None:
        """Reject unsupported bundle schema versions and non-integer values, including booleans."""
        for version in (None, True, False, 1.0, 0, 2, '1', {}, []):
            with self.subTest(version=version):
                self.inputs.manifest['schema_version'] = version
                self.inputs.write_manifest()
                with self.assertRaisesRegex(builder.IgvmBuildError, 'Expected schema 1'):
                    self.inputs.build()
        self.run.assert_not_called()

    def test_epoch_validation_precedes_workspace_creation(self) -> None:
        """Reject invalid archive timestamps before creating workspaces or executing build tools."""
        for epoch in ('-1', '4294967296', 'not-an-integer'):
            with self.subTest(epoch=epoch), patch.dict(os.environ, {'SOURCE_DATE_EPOCH': epoch}):
                with self.assertRaises(builder.IgvmBuildError):
                    self.inputs.build()
                self.assertFalse(self.inputs.workspace.exists())
        self.run.assert_not_called()

    def test_application_requires_static_x64_elf(self) -> None:
        """Reject invalid ELF applications and dynamically interpreted executables before packaging."""
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
        """Report invalid caller applications without silently substituting the packaged executable."""
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
        """Reject startup scripts without a shebang or with CRLF line endings before running tools."""
        for data in (b'exec /bin/enclave_app\n', b'#!/bin/sh\r\nexec /bin/enclave_app\r\n'):
            with self.subTest(data=data):
                self.inputs.init.write_bytes(data)
                with self.assertRaisesRegex(builder.IgvmBuildError, 'shebang script with LF'):
                    self.inputs.build()
        self.run.assert_not_called()

    def test_protected_bundle_and_missing_tools(self) -> None:
        """Reject workspaces overlapping bundle inputs and report missing shared packaging tools."""
        self.inputs.workspace = self.inputs.bundle / 'work'
        with self.assertRaisesRegex(builder.IgvmBuildError, 'overlaps a protected input'):
            self.inputs.build()
        self.inputs.workspace = self.inputs.root / 'work'
        with patch.object(builder.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(builder.IgvmBuildError, 'Required command not found: cpio'):
                self.inputs.build()
        self.run.assert_not_called()

    def test_non_linux_failure_is_a_cli_error(self) -> None:
        """Translate unsupported host-platform packaging into an actionable CLI error before tool execution."""
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
        """Exercise real cpio/gzip packaging and generator subprocess invocation with a stand-in executable."""
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
