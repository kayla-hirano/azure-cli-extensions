#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
    printf 'Run developer bootstrap as your normal user, not root.\n' >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export ENCLAVE_REPO="${ENCLAVE_REPO:-$(cd -- "$script_dir/../../.." && pwd)}"
export ENCLAVE_VENV="${ENCLAVE_VENV:-$HOME/.venvs/enclave-dev}"

if [[ ! -f "$ENCLAVE_REPO/src/enclave/setup.py" ]]; then
    printf 'Enclave checkout not found: %s\n' "$ENCLAVE_REPO/src/enclave/setup.py" >&2
    exit 1
fi
if [[ "$ENCLAVE_VENV" != /* ]]; then
    printf 'ENCLAVE_VENV must be an absolute path.\n' >&2
    exit 1
fi
for tool in python3.12 git sudo; do
    if ! command -v "$tool" >/dev/null; then
        printf 'Required developer tool not found: %s\n' "$tool" >&2
        exit 1
    fi
done

umask 077
if [[ ! -e "$ENCLAVE_VENV" ]]; then
    python3.12 -m venv "$ENCLAVE_VENV"
elif [[ ! -f "$ENCLAVE_VENV/pyvenv.cfg" || ! -x "$ENCLAVE_VENV/bin/python" ]]; then
    printf 'Existing ENCLAVE_VENV is not a usable virtual environment: %s\n' "$ENCLAVE_VENV" >&2
    exit 1
fi
source "$ENCLAVE_VENV/bin/activate"
export AZURE_CONFIG_DIR="$ENCLAVE_VENV/azure-config"
export AZURE_EXTENSION_DIR="$ENCLAVE_VENV/extensions"
mkdir -p -- "$AZURE_CONFIG_DIR" "$AZURE_EXTENSION_DIR"
chmod 700 "$AZURE_CONFIG_DIR" "$AZURE_EXTENSION_DIR"

python -m pip install azdev setuptools wheel
"$ENCLAVE_VENV/bin/azdev" setup --cli pypi --repo "$ENCLAVE_REPO"
"$ENCLAVE_VENV/bin/azdev" extension add enclave
python -m pip install --no-build-isolation \
    --config-settings editable_mode=compat -e "$ENCLAVE_REPO/src/enclave"
python -c 'import grpc; import google.protobuf; import azext_enclave'

{
    printf 'source %q\n' "$ENCLAVE_VENV/bin/activate"
    printf 'export ENCLAVE_REPO=%q\n' "$ENCLAVE_REPO"
    printf 'export ENCLAVE_VENV=%q\n' "$ENCLAVE_VENV"
    printf 'export AZURE_CONFIG_DIR=%q\n' "$AZURE_CONFIG_DIR"
    printf 'export AZURE_EXTENSION_DIR=%q\n' "$AZURE_EXTENSION_DIR"
} > "$ENCLAVE_VENV/activate-enclave.sh"
printf 'Developer environment ready. In your shell, run:\nsource %q\n' "$ENCLAVE_VENV/activate-enclave.sh"
