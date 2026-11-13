# Enclave isolation type support

## 1. Scope and current limitations

This change is a **manifest-backed refactor of the existing SNP flow**, not
delivery of isolation-type selection. Centralize isolation-specific
configuration, requirements, and defaults in a packaged JSON manifest.
**SNP remains the only enabled type and the implicit default** for create and
launch; no new customer selector is introduced.

Today, create requires an SNP Linux-direct recipe and fixed artifact set.
Launch hashes the image but does not inspect its platforms; it unconditionally
requests SNP, one CPU, 160 MiB, and fixed COM1/PCIe/vsock settings. Readiness
requires an SNP-shaped report. Instance records do not identify isolation.

### In scope for this change

- Package and validate the SNP manifest defined in section 4, including typed
  loading through an explicit enabled-type/file mapping.
- Replace hardcoded isolation-specific configuration in setup, create, and
  launch with values from that manifest: dependency requirements, supported
  recipe/layout, CPU/memory defaults, runtime/device settings, and host
  requirements. Apply the corresponding subset of section 2's file changes.
- Preserve existing SNP behavior, shared Python policy, and lifecycle wiring.
  Keep genuine SNP implementations in Python, including measurement, signing,
  boot-mode/protobuf mappings, and report parsing; moving configuration does
  not make these implementations isolation-independent.
- Add focused manifest validation, installed-wheel resource lookup, and
  regression tests, with documentation updates for this refactor.

**Note:** Create will use the bundle’s IGVM recipe; launch will use snp.json manifest. These configurations must be kept compatible externally by updating and validating them together during bundle preparation/release—particularly architecture, boot/isolation type, CPU count, memory size (accounting for units), and device layout. The extension does not check synchronization between them.

### Out of scope / deferred

- `--isolation-type`, IGVM platform inspection/discovery/selection, and
  multi-platform runtime-selection validation (section 3).
- New persisted isolation identity/provenance and schema-2 instance records
  (section 5); these are not prerequisites for consuming manifest defaults.
- Customer CPU/memory overrides and image-embedded effective-settings metadata
  from [NEW_DESIGN.md](NEW_DESIGN.md) proposal 2, additional isolation
  implementations, dependency retrieval, runtime/protocol upgrades,
  status/diagnostics, and workspace/path/cleanup changes.
- Profile selectors, plugins, per-type workers, or duplicated lifecycle flows.

## 2. Code changes by file

Paths below are under `azext_enclave/` unless stated otherwise. Add two small
helpers and one JSON file; refactor existing functions in place.

| File / function | Required change |
| --- | --- |
| **New `manifests/snp.json`** | Package the configuration shown in section 4. |
| **New `_isolation.py`** | Add an explicit enabled-type/file mapping, installed-resource lookup, and typed schema validation. Reject missing fields, malformed/contradictory values, and unsupported versions. No customer isolation-manifest paths or directory discovery. |
| **New `_igvm.py`** | Add a bounded header reader and pure selection function following section 3. Return declared platforms and selected type/version/VTL/mask; distinguish malformed, ambiguous, and unsupported errors. |
| `_params.py:load_arguments()` | Register `--isolation-type` as specified in section 3; keep other arguments unchanged. |
| `custom.py:setup_enclave()` / `create_enclave()` / `launch_enclave()` | Update arguments/call sites. Resolve create configuration and check implemented type support before bundle lookup or host changes; pass launch's optional selector unchanged. Preserve override precedence and error translation. |
| `_image_builder.py:_dependencies()` | Read required artifacts and expected bundle schema/profile from typed configuration. Enforce bundle/architecture/recipe agreement. Preserve bundle schema-1 validation, all supplied-artifact hashes, containment, safe extraction, and executable checks. |
| `_image_builder.py:build_igvm()` | Accept configuration; gate debug/measurement/signing, validate the recipe/layout, and apply CLI-owned settings to its private copy. Check final platform identity before publication, including signed output. Preserve initrd composition and build/publication/cleanup behavior. |
| `_setup.py:setup_host()` | Consume backend/device requirements through existing checks. Preserve root/Azure Linux restrictions, confirmation, bounded installation, and runtime verification; add no probes, kernel changes, or reboot behavior. |
| `_rpc.py:create_request()` | Map resolved resource/device settings into existing protobuf fields. Keep explicit SNP oneof and boot-enum serialization: absent isolation must not silently mean non-confidential launch. Leave `VmClient` unchanged. |
| `_lifecycle.py:launch()` | Inspect/select the platform and validate resolved configuration, implemented type support, and host requirements before instance creation/startup. Pass configuration to RPC and type to readiness; write initial state as specified in section 5. |
| `_lifecycle.py:probe_guest()` | Guard the SNP exchange/parser by type. Preserve deadlines, response retention, and the non-verification warning. |
| `_lifecycle.py:destroy()` | Validate schema-2 identity/settings and use saved configuration independently of image/manifest availability. Preserve shutdown/recovery; see section 5. |
| `custom.py:_instance_result()` | Filter new internal provenance/configuration fields deliberately; preserve existing public output and ownership filtering. |
| **Extension root `setup.py`** | Package `manifests/*.json`; verify installed-wheel lookup. Add a dependency only if required by the chosen reader. |
| `_help.py`; **root `README.rst`, `HISTORY.rst`** | Document selection rules, defaults, compatibility changes, and examples with current workspace requirements. |
| `tests/latest/` | Add focused manifest/header/selection tests and extend existing command/build/setup/RPC/readiness/lifecycle tests; see section 6. Replace placeholder image bytes where real header validation now applies. |

Keep genuine SNP implementations: `_snp_measurement()`, `_sign_igvm()`,
`snp_linux_direct`/`snp_bootshim` mapping, SNP protobuf selection, and report
validation. Guard them; do not rename them into supposedly universal handlers.

Leave `commands.py`, `_worker.py`, and `_instance.py` ownership/permissions/locks
unchanged. Preserve runtime package/integrity checks, generated protobufs,
protocol pins, paths, endpoint descriptions, and the single-runtime host guard.

## 3. CLI and image platform selection

| Command | `--isolation-type` behavior |
| --- | --- |
| `create` | Required, no default; reject omission/unsupported values before side effects. |
| `launch` | Optional, no default; omission requests discovery, not SNP. An explicit value selects an image-declared platform, not an override. |
| `destroy` | No selector; use validated persisted identity. |

```bash
az enclave create --isolation-type snp \
    --workspace ./build --output-file ./image.igvm

# Auto-select a single-platform image
az enclave launch --name demo --igvm ./image.igvm

# Select explicitly; required when the image declares multiple platforms
az enclave launch --name demo-selected --igvm ./image.igvm \
    --isolation-type snp
```

In `_igvm.py`, read standard
[`IGVM_VHS_SUPPORTED_PLATFORM`](https://docs.rs/igvm_defs/latest/igvm_defs/struct.IGVM_VHS_SUPPORTED_PLATFORM.html)
headers. Their
[`platform_type`](https://docs.rs/igvm_defs/latest/igvm_defs/struct.IgvmPlatformType.html)
declares compatible platforms, such as `SEV_SNP`, `TDX`, or `NATIVE`, not the
user's preferred mode. Map recognized platform identifiers explicitly; recognizing
TDX for errors/ambiguity does not enable TDX launch.

| Image / request | Result |
| --- | --- |
| One declared platform; no selector | Select it, then require enabled extension/backend support. |
| Multiple platforms; no selector | Reject and require selection. Count entries before filtering by host capability or enabled types. |
| Explicit selector matches one entry | Validate that entry's version/VTL, manifest, backend, and implemented type support. |
| Selector is absent from the image | Reject; report the request and declared platforms. |
| Multiple variants remain for the selected type | Reject as ambiguous; a type-only selector cannot distinguish them. |
| Disabled/unimplemented selected type | Reject; never substitute SNP or non-confidential isolation. |
| Missing entries, malformed headers, unsupported format, or bad checksum | Reject even with an explicit selector. Never fall back to assumed SNP. |

Reader requirements:

- Support reviewed versions compatible with the pinned generator. Validate
  magic, file size, header bounds/lengths/alignment/padding/checksum, platform
  entry structure/order, and distinct single-bit compatibility masks.
- Honor required/optional header rules: reject unsupported required structures
  and safely skip valid optional ones. Keep inspection bounded and read-only;
  no string scanning, dependency downloads, or image-selected executables.
- Return the selected compatibility mask.
- Establish explicit accepted platform-version/VTL combinations from pinned
  artifacts and runtime support, and enforce them in Python; recognition alone
  does not establish support.

**Runtime-selection gate:** the RPC sends SNP isolation, not a compatibility
mask. Verify that the pinned runtime uses this to load the intended platform's
content. Accept multi-platform images only where this behavior is demonstrated;
otherwise reject them. Do not expand runtime/protocol scope to bypass this gate.

The launch flow is:

```text
IGVM headers + optional selector -> validated platform -> packaged manifest
    -> supported configuration + shared Python policy/wiring -> existing launch flow
```

Create validates recipe compatibility before generation. Launch validates
inspectable image structure/platforms, but an SNP header does not prove boot,
resources, devices, application protocol, publisher authenticity, or attestation.
Do not claim complete pre-launch compatibility checks for external images:
reject detectable incompatibilities early and report runtime/readiness failures
explicitly. This is not a general-purpose IGVM launcher.

## 4. Manifest contract and example

Package `azext_enclave/manifests/snp.json` through the existing wheel resource
mechanism, with one manifest per enabled type. This is proposed extension
metadata, not an implemented schema or an `igvmfilegen` recipe.

```json
{
  "schema_version": 1,
  "isolation_type": "snp",
  "supported_configuration": {
    "architecture": "x86_64",
    "boot_mode": "linux_direct",
    "device_layout": "measured-pcie-v1"
  },
  "dependency_bundle": {
    "schema_version": 1,
    "profile": "measured-pcie-v1",
    "required_artifacts": [
      "kernel", "base_initrd", "snp_bootshim", "igvmfilegen", "igvm_manifest"
    ]
  },
  "defaults": {
    "processor_count": 1,
    "memory_mib": 160
  },
  "runtime": {
    "backend": "mshv",
    "disable_vmbus": true,
    "disable_hv": false
  },
  "devices": {
    "serial": {"port": 0},
    "pcie": {
      "segment": 0,
      "start_bus": 0,
      "end_bus": 1,
      "ecam_base": 4141875200,
      "low_mmio_base": 4143972352,
      "low_mmio_size": 16777216,
      "high_mmio_base": 4294967296,
      "high_mmio_size": 1048576
    }
  },
  "host_requirements": {
    "character_devices": ["/dev/mshv"]
  }
}
```

### Manifest properties

Object sections group related settings; each value below has a distinct role.

| Property | Why it belongs in the manifest |
| --- | --- |
| `schema_version` | Versions this extension metadata so the loader can reject formats it does not understand. |
| `isolation_type` | Identifies the configuration as SNP and lets the loader check agreement with the requested or image-declared type. |
| `supported_configuration.architecture` | Defines the supported guest architecture so bundle, recipe, and inspectable image properties can be checked consistently. |
| `supported_configuration.boot_mode` | Defines the supported boot method and its mapping to the runtime's boot-personality field. |
| `supported_configuration.device_layout` | Names the supported image/device contract. This layout is fixed for our SNP implementation, not required by SNP generally. |
| `dependency_bundle.schema_version` | Specifies the bundle-index format accepted for this configuration, independently of the extension metadata version. |
| `dependency_bundle.profile` | Identifies the expected bundle compatibility contract so incompatible bundles can be rejected. It is not another user selector. |
| `dependency_bundle.required_artifacts` | Lists the inputs that must be present before generation, including CLI packaging inputs beyond the generator's own resources. The bundle cannot weaken this list. |
| `defaults.processor_count` | Provides the shared CPU-count default for image creation and launch rather than defining it independently in each path. |
| `defaults.memory_mib` | Provides the shared guest-memory default for image creation and launch. |
| `runtime.backend` | Identifies the backend this configuration requires so launch can check implementation/host compatibility. It does not introduce a new worker argument. |
| `runtime.disable_vmbus` | Specifies whether the supported configuration runs without VMBus, keeping that choice out of hardcoded RPC construction. |
| `runtime.disable_hv` | Specifies whether Hyper-V enlightenments are disabled; the current `false` value preserves them. This is separate from disabling VMBus. |
| `devices.serial.port` | Selects the guest serial port expected by this configuration while Python retains host connection wiring. |
| `devices.pcie.segment` | Identifies the PCI segment that must agree between the measured layout and runtime configuration. |
| `devices.pcie.start_bus` | Defines the lowest valid PCI bus number for the supported root complex. |
| `devices.pcie.end_bus` | Defines the highest valid PCI bus number for the supported root complex. |
| `devices.pcie.ecam_base` | Records the PCI configuration-space base expected by the measured recipe so layout compatibility can be checked. |
| `devices.pcie.low_mmio_base` | Fixes the starting address of the low MMIO window so the recipe and runtime use the same address map. |
| `devices.pcie.low_mmio_size` | Defines the low MMIO window's extent for layout validation and runtime mapping. |
| `devices.pcie.high_mmio_base` | Fixes the starting address of the high MMIO window so the recipe and runtime use the same address map. |
| `devices.pcie.high_mmio_size` | Defines the high MMIO window's extent for layout validation and runtime mapping. |
| `host_requirements.character_devices` | Declares required host interfaces such as `/dev/mshv`; launch checks these using existing Python validation. |

### Implementation rules

| Contract | Implementation rule |
| --- | --- |
| Field mappings and units | Map manifest architecture `x86_64` to recipe `x64`; serial port 0 means COM1. Memory is in MiB and MMIO/ECAM values are bytes. Map `low_mmio_size`/`high_mmio_size` to RPC `low_mmio`/`high_mmio`; ECAM has no new pinned RPC field. |
| Ownership boundaries | The bundle index owns supplied artifact paths/hashes; the recipe owns SNP policy/boot details. Validate agreement and apply CLI-owned settings to a private recipe copy; never pass this entire manifest to `igvmfilegen`. |
| Host policy | Keep the Azure Linux installer restriction in Python and runtime pins in `_runtime.py`, not in this configuration. |
| Shared behavior and wiring | Python retains type guards, disabled guest-debug default, serial `connect=True`, readiness port 4050, names `vsock`/`rc0`, and generated per-run paths/ownership. These are not isolation-specific configuration. |

### OpenVMM manifest recipe

OpenVMM's
[`vm/loader/manifests`](https://github.com/microsoft/openvmm/tree/main/vm/loader/manifests)
contains build recipes consumed by `igvmfilegen`. For example,
[`snp-linux-direct.json`](https://github.com/microsoft/openvmm/blob/main/vm/loader/manifests/snp-linux-direct.json)
describes how to construct an SNP Linux-direct image.

A separate, generated resources JSON maps kernel/initrd/bootshim names to
absolute paths; it does not declare requirements or hashes. OpenVMM's Rust
[`required_resources()`](https://github.com/microsoft/openvmm/blob/main/vm/loader/igvmfilegen_config/src/lib.rs)
derives required generator resources from the recipe, and `check_required()`
validates their presence. Build tooling supplies the binaries; launch arguments
and runtime code supply CPU/RAM, backend, and device configuration.

**Our schema centralizes isolation-specific settings across create/launch**,
including packaging inputs (generator, recipe, base initrd), launch settings,
and host requirements. Unlike OpenVMM's build-only recipe, it
serves both CLI flows. Continue passing the separate recipe and generated
`resources.json` to `igvmfilegen`. Keep its resource validation without Rust
bindings or a duplicate resolver; bundles cannot weaken packaged requirements.

The [upstream README](https://github.com/microsoft/openvmm/blob/main/vm/loader/manifests/README.md)
describes host-device-tree-driven topology. Our pinned `measured-pcie-v1` recipe
instead includes `processor_count` and measured `pcie_host_bridge` fields.
Preserve that contract; do not adopt upstream runtime behavior or infer saved
customer resource choices from generator memory fields in this refactor.

## 5. Behavior and state requirements

- **Inputs:** Keep packaged application/init defaults and explicit override
  precedence. A customer dependency archive/directory replaces that source only;
  never mix missing artifacts with packaged defaults or rewrite an incompatible
  recipe to appear supported. Hashes detect mismatches, not trusted publishers.
- **Debug/signing/evidence:** Preserve today's global `--debug` to SNP guest-debug
  mapping and warning, measurement output, and `--signing-key` behavior. Reject
  unsupported operations early; signing failures never publish unsigned fallback
  images. Keep report-shape checks, not claims of attestation verification.
- **Images:** Compatible SNP images need no new metadata/rebuild. Launch uses
  manifest CPU/memory defaults until proposal 2; these cannot recover custom
  choices or guarantee boot.
- **State:** Write instance schema 2 in the initial `starting` record: type,
  platform version/VTL/mask, selection source, effective settings, manifest
  provenance, and image hash. Reject older, unknown, corrupt, or incomplete
  records without migration. Destroy uses saved settings without needing the
  image or current manifest. Preserve ownership, liveness, output, rollback,
  shutdown, and retention. Destroy development instances with their original
  CLI before upgrading; do not discard records for running instances.

## 6. Implementation validation

Add focused tests in each phase. Manifest and reader work can progress
independently after step 1; CLI/lifecycle integration depends on both.

| Test location | Required coverage |
| --- | --- |
| New `tests/latest/test_isolation.py` | Missing/invalid/versioned manifests, invalid dependency declarations, contradictory settings, disabled types, and packaged-resource lookup. |
| New `tests/latest/test_igvm.py` | Real pinned unsigned/signed headers; malformed/truncated/unsupported headers, bounds/alignment/padding/checksum/masks, required/optional structures, missing platforms, auto/explicit match, mismatch, multi-platform ambiguity, and duplicate variants. Do not select SNP just because it is the only enabled type. |
| Existing `test_commands.py`, `test_create.py`, `test_setup.py` | Required/optional argument behavior, rejection before side effects, complete default/customer bundles, missing packaging inputs, unsupported recipes, independent app/init overrides, type guards for debug/measurement/signing, platform checks, publication/cleanup, and unchanged setup behavior. |
| Existing `test_rpc.py`, `test_guest_probe.py`, `test_lifecycle.py` | Unchanged SNP serialization/resources/devices/evidence, readiness type guards, and shared serial/vsock wiring; complete schema-2 state before startup, rejection of older/partial/corrupt records, destroy independent of image/manifest availability or updates, endpoints, ownership, rollback, and teardown failures. |
| Installed wheel and supported host | Load resources without repository-relative paths or downloads. Verify automatic and explicit SNP launch plus readiness/destroy, recording OS/kernel/runtime/bundle identity. Test representative multi-platform runtime selection; reject/document cases not demonstrably supported. |

Run relevant existing unit tests, `azdev style enclave --pep8`, and
`python scripts/generate_protocol.py --check` using the README's pinned protocol
environment. Use the existing wheel workflow; add no test tooling solely for
this refactor. Doubles do not establish backend boot support. Reader delivery,
runtime selection, and host availability are the main estimate risks.
