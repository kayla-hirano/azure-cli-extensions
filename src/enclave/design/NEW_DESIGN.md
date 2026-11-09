# Enclave extension improvements

| # | Proposed change | Status |
| --- | --- | --- |
| 1 | External dependency retrieval and default migration | Not started |
| 2 | Create-time configuration with automatic launch settings | Not started |
| 3 | Fixed instance location | Not started |
| 4 | Launch/destroy runtime cleanup | Not started |
| 5 | Destroy with automatic cleanup and optional log retention | Not started |
| 6 | `az enclave status`, including IGVM measurement | Not started |
| 7 | `az enclave diagnostics logs` listing and viewing | Not started |
| 8 | Guest application command line (`--app-cmd-line`) | Not started |
| 9 | Optional build workspace and intermediate-file visibility | Not started |
| 10 | CLI core support floor | Not started |

## Dependency delivery and parameterized launch configuration

### 1. Proposed change: external dependency retrieval and default migration

#### Purpose and ownership

Defer retrieval/assembly and default migration until we get confirmation on how
prebuilt dependencies will be delivered: layout, repository/access, versions,
and compatibility/integrity metadata. The CLI will not compile dependencies.
Keep the current packaged bundle and create defaults meanwhile.

#### Wheel construction and migration

Provisional direction after agreement: a helper under `src/enclave/scripts`,
called during wheel construction through thin `setup.py` orchestration,
assembles a candidate bundle with indexed paths/hashes in private staging.
Preserve source inputs and surface retrieval/assembly failures; do not silently
substitute the old bundle. Switch defaults only after compatibility and
end-to-end validation, not fixture-only assembly.

Until migration, normal builds package existing dependencies/application/init
without external requests. Never download during metadata inspection, customer
installation, create, or setup. Shared/profile-specific bundle requirements are
CLI contracts, not assumed producer publishing requirements.

#### Build-time OpenVMM protocol generation

Retrieve `vmservice.proto` during every wheel build from the
immutable OpenVMM commit associated with the release tag pinned in
`scripts/openvmm-protocol.json`, alongside dependency/application retrieval.
Use a thin `setup.py` build hook and `scripts/generate_protocol.py` to verify
the schema checksum and runtime pin, then generate Python messages/type stubs
into private build staging with the pinned compiler.

Keep compiler dependencies build-only and source files unchanged. Fail on
download, validation, or generation errors without falling back to checked-in
bindings. Never fetch schemas or generate bindings during metadata inspection,
customer installation, or CLI commands. Retain manual regeneration for editable
development and runtime compatibility checks; test retrieval failures,
generated wheel contents, and unchanged source files.

#### Customer overrides and validation

Preserve independent `--dependency-bundle`, `--application`, and `--init-script`
overrides and proposal 8's conflict rules. A customer bundle must contain all
artifacts for the selected isolation type; it may omit other profiles but cannot
borrow missing artifacts from packaged defaults or implicitly replace app/init.

Validate retrieval failures, hashes/paths, safe staging, candidate separation,
legacy SNP/customer inputs, unchanged default create, and no external requests
from ordinary builds before migration.

### 2. Proposed change: create-time configuration with automatic launch settings

#### Purpose and ownership

Set CPU count, guest memory, and isolation while creating the IGVM file. The same settings will be used during launch.

Require explicit `--isolation-type` for create and setup; there is no CLI
default. Reject omission before side effects. Launch reads image identity
instead, retaining the legacy SNP compatibility rules below.

Package one versioned isolation manifest JSON per type with the extension,
sharing defaults and supported settings between create and launch. This is
extension metadata, not a per-image file or executable plugin.
Store selected values in supported IGVM fields or agreed embedded metadata.
Launch validates them, configures the runtime, and saves effective settings in
instance state; it does not resize or rewrite the image.

#### Property classification and validation

Today `_rpc.py` fixes SNP/Linux-direct boot, one CPU, 160 MiB, and PCIe/vsock
layout; create overrides bundle debug policy, not CPU/memory. Audit generator
fields, limits, and measured-versus-runtime semantics before adding parameters.

| Property | Proposed classification | Configuration and constraints |
| --- | --- | --- |
| Isolation type (`Snp`) | Fixed by isolation selection | `--isolation-type snp` selects the registry mapping and packaged SNP manifest; only SNP is currently enabled. Never reinterpret an image as another type. |
| Architecture/boot personality | Fixed by the supported architecture/boot combination | The SNP manifest declares the currently supported x86-64/Linux-direct combination; this is not a universal SNP requirement. Reject unsupported combinations. |
| `processor_count` | User build preference | Positive integer within validated limits; verify exact-count versus range semantics. |
| Guest memory | User build preference | Verify generator support, conversions, limits, alignment, and address-map conflicts. |
| PCIe buses/segments, MMIO, ECAM, device topology | Fixed by the supported image/device layout | The SNP manifest declares the currently supported layout. Keep the image's measured layout and launch RPC mapping consistent; no raw topology flags or assumption that all SNP images share this layout. |
| `disable_vmbus`, `disable_hv`, COM1/vsock wiring | CLI-owned runtime configuration | Preserve required devices/settings; separate measured declarations from generated host socket paths. |
| Guest debug policy/signing | Build-time security choices | Preserve behavior; no launch override or silent image regeneration. |
| Application/init/command line | User build inputs | Apply existing overrides and proposal 8; rebuild and remeasure rather than substitute at launch. |
| Name, host paths, sockets, logs, ownership | Per-run host state | Generate through trusted CLI logic; no image-embedded instance paths or bundle-defined host commands. |

Verify classifications, CPU/memory support, and measured-versus-runtime limits
with dependency/runtime owners. Validate isolation manifest schemas and keep
defaults in that single source.

#### Parameters and examples

| Parameter | Description |
| --- | --- |
| `create --processor-count <count>` | Optional CPU count; current SNP default 1. Apply before measurement/signing. |
| `create --memory-mb <MiB>` | Optional guest memory in MiB; current SNP default 160. |
| `create --isolation-type <type>` | Required manifest selection; only `snp` is enabled. Reject missing or unsupported selections before side effects. |
| `setup --isolation-type <type>` | Required isolation selection for host checks, with no default; only `snp` is enabled. |
| `launch --igvm <path>` | Required image path; read saved settings automatically, without resource/isolation overrides or companion JSON. |

```bash
# Omitted create resources use the supported SNP defaults
az enclave create --isolation-type snp --output-file ./default.igvm
sudo az enclave launch --name demo-1 --igvm ./default.igvm

# Illustrative non-default resources, once validated as supported
az enclave create --isolation-type snp --processor-count 2 --memory-mb 256 \
    --output-file ./custom.igvm
sudo az enclave launch --name demo-2 --igvm ./custom.igvm
```

#### Configuration flow and open validation questions

Create resolves defaults/overrides once in a private image-generation
configuration. Embed effective settings before measurement and optional signing
as required by the format. Preserve source inputs and no-overwrite publication;
normal output remains the final image path and measurements (proposal 9).

Launch reads embedded settings, validates resource/device/host compatibility,
and maps them to RPC. Do not infer settings from filenames, workspaces, or local
caches. Missing fields use packaged isolation manifest defaults; warn with every
substituted name/value, isolation type, and manifest version. For example:

```text
IGVM settings missing; using packaged snp manifest defaults (version <version>):
processor_count=1, memory_mib=160
```

Warn only for fields actually defaulted. Missing isolation identity uses the
SNP compatibility default, adding `isolation_type=snp` to the warning.
Missing fields do not cause rejection; malformed, unsupported, or contradictory
values still fail validation. Defaults do not prove the image's intended
configuration; surface runtime/boot failures.

**Technical prerequisite:** identify IGVM fields that preserve selected values,
not runtime-input locations or bounds. The
[IGVM parameter specification](https://docs.rs/igvm_defs/latest/igvm_defs/struct.IGVM_VHS_PARAMETER.html)
defines loader-supplied information, not necessarily stored CPU/memory choices.
If insufficient, agree versioned embedded metadata and a validated reader with
generator/runtime owners. Do not append arbitrary data or assume current tool
support. Defaults cannot recover lost custom values.

Save identity, effective settings, image/default provenance, and final image
hash for status. A hash does not prove configuration, publisher authenticity,
or attestation.

Return the persisted `run_id` with the name and effective settings in launch's
structured result, using proposal 5's lifecycle identity. Expose it in readable
launch output as well, so callers can record it before destroy.

Validate required isolation selection and rejection before side effects,
resource defaults/overrides, limits/conversions, device-layout compatibility,
embedded-configuration round trips, generation/RPC agreement, malformed or
unsupported metadata, partial/all missing settings and exact fallback warnings,
shared manifest defaults/schema/version validation, legacy/customer images,
signing/publication failures, and actual non-default guest boot. RPC schema
support alone does not prove boot compatibility.

## Instance state, shutdown, and cleanup

### 3. Proposed change: fixed instance location

Use `/var/lib/enclave/instances` as the sole, non-overridable instance root.
Instances use `<root>/<name>` with host-wide unique names and a shared inventory
of active, failed, and incompletely destroyed instances. No equivalent
environment-variable or configuration override.

#### Affected commands and components

| Command/component | Required change |
| --- | --- |
| `az enclave launch` | Remove `--state-dir`; create records, endpoints, and active logs under the fixed root. Preserve name validation and no-overwrite checks. |
| `az enclave destroy` | Remove `--state-dir`; resolve `--name` under the fixed root. Proposal 5 defines archival/removal. |

#### Ownership, compatibility, and storage

Keep root-owned/private directories, no-symlink path validation, full process
identities, and the host-wide runtime lock; a shared root alone cannot prevent
concurrent launches. Custom-root discovery/owner records are unnecessary.
Proposal 5's separate log archives do not reserve names.

### 4. Proposed change: launch/destroy runtime cleanup

#### Purpose and boundary

Release owned processes/sockets so later launches are not blocked. Proposal 5
owns post-shutdown archival/removal; no standalone cleanup command or
`destroy --cleanup` flag.

#### Required behavior and validation

Share supervision/ownership checks across types. Prefer RPC shutdown, confirm
exit, and retain bounded owned-process recovery; never signal reused/unowned
PIDs or force shutdown after an unconfirmed destroy RPC failure. Reap children,
remove only known sockets safely, and hold the host guard until its owners exit.

Unconfirmed shutdown preserves state/logs for retry. Once runtime/log writers
exit, finalize through proposal 5, including failed launches. Surface cleanup
errors and retain diagnostic state/timestamps/identity until finalization;
status must distinguish stopped from removal-complete, and existing directories
still reserve names.

Validate successful/failed/timed-out launch, readiness failure, process exits,
interrupted/repeated destroy, ownership/socket/lock handling, and concurrent or
partially failed lifecycle operations.

### 5. Proposed change: destroy with automatic cleanup and retained logs

#### Purpose and command behavior

`az enclave destroy --name <name>` stops the enclave, archives logs by default,
and removes instance data to release its name. Historical logs remain separate
from managed inventory; preserve external IGVMs/inputs and shared locks.

| Parameter | Description |
| --- | --- |
| `--no-logs` | Optional, default false. Discard this run's unarchived logs instead of archiving them; retain all shutdown/ownership/path checks. |

```bash
# Stop the enclave, archive logs, and remove instance data
sudo az enclave destroy --name demo-1

# List archived log locations using the run ID returned by destroy
sudo az enclave diagnostics logs --name demo-1 --run-id <run-id>

# Stop and remove instance data without retaining this run's logs
sudo az enclave destroy --name demo-1 --no-logs
```

Document `--no-logs` log loss. Successful removal in either mode also deletes
instance configuration, measurements, process records, and saved guest
response/evidence; none of these are archived. `--no-logs` creates no
archive or leftover staging and does not erase committed archives, even from a
partial destroy of this run; report those explicitly. It is not secure erasure
or a purge of backups, external collectors, or Azure CLI diagnostics.

#### Archive location, contents, and retention

Use `/var/log/enclave/<name>/<run-id>/` for completed-run diagnostics, separate
from operational state under `/var/lib`. Archive only the three log sources
in proposal 7: `com1.log`, `openvmm.log`, and `supervisor.log`, when available.
The archive path identifies the enclave name and run ID; do not add an archive
manifest or state snapshots. Exclude `instance.json`, `runtime.json`,
`worker-error.json`, `response.json`, sockets, locks, and external images/inputs.
Archived logs are historical diagnostics, not live ownership or verified
attestation.

Assign/persist a unique immutable `run_id` when creating instance state at
launch, before starting runtime processes; it identifies one lifecycle, not
the reusable enclave name or image. Return it from launch, status, and destroy,
and use it in the archive directory path. Lifecycle recovery and destroy
retries reuse it; a new launch after removal gets a new ID even with the same
name and image. Report it on launch failure when instance state was persisted,
so the retained failed run can be inspected. Never overwrite/adopt another run's
archive. Keep active logs in instance directories until writers exit.

Archives remain root-owned/private with regular-file/no-symlink validation:
logs may contain secrets. Keep them until explicit administrator removal, never
automatic expiration, pruning on destroy/name reuse, or eviction under space
pressure. Administrators own removal, capacity monitoring, and backups/export
using host tools; no new extension features. Report archival failures and
preserve recoverable state. Local storage cannot guarantee indefinite availability.

#### Shutdown, archival, and removal ordering

1. Hold the instance lifecycle lock, validate paths/ownership/full process
   identities, and confirm shutdown of runtime and log writers. Recorded status
   or missing/corrupt process records alone are not proof.
2. Unless `--no-logs`, collect only the three allowed log sources.
   Report lost/unreadable logs; record legitimate
   absence from failed launch stages without blocking removal.
3. If retaining logs, copy to private staging on the archive filesystem, validate
   completeness/integrity, and durably publish without overwrite. `/var/lib`
   and `/var/log` may differ; do not assume an atomic cross-root rename.
4. After archive commit or explicit discard, revalidate/remove only the named
   instance directory under lifecycle coordination. Release its name only after
   removal; never follow a concurrent replacement.
5. Return structured name, run ID, log disposition, nullable archive path, and
   removal outcome. Account for prior archives; success requires all required
   archival/removal to complete. Send warnings/errors to stderr.

#### Failure handling, retries, and validation

Unconfirmed shutdown blocks both modes. Archive failure retains original logs/
state and reserves the name; removal failure retains the committed archive and
recovery state. Report partial outcomes nonzero with actionable paths. Discard
mode does not require writable archive storage. Release runtime leases once
their owners exit; name reservation is separate.

Persist run ID, archive progress, and retention/discard policy until removal
completes. Retry stopped instances without a live RPC endpoint, recognize owned
archive commits, and distinguish intentional discard from corruption. After
archival failure, an explicit `--no-logs` retry may opt out before deletion;
never discard implicitly. After removal, repeated destroy reports absence
without altering history or claiming a process was stopped.

Validate both modes after normal/failed launch, complete archives and legitimate
absence, excluded inputs, writer shutdown, retries at commit/removal boundaries,
concurrent lifecycle/log access, unsafe paths/collisions, cross-filesystem
copying, disk/permission errors, and partial-result exit codes. Verify run ID
stability across recovery/destroy retries, a new ID on name reuse, and consistent
IDs in launch/status/destroy output and archive paths. Verify archives contain
only allowed logs, and successful removal deletes operational state and guest
evidence in both modes. Verify external
inputs/prior archives remain intact and age/name reuse never triggers pruning.

## Diagnostics and inspection

### 6. Proposed change: `az enclave status`

#### Purpose

Inspect current operational state. List the running
instance and any retained failed or incompletely cleaned-up instances, or
inspect one instance's configuration, runtime, measurement, and diagnostic
paths. Identify occupied names and runtime blockers. Only one instance can run
at a time, but failed or incomplete cleanup can leave multiple retained records.

#### Parameters

| Parameter | Description |
| --- | --- |
| `--name`, `-n` | Optional. Omit for a compact retained-instance list; supply a name for detailed inspection of that retained instance.|
| `--output`, `-o` | Optional existing Azure CLI option. Respect explicit and configured output preferences; use `table` only when neither is set. |

#### Examples

```bash
# List retained instances and observed liveness (table by default)
sudo az enclave status

# Inspect one enclave as a table
sudo az enclave status --name demo-1

# Request structured details explicitly
sudo az enclave status --name demo-1 --output json
```

Illustrative default list output:

```text
Name     Run ID        Recorded state   Observed runtime
-------  ------------  ---------------  ----------------
demo-1   <run-id-1>    running          alive
demo-2   <run-id-2>    removal-pending  exited
demo-3   <run-id-3>    failed           exited
```

Illustrative named output (selected fields):

```text
Field               Value
------------------  -------------------------------------------
Name                demo-1
Run ID              <run-id-1>
Recorded state      running
Observed runtime    alive
Isolation type      snp
Instance directory  /var/lib/enclave/instances/demo-1
Image path          /opt/enclave/custom.igvm
SNP measurement     <96 hexadecimal characters, when available>
Log directory       /var/lib/enclave/instances/demo-1
Created at          2026-11-04T20:00:00Z
Status changed at   2026-11-04T20:00:05Z
```

#### Important behavior

Use proposal 3's fixed root. Include all retained states, including pending
archival/removal; unexpected exit is not successful destruction. Cleanup is
automatic as part of destroy/finalization, not a promise that a running enclave
automatically shuts down. Keep operational records until shutdown and required
archival/removal complete, so failures can be inspected and retried.

Named details show recorded status separately from observed liveness, process
identities, isolation/configuration, image/instance/log/response paths, and
measurement. Keep JSON fields/types stable; list tables use name/run ID/recorded
state/observed liveness and
named tables field/value. Missing/changing evidence has an explicit reason,
not a healthy/stopped default. UTC ISO 8601 `created_at` is immutable;
`status_changed_at` changes only on status transitions. Legacy timestamps are
unavailable, not inferred from file dates.

#### IGVM measurement in named status

Use create's `measurements` mapping (`{"snp": "<96-hex-character digest>"}`)
in named status, not the compact list. This expected launch measurement is not
the whole-file SHA-256 or verified/live attestation.

Today create reads `series[0].reference.snp_ld` from temporary `image-snp.json`;
launch saves only the image path/hash. Propose a shared isolation-specific
reader/calculator at launch, persisting measurement, source, and image hash in
the existing `instance.json` while the instance is retained. Status reads saved
values without the workspace, generator execution, or trusting the current
source path.

**Technical prerequisite:** first check whether the bundled generator embeds
the same SNP measurement that create returns; launch may be able to extract it
rather than recalculate it.

1. Generate signed and unsigned images with the bundled generator, including
   supported CPU/memory variants, and retain create's measurement for comparison.
2. Inspect the final images' embedded metadata. Upstream `igvmfilegen` documents
   embedded CoRIM documents and `dump-corim` extraction; verify availability in
   the bundled version and whether the metadata contains the exact `snp_ld`
   value returned by create.
3. Compare extracted measurements with create's output across those variants.
   If compatible, use a validated reader/extractor at launch and save the
   measurement and its source in the existing
   `/var/lib/enclave/instances/<name>/instance.json`. Treat extracted metadata
   as a declared expected measurement, not independently verified attestation.
4. If extraction is unavailable or insufficient, investigate an owner-supported
   measurement calculator. Saving create's measurement in a companion JSON is
   a simpler alternative, but requires explicitly revising the no-sidecar
   design: the file must travel with the image and be checked against the final
   IGVM's recorded hash. A local cache alone does not support copied images or
   launch on another host. Do not introduce a required sidecar implicitly.

Unsupported calculation must not block an otherwise valid launch: warn, persist
the unavailable reason, and display it in named status. Surface malformed images,
corrupt state, and actual calculation errors; never substitute a file hash or
unverified guest report. Source-image replacement must not change saved values.

Validate create/status parity, resource/signing variants, workspace cleanup,
missing/replaced images, warning-and-continue for unsupported calculation,
persisted unavailable reasons versus actual errors, and JSON/table output.

#### Read-only inspection and concurrency

Keep Linux/root/path/ownership checks and report missing names/logs or corrupt/
unsafe/unreadable state explicitly. A missing root is empty, without creation;
a busy host guard must be reported even without an identifiable owner.
Launch conflicts suggest status, then a new name or destroy.

Inspection is read-only, lock-free, and best-effort: no record changes, process
control, lock creation/waits, or runtime lease. Read atomic records, detect
observable changes/replacement with bounded retries, and report changing/
unavailable observations rather than combine inconsistent data. Disappearance
must not crash a listing or imply shutdown; never follow a same-name replacement.
Status is not an atomic snapshot or authorization for deletion.

Validate list/detail and output precedence, stale/corrupt records, timestamps,
busy guards, and concurrent creation/transitions/removal/name reuse.

### 7. Proposed change: `az enclave diagnostics logs`

#### Purpose

List, read, or follow local L1 logs from active, failed, or destroyed instances.
Add `diagnostics logs`, leaving room for future diagnostics subcommands.

#### Parameters

| Parameter | Description |
| --- | --- |
| `--name`, `-n` | Required current instance name or archived instance name. |
| `--run-id` | Immutable ID selecting an archived run under `/var/log/enclave/<name>/`, not an arbitrary path. Optional when selecting the current instance or a sole archive; required to read a specific archive when multiple archives exist and no current instance is selected, or to access an archive instead of a same-name current instance. |
| `--source` | Optional log filename: `com1.log`, `openvmm.log`, or `supervisor.log`. No default file. Omit to list locations and availability without reading contents. |
| `--line-count` | With `--source`, number of most recent lines to show; nonnegative integer, default 100. Zero skips history when following. Reject an explicitly supplied value without `--source`. |
| `--follow` | With `--source`, stream appended output until interrupted or the instance/file is removed, replaced, or truncated. Reject without `--source`. |

#### Examples

```bash
# List log paths and availability; do not dump a default log
sudo az enclave diagnostics logs --name demo-1

# Recent guest serial output
sudo az enclave diagnostics logs --name demo-1 --source com1.log

# Recent serial output followed by live updates
sudo az enclave diagnostics logs --name demo-1 --source com1.log --line-count 100 --follow

# Read a completed run after destroy, even if its name has been reused
sudo az enclave diagnostics logs --name demo-1 --run-id <run-id> --source openvmm.log

# With no current instance and two archived runs named demo-1, list both runs
sudo az enclave diagnostics logs --name demo-1

# Select one of those archived runs to read its serial log
sudo az enclave diagnostics logs --name demo-1 --run-id <selected-run-id> --source com1.log
```

Illustrative listing output without `--source`:

```text
Run       Source          Availability  Path
--------  --------------  ------------  --------------------------------------------------
<run-id>  com1.log        available     /var/lib/enclave/instances/demo-1/com1.log
<run-id>  openvmm.log     available     /var/lib/enclave/instances/demo-1/openvmm.log
<run-id>  supervisor.log  available     /var/lib/enclave/instances/demo-1/supervisor.log
```

Listing returns structured source/path/availability/run-ID records with normal
CLI formatting, never file contents. Advise on stderr to use
`--source <available-file>`, including `--run-id` for archived runs.
Show missing logs explicitly; empty files are available, unsafe/unreadable files
are errors. Report known non-retention or an absent run without inventing
discard history.

Illustrative content output with `--source com1.log`:

```text
Instance: demo-1 | Run: <run-id> | Log: /var/lib/enclave/instances/demo-1/com1.log
[guest serial output follows]
```

Print the instance/run/path label once on stderr after opening the validated
file, even if empty or `--line-count 0 --follow`; contents stream to stdout.

#### Important behavior

`com1.log` captures L2 COM1 only, not guest-file-only logs; the other sources
capture OpenVMM/supervisor stdout/stderr. Access requires neither RPC nor guest
readiness. Proposal 5 owns archival/retention.

Every new instance has a run ID; optional `--run-id` means callers can omit the
selector when the rules below identify a run unambiguously, not that runs lack
IDs. Only one current instance may occupy a name.

| Selection | Without `--source` | With `--source` |
| --- | --- | --- |
| No `--run-id`; current instance exists, with or without same-name archives | List current-run logs only. | Read the current run's selected log. |
| No `--run-id`; no current instance and exactly one archive | List that archive's logs. | Read that archive's selected log. |
| No `--run-id`; no current instance and multiple archives | List all archived runs' logs with their run IDs. | Reject ambiguous selection and require `--run-id`; never choose the newest archive automatically. |
| Explicit `--run-id` | List only that archive's logs, even if the name has been reused. | Read only that archive's selected log, even if the name has been reused. |

Reading one log requires `--source`; in the multiple-archive case without a
current instance, it also requires `--run-id`. Missing files/runs are errors,
never permission to fall back to another run or archive.

Validate Linux/root/private ownership, regular files, name/run-ID components,
and source allowlist; reject traversal, arbitrary paths, and symlinks. Stream
content without JSON wrapping or unbounded buffering, escaping unsafe terminal
controls/invalid text predictably without changing raw files. Send diagnostics
to stderr; suggest sources rather than dump potentially sensitive content.

Follow holds no runtime lease and cannot block destroy. Active EOF waits;
removal/replacement/truncation ends the stream with a notice, never switching
instances or to an archive. Archived `--follow` prints requested history,
reports completion, and exits.

Validate no-content listing/guidance, explicit sources, option conflicts,
selection/ambiguity, missing/unsafe files, line-count/follow/Ctrl+C, large/partial/
non-text output, stdout/stderr separation, unavailable RPC, and concurrent
destroy/name reuse with post-removal archive access.

## Image creation and build workspace

### 8. Proposed change: guest application command line

#### Purpose

Use `--app-cmd-line` to customize everything after the default init's final
`exec`, preserving mounts/initialization and application installation location.
This replaces the former `--application-path` proposal.

#### Parameters

| Parameter | Description |
| --- | --- |
| `--app-cmd-line` | Optional guest-shell command after `exec `. Default SNP command: `/bin/enclave_app serve-vsock 4050`. |
| `--application` | Existing host executable input, still installed at `/bin/enclave_app`; defaults to the packaged application. Independent of the command-line override. |
| `--init-script` | Existing complete guest `/init` replacement. Mutually exclusive with an explicitly supplied `--app-cmd-line`, even if it equals the default command. |

#### Examples

```bash
az enclave create \
    --isolation-type snp \
    --application ./my-app \
    --app-cmd-line '/bin/enclave_app serve-vsock 4050 2>&1' \
    --output-file ./custom.igvm
```

For SNP with the default init, the generated init ends with:

```sh
exec /bin/enclave_app serve-vsock 4050 2>&1
```

#### Important behavior

Preserve the default init when omitted. Otherwise create a build-local copy,
replacing only text after the final application `exec`; do not append defaults,
alter initialization/installation, or modify source scripts. Custom init bytes
remain unchanged.

The value is trusted guest `/bin/sh` source, not a host command or one quoted
executable name. Preserve quoting, arguments, expansions, and redirections;
quote the whole option on the host so expansion occurs in the guest. Never
evaluate it on the host. Reject blank values, NUL, CR/newlines, and invalid
generated shell syntax without execution. Avoid logging full command lines;
discourage secrets in arguments.

Accept one executable command with shell quoting, expansions, and redirections;
the CLI adds `exec`. Top-level pipelines, command lists, backgrounding, and
compound control flow require `--init-script` for explicit PID 1/process handling.
**Technical prerequisite:** choose syntax validation tooling enforcing this
policy without host execution.
Referenced executables/files must already exist in the guest; the option does
not install them. Preserve application ELF/permission checks and vsock readiness;
incompatible commands/ports cannot silently bypass probing.

Validate defaults, full replacement, quoting/expansion/redirection, rejection of
pipelines/control flow, no host execution, init conflicts, unchanged sources/
initialization, deterministic composition, and boot/readiness. Return rebuilt
measurements after init changes.

### 9. Proposed change: optional build workspace

Create needs a private workspace to extract dependencies, assemble the guest
filesystem, and generate/sign the IGVM; after success or failure, the CLI deletes
its private build directory and intermediates, preserving original inputs, the
workspace parent, and any published final IGVM, and reports cleanup failures.

#### Parameters

| Parameter | Description |
| --- | --- |
| `--workspace` | Becomes optional. Default: a private temporary directory using Python's temp-location policy, normally `/tmp` on Linux and honoring `TMPDIR`. Explicit paths select the build parent. |

Remove `--keep-intermediates`; build intermediates are CLI-managed, not
customer-facing outputs. An explicit workspace changes location, not retention.

#### Examples

```bash
# CLI-managed temporary build directory; only final output is published
az enclave create --isolation-type snp --output-file ./custom.igvm

# Override the build parent
az enclave create --isolation-type snp --workspace /mnt/build/enclave --output-file ./custom.igvm
```

#### Current workspace inventory

Today the required workspace holds a private `igvm-build-*` directory from
`tempfile.mkdtemp`, containing:

| Contents | Why saved / visibility implications |
| --- | --- |
| `dependencies/` | Extracted archive dependencies/executable generator; directory bundles are read in place. |
| `base.cpio`, `rootfs/` | Unpacked initrd/guest filesystem with application and init. |
| `archive-files`, `application.cpio`, `application-initrd.cpio.gz` | Deterministic listing/repacked guest initrd. |
| `igvm-manifest.json`, `resources.json` | Generation settings/host paths; proposal 8 adds build-local init. |
| `image.igvm`, `image-snp.json`, SNP ID-block payload | Generated image, expected measurement, signing inputs. |
| `id-public-key.pem`, `id-signature.der`, `image-signed.igvm` | Optional signing outputs; the private key is never copied into workspace/image. |
| Stage `*.log` files | Tool diagnostics; some stdout goes to artifacts. May expose inputs/paths. |

Tools use this directory as `TMPDIR`. Publication also needs `.igvm-*.tmp` beside
the output, on its filesystem, for no-overwrite publication. Only the final
image is published; create returns its path/measurements.

#### Build storage and visibility

Keep intermediates out of normal project/home folders and output, not hidden
from their owner/root. Mode 0700 protects against other unprivileged users;
dot-prefixes are cosmetic, and deletion is not secure erasure.

Create a unique private directory under the selected temporary or explicit
parent. A `.enclave-build-` prefix can reduce clutter. Validate capacity and
execution support: temp storage may be small, memory-backed, `noexec`, or pruned,
while extraction expands inputs and runs tools. Suggest `--workspace` on failure;
never silently change roots/permissions. Memory-only/unlinked storage cannot
generally replace the named files required by build tools.

Build-stage logs remain separate from runtime archives. Include concise
stage-failure diagnostics in errors before deleting intermediates; do not dump
sensitive inputs. Always report cleanup failures and the residual directory
on stderr rather than silently leaving files behind.

Normal create stdout contains only the final image path/measurements. Show
stage progress with `--verbose` and intermediate paths with `--debug`; errors
and cleanup-failure paths remain visible regardless of logging level. Neither
logging option retains intermediates.

#### Important behavior and validation

Create the parent if needed; remove only the private child, never parent,
unrelated files, or final output. Preserve overlap checks, location-independent
image/measurement results, and cleanup on success/failure. Crashes can leave
files; stale-workspace cleanup must not delete active builds.

Validate defaults/overrides, permissions/capacity/execution, concurrent builds,
success/failure cleanup without retention, cleanup-error disclosure, removed
parameter rejection, normal/verbose/debug output separation, and unchanged
results.

## CLI compatibility

### 10. Proposed change: CLI core support floor

#### Purpose

Replace the inherited `azext.minCliCoreVersion` declaration (`2.0.67`) with the
oldest verified CLI/Python combination, not metadata-only compatibility.

#### Required changes and validation

Inventory command-loading, argument, output, error, and runtime dependencies.
On supported Linux hosts, test candidate versions for wheel install/discovery,
help, and setup/create/launch/destroy, including new options, archival/removal,
status, and diagnostics as implemented.

Record tested combinations/rationale in the [README](../README.rst), keeping
the floor aligned with isolation/protocol dependencies. An unset maximum does
not guarantee future compatibility.
