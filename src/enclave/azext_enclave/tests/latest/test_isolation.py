# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Validate packaged isolation metadata without accepting customer paths or enabling other types."""

from copy import deepcopy
from dataclasses import replace
from importlib import resources
import io
import json
import unittest
from unittest.mock import patch

from azext_enclave import _isolation


class IsolationManifestTest(unittest.TestCase):
    """Reject malformed metadata and unsupported implementation contracts."""

    def setUp(self) -> None:
        self.document = json.loads(resources.files('azext_enclave').joinpath('manifests/snp.json').read_text())

    def test_installed_resource_lookup_is_explicit(self) -> None:
        """Load the enabled packaged manifest while rejecting unknown types and caller-supplied paths."""
        configuration = _isolation.load_isolation()
        self.assertEqual(configuration.isolation_type, 'snp')
        self.assertEqual(configuration.dependency_bundle.profile, 'measured-pcie-v1')
        self.assertEqual(configuration.image.command_line, self.document['image']['command_line'])
        with patch.object(_isolation.resources, 'files') as lookup:
            for value in ('tdx', 'native', '../snp', '/tmp/snp.json', ''):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'not enabled'):
                    _isolation.load_isolation(value)
            lookup.assert_not_called()

    def test_missing_and_malformed_packaged_resources_fail_explicitly(self) -> None:
        """Report missing or invalid packaged JSON without falling back to another configuration."""
        with patch.object(_isolation.resources, 'files') as lookup:
            resource = lookup.return_value.joinpath.return_value
            resource.open.side_effect = FileNotFoundError('missing resource')
            with self.assertRaisesRegex(ValueError, 'packaged snp isolation manifest.*missing resource'):
                _isolation.load_isolation()
            resource.open.side_effect = None
            resource.open.return_value = io.StringIO('{bad json')
            with self.assertRaisesRegex(ValueError, 'packaged snp isolation manifest'):
                _isolation.load_isolation()

    def test_registration_alone_does_not_implement_a_manifest_contract(self) -> None:
        """Do not impose SNP's schema on a registered type that has no parser."""
        document = {'different_manifest_format': True}
        with patch.dict(_isolation._ENABLED_MANIFESTS, {'tdx': 'manifests/tdx.json'}), \
                patch.object(_isolation, '_parse_snp_manifest') as snp_parser:
            with self.assertRaisesRegex(ValueError, 'Unsupported isolation configuration type: tdx'):
                _isolation.parse_manifest(document, 'tdx')
            snp_parser.assert_not_called()

    def test_backend_requirements_do_not_depend_on_the_isolation_validator(self) -> None:
        """Reject unsupported backends and missing backend devices before isolation-specific validation."""
        configuration = _isolation.load_isolation()
        for unsupported, message in (
                (replace(configuration, character_devices=('/dev/other',)), 'host character devices for mshv'),
                (replace(configuration, runtime=replace(configuration.runtime, backend='kvm')),
                 'Unsupported runtime backend: kvm')):
            with self.subTest(configuration=unsupported), \
                    patch.object(_isolation, '_validate_snp_configuration') as snp_configuration:
                with self.assertRaisesRegex(ValueError, message):
                    _isolation.validate_configuration(unsupported)
                snp_configuration.assert_not_called()

    def test_every_field_is_required(self) -> None:
        """Reject removal of any declared manifest field rather than supplying silent defaults."""
        def paths(value, prefix=()):
            for key, child in value.items():
                yield prefix + (key,)
                if isinstance(child, dict):
                    yield from paths(child, prefix + (key,))

        for path in paths(self.document):
            with self.subTest(path=path):
                document = deepcopy(self.document)
                section = document
                for key in path[:-1]:
                    section = section[key]
                del section[path[-1]]
                with self.assertRaises(ValueError):
                    _isolation.parse_manifest(document, 'snp')

    def test_malformed_and_unsupported_values(self) -> None:
        """Reject invalid field types, numeric bounds, device paths, and unsupported platform settings."""
        cases = {
            'schema_version': (None, True, 1.0, 0, 2, '1'),
            'isolation_type': ('tdx', None),
            'supported_configuration': (None, []),
            'supported_configuration.architecture': ('aarch64', None, []),
            'supported_configuration.boot_mode': ('uefi',),
            'supported_configuration.device_layout': ('unmeasured',),
            'dependency_bundle.schema_version': (2, True, 1.0),
            'dependency_bundle.profile': ('other',),
            'dependency_bundle.required_artifacts': (
                [], ['kernel'], ['kernel'] * 5, ['kernel', {}], 'kernel'),
            'defaults.processor_count': (True, 1.0, 0, -1, 1 << 32),
            'defaults.memory_mib': (False, '160', 0, -1, 1 << 64),
            'image': (None, [], 'console=ttyS0'),
            'image.command_line': (None, True, 1, [], {}),
            'runtime.backend': ('kvm', None),
            'runtime.disable_vmbus': (1, 'true', None),
            'runtime.disable_hv': (0, 'false', None),
            'devices.serial.port': (-1, 4, True, 0.0),
            'devices.pcie.segment': (-1, 65536, False),
            'devices.pcie.start_bus': (2, -1, 256),
            'devices.pcie.end_bus': (256, '1'),
            'devices.pcie.ecam_base': (1, 1 << 32, True),
            'devices.pcie.low_mmio_base': (4141875200, 1 << 32),
            'devices.pcie.low_mmio_size': (0, -1, 1, 1 << 32),
            'devices.pcie.high_mmio_base': (0, (1 << 64) - 4096),
            'devices.pcie.high_mmio_size': (0, 1, 1 << 64),
            'host_requirements.character_devices': (
                [], ['/dev/kvm'], ['/dev/mshv', '/dev/mshv'], ['/dev/mshv', '/dev/../etc/passwd'],
                ['/dev/mshv', '/etc/passwd'], ['/dev/mshv', '/dev//other']),
        }
        for path, values in cases.items():
            for value in values:
                with self.subTest(path=path, value=value):
                    document = deepcopy(self.document)
                    section = document
                    keys = path.split('.')
                    for key in keys[:-1]:
                        section = section[key]
                    section[keys[-1]] = value
                    with self.assertRaises(ValueError):
                        _isolation.parse_manifest(document, 'snp')

    def test_kernel_command_line_is_preserved(self) -> None:
        """Allow an empty command line and preserve kernel argument spacing and quotes."""
        for value in ('', 'console=ttyS0  init="/bin/custom init" panic=-1'):
            with self.subTest(value=value):
                self.document['image']['command_line'] = value
                configuration = _isolation.parse_manifest(self.document, 'snp')
                self.assertEqual(configuration.image.command_line, value)

    def test_unknown_fields_are_not_silently_ignored(self) -> None:
        """Reject undeclared manifest fields rather than silently ignoring misspelled settings."""
        self.document['defaults']['memory_mb'] = 160
        with self.assertRaisesRegex(ValueError, 'defaults'):
            _isolation.parse_manifest(self.document, 'snp')

    def test_overlapping_high_and_low_ranges_are_rejected(self) -> None:
        """Reject high-MMIO settings that encroach on the below-4GiB address space."""
        self.document['devices']['pcie']['high_mmio_base'] = 4143972352
        with self.assertRaisesRegex(ValueError, 'PCIe'):
            _isolation.parse_manifest(self.document, 'snp')
