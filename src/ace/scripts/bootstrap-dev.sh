#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
    printf 'Run developer bootstrap as your normal user, not root.\n' >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export ACE_REPO="${ACE_REPO:-$(cd -- "$script_dir/../../.." && pwd)}"
export ACE_VENV="${ACE_VENV:-$HOME/.venvs/ace-dev}"

if [[ ! -f "$ACE_REPO/src/ace/setup.py" ]]; then
    printf 'ACE checkout not found: %s\n' "$ACE_REPO/src/ace/setup.py" >&2
    exit 1
fi
if [[ "$ACE_VENV" != /* ]]; then
    printf 'ACE_VENV must be an absolute path.\n' >&2
    exit 1
fi
for tool in python3.12 git sudo; do
    if ! command -v "$tool" >/dev/null; then
        printf 'Required developer tool not found: %s\n' "$tool" >&2
        exit 1
    fi
done

umask 077
if [[ ! -e "$ACE_VENV" ]]; then
    python3.12 -m venv "$ACE_VENV"
elif [[ ! -f "$ACE_VENV/pyvenv.cfg" || ! -x "$ACE_VENV/bin/python" ]]; then
    printf 'Existing ACE_VENV is not a usable virtual environment: %s\n' "$ACE_VENV" >&2
    exit 1
fi
source "$ACE_VENV/bin/activate"
export AZURE_CONFIG_DIR="$ACE_VENV/azure-config"
export AZURE_EXTENSION_DIR="$ACE_VENV/extensions"
mkdir -p -- "$AZURE_CONFIG_DIR" "$AZURE_EXTENSION_DIR"
chmod 700 "$AZURE_CONFIG_DIR" "$AZURE_EXTENSION_DIR"

python -m pip install azdev setuptools wheel
"$ACE_VENV/bin/azdev" setup --cli pypi --repo "$ACE_REPO"
"$ACE_VENV/bin/azdev" extension add ace
python -m pip install --no-build-isolation \
    --config-settings editable_mode=compat -e "$ACE_REPO/src/ace"
python -c 'import grpc; import google.protobuf; import azext_ace'

{
    printf 'source %q\n' "$ACE_VENV/bin/activate"
    printf 'export ACE_REPO=%q\n' "$ACE_REPO"
    printf 'export ACE_VENV=%q\n' "$ACE_VENV"
    printf 'export AZURE_CONFIG_DIR=%q\n' "$AZURE_CONFIG_DIR"
    printf 'export AZURE_EXTENSION_DIR=%q\n' "$AZURE_EXTENSION_DIR"
} > "$ACE_VENV/activate-ace.sh"
printf 'Developer environment ready. In your shell, run:\nsource %q\n' "$ACE_VENV/activate-ace.sh"
