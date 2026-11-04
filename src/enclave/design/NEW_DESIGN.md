# Enclave extension improvements

This document tracks proposed improvements to the current extension, grouped by
related behavior and shared prerequisites. See [CURRENT_DESIGN.md](CURRENT_DESIGN.md)
for the current implementation and command reference; proposals below are not
implemented behavior.

The isolation profile registry, type-specific behavior, and TDX RPC work are
specified in [ISOLATION_TYPES_DESIGN.md](ISOLATION_TYPES_DESIGN.md). The resource
assembly and launch-profile proposals below use that shared design rather than
assuming every dependency or runtime setting is SNP-specific.

## Isolation framework, dependencies, and launch configuration

The registry defines supported behavior and required artifacts; dependency
assembly supplies those artifacts, and the launch profile carries the selected
configuration from image creation into launch.

### 1. Proposed change: isolation framework abstraction

Replace SNP-specific assumptions with a CLI-owned isolation profile registry
and shared command workflows. Add `create --isolation-type` (default `snp`);
resolve each profile's required artifacts, configuration validation, image
generation, measurement/signing, RPC mapping, and readiness behavior through
that registry. Carry the validated isolation/configuration identity into the
image-bound launch profile and instance state so launch, status, destroy, and
cleanup use consistent behavior without duplicating whole lifecycles per type.

Include TDX RPC schema/client support and compatibility validation. Keep this
work CLI-only: external artifact production and OpenVMM server/hypervisor
implementation remain prerequisites, not CLI deliverables. Do not advertise a
new isolation type as supported until its end-to-end path is validated.
See [ISOLATION_TYPES_DESIGN.md](ISOLATION_TYPES_DESIGN.md) for the full contracts,
command impacts, risks, and acceptance criteria.

### 2. Proposed change: external dependency retrieval and default migration

#### Purpose and ownership

Move default-resource delivery to externally versioned releases without
breaking no-override usage or customer inputs. The external repository supplies
individual prebuilt artifacts; CLI wheel-build tooling assembles them into one
combined dependency bundle. The CLI must not assume it receives an already
assembled external bundle or compile dependencies/applications itself.

The detailed release-manifest, isolation-registry, and bundle contracts are
defined in [ISOLATION_TYPES_DESIGN.md](ISOLATION_TYPES_DESIGN.md).

#### Wheel construction and migration

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

#### Customer overrides and validation

Preserve `--dependency-bundle`, `--application`, and `--init-script`, including
the application-path/custom-init rules in proposal 9. Explicit inputs override
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

### 3. Proposed change: image-bound launch profile

#### Purpose and contents

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

#### Selection, compatibility, and validation

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

## Instance state, shutdown, and cleanup

### 4. Proposed change: fixed instance location

Use `/var/lib/enclave/instances` as the sole, non-overridable instance root.
Every managed enclave uses `/var/lib/enclave/instances/<name>`, so names are
unique across the host's managed inventory and status can discover all retained
instances in one place. Do not introduce an equivalent environment-variable or
configuration override.

#### Affected commands and components

| Command/component | Required change |
| --- | --- |
| `az enclave launch` | Remove `--state-dir`; always create instance records, runtime endpoints, and logs under the fixed root. Preserve name validation and refusal to overwrite retained instances. |
| `az enclave destroy` | Remove `--state-dir`; resolve `--name` under the fixed root. Preserve evidence by default; `--cleanup` removes only the validated named instance after confirmed shutdown. |

#### Ownership, compatibility, and storage

Keep the root private and root-owned, validate paths without following symlinks,
and retain full process-identity checks. Retain the host-wide runtime lock:
a shared directory alone does not prevent concurrent launches. Cross-root
discovery and a host-wide owner record for locating custom roots are no longer
required; status can inspect the shared inventory to identify the blocker.

### 5. Proposed change: launch/destroy runtime cleanup

#### Purpose and boundary

Verify and close gaps in runtime teardown so owned processes or stale sockets
do not block later launches. This concerns releasing runtime resources, not
deleting retained evidence: proposal 6 separately defines `cleanup`,
`destroy --cleanup`, confirmation, and dry-run behavior.

#### Required behavior and validation

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
`--cleanup`, require confirmed shutdown before entering proposal 6's shared
retained-state removal path; dry-run must not stop processes or remove files.
Test concurrent lifecycle operations and partial failures so status distinguishes
confirmed shutdown from incomplete cleanup and never reports a retained
instance directory as a free name.

### 6. Proposed change: `az enclave cleanup` and `destroy --cleanup`

#### Purpose

Remove stopped enclaves' retained records, logs, and evidence to release their
names. Keep ordinary destroy's existing behavior: stop the runtime and remove
sockets, but retain diagnostic state.

#### Parameters

| Command/parameter | Description |
| --- | --- |
| `cleanup --name <name>` | Required name of the stopped instance to remove under the fixed root. |
| `destroy --cleanup` | New opt-in flag: confirm shutdown, then remove retained state through the same cleanup implementation. |
| `--dry-run` | On cleanup or `destroy --cleanup`, preview actions without stopping processes, changing records, or deleting files. |

#### Examples

```bash
# Preview removal of a stopped instance
sudo az enclave cleanup --name demo-1 --dry-run

# Remove retained state after shutdown
sudo az enclave cleanup --name demo-1

# Stop and remove in one operation
sudo az enclave destroy --name demo-1 --cleanup

# Preview both steps without performing either
sudo az enclave destroy --name demo-1 --cleanup --dry-run
```

#### Important behavior

Require deletion confirmation; obtain it before the combined destroy operation.
Reject standalone cleanup of live or unverifiable runtimes and direct live
instances to destroy first. Missing/corrupt records are not proof of shutdown.
Preserve the original IGVM and external inputs; delete only the validated
instance directory. Release the name only after removal succeeds.

Share validation, confirmation, and removal logic across both paths. Coordinate
with lifecycle locks through shutdown/removal, recheck full process identities,
and do not remove shared locks in ways that bypass coordination. Failed or
unconfirmed destroy skips deletion; partial removal reports remaining state
and allows retry rather than claiming success.

Dry-run shows targets, preserved external inputs, name-release effects, and
blockers. Combined preview makes deletion conditional on confirmed shutdown.
It does not reserve anything: execution must revalidate and confirm again.
Test cancellation, unsafe paths/ownership, concurrent commands, partial failures,
unchanged dry-run state, and successful name reuse only after removal.

## Diagnostics and inspection

### 7. Proposed change: `az enclave status`

#### Purpose

List retained enclave names and states, or inspect one enclave's configuration,
runtime observations, errors, and log locations. Help users identify occupied
names and the active runtime blocking another launch.

#### Parameters

| Parameter | Description |
| --- | --- |
| `--name`, `-n` | Optional. Omit for a compact name/state list; supply a name for detailed inspection. |
| `--output` | Existing Azure CLI option: table for readable summaries, JSON for structured results. |

#### Examples

```bash
# List names and states
sudo az enclave status --output table

# Inspect one enclave, including paths and errors
sudo az enclave status --name demo-1 --output json
```

#### Important behavior

Use the fixed root `/var/lib/enclave/instances`; no `--state-dir`. Include all
retained states, not just running/destroyed. Existing directories reserve names
until cleanup, and unexpected process exit is not successful destruction.

Named details include recorded status versus observed process liveness,
instance/image paths, process identities, persisted isolation configuration,
log/response locations, and recorded errors. Show UTC ISO 8601 `created_at` and
`status_changed_at`: creation time is immutable, and last-change time updates
only on recorded status transitions. Legacy timestamps are unavailable, not
inferred from file dates; collected evidence is not verified attestation.

Keep inspection read-only with existing Linux/root, path, and ownership checks.
Report missing names, unavailable logs, and corrupt/inconsistent records
explicitly. A missing root means an empty inventory without creating it;
unsafe/unreadable roots are errors. Report a busy host guard even if its owner
cannot be identified; an empty list does not prove the host is free.

Launch conflicts should suggest status to identify the occupied name or runtime,
then a new name, destroy, or explicit cleanup as appropriate. Status never
changes records, stops processes, or retains the runtime lease. Validate
list/detail output, legacy/stale records, timestamp transitions, and concurrent
lifecycle activity. Log contents are accessed through proposal 8.

### 8. Proposed change: `az enclave logs`

#### Purpose

Read or follow diagnostics already captured on L1, including from failed or
destroyed enclaves. This is local, read-only log access, not an interactive L2
console or a monitoring/health API.

#### Parameters

| Parameter | Description |
| --- | --- |
| `--name`, `-n` | Required instance name under the fixed root |
| `--source` | `serial` (default, `com1.log`), `runtime` (`openvmm.log`), or `supervisor` (`supervisor.log`). One source per invocation. |
| `--tail` | Recent line count; nonnegative integer, default 100. Zero skips history when following. |
| `--follow` | Stream appended output until interrupted or the instance/file is removed, replaced, or truncated. |

#### Examples

```bash
# Recent guest serial output
sudo az enclave logs --name demo-1

# Runtime diagnostics
sudo az enclave logs --name demo-1 --source runtime --tail 100

# Recent serial output followed by live updates
sudo az enclave logs --name demo-1 --source serial --tail 100 --follow
```

#### Important behavior

Serial includes only output routed to L2 COM1, not guest-file-only application
logs. Runtime/supervisor sources contain their respective stdout/stderr.
No healthy RPC connection or successful guest readiness is required.

Stream text to stdout, warnings/errors to stderr, without JSON wrapping or
unbounded buffering. Escape unsafe terminal controls and handle invalid text
predictably without altering raw files. Report missing/unreadable logs; empty
existing files are valid. Enforce private Linux/root access, regular-file/path
validation, and no symlink escapes.

Following must not block destroy/cleanup or hold the runtime lease. Surface
removal/replacement/truncation and end the stream; never switch to a new instance
that reuses the name. Ordinary EOF waits for more output. Errors should suggest
the relevant source rather than automatically dumping potentially sensitive
guest data.

Validate tail/follow, large/partial/non-text output, safe display, Ctrl+C,
unavailable RPC, and concurrent cleanup/name reuse through the CLI handler.
Monitoring/export, interactive input, archive collection, capture/rotation/
retention changes, structured events, and guest-debug-policy changes are
outside this CLI-only proposal.

## Image creation and build workspace

### 9. Proposed change: guest application path

#### Purpose

Change where create installs and executes the guest application without
replacing the default init's mounts and initialization.

#### Parameters

| Parameter | Description |
| --- | --- |
| `--application-path` | New absolute guest installation/execution path; default `/bin/enclave_app`. A path only, not a shell command or argument list. |
| `--application` | Existing host executable input; defaults to the packaged application. Independent of its guest path. |
| `--init-script` | Existing complete guest `/init` replacement. Mutually exclusive with an explicitly supplied `--application-path`. |

#### Examples

```bash
az enclave create \
    --application ./my-app \
    --application-path /opt/my-app \
    --output-file ./custom.igvm
```

For the default SNP profile, the generated init ends with:

```sh
exec /opt/my-app serve-vsock 4050
```

#### Important behavior

With neither init option, preserve the current default path/init. With
`--application-path`, install there and generate a matching build-local init,
retaining the selected isolation profile's startup arguments. With a custom
init, retain `/bin/enclave_app` placement and leave that script unchanged.
Reject both options together even when the explicit path equals the default.

The path option works with packaged or custom applications. Different arguments
or initialization still require a custom init; readiness compatibility remains
the customer's responsibility. Generate safely quoted executable paths, create
parent directories, and reject traversal, invalid/reserved destinations such
as `/init`, and symlink escapes. Preserve executable checks/permissions and
never modify source scripts in place.

Test default/custom inputs, matching installation/init paths, conflicts, safe
quoting, and path validation. Preserve deterministic composition; path or init
changes may change the image measurement, so use the rebuilt measurements.

### 10. Proposed change: optional build workspace

#### Parameters

| Parameter | Description |
| --- | --- |
| `--workspace` | Existing option becomes optional; default `$HOME/enclave-build` for the invoking user. An explicit path overrides the build parent. |

#### Examples

```bash
# Default build parent: $HOME/enclave-build
az enclave create --output-file ./custom.igvm

# Override the build parent
az enclave create --workspace /mnt/build/enclave --output-file ./custom.igvm
```

#### Important behavior

Resolve the home directory at invocation time. Create the parent if needed,
then a unique private subdirectory for dependencies, composed rootfs/initrd,
generator configuration/artifacts, and stage logs. This host build area is
separate from the guest path and fixed runtime state root in proposal 4.

Normally remove only the private subdirectory after success or failure; retain
it with `--keep-intermediates`. Never remove the parent, unrelated files, or
final output. Preserve input/output overlap checks and fail explicitly on
unavailable home/unusable workspace, suggesting an override rather than
silently switching roots. Test defaults/overrides, permissions, overlaps,
concurrent builds, retention, and safe cleanup.

## CLI compatibility

### 11. Proposed change: CLI core support floor

#### Purpose

Make the declared minimum CLI version reflect tested behavior instead of an
inherited metadata value. This prevents installations from appearing supported
when their CLI APIs or Python environment cannot actually run the extension.
The current `azext.minCliCoreVersion` value of `2.0.67` is a declaration, not a
validated support floor.

#### Required changes and validation

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

These are proposed changes and validation criteria, not claims that lifecycle
bugs have already been found or the new behaviors are implemented.
