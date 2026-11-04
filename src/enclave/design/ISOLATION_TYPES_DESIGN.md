# Enclave isolation type support

**Status:** Proposed, not implemented. This document describes the CLI changes
required to support multiple isolation types using the decisions below. It does not
claim that additional isolation types can run with today's packaged resources
or runtime. See [CURRENT_DESIGN.md](CURRENT_DESIGN.md) for current behavior and
[NEW_DESIGN.md](NEW_DESIGN.md) for the other proposed command improvements.

## 1. Objective and scope

Support multiple explicitly selected isolation types through shared create,
setup, launch, status, destroy, and cleanup workflows rather than treating every
guest as an SNP CVM. Introduce `--isolation-type` and an isolation profile
registry that selects the required dependencies and supported CLI behavior.

The external repository supplies **individual prebuilt artifacts**. CLI
wheel-build tooling retrieves them and assembles one combined dependency bundle
for inclusion in the extension. Create selects artifacts from that packaged
bundle or a customer-provided `--dependency-bundle`; it does not compile them.

Scope required implementation to the CLI repository: packaging and assembly,
bundle consumption, registry and command behavior, protocol/client changes,
state/profile contracts, documentation, and tests. External dependency builds,
guest application changes, firmware/hypervisor implementation, and OpenVMM
server implementation are prerequisites owned elsewhere, not work proposed
here. Defining the CLI's input contract does not implement its external producer.

Include TDX RPC schema/client support as required CLI work. Working TDX guest
launch additionally depends on a compatible server, generator, supplied
artifacts, hardware, and nested host environment.

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

A customer bundle may contain only the selected profile and its required
artifacts. It need not contain every type packaged in the extension. Reject
unknown types, unsupported configurations, missing required artifacts, or
isolation mismatches before generation. Never substitute another isolation
type or downgrade confidential execution.

Target SNP, non-confidential guests, and TDX, with final non-SNP CLI identifiers
documented in the implemented contract. Only advertise end-to-end support after
the corresponding path is validated; a defined registry entry or protobuf
message alone is insufficient.

### Isolation profile registry versus bundle manifest

| Component | Owned by | Responsibility |
| --- | --- | --- |
| Isolation profile registry | Reviewed CLI code | Identify supported types/configurations, required logical artifacts, configuration validation, generation/resource mapping, measurement/signing support, host requirements, RPC mapping, and readiness/evidence handling. |
| Source release manifest | External artifact producer; validated by CLI build tooling | Identify the pinned release, individual artifacts, integrity values, and declared compatibility/provenance needed for assembly. |
| Combined bundle manifest | CLI assembly helper; supplied by customers for overrides | Map profile artifact references to bundle-relative paths and hashes, preserving release identity and compatibility/provenance. Multiple profiles may reference shared artifacts. |

Keep the registry small: explicit profile definitions and shared helpers with
type-specific handlers where behavior genuinely differs. Do not duplicate the
entire lifecycle per type or introduce a dynamic plugin framework.

Bundle metadata selects only behavior already supported by the CLI. It must not
load arbitrary Python modules or supply unchecked host command templates.
Existing trust requirements for executing a bundled generator still apply;
artifact hashes detect mismatches but do not authenticate a publisher.

Initially each type can select one explicitly supported architecture/boot
configuration. Record those identities and compatibility requirements without
adding arbitrary architecture or boot-mode flags in this increment.

## 4. Dependency retrieval, assembly, and packaging

```text
External release
    individual prebuilt artifacts + release manifest
    |
    v
CLI wheel-build helper under src/enclave/scripts
    retrieve pinned artifacts -> validate hashes/compatibility
    -> stage resources -> assemble combined archive and bundle manifest
    |
    v
Extension wheel
    CLI code + isolation registry + combined resource bundle
    |
    v
create --isolation-type <type>
    select packaged or customer bundle -> validate indexed artifacts
    -> resolve selected profile -> compose/generate image
    -> optional supported signing -> image + image-bound launch profile
```

Use a dedicated build helper under `src/enclave/scripts`, invoked by the actual
wheel-build phase configured through `setup.py`. Keep `setup.py` as thin
packaging orchestration. Do not download during module import, metadata
inspection, customer wheel installation, or `az enclave setup`. Keep dependency
assembly separate from protocol regeneration.

The helper must:

- Select an explicit release identity, not an unrecorded moving latest release.
  Validate the source manifest and expected integrity values before packaging.
- Retrieve prebuilt kernel/initrd/boot components, generators, and IGVM
  configurations required by the supported profiles. Do not compile them.
  Retrieve compatible application/init defaults as individual resources where
  provided; keep their customer overrides independent of bundle selection.
- Stage in a private build directory, use deterministic archive paths/metadata,
  preserve executable permissions, and index shared artifacts once where
  possible. Record source identity and build provenance accurately.
- Validate the assembled archive and fail on missing, corrupt, incompatible,
  or duplicate/conflicting artifacts. Never publish a partial wheel or disguise
  retrieval failure by using unrelated stale local resources.
- Clean temporary staging and keep credentials out of archives, metadata, and
  logs. Make release-selection and network errors actionable.

Version the bundle contract rather than reinterpreting schema-1 SNP metadata.
Preserve legacy SNP bundle consumption through an explicit compatibility path.
Required artifacts become profile-specific; not every profile requires
`snp_bootshim`. Paths remain manifest-indexed rather than hard-coded filenames.
Preserve path-containment, safe extraction, and integrity validation for all
manifest-listed artifacts; only the required-artifact set varies by profile.

Building from the same pinned inputs should produce the same combined bundle.
Define a verified local artifact-cache path for offline/repeatable builds if
needed; it must use the same release and integrity checks, not a success-shaped
fallback. The exact upstream release layout and assembly schema remain to be
specified with the artifact producer.

## 5. Required CLI and command changes

| Area | Required change |
| --- | --- |
| Argument handling | Add `--isolation-type` to create and setup, defaulting to SNP. Validate against implemented profiles and document per-type restrictions. |
| Create validation | Resolve the profile and bundle configuration, validate required artifacts and their compatibility, and reject mismatched effective IGVM isolation settings. Use profile-specific generator resource mappings rather than unconditional SNP resource names. |
| Image composition | Reuse initrd composition for compatible configurations. Preserve the proposed application-path/custom-init rules and optional build workspace from `NEW_DESIGN.md`; reject unsupported combinations rather than ignoring options. |
| Default application/init | Select compatible packaged defaults where supplied. Preserve explicit `--application` and `--init-script` precedence; overriding a bundle does not implicitly select a customer application. Custom workloads must still satisfy the supported guest/readiness contract. |
| Debug settings | Apply guest-debug behavior only through the supported profile. Global `--debug` remains usable for CLI diagnostics; do not copy SNP debug fields into another type's configuration or silently enable unrelated guest features. |
| Measurements | Parse generator outputs by type and expose explicitly typed results. Represent absence of a confidential measurement explicitly for configurations that do not produce one; do not fabricate SNP-shaped results. |
| Signing | Retain `--signing-key` as SNP ID-block signing unless another mechanism is separately designed. Reject it for unsupported types. Signing failure must not publish an unsigned fallback. |
| Setup | Check selected-type host/backend/runtime requirements and verify/install a compatible host runtime within setup's existing bounded responsibilities. Do not assume `/dev/mshv` existence proves support for every type, change kernels, reboot, or fetch image dependencies here. |
| Launch | Read the type and supported configuration from the image-bound launch profile; validate compatibility and map to explicit RPC settings. Do not require repeating a potentially contradictory type selection for profiled images. |
| Worker/supervisor | Keep process ownership, serial collection, logs, locks, and child reaping shared. Pass only validated configuration needed by the runtime; do not move CLI validation into arbitrary bundle-driven worker behavior. |
| Readiness/evidence | Separate application readiness from type-specific evidence collection. Non-confidential guests must not need SNP evidence; other confidential profiles use their own supported formats. Keep evidence collection distinct from cryptographic verification. |
| Instance state/status | Persist isolation/configuration identity and readiness/evidence outcome. Include the type in named status details while preserving the agreed compact default list. Keep timestamps and process-liveness checks independent of isolation. |
| Destroy | Preserve shared RPC shutdown and owned-process recovery. Use persisted validated configuration only where shutdown needs type-specific handling; do not infer a stopped instance's type from current packaged defaults. |
| Cleanup | Remain isolation-independent and reuse the proposed confirmation, dry-run, and shutdown checks. Remove only retained named-instance state, preserving external images and inputs. |

The fixed runtime state root and single-runtime host guard proposed in
`NEW_DESIGN.md` remain unchanged. The optional create workspace is separate from
runtime state. Do not add a per-type state root or permit concurrent types as
an incidental effect of this work.

### Image-bound launch profile and backward compatibility

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

## 6. TDX RPC schema and runtime integration

Treat TDX protocol support as an explicit dependency of TDX launch:

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
| External support unavailable | CLI changes cannot supply missing nested TDX capabilities, guest components, or server implementation. Gate claims of support on verified external prerequisites and actual target-host boot tests. |
| Isolation/configuration drift | Requested type, bundle profile, effective IGVM JSON, launch profile, and instance state must agree. Validate each boundary and never downgrade isolation to make a launch succeed. |
| Readiness coupling | The current sample requires SNP evidence. Compatible supplied sample resources and a type-aware CLI contract are prerequisites; changing dependency lookup alone is insufficient. |
| Larger wheels | Packaging every profile can increase download, installation, and disk usage. Measure wheel size and host architecture/tool compatibility; deduplicate shared artifacts without obscuring ownership or hashes. |
| Build network and trust | Private release access, moving assets, and corrupt downloads can undermine reliable builds. Pin releases, verify artifacts, keep credentials out of outputs, and fail before wheel publication. |
| Compatibility migration | New bundle/profile/state schemas and runtime upgrades can break existing SNP usage. Maintain explicit legacy paths and regression coverage rather than changing old field meanings. |
| Signing/debug ambiguity | SNP-specific options do not automatically generalize. Validate support explicitly while preserving CLI logging and avoiding accidental guest security-policy changes. |
| Reproducibility | Resource retrieval and per-profile generation introduce new input/version boundaries. Record them and compare controlled repeated builds; characterize signed outputs separately. |

## 8. Suggested implementation sequence and acceptance criteria

1. **Agree on contracts and prerequisites.** Specify supported type/configuration
   combinations, registry entries, release artifact contract, bundle schema,
   launch profile, measurement results, and readiness/evidence behavior.
   Confirm TDX generator/server/host availability; flag missing external inputs.
2. **Implement resource assembly.** Add the wheel-build helper and thin
   packaging integration. Test pinned individual-artifact retrieval, missing/
   corrupt resources, safe staging, deterministic assembly, and wheel contents.
   Verify metadata inspection and customer installation do not trigger downloads.
3. **Introduce the registry while preserving SNP.** Route existing SNP create,
   setup, launch, measurements, and readiness through shared/profile-specific
   helpers. Existing defaults, overrides, and legacy bundles must still work.
4. **Generalize create and image identity.** Add isolation selection,
   profile-specific dependencies/options, launch-profile export, typed results,
   and compatibility validation. Test partial customer bundles containing one
   complete profile and ensure missing inputs never pull from packaged defaults.
5. **Add non-SNP runtime paths, including TDX RPC work.** Update the authoritative
   protocol pin/bindings and runtime mapping together, implement preflight and
   readiness/evidence handling, and persist the selected identity for inspection
   and teardown. Keep unsupported combinations explicit.
6. **Validate integrated behavior.** Test each supported type's default/custom
   resources, malformed/mismatched profiles, unsupported signing, debug handling,
   old records, missing host capabilities, failures/timeouts, status, destroy,
   cleanup, and retained evidence. Verify protocol checks and existing linters/
   targeted tests using repository-documented commands.
7. **Gate release on real execution.** On compatible hosts, verify image
   creation, VM boot, readiness, correct evidence labeling, shutdown, and
   cleanup for each advertised configuration. Record the tested hardware,
   generator, resources, runtime, and CLI versions. Mocks and schema checks
   alone do not establish nested isolation support.

Before implementation, resolve the upstream release location/manifest format,
exact non-SNP identifiers and boot-component requirements, compatible sample
resources, authoritative TDX protocol/runtime version, and legacy image-profile
production guidance. These are integration decisions, not assumed capabilities
of today's extension.
