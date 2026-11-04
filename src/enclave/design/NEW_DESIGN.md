# Enclave extension improvements

This document tracks proposed improvements to the current extension. The first
new focus is making existing enclave instances and their logs discoverable
before users launch another enclave. See [CURRENT_DESIGN.md](CURRENT_DESIGN.md)
for the current implementation and command reference; proposals below are not
implemented behavior.

The isolation profile registry, type-specific behavior, and TDX RPC work are
specified in [ISOLATION_TYPES_DESIGN.md](ISOLATION_TYPES_DESIGN.md). The resource
assembly and launch-profile proposals below use that shared design rather than
assuming every dependency or runtime setting is SNP-specific.

## 1. Proposed change: fixed instance location

Use `/var/lib/enclave/instances` as the sole, non-overridable instance root.
Every managed enclave uses `/var/lib/enclave/instances/<name>`, so names are
unique across the host's managed inventory and status can discover all retained
instances in one place. Do not introduce an equivalent environment-variable or
configuration override.

### Affected commands and components

| Command/component | Required change |
| --- | --- |
| `az enclave launch` | Remove `--state-dir`; always create instance records, runtime endpoints, and logs under the fixed root. Preserve name validation and refusal to overwrite retained instances. |
| `az enclave destroy` | Remove `--state-dir`; resolve `--name` under the fixed root. Preserve evidence by default; `--cleanup` removes only the validated named instance after confirmed shutdown. |
| `az enclave status` | Do not add `--state-dir`. List the shared inventory or inspect one named instance there. |
| `az enclave cleanup` | Do not add `--state-dir`. Resolve the explicit name under the fixed root for both preview and execution; do not accept arbitrary deletion paths. |
| Setup and create | No instance-root option is needed. Keep create's `--workspace`, `--output-file`, and input-path overrides: build artifacts are separate from managed runtime state. Setup must not start a runtime or migrate instance records. |
| Worker and state helpers | Resolve CLI lifecycle operations through one shared root constant. Remove state-root parameters from command handlers and lifecycle entry points, and derive worker paths from the validated instance directory rather than exposing another override. |
| Help, errors, and tests | Remove `--state-dir` from argument registration, usage examples, and suggested commands. Cover the fixed-root behavior and rejection of the removed option; isolate test paths internally without touching real host state or adding a production override. |

### Ownership, compatibility, and storage

Keep the root private and root-owned, validate paths without following symlinks,
and retain full process-identity checks. Retain the host-wide runtime lock:
a shared directory alone does not prevent concurrent launches. Cross-root
discovery and a host-wide owner record for locating custom roots are no longer
required; status can inspect the shared inventory to identify the blocker.

This is a breaking CLI change. Reject `--state-dir` explicitly rather than
silently ignoring it. Before upgrading, users must stop runtimes launched by
older versions, including those in custom roots, using that version's destroy
command. Do not run old and new installations together or automatically move
live instance directories. Document an explicit offline migration or archival
procedure for retained custom-root records; the new status command must not
claim to discover unmigrated legacy directories.

The fixed location reduces storage-placement flexibility. Administrators may
mount dedicated storage at that location, but must preserve ownership and
permissions; a symlink is not a supported substitute. Account for retained-log
disk usage and report directory creation/write failures clearly.
[CURRENT_DESIGN.md](CURRENT_DESIGN.md) continues to document today's configurable
interface until implementation.

## 2. Proposed change: `az enclave status`

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
```

List every retained instance in the fixed directory
`/var/lib/enclave/instances`, not just those recorded as running. Include failed,
starting, paused, and destroy-failed records as well as running/destroyed
records, since all can reserve names or require investigation.

Without `--name`, return a compact list of names and observed states. Preserve
states such as `starting`, `failed`, `exited`, and `unknown` rather than forcing
every entry into running/destroyed. A destroyed result requires a recorded
successful destroy and no remaining owned processes; an unexpected runtime exit
is not equivalent to successful destruction.

With `--name`, return details for exactly that instance under the fixed root.
Report a missing name as an actionable error, not an empty successful result.
Neither form accepts `--state-dir`; named inspection adds the information below
without requiring a separate show command.

| Output information | Purpose |
| --- | --- |
| Name and instance directory | Identify the occupied launch name and its directory under the fixed instance root. |
| Recorded lifecycle status | Show the last persisted outcome, such as `running`, `failed`, or `destroyed`. |
| Observed runtime state | Check the recorded supervisor/OpenVMM identities using existing ownership helpers. Distinguish live processes, exited processes, and unknown/inconsistent state rather than treating a saved `running` value as proof of liveness. |
| Name availability | Indicate that an existing instance directory reserves the name, even if its runtime has exited or been destroyed. Do not imply that destroy makes the name reusable. |
| Log locations | Show the paths to `com1.log` (guest output), `openvmm.log` (runtime output), and `supervisor.log` (worker output/errors). Report logs not yet created as unavailable rather than creating them. |
| Image and runtime details | Identify the selected IGVM and the recorded supervisor/OpenVMM process identities, with observed liveness clearly distinguished from saved metadata. |
| Isolation configuration | Include the persisted isolation type and supported configuration identity in named details, without expanding the compact default list. Do not infer identity from the currently packaged defaults. |
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
an occupied name or reporting it as healthy. An absent fixed state root can be
reported as no recorded instances without creating it; an unreadable or unsafe
root should produce an actionable error.

Also report whether the host-wide runtime guard is occupied without retaining
its lock. If it is occupied but no matching live instance is discoverable,
report inconsistent or unavailable ownership information rather than guessing
a name. An empty inventory must not imply the host is free when the guard is
busy; startup races and incomplete records still need explicit handling.

### Launch error guidance

Update the existing name-conflict error to recommend
`az enclave status`.
Explain that retained evidence reserves the name and that the user should
choose a new `--name`; recommend `az enclave status --name <name>` to inspect
the retained instance. Do not recommend destroying a stopped instance as a way
to release its name.

Update the host-guard conflict error to recommend status to identify the
blocking runtime, then `az enclave destroy --name <name>`. If ownership cannot
be established from the shared inventory, report that uncertainty rather than
guessing a name or suggesting forceful cleanup.

### Implementation and validation

Register the command/help/arguments and delegate through `custom.py` to
read-only lifecycle/state inspection. Reuse ownership and log-path helpers,
but do not call launch/destroy paths that create directories or mutate state.
Add distinct list/detail result formatting so the compact list stays small
while named inspection retains structured log and diagnostic fields.

Test empty roots; running, destroyed, and failed instances; stale `running`
records; missing/corrupt records and logs; an unsafe/unreadable fixed root; and a busy host guard
whose owner is not listed. Verify occupied names remain visible, status does
not modify records or interrupt live runtimes, and both launch-conflict errors
provide correct status guidance. Test named inspection of existing and missing
names, rejection of `--state-dir`, and the difference between the compact list and
detailed results. Keep the current command reference in
[CURRENT_DESIGN.md](CURRENT_DESIGN.md) unchanged until the command is implemented.
Verify creation time remains unchanged across transitions, last-change time
updates only when status changes, status reads do not modify timestamps, and
legacy records without timestamps remain inspectable.

## 3. Proposed change: `az enclave cleanup` and `destroy --cleanup`

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

The cleanup command should require an explicit name, use the fixed instance root,
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
live or unverifiable runtimes, confirmation cancellation, fixed-root containment,
and concurrent lifecycle commands. Test both successful and failed
`destroy --cleanup` paths, including partial file-removal failures. Verify
ordinary destroy still retains evidence, cleanup preserves external images,
and a name becomes reusable only after its instance directory is removed.
Verify both dry-run forms leave processes and retained files unchanged, show
the same targets as real cleanup, report blockers, and require revalidation
when the instance changes between preview and execution.

## 4. Proposed change: guest application path

### Purpose and parameter contract

Add `--application-path` to `az enclave create` so users can change the
application's guest installation and execution path without replacing the
entire init script. The default init mounts guest filesystems, prints startup
diagnostics, and uses `exec` to replace its shell with the application.
Providing a custom init currently replaces all of that initialization, even
when the user only needs a different executable path.

Distinguish the existing `--application` host input from the new guest path:

| Parameter | Meaning |
| --- | --- |
| `--application` | Path to the prebuilt executable on the build host. If omitted, use the packaged application. Its original filename does not determine its guest installation path. |
| `--application-path` | Absolute Linux path inside the guest where create installs the selected executable and where the default init executes it. Defaults to `/bin/enclave_app`. It is not a host input path or an arbitrary shell command. |
| `--init-script` | Complete replacement for guest `/init`, retaining existing application placement at `/bin/enclave_app`. Mutually exclusive with an explicitly supplied `--application-path`. |

Support three modes:

| Selection | Result |
| --- | --- |
| Neither `--application-path` nor `--init-script` | Preserve the current default init and installation at `/bin/enclave_app`. |
| `--application-path` only | Install the selected application at that guest path and generate the compatible default init with the matching executable path. Preserve the selected profile's initialization and startup arguments; the existing SNP default uses `serve-vsock 4050`. |
| `--init-script` only | Install the selected application at the existing default path and use the customer's full init unchanged. The customer owns initialization and startup behavior. |

Reject both options together with an actionable error, even if the explicitly
supplied application path equals the default. `--application` remains usable
with any of these modes; the new path option must also work with the packaged
application.

For example:

```bash
az enclave create \
    --application ./my-app \
    --application-path /opt/my-app \
    --output-file ./custom.igvm
```

With the optional workspace described below, this installs the host executable
as `/opt/my-app` in the guest and generates an init ending with:

```sh
exec /opt/my-app serve-vsock 4050
```

This example uses the default SNP profile. This is a path-only change:
customers needing different startup arguments or environment setup still supply
a custom init. Keep the existing readiness protocol/port for SNP; additional
isolation profiles must supply compatible defaults and their defined readiness
contract as described in `ISOLATION_TYPES_DESIGN.md`. Changing the executable
path does not make arbitrary applications compatible with launch.

### Composition, validation, and testing

Update argument registration/help and `custom.py` to distinguish an omitted
path option from an explicitly supplied one and enforce mutual exclusion.
Pass the resolved guest destination into the image builder so installation and
init generation cannot disagree. Generate a build-local init from the packaged
default; do not modify the packaged script or customer input files in place.

Validate the guest path as an absolute Linux executable destination. Reject
parent traversal, invalid file destinations, and reserved paths such as `/init`.
Translate it to a destination inside the composed rootfs, create required
parent directories, and prevent symlinks from redirecting writes outside that
root. Preserve existing executable format checks and permissions. Quote the
executable as a single shell argument so spaces or metacharacters cannot alter
the generated command; do not treat the option as executable arguments.

Test unchanged defaults, a custom host application with a custom guest path,
the packaged application at a custom path, parent-directory creation, safe
quoting, invalid paths/symlink escapes, and explicit-option conflicts. Verify
custom init contents remain unchanged and the generated init executes the
actual installed path. Preserve deterministic composition and use newly
generated measurements: changing the destination or init contents can change
the final image measurement. Update create help and examples when implemented.

## 5. Proposed change: optional build workspace

Make `az enclave create --workspace` optional, defaulting to
`$HOME/enclave-build` for the user running create. Resolve the user's home
directory at invocation time, not as a literal `$HOME` path or a build-time
developer path. An explicitly supplied `--workspace` continues to select
another parent directory, for example on a disk with more free space.

```bash
# Default build parent: $HOME/enclave-build
az enclave create --output-file ./custom.igvm

# Override the build parent
az enclave create --workspace /mnt/build/enclave --output-file ./custom.igvm
```

The workspace is a host-side build area, not a guest path or runtime instance
directory. Create allocates a unique private subdirectory under the selected
parent for extracted dependencies, the composed guest filesystem/initrd,
effective generator configuration, generated artifacts, and stage logs.
Create the parent if necessary; never use the entire parent as a disposable
build directory.

Preserve existing retention behavior: normally remove the private build
subdirectory after success or failure; `--keep-intermediates` retains it and
reports its exact location for debugging. Do not remove the parent or unrelated
files, and preserve the final IGVM selected by `--output-file`. Report an
unavailable home directory or unusable default workspace explicitly, with
guidance to supply `--workspace`; do not silently switch to a different root.

Remove the required-argument setting and resolve the default in the command
handling path before calling the builder. Keep existing input/output overlap
checks and private-directory creation for both default and explicit paths.
Do not conflate this overridable build parent with the fixed
`/var/lib/enclave/instances` runtime root from proposal 1.

Test omitted/explicit workspaces, invocation-user home resolution, missing
parent creation, unwritable paths, protected-input overlaps, concurrent builds
with separate private directories, and retained intermediates on success and
failure. Verify cleanup never removes unrelated parent contents. Update CLI
help and examples without changing the existing explicit-workspace behavior.

## 6. Proposed change: CLI core support floor

### Purpose

Make the declared minimum CLI version reflect tested behavior instead of an
inherited metadata value. This prevents installations from appearing supported
when their CLI APIs or Python environment cannot actually run the extension.
The current `azext.minCliCoreVersion` value of `2.0.67` is a declaration, not a
validated support floor.

### Required changes and validation

Inventory the CLI APIs and Python runtime dependencies used by command loading,
argument registration, output formatting, and error handling. Test candidate
older core versions for wheel installation/discovery, help, and the supported
setup/create/launch/destroy paths on the supported Linux host. Include the
proposed status/cleanup commands and new create options as they are implemented.

Set the minimum to the oldest verified version and record the tested CLI,
Python, and dependency combinations and rationale in the
[README](../README.rst). Keep the floor aligned with isolation-profile and
protocol dependency changes. Metadata-gate tests alone do not prove runtime
compatibility, and an unset maximum does not guarantee compatibility with
future breaking changes.

## 7. Proposed change: resource retrieval and default migration

### Purpose and ownership

Move default-resource delivery to externally versioned releases without
breaking no-override usage or customer inputs. The external repository supplies
individual prebuilt artifacts; CLI wheel-build tooling assembles them into one
combined dependency bundle. The CLI must not assume it receives an already
assembled external bundle or compile dependencies/applications itself.

The detailed release-manifest, isolation-registry, and bundle contracts are
defined in [ISOLATION_TYPES_DESIGN.md](ISOLATION_TYPES_DESIGN.md).

### Wheel construction and migration

Add a dedicated retrieval/assembly helper under `src/enclave/scripts` and invoke
it in the actual wheel-build phase through thin `setup.py` orchestration.
Retrieve pinned individual artifacts, verify their expected integrity and
compatibility, and record release identity, paths, hashes, and provenance in a
versioned combined-bundle manifest. Shared artifacts may serve multiple
isolation profiles; required dependency sets are profile-specific.

Package the combined bundle and compatible application/init defaults in the
wheel. Do not download during metadata inspection, customer wheel installation,
or `az enclave setup`. Build staging must be private and cleaned up without
modifying source inputs. Retrieval, verification, or assembly failure must stop
wheel publication rather than silently packaging incomplete/stale resources.

Keep the current packaged defaults until the replacement build path works.
Verify that all advertised profiles' required defaults are available before
switching delivery. External releases own artifact versions; keep CLI
release-selection metadata and compatibility constraints, not independent
component version histories.

### Customer overrides and validation

Preserve `--dependency-bundle`, `--application`, and `--init-script`, including
the application-path/custom-init rules in proposal 4. Explicit inputs override
their corresponding defaults independently. For the selected isolation type,
a customer dependency bundle must supply all required artifacts; it may omit
unselected profiles but must not silently borrow missing resources from the
packaged bundle. A custom bundle does not implicitly select a customer
application or init script.

Test retrieval failures, hashes and release identity, safe extraction/staging,
deterministic bundle assembly, and actual wheel contents. Verify no-override
create, legacy SNP bundles, selected-profile customer bundles, and independent
application/init overrides. Compare controlled repeated builds and report
compatibility errors explicitly. Repository/release locations and the exact
source-manifest format remain integration decisions with the artifact producer.

## 8. Proposed change: image-bound launch profile

### Purpose and contents

Keep launch configuration consistent with the exact image create produced,
rather than applying fixed SNP resources/devices to every image. Export a
versioned launch-profile JSON file alongside the final IGVM, derived from the
effective generation configuration after create's overrides, not a later copy
of the bundle JSON.

| Profile information | Use at launch |
| --- | --- |
| Schema version | Reject unsupported formats instead of guessing field meanings. |
| Final IGVM SHA-256 | Detect mismatched image/profile pairs after any optional signing. This is not publisher authentication or attestation verification. |
| Isolation type, architecture, and supported boot/configuration identity | Select a supported isolation-registry entry and verify generator/runtime compatibility. Do not infer identity from current defaults. |
| Virtual CPU count and guest memory | Configure supported resources with exact unit conversions, limits, and alignment checks. |
| Supported device layout | Map measured PCIe segment/bus ranges and MMIO windows where applicable. Validate ECAM/runtime support; reject unsupported settings rather than silently dropping them. |

Keep host paths, serial/vsock sockets, process ownership, and executable
arguments generated by trusted CLI logic. The profile is not an arbitrary
OpenVMM configuration or a mechanism to execute bundle-defined host commands.
Image-only settings must not be blindly copied into RPC fields.

### Selection, compatibility, and validation

Create writes `<image-filename>.launch.json` adjacent to the image, for example
`custom.igvm.launch.json`, and publishes the pair consistently. Launch accepts
an explicit profile path or discovers the adjacent file; the option name
remains to be finalized. A supplied missing/unreadable profile or any present
invalid profile is an error, not permission to fall back.

Use the profile's isolation identity rather than asking users to repeat a
potentially contradictory launch selection. Validate the image hash, supported
configuration, resource/device settings, and host/runtime compatibility before
issuing VM creation requests. Persist the validated identity in instance state
for named status and any type-specific teardown handling.

Only when no explicit profile was requested and no adjacent profile exists,
retain the documented legacy SNP configuration with a visible warning,
including its one CPU and 160 MiB settings. Do not use this path for new
isolation types or downgrade to another type. New isolation types, including
customer-built images, require an image-bound profile under the supported
contract; document how customers can produce one.

Test automatic/explicit selection, legacy fallback, final-image hash mismatches,
malformed/unsupported schemas, type/configuration mismatches, exact conversions,
bus/MMIO ranges and overlaps, ECAM compatibility, and actual guest boot.
Use shared mapping with type-specific validation as required by the isolation
registry. Protocol/schema support alone is not evidence that a guest can boot.

## 9. Proposed change: launch/destroy runtime cleanup

### Purpose and boundary

Verify and close gaps in runtime teardown so owned processes or stale sockets
do not block later launches. This concerns releasing runtime resources, not
deleting retained evidence: proposal 3 separately defines `cleanup`,
`destroy --cleanup`, confirmation, and dry-run behavior.

### Required behavior and validation

Preserve shared supervision and ownership checks across isolation types.
Prefer RPC teardown/server shutdown, confirm exit, and apply only the existing
bounded owned-process recovery where appropriate. Unconfirmed destroy RPC
failures remain retryable; do not turn an error into forced shutdown or a
success-shaped result.

Verify launch success, failed/timed-out creation, guest-readiness failure,
worker/server exits, interrupted teardown, and repeated destroy. Confirm the
supervisor reaps its child, recovery never signals a reused/unowned PID, known
sockets are removed safely, and the host guard remains held until its owning
processes exit. Report cleanup errors and preserve logs, records, timestamps,
and isolation identity needed for status and diagnosis.

Default destroy must retain evidence and the occupied name. With explicit
`--cleanup`, require confirmed shutdown before entering proposal 3's shared
retained-state removal path; dry-run must not stop processes or remove files.
Test concurrent lifecycle operations and partial failures so status distinguishes
confirmed shutdown from incomplete cleanup and never reports a retained
instance directory as a free name.

These are proposed changes and validation criteria, not claims that lifecycle
bugs have already been found or the new behaviors are implemented.
