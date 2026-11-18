# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Load packaged isolation configuration using type-specific manifest parsers.

Only the SNP manifest format is currently implemented. Future isolation types
can define their own manifest structure and parser.
"""

from dataclasses import dataclass
from enum import Enum
from importlib import resources
import json
from pathlib import PurePosixPath
from typing import Tuple, Union


class IsolationType(str, Enum):
    """Isolation types recognized by the extension, using their manifest values."""

    SNP = 'snp'

    def __str__(self) -> str:
        """Preserve the manifest value in diagnostics and formatted output."""
        return self.value


_ENABLED_MANIFESTS = {IsolationType.SNP: 'manifests/snp.json'}
_SNP_ARTIFACTS = {'kernel', 'base_initrd', 'snp_bootshim', 'igvmfilegen', 'igvm_manifest'}


@dataclass(frozen=True)
class SupportedConfiguration:
    """Guest contract implemented by this extension."""

    architecture: str
    boot_mode: str
    device_layout: str


@dataclass(frozen=True)
class DependencyBundle:
    """Bundle index contract and mandatory packaging inputs."""

    schema_version: int
    profile: str
    required_artifacts: Tuple[str, ...]


@dataclass(frozen=True)
class ResourceDefaults:
    """Launch resources, in processors and MiB; create uses the bundle's IGVM recipe."""

    processor_count: int
    memory_mib: int


@dataclass(frozen=True)
class ImageConfiguration:
    """Image metadata; create continues to use the bundle's IGVM recipe."""

    command_line: str


@dataclass(frozen=True)
class RuntimeConfiguration:
    """Backend and existing runtime feature switches."""

    backend: str
    disable_vmbus: bool
    disable_hv: bool


# The attribute count reflects one cohesive PCIe schema, not multiple responsibilities.
@dataclass(frozen=True)
class PcieConfiguration:  # pylint: disable=too-many-instance-attributes
    """Measured PCIe address map, with byte-valued addresses and extents."""

    segment: int
    start_bus: int
    end_bus: int
    ecam_base: int
    low_mmio_base: int
    low_mmio_size: int
    high_mmio_base: int
    high_mmio_size: int


# This immutable manifest record already groups related settings into nested dataclasses.
@dataclass(frozen=True)
class IsolationConfiguration:  # pylint: disable=too-many-instance-attributes
    """Validated support/dependency contracts and launch settings, not a generator recipe.

    Manifest loading validates this metadata. Create uses its support/dependency contracts,
    but takes image resource and PCIe settings from the bundle's IGVM recipe.
    """

    schema_version: int
    isolation_type: IsolationType
    supported_configuration: SupportedConfiguration
    dependency_bundle: DependencyBundle
    defaults: ResourceDefaults
    image: ImageConfiguration
    runtime: RuntimeConfiguration
    serial_port: int
    pcie: PcieConfiguration
    character_devices: Tuple[str, ...]


def validate_configuration(configuration: IsolationConfiguration) -> None:
    """Validate backend requirements and the type-specific contract independently of operation support."""
    _validate_backend(configuration.runtime.backend, configuration.character_devices)
    if configuration.isolation_type == IsolationType.SNP:
        _validate_snp_configuration(configuration)
    else:
        raise ValueError('Unsupported isolation configuration type: {}'.format(configuration.isolation_type))


def _validate_backend(backend: str, character_devices: Tuple[str, ...]) -> None:
    """Reject unsupported backends or missing device declarations required by the backend."""
    if backend == 'mshv':
        if '/dev/mshv' not in character_devices:
            raise ValueError('Invalid isolation manifest host character devices for mshv')
    else:
        raise ValueError('Unsupported runtime backend: {}'.format(backend))


def _validate_snp_configuration(configuration: IsolationConfiguration) -> None:
    """Require the SNP contracts, resource bounds, and consistent device settings."""
    _validate_integer_range(configuration.schema_version, 1, 1, 'schema_version')
    _validate_integer_range(configuration.dependency_bundle.schema_version, 1, 1, 'dependency_bundle.schema_version')
    if configuration.supported_configuration != SupportedConfiguration(
            architecture='x86_64', boot_mode='linux_direct', device_layout='measured-pcie-v1'):
        raise ValueError('Unsupported isolation configuration; only SNP Linux-direct on mshv is implemented')
    _validate_strings(configuration.dependency_bundle.required_artifacts, 'dependency_bundle.required_artifacts')
    if (set(configuration.dependency_bundle.required_artifacts) != _SNP_ARTIFACTS
            or configuration.dependency_bundle.profile != configuration.supported_configuration.device_layout):
        raise ValueError('Invalid isolation manifest dependency declarations for the SNP implementation')
    _validate_integer_range(configuration.defaults.processor_count, 1, 0xffffffff, 'defaults.processor_count')
    _validate_integer_range(configuration.defaults.memory_mib, 1, 0xffffffffffffffff // (1024 * 1024),
                            'defaults.memory_mib')
    _validate_integer_range(configuration.serial_port, 0, 3, 'devices.serial.port')
    _validate_snp_pcie(configuration.pcie)
    _validate_strings(configuration.character_devices, 'host_requirements.character_devices')
    if any(not path.startswith('/dev/') or str(PurePosixPath(path)) != path
           or '..' in PurePosixPath(path).parts for path in configuration.character_devices):
        raise ValueError('Invalid isolation manifest host character device paths')


def _validate_integer_range(value: int, minimum: int, maximum: int, location: str) -> None:
    """Require an inclusive numeric bound on a parsed integer."""
    if not minimum <= value <= maximum:
        raise ValueError('Invalid isolation manifest {}: expected integer in [{}, {}]'.format(
            location, minimum, maximum))


def _validate_strings(values: Tuple[str, ...], location: str) -> None:
    """Require a nonempty collection of unique nonempty parsed strings."""
    if not values or any(not item for item in values) or len(set(values)) != len(values):
        raise ValueError('Invalid isolation manifest {}: expected unique nonempty strings'.format(location))


def _validate_snp_pcie(pcie: PcieConfiguration) -> None:
    """Reject out-of-range, unaligned, or overlapping measured PCIe addresses."""
    for key, value in vars(pcie).items():
        if key == 'segment':
            maximum = 0xffff
        elif key in ('start_bus', 'end_bus'):
            maximum = 0xff
        else:
            maximum = 0xffffffffffffffff
        _validate_integer_range(value, 0, maximum, 'devices.pcie.' + key)
    if pcie.start_bus > pcie.end_bus:
        raise ValueError('Invalid isolation manifest PCIe bus range')
    ranges = (
        (pcie.ecam_base, (pcie.end_bus - pcie.start_bus + 1) * 1024 * 1024, 1 << 32),
        (pcie.low_mmio_base, pcie.low_mmio_size, 1 << 32),
        (pcie.high_mmio_base, pcie.high_mmio_size, 1 << 64),
    )
    if (pcie.ecam_base % (1024 * 1024) or pcie.high_mmio_base < 1 << 32
            or any(not size or base % 4096 or size % 4096 or base + size > limit
                   for base, size, limit in ranges)):
        raise ValueError('Invalid isolation manifest PCIe address range or alignment')
    for index, (base, size, _) in enumerate(ranges):
        if any(base < other + extent and other < base + size for other, extent, _ in ranges[index + 1:]):
            raise ValueError('Contradictory isolation manifest PCIe address ranges overlap')


def _object(value: object, fields: str, location: str) -> dict:
    """Require exactly the declared manifest fields, rejecting missing and unknown settings."""
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ValueError('Invalid isolation manifest {}: expected fields {}'.format(location, fields))
    return value


def _integer(value: object, location: str) -> int:
    """Parse an integer without accepting booleans as integers."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError('Invalid isolation manifest {}: expected an integer'.format(location))
    return value


def _strings(value: object, location: str) -> Tuple[str, ...]:
    """Parse a manifest list of strings as an immutable tuple."""
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError('Invalid isolation manifest {}: expected a list of strings'.format(location))
    return tuple(value)


def _parse_snp_pcie(document: object) -> PcieConfiguration:
    """Parse the SNP measured PCIe layout's fields and integer types."""
    pcie = _object(document, 'segment start_bus end_bus ecam_base low_mmio_base low_mmio_size '
                   'high_mmio_base high_mmio_size', 'devices.pcie')
    return PcieConfiguration(**{key: _integer(value, 'devices.pcie.' + key) for key, value in pcie.items()})


def _parse_snp_manifest(document: object) -> IsolationConfiguration:
    """Parse SNP schema 1; its JSON structure is not a contract for other isolation types."""
    root = _object(document, 'schema_version isolation_type supported_configuration dependency_bundle '
                   'defaults image runtime devices host_requirements', 'root')
    schema_version = _integer(root['schema_version'], 'schema_version')
    if root['isolation_type'] != IsolationType.SNP:
        raise ValueError('Isolation manifest type does not match snp')
    supported = SupportedConfiguration(**_object(
        root['supported_configuration'], 'architecture boot_mode device_layout', 'supported_configuration'))
    bundle = _object(root['dependency_bundle'], 'schema_version profile required_artifacts', 'dependency_bundle')
    bundle_version = _integer(bundle['schema_version'], 'dependency_bundle.schema_version')
    required = _strings(bundle['required_artifacts'], 'dependency_bundle.required_artifacts')
    defaults = _object(root['defaults'], 'processor_count memory_mib', 'defaults')
    cpu = _integer(defaults['processor_count'], 'defaults.processor_count')
    memory = _integer(defaults['memory_mib'], 'defaults.memory_mib')
    image = ImageConfiguration(**_object(root['image'], 'command_line', 'image'))
    if not isinstance(image.command_line, str):
        raise ValueError('Invalid isolation manifest image.command_line: expected a string')
    runtime = _object(root['runtime'], 'backend disable_vmbus disable_hv', 'runtime')
    if any(not isinstance(runtime[key], bool) for key in ('disable_vmbus', 'disable_hv')):
        raise ValueError('Invalid isolation manifest runtime switches: expected booleans')
    devices = _object(root['devices'], 'serial pcie', 'devices')
    serial = _object(devices['serial'], 'port', 'devices.serial')
    serial_port = _integer(serial['port'], 'devices.serial.port')
    pcie = _parse_snp_pcie(devices['pcie'])
    host = _object(root['host_requirements'], 'character_devices', 'host_requirements')
    character_devices = _strings(host['character_devices'], 'host_requirements.character_devices')
    return IsolationConfiguration(
        schema_version=schema_version,
        isolation_type=IsolationType(root['isolation_type']),
        supported_configuration=supported,
        dependency_bundle=DependencyBundle(
            schema_version=bundle_version, profile=bundle['profile'], required_artifacts=required),
        defaults=ResourceDefaults(processor_count=cpu, memory_mib=memory),
        image=image,
        runtime=RuntimeConfiguration(**runtime),
        serial_port=serial_port,
        pcie=pcie,
        character_devices=character_devices)


def parse_manifest(document: object, isolation_type: Union[str, IsolationType]) -> IsolationConfiguration:
    """Parse and validate the selected type without assuming a shared manifest format."""
    if isolation_type not in _ENABLED_MANIFESTS:
        raise ValueError('Isolation type is not enabled: {}'.format(isolation_type))
    if isolation_type == IsolationType.SNP:
        configuration = _parse_snp_manifest(document)
    else:
        raise ValueError('Unsupported isolation configuration type: {}'.format(isolation_type))
    validate_configuration(configuration)
    return configuration


def load_isolation(isolation_type: Union[str, IsolationType] = IsolationType.SNP) -> IsolationConfiguration:
    """Read only explicitly enabled, installed package resources, without discovery or downloads."""
    if isolation_type not in _ENABLED_MANIFESTS:
        raise ValueError('Isolation type is not enabled: {}'.format(isolation_type))
    try:
        resource = resources.files('azext_enclave').joinpath(_ENABLED_MANIFESTS[isolation_type])
        with resource.open('r', encoding='utf-8') as source:
            document = json.load(source)
        return parse_manifest(document, isolation_type)
    except (OSError, ValueError) as error:
        raise ValueError('Unable to load packaged {} isolation manifest: {}'.format(isolation_type, error)) from error
