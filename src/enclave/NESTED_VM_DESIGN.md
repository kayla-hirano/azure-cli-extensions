# Enclave nested VM type support

**Status:** Proposed, not implemented. This is a separate expansion of the
scope in [DESIGN.md](DESIGN.md), not a claim that other nested VM types work
today. The extension and command group are named `enclave`, and the Python
package is `azext_enclave`. Runtime state uses `/var/lib/enclave/instances`
and the host guard uses `/run/enclave/enclave.lock`. Upgrading requires stopping
old runtimes and retiring old installations; state is not automatically migrated.

## 1. Current design support

The supported composition and launch path is an x86-64 SNP confidential guest
using IGVM Linux-direct boot and a measured PCIe layout.

```text
External dependency build
    -> bundle: L2 kernel + base initrd + SNP boot shim
               + igvmfilegen + IGVM JSON + manifest/provenance
    -> extension wheel packages the bundle and sample defaults
    -> create: validate -> compose initrd -> generate IGVM
               -> read SNP measurement -> optionally sign SNP ID block
    -> launch: configure SNP VM -> resume -> require sample SNP response
    -> destroy: clean up owned runtime processes and sockets
```

| Boundary | Current support and constraints |
| --- | --- |
| Dependency builder | The external `build-igvm-dependencies.sh` builds pinned sources using Rust/Cargo and Docker and produces the complete bundle. It does not accept arbitrary prebuilt artifact paths. Its local-source options require clean checkouts at the pinned revisions. The script is outside this checkout; its precise JSON generation/copying logic must be inspected before changing it. |
| Bundle contract | `_image_builder.py` requires schema 1, `measured-pcie-v1`, `x86_64`, and the artifact identifiers `kernel`, `base_initrd`, `snp_bootshim`, `igvmfilegen`, and `igvm_manifest`. |
| IGVM JSON discovery | `dependency-manifest.v1.json` maps `artifacts.igvm_manifest` to a bundle-relative path and SHA-256. The packaged path is `manifests/snp-linux-direct.json`; customer bundles may use another path/name within the bundle. |
| Image creation | Validates one x64 measured-PCIe guest with `snp_linux_direct` and SNP isolation settings. Installs a prebuilt static x86-64 ELF and init script into the base initrd. Reads an SNP-specific measurement and optionally signs/embeds an SNP ID block. |
| Host setup | Automatic setup supports Azure Linux 3.0 L1, checks `/dev/mshv`, and verifies or installs the pinned OpenVMM package. These checks do not prove successful nested SNP boot or attestation. |
| Runtime configuration | `create_request()` in [`azext_enclave/_rpc.py`](azext_enclave/_rpc.py) selects SNP isolation and Linux-direct IGVM boot, with one CPU, 160 MiB, fixed PCIe/MMIO settings, COM1, and vsock. |
| Guest readiness | `_lifecycle.py` requires the sample exchange on port 4050 and an SNP-shaped 1184-byte report. It does not cryptographically verify attestation. |
| Lifecycle | Supervised OpenVMM creation/resume and teardown, owned process/socket cleanup, and retained logs/evidence. One active managed runtime per host. |

The checked-in [RPC schema](azext_enclave/rpc/vmservice.proto) represents
non-confidential (`none`) and SNP isolation. It does not represent TDX isolation.
It also models direct kernel boot and UEFI-related configuration, but schema
presence is not evidence of end-to-end support. The IGVM UEFI personality is
explicitly marked reserved.

## 2. What we want to add

Support explicitly identified nested VM configurations rather than treating all
guests as SNP CVMs. Keep two separate dimensions:

| Dimension | Proposed choices and scope |
| --- | --- |
| Isolation type | Retain SNP; add non-confidential guests first. Evaluate TDX or other confidential types separately, subject to verified generator, runtime, hardware, and nested-platform support. |
| Boot mode | Initially retain x86-64 Linux-direct IGVM composition where supported for each isolation type. Direct kernel boot, UEFI, disk-based workloads, and other architectures are separate increments. |

Use a compatibility matrix that identifies supported combinations of isolation,
architecture, boot mode, dependency profile, generator, host environment, and
runtime. Do not promise the Cartesian product of these choices.

Preserve external dependency/application builds and the create/launch/destroy
boundary. Create consumes a compatible prebuilt bundle; it does not compile
dependencies or applications. Application and init-script overrides remain
independent of bundle selection.

Extend the proposed image-bound launch profile to describe the selected VM
configuration as well as its resources and final image hash. Separate guest
readiness from optional, type-specific evidence collection. Neither change
implicitly adds attestation verification or key release.

**Safety and compatibility:** Never downgrade confidential isolation to
non-confidential execution. Unsupported combinations must fail explicitly.
Existing images without a launch profile retain the documented legacy SNP
runtime settings with a visible warning; missing metadata must not select a
new VM type.

## 3. Changes required

### Dependency builder and bundle

Introduce versioned, configuration-specific build recipes and bundle metadata.
The builder must select a compatible IGVM JSON and boot components for each
supported combination, not merely change an isolation label in the current JSON.

```text
Selected supported build recipe
    isolation type + architecture + boot mode + source/toolchain pins
    |
    v
Build compatible kernel, base initrd, and required boot components/tooling
    +
Supply compatible IGVM JSON for the selected configuration
    |
    v
Package bundle with configuration identity, artifact paths/hashes,
and accurate source/toolchain/build provenance
    |
    v
Create validates that configuration and consumes its indexed IGVM JSON
```

Define required artifacts per configuration. The existing SNP triple remains
valid for SNP, but `snp_bootshim` must not be universally required for other
types. Determine their actual boot-component requirements through generator
and runtime validation.

Continue using manifest-indexed bundle-relative paths and SHA-256 checks.
Version the contract without reinterpreting existing schema-1 SNP bundles.
Publish separately identifiable release assets and compatibility records for
each supported bundle; wheel resource selection must preserve the SNP default.
New bundle selection options and metadata field names remain to be designed.

Arbitrary prebuilt inputs to the dependency builder are a separate feature,
not required to add a second VM type. If added later, they must include all
configuration-required artifacts and accurate provenance, and undergo the same
validation. Today customers can maintain a derivative build recipe or replace
artifacts in a compatible bundle and update its manifest/provenance.

### CLI, runtime, and guest contract

| Area | Required change |
| --- | --- |
| Bundle validation | Dispatch validation by supported bundle configuration. Preserve path containment, artifact hash checks, and existing schema-1 behavior; reject unknown versions and unsupported configurations. |
| Image generation | Replace unconditional SNP configuration handling with configuration-specific validation and generator resource mapping. Retain shared initrd composition where appropriate; apply debug/policy settings only where supported. |
| Measurements | Read type-specific generator outputs where available. Define typed result semantics, including an explicit absence of confidential launch measurements for configurations that do not produce them. Do not fabricate SNP-shaped results. |
| Signing | Keep `--signing-key` as SNP ID-block signing unless a separately specified mechanism is added. Reject it for configurations where it is unsupported. Signing failure must not publish an unsigned fallback. |
| Launch profile | Add versioned isolation, architecture, and boot-mode identity derived from the effective image-generation configuration. Validate the final image hash, settings, and compatibility before issuing runtime requests. Hash after any optional signing. |
| Runtime mapping | Map validated configuration to explicit `none` or `snp` isolation and the supported boot configuration. TDX requires verified runtime support and an RPC contract extension, with regenerated bindings; changing JSON alone is insufficient. |
| Host prerequisites | Check host/backend capabilities for the selected configuration, not just device existence or package identity. Keep setup bounded; do not silently change kernels, reboot, or switch isolation modes. |
| Readiness and evidence | Define a minimal application-readiness exchange usable by the supported sample guests. Non-confidential guests must not need SNP evidence. Collect confidential evidence through explicitly type-specific handling and label it unverified unless verification is implemented. |
| Lifecycle state | Persist the validated VM configuration and readiness/evidence outcome needed for diagnosis. Preserve existing supervision and teardown; do not let configuration generalization weaken ownership checks. |
| Documentation and defaults | Update supported configurations, bundle recipes, CLI help/results, and compatible sample application/init defaults. Ensure default resources do not accidentally assume SNP for a non-confidential selection. |

## 4. Suggested steps

1. **Validate feasibility and define the first matrix.** Inspect the external
   builder and pinned generator/runtime implementation. Prove that a
   non-confidential x86-64 Linux-direct IGVM guest can boot on a compatible
   target host before committing to that workflow. If unsupported, design a
   separate boot-mode increment rather than silently substituting direct boot.
2. **Define versioned contracts.** Specify configuration identity, required
   artifacts, compatibility rules, launch-profile mapping, measurement/result
   semantics, and readiness/evidence behavior. Preserve legacy SNP defaults
   and explicitly reject mismatches and isolation downgrades.
3. **Extend the dependency build first.** Add the second supported recipe and
   JSON, emit its indexed artifacts/hashes/provenance, and publish identifiable
   bundles. Verify controlled repeated builds and reject invalid combinations.
4. **Generalize create and launch.** Add configuration-specific validation,
   generation, measurement handling, signing checks, profile export, runtime
   mapping, and host checks while reusing existing composition and lifecycle
   code. Update sample resources for the new readiness contract.
5. **Verify both paths end to end.** Run existing SNP regression tests plus
   focused tests for the new type, legacy bundles/images, mismatched profiles,
   unsupported signing, malformed metadata, and missing capabilities. On
   compatible hosts, verify actual guest boot/readiness and cleanup after
   success, failure, timeout, and repeated destroy.
6. **Evaluate additional configurations separately.** Assess TDX, UEFI,
   direct kernel boot, and other architectures against the same matrix and
   acceptance criteria. Add only combinations backed by working bundle builds
   and target-host boot tests.

These steps are proposed work, not completed validation. This expansion moves
type-independent readiness into scope for the selected sample workflows; it
does not promise arbitrary workload formats or a universal readiness protocol.
