# Enclave extension improvements

This document tracks proposed improvements to the current extension. The first
new focus is making existing enclave instances and their logs discoverable
before users launch another enclave. The appendix remains a reference for the
current implementation; proposals below are not implemented behavior.

## 1. Proposed change: `az enclave status`

### Purpose

Add a read-only command that lists retained enclave instances, including running
and destroyed instances, and shows where their output logs can be viewed.
Users should be able to discover names already taken by previous launches and
identify an active runtime that must be destroyed before another can launch.

Today, launch refuses an existing instance directory even after destroy,
because its records and logs are retained. Separately, a host-wide guard permits
only one managed runtime at a time. Status should explain both constraints:
a destroyed enclave still reserves its name, while a live owned runtime can
block a launch under a different name.

### Command and output

Proposed usage on the local Linux L1 host:

```bash
sudo az enclave status --output table
sudo az enclave status --name demo-1 --output json
sudo az enclave status --name demo-1 --state-dir /path/to/instances --output json
```

Without `--state-dir`, list every retained instance in
`/var/lib/enclave/instances`, not just those recorded as running. Include failed,
starting, paused, and destroy-failed records as well as running/destroyed
records, since all can reserve names or require investigation.

Without `--name`, return a compact list of names and observed states. Preserve
states such as `starting`, `failed`, `exited`, and `unknown` rather than forcing
every entry into running/destroyed. A destroyed result requires a recorded
successful destroy and no remaining owned processes; an unexpected runtime exit
is not equivalent to successful destruction.

With `--name`, return details for exactly that instance in the selected state
root. Report a missing name as an actionable error, not an empty successful
result. Both forms support `--state-dir`; named inspection adds the information
below without requiring a separate show command.

| Output information | Purpose |
| --- | --- |
| Name and instance directory | Identify the occupied launch name and the state root to use for follow-up commands. |
| Recorded lifecycle status | Show the last persisted outcome, such as `running`, `failed`, or `destroyed`. |
| Observed runtime state | Check the recorded supervisor/OpenVMM identities using existing ownership helpers. Distinguish live processes, exited processes, and unknown/inconsistent state rather than treating a saved `running` value as proof of liveness. |
| Name availability | Indicate that an existing instance directory reserves the name, even if its runtime has exited or been destroyed. Do not imply that destroy makes the name reusable. |
| Log locations | Show the paths to `com1.log` (guest output), `openvmm.log` (runtime output), and `supervisor.log` (worker output/errors). Report logs not yet created as unavailable rather than creating them. |
| Image and runtime details | Identify the selected IGVM and the recorded supervisor/OpenVMM process identities, with observed liveness clearly distinguished from saved metadata. |
| Retained response and errors | Locate `response.json` when present and expose recorded launch, destroy, worker, or cleanup failures to support diagnosis. Evidence collection does not imply attestation verification. |
| Lifecycle timestamps | Show when the instance record was created and when its recorded lifecycle status last changed, helping users distinguish old retained instances from recent activity. |

Keep the unnamed listing limited to name and state in both table and structured
output. Named inspection exposes recorded status, process observations,
instance location, and individual log paths in structured output, with a readable
summary for table output. Host-guard warnings apply to both forms and must not
be lost when presenting the compact list.
Users can inspect guest output with, for example,
`sudo tail -f /var/lib/enclave/instances/<name>/com1.log`.
Status locates logs; streaming or downloading them is not part of this change.

### Lifecycle timestamps

Persist `created_at` when launch first creates the instance record and
`status_changed_at` when a lifecycle operation changes its recorded status.
Use timezone-aware UTC timestamps in a consistent ISO 8601 format. Keep creation
time immutable; reading status must not update either timestamp.

Expose both values in named status details while keeping the unnamed list
compact. These timestamps describe recorded lifecycle events, not continuous
health monitoring: an unexpected process exit need not have a recorded exit
time. Show timestamps as unavailable for older records that lack them, rather
than inferring them from filesystem modification times.

### Discovery and read-only behavior

Reuse existing state-directory validation, record parsing, and full process
identity checks. Status must not start/stop processes, invoke teardown, rewrite
lifecycle records, remove evidence, or acquire the exclusive runtime lease
needed by launch. It must work while an enclave is running and enforce the
same local access protections as other lifecycle commands.

Treat results as a point-in-time snapshot. Display missing, malformed,
unreadable, or inconsistent records explicitly instead of silently omitting
an occupied name or reporting it as healthy. An absent default state root can
be reported as no recorded instances without creating it; an explicitly
requested missing/unreadable root should produce an actionable error.

Listing is scoped to the selected state root. Because the runtime guard is
host-wide, also report whether that guard is occupied without retaining its
lock. If it is occupied but no matching live instance is discoverable in the
selected root, explicitly report that the owner may be in another state root
or cannot be identified; an empty list must not imply the host is free.
Automatic discovery of every custom state root would require additional
tracking and is not assumed by this proposal.

### Launch error guidance

Update the existing name-conflict error to recommend
`az enclave status`, preserving `--state-dir` when a custom root was used.
Explain that retained evidence reserves the name and that the user should
choose a new `--name`; recommend `az enclave status --name <name>` to inspect
the retained instance. Do not recommend destroying a stopped instance as a way
to release its name.

Update the host-guard conflict error to recommend status to identify the
blocking runtime, then `az enclave destroy --name <name>` with the matching
state root. If the owner is outside the selected root, keep the discovery
limitation visible rather than guessing a name or suggesting forceful cleanup.

### Implementation and validation

Register the command/help/arguments and delegate through `custom.py` to
read-only lifecycle/state inspection. Reuse ownership and log-path helpers,
but do not call launch/destroy paths that create directories or mutate state.
Add distinct list/detail result formatting so the compact list stays small
while named inspection retains structured log and diagnostic fields.

Test empty roots; running, destroyed, and failed instances; stale `running`
records; missing/corrupt records and logs; custom roots; and a busy host guard
whose owner is not listed. Verify occupied names remain visible, status does
not modify records or interrupt live runtimes, and both launch-conflict errors
provide correct status guidance. Test named inspection of existing and missing
names, custom-root selection, and the difference between the compact list and
detailed results. Keep the appendix's current command reference unchanged until
the command is implemented.
Verify creation time remains unchanged across transitions, last-change time
updates only when status changes, status reads do not modify timestamps, and
legacy records without timestamps remain inspectable.

## 2. Proposed change: `az enclave cleanup` and `destroy --cleanup`

Keep read-only inspection, runtime shutdown, and removal of retained evidence
as distinct operations:

| Operation | Responsibility |
| --- | --- |
| `az enclave status` | List retained names and states without modifying them. |
| `az enclave status --name <name>` | Inspect one instance, its runtime observations, logs, and errors. |
| `az enclave destroy --name <name>` | Stop the owned runtime and remove runtime sockets, retaining records and logs by default. |
| `az enclave cleanup --name <name>` | Remove a stopped instance's retained directory and release its name. Refuse cleanup while either owned process is alive. |
| `az enclave destroy --name <name> --cleanup` | Stop the owned runtime, confirm shutdown, then remove its retained directory using the same cleanup implementation. |
| `az enclave cleanup --name <name> --dry-run` | Preview retained-state removal and report blockers without deleting files or stopping processes. |
| `az enclave destroy --name <name> --cleanup --dry-run` | Preview shutdown followed by retained-state removal without performing either step. |

Use `cleanup` rather than `delete` to distinguish removal of retained state
from runtime destruction. Destroy already removes runtime resources and
sockets; the new `--cleanup` option specifically opts into deleting records,
logs, and response evidence. Without this option, destroy retains its current
behavior and does not release the instance name.

The cleanup command should require an explicit name, honor `--state-dir`,
coordinate with lifecycle locks, and verify that neither owned process is
alive. If either is alive, refuse removal and direct the user to destroy first.
If ownership or shutdown cannot be established, report that uncertainty rather
than treating missing or corrupt records as proof that deletion is safe.

Require confirmation that retained records, logs, and response evidence will
be deleted. Limit removal to the validated instance directory; preserve the
original IGVM and other external inputs. Release the name only after removal
succeeds, and surface partial failures explicitly so status can still identify
remaining state. Do not remove shared locks in a way that permits concurrent
commands to bypass lifecycle coordination.

The intended workflow is **list -> inspect -> destroy -> cleanup**.
`destroy --cleanup` combines the last two steps as an explicit convenience,
not a change to the default retention policy. Both cleanup paths must share
validation and deletion confirmation; obtain confirmation before starting
the combined destructive operation. Hold lifecycle coordination across shutdown
and removal, and recheck owned-process exit before deleting retained files.

If destruction fails or shutdown is unconfirmed, skip cleanup and retain the
evidence. If shutdown succeeds but file removal fails, report that distinction
and the remaining state location rather than claiming complete success. The
user can inspect the retained state and retry standalone cleanup.

### Dry-run preview

`--dry-run` should show the resolved instance directory, files/directories
targeted for removal, whether the name would be released, and the external
image/inputs that will be preserved. Apply the same path, ownership, and
process-identity checks used for actual cleanup. If standalone cleanup is
blocked by a live or unverifiable runtime, explain the blocker rather than
presenting the plan as safe to execute.

For `destroy --cleanup --dry-run`, show shutdown as the first planned step
and removal as conditional on confirmed shutdown. Do not send RPC shutdown
requests, signal processes, modify records/logs, or delete files. A preview
does not reserve the name or authorize later deletion; execution must revalidate
the instance under lifecycle coordination and obtain normal confirmation.

Validate standalone cleanup of stopped and destroyed instances, rejection of
live or unverifiable runtimes, confirmation cancellation, custom state roots,
and concurrent lifecycle commands. Test both successful and failed
`destroy --cleanup` paths, including partial file-removal failures. Verify
ordinary destroy still retains evidence, cleanup preserves external images,
and a name becomes reusable only after its instance directory is removed.
Verify both dry-run forms leave processes and retained files unchanged, show
the same targets as real cleanup, report blockers, and require revalidation
when the instance changes between preview and execution.

## 3. Existing fixes and follow-up work

Retain the existing work items below while shifting the detailed proposal to
status and discoverability. Each row explains why the change matters as well
as its implementation or validation work; these are not all completed fixes.

| Area | Purpose | Remaining work |
| --- | --- | --- |
| CLI core support floor | Make the declared minimum CLI version reflect tested behavior. This avoids accepting installations whose CLI APIs cannot actually run the extension. | Establish an evidence-based `azext.minCliCoreVersion`: the inherited `2.0.67` value is a declared minimum, not a validated support floor. Inventory the CLI APIs used and test candidate older core versions for extension installation/discovery, help, argument wiring, and setup/create/launch/destroy behavior on the supported Linux host. Set the minimum to the oldest verified version and document the tested range and rationale in the README. Metadata-gate tests alone do not prove runtime compatibility; leaving the maximum unset does not guarantee compatibility with future breaking changes. |
| Naming | Make command, package, and state naming consistent. Define the upgrade boundary so old and new installations are not accidentally used together. | Use `enclave` for the extension and command group, and `azext_enclave` for the Python package. State uses `/var/lib/enclave/instances` and the host guard uses `/run/enclave/enclave.lock`. Stop old runtimes and retire old installations before upgrading; no automatic state migration or mixed-version launches. |
| Resource retrieval | Obtain default resources from the release that owns their versions instead of independently maintaining copies. Keep retrieval in wheel construction so customer installation does not need to fetch build inputs. | Add the `setup.py` placeholder first, then connect it to external releases during wheel construction, with integrity checks, recorded release identity, and explicit errors. The external release owns component versions; release/compatibility selection remains TBD. Verify retrieval in the wheel-build workflow, not during customer installation. |
| Default migration and overrides | Change how defaults are supplied without breaking existing no-override usage. Preserve customers' ability to replace individual supported inputs independently. | Keep current defaults until retrieval works; replace locally maintained resource payloads with build-fetched resources while retaining their inclusion in the wheel. Package all required defaults published before extension release. Preserve per-resource parameter precedence and customer compatibility responsibilities. The stub must not pretend retrieval succeeded. |
| Pipeline and create contract | Keep dependency compilation in a reproducible external build pipeline. Let create remain an image-composition command that consumes already-built inputs. | Run the existing dependency-build script in the pipeline and publish its bundle, including the L2 kernel/base initrd/SNP boot shim, tooling/profile, and metadata. Reuse existing validation, composition, and optional signing; do not compile dependencies or applications in create. |
| Image-bound launch profile | Keep runtime resources/devices consistent with the configuration used to build the image. Binding the profile to the final image helps detect accidentally mixed artifacts. | Define the schema and shared mapping; export the adjacent launch profile bound to the final IGVM. Support explicit profile selection, automatic discovery, and the original hard-coded settings with a visible warning when no launch profile is found. Reject invalid profiles rather than falling back. Test both paths, conversions, unsupported layouts, mismatches, and guest boot. |
| Reproducibility and validation | Establish that defaults and supported overrides produce reliable, explainable artifacts. Cover failure and retention behavior as well as successful builds so failures do not look like success. | Verify repository defaults and custom overrides, retrieval failures, output safety, retention, and signing. Compare controlled repeated initrd/IGVM builds and measurements; characterize signed outputs separately. Update help and run Linux image-build/guest tests. |
| Launch/destroy cleanup | Prevent orphaned owned processes or sockets from blocking later launches. Retain diagnostic evidence while releasing runtime resources only after shutdown is confirmed. | Verify existing cleanup for success, failure/timeout, child exits, interrupted teardown, and repeated destroy; fix uncovered gaps. Confirm owned processes are reaped, sockets removed, guards released only after process exit, and logs/evidence retained. |

These are planned changes and validation criteria, not completed implementation
or findings that lifecycle bugs necessarily exist.

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
| `--state-dir` | No | `/var/lib/enclave/instances` | Must match launch; same privacy/path constraints. |
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
