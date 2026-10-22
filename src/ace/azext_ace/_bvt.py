# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Fixed SKR BVT demo adapter; not a general-purpose enclave image builder."""

import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from knack.log import get_logger
from knack.util import CLIError

logger = get_logger(__name__)

OPENVMM_REVISION = '6b350086b9c46fa7f8783240c808597454c6399b'
DEPS_REVISION = '6c779d3d6c0eaa0ba3bf361ac504866bfe86c6bd'
KERNEL_RELEASE = '6.18.34.mshv4'
OPENVMM_PACKAGE = '0.2.0-2.azl3'
ACE_URL = 'https://msazure.visualstudio.com/DefaultCollection/One/_git/ACC-CVM-Tooling'
ACE_BRANCH = 'feature/skr-bvt-igvm'
ACE_REVISION = 'd2dddacf713341f0062b68dba27886d00ca9de1a'
RUST_TOOLCHAIN = '1.95.0'
BUNDLE_FILES = (
    'skr-bvt.igvm', 'skr-bvt.igvm.map', 'skr-bvt-snp.json', 'skr-bvt-snp.cbor', 'skr-bvt-snp.idblock',
    'skr-bvt-initrd.cpio.gz', 'bzImage', 'kernel.config', 'snp_bootshim',
    'run-skr-bvt.sh', 'skr-bvt-client.py', 'igvm-manifest.json', 'resources.json', 'INPUTS.txt', 'SHA256SUMS',
)

# Install providers only when their executable is absent; never upgrade the host.
TOOLS = {
    'bash': 'bash', 'sh': 'bash', 'python3': 'python3',
    'git': 'git', 'curl': 'curl', 'gcc': 'gcc', 'cc': 'gcc',
    'ld': 'binutils', 'make': 'make', 'perl': 'perl',
    'cpio': 'cpio', 'gzip': 'gzip', 'find': 'findutils', 'xargs': 'findutils',
    'awk': 'gawk', 'grep': 'grep', 'sed': 'sed', 'tar': 'tar',
    'diff': 'diffutils', 'docker': 'docker-cli', 'dockerd': 'moby-engine',
    'sha256sum': 'coreutils', 'realpath': 'coreutils', 'install': 'coreutils',
    'sort': 'coreutils', 'touch': 'coreutils', 'mktemp': 'coreutils',
    'cp': 'coreutils', 'mv': 'coreutils', 'rm': 'coreutils', 'mkdir': 'coreutils',
    'rpm': 'rpm', 'systemctl': 'systemd', 'openvmm': 'openvmm-' + OPENVMM_PACKAGE,
}


def _environment():
    env = os.environ.copy()
    env['PATH'] = str(Path.home() / '.cargo' / 'bin') + os.pathsep + env.get('PATH', '')
    env['TARGET'] = 'x86_64-unknown-linux-musl'
    env['DOCKER_BUILDKIT'] = '1'
    env['RUSTUP_TOOLCHAIN'] = RUST_TOOLCHAIN
    return env


def _run(args, cwd=None, capture=False, env=None):
    logger.info('ACE BVT: running %s', args[0])
    try:
        result = subprocess.run(
            [str(arg) for arg in args], cwd=cwd, env=env or _environment(),
            check=True, text=True, stdout=subprocess.PIPE if capture else sys.stderr)
    except subprocess.CalledProcessError as error:
        raise CLIError(
            "ACE BVT command '{}' failed (exit {}). See command output above; "
            "correct the failure and retry.".format(args[0], error.returncode)) from error
    except OSError as error:
        raise CLIError("Cannot execute '{}': {}".format(args[0], error)) from error
    return result.stdout.strip() if capture else None


def _require_platform():
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise CLIError('This demo requires x86-64 Azure Linux L1. Run az inside that Linux host.')
    try:
        release = Path('/etc/os-release').read_text(encoding='utf-8')
    except OSError as error:
        raise CLIError('Cannot read /etc/os-release: {}'.format(error)) from error
    values = dict(line.split('=', 1) for line in release.splitlines() if '=' in line)
    if values.get('ID', '').strip('"\'') not in ('azurelinux', 'mariner'):
        raise CLIError('Automatic dependency installation is supported only on Azure Linux (tdnf).')


def _paths(workspace, source_dir):
    work = Path(workspace or '~/.azure/ace/bvt').expanduser().resolve()
    source = Path(source_dir).expanduser().resolve() if source_dir else work / 'ACC-CVM-Tooling'
    return work, source


def _require_tools(names):
    missing = sorted(name for name in names if not shutil.which(name, path=_environment()['PATH']))
    if missing:
        raise CLIError('Missing dependencies: {}. Run az ace setup on Azure Linux L1.'.format(', '.join(missing)))


def _privileged(args):
    if os.geteuid() == 0:
        return args
    _require_tools(['sudo'])
    return ['sudo'] + args


def _install_tools():
    _require_tools(['tdnf'])
    packages = {package for tool, package in TOOLS.items() if not shutil.which(tool)}
    if not Path('/etc/pki/tls/certs/ca-bundle.crt').is_file():
        packages.add('ca-certificates')
    if not Path('/usr/libexec/docker/cli-plugins/docker-buildx').is_file():
        packages.add('docker-buildx')
    if packages:
        _run(_privileged(['tdnf', 'install', '-y'] + sorted(packages)))
    _require_tools(TOOLS)
    _run(_privileged(['systemctl', 'start', 'docker']))
    # Do not change docker-group membership or socket permissions.
    try:
        _run(['docker', 'info'])
    except CLIError as error:
        raise CLIError(
            'Docker is not accessible to the current user. Run under an account already authorized '
            'to use Docker (or root). Setup does not change socket permissions or group membership.') from error
    _run(['docker', 'buildx', 'version'])
    _run(['docker', 'buildx', 'inspect', '--bootstrap'])


def _prepare_rust():
    if not shutil.which('rustup', path=_environment()['PATH']):
        with tempfile.TemporaryDirectory(prefix='ace-rustup-') as temporary:
            installer = Path(temporary) / 'rustup.sh'
            _run(['curl', '--proto', '=https', '--tlsv1.2', '-fsSL',
                  'https://sh.rustup.rs', '-o', installer])
            _run(['sh', installer, '-y', '--no-modify-path', '--default-toolchain', 'none'])
    _require_tools(['rustup', 'cargo'])
    _run(['rustup', 'toolchain', 'install', RUST_TOOLCHAIN, '--profile', 'minimal',
          '--target', 'x86_64-unknown-none', '--target', 'x86_64-unknown-linux-musl'])


def _validate_host(check_package=True):
    if platform.release() != KERNEL_RELEASE:
        raise CLIError(
            'This fixed BVT profile requires L1 kernel {}; found {}. '
            'Use the validated L1 image; setup does not replace kernels.'.format(
                KERNEL_RELEASE, platform.release()))
    device = Path('/dev/mshv')
    if not device.is_char_device() or not os.access(str(device), os.R_OK | os.W_OK):
        raise CLIError('/dev/mshv must be a character device accessible for reading and writing by the launcher.')
    if check_package:
        _require_tools(['rpm', 'openvmm'])
        executable = shutil.which('openvmm', path=_environment()['PATH'])
        package = _run(['rpm', '-qf', '--qf', '%{NAME}-%{VERSION}-%{RELEASE}', executable], capture=True)
        if package != 'openvmm-' + OPENVMM_PACKAGE:
            raise CLIError('Expected packaged OpenVMM {}; found {} at {}.'.format(
                OPENVMM_PACKAGE, package, executable))


def _checkout(destination, url, revision=None, branch=None):
    if not destination.exists():
        args = ['git', 'clone']
        if branch:
            args += ['--branch', branch]
        _run(args + [url, destination])
        if revision:
            _run(['git', '-C', destination, 'checkout', '--detach', revision])
    if not (destination / '.git').exists():
        raise CLIError('Expected a Git checkout at {}. Move the conflicting directory and retry.'.format(destination))
    if revision:
        _validate_revision(destination, revision)


def _validate_revision(repository, revision):
    actual = _run(['git', '-C', repository, 'rev-parse', 'HEAD'], capture=True)
    if actual != revision:
        raise CLIError('Expected revision {} at {}; found {}. Existing checkouts are not reset.'.format(
            revision, repository, actual))


def _require_files(paths):
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise CLIError('Missing or empty BVT input: {}. Run az ace setup.'.format(path))


def _scripts(source):
    scripts = source / 'src' / 'ace' / 'scripts'
    _require_files([scripts / name for name in (
        'prepare-openvmm-igvmfilegen.sh', 'build-skr-bvt.sh', 'build-enclave-app.sh',
        'package-enclave-initramfs.sh', 'generate-igvm.sh', 'run-skr-bvt.sh', 'skr-bvt-client.py')])
    return scripts


def _inputs(work):
    return [
        work / 'inputs' / 'linux-snp-guest' / 'bzImage',
        work / 'inputs' / 'linux-snp-guest' / 'config',
        work / 'inputs' / 'base-initrd' / 'initrd',
        work / 'openvmm' / 'target' / 'x86_64-unknown-none' / 'boot-dev' / 'snp_bootshim',
    ]


def _validate_inputs(work, source):
    _scripts(source)
    _validate_revision(work / 'openvmm', OPENVMM_REVISION)
    _validate_revision(work / 'openvmm-deps', DEPS_REVISION)
    if _run(['git', '-C', work / 'openvmm-deps', 'status', '--porcelain'], capture=True):
        raise CLIError('openvmm-deps must be clean so the pinned Dockerfile and kernel inputs are used.')
    _run(['git', '-C', work / 'openvmm', 'apply', '--reverse', '--check',
          source / 'src' / 'ace' / 'patches' / 'openvmm-igvmfilegen-measured-pcie.patch'])
    # Compare with the expected patched tree without changing the user's Git index.
    with tempfile.TemporaryDirectory(prefix='ace-index-') as temporary:
        env = _environment()
        env['GIT_INDEX_FILE'] = str(Path(temporary) / 'index')
        _run(['git', 'read-tree', 'HEAD'], cwd=work / 'openvmm', env=env)
        _run(['git', 'apply', '--cached',
              source / 'src' / 'ace' / 'patches' / 'openvmm-igvmfilegen-measured-pcie.patch'],
             cwd=work / 'openvmm', env=env)
        _run(['git', 'diff', '--exit-code'], cwd=work / 'openvmm', env=env)
    _require_files(_inputs(work))
    if not os.access(str(_inputs(work)[3]), os.X_OK):
        raise CLIError('SNP bootshim is not executable. Run az ace setup.')


def setup_bvt(workspace=None, source_dir=None):
    _require_platform()
    _validate_host(check_package=bool(shutil.which('openvmm', path=_environment()['PATH'])))
    _install_tools()
    _validate_host()
    _prepare_rust()
    work, source = _paths(workspace, source_dir)
    work.mkdir(parents=True, exist_ok=True)
    if source_dir:
        _scripts(source)
    else:
        _checkout(source, ACE_URL, revision=ACE_REVISION, branch=ACE_BRANCH)
    scripts = _scripts(source)
    openvmm = work / 'openvmm'
    deps = work / 'openvmm-deps'
    _checkout(openvmm, 'https://github.com/microsoft/openvmm.git', OPENVMM_REVISION)
    _checkout(deps, 'https://github.com/microsoft/openvmm-deps.git', DEPS_REVISION)
    _run(['bash', scripts / 'prepare-openvmm-igvmfilegen.sh', openvmm])
    _run(['cargo', 'xflowey', 'restore-packages'], cwd=openvmm)
    _run([openvmm / '.packages' / 'Google.Protobuf.Tools' / 'tools' / 'protoc', '--version'],
         cwd=openvmm)
    env = _environment()
    env['MINIMAL_RT_BUILD'] = '1'
    _run(['cargo', 'build', '--profile', 'boot-dev', '--target', 'x86_64-unknown-none',
          '-p', 'snp_bootshim'], cwd=openvmm, env=env)
    _run(['cargo', 'build', '-p', 'igvmfilegen'], cwd=openvmm)
    for target, directory in (('result-linux-snp-guest', 'linux-snp-guest'), ('result-initrd', 'base-initrd')):
        destination = work / 'inputs' / directory
        destination.mkdir(parents=True, exist_ok=True)
        _run(['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--target', target,
              '--output', 'type=local,dest={}'.format(destination), '-f', 'Dockerfile', '.'], cwd=deps)
    _validate_inputs(work, source)
    checksums = _run(['sha256sum'] + [path.relative_to(work) for path in _inputs(work)],
                     cwd=work, capture=True)
    (work / 'setup-inputs.sha256').write_text(checksums + '\n', encoding='utf-8')
    logger.warning('BVT build inputs ready. No enclave has been launched and no attestation has been verified.')
    return {'status': 'ready', 'profile': 'skr-bvt', 'workspace': str(work), 'sourceDir': str(source),
            'openvmmRevision': OPENVMM_REVISION, 'openvmmDepsRevision': DEPS_REVISION}


def create_bvt(workspace=None, source_dir=None, output_dir=None):
    _require_platform()
    work, source = _paths(workspace, source_dir)
    output = Path(output_dir).expanduser().resolve() if output_dir else work / 'skr-bvt'
    if output.exists():
        raise CLIError('Output directory must not already exist: {}. Choose another --output-dir.'.format(output))
    _require_tools([name for name in TOOLS if name not in ('docker', 'dockerd', 'systemctl', 'openvmm', 'rpm')]
                   + ['cargo', 'rustup'])
    _validate_inputs(work, source)
    _require_files([work / 'setup-inputs.sha256'])
    _run(['sha256sum', '--strict', '--check', 'setup-inputs.sha256'], cwd=work)
    targets = _run(['rustup', 'target', 'list', '--installed'], cwd=source / 'src' / 'ace', capture=True)
    if 'x86_64-unknown-linux-musl' not in targets.splitlines():
        raise CLIError('Missing Rust target x86_64-unknown-linux-musl. Run az ace setup.')
    _run(['bash', _scripts(source) / 'build-skr-bvt.sh', work / 'openvmm'] + _inputs(work) + [output],
         cwd=source / 'src' / 'ace')
    _require_files([output / name for name in BUNDLE_FILES])
    _run(['sha256sum', '--strict', '--check', 'SHA256SUMS'], cwd=output)
    return {'status': 'created', 'profile': 'skr-bvt', 'igvm': str(output / 'skr-bvt.igvm'),
            'outputDir': str(output), 'provenance': str(output / 'INPUTS.txt'),
            'checksums': str(output / 'SHA256SUMS'),
            'artifacts': [str(path) for path in sorted(output.iterdir()) if path.is_file()]}
