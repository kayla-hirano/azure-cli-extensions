# Enclave isolation type support

**Status:** Proposed, not implemented. The current increment abstracts the CLI
so shared workflows no longer assume SNP; SNP remains the only enabled isolation
profile. New isolation backends and TDX RPC integration are future work.
See [CURRENT_DESIGN.md](CURRENT_DESIGN.md) for current behavior and
[NEW_DESIGN.md](NEW_DESIGN.md) for the other proposed command improvements.

## 1. Objective and scope

Abstract create, setup, launch, inspection, and teardown workflows behind a
CLI-owned isolation profile registry. Move SNP-specific dependency requirements,
image-generation settings, measurement/signing, host checks, RPC mapping, and
readiness/evidence handling into an explicit SNP profile rather than assuming
they apply to every guest. Keep shared lifecycle and ownership logic common.

Preserve today's SNP behavior using the default bundle already in the repository
and compatible customer overrides. Introduce `--isolation-type` with an SNP
default, but enable only the implemented SNP profile in this increment. Define
extension points for other types without advertising them as runnable or
returning fabricated results from placeholder handlers.

The intended external source provides prebuilt dependencies, but its exact
output layout and release/version mechanism are still being confirmed. For now,
prepare retrieval from a placeholder repository and candidate bundle assembly
as development-only build tooling. Continue packaging and using the currently
provided dependency bundle; do not activate placeholder outputs as defaults.
Create selects the active packaged bundle or a customer-provided
`--dependency-bundle`; it does not fetch or compile dependencies.

Current implementation is CLI-only: registry/interfaces, dispatch through the
existing SNP behavior, legacy bundle adaptation, placeholder retrieval/assembly,
documentation, and regression tests. Preserve current RPC bindings, runtime
pins, bundle schema, and resource defaults; no new protocol or backend is
required to complete this refactor.

TDX RPC schema/client changes, other isolation implementations, and external
default activation are future work. Their later prerequisites include compatible
artifacts, generator, server, hardware, and nested host environment. External
dependency builds, guest changes, and firmware/hypervisor/OpenVMM server
implementation remain owned elsewhere.

### Prerequisites and related work from NEW_DESIGN.md

The following references use proposal numbers in
[NEW_DESIGN.md](NEW_DESIGN.md). They distinguish required integration contracts
from improvements that do not block the current abstraction work. A proposal
being referenced here does not add its implementation to this increment.

| Proposal | Relationship to isolation support |
| --- | --- |
| 1. External dependency retrieval and default migration | Add placeholder retrieval/assembly after the shared interfaces are established. Keep the repository's provided bundle active; producer details do not block abstraction. Confirmation and validation gate future external-default activation. |
| 2. Image-bound launch profile | Required before enabling new isolation types, not before refactoring SNP. Can ship separately for SNP first; later carry image hash, configuration/resources, and validated identity from create to launch/state. |
| 3. Fixed instance location | Prerequisite for the proposed shared inventory, named diagnostics, and retained-state cleanup, not for extracting isolation handlers. Preserve current path behavior until that proposal ships; never introduce per-type roots or replace the host-wide lock. |
| 4. Launch/destroy runtime cleanup | Preserve and regression-test existing owned-process, socket, lock, and evidence handling during refactoring. Broader teardown improvements can ship separately; verified teardown is required before enabling another backend. |
| 5. Cleanup and destroy cleanup flag | Separate feature requiring proposal 3's fixed root and proposal 4's confirmed-shutdown contract. Keep removal isolation-independent when integrated; do not require a new cleanup command to finish abstraction. |
| 6. Status | Separate inspection feature. When integrated, expose persisted isolation/configuration identity and distinguish recorded state from process observations; shared-inventory behavior depends on proposal 3. |
| 7. Diagnostics logs | Separate troubleshooting feature. Keep filename-based log access isolation-independent when integrated; not a prerequisite for registry work. |
| 8. Guest application path | Preserve existing application/init behavior now and the proposed installation/conflict rules when this independently deliverable option is integrated. |
| 9. Optional build workspace | Preserve private staging and input/output separation. Making the workspace optional can ship independently and is not a registry prerequisite. |
| 10. CLI core support floor | Validate refactoring against supported CLI/Python/dependency combinations. A revised minimum version is separate work; do not change it merely to introduce the registry. |

## 2. Current constraints

| Area | SNP-specific behavior today |
| --- | --- |
| Bundle validation | Requires schema 1, `measured-pcie-v1`, x86-64, and the same artifact identifiers for every build, including `snp_bootshim`. |
| Image generation | Requires an SNP Linux-direct configuration, applies SNP guest-debug settings, reads SNP measurements, and optionally signs an SNP ID block. |
| Runtime request | `_rpc.py` unconditionally selects SNP isolation and Linux-direct IGVM boot with fixed resources/devices. |
| Readiness | `_lifecycle.py` requires the sample exchange and an SNP-shaped report; it does not verify attestation. |
| Protocol | The checked-in RPC schema represents SNP and non-confidential isolation, but not TDX isolation. |
| Packaging | `setup.py` packages existing resource files. Individual-artifact retrieval and combined-bundle assembly are not implemented. |

The current dependency triple is an SNP-specific grouping, not a universal
artifact requirement. Another isolation type may require different boot
components or configuration. Isolation type, architecture, and boot mode must
remain separate concepts; adding a type does not promise every combination.

## 3. Proposed selection and registry

### CLI selection

```bash
# Default remains SNP; use packaged dependencies
az enclave create --output-file ./image.igvm

# Explicit type and customer bundle
az enclave create --isolation-type snp \
    --dependency-bundle ./custom-dependencies.tar.gz \
    --output-file ./image.igvm
```

Default `--isolation-type` to `snp` for compatibility. Retain the existing
archive-or-directory `--dependency-bundle` override. An override replaces the
packaged dependency source, not individual missing artifacts: never silently
mix incomplete customer inputs with packaged defaults.

Only `snp` is selectable in this increment. Reject unknown, disabled, or placeholder types
before dependency lookup, generation, setup changes, or RPC calls. Keep the
existing schema-1 SNP bundle contract; move its required-artifact and
configuration checks into the SNP implementation without weakening them.

Future bundles may contain only a selected profile and its required artifacts,
but their generalized schema is not needed for this refactor. Future targets
include non-confidential guests and TDX; their identifiers and complete
implementations remain deferred. Never substitute SNP for a requested but
unsupported type or infer support from protobuf availability.

### Isolation profile registry versus bundle manifest

| Component | Owned by | Responsibility |
| --- | --- | --- |
| Isolation profile registry | Reviewed CLI code | Identify supported types/configurations, required logical artifacts, configuration validation, generation/resource mapping, measurement/signing support, host requirements, RPC mapping, and readiness/evidence handling. |
| Existing dependency-bundle manifest | Repository bundle or compatible customer override | Preserve current indexed paths, hashes, and schema-1 SNP validation behind a bundle adapter. Do not require a manifest migration for abstraction. |
| Candidate bundle index | Development-only CLI assembly helper | Index provisional output paths and hashes without changing active defaults. Generalized per-profile metadata and upstream output contracts remain future integration work. |

Keep the registry small: explicit profile definitions and shared helpers with
type-specific handlers where behavior genuinely differs. Do not duplicate the
entire lifecycle per type or introduce a dynamic plugin framework. Shared
orchestration must dispatch through these interfaces, not retain scattered
SNP-specific branches. SNP requirements belong in its profile/handlers; shared
code may still explicitly select SNP as the compatibility default.

Bundle metadata selects only behavior already supported by the CLI. It must not
load arbitrary Python modules or supply unchecked host command templates.
Existing trust requirements for executing a bundled generator still apply;
artifact hashes detect mismatches but do not authenticate a publisher.

For now, describe the existing SNP architecture/boot configuration explicitly
in its profile. Do not add arbitrary architecture/boot-mode flags, speculative
non-SNP requirements, or fake successful implementations for future types.

## 4. Dependency retrieval, assembly, and packaging

**Interim scope:** implement retrieval from a placeholder repository and bundle
its outputs using a provisional adapter/fixture layout. The actual dependency
shape and release/version mechanism are unconfirmed. Do not prescribe a release
manifest, version scheme, or release-selection policy yet; confirm these with
the producer before production integration.

```text
Placeholder repository / controlled fixtures (development build only)
    provisional prebuilt outputs
    -> CLI retrieval/assembly helper
    -> separate candidate bundle (not an active default)

Currently provided dependency bundle
    -> normal extension wheel (unchanged resource defaults)
    -> installed CLI
create --isolation-type snp
    select provided packaged or customer bundle -> validate required artifacts
    -> resolve selected profile -> compose/generate image
    -> existing SNP measurement/signing -> image

Image-bound launch-profile export/selection: separate NEW_DESIGN work
```

Use a dedicated build helper under `src/enclave/scripts`, invoked by the actual
wheel-build phase configured through `setup.py`. Keep `setup.py` as thin
packaging orchestration. Enable placeholder retrieval only through an explicit
development build option; normal wheel builds must not contact the placeholder
source. Do not download during module import, metadata inspection, customer
wheel installation, create, or `az enclave setup`. Keep dependency assembly
separate from protocol regeneration.

The helper must:

- Retrieve prebuilt outputs through a placeholder source adapter and map them
  into a candidate combined bundle. Use controlled fixtures to exercise the
  intended dependency categories without claiming the layout matches the
  unconfirmed producer contract. Do not compile dependencies.
- Stage in a private build directory, use deterministic archive paths/metadata,
  preserve executable permissions, and index shared artifacts once where
  possible. Index bundle-relative paths and content hashes; these do not
  establish an upstream release or authenticate its producer.
- Validate the candidate archive against the provisional mapping and fail an
  explicitly requested retrieval/assembly operation on missing, corrupt, or
  conflicting outputs. Do not silently substitute the provided bundle to make
  that operation appear successful.
- Keep candidate outputs separate from the active provided bundle. Verify
  ordinary wheel contents and no-override create remain unchanged.
- Clean temporary staging and keep credentials out of archives, metadata, and
  logs. Make source-access and assembly errors actionable.

Keep schema-1 SNP bundle consumption unchanged behind the adapter. The
`snp_bootshim` requirement belongs to the SNP profile, not the shared resolver.
Preserve manifest-indexed paths, containment, safe extraction, and integrity
validation. Version any future generalized bundle contract separately rather
than reinterpreting current metadata or requiring speculative producer fields.

Confirm the producer's actual outputs, repository/access details, release/version
mechanism, and any integrity/compatibility metadata later. Only after that
agreement and validation should candidate assembly be adapted for production
and replace the active defaults. Placeholder packaging does not make additional
isolation types runnable; each still needs compatible supplied dependencies.

## 5. Required CLI abstraction changes

| Area | Required change |
| --- | --- |
| Argument handling | Add `--isolation-type` to create and setup, defaulting to SNP. Allow only the enabled SNP profile; reject disabled/placeholder types before side effects. |
| Create validation | Resolve the SNP profile and legacy bundle adapter. Move SNP artifact/configuration checks and generator mappings into that profile while preserving current validation/results. |
| Image composition | Keep compatible rootfs/initrd composition shared; isolate type-specific generation inputs behind profile handlers. Preserve current application/init and workspace behavior; integrate NEW_DESIGN options separately. |
| Default application/init | Resolve defaults through the profile using today's provided resources. Preserve explicit `--application` and `--init-script` precedence; overriding a bundle must not implicitly replace either. |
| Debug settings | Move SNP guest-debug mapping into its handler. Preserve global `--debug` for CLI diagnostics; do not treat it as a generic guest security policy. |
| Measurements | Isolate SNP measurement parsing behind a typed result interface while preserving existing command output. Leave other formats unimplemented, not fabricated as SNP results. |
| Signing | Move existing SNP ID-block signing and capability checks behind the profile. Preserve `--signing-key` behavior and fail explicitly on unsupported operations or signing errors; never publish an unsigned fallback. |
| Setup | Move SNP-specific host/backend/runtime checks into its profile while retaining shared setup orchestration and bounded installation behavior. Do not change kernels, reboot, or fetch image dependencies. |
| Launch | Route the current SNP configuration and RPC request mapping through the SNP handler without changing its values or RPC schema. Image-bound profile selection is a separate integration; once available, dispatch using its validated identity rather than current defaults. |
| Worker/supervisor | Keep process ownership, serial collection, logs, locks, and child reaping shared. Pass only validated configuration needed by the runtime; do not move CLI validation into arbitrary bundle-driven worker behavior. |
| Readiness/evidence | Separate shared readiness orchestration from the SNP exchange/report handler while preserving today's readiness requirements. Define extension points for future evidence formats; collection is not cryptographic verification. |
| Instance state/status | Keep ownership and liveness handling shared. Record resolved type/configuration identity for new records without invalidating existing SNP records; preserve existing fields and command output. Integrate the proposed status command separately. |
| Destroy | Preserve shared RPC shutdown and owned-process recovery. Resolve new records through their persisted identity; treat valid legacy records explicitly as SNP, not as whichever profile later becomes the default. Reject corrupt/unknown identities. |
| Cleanup | Keep retained-state removal independent of guest isolation. If/when NEW_DESIGN cleanup is integrated, reuse its confirmation, dry-run, and shutdown checks rather than creating a per-type implementation. |

Preserve current state-path behavior and the single-runtime host guard during
refactoring. When NEW_DESIGN's fixed-root and optional-workspace changes ship,
reuse their contracts; do not require them to land first. Never introduce a
per-type state root or permit concurrent runtimes as an incidental effect.

### Image-bound launch profile and backward compatibility

This is a separate delivery/integration contract, not an acceptance requirement
for the current abstraction increment. Keep today's create/launch behavior
until that proposal is implemented. Before any new isolation type is enabled,
integrate the contract below.

The profile contract can ship independently for today's SNP create/launch
workflow before registry generalization or TDX integration. Multi-isolation
support then reuses that contract with supported type-specific validation and
RPC mapping. See [NEW_DESIGN.md](NEW_DESIGN.md) for the detailed parameter and
selection rules: launch's optional `--launch-profile <path>` selects an explicit
profile; otherwise launch discovers `<image-filename>.launch.json` beside the
image. An explicit selection takes precedence and must not silently fall back.

Create must export a versioned profile containing isolation identity,
architecture, boot/configuration identity, supported resource/device settings,
and SHA-256 of the final image after any signing. Derive it from the exact
effective configuration used to generate that image, then publish the pair
consistently. Launch must reject malformed, unsupported, or mismatched profiles;
hash binding is not publisher authentication or attestation verification.

Retain the documented legacy SNP launch path with a visible warning for images
without profiles. It must not become an automatic choice of a new type.
Require an image-bound profile for new isolation types, including customer-built
images; document the supported profile-production contract. Never trust a
filename or guess isolation from absent metadata. Older instance records need
an explicit legacy-SNP interpretation where supported, with unknown/corrupt
state reported rather than silently relabeled.

## 6. Future work: TDX RPC schema and runtime integration

TDX integration is out of scope for the current abstraction increment. Keep
the current schema, generated bindings, protocol pins, and SNP runtime mapping
unchanged; extracting that mapping into an SNP handler is sufficient now.
The interfaces must permit a later TDX implementation without assuming SNP
request fields or evidence formats in shared code.

When TDX work is scheduled, treat protocol support as an explicit dependency
of TDX launch:

1. Obtain the authoritative TDX-capable OpenVMM service contract and establish
   the runtime version/capabilities that implement it. Do not invent protobuf
   fields or isolation settings that the server does not understand.
2. Update the schema commit/checksum, generator pins where needed, and runtime
   compatibility metadata in
   [`scripts/openvmm-protocol.json`](../scripts/openvmm-protocol.json). Keep these
   consistent with runtime verification in
   [`azext_enclave/_runtime.py`](../azext_enclave/_runtime.py).
3. Use [`scripts/generate_protocol.py`](../scripts/generate_protocol.py) to retrieve
   the pinned schema and regenerate messages/type stubs. Preserve existing
   protocol checks and generated-file conventions; do not hand-edit bindings.
4. Add TDX request mapping, type-specific configuration validation, and
   preflight compatibility checks. Fail explicitly when the selected host or
   server cannot support TDX, with no SNP/non-confidential fallback.
5. Test protocol generation/checking, request serialization, profile dispatch,
   server compatibility errors, and SNP regression behavior. Verify the actual
   TDX server/guest interaction on a compatible target before declaring support.

The existing protocol-generation workflow ties its runtime version to the
verified package. Prefer one verified runtime supporting the required profiles.
If that is not possible, design an explicit per-profile runtime compatibility
policy; do not merely relax package checks to accept unverified runtimes.

## 7. Potential issues and constraints

| Issue | Impact and handling |
| --- | --- |
| Abstraction regression | Moving SNP logic must preserve image composition, measurements/signing, request serialization, readiness, and teardown. Test shared dispatch as well as the concrete SNP handlers. |
| External support unavailable | Missing TDX/server/artifact capabilities do not block abstraction. They do block enabling those future backends; do not advertise placeholder types as supported. |
| Isolation/configuration drift | Resolve an explicit SNP identity now and persist it for new records. Validate future image/profile/state boundaries when integrated; never downgrade an unsupported selection. |
| Readiness coupling | Keep the sample's SNP exchange/evidence rules in the SNP handler, not shared orchestration. Future workloads/types require their own compatible implementation and resources. |
| Speculative interfaces | Define boundaries around actual SNP responsibilities rather than guessing future dependency layouts or TDX protocol fields. Avoid unused plugin systems and duplicated lifecycles. |
| Unconfirmed producer contract | Placeholder retrieval is not production integration. Confirm dependency layout, access, release/version handling, and integrity metadata before replacing provided defaults. Keep credentials out of outputs and fail explicitly requested retrieval/assembly operations on errors. |
| Compatibility migration | Preserve the current bundle schema, runtime pins, output, and legacy state interpretation. Keep future profile/schema migrations separate and regression-tested. |
| Signing/debug ambiguity | SNP-specific options do not automatically generalize. Validate support explicitly while preserving CLI logging and avoiding accidental guest security-policy changes. |
| Reproducibility | Compare candidate assembly using identical fixture inputs. Production input/version tracking remains pending producer agreement; characterize signed outputs separately. |

## 8. Suggested implementation sequence and acceptance criteria

1. **Abstract shared workflows first, preserving SNP.** Identify the existing
   SNP responsibilities and define the smallest registry/handler interfaces
   needed for them. Route create, setup, launch, inspection/state handling,
   measurements/signing, readiness, and teardown through shared orchestration
   plus the concrete SNP implementation. Use the repository's current default
   bundle and compatible overrides. Preserve existing behavior; do not wait
   for external artifact details, launch-profile delivery, or TDX schema work.
2. **Add explicit selection and compatibility handling.** Wire
   `--isolation-type` into create/setup with default `snp` and only SNP enabled.
   Record resolved identity for new instances and interpret valid legacy SNP
   records explicitly. Test rejection of unknown/placeholder selections before
   side effects and preservation of the existing bundle/CLI output contracts.
3. **Prepare placeholder retrieval and assembly.** Add the development-only
   wheel-build helper and thin packaging integration. Test fixture retrieval,
   missing/corrupt outputs, safe staging, and candidate assembly. Verify normal
   wheel builds retain the provided bundle without contacting the placeholder
   source, and installed commands never fetch dependencies.
4. **Validate the abstraction increment.** Test shared dispatch, SNP handlers,
   default/customer bundles, independent application/init overrides, debug
   mapping, measurements/signing, old/new records, host checks, failure paths,
   process ownership, teardown, and retained evidence. Verify existing protocol
   checks and repository-documented linters/targeted tests. Use test doubles to
   exercise handler boundaries, not to claim real non-SNP support.
5. **Verify SNP end to end before declaring the refactor complete.** On a
   supported SNP host, confirm image creation, VM boot, readiness/evidence,
   shutdown, and existing cleanup behavior using unchanged default resources
   and runtime. Record the tested environment; mocked dispatch alone does not
   prove preservation of the current working path.

Current completion means shared workflows no longer encode SNP-specific guest
behavior, the enabled SNP profile preserves that behavior, unsupported types
fail explicitly, and placeholder assembly cannot replace active defaults.
It does not require TDX/non-confidential boot, a new bundle schema, external
release integration, or delivery of the separate NEW_DESIGN features.

Before activating external defaults, confirm the upstream dependency layout,
source/access details, release/version mechanism, and integrity/compatibility
metadata. These decisions do not block placeholder retrieval/assembly work.
In a future increment, before advertising new isolation support, resolve exact
non-SNP identifiers and boot-component requirements, compatible sample resources,
the authoritative TDX protocol/runtime version, the required NEW_DESIGN contracts above, and
customer image-profile production guidance. These are integration decisions,
not assumed capabilities of today's extension.
