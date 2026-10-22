Microsoft Azure CLI 'ace' Extension
==========================================

This package is for the 'ace' extension.
i.e. 'az ace'

Commands
--------

* ``az ace setup``: Install dependencies and build the pinned SKR BVT inputs.
* ``az ace create``: Build the sample workload, initrd, IGVM, and checksummed BVT bundle.
* ``az ace launch``: Use OpenVMM RPC to create and launch the enclave VM from the IGVM file.
* ``az ace destroy``: Use OpenVMM RPC to destroy the enclave.

Setup and create implement a fixed-profile demo using the scripts and sample
workload in ACC-CVM-Tooling's ``src/ace/docs/SKR_BVT.md``. They are not a
general-purpose image-builder API. Launch and destroy remain unimplemented.

Use ``az ace --help`` or ``az ace <command> --help`` to view the command structure.

Environment
-----------

Run the installed extension inside an x86-64 Azure Linux L1, not Windows.
The fixed demo requires the validated captured image: L1 kernel
``6.18.34.mshv4``, packaged OpenVMM ``0.2.0-2.azl3``, and accessible
``/dev/mshv``. The documented Hyper-V baseline is ``10.0.29695.1002-1-0``;
the commands do not inspect the underlying Hyper-V build.

Setup installs missing packages using ``tdnf`` (through ``sudo`` when needed),
including Docker/Buildx and compiler/archive tools, starts the Docker service,
and installs rustup into the current user's Cargo home if absent. It selects
Rust ``1.95.0`` for all demo builds without changing the user's default toolchain.
Host kernel, MSHV access, and any existing OpenVMM executable are checked before
installation. OpenVMM validation checks the RPM owning the executable on PATH.
Setup does not
replace the kernel, upgrade an existing OpenVMM package, configure package
repositories, change Docker group membership, or change device permissions.
Run under an account already authorized to use Docker and MSHV, or root.
Use the same account and workspace for setup and create.

Existing Git authentication for the private ACC-CVM-Tooling repository is
required. Do not put credentials in command arguments. Network access to the
Git hosts, package repositories, Rust distribution and crate registries, and
Docker build dependencies is required. Source builds need substantial disk
space and may take a long time. Package availability depends on the configured
Azure Linux repositories; package installation failures are reported, not ignored.

Demo workflow
-------------

::

    az ace setup --workspace ~/ace-demo
    az ace create --workspace ~/ace-demo --output-dir ~/ace-demo/bundle

Setup clones ``feature/skr-bvt-igvm`` from ACC-CVM-Tooling and checks out the
validated revision ``d2dddacf713341f0062b68dba27886d00ca9de1a``, OpenVMM revision
``6b350086b9c46fa7f8783240c808597454c6399b``, and openvmm-deps revision
``6c779d3d6c0eaa0ba3bf361ac504866bfe86c6bd``. It applies the measured-PCIe patch,
restores OpenVMM build tools, installs the Rust targets, builds the bootshim
and image generator, and exports the kernel and base initrd from Docker.
The L2 kernel comes from the pinned Dockerfile (``6.18.52.mshv2``).

Alternatively, supply a local ACC-CVM-Tooling checkout to both commands,
including for unpublished handoff changes::

    az ace setup --workspace ~/ace-demo --source-dir ~/ACC-CVM-Tooling
    az ace create --workspace ~/ace-demo --source-dir ~/ACC-CVM-Tooling --output-dir ~/ace-demo/bundle

The default workspace is ``~/.azure/ace/bvt``. Existing source checkouts are
reused without pulling or resetting; incompatible revisions, dirty openvmm-deps
inputs, and OpenVMM changes beyond the expected PCIe patch are rejected.
``--source-dir`` intentionally permits local workload changes instead of the
pinned handoff. Setup can be rerun after correcting a failed dependency or build
step; builds use their normal caches. Run commands sequentially within a workspace.

Create rechecks tools, upstream revisions, patch application, Rust target, and
input files. Setup records input hashes in ``setup-inputs.sha256``; create rejects
changed inputs and requires setup to be rerun. It always invokes
``build-skr-bvt.sh`` for the sample workload:
there is no arbitrary workload or prebuilt-IGVM import mode in this demo.
The output directory must not already exist. Create verifies the generated
bundle's SHA-256 manifest and requires the map, SNP sidecars, runner/client,
and remaining BVT artifacts before returning their paths. Child build output
goes to stderr, leaving CLI JSON output
available for automation. The source builder records source revisions and
input digests in ``INPUTS.txt``.

Neither command launches an enclave, runs the BVT, verifies attestation, or
releases a key. After creation, use the bundle's ``run-skr-bvt.sh`` as described
in ``SKR_BVT.md`` to test launch and transport. RPC management remains future work.