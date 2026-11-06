# Enclave isolation type support

## 1. Objective and scope

Package one versioned isolation manifest JSON per type, shared with
[NEW_DESIGN.md](NEW_DESIGN.md), proposal 2. Store defaults, supported
configurations, required artifacts, and operation capabilities as data.
Existing workflows read the selected manifest and check operation support.

Keep the current module layout; no per-type profile classes, handler hierarchy,
or duplicated lifecycle code. Measurement parsing, signing, host probes, RPC
serialization, and guest exchanges remain Python logic, with explicit isolation
checks where behavior differs. JSON describes support; it does not implement it.

Move isolation-specific defaults and settings into the packaged SNP manifest, preserving their current values and behavior. Keep the existing dependency bundle, application/init defaults, customer overrides, bundle schema, RPC bindings, and runtime pins unchanged.

## 2. Current constraints

| Area | SNP-specific behavior today |
| --- | --- |
| Bundle validation | Requires schema 1, `measured-pcie-v1`, x86-64, and the same artifact identifiers for every build, including `snp_bootshim`. |
| Image generation | Requires an SNP Linux-direct configuration, applies SNP guest-debug settings, reads SNP measurements, and optionally signs an SNP ID block. |
| Runtime request | `_rpc.py` unconditionally selects SNP isolation and Linux-direct IGVM boot with fixed resources/devices. |
| Readiness | `_lifecycle.py` requires the sample exchange and an SNP-shaped report; it does not verify attestation. |
| Protocol | The checked-in RPC schema represents SNP and non-confidential isolation, but not TDX isolation. |
| Packaging | `setup.py` packages existing resource files. Individual-artifact retrieval and combined-bundle assembly are not implemented. |

Artifact requirements are type-specific. Keep isolation type, architecture,
and boot mode separate; supporting a type does not support every combination.

## 3. Proposed selection and isolation manifests

### CLI selection

```bash
# Explicit SNP selection with packaged dependencies
az enclave create --isolation-type snp --output-file ./image.igvm

# Setup also requires explicit isolation selection
az enclave setup --isolation-type snp

# Explicit type and customer bundle
az enclave create --isolation-type snp \
    --dependency-bundle ./custom-dependencies.tar.gz \
    --output-file ./image.igvm
```

Require `--isolation-type` for create and setup, with no implicit default.
Omission is an argument error before side effects; existing callers must add
`--isolation-type snp`. Launch still reads isolation from the image using
NEW_DESIGN's embedded-settings and legacy compatibility rules, not a CLI
isolation override.

Retain the existing archive-or-directory `--dependency-bundle` override.
An override replaces the
packaged dependency source, not individual missing artifacts: never silently
mix incomplete customer inputs with packaged defaults.

Only `snp` is selectable. Reject unknown, disabled, or unimplemented types
before dependency lookup, generation, setup changes, or RPC calls; never
substitute SNP. Customer bundles must satisfy the selected type's full artifact
contract. Preserve schema-1 path, hash, artifact, and configuration validation;
no generalized bundle schema is required now.

### Manifest directory and ownership

Follow the organization of OpenVMM's
[`vm/loader/manifests`](https://github.com/microsoft/openvmm/tree/main/vm/loader/manifests):
keep reviewed JSON configuration files in a dedicated directory. Proposed
extension layout:

```text
azext_enclave/
    manifests/
        snp.json
```

Use an explicit mapping of supported type identifiers to packaged files, not
user-supplied paths or directory enumeration. Add another type's manifest only
with validated code, artifacts, runtime, and host support.

OpenVMM's files are **generator recipes**; the proposed JSON is **CLI isolation
metadata** with a separate schema. Keep the bundle's generator recipe as an
input and apply validated CLI settings to a private copy during create.
Do not pass CLI-only metadata directly to `igvmfilegen`.

| Component | Owned by | Responsibility |
| --- | --- | --- |
| Packaged isolation manifest | Reviewed extension metadata | Versioned identity, supported architecture/boot/layout, required logical artifacts, resource defaults/validated constraints, device settings, host requirements, and operation capabilities. Same source for NEW_DESIGN create and launch. |
| Existing dependency-bundle index and generator manifest | Repository bundle or compatible customer override | Artifact paths/hashes and IGVM generation inputs. Preserve existing schema-1 SNP validation; no bundle migration or automatic replacement of application/init defaults. |
| Existing CLI modules | Reviewed Python code | Validate manifest data, enforce support checks, map settings to generator/RPC fields, and perform measurement, signing, host checks, readiness, and shared lifecycle operations. |

### Settings and operation checks

Define and validate a versioned schema before consumption. Load each manifest
into a typed validated representation; reject missing required fields, malformed
values, unsupported versions, contradictory settings, and unknown operation
identifiers. Preserve today's SNP defaults in this single source: x86-64,
Linux-direct, one processor, 160 MiB, and the current supported device layout.
Do not invent CPU/memory limits before owner validation.

Keep defaults separate from fixed constraints and supported operations. Declare
capabilities for concrete behavior such as SNP guest-debug policy, SNP ID-block
signing, SNP measurement parsing, and the current SNP readiness/report exchange.
Existing code checks the selected type and capability before invoking that
operation. For example, an unsupported `--signing-key` request fails before
generation; it never silently produces an unsigned image.

An enabled type needs implemented create/setup/launch/readiness behavior.
Reject requests for unsupported optional operations; never run SNP code for a
different type. Keep ownership, locks, supervision, logs, and teardown shared.

Manifests and bundle metadata must not load arbitrary Python modules, name
unrestricted executables, or supply unchecked host command templates.
Existing trust requirements for executing a bundled generator still apply;
artifact hashes detect mismatches but do not authenticate a publisher.

## 4. Packaging and dependency delivery

Package isolation JSON through the existing wheel resource mechanism, alongside
unchanged dependency/application/init defaults. Validate installed-wheel lookup;
do not depend on repository-relative paths at runtime.

Keep schema-1 SNP bundle consumption unchanged. Declare the `snp_bootshim`
requirement in `snp.json` and enforce it in the existing bundle-validation code.
Preserve indexed paths, containment, safe extraction, and integrity validation.
Version any future bundle contract separately from the isolation manifest.

External retrieval/assembly and default migration remain deferred until delivery
details are confirmed. Then follow NEW_DESIGN proposal 1's candidate assembly
and validation gates. Do not fetch or compile dependencies during create/setup,
or download during metadata inspection or customer installation.

## 5. Required CLI manifest integration

| Area | Required change |
| --- | --- |
| Argument handling | Require `--isolation-type` for create and setup with no default. Allow only SNP; reject missing/unknown/disabled selections before side effects. |
| Create validation | Load the packaged manifest and use its required artifacts/configuration constraints in existing bundle validation and generator mapping. Preserve validation/results and the schema-1 contract. |
| Image composition | Keep rootfs/initrd composition in place. Apply validated manifest settings to private generator inputs; preserve application/init and workspace behavior and integrate NEW_DESIGN options separately. |
| Default application/init | Keep today's provided defaults and explicit override precedence. Manifest requirements must not let a dependency-bundle override implicitly replace application/init. |
| Debug settings | Check support before applying the existing SNP guest-debug mapping in place. Preserve global `--debug` for CLI diagnostics; it is not a guest security policy. |
| Measurements | Check the supported measurement format and keep existing SNP parsing/output. Implement other formats only when supported; never label another type's result as SNP. |
| Signing | Check SNP ID-block signing capability before invoking existing signing code. Preserve `--signing-key` behavior, fail on unsupported requests/signing errors, and never publish an unsigned fallback. |
| Setup | Read declarative host/backend requirements and apply supported checks in existing setup code. Retain bounded installation behavior; do not change kernels, reboot, or fetch image dependencies. |
| Launch | Use validated manifest defaults/device settings in existing RPC mapping; retain the SNP serialization check and current schema. Integrate NEW_DESIGN's embedded image identity/settings separately; never take a launch isolation override or regenerate the image. |
| Worker/supervisor | Keep process ownership, serial collection, logs, locks, and child reaping shared. Pass only validated configuration needed by the runtime; do not move CLI validation into arbitrary bundle-driven worker behavior. |
| Readiness/evidence | Check support for the selected type's exchange/report format before using the existing SNP probe. Preserve today's readiness requirements; a future type needs a real compatible probe, not an SNP-shaped fallback. Collection is not cryptographic verification. |
| Instance state/status | Keep ownership and liveness handling shared. Record resolved type/configuration identity for new records without invalidating existing SNP records; preserve existing fields and command output. Integrate the proposed status command separately. |
| Destroy | Preserve shared RPC shutdown and owned-process recovery. Validate persisted isolation identity; treat valid legacy records explicitly as SNP, not as whichever type later becomes the default. Reject corrupt/unknown identities. |
| Cleanup | Keep confirmed shutdown, removal, and log-only archival independent of isolation. Integrate NEW_DESIGN's automatic cleanup contract separately; no per-type cleanup implementation or dry-run/opt-in cleanup flag. |

Preserve current paths and the single-runtime host guard until NEW_DESIGN's
path/workspace changes ship. Do not introduce per-type roots or concurrent
runtimes.

### Embedded image settings and backward compatibility

NEW_DESIGN proposal 2 can ship separately for SNP, but must be integrated before
enabling another isolation type:

The packaged manifest supplies per-type defaults/constraints, not per-image
choices. Create stores effective identity/configuration/resources in supported
IGVM fields or agreed embedded metadata before measurement/signing. Confirm
writer/reader semantics with owners; parameter bounds may not encode choices.

Launch validates saved image settings against the packaged manifest and maps
them to RPC without rewriting the image or requiring companion JSON. Save
identity, effective settings, manifest/default provenance, and image hash in
instance state; a hash is not publisher authentication or attestation.

For missing fields, use NEW_DESIGN's explicit warnings and packaged defaults;
missing isolation identity uses SNP compatibility, not a future default.
Malformed, contradictory, or unsupported values fail. Defaults cannot recover
lost custom choices or guarantee boot. Non-SNP customer images need validated
embedded identity/settings; never infer type from filenames. Interpret valid
legacy instance records as SNP; report corrupt/unknown identity explicitly.

## 6. Potential issues and constraints

| Issue | Impact and handling |
| --- | --- |
| Configuration drift | Validate manifest/image/state boundaries; never downgrade unsupported selections or fill incomplete customer bundles from defaults. |
| Excessive refactoring | Keep existing modules and extract only reusable helpers; no profile hierarchy, plugins, or duplicated lifecycle. |
| Compatibility regression | Preserve bundle schema, runtime pins, output, and legacy state interpretation. Version isolation metadata separately. |
| Signing/debug ambiguity | Check guest-operation support explicitly; preserve CLI `--debug` semantics and never substitute an unsigned image. |
| Metadata mistaken for support | Customer bundles cannot replace packaged capabilities, supply host commands, or enable missing RPC/readiness implementations. |

## 7. Suggested implementation sequence and acceptance criteria

1. **Package and validate `snp.json`.** Define the versioned schema, supported-type
   mapping, and installed-resource lookup using current settings and requirements.
2. **Wire selection and compatibility.** Require create/setup `--isolation-type`
   with no default, reject missing/unsupported selections before side effects, and persist
   identity without invalidating valid legacy SNP records.
3. **Consume settings and guard operations in place.** Update existing
   bundle/generator/RPC code and add signing, guest-debug, measurement, setup,
   and readiness checks; keep shared lifecycle code unchanged.
4. **Run focused regression checks.** Cover missing required isolation arguments,
   schema/version errors, missing
   packaged files, unsupported operations before side effects, default/customer
   bundles, independent app/init overrides, signing/debug/measurements, RPC
   serialization, old/new state, host checks, ownership, and teardown failures.
   Run existing protocol checks and documented linters/tests; verify ordinary
   builds and installed commands do not retrieve dependencies.
5. **Verify SNP end to end.** On a supported host, validate creation, boot,
   readiness/evidence, shutdown, and cleanup with unchanged resources/runtime.
   Record the environment; test doubles do not establish real backend support.

Completion means manifest-backed settings and operation checks preserve SNP
behavior without reorganizing the modules. TDX/non-confidential boot, external
dependency migration, and separate NEW_DESIGN features are not required.
