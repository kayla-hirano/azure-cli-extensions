# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Translate BVT launch settings to the pinned protobuf contract and issue bounded local gRPC calls."""

import time
from pathlib import Path
from typing import Literal, Optional, Union, overload

import grpc
from google.protobuf.empty_pb2 import Empty
from knack.util import CLIError

from .rpc import vmservice_pb2 as vm


def create_request(image: Path, directory: Path, name: str) -> vm.CreateVMRequest:
    """Build the RPC equivalent of the BVT's SNP IGVM, COM1, CPU, memory, and PCIe arguments."""
    request = vm.CreateVMRequest(log_id=name)
    config = request.config
    config.igvm.igvm_path = str(image)
    config.igvm.personality = vm.IgvmBoot.PERSONALITY_LINUX_DIRECT
    # Selecting the empty SNP submessage is essential: an absent isolation config means no isolation.
    config.isolation_config.snp.SetInParent()
    config.disable_vmbus = True
    config.disable_hv = False
    config.memory_config.memory_mb = 160
    config.processor_config.processor_count = 1
    # COM1 is zero-indexed in RPC. The supervisor replaces the BVT's --com1 file=... backend.
    config.serial_config.ports.add(port=0, socket_path=str(directory / 'serial.sock'), connect=True)
    # Match run-skr-bvt.sh and the measured pcie_host_bridge in snp-linux-direct-skr-bvt.json.
    root = config.pcie.root_complexes.add(
        name='rc0', segment=0, start_bus=0, end_bus=1,
        low_mmio=16 * 1024 * 1024, low_mmio_base=0xf7000000,
        high_mmio=1024 * 1024, high_mmio_base=0x100000000)
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
        ...

    @overload
    def call(self, method: Literal['CreateVM', 'ResumeVM', 'TeardownVM', 'Quit'],
             request: Optional[vm.CreateVMRequest] = None) -> Empty:
        """Describe the empty acknowledgments returned by supported lifecycle operations."""
        ...

    def call(self, method: str, request: Optional[vm.CreateVMRequest] = None
             ) -> Union[vm.PropertiesVMResponse, Empty]:
        """Serialize a service request and turn transport/status failures into actionable CLI errors."""
        message = request if request is not None else Empty()
        response_type = vm.PropertiesVMResponse if method == 'PropertiesVM' else Empty
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
