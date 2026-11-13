# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Check the OpenVMM protobuf and gRPC transport contract."""

import time
from dataclasses import replace
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import grpc
from google.protobuf.empty_pb2 import Empty
from knack.util import CLIError

from azext_enclave import _rpc
from azext_enclave._isolation import load_isolation
from azext_enclave.rpc import vmservice_pb2 as vm


class RpcContractTest(unittest.TestCase):
    """Check protobuf mapping and the wire contract independently of OpenVMM execution."""

    def test_request_consumes_resolved_manifest_settings(self) -> None:
        """Serialize resolved resource, runtime, serial, PCIe, and explicit SNP settings into the RPC request."""
        configuration = load_isolation()
        configuration = replace(
            configuration, defaults=replace(configuration.defaults, processor_count=2, memory_mib=256),
            runtime=replace(configuration.runtime, disable_hv=True, disable_vmbus=False), serial_port=1,
            pcie=replace(configuration.pcie, segment=1, start_bus=2, end_bus=3,
                         low_mmio_base=0xe0000000, low_mmio_size=0x200000,
                         high_mmio_base=0x200000000, high_mmio_size=0x400000))
        request = _rpc.create_request(Path('image'), Path('instance'), 'demo', configuration)
        config = vm.CreateVMRequest.FromString(request.SerializeToString()).config
        self.assertEqual(config.processor_config.processor_count, 2)
        self.assertEqual(config.memory_config.memory_mb, 256)
        self.assertFalse(config.disable_vmbus)
        self.assertTrue(config.disable_hv)
        self.assertEqual(config.serial_config.ports[0].port, 1)
        self.assertTrue(config.serial_config.ports[0].connect)
        root = config.pcie.root_complexes[0]
        self.assertEqual((root.segment, root.start_bus, root.end_bus), (1, 2, 3))
        self.assertEqual((root.low_mmio_base, root.low_mmio), (0xe0000000, 0x200000))
        self.assertEqual((root.high_mmio_base, root.high_mmio), (0x200000000, 0x400000))
        self.assertEqual(config.isolation_config.WhichOneof('isolation_type'), 'snp')

    def test_request_rejects_unimplemented_isolation_and_boot_modes(self) -> None:
        """Reject isolation, backend, and boot combinations without an implemented RPC launch path."""
        configuration = load_isolation()
        for unsupported in (
                replace(configuration, isolation_type='tdx'),
                replace(configuration, runtime=replace(configuration.runtime, backend='kvm')),
                replace(configuration, supported_configuration=replace(
                    configuration.supported_configuration, boot_mode='uefi'))):
            with self.subTest(configuration=unsupported), self.assertRaisesRegex(
                    ValueError, 'RPC launch is not implemented|Unsupported runtime backend|only SNP'):
                _rpc.create_request(Path('image'), Path('instance'), 'demo', unsupported)

    def test_unmapped_boot_mode_is_rejected_by_preflight_and_request_mapping(self) -> None:
        """Require a boot personality mapping in both launch preflight and request construction."""
        configuration = load_isolation()
        configuration = replace(configuration, supported_configuration=replace(
            configuration.supported_configuration, boot_mode='uefi'))
        with patch.object(_rpc, 'validate_configuration'):
            with self.assertRaisesRegex(ValueError, 'RPC boot mode is not implemented: uefi'):
                _rpc.validate_launch_configuration(configuration)
        with patch.object(_rpc, 'validate_launch_configuration'):
            with self.assertRaisesRegex(ValueError, 'RPC boot mode is not implemented: uefi'):
                _rpc.create_request(Path('image'), Path('instance'), 'demo', configuration)

    def test_unmapped_rpc_isolation_never_produces_a_non_confidential_request(self) -> None:
        """Fail on an unmapped isolation type rather than emitting a request with isolation absent."""
        configuration = replace(load_isolation(), isolation_type='tdx')
        with patch.object(_rpc, 'validate_launch_configuration'):
            with self.assertRaisesRegex(ValueError, 'RPC isolation mapping is not implemented'):
                _rpc.create_request(Path('image'), Path('instance'), 'demo', configuration)

    def test_request_preserves_enclave_configuration(self):
        """Round-trip the request and verify the supported boot, resource, and device configuration."""
        directory = Path('/var/lib/enclave/instances/demo')
        request = _rpc.create_request(Path('/images/enclave.igvm'), directory, 'demo')
        decoded = vm.CreateVMRequest.FromString(request.SerializeToString())
        config = decoded.config
        self.assertEqual(decoded.log_id, 'demo')
        self.assertEqual(config.WhichOneof('BootConfig'), 'igvm')
        self.assertEqual(config.igvm.personality, vm.IgvmBoot.PERSONALITY_LINUX_DIRECT)
        self.assertEqual(config.isolation_config.WhichOneof('isolation_type'), 'snp')
        self.assertEqual(config.igvm.igvm_path, str(Path('/images/enclave.igvm')))
        self.assertEqual(config.memory_config.memory_mb, 160)
        self.assertEqual(config.processor_config.processor_count, 1)
        self.assertTrue(config.disable_vmbus)
        self.assertFalse(config.disable_hv)
        serial = config.serial_config.ports[0]
        self.assertEqual(serial.port, 0)
        self.assertTrue(serial.connect)
        self.assertEqual(serial.socket_path, str(directory / 'serial.sock'))
        root = config.pcie.root_complexes[0]
        self.assertEqual((root.name, root.start_bus, root.end_bus), ('rc0', 0, 1))
        self.assertEqual((root.low_mmio, root.low_mmio_base), (16 * 1024 * 1024, 0xf7000000))
        self.assertEqual((root.high_mmio, root.high_mmio_base), (1024 * 1024, 0x100000000))
        port = root.root_ports[0]
        self.assertEqual(port.name, 'vsock')
        self.assertEqual(port.attached.device.virtio.vsock.socket_path, str(directory / 'vsock.sock'))

    def test_unix_channel_uses_valid_http2_authority(self) -> None:
        """Keep the socket path out of the authority header rejected by OpenVMM's HTTP/2 server."""
        path = Path('/var/lib/enclave/instances/demo/rpc.sock')
        with patch('azext_enclave._rpc.grpc.insecure_channel') as channel:
            with _rpc.VmClient(path, time.monotonic() + 5):
                channel.assert_called_once_with(
                    'unix:' + str(path),
                    options=(('grpc.default_authority', 'localhost'),))
            channel.return_value.close.assert_called_once_with()

    def test_real_grpc_round_trip(self):
        """Exercise actual serialization and status handling against a localhost test service."""
        received = []

        def create(request, _context):
            """Record the decoded creation request and acknowledge it like the upstream service."""
            received.append(request)
            return Empty()

        server = grpc.server(ThreadPoolExecutor(max_workers=2))
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler('vmservice.VM', {
            'CreateVM': grpc.unary_unary_rpc_method_handler(
                create, request_deserializer=vm.CreateVMRequest.FromString,
                response_serializer=Empty.SerializeToString),
            'PropertiesVM': grpc.unary_unary_rpc_method_handler(
                lambda _r, _c: vm.PropertiesVMResponse(state=vm.Running),
                request_deserializer=vm.PropertiesVMRequest.FromString,
                response_serializer=vm.PropertiesVMResponse.SerializeToString),
            'Quit': grpc.unary_unary_rpc_method_handler(
                lambda _r, context: context.abort(
                    grpc.StatusCode.UNAVAILABLE, 'Stream removed (Socket closed)'),
                request_deserializer=Empty.FromString,
                response_serializer=Empty.SerializeToString),
        }),))
        port = server.add_insecure_port('127.0.0.1:0')
        server.start()
        channel = grpc.insecure_channel('127.0.0.1:{}'.format(port))
        try:
            with patch('azext_enclave._rpc.grpc.insecure_channel', return_value=channel):
                with _rpc.VmClient(Path('unused'), time.monotonic() + 5) as client:
                    client.ready()
                    client.call('CreateVM', _rpc.create_request(Path('enclave'), Path('instance'), 'demo'))
                    self.assertEqual(client.call('PropertiesVM').state, vm.Running)
                    with self.assertRaisesRegex(CLIError, 'UNIMPLEMENTED'):
                        client.call('ResumeVM')
                    with self.assertRaisesRegex(CLIError, 'Quit failed.*UNAVAILABLE') as failure:
                        client.call('Quit')
                    self.assertIsInstance(failure.exception.__cause__, grpc.RpcError)
                    self.assertEqual(failure.exception.__cause__.code(), grpc.StatusCode.UNAVAILABLE)
            self.assertEqual(received[0].log_id, 'demo')
        finally:
            channel.close()
            server.stop(0).wait()

    def test_rpc_deadline_and_readiness_errors(self):
        """Surface expired operation budgets and endpoints that never become ready."""
        with _rpc.VmClient(Path('unused'), time.monotonic() - 1) as client:
            with self.assertRaisesRegex(CLIError, 'timed out'):
                client.call('Quit')
        with patch('azext_enclave._rpc.grpc.channel_ready_future') as ready:
            ready.return_value.result.side_effect = grpc.FutureTimeoutError()
            with _rpc.VmClient(Path('unused'), time.monotonic() + 1) as client:
                with self.assertRaisesRegex(CLIError, 'did not become ready'):
                    client.ready()
