Azure CLI ACE Extension
======================

``az ace`` manages local SNP enclave VMs using OpenVMM. The intended workflow
lets you generate launch artifacts with ``az ace create`` or bring your own
compatible IGVM. ACE is not yet a general-purpose image launcher.

Initial set up for development environment
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

Always use the virtual environment's absolute ``az`` path under sudo, not
plain ``sudo az``. This runs your checkout and dependencies as root: use
trusted code in directories not writable by untrusted users.

Why separate setup? ``setup.py`` installs Python runtime dependencies with the extension.
``az ace setup`` handles privileged host dependencies, not its own installation.
Keeping them separate avoids changing your Python environment as root.

Set up to resume testing
--------------

In a new terminal or SSH session, restore the same paths and activate the
existing environment; no bootstrap or package installation is needed::

    export ACE_VENV="$HOME/.venvs/ace-dev"
    source "$ACE_VENV/activate-ace.sh"

Source edits take effect on the next CLI invocation. If dependencies or
packaging metadata change, stop managed instances before rerunning bootstrap.
Do not remove or update their environment/checkout while they are running.
Existing environments created without the bootstrap need one bootstrap run
to generate ``activate-ace.sh``.

Extension commands
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

Build an IGVM using the dependency archive, enclave application, and init
script packaged with the extension. Run as your normal user on Linux L1
with GNU cpio and gzip installed::

    "$ACE_VENV/bin/az" ace create \
        --workspace "$HOME/ace-build" \
        --output-file "$HOME/images/enclave.igvm"

The packaged inputs are under ``azext_ace/dependencies/``:

* ``dependencies.tar.gz``: the trusted ``measured-pcie-v1`` bundle containing
  the kernel, base initrd, SNP boot shim, ``igvmfilegen``, guest manifest,
  and artifact hashes.
* ``enclave_app``: the static x86-64 application installed as
  ``/bin/enclave_app``.
* ``initramfs/skr-bvt-init``: the default guest startup script, which starts
  the application on guest vsock port 4050.

Guest debugging is disabled by default, regardless of the bundle's setting.
``--debug`` enables both SNP guest debugging and diagnostic logs, so use it
only when a debug-enabled guest is intended. ``--verbose`` changes logging
only. Neither logging flag retains the workspace by itself.

Optional overrides
^^^^^^^^^^^^^^^^^^

Use ``--dependency-bundle`` to supply your own trusted tar/tar.gz archive
or existing bundle directory instead of the packaged archive.
An archive must contain ``dependency-manifest.v1.json`` and its referenced
artifacts at the root or inside one top-level directory.

Use ``--init-script`` to override guest startup. The supplied script must
have a shebang and LF line endings and start the application appropriately.
Launch expects a compatible application response on guest vsock port 4050.

Use ``--application`` to supply your own static x86-64 ELF binary instead
of the packaged application. It is installed as ``/bin/enclave_app`` in the
guest.

Optional ``--signing-key`` requires OpenSSL and an unencrypted PEM ECDSA
P-384 key. Signing failure never publishes an unsigned fallback. Adding a
signed SNP ID block does not change the expected launch measurement.

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

Processes and connections
~~~~~~~~~~~~~~~~~~~~~~~~~

The launching CLI starts a detached Python supervisor, which starts OpenVMM
as its child. Both outlive the CLI command::

    az ace launch / destroy -- gRPC via rpc.sock --> OpenVMM
    az ace launch ---------- CONNECT via vsock.sock --> guest app, port 4050
    supervisor <------------ COM1 via serial.sock ---- OpenVMM
        |
        +-- owns and reaps the OpenVMM child

These are three separate local Unix-socket connections:

* ``rpc.sock`` carries management RPCs, not application requests. Closing
  the CLI's gRPC channel does not stop OpenVMM.
* ``vsock.sock`` is OpenVMM's guest relay. Launch sends ``CONNECT 4050``,
  waits for ``OK``, then exchanges a newline-delimited JSON request and
  response with the enclave application.
* ``serial.sock`` is the supervisor's listener for OpenVMM's COM1 connection.
  The supervisor writes guest serial output to ``com1.log`` and directs
  OpenVMM stdout/stderr to ``openvmm.log``. Its own output goes to
  ``supervisor.log``.

A host-wide lock permits only one ACE-managed runtime. Its descriptor is
inherited by the supervisor and OpenVMM, so the lock remains held while
either process survives the CLI. A separate per-instance lock prevents
concurrent commands from managing the same instance.

User and root CLI configuration
-------------------------------

ACE currently requires root for setup, launch, and destroy: setup
can install system packages, while launch/destroy manage the host's VM
runtime, access ``/dev/mshv``, and use root-owned instance state. Developer
bootstrap, image creation, and ordinary help commands do not need sudo.
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
certificate chain. Successful launch is not verified attestation and
performs no key release.

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

Testing using a wheel (.whl)
---------------------------

Install a trusted ACE wheel to use or test a packaged version of the
extension. The target host does not need a development venv, azdev or a
source checkout. This tests the packaged artifact rather than editable
source; it does not change ACE's prototype status or attestation limitations.

Build the wheel on the development machine using the venv/azdev setup above::

    azdev extension build ace

Copy the resulting ``.whl`` to the target host.

To test the wheel on the same machine as the development venv, first stop
any ACE-managed instance. Open a separate shell without sourcing the
development activation script (or run ``deactivate`` and
``unset AZURE_CONFIG_DIR AZURE_EXTENSION_DIR``), then use the standard
system-installed ``az``, not ``$ACE_VENV/bin/az``. Check ``az --version``
and ``sudo az --version`` to confirm the Python locations. Keep the venv
and its editable extension intact. The user and root CLI configurations
used for wheel testing must not register ACE through
``extension.dev_sources``, which can override the installed wheel. If they
do, select separate wheel-testing configuration and extension directories
with ``AZURE_CONFIG_DIR`` and ``AZURE_EXTENSION_DIR`` on every installation
and test command, including explicitly through ``sudo env`` for root.
Return to editable development by sourcing the venv's ``activate-ace.sh``
again after stopping the wheel-tested instance.

Setup, launch and destroy require Azure Linux 3.0 L1 with a compatible
kernel exposing ``/dev/mshv``, sudo access, and a standard Azure CLI installation available
to both your normal user and root. Image creation also requires GNU cpio
and gzip. No Azure login is required for these local ACE commands.

If Azure CLI is not installed outside the development venv, install it
system-wide on Azure Linux using tdnf, from a shell outside the venv::

    sudo tdnf install ca-certificates
    sudo tdnf install azure-cli
    hash -r

This installs the standard ``az`` command for normal-user and sudo use;
it does not modify the development venv. See the
`Azure CLI installation instructions for Azure Linux
<https://learn.microsoft.com/cli/azure/install-azure-cli-linux?view=azure-cli-latest&pivots=tdnf>`_
for details.

Verify that both invocations select the intended standard CLI installation::

    az --version
    sudo az --version

Install the wheel separately for your normal user and root, adjusting the
filename to match the artifact you copied::

    export ACE_WHEEL="$HOME/dist/ace-0.1.0-py3-none-any.whl"
    az extension add --source "$ACE_WHEEL"
    sudo az extension add --source "$ACE_WHEEL"
    az ace --help
    sudo az ace --help

The normal-user installation supports image creation; root's installation
supports setup, launch and destroy. If ACE is already installed, stop any
managed instances before replacing it and use ``--upgrade`` with the
extension installation commands.

Prepare OpenVMM::

    sudo az ace setup

Approve installation if prompted. Setup verifies the supported OpenVMM
package; it does not install a compatible kernel or reboot.

Create an image as your normal user, or skip this step if you copied a
prepared image::

    az ace create \
        --workspace "$HOME/ace-build" \
        --output-file "$HOME/ace-artifacts/enclave.igvm"

Launch the image, then destroy the instance when finished::

    sudo az ace launch --name test-1 --igvm "$HOME/ace-artifacts/enclave.igvm"
    sudo az ace destroy --name test-1
