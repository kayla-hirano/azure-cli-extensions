Azure CLI ACE Extension
======================

``az ace`` manages local SNP enclave VMs using OpenVMM. The intended workflow
lets you generate launch artifacts with ``az ace create`` or bring your own
compatible IGVM. ACE is not yet a general-purpose image launcher.

Set up a development environment
-------------------------------

Use Azure Linux 3.0 L1 with a compatible kernel exposing ``/dev/mshv``.
Have Python 3.12 with pip/venv, git, sudo, and a checkout containing ACE ready.
No Azure login is required.

Run the developer bootstrap as your normal user, adjusting the paths if needed::

    export ACE_REPO="$HOME/azure-cli-extensions"
    export ACE_VENV="$HOME/.venvs/ace-dev"
    bash "$ACE_REPO/src/ace/scripts/bootstrap-dev.sh"
    source "$ACE_VENV/activate-ace.sh"

``bootstrap-dev.sh`` creates a virtual environment, configures azdev with isolated
Azure CLI directories, and installs ACE in editable mode with its Python
dependencies. Register the same editable extension in root's separate CLI
configuration once::

    sudo env AZURE_CONFIG_DIR=/root/.azure-ace \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" config set \
        extension.dev_sources="$ACE_REPO/src/ace"

Then prepare the host::

    sudo env AZURE_CONFIG_DIR=/root/.azure-ace \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace setup

Why sudo? ACE currently requires root for setup, launch, and destroy: setup
can install system packages, while launch/destroy manage the host's VM
runtime, access ``/dev/mshv``, and use root-owned instance state. Developer
bootstrap, image creation, and ordinary help commands do not need sudo.

Always use the virtual environment's absolute ``az`` path under sudo, not
plain ``sudo az``. This runs your checkout and dependencies as root: use
trusted code in directories not writable by untrusted users.

Why separate setup? ``setup.py`` installs Python runtime dependencies with the extension.
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

    sudo env AZURE_CONFIG_DIR=/root/.azure-ace \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace setup

Create
~~~~~~

Package a precompiled static x86-64 application into an IGVM using an
existing dependency bundle. Run as your normal user on Linux L1 with GNU
cpio and gzip installed; root and Azure login are not required. See
`Prepare dependency bundle and application`_ below for example inputs::

    "$ACE_VENV/bin/az" ace create \
        --dependency-bundle "$HOME/ace-inputs/dependencies" \
        --application "$HOME/ace-inputs/enclave_app" \
        --workspace "$HOME/ace-build" \
        --output-file "$HOME/images/enclave.igvm"

By default the packaged init script ``skr-bvt-init`` will be used, but
``--init-script`` parameter can be used to specify a custom init script.

Optional ``--signing-key`` requires OpenSSL and an unencrypted PEM ECDSA
P-384 key. Signing failure never publishes an unsigned fallback.

The extension ships direct, unmodified copies of ACC-CVM-Tooling's
``src/ace/image_builder.py`` as ``azext_ace/_image_builder.py`` and
``src/ace/initramfs/skr-bvt-init`` as ``azext_ace/initramfs/skr-bvt-init``
to use as part of the create command.

Prepare dependency bundle and application
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``--dependency-bundle`` is a trusted ``measured-pcie-v1`` bundle containing
the L2 kernel, base initrd, SNP boot shim, executable ``igvmfilegen``, guest
manifest, and artifact hashes. ``--application`` is a static x86-64 ELF
executable, installed in the guest as ``/bin/enclave_app``.

You can build both on a Linux/WSL machine with an authorized
ACC-CVM-Tooling checkout, then copy them to L1. See the example below for guidance::

    export TOOLING="$HOME/ACC-CVM-Tooling"
    export WORK="$HOME/ace-input-build"
    mkdir -p "$WORK"

    bash "$TOOLING/src/ace/scripts/build-igvm-dependencies.sh" \
        --workspace "$WORK/dependency-build-1" \
        --output "$WORK/dependencies"

    rustup target add x86_64-unknown-linux-musl
    env -u CARGO_TARGET_DIR TARGET=x86_64-unknown-linux-musl \
        bash "$TOOLING/src/ace/scripts/build-enclave-app.sh"
    export APP="$TOOLING/src/ace/target/x86_64-unknown-linux-musl/release/enclave_app"

    (cd "$WORK/dependencies" && sha256sum --check SHA256SUMS)
    test -s "$APP"

Once the two artifacts are created, copy them over to the L1 host for use with the ACE extension.

Launch
~~~~~~

Create and resume a VM from the IGVM supplied with ``--igvm``, then wait for
a guest response containing an SNP-shaped report. This can be create's
output or your own compatible IGVM. Run as root on L1,
choosing a new name for every launch. Set ``IGVM`` to your image::

    export IGVM="$HOME/images/enclave.igvm"
    sudo env AZURE_CONFIG_DIR=/root/.azure-ace \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace launch --name demo-1 \
        --igvm "$IGVM" --openvmm /usr/bin/openvmm

Currently, only one ACE-managed enclave may be active on an L1, even with
different names or ``--state-dir`` values. A second launch fails until the
existing runtime stops. OpenVMM starts on demand, not during setup.

The default timeout is 120 seconds; change it with ``--timeout``.

Destroy
~~~~~~~

Stop the VM and OpenVMM and clean up owned sockets, retaining the input IGVM,
logs, and evidence. Run on the same L1 host::

    sudo env AZURE_CONFIG_DIR=/root/.azure-ace \
        AZURE_EXTENSION_DIR="$AZURE_EXTENSION_DIR" \
        "$ACE_VENV/bin/az" ace destroy --name demo-1

The default timeout is 30 seconds; change it with ``--timeout``. Cleanup has
additional bounded grace periods. Failed destruction is not confirmation
that the VM stopped; inspect the logs and retry. Repeating destroy is supported.
After the instance's OpenVMM and supervisor exit, a new instance can be launched
with a new name. The host-wide guard remains held while either process lives.

Always destroy test instances
before running ``deactivate``: leaving the shell does not stop them, and a host
reboot does not automatically restart them.

User and root CLI configuration
-------------------------------

Use sudo for setup, launch, and destroy; run create as your normal user.
All commands use the same virtual environment and extension installation,
but keep their Azure CLI session files and command logs separate:

* Normal-user commands use ``AZURE_CONFIG_DIR`` from ``activate-ace.sh``.
* Sudo commands use ``AZURE_CONFIG_DIR=/root/.azure-ace``,
  while preserving ``AZURE_EXTENSION_DIR`` to find the installed extension.

Do not export the root configuration path into your normal shell or run
create with sudo. If earlier sudo commands used your user configuration,
restore ownership of that dedicated directory once::

    sudo chown -R -- "$(id -u):$(id -g)" "$ACE_VENV/azure-config"

View launch evidence
--------------------

After successful guest readiness, launch saves the JSON response under the
instance directory. ACE requires root-owned state directories with mode
``0700``, including custom ``--state-dir`` locations. Your normal user cannot
enter these directories; use sudo to read the evidence directly without
changing directories::

    sudo "$ACE_VENV/bin/python" -m json.tool \
        /var/lib/ace/instances/demo-1/response.json

``evidence.report`` is the hex-encoded 1184-byte SNP report. ACE checks the
report's shape, **not** its signature, measurement, challenge binding, or
certificate chain. The instance record has ``attestation_verified: false``;
successful launch is not verified attestation and performs no key release.

The same directory retains ``instance.json``, ``openvmm.log``,
``supervisor.log``, and ``com1.log`` for diagnosis, including after destroy.
Use your custom state directory instead if you specified ``--state-dir``.

To browse all results without sudo, copy the instance directory after a
successful destroy, when logs have stopped changing and runtime sockets have
been removed. Choose a destination that does not already exist::

    sudo cp -a -- /var/lib/ace/instances/demo-1 "$HOME/demo-1-results"
    sudo chown -R -- "$(id -u):$(id -g)" "$HOME/demo-1-results"
    cd "$HOME/demo-1-results"
    python -m json.tool response.json

Keep the original ACE directory root-owned and private; do not change its
permissions to gain access. The user-owned copy is a results archive, not a
relocatable or resumable ACE instance.
