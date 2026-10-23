Azure CLI ACE Extension
======================

``az ace`` manages local SNP enclave VMs using OpenVMM. The intended workflow
lets you generate launch artifacts with ``az ace create`` or bring your own
compatible IGVM. Create is not implemented yet, so testing requires an existing
SKR BVT-compatible IGVM with a fixed VM configuration. ACE is not yet a
general-purpose image launcher.

Set up a development environment
-------------------------------

Use Azure Linux 3.0 L1 with a compatible kernel exposing ``/dev/mshv``.
Have Python 3.12 with pip/venv, git, sudo, and a checkout containing ACE ready.
No Azure login is required.

Run the developer bootstrap as your normal user, adjusting the paths::

    export ACE_REPO="$HOME/azure-cli-extensions"
    export ACE_VENV="$HOME/.venvs/ace-dev"
    bash "$ACE_REPO/src/ace/scripts/bootstrap-dev.sh"
    source "$ACE_VENV/activate-ace.sh"

The script creates a virtual environment, configures azdev with isolated
Azure CLI directories, and installs ACE in editable mode with its Python
dependencies. Then prepare the host::

    sudo env AZURE_CONFIG_DIR="$AZURE_CONFIG_DIR" \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace setup

Why sudo? ACE currently requires root for setup, launch, and destroy: setup
can install system packages, while launch/destroy manage the host's VM
runtime, access ``/dev/mshv``, and use root-owned instance state. Developer
bootstrap and ordinary help commands do not need sudo.

Always use the virtual environment's absolute ``az`` path under sudo, not
plain ``sudo az``. This runs your checkout and dependencies as root: use
trusted code in directories not writable by untrusted users.

Why separate setup? Bootstrap installs developer tooling before ACE can run;
``setup.py`` installs Python runtime dependencies with the extension.
``az ace setup`` handles privileged host dependencies, not its own installation.
Keeping them separate avoids changing your Python environment as root.

Resume testing
--------------

In a new terminal or SSH session, restore the same paths and activate the
existing environment; no bootstrap or package installation is needed::

    export ACE_VENV="$HOME/.venvs/ace-dev"
    source "$ACE_VENV/activate-ace.sh"
    export IGVM="$HOME/skr-bvt/skr-bvt.igvm"

Source edits take effect on the next CLI invocation. If dependencies or
packaging metadata change, stop managed instances before rerunning bootstrap.
Do not remove or update their environment/checkout while they are running.
Existing environments created without the bootstrap need one bootstrap run
to generate ``activate-ace.sh``.

Commands
--------

Setup
~~~~~

Validate Azure Linux 3.0 and ``/dev/mshv``, install
``openvmm-0.2.0-2.azl3`` if absent, and verify its RPM version and integrity::

    sudo env AZURE_CONFIG_DIR="$AZURE_CONFIG_DIR" \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace setup

Setup prompts before installation; use ``--yes`` for unattended approval.
It is safe to repeat when the expected runtime is installed. Existing
incompatible or modified runtimes fail verification and need manual repair.
Setup uses configured repositories only; it does not change kernels, add
repositories, reboot, or install Python/git. It does not prove a successful
SNP boot or verified attestation.

Launch
~~~~~~

Create and resume a VM from the IGVM supplied with ``--igvm``, then wait for
a guest response containing an SNP-shaped report. Once create is implemented,
this can be its output or your own compatible IGVM. Run as root on L1,
choosing a new name for every launch. Set ``IGVM`` to your image::

    export IGVM="$HOME/skr-bvt/skr-bvt.igvm"
    sudo env AZURE_CONFIG_DIR="$AZURE_CONFIG_DIR" \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace launch --name demo-1 \
        --igvm "$IGVM" --openvmm /usr/bin/openvmm

For demos, only one ACE-managed enclave may be active on an L1, even with
different names or ``--state-dir`` values. A second launch fails until the
existing runtime stops. OpenVMM starts on demand, not during setup.

The default timeout is 120 seconds; change it with ``--timeout``.
Custom runtimes require ``--allow-untested-runtime`` and equivalent SNP
IGVM RPC and below-4-GiB ECAM support; the flag only bypasses package checks.

Destroy
~~~~~~~

Stop the VM and OpenVMM and clean up owned sockets, retaining the input IGVM,
logs, and evidence. Run on the same L1 host::

    sudo env AZURE_CONFIG_DIR="$AZURE_CONFIG_DIR" \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace destroy --name demo-1

The default timeout is 30 seconds; change it with ``--timeout``. Cleanup has
additional bounded grace periods. Failed destruction is not confirmation
that the VM stopped; inspect the logs and retry. Repeating destroy is supported.
After the instance's OpenVMM and supervisor exit, a new instance can be launched
with a new name. The host-wide guard remains held while either process lives.

Instances use ``/var/lib/ace/instances`` by default. If you pass ``--state-dir``,
use the same value for launch and destroy. Always destroy test instances
before running ``deactivate``: leaving the shell does not stop them, and a host
reboot does not automatically restart them.

Create (not implemented)
~~~~~~~~~~~~~~~~~~~~~~~~

``az ace create`` will package a workload and generate launch artifacts,
including the IGVM passed to ``az ace launch --igvm``. Skip this step if you
already have a compatible IGVM.

It currently returns a "not implemented" error. View available options with
``az ace --help`` or ``az ace <command> --help``. The workflow is:
bootstrap, host setup, create artifacts or supply an IGVM, launch, destroy.

View launch evidence
--------------------

After successful guest readiness, launch saves the JSON response under the
instance directory. For the example above::

    sudo "$ACE_VENV/bin/python" -m json.tool \
        /var/lib/ace/instances/demo-1/response.json

``evidence.report`` is the hex-encoded 1184-byte SNP report. ACE checks the
report's shape, **not** its signature, measurement, challenge binding, or
certificate chain. The instance record has ``attestation_verified: false``;
successful launch is not verified attestation and performs no key release.

The same directory retains ``instance.json``, ``openvmm.log``,
``supervisor.log``, and ``com1.log`` for diagnosis, including after destroy.
Use your custom state directory instead if you specified ``--state-dir``.
