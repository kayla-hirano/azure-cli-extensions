# Enclave extension improvements

This document tracks proposed improvements to the current extension. The first
new focus is making existing enclave instances and their logs discoverable
before users launch another enclave. See [CURRENT_DESIGN.md](CURRENT_DESIGN.md)
for the current implementation and command reference; proposals below are not
implemented behavior.

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

## 4. Existing fixes and follow-up work

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
