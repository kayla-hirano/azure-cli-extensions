# Enclave extension improvements

This document tracks proposed improvements to the current extension, grouped by
related behavior and shared prerequisites. See [CURRENT_DESIGN.md](CURRENT_DESIGN.md)
for the current implementation and command reference; proposals below are not
implemented behavior.

The isolation profile registry, type-specific behavior, and TDX RPC work are
specified in [ISOLATION_TYPES_DESIGN.md](ISOLATION_TYPES_DESIGN.md). The resource
assembly and launch-profile proposals below use that shared design rather than
assuming every dependency or runtime setting is SNP-specific.

## Dependency delivery and image-bound launch configuration

### 1. Proposed change: external dependency retrieval and default migration

#### Purpose and ownership

Prepare CLI build-time retrieval from a placeholder external repository and
bundle its outputs while the producer's dependency layout is being confirmed.
Keep the currently provided dependency bundle as the packaged and create-time
default; placeholder outputs must not replace it.

The eventual input is expected to be prebuilt dependencies, not components
compiled by the CLI. Their exact shape, repository location, and release/version
contract remain unconfirmed. See
[ISOLATION_TYPES_DESIGN.md](ISOLATION_TYPES_DESIGN.md) for the shared isolation
and bundle-consumption design; confirm producer details before activating
external defaults.

#### Wheel construction and migration

Add a dedicated retrieval/assembly helper under `src/enclave/scripts` and invoke
it in the actual wheel-build phase through thin `setup.py` orchestration.
For now, make this a development-only, explicitly enabled build path using a
placeholder source and provisional output mapping, tested with controlled
fixtures. Retrieve the supplied outputs, stage them safely, and assemble a
candidate archive with indexed paths and content hashes. Keep it as a separate
build output, not the bundle selected by installed create commands.

Normal wheel builds continue packaging the currently provided dependency
bundle and application/init defaults, without contacting the placeholder source.
Do not download during metadata inspection, customer wheel installation, create,
or `az enclave setup`. Keep staging private and clean it without modifying
source inputs. An explicitly requested retrieval/assembly failure must fail that
build operation visibly, not be disguised as successful external integration.

Defer switching defaults until the producer's inputs and release/version
mechanism are confirmed and the resulting bundle passes compatibility and
end-to-end validation. Shared artifacts and profile-specific requirements remain
part of the CLI bundle design, not an assumed upstream publishing contract.

#### Customer overrides and validation

Preserve `--dependency-bundle`, `--application`, and `--init-script`, including
the application-path/custom-init rules in proposal 8. Explicit inputs override
their corresponding defaults independently. For the selected isolation type,
a customer dependency bundle must supply all required artifacts; it may omit
unselected profiles but must not silently borrow missing resources from the
packaged bundle. A custom bundle does not implicitly select a customer
application or init script.

Test placeholder retrieval and failures, indexed paths/content hashes, safe
staging, candidate assembly, and separation from the active packaged bundle.
Verify normal wheel builds make no placeholder requests and no-override create
still uses the currently provided bundle. Preserve legacy SNP/customer bundle
handling and independent application/init overrides. Confirm the upstream layout,
release/version mechanism, and any source metadata later; do not treat fixture
assembly as proof of production compatibility.

### 2. Proposed change: image-bound launch profile

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

#### Parameters and examples

| Parameter | Description |
| --- | --- |
| `launch --launch-profile <path>` | New optional path to the image-bound launch-profile JSON file on L1. Overrides adjacent-file discovery, not individual profile settings or image identity. |
| `launch --igvm <path>` | Existing required image path. Its final SHA-256 must match the selected profile. |

```bash
# Automatically discover ./custom.igvm.launch.json
sudo az enclave launch --name demo-1 --igvm ./custom.igvm

# Use a profile stored elsewhere or under a different filename
sudo az enclave launch --name demo-1 --igvm ./custom.igvm \
    --launch-profile ./profiles/custom-launch.json
```

#### Selection, compatibility, and validation

Create writes `<image-filename>.launch.json` adjacent to the image, for example
`custom.igvm.launch.json`, and publishes the pair consistently. If
`--launch-profile` is supplied, use only that file; otherwise discover the
adjacent file. An explicitly selected missing/unreadable profile or an invalid
selected/discovered profile is an error, not permission to try another profile
or use the legacy fallback.

#### Crash-safe publication

Preserve create's no-overwrite guarantee for both the image and adjacent profile.
Serialize publication for the destination pair, recheck both destinations before
publishing, and reject existing files or symlinks at either path. Stage and
validate both outputs on the destination filesystem, including the final image
hash after signing. Publish the profile first and make it durable before
publishing the image; never expose a newly published image without its profile
and thereby accidentally enable legacy fallback. Report success only after both
outputs are durable. Two separate file publications are not an atomic pair.

On failure, remove only staging files and published outputs demonstrably owned
by that operation, without overwriting or deleting another caller's files.
An interrupted operation may leave an orphan profile; report destination
collisions explicitly and document how to inspect and remove that orphan before
retrying. Never silently adopt an existing profile or image. Test failures and
interruption between publication steps, durability ordering, existing profile-only
and image-only destinations, concurrent publishers, rollback failure, and retry.

#### Validation guarantees and future work

The image hash detects pairing with different image bytes; it does not prove
that editable profile fields describe the image's measured configuration.
Current validation checks profile structure, supported values, compatibility,
and image hash, while relying on trusted profile production for correspondence
with generation settings. Do not describe those checks as independently proving
that CPU, memory, or device declarations match the image.

Future work must identify which configuration fields can be independently
validated against the IGVM and which require authenticated producer metadata
or another explicit trust contract. Define those guarantees and test edited
profile fields with an unchanged image hash before claiming stronger binding.
This is separate from publisher authentication and attestation verification.

#### Launch validation

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

Test explicit-path precedence, automatic discovery, missing/unreadable profiles,
legacy fallback, final-image hash mismatches,
malformed/unsupported schemas, type/configuration mismatches, exact conversions,
bus/MMIO ranges and overlaps, ECAM compatibility, and actual guest boot.
Use shared mapping with type-specific validation as required by the isolation
registry. Protocol/schema support alone is not evidence that a guest can boot.

## Instance state, shutdown, and cleanup

### 3. Proposed change: fixed instance location

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

### 4. Proposed change: launch/destroy runtime cleanup

#### Purpose and boundary

Verify and close gaps in runtime teardown so owned processes or stale sockets
do not block later launches. This concerns releasing runtime resources, not
deleting retained evidence: proposal 5 separately defines `cleanup`,
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
`--cleanup`, require confirmed shutdown before entering proposal 5's shared
retained-state removal path. Dry-run is available only on standalone cleanup
and must not stop processes or remove files.
Test concurrent lifecycle operations and partial failures so status distinguishes
confirmed shutdown from incomplete cleanup and never reports a retained
instance directory as a free name.

### 5. Proposed change: `az enclave cleanup` and `destroy --cleanup`

#### Purpose

Remove one or all confirmed-stopped enclaves' retained records, logs, and
evidence to release their names. Keep ordinary destroy's existing behavior:
stop the runtime and remove sockets, but retain diagnostic state.

#### Parameters

| Command/parameter | Description |
| --- | --- |
| `cleanup --name <name>` | Optional. Select one stopped instance under the fixed root; omit to select all instances confirmed not running. |
| `cleanup --dry-run` | Preview targets, skips, blockers, and suggested commands without changing records or deleting files.|
| `destroy --cleanup` | New opt-in flag: confirm shutdown, then remove retained state through the same cleanup implementation. |
| `cleanup --yes`, `destroy --cleanup --yes` | Approve the computed shutdown/deletion plan without prompting; never bypass validation or shutdown checks. |

#### Examples

```bash
# Preview removal of a stopped instance
sudo az enclave cleanup --name demo-1 --dry-run

# Remove retained state after shutdown
sudo az enclave cleanup --name demo-1

# Preview cleanup of all confirmed-stopped instances
sudo az enclave cleanup --dry-run

# Confirm and remove all confirmed-stopped instances
sudo az enclave cleanup

# Stop and remove in one operation
sudo az enclave destroy --name demo-1 --cleanup
```

#### Important behavior

Require confirmation listing the exact names, paths, and actions before any
deletion unless `--yes` explicitly approves the computed plan; obtain approval
before shutdown for the combined destroy operation. Without `--yes`,
noninteractive execution must fail before shutdown/deletion rather than hang
or implicitly approve. Without
`--name`, discover the fixed-root inventory and select only confirmed-stopped
instances. Show running instances as skipped and unverifiable instances as
blocked; never infer shutdown from a recorded state or missing/corrupt records.
Reject named cleanup of a live or unverifiable instance and direct live
instances to destroy first. With no eligible instances, report no action and
do not prompt. Preserve the original IGVM and external inputs; delete only
validated instance directories. Release each name only after removal succeeds.

Share validation, confirmation, and removal logic across both paths. Coordinate
with lifecycle locks through shutdown/removal, recheck full process identities,
and do not remove shared locks in ways that bypass coordination. Revalidate each
confirmed target before deletion; never add newly discovered or replaced
instances to the confirmed set. If a target is now running or unverifiable,
leave it untouched and report the changed condition. Failed or unconfirmed
destroy skips deletion; partial removal reports remaining state and allows
retry rather than claiming success. Bulk results distinguish removed, skipped,
blocked, and failed instances; blockers or failures are not overall success.

Return stable structured per-target results identifying the name, path, outcome,
and any skip/block/failure reason. Exit nonzero if any target is blocked or
failed, including partial success; running instances skipped by bulk cleanup
are not failures. Keep prompts and diagnostic messages out of structured stdout.

Dry-run shows targets, preserved external inputs, name-release effects, and
blockers without prompting. Suggest the matching cleanup command without
`--dry-run`, such as `sudo az enclave cleanup --name demo-1` or
`sudo az enclave cleanup`. For running instances, suggest
`sudo az enclave destroy --name <name> --cleanup` as a separate, explicitly
confirmed operation, not part of cleanup. A preview reserves nothing; execution
recomputes its targets and requires approval of the resulting plan, interactively
or through explicit `--yes`.
`destroy --cleanup` has no dry-run option: its confirmation describes both
shutdown and retained-state deletion before either action. `--yes` skips only
the prompt, not plan computation, target revalidation, or shutdown verification.

Test named/bulk selection, mixed running/stopped/unverifiable inventories,
empty results, confirmation/cancellation, suggested commands, unsafe paths,
ownership, concurrent state changes, partial failures, unchanged dry-run state,
successful name reuse only after removal, `--yes`, noninteractive rejection,
structured partial results, and exit status.

#### Administrator recovery for unverifiable state

Provide a recovery runbook for missing/corrupt records, interrupted initial
launch, host reboot, and partial retained-state deletion. Normal cleanup remains
blocked until shutdown can be established; a missing process record, stale
status, or free host guard alone is not sufficient proof. Do not add a force
flag that bypasses ownership or shutdown checks.

The runbook must explain how to preserve available diagnostic evidence, prevent
new lifecycle operations during recovery, and establish that no runtime can
still use the affected directory. Where process ownership cannot be established,
require a controlled host maintenance/reboot procedure that also prevents
automatic runtime restart, rather than signaling unverified PIDs. A reboot alone
does not validate paths or authorize deletion.

Only after shutdown and exclusive maintenance are established may an
administrator remove the exact validated, private, root-owned instance directory.
Preserve external images/inputs and shared coordination locks; do not fabricate
replacement ownership records or mark corrupt state destroyed to pass cleanup.
Document verification of removal and name reuse, and how to resume normal
operations. Test that unverifiable state stays blocked in ordinary cleanup and
that partial removal remains diagnosable and recoverable.

## Diagnostics and inspection

### 6. Proposed change: `az enclave status`

#### Purpose

List retained enclave names and states, or inspect one enclave's configuration,
runtime observations, and diagnostic locations. Help users identify occupied
names and the active runtime blocking another launch.

#### Parameters

| Parameter | Description |
| --- | --- |
| `--name`, `-n` | Optional. Omit for a compact name/state list; supply a name for detailed inspection. |
| `--output`, `-o` | Optional existing Azure CLI option. Respect explicit and configured output preferences; use `table` only when neither is set. |

#### Examples

```bash
# List names and states (table when no output preference is configured)
sudo az enclave status

# Inspect one enclave as a table
sudo az enclave status --name demo-1

# Request structured details explicitly
sudo az enclave status --name demo-1 --output json
```

Illustrative default list output:

```text
Name     State
-------  ---------
demo-1   running
demo-2   destroyed
demo-3   failed
```

Illustrative named output (selected fields):

```text
Field               Value
------------------  -------------------------------------------
Name                demo-1
Recorded state      running
Observed runtime    alive
Isolation type      snp
Instance directory  /var/lib/enclave/instances/demo-1
Image path          /opt/enclave/custom.igvm
Log directory       /var/lib/enclave/instances/demo-1
Created at          2026-11-04T20:00:00Z
Status changed at   2026-11-04T20:00:05Z
```

#### Important behavior

Use the fixed root `/var/lib/enclave/instances`; no `--state-dir`. Include all
retained states, not just running/destroyed. Existing directories reserve names
until cleanup, and unexpected process exit is not successful destruction.

Named details include recorded status versus observed process liveness,
instance/image paths, process identities, persisted isolation configuration,
and log/response locations. Use name/state rows for list tables and field/value
rows for named tables; retain structured detail fields for JSON output. Diagnostic contents belong
in proposal 7. This does not suppress errors encountered while inspecting state.
Keep structured field names and types stable and represent recorded status and
observed runtime separately. Use explicit unavailable/changing observations,
with reasons, rather than presenting missing evidence as healthy or stopped.
Show UTC ISO 8601 `created_at` and `status_changed_at`: creation time is
immutable, and last-change time updates
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
lifecycle activity. Log contents are accessed through proposal 7.

Inspection is lock-free and best-effort, not an atomic host-wide snapshot.
Do not create lock files or wait for lifecycle locks. Read atomically published
records and check for observable instance replacement or record changes while
collecting details; bound any retries. Report a detected change as changing or
unavailable rather than combining contradictory observations into a definitive
state. An undetected change remains possible after any observation; status is
not authorization for cleanup.

If an instance disappears during inventory inspection, report that observation
without crashing the whole listing or claiming successful shutdown. Distinguish
concurrent disappearance from unsafe paths, unreadable files, and corrupt
records, which remain explicit inspection errors. Never follow a replacement
instance that reuses the same name within the original observation. Test
creation, transitions, deletion, and name reuse during list/detail inspection,
as well as explicit/configured output precedence.

### 7. Proposed change: `az enclave diagnostics logs`

#### Purpose

Read or follow diagnostics already captured on L1, including from failed or
destroyed enclaves. This is local, read-only log access, not an interactive L2
console or a monitoring/health API. Introduce `diagnostics` as a command group
with a `logs` subcommand now, leaving room for future subcommands such as
`stack` without changing log access. Only `logs` is in scope here.

#### Parameters

| Parameter | Description |
| --- | --- |
| `--name`, `-n` | Required instance name under the fixed root |
| `--source` | Log filename: `com1.log` (default), `openvmm.log`, or `supervisor.log`. One supported filename per invocation, not an arbitrary path. |
| `--line-count` | Number of most recent lines to show; nonnegative integer, default 100. Zero skips history when following. |
| `--follow` | Stream appended output until interrupted or the instance/file is removed, replaced, or truncated. |

#### Examples

```bash
# Recent guest serial output
sudo az enclave diagnostics logs --name demo-1

# Runtime diagnostics
sudo az enclave diagnostics logs --name demo-1 --source openvmm.log --line-count 100

# Recent serial output followed by live updates
sudo az enclave diagnostics logs --name demo-1 --source com1.log --line-count 100 --follow
```

Illustrative terminal output:

```text
Instance: demo-1 | Log: /var/lib/enclave/instances/demo-1/com1.log
[guest serial output follows]
```

The first line identifies the instance and selected file on stderr; log content
streams to stdout. Show the label once after successfully opening the validated
file, including when it is empty or `--line-count 0 --follow` is used.

#### Important behavior

`com1.log` includes only output routed to L2 COM1, not guest-file-only
application logs. `openvmm.log` contains OpenVMM stdout/stderr;
`supervisor.log` contains supervisor stdout/stderr. No healthy RPC connection
or successful guest readiness is required.

Resolve `--source` only from the supported filename allowlist inside the named
instance directory. Reject other names, absolute paths, and traversal; do not
silently select a different file.

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

Validate filename selection/defaults, invalid paths, the source label and
stdout/stderr separation, line-count/follow, large/partial/non-text output,
safe display, Ctrl+C, unavailable RPC, and concurrent cleanup/name reuse through
the CLI handler.
Monitoring/export, interactive input, archive collection, capture/rotation/
retention changes, structured events, and guest-debug-policy changes are
outside this CLI-only proposal.

## Image creation and build workspace

### 8. Proposed change: guest application path

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

### 9. Proposed change: optional build workspace

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
separate from the guest path and fixed runtime state root in proposal 3.

Normally remove only the private subdirectory after success or failure; retain
it with `--keep-intermediates`. Never remove the parent, unrelated files, or
final output. Preserve input/output overlap checks and fail explicitly on
unavailable home/unusable workspace, suggesting an override rather than
silently switching roots. Test defaults/overrides, permissions, overlaps,
concurrent builds, retention, and safe cleanup.

## CLI compatibility

### 10. Proposed change: CLI core support floor

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
