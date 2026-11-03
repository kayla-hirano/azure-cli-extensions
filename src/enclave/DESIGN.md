# Enclave extension improvements

**Naming:** The extension is `enclave`, its Python package is `azext_enclave`,
and its commands use `az enclave`. The existing `/var/lib/ace/instances` state
root and `/run/ace` host lock are retained for compatibility. The separate
ACC-CVM-Tooling `src/ace` sample directory is unchanged.

## 1. Objective and scope

Make Enclave a reusable enclave image-composition tool: accept externally built
dependency bundles and applications, compose reproducible images, optionally sign
them, and preserve the existing launch/destroy interface.

Create will **not** compile dependencies or applications. This increment does
not add arbitrary workload formats, new attestation guarantees, or a generic
application-readiness protocol. Supported launch resources/devices will instead
be derived from the image configuration through an image-bound launch profile.

## 2. Current design

```text
Packaged defaults or explicit local bundle + application + init script
    -> az enclave create -> composed initrd -> IGVM + expected measurements
    -> az enclave launch -> guest readiness exchange -> az enclave destroy
```

| Command | Current responsibility |
| --- | --- |
| `setup` | Validate Azure Linux 3.0/MSHV prerequisites and install or verify the pinned OpenVMM package. |
| `create` | Validate prebuilt inputs, compose the initrd, generate the IGVM, and optionally sign its SNP ID block. |
| `launch` | Start supervised OpenVMM, create/resume the VM, and wait for the sample guest response. |
| `destroy` | Stop owned processes, remove owned sockets, and retain logs/evidence. |

Most image composition already lives in the CLI rather than external scripts.
Remaining demo coupling is concentrated in:

- **Artifact delivery:** The wheel packages a dependency archive, `enclave_app`,
  and `enclave-init`; release retrieval is not implemented.
- **Input compatibility:** The bundle requires a specific manifest/profile and
  compatible image-generation tooling. The application is a static x86-64 ELF.
- **Startup and readiness:** The default `/init` runs
  `/bin/enclave_app serve-vsock 4050`. Launch requires the sample JSON exchange
  and an SNP-shaped response on that port.
- **Runtime configuration:** Launch uses one CPU, 160 MiB, a fixed PCIe/vsock
  layout, and one active managed runtime per host.

The initial abstraction removes dependency delivery/build coupling, not every
sample protocol constraint. Customers can already override the application and
init script, but those inputs must remain compatible with launch.

## 3. Target design and agreed decisions

The **dependency triple** is the **L2 kernel, base initrd, and SNP boot shim**.
It is a subset of the **dependency bundle**, which also carries `igvmfilegen`,
IGVM configuration, and provenance records. The bundle remains the pipeline
output and create input; the triple is not a new input format.

| Boundary | Agreed design |
| --- | --- |
| Dependency builds | Run the existing `build-igvm-dependencies.sh` script in a separate pipeline and publish its dependency bundle output. The script owns source/toolchain pinning and reproducibility of the triple; create does not rebuild or assemble that bundle. |
| Application builds | Customers or the samples repository build the executable. Application reproducibility scripts stay in that repository. The default `enclave_app` will live in the same public dependency/sample repository. |
| Default resources | Add a retrieval stub/placeholder in `setup.py`, then retrieve external release resources during extension wheel construction and package them in the wheel as the no-override defaults. Keep existing defaults until this works. The external repository/pipeline owns dependency versions; the extension does not maintain independent component pins. Repository, release assets, and compatibility versions remain TBD. This is separate from `az enclave setup`. |
| Customer overrides | Retain `--dependency-bundle`, `--application`, and `--init-script`. Explicit parameters override the corresponding defaults; customers provide and are responsible for compatible custom resources. Other inputs retain their defaults. Individual artifact options are not required. |
| Create | Consume the dependency bundle and prebuilt application; unpack the bundle's base initrd; install the application and base init script; repack; generate the IGVM and expected measurements; optionally sign its SNP ID block; export a launch profile. |
| Startup | The external repository will contain all default resources before the extension release, including the dependency bundle, sample application, and default init script. Keep the existing `--init-script` replacement option; application selection remains independent of dependency selection. |
| Lifecycle | Preserve lifecycle ownership and teardown behavior. Extend launch configuration to consume the image-bound profile instead of hard-coded resources/devices; define profile selection without redesigning destroy. Verify cleanup and fix gaps as part of implementation. |

```text
Existing dependency build script -> pipeline output: dependency bundle
    (triple + compatible tooling/profile + metadata)
Samples/customer build -> prebuilt application
    -> wheel build: retrieve external resources -> package defaults in extension
       (customer-provided parameter overrides take precedence)
    -> create: unpack -> install app and /init -> repack -> generate -> optional sign
       -> final IGVM + launch profile
    -> launch: validate profile/image -> configure OpenVMM -> existing lifecycle
    -> destroy using the existing lifecycle contract
```

### Image-bound launch configuration

**Proposed, not implemented:** Create exports a versioned launch profile
alongside the IGVM, derived from the exact effective IGVM JSON used to build it,
not a later copy of the bundle configuration. Launch validates the profile and
its image hash before constructing the OpenVMM request through shared mapping
logic. This extends the earlier largely-unchanged launch scope only for
configuration; readiness and process-management responsibilities remain intact.

**Selection rules:** Create writes `<image-filename>.launch.json` adjacent to
the final image (for example, `custom.igvm.launch.json`). Launch uses an explicit
profile path when supplied, otherwise discovers that adjacent file. The exact
option name is not finalized. A present profile must pass schema, settings,
and image-hash validation; an explicitly supplied missing/unreadable path is
an input error, not permission to ignore the requested profile.

If no explicit profile is requested and no adjacent launch profile exists, launch
continues with today's hard-coded resource/device settings and logs a visible
warning identifying that fallback, including one CPU and 160 MiB of memory.
No opt-in compatibility flag is required. This preserves existing image usage
but does not prove that the image is compatible with those settings.

**Current runtime settings source:** `create_request()` in
[`azext_enclave/_rpc.py`](azext_enclave/_rpc.py) hard-codes one CPU, 160 MiB of memory,
PCIe segment 0/buses 0-1, and the low/high MMIO windows. It also configures
COM1 and the vsock attachment. These settings are defined in launch code,
not read from the dependency bundle's IGVM JSON; the proposed missing-profile
fallback retains them.

| Profile setting | Source and purpose |
| --- | --- |
| Schema version | Identifies the profile contract so launch can reject unsupported formats rather than guess field meanings. |
| Final IGVM SHA-256 | Binds the profile to the published image, including optional ID-block signing, to detect accidental mismatches. This is not publisher authentication or attestation verification. |
| Virtual CPU count | Derived from `processor_count`; tells OpenVMM how many processors to provide, matching image expectations. |
| Memory size | Derived from `memory_page_count` using the supported profile's 4-KiB pages, converted to OpenVMM units with exactness/range checks; supplies the guest's required memory. |
| PCIe segment and bus range | Derived from `pcie_host_bridge`; identifies the PCIe address space in which the guest expects devices. |
| Low/high MMIO bases and sizes | Derived from `pcie_host_bridge`; reserves the device memory windows described by the measured image configuration. |
| ECAM base / supported layout | Records where the guest expects PCIe configuration space. Map it only if the runtime exposes that setting; otherwise validate the layout against the pinned runtime and reject unsupported values. Do not silently drop it. |

**Proposed launch profile structure:** This diagram groups the stored data; it is not
a finalized JSON schema. Example resource/device values reflect the current
packaged configuration, not fixed defaults for every image.

```text
Default dependency bundle (currently packaged in the extension)
    manifests/snp-linux-direct.json defines the default IGVM settings
    (selected through dependency-manifest.v1.json's igvm_manifest entry)
    |
    | --dependency-bundle selects a customer bundle instead, if supplied
    v
Create: read selected bundle's IGVM JSON; apply guest-debug override
    |
    v
Exact effective IGVM JSON used by create
    |                                           Final custom.igvm
    | supported resource/device settings        (unsigned by default;
    |                                            SNP ID block signed only
    |                                            with --signing-key)
    |                                               |
    v                                               | SHA-256
+---------------------------------------------------v------------------+
| custom.igvm.launch.json (adjacent JSON launch profile)                |
|                                                                     |
| Contract and image binding                                           |
|   Schema version: identifies the supported profile format            |
|   Final IGVM SHA-256: hash of custom.igvm's published bytes            |
|                                                                     |
| Guest resources                                                      |
|   Virtual CPU count: 1 (from processor_count)                         |
|   Memory size: 160 MiB (from 40960 x 4-KiB pages)                      |
|                                                                     |
| Measured PCIe layout                                                 |
|   Segment: 0; bus range: 0-1                                         |
|   Low MMIO:  base 0xf7000000,  size 16 MiB                            |
|   High MMIO: base 0x100000000, size 1 MiB                              |
|   ECAM base: 0xf6e00000 / supported layout                            |
+---------------------------------------------------------------------+
    |
    v
Launch: validate schema + settings + hash against custom.igvm
    |
    v
Shared mapping -> OpenVMM resource/device configuration
    +
Launch-generated host sockets, serial connections, vsock attachment,
and process ownership (not stored in the launch profile)
```

The JSON is not a complete OpenVMM configuration. Host socket paths, serial
connections, process ownership, and vsock device attachment remain generated by
launch; the profile must not accept arbitrary executable paths or process
arguments. Image-only settings such as kernel command line and SNP image policy
remain in the image-generation configuration, not blindly copied into RPC fields.

Validate CPU/memory limits, unit conversions, bus ranges, MMIO alignment/overlap,
and generator/runtime support. Publish the IGVM and profile consistently, and
reject malformed, mismatched, or unsupported profiles with actionable errors;
do not fall back when a present profile is invalid. The missing-profile path
uses the original configuration with a warning, not inferred image settings.

### Customer customization and build workflow

The following describes **existing overrides**, not proposed individual-artifact
flags. Defaults are currently packaged; the target design changes their source
to repository releases, not the customer's responsibility for custom inputs.

| Component | What the customer can change | How to build and supply it |
| --- | --- | --- |
| L2 kernel | Kernel image and enabled guest features/drivers; not the L1 host kernel. | Build a compatible x86-64 kernel externally, replace the bundle's kernel artifact, and supply the complete bundle through `--dependency-bundle`. |
| Base initrd | Guest filesystem, libraries, utilities, and supporting files. | Build a gzip-compressed cpio initrd externally and replace its bundle artifact. Create subsequently overwrites `/bin/enclave_app` and `/init` with the separately selected inputs. |
| SNP boot shim | Compatible early-boot implementation/version. | Build externally using the supported OpenVMM toolchain and replace its bundle artifact. Kernel/shim/profile compatibility remains the customer's responsibility. |
| IGVM configuration | Kernel command line and compatible image/SNP settings. | Edit the bundle's IGVM JSON and update its manifest hash. Today resource/device changes must match hard-coded launch settings; the proposed launch profile will carry supported settings into launch. Create overrides the JSON's guest-debug setting. |
| `igvmfilegen` | Compatible image-generation tool/version. | Build externally and replace the executable bundle artifact. It must support the CLI's generator commands and measurement/signing outputs; it is not the host OpenVMM runtime. |
| Application | Customer workload logic in one static x86-64 ELF executable. | Build using the customer's toolchain or sample recipe; supply its binary with `--application`. Source directories and container images are not accepted. |
| Init script | Guest initialization, application arguments, and environment. | Supply a shebang script with LF line endings through `--init-script`. This replaces all of guest `/init`, including mounts/initialization, rather than adding a post-start hook. |

**Building a custom bundle:** Start from the existing dependency-build script
linked in the references. It builds pinned sources with Rust/Cargo and Docker
and emits the complete bundle; it does not accept arbitrary prebuilt artifact
paths. Its local-source options require clean checkouts at the pinned revisions.
For customized components, maintain an explicit derivative build recipe or
replace artifacts in a private copy of a compatible bundle after building them
externally. Do not expect create to perform those builds.

Keep `dependency-manifest.v1.json` paths and SHA-256 hashes consistent with every
replaced artifact, and update accompanying build/provenance records and
`SHA256SUMS` when present. Do not retain default-build provenance for custom
outputs. The reader validates all manifest-listed artifacts; regenerating hashes
does not prove authenticity or boot compatibility.

**Building a custom application:** The sample `build-enclave-app.sh` builds the
Rust `enclave_app` package with `--release --locked`, defaulting to
`x86_64-unknown-linux-musl`. The script requires the sample workspace and build
dependencies, not just the script file. Customers can adapt that source/build
recipe or use another language/toolchain that produces the accepted static ELF.
Application reproducibility remains in the sample/customer build workflow.

For example, after building compatible inputs on Linux:

```bash
az enclave create \
    --dependency-bundle "$HOME/custom-dependencies" \
    --application "$HOME/build/my-app" \
    --init-script "$HOME/my-init" \
    --workspace "$HOME/enclave-build" \
    --output-file "$HOME/images/custom.igvm"
```

Omit a resource option to retain that resource's default. A custom bundle does
not select an application or init script implicitly. Use returned measurements
for the rebuilt image, then launch it using the existing command.

### Defaults and compatibility constraints

Values below are from the currently packaged bundle and CLI, not promised values
for every future dependency release.

| Source | Current values | Effect on customization |
| --- | --- | --- |
| Bundle IGVM profile | `guest_arch: x64`, `guest_svn: 1`, `max_vtl: 0`; SNP Linux-direct boot with `use_initrd: true`. | Custom images must remain within the supported x86-64 SNP profile and generator contract. |
| Bundle image resources | `processor_count: 1`, `memory_page_count: 40960` (160 MiB), `c_bit_position: 51`. | Changing image settings does not change launch's one-CPU/160-MiB runtime configuration. |
| Bundle kernel command line | `console=ttyS0 earlyprintk=serial earlycon panic=-1`. | Change through the bundle profile; preserve console/boot behavior needed for diagnosis. |
| Bundle SNP settings | `policy: 196608` (`0x30000`), `injection_type: normal`, `secure_avic: disabled`, `shared_gpa_boundary_bits: null`. | Advanced policy changes require compatibility validation. The bundle records `enable_debug: true`, but create forces it off unless CLI `--debug` is supplied. |
| Bundle measured PCIe layout | Segment 0, buses 0-1; ECAM base `0xf6e00000`; low MMIO base `0xf7000000`, size 16 MiB; high MMIO base `0x100000000`, size 1 MiB. | Keep the measured layout compatible with launch; editing the bundle does not reconfigure the runtime devices. |
| Default init/application startup | Application installed as `/bin/enclave_app`; script executes `serve-vsock 4050`. | A replacement executable must accept those arguments or use a matching custom init script. |
| CLI readiness | Port 4050; newline-delimited challenge/payload JSON with payload `hello world`; requires an SNP-shaped report. | This is enforced by launch, not merely an application default. Different protocols/ports require future readiness work. No attestation verification or key release is performed by launch. |
| CLI composition | `SOURCE_DATE_EPOCH` defaults to 0; normalized cpio metadata and timestamp-free gzip; unsigned unless `--signing-key` is supplied. | Control inputs/tool versions for reproducibility. Changes to image contents/configuration can change expected measurements. |

## 4. Required changes

| Area | Remaining work |
| --- | --- |
| Naming | Use `enclave` for the extension and command group, and `azext_enclave` for the Python package. Preserve existing runtime state and host-lock paths; stop managed instances before replacing the old extension installation. |
| Resource retrieval | Add the `setup.py` placeholder first, then connect it to external releases during wheel construction, with integrity checks, recorded release identity, and explicit errors. The external release owns component versions; release/compatibility selection remains TBD. Verify retrieval in the wheel-build workflow, not during customer installation. |
| Default migration and overrides | Keep current defaults until retrieval works; replace locally maintained resource payloads with build-fetched resources while retaining their inclusion in the wheel. Package all required defaults published before extension release. Preserve per-resource parameter precedence and customer compatibility responsibilities. The stub must not pretend retrieval succeeded. |
| Pipeline and create contract | Run the existing dependency-build script in the pipeline and publish its bundle, including the triple, tooling/profile, and metadata. Reuse existing validation, composition, and optional signing; do not compile dependencies or applications in create. |
| Image-bound launch profile | Define the schema and shared mapping; export the adjacent launch profile bound to the final IGVM. Support explicit profile selection, automatic discovery, and the original hard-coded settings with a visible warning when no launch profile is found. Reject invalid profiles rather than falling back. Test both paths, conversions, unsupported layouts, mismatches, and guest boot. |
| Reproducibility and validation | Verify repository defaults and custom overrides, retrieval failures, output safety, retention, and signing. Compare controlled repeated initrd/IGVM builds and measurements; characterize signed outputs separately. Update help and run Linux image-build/guest tests. |
| Launch/destroy cleanup | Verify existing cleanup for success, failure/timeout, child exits, interrupted teardown, and repeated destroy; fix uncovered gaps. Confirm owned processes are reaped, sockets removed, guards released only after process exit, and logs/evidence retained. |

These are planned changes and validation criteria, not completed implementation
or findings that lifecycle bugs necessarily exist.

## 5. Deferred improvements and open questions

**Deferred:** Individual kernel/initrd/boot-shim overrides; resolved-input
provenance export; modular sample workload/transport/attestation libraries;
generic readiness; independent guest-debug/logging controls; and richer
multi-file or multi-process workloads. These are not prerequisites for the
agreed initial scope.

**Target host follow-up:** Validate the planned commercially available Azure
Linux 4/MSHV image when details arrive in November 2026. Current setup remains
Azure Linux 3.0-specific.

**Open implementation questions:**

- Which public repository/release assets and compatibility versions will be used?

## Appendix: Current code flow and command reference

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

### Overall extension code flow

This section describes the **current implementation**, not the proposed launch
profile or support for additional VM types. The worker and supervisor are the
same Python process: `_worker.py` implements the persistent supervisor; there
is no separate supervisor service or in-memory state daemon.

#### Extension build, packaging, and first installation

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

#### Command dispatch and module responsibilities

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
The image-bound launch profile described earlier is not yet exported.

`custom.py` keeps CLI handling separate from these implementations. For
launch/destroy results, `_instance_result()` omits internal executable and
ownership fields and exposes the recorded supervisor PID.

#### Launch, worker, and runtime sequence

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

#### State, endpoints, and ownership

The default state root remains `/var/lib/ace/instances` for compatibility with
the rename. A custom `--state-dir` changes the instance root, not the host guard.

```text
/run/ace/enclave.lock                host-wide runtime guard

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

#### Failure recovery and destroy

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

### `az enclave setup`

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

### `az enclave create`

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

#### Input contents and customization

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

### `az enclave launch`

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
| `--state-dir` | No | `/var/lib/ace/instances` | Private root-owned state root with mode `0700`. Symlinked paths are rejected; length must fit Unix socket limits. |
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

### `az enclave destroy`

```bash
sudo az enclave destroy --name demo-1
```

Run as root on the same L1 with the original state root. Destroy tears down the
VM through RPC, quits OpenVMM, finishes owned-process cleanup, and removes owned
sockets. Input IGVM, instance record, logs, and saved evidence remain.

| Parameter | Required | Default | Behavior |
| --- | --- | --- | --- |
| `--name`, `-n` | Yes | None | Existing instance name, using launch's naming rules. |
| `--state-dir` | No | `/var/lib/ace/instances` | Must match launch; same privacy/path constraints. |
| `--timeout` | No | `30` seconds | Finite positive timeout; fractional seconds accepted. Cleanup has additional bounded grace periods. |

Repeated destruction of a confirmed destroyed instance is supported; new launches
require new names. Failed destruction is not proof of shutdown: inspect logs and
retry. Returned socket paths are informational after successful cleanup.
Always destroy before changing the environment/extension. Closing the shell or
deactivating does not stop instances; reboot does not automatically restart them.

### References

- [Installation and usage](README.rst)
- [Arguments](azext_enclave/_params.py), [help](azext_enclave/_help.py), and [handlers](azext_enclave/custom.py)
- [Image builder](azext_enclave/_image_builder.py), [host setup](azext_enclave/_setup.py), and [lifecycle](azext_enclave/_lifecycle.py)
- [Dependency build script](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/enclave/scripts/build-igvm-dependencies.sh&version=GBfeature/modular-igvmfile-generation)
- [Application build script](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/ace/scripts/build-enclave-app.sh&version=GBusers/kpostiglione/ace-skr-demo)
