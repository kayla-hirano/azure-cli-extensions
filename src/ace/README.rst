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

Usage
-----

After installing the extension and its Python dependencies in the appropriate
Azure CLI environment, run on L1::

    sudo az ace launch --name demo \
        --igvm /home/user/skr-bvt/skr-bvt.igvm \
        --openvmm /usr/bin/openvmm

    sudo az ace destroy --name demo

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
