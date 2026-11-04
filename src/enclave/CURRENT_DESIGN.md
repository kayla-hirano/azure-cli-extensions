# Enclave current design and command reference

For proposed improvements, see [DESIGN.md](DESIGN.md).

These options describe today's CLI, not the target release-provider behavior.
No Azure login is required for local Enclave operations. Customers install Azure
CLI and Enclave before setup. Use the
[customer-testing fork](https://github.com/kayla-hirano/azure-cli-extensions)
initially; the intended source after merge is
[upstream](https://github.com/Azure/azure-cli-extensions).
With wheel installation, install the extension separately for the normal user
and root. Developer installations share the environment but use separate CLI
configuration directories. Follow the [README](README.rst) for exact installation
and privileged invocation instructions.

## Overall extension code flow

This section describes the **current implementation**, not the proposed launch
profile or support for additional VM types. The worker and supervisor are the
same Python process: `_worker.py` implements the persistent supervisor; there
is no separate supervisor service or in-memory state daemon.

### Extension build, packaging, and first installation

**Building the extension wheel is not building a guest IGVM.** The documented
build command is `azdev extension build enclave`, run in the developer
environment described in the [README](README.rst). It packages the Python
extension and existing default resources using [`setup.py`](setup.py).

```text
Prepared checkout: src/enclave/
    |
    +-- Python package: azext_enclave/
    |     command loader/handlers, build/lifecycle helpers, worker
    |     RPC package including existing generated protobuf Python messages
    |
    +-- Package data
    |     azext_metadata.json
    |     RPC .proto sources and .pyi type stubs
    |     dependencies/dependencies.tar.gz
    |     dependencies/enclave_app
    |     dependencies/initramfs/enclave-init
    |
    +-- setup.py: distribution name/version and Python dependencies
    |     README.rst + HISTORY.rst: package description
    |
    v
azdev extension build enclave -> setuptools wheel packaging
    |
    v
enclave wheel (.whl): package code + data + distribution/dependency metadata
    |
    v
az extension add --source <wheel>
    install extension and resolve its Python runtime dependencies
    |
    v
Next az enclave invocation -> load extension -> dispatch requested command
```

`setup.py` uses `find_packages()` to discover Python packages and explicitly
lists non-Python resources in `package_data`. It declares `grpcio` and
`protobuf` as runtime dependencies; these are separate from the guest dependency
bundle. The generated protobuf Python module is already in the checkout.
Packaging does not run `protoc` or regenerate the protocol bindings.
Protocol maintenance uses [`scripts/generate_protocol.py`](scripts/generate_protocol.py)
with the upstream commit, normalized schema checksum, runtime package version,
and generator versions in [`scripts/openvmm-protocol.json`](scripts/openvmm-protocol.json).
`--write` explicitly fetches that pinned schema and regenerates messages and type
stubs, including the generated module's Flake8 exemption. `--check` verifies the
committed files offline without changing them. Runtime and protocol upgrades
must be reviewed together and tested against the actual OpenVMM package; a
matching schema does not guarantee unchanged runtime behavior. See the
[maintenance instructions](README.rst) for the developer-only environment and commands.
`azext_metadata.json` carries preview status and supported CLI-core version
bounds, while `__init__.py` exposes the command loader used by Azure CLI.

Currently, the dependency archive, application binary, and init script must
already be present under `azext_enclave/dependencies`. Wheel construction does
not invoke the external dependency builder, compile the application, fetch
release resources, generate an IGVM, or validate the bundle's artifact hashes.
Bundle validation happens during create. External release retrieval during
wheel construction remains a proposed change, not current packaging behavior.

Wheel installation installs code/data and Python dependencies into the selected
CLI extension environment. It does not run `az enclave setup`, install OpenVMM,
create runtime state, start the supervisor, or boot a guest. Install separately
for the normal user and root as documented above; host setup, image creation,
and launch remain explicit commands.

Developer bootstrap is an alternative to wheel installation:
[`scripts/bootstrap-dev.sh`](scripts/bootstrap-dev.sh) creates or reuses a
virtual environment, configures azdev and isolated CLI directories, registers
the extension, and installs it in editable mode with Python dependencies.
It checks imports but does not build a wheel or start a VM. Editable source
changes take effect on the next CLI invocation; packaging/dependency changes
require reinstalling. Stop managed instances before replacing their installed
extension or modifying/removing their environment.

### Command dispatch and module responsibilities

```text
az enclave <command>
    |
    v
azext_enclave/__init__.py: EnclaveCommandsLoader
    +-- commands.py: register command -> custom handler
    +-- _params.py: register arguments; _help.py: command help
    |
    v
custom.py: select inputs/defaults, delegate, translate errors/results
    |
    +-- setup   -> _setup.setup_host() -> _runtime.resolve_runtime()
    |
    +-- create  -> _image_builder.build_igvm()
    |             validate -> compose initrd -> generate -> optional sign
    |             -> publish IGVM; return expected SNP measurement
    |
    +-- launch  -> _lifecycle.launch()
    |             +-- _instance: private state, locks, process identities
    |             +-- _runtime: executable/package verification
    |             +-- _worker: persistent supervisor and OpenVMM child
    |             +-- _rpc: CreateVM/ResumeVM over local gRPC
    |             +-- probe_guest(): readiness over the vsock relay
    |
    +-- destroy -> _lifecycle.destroy()
                  read state -> verify ownership -> RPC shutdown
                  -> reconcile owned processes/sockets -> retain evidence
```

Setup and create are synchronous and do not start the supervisor or OpenVMM.
Create's private build workspace is distinct from launch's instance state.
It holds extracted dependencies, rootfs/initrd, effective IGVM JSON, resource
paths, generator outputs, and stage logs. Normally only the final IGVM is
published and the workspace is removed; `--keep-intermediates` retains it.
The image-bound launch profile proposed in [DESIGN.md](DESIGN.md) is not yet exported.

`custom.py` keeps CLI handling separate from these implementations. For
launch/destroy results, `_instance_result()` omits internal executable and
ownership fields and exposes the recorded supervisor PID.

### Launch, worker, and runtime sequence

Launch validates the local Linux/root environment, timeout, image, `/dev/mshv`,
runtime, and private state path. It hashes the image/runtime, acquires the host
guard and per-instance command lock, and refuses to reuse an existing instance
directory, including one retained after failure or destroy.

```text
Launching CLI                 Worker/supervisor             OpenVMM
(_lifecycle.py)               (_worker.py)                  (child process)
    |                              |                            |
    | write instance.json          |                            |
    | status=starting              |                            |
    | start detached Python ------>| read instance.json         |
    | save supervisor identity     | listen on serial.sock      |
    |                              | start RPC server --------->|
    |                              | write runtime.json         |
    | wait for matching, live      |                            |
    | process ownership            |                            |
    | wait for gRPC readiness --------------------------------->|
    | CreateVM ------------------------------------------------>|
    | write status=paused          |             VM allocated, paused
    | ResumeVM ------------------------------------------------>|
    | close client channel         |                 guest executes
    |                              |<-- guest COM1 output -------|
    |                              | append to com1.log          |
    | probe via vsock.sock ------------------------------------>|
    |       relay CONNECT 4050 -> guest sample response          |
    | save response.json           |                            |
    | write status=running         |                            |
    | return; CLI exits            | remain alive               | remain alive
```

The worker starts in a new session with CLI input disconnected and its
stdout/stderr routed to `supervisor.log`. It launches OpenVMM in local
gRPC-server mode; OpenVMM output goes to `openvmm.log`. The supervisor owns
the COM1 listener, collects serial output, monitors its child, and handles
termination signals. It terminates/reaps the child on shutdown, records its
exit code, and removes known runtime sockets. Fatal worker errors retain a
traceback in `supervisor.log` and an error marker.

`VmClient` in `_rpc.py` applies deadlines to management calls over `rpc.sock`.
Closing its channel does not shut down the server. Guest readiness uses the
separate `vsock.sock` relay, not gRPC: launch sends the sample challenge/payload
exchange and checks the SNP report's shape before saving the response.
This is not attestation verification.

### State, endpoints, and ownership

The default state root is `/var/lib/enclave/instances`. A custom `--state-dir`
changes the instance root, not the host guard. Old records remain under
`/var/lib/ace/instances` and require an explicit `--state-dir` for teardown.
Never move live instance directories. Stop old runtimes and retire old
installations before upgrading: their different host lock cannot coordinate
with the new guard.

```text
/run/enclave/enclave.lock            host-wide runtime guard

<state-dir>/<name>.lock              per-instance command lock
<state-dir>/<name>/                  private instance directory (0700)
    instance.json                   lifecycle configuration/status
    runtime.json                    supervisor/OpenVMM identities
    response.json                   accepted guest response, if obtained
    exit.json                       OpenVMM exit code, if recorded
    worker-error.json               fatal supervisor marker, if recorded
    supervisor.log                  worker stdout/stderr and traceback
    openvmm.log                     OpenVMM stdout/stderr
    com1.log                        captured guest serial output
    rpc.sock                        OpenVMM management endpoint
    serial.sock                     supervisor's COM1 listener
    vsock.sock                      OpenVMM guest-vsock relay
```

`instance.json` records selected paths, image/runtime hashes, lifecycle status,
the supervisor identity, endpoint/log locations, and
`attestation_verified: false`. `runtime.json` records owned processes, not
executable paths. `_instance.py` writes JSON records through flushed temporary
files and atomic replacement; it checks state-directory ownership/permissions
and rejects symlinked state paths.

Process ownership combines PID, boot ID, process start time, and a command-line
hash from `/proc`. Liveness checks and signal recovery match the full identity
rather than trusting a PID that may have been reused.

The per-instance lock serializes managing commands and is released when the
command finishes. The host guard limits launch to one managed runtime across
instance names and state roots. Its file descriptor is passed to both the
supervisor and OpenVMM, so the guard remains held after the CLI returns and
while either process retains it. Lock files may remain; the live file lock,
not file existence, determines whether the slot is reserved.

### Failure recovery and destroy

```text
Recorded launch status:
    starting -> paused -> running
         \         \          launch error/interruption
          +---------+-------> failed (retain error/cleanup evidence)

destroy:
    lock instance -> read records -> verify owned runtime
        |
        +-- server alive -> PropertiesVM
        |                   -> TeardownVM if initialized -> Quit
        |                   -> RPC failure: destroy-failed; retain for retry
        |
        +-- server already exited -> reconcile remaining owned processes
        |
        v
    finish owned-process cleanup -> remove known sockets -> destroyed
    retain instance directory, records, logs, response, and original IGVM
```

Launch rollback requests `Quit` where possible, then attempts owned-process
recovery, recording cleanup errors rather than hiding incomplete teardown.
Destroy prefers RPC: `TeardownVM` releases guest resources, whereas `Quit`
stops the OpenVMM server. A lost Quit reply is accepted only when the owned
server's exit is confirmed. Unconfirmed RPC failures are retained for retry
rather than automatically forcing shutdown.

After RPC shutdown, or when reconciling an already-exited server, lifecycle
cleanup waits for/stops the owned supervisor and recovers a remaining owned
OpenVMM process if necessary. The supervisor normally reaps its child.
Socket cleanup removes only the known socket endpoints and refuses unexpected
non-socket entries. Destroy does not delete the image or retained evidence;
repeated destroy reconciles the same record, but a new launch needs a new name.
Persisted status is a command outcome, not continuous health monitoring:
an unexpected runtime exit can leave a previously `running` record until a
later destroy reconciles it.

## `az enclave setup`

```bash
sudo az enclave setup [--yes]
```

Run as root on Azure Linux 3.0 L1 with a compatible kernel exposing `/dev/mshv`.
Setup installs `openvmm-0.2.0-2.azl3` through configured `tdnf` repositories if
absent, with confirmation, and verifies RPM version/integrity. Incompatible
installations require manual repair.

| Parameter | Required | Default | Behavior |
| --- | --- | --- | --- |
| `--yes`, `-y` | No | `false` | Approve package installation without prompting; validation remains required. |

Returns status, OS, kernel, and runtime path. It does not provision the host,
install a kernel, add repositories, reboot, install developer tools, or start
OpenVMM. Host readiness does not establish guest boot or attestation.

## `az enclave create`

```bash
az enclave create --workspace "$HOME/enclave-build" \
    --output-file "$HOME/images/enclave.igvm"
```

Run as a normal user on Linux or WSL with GNU cpio and gzip. No L1 runtime is
needed for image creation. Create does not compile inputs or install build tools.

| Parameter | Required | Default | Behavior |
| --- | --- | --- | --- |
| `--workspace` | Yes | None | Parent for a private workspace; the parent may already exist. |
| `--output-file` | Yes | None | IGVM destination; existing files are never overwritten. |
| `--dependency-bundle` | No | Packaged `dependencies/dependencies.tar.gz` | Trusted `measured-pcie-v1` bundle directory or tar/tar.gz archive. |
| `--application` | No | Packaged `dependencies/enclave_app` | One prebuilt static x86-64 ELF, installed as `/bin/enclave_app`. |
| `--init-script` | No | Packaged `dependencies/initramfs/enclave-init` | Full guest `/init` replacement; must have a shebang and LF line endings. |
| `--signing-key` | No | No signing | Unencrypted PEM ECDSA P-384 key for SNP ID-block signing; requires OpenSSL. Failure never publishes an unsigned fallback. |
| `--keep-intermediates` | No | `false` | Retain private workspace and stage logs on success or failure. |
| Global `--debug` | No | Disabled | Enables SNP guest debugging **and** diagnostic logging, regardless of the bundle's debug setting. |
| Global `--verbose` | No | Disabled | Logging only; does not enable guest debugging. |

Logging flags do not retain intermediates. Returns `igvm` and expected SNP
`measurements`; signing the ID block does not change the expected measurement.

### Input contents and customization

| Input | Contents and limits |
| --- | --- |
| Dependency triple within the bundle | `kernel/bzImage`, `initrd/base-initrd.cpio.gz`, and `openvmm/snp_bootshim`. The kernel is the L2 guest kernel, not the host kernel. Customers can replace these through a complete compatible bundle today. |
| Tooling/profile within the bundle | `openvmm/igvmfilegen` and `manifests/snp-linux-direct.json`. The tool executes on the build host; it is not OpenVMM. Profile changes must remain compatible with launch's fixed resources/devices and generator interface. |
| Bundle metadata | Required `dependency-manifest.v1.json` maps artifact names to paths/hashes. Schema 1, `measured-pcie-v1`, x86-64, and an x64 measured-PCIe configuration are required. Archives place this at the root or inside one top-level directory. |
| Supplementary bundle records | Default bundle includes `kernel/config`, `kernel/manifest.txt`, the measured-PCIe patch, and `SHA256SUMS`. The CLI checks manifest artifact hashes, not `SHA256SUMS`, and does not apply the patch. |
| Application | One static little-endian x86-64 ELF executable without `PT_INTERP`; not source, a container, or a dependency archive. No compilation, library installation, or arguments are provided by this option. |
| Init script | Owns guest mounts, environment, arguments, and startup. Default mounts guest filesystems and executes `/bin/enclave_app serve-vsock 4050`. An override replaces all of `/init`, not an extra hook. |

Required artifact names are `kernel`, `base_initrd`, `snp_bootshim`,
`igvmfilegen`, and `igvm_manifest`. All manifest-listed artifacts, including
optional records, must exist and match their hashes. Hashes do not authenticate
the publisher or prove boot compatibility; bundled tools must be trusted.

Application, init script, and bundle are independent selections. A custom
bundle does not automatically supply the selected application/script. Extra
guest files currently belong in a custom base initrd; composition overwrites
`/bin/enclave_app` and `/init`. Changed contents can change measurements.

## `az enclave launch`

```bash
sudo az enclave launch --name demo-1 --igvm "$HOME/images/enclave.igvm"
```

Run as root on Linux L1 with `/dev/mshv` and a compatible runtime. Launch starts
a detached supervisor and OpenVMM child, creates/resumes the VM through local
gRPC, and probes the guest through the vsock relay. Both processes outlive the CLI.

| Parameter | Required | Default | Behavior |
| --- | --- | --- | --- |
| `--name`, `-n` | Yes | None | 1-32 characters; starts with a letter/digit, then letters/digits/underscores/hyphens. Existing instance directories cannot be reused, even after destroy. |
| `--igvm` | Yes | None | Existing compatible IGVM on L1; create output or a customer-provided image. |
| `--openvmm` | No | `openvmm` | Executable path or name resolved through the executable search path. |
| `--state-dir` | No | `/var/lib/enclave/instances` | Private root-owned state root with mode `0700`. Symlinked paths are rejected; length must fit Unix socket limits. |
| `--timeout` | No | `120` seconds | Finite positive operation timeout; fractional seconds accepted. |
| `--allow-untested-runtime` | No | `false` | Permit a runtime outside the expected Azure Linux `0.2.0-2.azl3` package; equivalent SNP IGVM RPC and low-ECAM support are still required. |

Only one managed runtime is allowed per L1, regardless of name/state root.
The guard remains held while either runtime process survives. VM resources are
fixed at one CPU and 160 MiB with the supported PCIe/vsock layout.

Readiness connects using `CONNECT 4050`, expects `OK`, and sends newline-delimited
JSON with a generated challenge and `hello world` payload. The response must
contain `evidence.tee: snp` and a hex-encoded 1184-byte `evidence.report`.
**This is shape checking, not signature, measurement, challenge-binding, or
certificate-chain verification. Launch performs no key release.**

Returns instance/status/IGVM details, supervisor PID, sockets, and log/evidence
paths. State under `<state-dir>/<name>` retains `instance.json`, `response.json`
after successful readiness, `openvmm.log`, `supervisor.log`, and `com1.log`.

## `az enclave destroy`

```bash
sudo az enclave destroy --name demo-1
```

Run as root on the same L1 with the original state root. Destroy tears down the
VM through RPC, quits OpenVMM, finishes owned-process cleanup, and removes owned
sockets. Input IGVM, instance record, logs, and saved evidence remain.

| Parameter | Required | Default | Behavior |
| --- | --- | --- | --- |
| `--name`, `-n` | Yes | None | Existing instance name, using launch's naming rules. |
| `--state-dir` | No | `/var/lib/enclave/instances` | Must match launch; same privacy/path constraints. |
| `--timeout` | No | `30` seconds | Finite positive timeout; fractional seconds accepted. Cleanup has additional bounded grace periods. |

Repeated destruction of a confirmed destroyed instance is supported; new launches
require new names. Failed destruction is not proof of shutdown: inspect logs and
retry. Returned socket paths are informational after successful cleanup.
Always destroy before changing the environment/extension. Closing the shell or
deactivating does not stop instances; reboot does not automatically restart them.

## References

- [Installation and usage](README.rst)
- [Arguments](azext_enclave/_params.py), [help](azext_enclave/_help.py), and [handlers](azext_enclave/custom.py)
- [Image builder](azext_enclave/_image_builder.py), [host setup](azext_enclave/_setup.py), and [lifecycle](azext_enclave/_lifecycle.py)
- [Dependency build script](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/enclave/scripts/build-igvm-dependencies.sh&version=GBfeature/modular-igvmfile-generation)
- [Application build script](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/ace/scripts/build-enclave-app.sh&version=GBusers/kpostiglione/ace-skr-demo)
