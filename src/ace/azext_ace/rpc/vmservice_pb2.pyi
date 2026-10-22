from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class VmState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    Uninitialized: _ClassVar[VmState]
    Paused: _ClassVar[VmState]
    Running: _ClassVar[VmState]
    Halted: _ClassVar[VmState]

class ModifyType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ADD: _ClassVar[ModifyType]
    REMOVE: _ClassVar[ModifyType]
    UPDATE: _ClassVar[ModifyType]

class DiskType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SCSI_DISK_TYPE_VHD1: _ClassVar[DiskType]
    SCSI_DISK_TYPE_VHDX: _ClassVar[DiskType]
    SCSI_DISK_TYPE_PHYSICAL: _ClassVar[DiskType]

class IpProtocol(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TCP: _ClassVar[IpProtocol]
    UDP: _ClassVar[IpProtocol]
Uninitialized: VmState
Paused: VmState
Running: VmState
Halted: VmState
ADD: ModifyType
REMOVE: ModifyType
UPDATE: ModifyType
SCSI_DISK_TYPE_VHD1: DiskType
SCSI_DISK_TYPE_VHDX: DiskType
SCSI_DISK_TYPE_PHYSICAL: DiskType
TCP: IpProtocol
UDP: IpProtocol

class DirectBoot(_message.Message):
    __slots__ = ("kernel_path", "initrd_path", "kernel_cmdline")
    KERNEL_PATH_FIELD_NUMBER: _ClassVar[int]
    INITRD_PATH_FIELD_NUMBER: _ClassVar[int]
    KERNEL_CMDLINE_FIELD_NUMBER: _ClassVar[int]
    kernel_path: str
    initrd_path: str
    kernel_cmdline: str
    def __init__(self, kernel_path: _Optional[str] = ..., initrd_path: _Optional[str] = ..., kernel_cmdline: _Optional[str] = ...) -> None: ...

class IgvmBoot(_message.Message):
    __slots__ = ("igvm_path", "personality")
    class Personality(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        PERSONALITY_UNSPECIFIED: _ClassVar[IgvmBoot.Personality]
        PERSONALITY_LINUX_DIRECT: _ClassVar[IgvmBoot.Personality]
        PERSONALITY_UEFI: _ClassVar[IgvmBoot.Personality]
    PERSONALITY_UNSPECIFIED: IgvmBoot.Personality
    PERSONALITY_LINUX_DIRECT: IgvmBoot.Personality
    PERSONALITY_UEFI: IgvmBoot.Personality
    IGVM_PATH_FIELD_NUMBER: _ClassVar[int]
    PERSONALITY_FIELD_NUMBER: _ClassVar[int]
    igvm_path: str
    personality: IgvmBoot.Personality
    def __init__(self, igvm_path: _Optional[str] = ..., personality: _Optional[_Union[IgvmBoot.Personality, str]] = ...) -> None: ...

class SnpIsolationConfig(_message.Message):
    __slots__ = ("host_data",)
    HOST_DATA_FIELD_NUMBER: _ClassVar[int]
    host_data: bytes
    def __init__(self, host_data: _Optional[bytes] = ...) -> None: ...

class IsolationConfig(_message.Message):
    __slots__ = ("none", "snp")
    NONE_FIELD_NUMBER: _ClassVar[int]
    SNP_FIELD_NUMBER: _ClassVar[int]
    none: _empty_pb2.Empty
    snp: SnpIsolationConfig
    def __init__(self, none: _Optional[_Union[_empty_pb2.Empty, _Mapping]] = ..., snp: _Optional[_Union[SnpIsolationConfig, _Mapping]] = ...) -> None: ...

class UEFI(_message.Message):
    __slots__ = ("firmware_path", "initial_variables", "secure_boot_enabled")
    class InitialVariables(_message.Message):
        __slots__ = ("secure_boot_template",)
        class SecureBootTemplate(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
            __slots__ = ()
            NONE: _ClassVar[UEFI.InitialVariables.SecureBootTemplate]
            MICROSOFT_WINDOWS: _ClassVar[UEFI.InitialVariables.SecureBootTemplate]
            MICROSOFT_UEFI_CERTIFICATE_AUTHORITY: _ClassVar[UEFI.InitialVariables.SecureBootTemplate]
        NONE: UEFI.InitialVariables.SecureBootTemplate
        MICROSOFT_WINDOWS: UEFI.InitialVariables.SecureBootTemplate
        MICROSOFT_UEFI_CERTIFICATE_AUTHORITY: UEFI.InitialVariables.SecureBootTemplate
        SECURE_BOOT_TEMPLATE_FIELD_NUMBER: _ClassVar[int]
        secure_boot_template: UEFI.InitialVariables.SecureBootTemplate
        def __init__(self, secure_boot_template: _Optional[_Union[UEFI.InitialVariables.SecureBootTemplate, str]] = ...) -> None: ...
    FIRMWARE_PATH_FIELD_NUMBER: _ClassVar[int]
    INITIAL_VARIABLES_FIELD_NUMBER: _ClassVar[int]
    SECURE_BOOT_ENABLED_FIELD_NUMBER: _ClassVar[int]
    firmware_path: str
    initial_variables: UEFI.InitialVariables
    secure_boot_enabled: bool
    def __init__(self, firmware_path: _Optional[str] = ..., initial_variables: _Optional[_Union[UEFI.InitialVariables, _Mapping]] = ..., secure_boot_enabled: bool = ...) -> None: ...

class MemoryConfig(_message.Message):
    __slots__ = ("memory_mb", "allow_overcommit", "deferred_commit", "hot_hint", "cold_hint", "cold_discard_hint", "low_mmio_gap_in_mb", "high_mmio_base_in_mb", "high_mmio_gap_in_mb")
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    ALLOW_OVERCOMMIT_FIELD_NUMBER: _ClassVar[int]
    DEFERRED_COMMIT_FIELD_NUMBER: _ClassVar[int]
    HOT_HINT_FIELD_NUMBER: _ClassVar[int]
    COLD_HINT_FIELD_NUMBER: _ClassVar[int]
    COLD_DISCARD_HINT_FIELD_NUMBER: _ClassVar[int]
    LOW_MMIO_GAP_IN_MB_FIELD_NUMBER: _ClassVar[int]
    HIGH_MMIO_BASE_IN_MB_FIELD_NUMBER: _ClassVar[int]
    HIGH_MMIO_GAP_IN_MB_FIELD_NUMBER: _ClassVar[int]
    memory_mb: int
    allow_overcommit: bool
    deferred_commit: bool
    hot_hint: bool
    cold_hint: bool
    cold_discard_hint: bool
    low_mmio_gap_in_mb: int
    high_mmio_base_in_mb: int
    high_mmio_gap_in_mb: int
    def __init__(self, memory_mb: _Optional[int] = ..., allow_overcommit: bool = ..., deferred_commit: bool = ..., hot_hint: bool = ..., cold_hint: bool = ..., cold_discard_hint: bool = ..., low_mmio_gap_in_mb: _Optional[int] = ..., high_mmio_base_in_mb: _Optional[int] = ..., high_mmio_gap_in_mb: _Optional[int] = ...) -> None: ...

class ProcessorConfig(_message.Message):
    __slots__ = ("processor_count", "processor_weight", "processor_limit", "x86", "aarch64")
    PROCESSOR_COUNT_FIELD_NUMBER: _ClassVar[int]
    PROCESSOR_WEIGHT_FIELD_NUMBER: _ClassVar[int]
    PROCESSOR_LIMIT_FIELD_NUMBER: _ClassVar[int]
    X86_FIELD_NUMBER: _ClassVar[int]
    AARCH64_FIELD_NUMBER: _ClassVar[int]
    processor_count: int
    processor_weight: int
    processor_limit: int
    x86: ProcessorX86Config
    aarch64: ProcessorAarch64Config
    def __init__(self, processor_count: _Optional[int] = ..., processor_weight: _Optional[int] = ..., processor_limit: _Optional[int] = ..., x86: _Optional[_Union[ProcessorX86Config, _Mapping]] = ..., aarch64: _Optional[_Union[ProcessorAarch64Config, _Mapping]] = ...) -> None: ...

class ProcessorX86Config(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ProcessorAarch64Config(_message.Message):
    __slots__ = ("msi_its", "msi_v2m")
    MSI_ITS_FIELD_NUMBER: _ClassVar[int]
    MSI_V2M_FIELD_NUMBER: _ClassVar[int]
    msi_its: GicMsiItsConfig
    msi_v2m: GicMsiV2mConfig
    def __init__(self, msi_its: _Optional[_Union[GicMsiItsConfig, _Mapping]] = ..., msi_v2m: _Optional[_Union[GicMsiV2mConfig, _Mapping]] = ...) -> None: ...

class GicMsiItsConfig(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GicMsiV2mConfig(_message.Message):
    __slots__ = ("spi_count",)
    SPI_COUNT_FIELD_NUMBER: _ClassVar[int]
    spi_count: int
    def __init__(self, spi_count: _Optional[int] = ...) -> None: ...

class DevicesConfig(_message.Message):
    __slots__ = ("scsi_disks", "vpmem_disks", "nic_config", "windows_device", "virtiofs_config", "virtio_console")
    SCSI_DISKS_FIELD_NUMBER: _ClassVar[int]
    VPMEM_DISKS_FIELD_NUMBER: _ClassVar[int]
    NIC_CONFIG_FIELD_NUMBER: _ClassVar[int]
    WINDOWS_DEVICE_FIELD_NUMBER: _ClassVar[int]
    VIRTIOFS_CONFIG_FIELD_NUMBER: _ClassVar[int]
    VIRTIO_CONSOLE_FIELD_NUMBER: _ClassVar[int]
    scsi_disks: _containers.RepeatedCompositeFieldContainer[SCSIDisk]
    vpmem_disks: _containers.RepeatedCompositeFieldContainer[VPMEMDisk]
    nic_config: _containers.RepeatedCompositeFieldContainer[NICConfig]
    windows_device: _containers.RepeatedCompositeFieldContainer[WindowsPCIDevice]
    virtiofs_config: _containers.RepeatedCompositeFieldContainer[VirtioFs]
    virtio_console: VirtioConsoleConfig
    def __init__(self, scsi_disks: _Optional[_Iterable[_Union[SCSIDisk, _Mapping]]] = ..., vpmem_disks: _Optional[_Iterable[_Union[VPMEMDisk, _Mapping]]] = ..., nic_config: _Optional[_Iterable[_Union[NICConfig, _Mapping]]] = ..., windows_device: _Optional[_Iterable[_Union[WindowsPCIDevice, _Mapping]]] = ..., virtiofs_config: _Optional[_Iterable[_Union[VirtioFs, _Mapping]]] = ..., virtio_console: _Optional[_Union[VirtioConsoleConfig, _Mapping]] = ...) -> None: ...

class PcieDeviceKind(_message.Message):
    __slots__ = ("virtio", "nvme", "vfio")
    VIRTIO_FIELD_NUMBER: _ClassVar[int]
    NVME_FIELD_NUMBER: _ClassVar[int]
    VFIO_FIELD_NUMBER: _ClassVar[int]
    virtio: VirtioDevice
    nvme: NvmeConfig
    vfio: VfioDevice
    def __init__(self, virtio: _Optional[_Union[VirtioDevice, _Mapping]] = ..., nvme: _Optional[_Union[NvmeConfig, _Mapping]] = ..., vfio: _Optional[_Union[VfioDevice, _Mapping]] = ...) -> None: ...

class VirtioDevice(_message.Message):
    __slots__ = ("blk", "net", "rng", "vsock", "console", "vhost_user", "fs")
    BLK_FIELD_NUMBER: _ClassVar[int]
    NET_FIELD_NUMBER: _ClassVar[int]
    RNG_FIELD_NUMBER: _ClassVar[int]
    VSOCK_FIELD_NUMBER: _ClassVar[int]
    CONSOLE_FIELD_NUMBER: _ClassVar[int]
    VHOST_USER_FIELD_NUMBER: _ClassVar[int]
    FS_FIELD_NUMBER: _ClassVar[int]
    blk: VirtioBlk
    net: VirtioNet
    rng: VirtioRng
    vsock: VirtioVsock
    console: VirtioConsole
    vhost_user: VhostUser
    fs: VirtioFs
    def __init__(self, blk: _Optional[_Union[VirtioBlk, _Mapping]] = ..., net: _Optional[_Union[VirtioNet, _Mapping]] = ..., rng: _Optional[_Union[VirtioRng, _Mapping]] = ..., vsock: _Optional[_Union[VirtioVsock, _Mapping]] = ..., console: _Optional[_Union[VirtioConsole, _Mapping]] = ..., vhost_user: _Optional[_Union[VhostUser, _Mapping]] = ..., fs: _Optional[_Union[VirtioFs, _Mapping]] = ...) -> None: ...

class VirtioConsole(_message.Message):
    __slots__ = ("backend",)
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    backend: SerialBackend
    def __init__(self, backend: _Optional[_Union[SerialBackend, _Mapping]] = ...) -> None: ...

class SerialBackend(_message.Message):
    __slots__ = ("relay",)
    RELAY_FIELD_NUMBER: _ClassVar[int]
    relay: SerialRelay
    def __init__(self, relay: _Optional[_Union[SerialRelay, _Mapping]] = ...) -> None: ...

class SerialRelay(_message.Message):
    __slots__ = ("socket_path", "connect")
    SOCKET_PATH_FIELD_NUMBER: _ClassVar[int]
    CONNECT_FIELD_NUMBER: _ClassVar[int]
    socket_path: str
    connect: bool
    def __init__(self, socket_path: _Optional[str] = ..., connect: bool = ...) -> None: ...

class DiskBackend(_message.Message):
    __slots__ = ("file",)
    FILE_FIELD_NUMBER: _ClassVar[int]
    file: FileDisk
    def __init__(self, file: _Optional[_Union[FileDisk, _Mapping]] = ...) -> None: ...

class FileDisk(_message.Message):
    __slots__ = ("path", "direct")
    PATH_FIELD_NUMBER: _ClassVar[int]
    DIRECT_FIELD_NUMBER: _ClassVar[int]
    path: str
    direct: bool
    def __init__(self, path: _Optional[str] = ..., direct: bool = ...) -> None: ...

class VirtioBlk(_message.Message):
    __slots__ = ("backend", "read_only", "serial")
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    READ_ONLY_FIELD_NUMBER: _ClassVar[int]
    SERIAL_FIELD_NUMBER: _ClassVar[int]
    backend: DiskBackend
    read_only: bool
    serial: str
    def __init__(self, backend: _Optional[_Union[DiskBackend, _Mapping]] = ..., read_only: bool = ..., serial: _Optional[str] = ...) -> None: ...

class VirtioNet(_message.Message):
    __slots__ = ("max_queues", "backend", "mac_address")
    MAX_QUEUES_FIELD_NUMBER: _ClassVar[int]
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    MAC_ADDRESS_FIELD_NUMBER: _ClassVar[int]
    max_queues: int
    backend: NicBackend
    mac_address: str
    def __init__(self, max_queues: _Optional[int] = ..., backend: _Optional[_Union[NicBackend, _Mapping]] = ..., mac_address: _Optional[str] = ...) -> None: ...

class NicBackend(_message.Message):
    __slots__ = ("dio", "tap", "consomme")
    DIO_FIELD_NUMBER: _ClassVar[int]
    TAP_FIELD_NUMBER: _ClassVar[int]
    CONSOMME_FIELD_NUMBER: _ClassVar[int]
    dio: DioBackend
    tap: TapBackend
    consomme: ConsommeBackend
    def __init__(self, dio: _Optional[_Union[DioBackend, _Mapping]] = ..., tap: _Optional[_Union[TapBackend, _Mapping]] = ..., consomme: _Optional[_Union[ConsommeBackend, _Mapping]] = ...) -> None: ...

class VhostUser(_message.Message):
    __slots__ = ("socket_path", "device")
    SOCKET_PATH_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    socket_path: str
    device: VhostUserDevice
    def __init__(self, socket_path: _Optional[str] = ..., device: _Optional[_Union[VhostUserDevice, _Mapping]] = ...) -> None: ...

class VhostUserDevice(_message.Message):
    __slots__ = ("blk", "fs", "other")
    BLK_FIELD_NUMBER: _ClassVar[int]
    FS_FIELD_NUMBER: _ClassVar[int]
    OTHER_FIELD_NUMBER: _ClassVar[int]
    blk: VhostUserBlk
    fs: VhostUserFs
    other: VhostUserGeneric
    def __init__(self, blk: _Optional[_Union[VhostUserBlk, _Mapping]] = ..., fs: _Optional[_Union[VhostUserFs, _Mapping]] = ..., other: _Optional[_Union[VhostUserGeneric, _Mapping]] = ...) -> None: ...

class VhostUserBlk(_message.Message):
    __slots__ = ("num_queues", "queue_size")
    NUM_QUEUES_FIELD_NUMBER: _ClassVar[int]
    QUEUE_SIZE_FIELD_NUMBER: _ClassVar[int]
    num_queues: int
    queue_size: int
    def __init__(self, num_queues: _Optional[int] = ..., queue_size: _Optional[int] = ...) -> None: ...

class VhostUserFs(_message.Message):
    __slots__ = ("tag", "num_queues", "queue_size")
    TAG_FIELD_NUMBER: _ClassVar[int]
    NUM_QUEUES_FIELD_NUMBER: _ClassVar[int]
    QUEUE_SIZE_FIELD_NUMBER: _ClassVar[int]
    tag: str
    num_queues: int
    queue_size: int
    def __init__(self, tag: _Optional[str] = ..., num_queues: _Optional[int] = ..., queue_size: _Optional[int] = ...) -> None: ...

class VhostUserGeneric(_message.Message):
    __slots__ = ("device_id", "queue_sizes")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    QUEUE_SIZES_FIELD_NUMBER: _ClassVar[int]
    device_id: int
    queue_sizes: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, device_id: _Optional[int] = ..., queue_sizes: _Optional[_Iterable[int]] = ...) -> None: ...

class VirtioRng(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class VirtioVsock(_message.Message):
    __slots__ = ("socket_path",)
    SOCKET_PATH_FIELD_NUMBER: _ClassVar[int]
    socket_path: str
    def __init__(self, socket_path: _Optional[str] = ...) -> None: ...

class NvmeConfig(_message.Message):
    __slots__ = ("controller_id", "namespaces")
    CONTROLLER_ID_FIELD_NUMBER: _ClassVar[int]
    NAMESPACES_FIELD_NUMBER: _ClassVar[int]
    controller_id: str
    namespaces: _containers.RepeatedCompositeFieldContainer[NvmeNamespace]
    def __init__(self, controller_id: _Optional[str] = ..., namespaces: _Optional[_Iterable[_Union[NvmeNamespace, _Mapping]]] = ...) -> None: ...

class NvmeNamespace(_message.Message):
    __slots__ = ("nsid", "backend", "read_only")
    NSID_FIELD_NUMBER: _ClassVar[int]
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    READ_ONLY_FIELD_NUMBER: _ClassVar[int]
    nsid: int
    backend: DiskBackend
    read_only: bool
    def __init__(self, nsid: _Optional[int] = ..., backend: _Optional[_Union[DiskBackend, _Mapping]] = ..., read_only: bool = ...) -> None: ...

class VfioDevice(_message.Message):
    __slots__ = ("host_pci_address", "bar_addresses", "iommufd_id")
    HOST_PCI_ADDRESS_FIELD_NUMBER: _ClassVar[int]
    BAR_ADDRESSES_FIELD_NUMBER: _ClassVar[int]
    IOMMUFD_ID_FIELD_NUMBER: _ClassVar[int]
    host_pci_address: str
    bar_addresses: _containers.RepeatedCompositeFieldContainer[VfioBarAddress]
    iommufd_id: str
    def __init__(self, host_pci_address: _Optional[str] = ..., bar_addresses: _Optional[_Iterable[_Union[VfioBarAddress, _Mapping]]] = ..., iommufd_id: _Optional[str] = ...) -> None: ...

class IommufdConfig(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class VfioBarAddress(_message.Message):
    __slots__ = ("bar_index", "host", "fixed")
    BAR_INDEX_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    FIXED_FIELD_NUMBER: _ClassVar[int]
    bar_index: int
    host: _empty_pb2.Empty
    fixed: int
    def __init__(self, bar_index: _Optional[int] = ..., host: _Optional[_Union[_empty_pb2.Empty, _Mapping]] = ..., fixed: _Optional[int] = ...) -> None: ...

class NumaConfig(_message.Message):
    __slots__ = ("nodes", "distances")
    NODES_FIELD_NUMBER: _ClassVar[int]
    DISTANCES_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[NumaNode]
    distances: _containers.RepeatedCompositeFieldContainer[NumaDistance]
    def __init__(self, nodes: _Optional[_Iterable[_Union[NumaNode, _Mapping]]] = ..., distances: _Optional[_Iterable[_Union[NumaDistance, _Mapping]]] = ...) -> None: ...

class NumaNode(_message.Message):
    __slots__ = ("memory", "vps")
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    VPS_FIELD_NUMBER: _ClassVar[int]
    memory: NodeMemoryConfig
    vps: VpAssignment
    def __init__(self, memory: _Optional[_Union[NodeMemoryConfig, _Mapping]] = ..., vps: _Optional[_Union[VpAssignment, _Mapping]] = ...) -> None: ...

class VpAssignment(_message.Message):
    __slots__ = ("vp_index",)
    VP_INDEX_FIELD_NUMBER: _ClassVar[int]
    vp_index: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, vp_index: _Optional[_Iterable[int]] = ...) -> None: ...

class NodeMemoryConfig(_message.Message):
    __slots__ = ("memory_mb", "host_numa_node", "prefetch", "private_memory", "transparent_hugepages", "hugepages", "hugepage_size_bytes")
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    HOST_NUMA_NODE_FIELD_NUMBER: _ClassVar[int]
    PREFETCH_FIELD_NUMBER: _ClassVar[int]
    PRIVATE_MEMORY_FIELD_NUMBER: _ClassVar[int]
    TRANSPARENT_HUGEPAGES_FIELD_NUMBER: _ClassVar[int]
    HUGEPAGES_FIELD_NUMBER: _ClassVar[int]
    HUGEPAGE_SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    memory_mb: int
    host_numa_node: int
    prefetch: bool
    private_memory: bool
    transparent_hugepages: bool
    hugepages: bool
    hugepage_size_bytes: int
    def __init__(self, memory_mb: _Optional[int] = ..., host_numa_node: _Optional[int] = ..., prefetch: bool = ..., private_memory: bool = ..., transparent_hugepages: bool = ..., hugepages: bool = ..., hugepage_size_bytes: _Optional[int] = ...) -> None: ...

class NumaDistance(_message.Message):
    __slots__ = ("src", "dst", "distance")
    SRC_FIELD_NUMBER: _ClassVar[int]
    DST_FIELD_NUMBER: _ClassVar[int]
    DISTANCE_FIELD_NUMBER: _ClassVar[int]
    src: int
    dst: int
    distance: int
    def __init__(self, src: _Optional[int] = ..., dst: _Optional[int] = ..., distance: _Optional[int] = ...) -> None: ...

class PcieTopologyConfig(_message.Message):
    __slots__ = ("root_complexes", "generic_initiators")
    ROOT_COMPLEXES_FIELD_NUMBER: _ClassVar[int]
    GENERIC_INITIATORS_FIELD_NUMBER: _ClassVar[int]
    root_complexes: _containers.RepeatedCompositeFieldContainer[PcieRootComplex]
    generic_initiators: _containers.RepeatedCompositeFieldContainer[PcieGenericInitiator]
    def __init__(self, root_complexes: _Optional[_Iterable[_Union[PcieRootComplex, _Mapping]]] = ..., generic_initiators: _Optional[_Iterable[_Union[PcieGenericInitiator, _Mapping]]] = ...) -> None: ...

class PcieGenericInitiator(_message.Message):
    __slots__ = ("port_name", "node")
    PORT_NAME_FIELD_NUMBER: _ClassVar[int]
    NODE_FIELD_NUMBER: _ClassVar[int]
    port_name: str
    node: int
    def __init__(self, port_name: _Optional[str] = ..., node: _Optional[int] = ...) -> None: ...

class PcieRootComplex(_message.Message):
    __slots__ = ("name", "segment", "start_bus", "end_bus", "low_mmio", "high_mmio", "low_mmio_base", "high_mmio_base", "preserve_bars", "node", "root_ports", "iommu")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SEGMENT_FIELD_NUMBER: _ClassVar[int]
    START_BUS_FIELD_NUMBER: _ClassVar[int]
    END_BUS_FIELD_NUMBER: _ClassVar[int]
    LOW_MMIO_FIELD_NUMBER: _ClassVar[int]
    HIGH_MMIO_FIELD_NUMBER: _ClassVar[int]
    LOW_MMIO_BASE_FIELD_NUMBER: _ClassVar[int]
    HIGH_MMIO_BASE_FIELD_NUMBER: _ClassVar[int]
    PRESERVE_BARS_FIELD_NUMBER: _ClassVar[int]
    NODE_FIELD_NUMBER: _ClassVar[int]
    ROOT_PORTS_FIELD_NUMBER: _ClassVar[int]
    IOMMU_FIELD_NUMBER: _ClassVar[int]
    name: str
    segment: int
    start_bus: int
    end_bus: int
    low_mmio: int
    high_mmio: int
    low_mmio_base: int
    high_mmio_base: int
    preserve_bars: bool
    node: int
    root_ports: _containers.RepeatedCompositeFieldContainer[PciePort]
    iommu: PcieIommuConfig
    def __init__(self, name: _Optional[str] = ..., segment: _Optional[int] = ..., start_bus: _Optional[int] = ..., end_bus: _Optional[int] = ..., low_mmio: _Optional[int] = ..., high_mmio: _Optional[int] = ..., low_mmio_base: _Optional[int] = ..., high_mmio_base: _Optional[int] = ..., preserve_bars: bool = ..., node: _Optional[int] = ..., root_ports: _Optional[_Iterable[_Union[PciePort, _Mapping]]] = ..., iommu: _Optional[_Union[PcieIommuConfig, _Mapping]] = ...) -> None: ...

class PcieIommuConfig(_message.Message):
    __slots__ = ("smmu",)
    SMMU_FIELD_NUMBER: _ClassVar[int]
    smmu: SmmuConfig
    def __init__(self, smmu: _Optional[_Union[SmmuConfig, _Mapping]] = ...) -> None: ...

class SmmuConfig(_message.Message):
    __slots__ = ("accel", "oas_bits")
    ACCEL_FIELD_NUMBER: _ClassVar[int]
    OAS_BITS_FIELD_NUMBER: _ClassVar[int]
    accel: bool
    oas_bits: int
    def __init__(self, accel: bool = ..., oas_bits: _Optional[int] = ...) -> None: ...

class PciePort(_message.Message):
    __slots__ = ("name", "hotplug", "attached", "devfn", "acs_capabilities_supported", "pasid")
    NAME_FIELD_NUMBER: _ClassVar[int]
    HOTPLUG_FIELD_NUMBER: _ClassVar[int]
    ATTACHED_FIELD_NUMBER: _ClassVar[int]
    DEVFN_FIELD_NUMBER: _ClassVar[int]
    ACS_CAPABILITIES_SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    PASID_FIELD_NUMBER: _ClassVar[int]
    name: str
    hotplug: bool
    attached: PcieAttachment
    devfn: int
    acs_capabilities_supported: int
    pasid: bool
    def __init__(self, name: _Optional[str] = ..., hotplug: bool = ..., attached: _Optional[_Union[PcieAttachment, _Mapping]] = ..., devfn: _Optional[int] = ..., acs_capabilities_supported: _Optional[int] = ..., pasid: bool = ...) -> None: ...

class PcieSwitch(_message.Message):
    __slots__ = ("name", "downstream_ports")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DOWNSTREAM_PORTS_FIELD_NUMBER: _ClassVar[int]
    name: str
    downstream_ports: _containers.RepeatedCompositeFieldContainer[PciePort]
    def __init__(self, name: _Optional[str] = ..., downstream_ports: _Optional[_Iterable[_Union[PciePort, _Mapping]]] = ...) -> None: ...

class PcieAttachment(_message.Message):
    __slots__ = ("device", "switch")
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    SWITCH_FIELD_NUMBER: _ClassVar[int]
    device: PcieDeviceKind
    switch: PcieSwitch
    def __init__(self, device: _Optional[_Union[PcieDeviceKind, _Mapping]] = ..., switch: _Optional[_Union[PcieSwitch, _Mapping]] = ...) -> None: ...

class VMConfig(_message.Message):
    __slots__ = ("memory_config", "processor_config", "devices_config", "serial_config", "direct_boot", "uefi", "igvm", "windows_options", "hvsocket_config", "numa_config", "pcie", "guest_power_actions", "smbios_config", "crash_dump_path", "iommufds", "disable_vmbus", "disable_hv", "isolation_config")
    class GuestPowerAction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        DEFAULT: _ClassVar[VMConfig.GuestPowerAction]
        RESTART: _ClassVar[VMConfig.GuestPowerAction]
        HALT: _ClassVar[VMConfig.GuestPowerAction]
    DEFAULT: VMConfig.GuestPowerAction
    RESTART: VMConfig.GuestPowerAction
    HALT: VMConfig.GuestPowerAction
    class GuestPowerActions(_message.Message):
        __slots__ = ("shutdown", "reset", "crash", "watchdog")
        SHUTDOWN_FIELD_NUMBER: _ClassVar[int]
        RESET_FIELD_NUMBER: _ClassVar[int]
        CRASH_FIELD_NUMBER: _ClassVar[int]
        WATCHDOG_FIELD_NUMBER: _ClassVar[int]
        shutdown: VMConfig.GuestPowerAction
        reset: VMConfig.GuestPowerAction
        crash: VMConfig.GuestPowerAction
        watchdog: VMConfig.GuestPowerAction
        def __init__(self, shutdown: _Optional[_Union[VMConfig.GuestPowerAction, str]] = ..., reset: _Optional[_Union[VMConfig.GuestPowerAction, str]] = ..., crash: _Optional[_Union[VMConfig.GuestPowerAction, str]] = ..., watchdog: _Optional[_Union[VMConfig.GuestPowerAction, str]] = ...) -> None: ...
    MEMORY_CONFIG_FIELD_NUMBER: _ClassVar[int]
    PROCESSOR_CONFIG_FIELD_NUMBER: _ClassVar[int]
    DEVICES_CONFIG_FIELD_NUMBER: _ClassVar[int]
    SERIAL_CONFIG_FIELD_NUMBER: _ClassVar[int]
    DIRECT_BOOT_FIELD_NUMBER: _ClassVar[int]
    UEFI_FIELD_NUMBER: _ClassVar[int]
    IGVM_FIELD_NUMBER: _ClassVar[int]
    WINDOWS_OPTIONS_FIELD_NUMBER: _ClassVar[int]
    HVSOCKET_CONFIG_FIELD_NUMBER: _ClassVar[int]
    NUMA_CONFIG_FIELD_NUMBER: _ClassVar[int]
    PCIE_FIELD_NUMBER: _ClassVar[int]
    GUEST_POWER_ACTIONS_FIELD_NUMBER: _ClassVar[int]
    SMBIOS_CONFIG_FIELD_NUMBER: _ClassVar[int]
    CRASH_DUMP_PATH_FIELD_NUMBER: _ClassVar[int]
    IOMMUFDS_FIELD_NUMBER: _ClassVar[int]
    DISABLE_VMBUS_FIELD_NUMBER: _ClassVar[int]
    DISABLE_HV_FIELD_NUMBER: _ClassVar[int]
    ISOLATION_CONFIG_FIELD_NUMBER: _ClassVar[int]
    memory_config: MemoryConfig
    processor_config: ProcessorConfig
    devices_config: DevicesConfig
    serial_config: SerialConfig
    direct_boot: DirectBoot
    uefi: UEFI
    igvm: IgvmBoot
    windows_options: WindowsOptions
    hvsocket_config: HVSocketConfig
    numa_config: NumaConfig
    pcie: PcieTopologyConfig
    guest_power_actions: VMConfig.GuestPowerActions
    smbios_config: SMBIOSConfig
    crash_dump_path: str
    iommufds: _containers.RepeatedCompositeFieldContainer[IommufdConfig]
    disable_vmbus: bool
    disable_hv: bool
    isolation_config: IsolationConfig
    def __init__(self, memory_config: _Optional[_Union[MemoryConfig, _Mapping]] = ..., processor_config: _Optional[_Union[ProcessorConfig, _Mapping]] = ..., devices_config: _Optional[_Union[DevicesConfig, _Mapping]] = ..., serial_config: _Optional[_Union[SerialConfig, _Mapping]] = ..., direct_boot: _Optional[_Union[DirectBoot, _Mapping]] = ..., uefi: _Optional[_Union[UEFI, _Mapping]] = ..., igvm: _Optional[_Union[IgvmBoot, _Mapping]] = ..., windows_options: _Optional[_Union[WindowsOptions, _Mapping]] = ..., hvsocket_config: _Optional[_Union[HVSocketConfig, _Mapping]] = ..., numa_config: _Optional[_Union[NumaConfig, _Mapping]] = ..., pcie: _Optional[_Union[PcieTopologyConfig, _Mapping]] = ..., guest_power_actions: _Optional[_Union[VMConfig.GuestPowerActions, _Mapping]] = ..., smbios_config: _Optional[_Union[SMBIOSConfig, _Mapping]] = ..., crash_dump_path: _Optional[str] = ..., iommufds: _Optional[_Iterable[_Union[IommufdConfig, _Mapping]]] = ..., disable_vmbus: bool = ..., disable_hv: bool = ..., isolation_config: _Optional[_Union[IsolationConfig, _Mapping]] = ...) -> None: ...

class WindowsOptions(_message.Message):
    __slots__ = ("cpu_group_id",)
    CPU_GROUP_ID_FIELD_NUMBER: _ClassVar[int]
    cpu_group_id: int
    def __init__(self, cpu_group_id: _Optional[int] = ...) -> None: ...

class SerialConfig(_message.Message):
    __slots__ = ("ports",)
    class Config(_message.Message):
        __slots__ = ("port", "socket_path", "connect")
        PORT_FIELD_NUMBER: _ClassVar[int]
        SOCKET_PATH_FIELD_NUMBER: _ClassVar[int]
        CONNECT_FIELD_NUMBER: _ClassVar[int]
        port: int
        socket_path: str
        connect: bool
        def __init__(self, port: _Optional[int] = ..., socket_path: _Optional[str] = ..., connect: bool = ...) -> None: ...
    PORTS_FIELD_NUMBER: _ClassVar[int]
    ports: _containers.RepeatedCompositeFieldContainer[SerialConfig.Config]
    def __init__(self, ports: _Optional[_Iterable[_Union[SerialConfig.Config, _Mapping]]] = ...) -> None: ...

class HVSocketConfig(_message.Message):
    __slots__ = ("path",)
    PATH_FIELD_NUMBER: _ClassVar[int]
    path: str
    def __init__(self, path: _Optional[str] = ...) -> None: ...

class SMBIOSConfig(_message.Message):
    __slots__ = ("bios", "system")
    class BIOS(_message.Message):
        __slots__ = ("vendor", "version", "release_date", "release")
        class Release(_message.Message):
            __slots__ = ("major", "minor")
            MAJOR_FIELD_NUMBER: _ClassVar[int]
            MINOR_FIELD_NUMBER: _ClassVar[int]
            major: int
            minor: int
            def __init__(self, major: _Optional[int] = ..., minor: _Optional[int] = ...) -> None: ...
        VENDOR_FIELD_NUMBER: _ClassVar[int]
        VERSION_FIELD_NUMBER: _ClassVar[int]
        RELEASE_DATE_FIELD_NUMBER: _ClassVar[int]
        RELEASE_FIELD_NUMBER: _ClassVar[int]
        vendor: str
        version: str
        release_date: str
        release: SMBIOSConfig.BIOS.Release
        def __init__(self, vendor: _Optional[str] = ..., version: _Optional[str] = ..., release_date: _Optional[str] = ..., release: _Optional[_Union[SMBIOSConfig.BIOS.Release, _Mapping]] = ...) -> None: ...
    class System(_message.Message):
        __slots__ = ("manufacturer", "product_name", "version", "serial_number", "sku_number", "family", "uuid")
        MANUFACTURER_FIELD_NUMBER: _ClassVar[int]
        PRODUCT_NAME_FIELD_NUMBER: _ClassVar[int]
        VERSION_FIELD_NUMBER: _ClassVar[int]
        SERIAL_NUMBER_FIELD_NUMBER: _ClassVar[int]
        SKU_NUMBER_FIELD_NUMBER: _ClassVar[int]
        FAMILY_FIELD_NUMBER: _ClassVar[int]
        UUID_FIELD_NUMBER: _ClassVar[int]
        manufacturer: str
        product_name: str
        version: str
        serial_number: str
        sku_number: str
        family: str
        uuid: str
        def __init__(self, manufacturer: _Optional[str] = ..., product_name: _Optional[str] = ..., version: _Optional[str] = ..., serial_number: _Optional[str] = ..., sku_number: _Optional[str] = ..., family: _Optional[str] = ..., uuid: _Optional[str] = ...) -> None: ...
    BIOS_FIELD_NUMBER: _ClassVar[int]
    SYSTEM_FIELD_NUMBER: _ClassVar[int]
    bios: SMBIOSConfig.BIOS
    system: SMBIOSConfig.System
    def __init__(self, bios: _Optional[_Union[SMBIOSConfig.BIOS, _Mapping]] = ..., system: _Optional[_Union[SMBIOSConfig.System, _Mapping]] = ...) -> None: ...

class CreateVMRequest(_message.Message):
    __slots__ = ("config", "log_id")
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    LOG_ID_FIELD_NUMBER: _ClassVar[int]
    config: VMConfig
    log_id: str
    def __init__(self, config: _Optional[_Union[VMConfig, _Mapping]] = ..., log_id: _Optional[str] = ...) -> None: ...

class MemoryStats(_message.Message):
    __slots__ = ("working_set_bytes", "available_memory", "reserved_memory", "assigned_memory")
    WORKING_SET_BYTES_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_MEMORY_FIELD_NUMBER: _ClassVar[int]
    RESERVED_MEMORY_FIELD_NUMBER: _ClassVar[int]
    ASSIGNED_MEMORY_FIELD_NUMBER: _ClassVar[int]
    working_set_bytes: int
    available_memory: int
    reserved_memory: int
    assigned_memory: int
    def __init__(self, working_set_bytes: _Optional[int] = ..., available_memory: _Optional[int] = ..., reserved_memory: _Optional[int] = ..., assigned_memory: _Optional[int] = ...) -> None: ...

class ProcessorStats(_message.Message):
    __slots__ = ("total_runtime_ns",)
    TOTAL_RUNTIME_NS_FIELD_NUMBER: _ClassVar[int]
    total_runtime_ns: int
    def __init__(self, total_runtime_ns: _Optional[int] = ...) -> None: ...

class PropertiesVMRequest(_message.Message):
    __slots__ = ("types",)
    class PropertiesType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        Memory: _ClassVar[PropertiesVMRequest.PropertiesType]
        Processor: _ClassVar[PropertiesVMRequest.PropertiesType]
    Memory: PropertiesVMRequest.PropertiesType
    Processor: PropertiesVMRequest.PropertiesType
    TYPES_FIELD_NUMBER: _ClassVar[int]
    types: _containers.RepeatedScalarFieldContainer[PropertiesVMRequest.PropertiesType]
    def __init__(self, types: _Optional[_Iterable[_Union[PropertiesVMRequest.PropertiesType, str]]] = ...) -> None: ...

class PropertiesVMResponse(_message.Message):
    __slots__ = ("memory_stats", "processor_stats", "state", "halt_reason")
    MEMORY_STATS_FIELD_NUMBER: _ClassVar[int]
    PROCESSOR_STATS_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    HALT_REASON_FIELD_NUMBER: _ClassVar[int]
    memory_stats: MemoryStats
    processor_stats: ProcessorStats
    state: VmState
    halt_reason: str
    def __init__(self, memory_stats: _Optional[_Union[MemoryStats, _Mapping]] = ..., processor_stats: _Optional[_Union[ProcessorStats, _Mapping]] = ..., state: _Optional[_Union[VmState, str]] = ..., halt_reason: _Optional[str] = ...) -> None: ...

class CapabilitiesVMResponse(_message.Message):
    __slots__ = ("supported_resources", "supported_guest_os")
    class Resource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        Vpmem: _ClassVar[CapabilitiesVMResponse.Resource]
        Scsi: _ClassVar[CapabilitiesVMResponse.Resource]
        Vpci: _ClassVar[CapabilitiesVMResponse.Resource]
        Plan9: _ClassVar[CapabilitiesVMResponse.Resource]
        VMNic: _ClassVar[CapabilitiesVMResponse.Resource]
        Memory: _ClassVar[CapabilitiesVMResponse.Resource]
        Processor: _ClassVar[CapabilitiesVMResponse.Resource]
    Vpmem: CapabilitiesVMResponse.Resource
    Scsi: CapabilitiesVMResponse.Resource
    Vpci: CapabilitiesVMResponse.Resource
    Plan9: CapabilitiesVMResponse.Resource
    VMNic: CapabilitiesVMResponse.Resource
    Memory: CapabilitiesVMResponse.Resource
    Processor: CapabilitiesVMResponse.Resource
    class SupportedGuestOS(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        Windows: _ClassVar[CapabilitiesVMResponse.SupportedGuestOS]
        Linux: _ClassVar[CapabilitiesVMResponse.SupportedGuestOS]
    Windows: CapabilitiesVMResponse.SupportedGuestOS
    Linux: CapabilitiesVMResponse.SupportedGuestOS
    class SupportedResource(_message.Message):
        __slots__ = ("Add", "Remove", "Update", "resource")
        ADD_FIELD_NUMBER: _ClassVar[int]
        REMOVE_FIELD_NUMBER: _ClassVar[int]
        UPDATE_FIELD_NUMBER: _ClassVar[int]
        RESOURCE_FIELD_NUMBER: _ClassVar[int]
        Add: bool
        Remove: bool
        Update: bool
        resource: CapabilitiesVMResponse.Resource
        def __init__(self, Add: bool = ..., Remove: bool = ..., Update: bool = ..., resource: _Optional[_Union[CapabilitiesVMResponse.Resource, str]] = ...) -> None: ...
    SUPPORTED_RESOURCES_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_GUEST_OS_FIELD_NUMBER: _ClassVar[int]
    supported_resources: _containers.RepeatedCompositeFieldContainer[CapabilitiesVMResponse.SupportedResource]
    supported_guest_os: _containers.RepeatedScalarFieldContainer[CapabilitiesVMResponse.SupportedGuestOS]
    def __init__(self, supported_resources: _Optional[_Iterable[_Union[CapabilitiesVMResponse.SupportedResource, _Mapping]]] = ..., supported_guest_os: _Optional[_Iterable[_Union[CapabilitiesVMResponse.SupportedGuestOS, str]]] = ...) -> None: ...

class SCSIDisk(_message.Message):
    __slots__ = ("controller", "lun", "host_path", "type", "read_only")
    CONTROLLER_FIELD_NUMBER: _ClassVar[int]
    LUN_FIELD_NUMBER: _ClassVar[int]
    HOST_PATH_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    READ_ONLY_FIELD_NUMBER: _ClassVar[int]
    controller: int
    lun: int
    host_path: str
    type: DiskType
    read_only: bool
    def __init__(self, controller: _Optional[int] = ..., lun: _Optional[int] = ..., host_path: _Optional[str] = ..., type: _Optional[_Union[DiskType, str]] = ..., read_only: bool = ...) -> None: ...

class VPMEMDisk(_message.Message):
    __slots__ = ("host_path", "type", "read_only")
    HOST_PATH_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    READ_ONLY_FIELD_NUMBER: _ClassVar[int]
    host_path: str
    type: DiskType
    read_only: bool
    def __init__(self, host_path: _Optional[str] = ..., type: _Optional[_Union[DiskType, str]] = ..., read_only: bool = ...) -> None: ...

class NICConfig(_message.Message):
    __slots__ = ("nic_id", "mac_address", "legacy_switch_id", "nic_name", "legacy_port_id", "dio", "tap", "consomme")
    NIC_ID_FIELD_NUMBER: _ClassVar[int]
    MAC_ADDRESS_FIELD_NUMBER: _ClassVar[int]
    LEGACY_SWITCH_ID_FIELD_NUMBER: _ClassVar[int]
    NIC_NAME_FIELD_NUMBER: _ClassVar[int]
    LEGACY_PORT_ID_FIELD_NUMBER: _ClassVar[int]
    DIO_FIELD_NUMBER: _ClassVar[int]
    TAP_FIELD_NUMBER: _ClassVar[int]
    CONSOMME_FIELD_NUMBER: _ClassVar[int]
    nic_id: str
    mac_address: str
    legacy_switch_id: str
    nic_name: str
    legacy_port_id: str
    dio: DioBackend
    tap: TapBackend
    consomme: ConsommeBackend
    def __init__(self, nic_id: _Optional[str] = ..., mac_address: _Optional[str] = ..., legacy_switch_id: _Optional[str] = ..., nic_name: _Optional[str] = ..., legacy_port_id: _Optional[str] = ..., dio: _Optional[_Union[DioBackend, _Mapping]] = ..., tap: _Optional[_Union[TapBackend, _Mapping]] = ..., consomme: _Optional[_Union[ConsommeBackend, _Mapping]] = ...) -> None: ...

class DioBackend(_message.Message):
    __slots__ = ("switch_id", "port_id")
    SWITCH_ID_FIELD_NUMBER: _ClassVar[int]
    PORT_ID_FIELD_NUMBER: _ClassVar[int]
    switch_id: str
    port_id: str
    def __init__(self, switch_id: _Optional[str] = ..., port_id: _Optional[str] = ...) -> None: ...

class TapBackend(_message.Message):
    __slots__ = ("name", "fd_name")
    NAME_FIELD_NUMBER: _ClassVar[int]
    FD_NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    fd_name: str
    def __init__(self, name: _Optional[str] = ..., fd_name: _Optional[str] = ...) -> None: ...

class PortConfig(_message.Message):
    __slots__ = ("host_port", "guest_port", "protocol", "host_address")
    HOST_PORT_FIELD_NUMBER: _ClassVar[int]
    GUEST_PORT_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_FIELD_NUMBER: _ClassVar[int]
    HOST_ADDRESS_FIELD_NUMBER: _ClassVar[int]
    host_port: int
    guest_port: int
    protocol: IpProtocol
    host_address: str
    def __init__(self, host_port: _Optional[int] = ..., guest_port: _Optional[int] = ..., protocol: _Optional[_Union[IpProtocol, str]] = ..., host_address: _Optional[str] = ...) -> None: ...

class ConsommeBackend(_message.Message):
    __slots__ = ("cidr", "ports")
    CIDR_FIELD_NUMBER: _ClassVar[int]
    PORTS_FIELD_NUMBER: _ClassVar[int]
    cidr: str
    ports: _containers.RepeatedCompositeFieldContainer[PortConfig]
    def __init__(self, cidr: _Optional[str] = ..., ports: _Optional[_Iterable[_Union[PortConfig, _Mapping]]] = ...) -> None: ...

class WindowsPCIDevice(_message.Message):
    __slots__ = ("instance_id",)
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    instance_id: str
    def __init__(self, instance_id: _Optional[str] = ...) -> None: ...

class VirtioFs(_message.Message):
    __slots__ = ("tag", "root_path", "read_only")
    TAG_FIELD_NUMBER: _ClassVar[int]
    ROOT_PATH_FIELD_NUMBER: _ClassVar[int]
    READ_ONLY_FIELD_NUMBER: _ClassVar[int]
    tag: str
    root_path: str
    read_only: bool
    def __init__(self, tag: _Optional[str] = ..., root_path: _Optional[str] = ..., read_only: bool = ...) -> None: ...

class VirtioConsoleConfig(_message.Message):
    __slots__ = ("socket_path", "connect")
    SOCKET_PATH_FIELD_NUMBER: _ClassVar[int]
    CONNECT_FIELD_NUMBER: _ClassVar[int]
    socket_path: str
    connect: bool
    def __init__(self, socket_path: _Optional[str] = ..., connect: bool = ...) -> None: ...

class ModifyMemoryRequest(_message.Message):
    __slots__ = ("memory_mb",)
    MEMORY_MB_FIELD_NUMBER: _ClassVar[int]
    memory_mb: int
    def __init__(self, memory_mb: _Optional[int] = ...) -> None: ...

class ModifyProcessorRequest(_message.Message):
    __slots__ = ("processor_index",)
    PROCESSOR_INDEX_FIELD_NUMBER: _ClassVar[int]
    processor_index: int
    def __init__(self, processor_index: _Optional[int] = ...) -> None: ...

class ModifyProcessorConfigRequest(_message.Message):
    __slots__ = ("processor_weight", "processor_limit")
    PROCESSOR_WEIGHT_FIELD_NUMBER: _ClassVar[int]
    PROCESSOR_LIMIT_FIELD_NUMBER: _ClassVar[int]
    processor_weight: int
    processor_limit: int
    def __init__(self, processor_weight: _Optional[int] = ..., processor_limit: _Optional[int] = ...) -> None: ...

class ModifyResourceRequest(_message.Message):
    __slots__ = ("type", "processor", "processor_config", "memory", "scsi_disk", "vpmem_disk", "nic_config", "windows_device")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    PROCESSOR_FIELD_NUMBER: _ClassVar[int]
    PROCESSOR_CONFIG_FIELD_NUMBER: _ClassVar[int]
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    SCSI_DISK_FIELD_NUMBER: _ClassVar[int]
    VPMEM_DISK_FIELD_NUMBER: _ClassVar[int]
    NIC_CONFIG_FIELD_NUMBER: _ClassVar[int]
    WINDOWS_DEVICE_FIELD_NUMBER: _ClassVar[int]
    type: ModifyType
    processor: ModifyProcessorRequest
    processor_config: ModifyProcessorConfigRequest
    memory: ModifyMemoryRequest
    scsi_disk: SCSIDisk
    vpmem_disk: VPMEMDisk
    nic_config: NICConfig
    windows_device: WindowsPCIDevice
    def __init__(self, type: _Optional[_Union[ModifyType, str]] = ..., processor: _Optional[_Union[ModifyProcessorRequest, _Mapping]] = ..., processor_config: _Optional[_Union[ModifyProcessorConfigRequest, _Mapping]] = ..., memory: _Optional[_Union[ModifyMemoryRequest, _Mapping]] = ..., scsi_disk: _Optional[_Union[SCSIDisk, _Mapping]] = ..., vpmem_disk: _Optional[_Union[VPMEMDisk, _Mapping]] = ..., nic_config: _Optional[_Union[NICConfig, _Mapping]] = ..., windows_device: _Optional[_Union[WindowsPCIDevice, _Mapping]] = ...) -> None: ...

class AddPcieDeviceRequest(_message.Message):
    __slots__ = ("port_name", "device")
    PORT_NAME_FIELD_NUMBER: _ClassVar[int]
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    port_name: str
    device: PcieDeviceKind
    def __init__(self, port_name: _Optional[str] = ..., device: _Optional[_Union[PcieDeviceKind, _Mapping]] = ...) -> None: ...

class RemovePcieDeviceRequest(_message.Message):
    __slots__ = ("port_name",)
    PORT_NAME_FIELD_NUMBER: _ClassVar[int]
    port_name: str
    def __init__(self, port_name: _Optional[str] = ...) -> None: ...

class AddVpciDeviceRequest(_message.Message):
    __slots__ = ("device", "instance_id")
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    device: PcieDeviceKind
    instance_id: str
    def __init__(self, device: _Optional[_Union[PcieDeviceKind, _Mapping]] = ..., instance_id: _Optional[str] = ...) -> None: ...

class RemoveVpciDeviceRequest(_message.Message):
    __slots__ = ("instance_id",)
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    instance_id: str
    def __init__(self, instance_id: _Optional[str] = ...) -> None: ...
