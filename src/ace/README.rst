Microsoft Azure CLI 'ace' Extension
==========================================

This package is for the 'ace' extension.
i.e. 'az ace'

Commands
--------

* ``az ace setup``: Set up enclave dependencies (placeholder).
* ``az ace create``: Pack the workload into the initrd and create the enclave IGVM file (placeholder).
* ``az ace launch``: Create and resume a local SNP IGVM VM through OpenVMM gRPC.
* ``az ace destroy``: Tear down the VM, quit its OpenVMM server and clean up owned sockets.

Launch and destroy are an initial Linux-L1 implementation for the existing
SKR BVT image, not a general image launcher. They preserve its 160 MB memory,
one processor, Linux-direct SNP boot, measured PCIe topology, and guest port
4050. Setup and create remain unimplemented.

Prerequisites
-------------

Run locally as root on the same Linux L1 where the CLI-based SKR BVT passed.
The extension must be installed in the Azure CLI environment used by root.
The existing IGVM, compatible L1 kernel and ``/dev/mshv`` must be available.
Host provisioning and image generation are not performed by this extension.

The expected package is Azure Linux ``openvmm-0.2.0-2.azl3``. Its public
``3.0-dev`` package recipe builds upstream OpenVMM 0.2.0 (which includes
SNP/IGVM RPC support) and applies the low-ECAM patch for isolated guests.
Launch checks package ownership and RPM verification. This is a provenance
check, not proof of successful confidential launch.

For a custom build, ``--allow-untested-runtime`` explicitly bypasses that
package check. First verify equivalent SNP IGVM RPC support and below-4-GiB
ECAM placement. Unpatched upstream 0.2.0/main snapshots inspected for these
notes do not select low ECAM in RPC. This switch does not fix that mismatch.

Development setup with azdev and sudo
------------------------------------

Use one dedicated Python environment for azdev, Azure CLI, ACE, and its
dependencies. Run setup as your normal user; invoke that environment's
``az`` by absolute path under sudo. Plain ``sudo az`` may select the
distribution CLI instead, which has different dependencies and configuration.
There is no need for a separate ``sudo azdev extension add``.

These commands assume Python 3.12 with pip/venv support, git, sudo, and an
existing checkout containing this ACE implementation on L1. Adjust ``ACE_REPO``
and ``IGVM`` to your paths. Do not clone a stock checkout that lacks ACE::

    export ACE_REPO="$HOME/azure-cli-extensions"
    export ACE_VENV="$HOME/.venvs/ace-dev"
    export IGVM="$HOME/skr-bvt/skr-bvt.igvm"

    test -f "$ACE_REPO/src/ace/setup.py"
    test -f "$IGVM"
    python3.12 -m venv "$ACE_VENV"
    source "$ACE_VENV/bin/activate"
    python -m pip install azdev

Use dedicated development configuration and extension directories, avoiding
your normal Azure credentials/configuration and root's default extension
directory. Keep these exports in each shell used for this workflow::

    export AZURE_CONFIG_DIR="$ACE_VENV/azure-config"
    export AZURE_EXTENSION_DIR="$ACE_VENV/extensions"
    mkdir -p "$AZURE_CONFIG_DIR" "$AZURE_EXTENSION_DIR"
    chmod 700 "$AZURE_CONFIG_DIR" "$AZURE_EXTENSION_DIR"

    azdev setup --cli pypi --repo "$ACE_REPO"
    azdev extension add ace

Ensure the dependencies declared by ACE's ``setup.py`` are installed in that
same environment. The explicit editable install also refreshes dependencies
after ``setup.py`` changes; it does not install into the system Python::

    python -m pip install --no-build-isolation \
        --config-settings editable_mode=compat -e "$ACE_REPO/src/ace"
    "$ACE_VENV/bin/python" -c \
        'import grpc; import google.protobuf; import azext_ace; print("ACE and RPC dependencies import successfully")'
    "$ACE_VENV/bin/az" ace launch --help

Check root can discover ACE and import dependencies using the exact same
interpreter and development configuration::

    sudo "$ACE_VENV/bin/python" -c \
        'import grpc; import google.protobuf; import azext_ace; print("Root can import ACE and RPC dependencies")'
    sudo env AZURE_CONFIG_DIR="$AZURE_CONFIG_DIR" \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace launch --help

azdev records source-extension discovery in the development configuration.
Passing both directories preserves that discovery under sudo; using the
absolute virtual-environment executable preserves Python dependencies.
``AZURE_EXTENSION_DIR`` is an extension container directory, not
``src/ace`` itself. Do not use the entire repository's ``src`` directory as
an extension container for this workflow.

This executes the editable checkout and environment as root. Use only trusted
code/dependencies, and do not run it from directories writable by untrusted
users. The shared configuration is for this development workflow only; no
Azure login is required by these local lifecycle commands. Do not update or
remove the environment/checkout while its supervisor is managing an instance.

Returning to the environment for future testing
----------------------------------------------

After opening a new terminal, reconnecting over SSH, or rebooting L1,
reactivate the existing environment and restore the shell variables. Use the
same paths as the initial setup; do not recreate the virtual environment::

    export ACE_REPO="$HOME/azure-cli-extensions"
    export ACE_VENV="$HOME/.venvs/ace-dev"
    export IGVM="$HOME/skr-bvt/skr-bvt.igvm"
    source "$ACE_VENV/bin/activate"
    export AZURE_CONFIG_DIR="$ACE_VENV/azure-config"
    export AZURE_EXTENSION_DIR="$ACE_VENV/extensions"

    sudo env AZURE_CONFIG_DIR="$AZURE_CONFIG_DIR" \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace launch --help

Then use the launch/destroy commands below. Installed packages and azdev
registration persist, so there is no need to rerun ``azdev setup`` or
``azdev extension add ace`` for each session. Python source edits are picked
up by new CLI invocations through the editable install. If dependencies or
packaging metadata change, repeat the editable install from the setup section
after stopping any managed instance.

When finished, stop any test instance with destroy before leaving the
environment::

    deactivate

Deactivation only changes the current shell; it does not stop a running VM or
its supervisor. Reactivating the environment after an L1 reboot does not
restart previous VMs. Choose a new instance name for each new launch.

Launch and destroy on L1
------------------------

With the variables above still set, launch the existing BVT image::

    sudo env AZURE_CONFIG_DIR="$AZURE_CONFIG_DIR" \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace launch --name rpc-test-1 \
        --igvm "$IGVM" \
        --openvmm /usr/bin/openvmm

    sudo env AZURE_CONFIG_DIR="$AZURE_CONFIG_DIR" \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace destroy --name rpc-test-1

If ``ace`` is not recognized, check the two directory variables and root's
help command above. If ``grpc`` is missing, check that the absolute
``$ACE_VENV/bin/az`` executable is used, not ``/usr/bin/az``, and repeat the
editable install in the virtual environment. Do not use plain ``sudo pip``
or install a package named ``grpc``; the distribution name is ``grpcio``.

If RPC fails with ``RST_STREAM`` and OpenVMM logs ``malformed authority``,
ensure the checkout includes the client's ``grpc.default_authority`` option.
ACE sets the HTTP/2 authority to ``localhost`` while still connecting through
the instance's Unix socket; this does not open a TCP listener or change the
VM configuration. An editable install picks up this Python source fix on the
next invocation. Retry with a new instance name.

For server-side diagnostics, add
``OPENVMM_LOG=info,openvmm_entry=debug,mesh_rpc=debug,h2=debug`` to the
``sudo env`` launch command above. OpenVMM uses ``OPENVMM_LOG``, not
``RUST_LOG``. After launch completes, read the retained log with a separate
command (substitute the instance name). Escape non-printable characters to
protect the terminal::

    sudo tail -n 200 -- /var/lib/ace/instances/rpc-test-1/openvmm.log | LC_ALL=C sed -n 'l'

The default state root is ``/var/lib/ace/instances``. It must be owned by root
with mode 0700. ``--state-dir`` changes it; use the same value for destroy.
Names are 1-32 letters, digits, underscores or hyphens. Retained instance names
are not reused: choose a new name for another launch to preserve evidence.

Launch starts a detached Python supervisor which owns OpenVMM and captures
COM1 through a serial Unix socket. It calls ``CreateVM`` then ``ResumeVM``,
and sends the same relay handshake/application request as ``skr-bvt-client.py``.
Unlike that transport-only BVT, readiness rejects mock/non-SNP evidence and
requires a hex-encoded 1184-byte report. It does NOT verify the report signature,
measurement, challenge binding or certificate chain, and performs no SKR.
The returned record explicitly has ``attestation_verified: false``.

The default launch timeout is 120 seconds; destroy defaults to 30 seconds.
Both accept ``--timeout``. Process recovery/cleanup has additional bounded
grace periods. Preserve the supervisor until destroy; it is not a systemd
service and does not automatically restart instances after host reboot.

Each instance directory retains ``instance.json``, runtime/process identity
records, ``openvmm.log``, ``supervisor.log``, ``com1.log`` and, after successful
readiness, ``response.json``. The supervisor and OpenVMM survive the launching
CLI. Destroy releases the VM, quits OpenVMM, checks owned process exit and
removes sockets, but keeps logs, records and the input IGVM. Repeating destroy
for a confirmed stopped instance is supported.

RPC errors do not imply successful destruction. A live server with an
unreachable endpoint produces an explicit error and retains the instance for
retry. Launch failures attempt RPC rollback, then bounded recovery against
verified owned processes. PID ownership includes boot identity, process start
time and command identity to distinguish stale/reused PIDs.

RPC contract and validation
---------------------------

``azext_ace/rpc/vmservice.proto`` is copied unmodified from upstream OpenVMM
0.2.0, commit ``49742cf0636bdda0dc94757d6185e5e50254873c``, under its MIT
license. The adjacent Python binding and type stub are generated with
``grpcio-tools==1.62.3``. Runtime dependencies are declared in ``setup.py``;
the generator is only needed when regenerating the pinned contract::

    python -m grpc_tools.protoc --proto_path=azext_ace/rpc \
        --python_out=azext_ace/rpc --pyi_out=azext_ace/rpc \
        azext_ace/rpc/vmservice.proto

Unit tests cover request parity, a real localhost gRPC transport round trip,
serial-supervisor behavior with mocked processes, readiness and lifecycle
failure/ownership handling::

    python -m pytest azext_ace/tests/latest -q

Linux-specific locking/process tests require Linux. The implementation still
requires actual L1 RPC validation: compare logs and genuine SNP evidence with
the successful CLI BVT, repeat launch/destroy with new names, and exercise
timeouts and crashes. Passing unit tests is not a successful SNP boot test.

The setup command may be removed in the future in favor of extension
installation through ``setup.py``. This is not implemented; ``setup.py``
declares gRPC/protobuf dependencies but does not provision the L1 host or launch
L2 during extension installation.

Use ``az ace --help`` or ``az ace <command> --help`` to view the command structure.
