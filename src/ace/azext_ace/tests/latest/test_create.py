# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Exercise CLI image creation and the packaged builder without real SNP dependencies."""

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
import unittest
from unittest.mock import patch

from azure.cli.core import AzCli, MainCommandsLoader, get_default_cli
from azure.cli.core._help import AzCliHelp
from azure.cli.core._output import AzOutputProducer
from azure.cli.core.azlogging import AzCliLogging
from azure.cli.core.commands import AzCliCommandInvoker
from azure.cli.core.parser import AzCliCommandParser
from knack.util import CLIError

from azext_ace import AceCommandsLoader
from azext_ace import _image_builder as builder
from azext_ace import custom


class AceTestCommandsLoader(MainCommandsLoader):
    """Load only ACE through the normal Azure CLI main-loader argument wiring."""

    def load_command_table(self, args: list) -> dict:
        loader = AceCommandsLoader(cli_ctx=self.cli_ctx)
        self.command_table = loader.load_command_table(args)
        self.cmd_to_loader_map = {name: [loader] for name in self.command_table}
        return self.command_table


class CreateCommandTest(unittest.TestCase):
    """Check CLI conversion, logging, result shape, and error translation."""

    def setUp(self) -> None:
        self.cmd = SimpleNamespace(cli_ctx=SimpleNamespace(data={'safe_params': []}))
        self.measurements = {'snp': 'AB' * 48}

    def test_default_delegation(self) -> None:
        image = Path('published.igvm').resolve()
        with patch.object(builder, 'build_igvm',
                          return_value=builder.IgvmBuildResult(image, self.measurements)) as build:
            result = custom.create_ace(self.cmd, 'work', 'image.igvm', dependency_bundle='bundle')
        self.assertEqual(result, {'igvm': str(image), 'measurements': self.measurements})
        self.assertEqual(json.loads(json.dumps(result)), result)
        build.assert_called_once_with(
            dependency_bundle=Path('bundle'), application=custom.DEFAULT_APPLICATION,
            init_script=custom.DEPENDENCIES_DIR / 'initramfs' / 'skr-bvt-init', workspace=Path('work'),
            output=Path('image.igvm'), signing_key=None, debug=False, enable_debug=False,
            logger=custom.logger)

    def test_signing_and_retention(self) -> None:
        with patch.object(builder, 'build_igvm',
                          return_value=builder.IgvmBuildResult(Path('signed.igvm'), self.measurements)) as build:
            custom.create_ace(self.cmd, 'work', 'signed.igvm', dependency_bundle='bundle', init_script='init',
                              signing_key='private.pem', keep_intermediates=True)
        self.assertEqual(build.call_args.kwargs['signing_key'], Path('private.pem'))
        self.assertEqual(build.call_args.kwargs['init_script'], Path('init'))
        self.assertIs(build.call_args.kwargs['debug'], True)
        self.assertIs(build.call_args.kwargs['enable_debug'], False)

    def test_global_debug_flag_through_cli_invocation(self) -> None:
        for flags, enable_debug, retain in (
                ([], False, False),
                (['--verbose'], False, False),
                (['--debug'], True, False),
                (['--keep-intermediates'], False, True),
                (['--debug', '--keep-intermediates'], True, True)):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as directory:
                cli = AzCli(cli_name='az', config_dir=directory, config_env_var_prefix='AZURE',
                            commands_loader_cls=AceTestCommandsLoader,
                            invocation_cls=AzCliCommandInvoker, parser_cls=AzCliCommandParser,
                            help_cls=AzCliHelp, logging_cls=AzCliLogging, output_cls=AzOutputProducer)
                output = io.StringIO()
                packaged = Path(directory) / 'dependency-bundle.tar.gz'
                packaged.write_bytes(b'archive selection fixture')
                bundle_args = ['--dependency-bundle', 'bundle'] if flags else []
                with patch.object(custom, 'DEFAULT_DEPENDENCY_BUNDLE', packaged), \
                        patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                        Path('image.igvm'), self.measurements)) as build:
                    code = cli.invoke(
                        ['ace', 'create', '--workspace', 'work',
                         '--output-file', 'image.igvm', '--output', 'json'] + bundle_args + flags,
                        out_file=output)
                self.assertEqual(code, 0, str(cli.result.error))
                self.assertEqual(json.loads(output.getvalue()),
                                 {'igvm': 'image.igvm', 'measurements': self.measurements})
                self.assertIs(build.call_args.kwargs['enable_debug'], enable_debug)
                self.assertIs(build.call_args.kwargs['debug'], retain)
                self.assertEqual(build.call_args.kwargs['dependency_bundle'],
                                 Path('bundle') if flags else packaged)
                self.assertEqual(build.call_args.kwargs['application'], custom.DEFAULT_APPLICATION)

    def test_build_errors_include_stage_and_retained_workspace(self) -> None:
        for workspace in (None, Path('retained')):
            with self.subTest(workspace=workspace):
                error = builder.IgvmBuildError('tool failed', stage='generate-igvm', workspace=workspace)
                with patch.object(builder, 'build_igvm', side_effect=error):
                    with self.assertRaises(CLIError) as raised:
                        custom.create_ace(self.cmd, 'work', 'image.igvm', dependency_bundle='bundle')
                self.assertIn('generate-igvm: tool failed', str(raised.exception))
                self.assertIs(raised.exception.__cause__, error)
                self.assertEqual('Build workspace retained at:' in str(raised.exception),
                                 workspace is not None)

    def test_required_inputs_and_nonconflicting_options(self) -> None:
        cli = get_default_cli()
        cli.invocation = SimpleNamespace(data={'command_string': 'ace create'})
        loader = AceCommandsLoader(cli_ctx=cli)
        table = loader.load_command_table([])
        loader.load_arguments('ace create')
        table['ace create'].load_arguments()
        arguments = table['ace create'].arguments
        required = {'workspace', 'output_file'}
        self.assertEqual({name for name, arg in arguments.items()
                          if arg.options.get('required') and name != 'cmd'}, required)
        self.assertEqual(arguments['output_file'].options_list, ['--output-file'])
        self.assertEqual(arguments['keep_intermediates'].options_list, ['--keep-intermediates'])
        self.assertEqual(arguments['keep_intermediates'].options['action'], 'store_true')
        self.assertIs(arguments['keep_intermediates'].options['default'], False)
        self.assertIsNone(arguments['signing_key'].options['default'])
        self.assertIsNone(arguments['init_script'].options['default'])
        self.assertIsNone(arguments['dependency_bundle'].options['default'])
        self.assertNotIn('application', arguments)
        default_init = custom.DEPENDENCIES_DIR / 'initramfs' / 'skr-bvt-init'
        script = default_init.read_bytes()
        self.assertTrue(script.startswith(b'#!/bin/sh\n'))
        self.assertNotIn(b'\r', script)
        self.assertIn(b'exec /bin/enclave_app serve-vsock 4050\n', script)
        with patch.object(builder, 'build_igvm',
                          return_value=builder.IgvmBuildResult(Path('image.igvm'), self.measurements)) as build:
            custom.create_ace(self.cmd, 'work', 'image.igvm', dependency_bundle='bundle')
        self.assertEqual(build.call_args.kwargs['init_script'], default_init)

    def test_packaged_bundle_is_selected_when_override_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'dependency-bundle.tar.gz'
            archive.write_bytes(b'archive selection is validated by the builder')
            with patch.object(custom, 'DEFAULT_DEPENDENCY_BUNDLE', archive), \
                    patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                        Path('image.igvm'), self.measurements)) as build:
                custom.create_ace(self.cmd, 'work', 'image.igvm')
            self.assertEqual(build.call_args.kwargs['dependency_bundle'], archive)

    def test_missing_packaged_bundle_has_actionable_override_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'dependency-bundle.tar.gz'
            with patch.object(custom, 'DEFAULT_DEPENDENCY_BUNDLE', archive), \
                    patch.object(builder, 'build_igvm') as build:
                with self.assertRaisesRegex(CLIError, 'missing.*--dependency-bundle'):
                    custom.create_ace(self.cmd, 'work', 'image.igvm')
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
            custom.create_ace(self.cmd, 'work', 'image.igvm')
        self.assertEqual(build.call_args.kwargs['dependency_bundle'], custom.DEFAULT_DEPENDENCY_BUNDLE)
        self.assertEqual(build.call_args.kwargs['application'], custom.DEFAULT_APPLICATION)

    def test_application_override_is_not_a_cli_option(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cli = AzCli(cli_name='az', config_dir=directory, config_env_var_prefix='AZURE',
                        commands_loader_cls=AceTestCommandsLoader,
                        invocation_cls=AzCliCommandInvoker, parser_cls=AzCliCommandParser,
                        help_cls=AzCliHelp, logging_cls=AzCliLogging, output_cls=AzOutputProducer)
            with patch.object(builder, 'build_igvm') as build, self.assertRaises(SystemExit) as raised:
                cli.invoke(['ace', 'create', '--workspace', 'work', '--output-file', 'image.igvm',
                            '--application', 'custom-app'], out_file=io.StringIO())
            self.assertEqual(raised.exception.code, 2)
            build.assert_not_called()

    def test_archive_override_is_forwarded_without_requiring_packaged_bundle(self) -> None:
        with patch.object(builder, 'build_igvm', return_value=builder.IgvmBuildResult(
                Path('image.igvm'), self.measurements)) as build:
            custom.create_ace(self.cmd, 'work', 'image.igvm', dependency_bundle='custom.tar.gz')
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


class ImageBuilderTest(unittest.TestCase):
    """Use real files and ELF/manifest validation, replacing only external build commands."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
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
        self.stages = []
        self.patchers = [
            patch.object(builder.sys, 'platform', 'linux'),
            patch.object(builder.shutil, 'which', return_value='/usr/bin/tool'),
            patch.dict(os.environ, {'SOURCE_DATE_EPOCH': '123'}),
        ]
        if os.utime not in os.supports_follow_symlinks:
            native_utime = os.utime
            self.patchers.append(patch.object(
                builder.os, 'utime', side_effect=lambda path, times, **kwargs: native_utime(path, times)))
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.run_patcher = patch.object(builder, '_run', side_effect=self.run_stage)
        self.run = self.run_patcher.start()
        self.addCleanup(self.run_patcher.stop)

    def write_manifest(self) -> None:
        (self.bundle / 'dependency-manifest.v1.json').write_text(
            json.dumps(self.manifest), encoding='utf-8')

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
            dependency_root = (self.bundle if self.bundle.is_dir()
                               else work / 'dependencies' / self.archive_prefix)
            self.assertEqual(resources, {
                'linux_kernel': str(dependency_root / 'kernel'),
                'linux_initrd': str(work / 'application-initrd.cpio.gz'),
                'snp_bootshim': str(dependency_root / 'snp_bootshim'),
            })
            (work / 'image.igvm').write_bytes(b'unsigned image')
            (work / 'image-snp.idblock').write_bytes(b'id block')
            (work / 'image-snp.json').write_text(json.dumps(self.measurement_document), encoding='utf-8')
        elif stage == 'export-id-public-key':
            Path(argv[argv.index('-out') + 1]).write_bytes(b'public key')
        elif stage == 'sign-id-block':
            Path(argv[argv.index('-out') + 1]).write_bytes(b'signature')
        elif stage == 'add-snp-id-block':
            Path(argv[argv.index('--output') + 1]).write_bytes(b'signed image')
        else:
            self.fail('Unexpected stage: ' + stage)

    def build(self, **kwargs) -> builder.IgvmBuildResult:
        return builder.build_igvm(
            dependency_bundle=self.bundle, application=self.application,
            init_script=self.init, workspace=self.workspace, output=self.output, **kwargs)

    def test_unsigned_pipeline_and_cleanup(self) -> None:
        result = self.build()
        self.assertEqual(result.igvm, self.output)
        self.assertEqual(result.measurements, {'snp': self.measurement})
        self.assertEqual(self.output.read_bytes(), b'unsigned image')
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])
        self.assertEqual(self.stages, [
            'unpack-gzip', 'unpack-cpio', 'pack-cpio', 'pack-gzip', 'generate-igvm'])
        self.assertEqual(self.application.read_bytes()[:4], b'\x7fELF')
        self.assertEqual((self.bundle / 'base_initrd').read_bytes(), b'base-initrd')

    def test_archive_bundles_build_and_follow_workspace_retention(self) -> None:
        directory = self.bundle
        for prefix, retain in (('', False), ('bundle', True)):
            with self.subTest(prefix=prefix, retain=retain):
                archive_path = self.root / 'dependencies.tar.gz'
                with tarfile.open(archive_path, 'w:gz') as archive:
                    archive.add(directory, arcname=prefix or '.')
                self.bundle = archive_path
                self.archive_prefix = prefix
                result = self.build(debug=retain)
                self.assertEqual(result.measurements, {'snp': self.measurement})
                self.assertEqual(result.igvm, self.output)
                if retain:
                    work, = self.workspace.iterdir()
                    self.assertTrue((work / 'dependencies' / prefix / 'dependency-manifest.v1.json').is_file())
                else:
                    self.assertEqual(list(self.workspace.iterdir()), [])
                self.output.unlink()

    def test_corrupt_archive_fails_before_build_and_retains_debug_workspace(self) -> None:
        archive_path = self.root / 'broken.tar.gz'
        archive_path.write_bytes(b'not an archive')
        self.bundle = archive_path
        for retain in (False, True):
            with self.subTest(retain=retain):
                with self.assertRaises(builder.IgvmBuildError) as raised:
                    self.build(debug=retain)
                self.assertEqual(raised.exception.stage, 'extract-dependencies')
                self.assertFalse(self.output.exists())
                if retain:
                    self.assertTrue(raised.exception.workspace.is_dir())
                else:
                    self.assertEqual(list(self.workspace.iterdir()), [])
        self.run.assert_not_called()

    def test_archive_artifact_checksums_are_still_required(self) -> None:
        (self.bundle / 'kernel').write_bytes(b'tampered kernel')
        archive_path = self.root / 'tampered.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            archive.add(self.bundle, arcname='.')
        self.bundle = archive_path
        with self.assertRaisesRegex(builder.IgvmBuildError, 'validate-dependencies:.*checksum mismatch'):
            self.build()
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.run.assert_not_called()

    def test_retains_composed_rootfs_and_normalized_metadata(self) -> None:
        self.build(debug=True)
        work, = self.workspace.iterdir()
        root = work / 'rootfs'
        self.assertEqual((root / 'bin' / 'enclave_app').read_bytes(), self.application.read_bytes())
        self.assertEqual((root / 'init').read_bytes(), self.init.read_bytes())
        self.assertEqual((root / 'base-file').read_bytes(), b'preserved')
        self.assertFalse((root / 'bin' / 'appliance').exists())
        self.assertFalse((root / 'appliance').exists())
        self.assertEqual(int((root / 'init').stat().st_mtime), 123)
        paths = (work / 'archive-files').read_bytes().split(b'\0')[:-1]
        self.assertEqual(paths, sorted(paths))
        self.assertTrue((work / 'generate-igvm.log').is_file())

    def test_signed_image_only_and_private_key_not_copied(self) -> None:
        result = self.build(signing_key=self.key, debug=True)
        self.assertEqual(result.measurements, {'snp': self.measurement})
        self.assertEqual(self.output.read_bytes(), b'signed image')
        work, = self.workspace.iterdir()
        self.assertEqual(self.stages[-3:], [
            'export-id-public-key', 'sign-id-block', 'add-snp-id-block'])
        self.assertEqual((work / 'id-public-key.pem').read_bytes(), b'public key')
        self.assertFalse(any(path.is_file() and path.read_bytes() == self.key.read_bytes()
                             for path in work.rglob('*')))
        self.assertEqual(self.key.read_bytes(), b'caller-owned-test-key')

    def test_guest_debug_is_independent_of_retention_and_bundle(self) -> None:
        original = (self.bundle / 'igvm_manifest').read_bytes()
        for enable_debug in (False, True):
            with self.subTest(enable_debug=enable_debug):
                previous = set(self.workspace.iterdir()) if self.workspace.exists() else set()
                result = self.build(enable_debug=enable_debug, debug=True)
                work, = set(self.workspace.iterdir()) - previous
                config = json.loads((work / 'igvm-manifest.json').read_text(encoding='utf-8'))
                self.assertIs(config['guest_configs'][0]['isolation_type']['snp']['enable_debug'],
                              enable_debug)
                self.assertEqual((self.bundle / 'igvm_manifest').read_bytes(), original)
                self.assertEqual(result.measurements, {'snp': self.measurement})
                self.output.unlink()

    def test_default_disables_guest_debug_from_bundle(self) -> None:
        self.build(debug=True)
        work, = self.workspace.iterdir()
        config = json.loads((work / 'igvm-manifest.json').read_text(encoding='utf-8'))
        self.assertIs(config['guest_configs'][0]['isolation_type']['snp']['enable_debug'], False)

    def test_invalid_measurements_fail_before_publication(self) -> None:
        for document in (
                {}, {'series': []}, {'series': [None]}, {'series': [{}, {}]},
                {'series': [{'reference': None}]},
                {'series': [{'reference': {'snp_ld': None}}]},
                {'series': [{'reference': {'snp_ld': 'AB' * 47}}]},
                {'series': [{'reference': {'snp_ld': 'ZZ' * 48}}]},
                {'series': [{'reference': {'snp_ld': 'AB' * 47 + '  '}}]}):
            with self.subTest(document=document):
                self.measurement_document = document
                with self.assertRaises(builder.IgvmBuildError) as raised:
                    self.build()
                self.assertEqual(raised.exception.stage, 'read-measurements')
                self.assertFalse(self.output.exists())
                self.assertEqual(list(self.workspace.iterdir()), [])

    def test_missing_or_invalid_json_measurements_retain_evidence(self) -> None:
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
                    self.build(debug=True)
                self.assertEqual(raised.exception.stage, 'read-measurements')
                self.assertTrue(raised.exception.workspace.is_dir())
                self.assertFalse(self.output.exists())

    def test_failure_cleanup_and_retention(self) -> None:
        for debug in (False, True):
            with self.subTest(debug=debug):
                self.run.side_effect = builder.IgvmBuildError('generator failed', stage='generate-igvm')
                with self.assertRaises(builder.IgvmBuildError) as raised:
                    self.build(debug=debug)
                self.assertFalse(self.output.exists())
                self.assertEqual(raised.exception.stage, 'generate-igvm')
                if debug:
                    self.assertTrue(raised.exception.workspace.is_dir())
                else:
                    self.assertIsNone(raised.exception.workspace)
                    self.assertEqual(list(self.workspace.iterdir()), [])

    def test_signing_failure_never_publishes_unsigned_image(self) -> None:
        def fail_signing(argv, stage, work, **kwargs) -> None:
            if stage == 'sign-id-block':
                raise builder.IgvmBuildError('signing failed', stage=stage)
            self.run_stage(argv, stage, work, **kwargs)

        self.run.side_effect = fail_signing
        with self.assertRaisesRegex(builder.IgvmBuildError, 'sign-id-block: signing failed'):
            self.build(signing_key=self.key)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_existing_output_is_preserved(self) -> None:
        self.output.parent.mkdir()
        self.output.write_bytes(b'existing')
        with self.assertRaisesRegex(builder.IgvmBuildError, 'Output already exists'):
            self.build()
        self.assertEqual(self.output.read_bytes(), b'existing')
        self.run.assert_not_called()

    def test_checksums_and_artifact_paths_are_validated(self) -> None:
        (self.bundle / 'kernel').write_bytes(b'modified')
        with self.assertRaisesRegex(builder.IgvmBuildError, 'checksum mismatch: kernel'):
            self.build()
        self.manifest['artifacts']['kernel']['path'] = '../application'
        self.write_manifest()
        with self.assertRaisesRegex(builder.IgvmBuildError, 'must be bundle-relative'):
            self.build()
        self.run.assert_not_called()

    def test_profile_and_measured_pcie_are_required(self) -> None:
        self.manifest['profile'] = 'other'
        self.write_manifest()
        with self.assertRaisesRegex(builder.IgvmBuildError, 'measured-pcie-v1'):
            self.build()
        self.manifest['profile'] = 'measured-pcie-v1'
        config = self.bundle / 'igvm_manifest'
        config.write_bytes(b'{"guest_arch":"x64","guest_configs":[{"image":{"snp_linux_direct":{}}}]}')
        self.manifest['artifacts']['igvm_manifest']['sha256'] = hashlib.sha256(config.read_bytes()).hexdigest()
        self.write_manifest()
        with self.assertRaisesRegex(builder.IgvmBuildError, 'missing measured PCIe'):
            self.build()
        self.run.assert_not_called()

    def test_application_requires_static_x64_elf(self) -> None:
        original = self.application.read_bytes()
        for data, message in (
                (b'not an executable', 'ELF64'),
                (original[:64] + struct.pack('<IIQQQQQQ', 3, 0, 0, 0, 0, 0, 0, 0), 'PT_INTERP')):
            with self.subTest(message=message):
                self.application.write_bytes(data)
                with self.assertRaisesRegex(builder.IgvmBuildError, message):
                    self.build()
        self.run.assert_not_called()

    def test_init_requires_shebang_and_unix_line_endings(self) -> None:
        for data in (b'exec /bin/enclave_app\n', b'#!/bin/sh\r\nexec /bin/enclave_app\r\n'):
            with self.subTest(data=data):
                self.init.write_bytes(data)
                with self.assertRaisesRegex(builder.IgvmBuildError, 'shebang script with LF'):
                    self.build()
        self.run.assert_not_called()

    def test_protected_bundle_and_missing_tools(self) -> None:
        self.workspace = self.bundle / 'work'
        with self.assertRaisesRegex(builder.IgvmBuildError, 'overlaps a protected input'):
            self.build()
        self.workspace = self.root / 'work'
        with patch.object(builder.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(builder.IgvmBuildError, 'Required command not found: cpio'):
                self.build()
        self.run.assert_not_called()

    def test_non_linux_failure_is_a_cli_error(self) -> None:
        with patch.object(builder.sys, 'platform', 'win32'):
            with self.assertRaisesRegex(CLIError, 'validate: IGVM packaging requires Linux'):
                cmd = SimpleNamespace(cli_ctx=SimpleNamespace(data={'safe_params': []}))
                custom.create_ace(cmd, str(self.workspace), str(self.output),
                                  dependency_bundle=str(self.bundle), init_script=str(self.init))
        self.run.assert_not_called()


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('cpio') and shutil.which('gzip'),
                     'Real initrd packaging requires Linux, GNU cpio, and gzip')
class LinuxPackagingTest(unittest.TestCase):
    """Exercise real archive tools and subprocess execution with a stand-in generator."""

    def test_real_packaging_and_generator_execution(self) -> None:
        fixture = ImageBuilderTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.run_patcher.stop()
        for patcher in fixture.patchers:
            patcher.stop()
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
