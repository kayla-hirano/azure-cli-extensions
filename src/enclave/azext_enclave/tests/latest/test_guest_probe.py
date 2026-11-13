# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Exercise relay framing, readiness deadlines, and SNP response validation."""

import io
import json
import socket
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from knack.util import CLIError

from azext_enclave import _instance, _lifecycle
from ._helpers import LinuxFilesystemTest


class RelayLineTest(unittest.TestCase):
    """Check bounded relay reads independently of guest startup."""

    def test_unimplemented_readiness_fails_before_guest_exchange(self) -> None:
        """Reject unsupported isolation evidence handling before reading runtime state or opening a connection."""
        with patch('azext_enclave._lifecycle.read_record') as read, \
                patch('azext_enclave._lifecycle.socket.socket') as connect:
            with self.assertRaisesRegex(CLIError, 'Guest readiness is not implemented for isolation type: tdx'):
                _lifecycle.probe_guest(Path('unused'), time.monotonic() + 5, 'tdx')
            read.assert_not_called()
            connect.assert_not_called()

    def test_line_limits(self):
        """Retry empty connections but reject oversized or incomplete relay lines."""
        self.assertEqual(_lifecycle.read_line(io.BufferedReader(io.BytesIO(b'OK 4050\n'))), b'OK 4050\n')
        with self.assertRaisesRegex(ConnectionError, 'closed before'):
            _lifecycle.read_line(io.BufferedReader(io.BytesIO(b'')))
        for line in (b'unterminated', b'x' * (_lifecycle.MAX_LINE_BYTES + 1) + b'\n'):
            with self.subTest(line_size=len(line)), self.assertRaises(CLIError):
                _lifecycle.read_line(io.BufferedReader(io.BytesIO(line)))

    def test_buffered_lines_preserve_handshake_and_response_boundaries(self) -> None:
        """Read a buffered handshake and response separately without losing bytes between relay lines."""
        response = json.dumps({'evidence': {'tee': 'snp', 'report': 'ab' * 1184}}).encode() + b'\n'
        with io.BufferedReader(io.BytesIO(b'OK 4050\n' + response)) as incoming:
            stream = MagicMock(wraps=incoming)
            connection = MagicMock()
            deadline = time.monotonic() + 5
            self.assertEqual(_lifecycle.read_line(stream, connection, deadline), b'OK 4050\n')
            self.assertEqual(_lifecycle.read_line(stream, connection, deadline), response)
            self.assertEqual(stream.read.call_count, 2)
            self.assertEqual(connection.settimeout.call_count, 2)

    def test_line_size_limit_includes_newline(self) -> None:
        """Accept relay lines at the size limit and count their terminating newline toward that limit."""
        with patch('azext_enclave._lifecycle.MAX_LINE_BYTES', 16):
            for size in (15, 16, 17):
                with self.subTest(size=size), io.BufferedReader(
                        io.BytesIO(b'x' * (size - 1) + b'\n'), buffer_size=8) as stream:
                    if size <= 16:
                        self.assertEqual(_lifecycle.read_line(stream), b'x' * (size - 1) + b'\n')
                    else:
                        with self.assertRaisesRegex(CLIError, 'oversized'):
                            _lifecycle.read_line(stream)

    def test_chunked_read_rechecks_total_deadline(self) -> None:
        """Stop a partial relay read when the original deadline expires between buffered chunks."""
        stream = MagicMock()
        stream.peek.side_effect = [b'O', b'K\n']
        stream.read.return_value = b'O'
        connection = MagicMock()
        with patch('azext_enclave._lifecycle.time.monotonic', side_effect=[0, 2]):
            with self.assertRaisesRegex(CLIError, 'timed out'):
                _lifecycle.read_line(stream, connection, 1)
        stream.peek.assert_called_once_with(1)
        stream.read.assert_called_once_with(1)
        connection.settimeout.assert_called_once_with(1)

    def test_slow_guest_response_has_total_deadline(self):
        """Prevent a slow response from extending the caller's absolute readiness deadline."""
        with self.assertRaisesRegex(CLIError, 'timed out'):
            _lifecycle.read_line(io.BufferedReader(io.BytesIO(b'OK\n')), MagicMock(), time.monotonic() - 1)


class GuestProbeTest(LinuxFilesystemTest):
    """Test the existing relay protocol and the stronger SNP-shape readiness check."""

    def test_probe_flushes_buffered_requests_over_real_socket(self) -> None:
        """Flush the relay handshake and guest request so a real socket peer can respond."""
        response = json.dumps({'evidence': {'tee': 'snp', 'report': '00' * 1184}}).encode() + b'\n'
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        server.settimeout(3)
        connection = MagicMock(wraps=client)
        connection.__enter__.return_value = connection
        connection.connect.return_value = None

        def serve() -> dict:
            with server.makefile('rb') as stream:
                self.assertEqual(stream.readline(), b'CONNECT 4050\n')
                server.sendall(b'OK 4050\n')
                request = json.loads(stream.readline())
                server.sendall(response)
                return request

        with tempfile.TemporaryDirectory() as temporary, ThreadPoolExecutor(max_workers=1) as executor:
            directory = Path(temporary)
            _instance.write_record(directory / 'runtime.json', {'openvmm': {}, 'supervisor': {}})
            exchange = executor.submit(serve)
            with patch('azext_enclave._lifecycle.alive', return_value=True), \
                    patch('azext_enclave._lifecycle.socket.socket', return_value=connection):
                _lifecycle.probe_guest(directory, time.monotonic() + 5)
            self.assertEqual(exchange.result(timeout=3)['payload'], 'hello world')
            self.assertEqual((directory / 'response.json').read_bytes(), response)

    def test_probe_retries_empty_handshake_or_response(self) -> None:
        """Allow early relay closures during boot without accepting an empty guest response."""
        response = json.dumps({'evidence': {'tee': 'snp', 'report': '00' * 1184}}).encode() + b'\n'
        for initial in (b'', b'OK 4050\n'):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                _instance.write_record(directory / 'runtime.json', {'openvmm': {}, 'supervisor': {}})
                streams = []
                for data in (initial, b'OK 4050\n' + response):
                    stream = MagicMock()
                    incoming = io.BufferedReader(io.BytesIO(data))
                    stream.peek.side_effect = incoming.peek
                    stream.read.side_effect = incoming.read
                    streams.append(stream)
                with patch('azext_enclave._lifecycle.alive', return_value=True), \
                        patch('azext_enclave._lifecycle.socket.socket') as factory, \
                        patch('azext_enclave._lifecycle.time.sleep') as sleep, \
                        patch('azext_enclave._lifecycle.logger.debug') as debug:
                    connection = factory.return_value.__enter__.return_value
                    connection.makefile.return_value.__enter__.side_effect = streams
                    _lifecycle.probe_guest(directory, time.monotonic() + 5)
                    self.assertEqual(connection.connect.call_count, 2)
                    sleep.assert_called_once()
                    debug.assert_called_once()
                self.assertEqual((directory / 'response.json').read_bytes(), response)

    def test_probe_empty_connections_respect_deadline(self) -> None:
        """Keep repeated early closures bounded by the original readiness budget."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _instance.write_record(directory / 'runtime.json', {'openvmm': {}, 'supervisor': {}})
            clock = [0.0]

            def advance(seconds: float) -> None:
                """Advance the test clock instead of waiting for another boot-time retry."""
                clock[0] += seconds

            with patch('azext_enclave._lifecycle.alive', return_value=True), \
                    patch('azext_enclave._lifecycle.socket.socket') as factory, \
                    patch('azext_enclave._lifecycle.time.monotonic', side_effect=lambda: clock[0]), \
                    patch('azext_enclave._lifecycle.time.sleep', side_effect=advance):
                connection = factory.return_value.__enter__.return_value
                stream = connection.makefile.return_value.__enter__.return_value
                stream.peek.return_value = b''
                with self.assertRaisesRegex(CLIError, 'readiness timed out: Guest relay closed'):
                    _lifecycle.probe_guest(directory, 1.0)
                self.assertEqual(connection.connect.call_count, 2)
                self.assertFalse((directory / 'response.json').exists())

    def test_probe_accepts_only_snp_report_shape(self):
        """Preserve the guest request while rejecting mock, malformed, or incorrectly sized reports."""
        for evidence in (
                {'tee': 'snp', 'report': '00' * 1184},
                {'tee': 'mock', 'report': None},
                {'tee': 'snp', 'report': 'zz' * 1184},
                {'tee': 'snp', 'report': '00'},
                {'tee': 'mock', 'report': '00' * 1184},
                None,
                'snp',
                []):
            with self.subTest(evidence_type=type(evidence).__name__), \
                    tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                _instance.write_record(directory / 'runtime.json', {'openvmm': {}, 'supervisor': {}})
                response = json.dumps({'evidence': evidence}).encode() + b'\n'
                stream = MagicMock()
                incoming = io.BufferedReader(io.BytesIO(b'OK 4050\n' + response))
                stream.peek.side_effect = incoming.peek
                stream.read.side_effect = incoming.read
                with patch('azext_enclave._lifecycle.alive', return_value=True), \
                        patch('azext_enclave._lifecycle.socket.socket') as factory:
                    connection = factory.return_value.__enter__.return_value
                    connection.makefile.return_value.__enter__.return_value = stream
                    if evidence == {'tee': 'snp', 'report': '00' * 1184}:
                        _lifecycle.probe_guest(directory, time.monotonic() + 5)
                        self.assertEqual((directory / 'response.json').read_bytes(), response)
                        self.assertEqual(stream.write.call_args_list[0].args, (b'CONNECT 4050\n',))
                        request = json.loads(stream.write.call_args_list[1].args[0])
                        self.assertEqual(request['payload'], 'hello world')
                        self.assertEqual(len(request['challenge']), 64)
                        self.assertEqual(stream.flush.call_count, 2)
                    else:
                        with self.assertRaises(CLIError):
                            _lifecycle.probe_guest(directory, time.monotonic() + 5)
                        self.assertFalse((directory / 'response.json').exists())

    def test_probe_detects_exited_runtime(self):
        """Stop retrying guest readiness when the recorded runtime has already exited."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _instance.write_record(directory / 'runtime.json', {'openvmm': {}, 'supervisor': {}})
            with patch('azext_enclave._lifecycle.alive', return_value=False):
                with self.assertRaisesRegex(CLIError, 'exited'):
                    _lifecycle.probe_guest(directory, time.monotonic() + 1)
