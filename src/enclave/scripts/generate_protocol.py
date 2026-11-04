# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Regenerate or verify Python bindings from a checksum-pinned OpenVMM schema."""

import argparse
import ast
import hashlib
from importlib import metadata, util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Dict
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = 'openvmm/openvmm_ttrpc_vmservice/src/vmservice.proto'
GENERATED_FILES = ('vmservice_pb2.py', 'vmservice_pb2.pyi')


def normalize(content: bytes) -> bytes:
    """Make checksums and comparisons independent of Git's checkout line endings."""
    return content.replace(b'\r\n', b'\n')


def read_pins(root: Path) -> Dict[str, str]:
    with (root / 'scripts' / 'openvmm-protocol.json').open(encoding='utf-8') as source:
        pins = json.load(source)
    required = (
        'upstream_tag', 'upstream_commit', 'schema_sha256', 'runtime_version',
        'grpcio_tools_version', 'protoc_version',
    )
    if not isinstance(pins, dict) or any(
            not isinstance(pins.get(key), str) or not pins[key] for key in required):
        raise ValueError('Protocol pins must contain nonempty strings for: ' + ', '.join(required))
    for key, length in (('upstream_commit', 40), ('schema_sha256', 64)):
        if not re.fullmatch('[0-9a-f]{' + str(length) + '}', pins[key]):
            raise ValueError('Invalid protocol pin: ' + key)
    runtime_source = root / 'azext_enclave' / '_runtime.py'
    runtime = ast.parse(runtime_source.read_text(encoding='utf-8'))
    versions = [
        ast.literal_eval(node.value) for node in runtime.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'RUNTIME_VERSION' for target in node.targets)
    ]
    if versions != [pins['runtime_version']]:
        raise ValueError('Protocol runtime_version must match RUNTIME_VERSION in azext_enclave/_runtime.py')
    return pins


def verify_schema(content: bytes, pins: Dict[str, str]) -> bytes:
    schema = normalize(content)
    if hashlib.sha256(schema).hexdigest() != pins['schema_sha256']:
        raise ValueError('OpenVMM schema SHA-256 mismatch; no files were changed')
    return schema


def download_schema(pins: Dict[str, str]) -> bytes:
    url = 'https://raw.githubusercontent.com/microsoft/openvmm/{}/{}'.format(
        pins['upstream_commit'], SCHEMA_PATH)
    with urlopen(url, timeout=30) as response:
        return verify_schema(response.read(), pins)


def generate(schema: bytes, pins: Dict[str, str]) -> Dict[str, bytes]:
    try:
        installed = metadata.version('grpcio-tools')
    except metadata.PackageNotFoundError as error:
        raise RuntimeError('Install scripts/requirements-protocol.txt in a developer-only venv') from error
    if installed != pins['grpcio_tools_version']:
        raise RuntimeError('Expected grpcio-tools {}; found {}'.format(pins['grpcio_tools_version'], installed))
    compiler = [sys.executable, '-m', 'grpc_tools.protoc']
    version = subprocess.run(compiler + ['--version'], capture_output=True, text=True, check=True)
    if version.stdout.strip() != pins['protoc_version']:
        raise RuntimeError('Expected {}; found {}'.format(pins['protoc_version'], version.stdout.strip()))
    spec = util.find_spec('grpc_tools')
    if spec is None or spec.origin is None:
        raise RuntimeError('Cannot locate grpc_tools; reinstall scripts/requirements-protocol.txt')
    includes = Path(spec.origin).parent / '_proto'
    with tempfile.TemporaryDirectory(prefix='enclave-protocol-') as directory:
        workspace = Path(directory)
        (workspace / 'vmservice.proto').write_bytes(schema)
        subprocess.run(
            compiler + [
                '-I', str(workspace), '-I', str(includes),
                '--python_out=' + str(workspace), '--pyi_out=' + str(workspace), 'vmservice.proto',
            ],
            cwd=workspace, check=True)
        generated = {name: normalize((workspace / name).read_bytes()) for name in GENERATED_FILES}
    # Keep the exemption enclave-local and reproducible instead of editing repository-wide lint settings.
    generated['vmservice_pb2.py'] = b'# flake8: noqa\n' + generated['vmservice_pb2.py']
    for name, content in generated.items():
        ast.parse(content, filename=name)
    return generated


def synchronize(root: Path, check: bool) -> None:
    pins = read_pins(root)
    rpc = root / 'azext_enclave' / 'rpc'
    schema = verify_schema((rpc / 'vmservice.proto').read_bytes(), pins) if check else download_schema(pins)
    outputs = generate(schema, pins)
    outputs['vmservice.proto'] = schema
    if check:
        changed = [name for name, content in outputs.items()
                   if not (rpc / name).is_file() or normalize((rpc / name).read_bytes()) != content]
        if changed:
            raise ValueError('Protocol files differ from pinned generation: ' + ', '.join(changed))
    else:
        for name, content in outputs.items():
            path = rpc / name
            if not path.is_file() or normalize(path.read_bytes()) != content:
                path.write_bytes(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Verify committed schema and bindings offline; write nothing')
    mode.add_argument('--write', action='store_true', help='Download pinned upstream schema and regenerate bindings')
    args = parser.parse_args()
    try:
        synchronize(ROOT, check=args.check)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, 'Protocol generation failed: {}\n'.format(error))
    print('Pinned OpenVMM protocol {}'.format('verified (offline)' if args.check else 'regenerated'))


if __name__ == '__main__':
    main()
