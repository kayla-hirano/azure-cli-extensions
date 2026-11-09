# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Exercise the persistent runtime supervisor without starting OpenVMM."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from azext_enclave import _instance, _worker
from ._helpers import LinuxFilesystemTest


class WorkerTest(LinuxFilesystemTest):
    """Exercise the persistent supervisor with simulated server and serial connections."""

    def test_supervisor_captures_com1_and_reaps_server(self):
        """Capture guest serial bytes and record a clean child exit without unnecessary signals."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _instance.write_record(directory / 'instance.json', {'openvmm': '/usr/bin/openvmm'})
            with patch('azext_enclave._worker.socket.socket') as sockets, \
                    patch('azext_enclave._worker.selectors.DefaultSelector') as selectors, \
                    patch('azext_enclave._worker.subprocess.Popen') as popen, \
                    patch('azext_enclave._worker.identity', return_value={'pid': 123}), \
                    patch('azext_enclave._worker.time.monotonic', side_effect=[0, 2]), \
                    patch('azext_enclave._worker.signal.signal'):
                listener = sockets.return_value.__enter__.return_value
                connection = MagicMock()
                listener.accept.return_value = (connection, None)
                connection.recv.return_value = b'guest serial output\n'
                selector = selectors.return_value.__enter__.return_value
                selector.select.side_effect = [
                    [(SimpleNamespace(fileobj=listener), 1)],
                    [(SimpleNamespace(fileobj=connection), 1)],
                ]
                child = popen.return_value
                child.poll.side_effect = [None, None, 0, 0]
                child.returncode = 0
                _worker.supervise(directory, 47)
                arguments = popen.call_args.args[0]
                self.assertEqual(arguments[:2], ['/usr/bin/openvmm', '--rpc'])
                self.assertIn('transport=grpc', arguments[2])
                self.assertEqual(popen.call_args.kwargs['pass_fds'], (47,))
                self.assertEqual((directory / 'com1.log').read_bytes(), b'guest serial output\n')
                self.assertEqual(_instance.read_record(directory / 'exit.json')['returncode'], 0)
                child.terminate.assert_not_called()
