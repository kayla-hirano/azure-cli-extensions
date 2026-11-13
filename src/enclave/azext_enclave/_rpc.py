# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Translate enclave launch settings to the pinned protobuf contract and issue bounded local gRPC calls."""

import time
from pathlib import Path
from typing import Literal, Optional, Union, overload

import grpc
from google.protobuf import empty_pb2
from knack.util import CLIError

from ._isolation import IsolationConfiguration, load_isolation, validate_configuration
from .rpc import vmservice_pb2 as vm


def _boot_personality(boot_mode: str) -> int:
    """Map an implemented boot mode to its pinned RPC enum independently of isolation type."""
    if boot_mode == 'linux_direct':
        return vm.IgvmBoot.PERSONALITY_LINUX_DIRECT
    raise ValueError('RPC boot mode is not implemented: ' + boot_mode)


def validate_launch_configuration(configuration: IsolationConfiguration) -> None:
    """Require implemented RPC isolation and boot mappings before starting a runtime."""
    if configuration.isolation_type == 'snp':
        validate_configuration(configuration)
    else:
        raise ValueError('RPC launch is not implemented for isolation type: ' + configuration.isolation_type)
    _boot_personality(configuration.supported_configuration.boot_mode)


def create_request(image: Path, directory: Path, name: str,
                   configuration: Optional[IsolationConfiguration] = None) -> vm.CreateVMRequest:
    """Map isolation-specific boot settings and shared resources/devices to the pinned RPC."""
    configuration = configuration if configuration is not None else load_isolation()
    validate_launch_configuration(configuration)
    request = vm.CreateVMRequest(log_id=name)
    config = request.config
    config.igvm.igvm_path = str(image)
    config.igvm.personality = _boot_personality(configuration.supported_configuration.boot_mode)
    if configuration.isolation_type == 'snp':
        # An absent isolation config means no isolation, not an implicit SNP selection.
        config.isolation_config.snp.SetInParent()
    else:
        raise ValueError('RPC isolation mapping is not implemented for isolation type: ' + configuration.isolation_type)
    config.disable_vmbus = configuration.runtime.disable_vmbus
    config.disable_hv = configuration.runtime.disable_hv
    config.memory_config.memory_mb = configuration.defaults.memory_mib
    config.processor_config.processor_count = configuration.defaults.processor_count
    # COM1 is zero-indexed in RPC and connects to the supervisor's serial listener.
    config.serial_config.ports.add(
        port=configuration.serial_port, socket_path=str(directory / 'serial.sock'), connect=True)
    # The PCIe layout must match the image's measured pcie_host_bridge configuration.
    pcie = configuration.pcie
    root = config.pcie.root_complexes.add(
        name='rc0', segment=pcie.segment, start_bus=pcie.start_bus, end_bus=pcie.end_bus,
        low_mmio=pcie.low_mmio_size, low_mmio_base=pcie.low_mmio_base,
        high_mmio=pcie.high_mmio_size, high_mmio_base=pcie.high_mmio_base)
    port = root.root_ports.add(name='vsock')
    port.attached.device.virtio.vsock.socket_path = str(directory / 'vsock.sock')
    return request


class VmClient:
    """Own one Unix-socket gRPC channel with a shared monotonic operation deadline."""

    def __init__(self, path: Path, deadline: float) -> None:
        """Connect to the instance management path without extending the caller's deadline."""
        self.deadline = deadline
        # gRPC otherwise derives an encoded socket-path authority that OpenVMM's HTTP/2 server rejects.
        self.channel = grpc.insecure_channel(
            'unix:' + str(path), options=(('grpc.default_authority', 'localhost'),))

    def __enter__(self) -> 'VmClient':
        """Expose the client for a context-managed group of RPC calls."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Release the channel even when an RPC or caller operation fails."""
        # Disconnect this client only; stopping OpenVMM's RPC server requires Quit or process cleanup.
        self.channel.close()

    def remaining(self) -> float:
        """Return the remaining operation budget or fail before issuing an expired request."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CLIError('OpenVMM RPC operation timed out.')
        return remaining

    def ready(self) -> None:
        """Wait for a usable transport rather than relying on socket-file existence."""
        try:
            grpc.channel_ready_future(self.channel).result(timeout=self.remaining())
        except grpc.FutureTimeoutError as error:
            raise CLIError('OpenVMM management endpoint did not become ready.') from error

    @overload
    def call(self, method: Literal['PropertiesVM'], request: None = None) -> vm.PropertiesVMResponse:
        """Describe the typed state-query response for static analysis."""

    @overload
    def call(self, method: Literal['CreateVM', 'ResumeVM', 'TeardownVM', 'Quit'],
             request: Optional[vm.CreateVMRequest] = None) -> empty_pb2.Empty:
        """Describe the empty acknowledgments returned by supported lifecycle operations."""

    def call(self, method: str, request: Optional[vm.CreateVMRequest] = None
             ) -> Union[vm.PropertiesVMResponse, empty_pb2.Empty]:
        """Serialize a service request and turn transport/status failures into actionable CLI errors."""
        message = request if request is not None else empty_pb2.Empty()
        response_type = vm.PropertiesVMResponse if method == 'PropertiesVM' else empty_pb2.Empty
        if method == 'PropertiesVM':
            message = vm.PropertiesVMRequest()
        # Bind to the pinned upstream service without another generated grpc stub dependency.
        rpc = self.channel.unary_unary(
            '/vmservice.VM/' + method,
            request_serializer=type(message).SerializeToString,
            response_deserializer=response_type.FromString)
        try:
            return rpc(message, timeout=self.remaining())
        except grpc.RpcError as error:
            raise CLIError('OpenVMM {} failed ({}): {}'.format(
                method, error.code().name, error.details())) from error
