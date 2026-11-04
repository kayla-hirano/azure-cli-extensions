# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Test protocol provenance and regeneration without network calls or a compiler."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('enclave_generate_protocol', ROOT / 'scripts' / 'generate_protocol.py')
protocol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(protocol)


class ProtocolGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'scripts').mkdir()
        self.rpc = self.root / 'azext_enclave' / 'rpc'
        self.rpc.mkdir(parents=True)
        self.schema = b'syntax = "proto3";\n'
        self.pins = protocol.read_pins(ROOT)
        self.pins['schema_sha256'] = hashlib.sha256(self.schema).hexdigest()
        self.write_pins()
        (self.root / 'azext_enclave' / '_runtime.py').write_text(
            'RUNTIME_VERSION = {!r}\n'.format(self.pins['runtime_version']), encoding='utf-8')
        (self.rpc / 'vmservice.proto').write_bytes(self.schema)
        self.outputs = {
            'vmservice_pb2.py': b'# -*- coding: utf-8 -*-\n# flake8: noqa\n# pylint: skip-file\nVALUE = 1\n',
            'vmservice_pb2.pyi': b'VALUE: int\n',
        }
        for name, content in self.outputs.items():
            (self.rpc / name).write_bytes(content)

    def write_pins(self) -> None:
        (self.root / 'scripts' / 'openvmm-protocol.json').write_text(
            json.dumps(self.pins), encoding='utf-8')

    def test_committed_schema_and_runtime_match_pins(self) -> None:
        pins = protocol.read_pins(ROOT)
        schema = (ROOT / 'azext_enclave' / 'rpc' / 'vmservice.proto').read_bytes()
        protocol.verify_schema(schema, pins)
        requirements = (ROOT / 'scripts' / 'requirements-protocol.txt').read_text(encoding='utf-8')
        self.assertIn('grpcio-tools==' + pins['grpcio_tools_version'], requirements.splitlines())

    def test_invalid_commit_or_checksum_is_rejected(self) -> None:
        for key in ('upstream_commit', 'schema_sha256'):
            with self.subTest(key=key):
                original = self.pins[key]
                self.pins[key] = '../main'
                self.write_pins()
                with self.assertRaisesRegex(ValueError, 'Invalid protocol pin'):
                    protocol.read_pins(self.root)
                self.pins[key] = original

    def test_missing_pin_is_rejected(self) -> None:
        del self.pins['upstream_commit']
        self.write_pins()
        with self.assertRaisesRegex(ValueError, 'nonempty strings'):
            protocol.read_pins(self.root)

    def test_runtime_upgrade_requires_protocol_pin_update(self) -> None:
        self.pins['runtime_version'] = 'another-version'
        self.write_pins()
        with self.assertRaisesRegex(ValueError, 'must match RUNTIME_VERSION'):
            protocol.read_pins(self.root)

    def test_checksum_normalizes_windows_line_endings(self) -> None:
        self.assertEqual(protocol.verify_schema(self.schema.replace(b'\n', b'\r\n'), self.pins), self.schema)

    def test_download_uses_immutable_commit_and_timeout(self) -> None:
        with patch.object(protocol, 'urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = self.schema
            self.assertEqual(protocol.download_schema(self.pins), self.schema)
        url = request.call_args.args[0]
        self.assertIn('/' + self.pins['upstream_commit'] + '/', url)
        self.assertTrue(url.endswith(protocol.SCHEMA_PATH))
        self.assertEqual(request.call_args.kwargs['timeout'], 30)

    def test_download_rejects_schema_checksum_mismatch(self) -> None:
        with patch.object(protocol, 'urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = b'changed upstream schema'
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                protocol.download_schema(self.pins)

    def test_check_is_offline_and_does_not_write(self) -> None:
        original = {path.name: path.read_bytes() for path in self.rpc.iterdir()}
        with patch.object(protocol, 'download_schema') as download:
            with patch.object(protocol, 'generate', return_value=self.outputs) as generate:
                protocol.synchronize(self.root, check=True)
        download.assert_not_called()
        generate.assert_called_once_with(self.schema, self.pins)
        self.assertEqual(original, {path.name: path.read_bytes() for path in self.rpc.iterdir()})

    def test_check_detects_modified_or_missing_bindings(self) -> None:
        for name in protocol.GENERATED_FILES:
            with self.subTest(name=name):
                (self.rpc / name).write_bytes(b'# edited\n')
                with patch.object(protocol, 'generate', return_value=self.outputs):
                    with self.assertRaisesRegex(ValueError, name):
                        protocol.synchronize(self.root, check=True)
                (self.rpc / name).unlink()
                with patch.object(protocol, 'generate', return_value=self.outputs):
                    with self.assertRaisesRegex(ValueError, name):
                        protocol.synchronize(self.root, check=True)
                (self.rpc / name).write_bytes(self.outputs[name])

    def test_bad_local_schema_fails_before_generation(self) -> None:
        (self.rpc / 'vmservice.proto').write_bytes(b'changed schema')
        with patch.object(protocol, 'generate') as generate:
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                protocol.synchronize(self.root, check=True)
        generate.assert_not_called()

    def test_write_restores_both_bindings_and_schema(self) -> None:
        for path in self.rpc.iterdir():
            path.write_bytes(b'old content')
        with patch.object(protocol, 'download_schema', return_value=self.schema):
            with patch.object(protocol, 'generate', return_value=self.outputs):
                protocol.synchronize(self.root, check=False)
        for name, content in self.outputs.items():
            self.assertEqual((self.rpc / name).read_bytes(), content)
        self.assertEqual((self.rpc / 'vmservice.proto').read_bytes(), self.schema)

    def test_compiler_failure_leaves_existing_files_untouched(self) -> None:
        original = {path.name: path.read_bytes() for path in self.rpc.iterdir()}
        with patch.object(protocol, 'download_schema', return_value=self.schema):
            with patch.object(protocol, 'generate', side_effect=subprocess.CalledProcessError(1, 'protoc')):
                with self.assertRaises(subprocess.CalledProcessError):
                    protocol.synchronize(self.root, check=False)
        self.assertEqual(original, {path.name: path.read_bytes() for path in self.rpc.iterdir()})

    def test_write_preserves_unchanged_files_and_checkout_line_endings(self) -> None:
        for path in self.rpc.iterdir():
            path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        with patch.object(protocol, 'download_schema', return_value=self.schema):
            with patch.object(protocol, 'generate', return_value=self.outputs):
                with patch.object(Path, 'write_bytes') as write:
                    protocol.synchronize(self.root, check=False)
        write.assert_not_called()

    def test_missing_or_wrong_generator_is_rejected(self) -> None:
        with patch.object(protocol.metadata, 'version', side_effect=protocol.metadata.PackageNotFoundError):
            with self.assertRaisesRegex(RuntimeError, 'developer-only venv'):
                protocol.generate(self.schema, self.pins)
        with patch.object(protocol.metadata, 'version', return_value='0.0.0'):
            with self.assertRaisesRegex(RuntimeError, 'Expected grpcio-tools'):
                protocol.generate(self.schema, self.pins)

    def test_wrong_compiler_version_is_rejected(self) -> None:
        with patch.object(protocol.metadata, 'version', return_value=self.pins['grpcio_tools_version']):
            with patch.object(protocol.subprocess, 'run', return_value=SimpleNamespace(stdout='libprotoc 99')):
                with self.assertRaisesRegex(RuntimeError, 'Expected libprotoc'):
                    protocol.generate(self.schema, self.pins)

    def test_generation_includes_stubs_and_reapplies_lint_exemption(self) -> None:
        def compile_schema(command, **_kwargs):
            if command[-1] == '--version':
                return SimpleNamespace(stdout=self.pins['protoc_version'])
            workspace = Path(next(item.split('=', 1)[1] for item in command if item.startswith('--python_out=')))
            self.assertIn('--pyi_out=' + str(workspace), command)
            self.assertEqual((workspace / 'vmservice.proto').read_bytes(), self.schema)
            (workspace / 'vmservice_pb2.py').write_bytes(b'# -*- coding: utf-8 -*-\nVALUE = 1\n')
            (workspace / 'vmservice_pb2.pyi').write_bytes(b'VALUE: int\n')
            return SimpleNamespace(returncode=0)

        with patch.object(protocol.metadata, 'version', return_value=self.pins['grpcio_tools_version']):
            with patch.object(protocol.util, 'find_spec', return_value=SimpleNamespace(origin=__file__)):
                with patch.object(protocol.subprocess, 'run', side_effect=compile_schema):
                    generated = protocol.generate(self.schema, self.pins)
        self.assertEqual(generated, self.outputs)


if __name__ == '__main__':
    unittest.main()
