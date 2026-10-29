# ACE extension improvements

**Naming direction:** All references to ACE (Azure Confidential Enclaves) will
eventually be replaced with a new name. Use CE (Confidential Enclaves) as the
provisional name. Existing ACE command, package, and path names in this document
describe the current implementation; the rename is not yet implemented.

## 1. Objective and scope

Make ACE a reusable enclave image-composition tool: accept externally built
dependency bundles and applications, compose reproducible images, optionally sign
them, and preserve the existing launch/destroy interface.

Create will **not** compile dependencies or applications. This increment does
not add arbitrary workload formats, new attestation guarantees, or a generic
application-readiness protocol. Supported launch resources/devices will instead
be derived from the image configuration through an image-bound launch profile.

## 2. Current design

```text
Packaged defaults or explicit local bundle + application + init script
    -> az ace create -> composed initrd -> IGVM + expected measurements
    -> az ace launch -> guest readiness exchange -> az ace destroy
```

| Command | Current responsibility |
| --- | --- |
| `setup` | Validate Azure Linux 3.0/MSHV prerequisites and install or verify the pinned OpenVMM package. |
| `create` | Validate prebuilt inputs, compose the initrd, generate the IGVM, and optionally sign its SNP ID block. |
| `launch` | Start supervised OpenVMM, create/resume the VM, and wait for the sample guest response. |
| `destroy` | Stop owned processes, remove owned sockets, and retain logs/evidence. |

Most image composition already lives in the CLI rather than external scripts.
Remaining demo coupling is concentrated in:

- **Artifact delivery:** The wheel packages a dependency archive, `enclave_app`,
  and `enclave-init`; release retrieval is not implemented.
- **Input compatibility:** The bundle requires a specific manifest/profile and
  compatible image-generation tooling. The application is a static x86-64 ELF.
- **Startup and readiness:** The default `/init` runs
  `/bin/enclave_app serve-vsock 4050`. Launch requires the sample JSON exchange
  and an SNP-shaped response on that port.
- **Runtime configuration:** Launch uses one CPU, 160 MiB, a fixed PCIe/vsock
  layout, and one active managed runtime per host.

The initial abstraction removes dependency delivery/build coupling, not every
sample protocol constraint. Customers can already override the application and
init script, but those inputs must remain compatible with launch.

## 3. Target design and agreed decisions

The **dependency triple** is the **L2 kernel, base initrd, and SNP boot shim**.
It is a subset of the **dependency bundle**, which also carries `igvmfilegen`,
IGVM configuration, and provenance records. The bundle remains the pipeline
output and create input; the triple is not a new input format.

| Boundary | Agreed design |
| --- | --- |
| Dependency builds | Run the existing `build-igvm-dependencies.sh` script in a separate pipeline and publish its dependency bundle output. The script owns source/toolchain pinning and reproducibility of the triple; create does not rebuild or assemble that bundle. |
| Application builds | Customers or the samples repository build the executable. Application reproducibility scripts stay in that repository. The default `enclave_app` will live in the same public dependency/sample repository. |
| Default resources | Add a retrieval stub/placeholder in `setup.py`, then retrieve external release resources during extension wheel construction and package them in the wheel as the no-override defaults. Keep existing defaults until this works. The external repository/pipeline owns dependency versions; the extension does not maintain independent component pins. Repository, release assets, and compatibility versions remain TBD. This is separate from `az ace setup`. |
| Customer overrides | Retain `--dependency-bundle`, `--application`, and `--init-script`. Explicit parameters override the corresponding defaults; customers provide and are responsible for compatible custom resources. Other inputs retain their defaults. Individual artifact options are not required. |
| Create | Consume the dependency bundle and prebuilt application; unpack the bundle's base initrd; install the application and base init script; repack; generate the IGVM and expected measurements; optionally sign its SNP ID block; export a launch-profile sidecar. |
| Startup | The external repository will contain all default resources before the extension release, including the dependency bundle, sample application, and default init script. Keep the existing `--init-script` replacement option; application selection remains independent of dependency selection. |
| Lifecycle | Preserve lifecycle ownership and teardown behavior. Extend launch configuration to consume the image-bound profile instead of hard-coded resources/devices; define profile selection without redesigning destroy. Verify cleanup and fix gaps as part of implementation. |

```text
Existing dependency build script -> pipeline output: dependency bundle
    (triple + compatible tooling/profile + metadata)
Samples/customer build -> prebuilt application
    -> wheel build: retrieve external resources -> package defaults in extension
       (customer-provided parameter overrides take precedence)
    -> create: unpack -> install app and /init -> repack -> generate -> optional sign
       -> final IGVM + launch profile
    -> launch: validate profile/image -> configure OpenVMM -> existing lifecycle
    -> destroy using the existing lifecycle contract
```

### Image-bound launch configuration

**Proposed, not implemented:** Create exports a versioned launch-profile sidecar
alongside the IGVM, derived from the exact effective IGVM JSON used to build it,
not a later copy of the bundle configuration. Launch validates the profile and
its image hash before constructing the OpenVMM request through shared mapping
logic. This extends the earlier largely-unchanged launch scope only for
configuration; readiness and process-management responsibilities remain intact.

**Selection rules:** Create writes `<image-filename>.launch.json` adjacent to
the final image (for example, `custom.igvm.launch.json`). Launch uses an explicit
profile path when supplied, otherwise discovers that adjacent file. The exact
option name is not finalized. A present profile must pass schema, settings,
and image-hash validation; an explicitly supplied missing/unreadable path is
an input error, not permission to ignore the requested profile.

If no explicit profile is requested and no adjacent sidecar exists, launch
continues with today's hard-coded resource/device settings and logs a visible
warning identifying that fallback, including one CPU and 160 MiB of memory.
No opt-in compatibility flag is required. This preserves existing image usage
but does not prove that the image is compatible with those settings.

| Profile setting | Source and purpose |
| --- | --- |
| Schema version | Identifies the profile contract so launch can reject unsupported formats rather than guess field meanings. |
| Final IGVM SHA-256 | Binds the profile to the published image, including optional ID-block signing, to detect accidental mismatches. This is not publisher authentication or attestation verification. |
| Virtual CPU count | Derived from `processor_count`; tells OpenVMM how many processors to provide, matching image expectations. |
| Memory size | Derived from `memory_page_count` using the supported profile's 4-KiB pages, converted to OpenVMM units with exactness/range checks; supplies the guest's required memory. |
| PCIe segment and bus range | Derived from `pcie_host_bridge`; identifies the PCIe address space in which the guest expects devices. |
| Low/high MMIO bases and sizes | Derived from `pcie_host_bridge`; reserves the device memory windows described by the measured image configuration. |
| ECAM base / supported layout | Records where the guest expects PCIe configuration space. Map it only if the runtime exposes that setting; otherwise validate the layout against the pinned runtime and reject unsupported values. Do not silently drop it. |

The JSON is not a complete OpenVMM configuration. Host socket paths, serial
connections, process ownership, and vsock device attachment remain generated by
launch; the profile must not accept arbitrary executable paths or process
arguments. Image-only settings such as kernel command line and SNP image policy
remain in the image-generation configuration, not blindly copied into RPC fields.

Validate CPU/memory limits, unit conversions, bus ranges, MMIO alignment/overlap,
and generator/runtime support. Publish the IGVM and profile consistently, and
reject malformed, mismatched, or unsupported profiles with actionable errors;
do not fall back when a present profile is invalid. The missing-sidecar path
uses the original configuration with a warning, not inferred image settings.

### Customer customization and build workflow

The following describes **existing overrides**, not proposed individual-artifact
flags. Defaults are currently packaged; the target design changes their source
to repository releases, not the customer's responsibility for custom inputs.

| Component | What the customer can change | How to build and supply it |
| --- | --- | --- |
| L2 kernel | Kernel image and enabled guest features/drivers; not the L1 host kernel. | Build a compatible x86-64 kernel externally, replace the bundle's kernel artifact, and supply the complete bundle through `--dependency-bundle`. |
| Base initrd | Guest filesystem, libraries, utilities, and supporting files. | Build a gzip-compressed cpio initrd externally and replace its bundle artifact. Create subsequently overwrites `/bin/enclave_app` and `/init` with the separately selected inputs. |
| SNP boot shim | Compatible early-boot implementation/version. | Build externally using the supported OpenVMM toolchain and replace its bundle artifact. Kernel/shim/profile compatibility remains the customer's responsibility. |
| IGVM configuration | Kernel command line and compatible image/SNP settings. | Edit the bundle's IGVM JSON and update its manifest hash. Today resource/device changes must match hard-coded launch settings; the proposed sidecar will carry supported settings into launch. Create overrides the JSON's guest-debug setting. |
| `igvmfilegen` | Compatible image-generation tool/version. | Build externally and replace the executable bundle artifact. It must support the CLI's generator commands and measurement/signing outputs; it is not the host OpenVMM runtime. |
| Application | Customer workload logic in one static x86-64 ELF executable. | Build using the customer's toolchain or sample recipe; supply its binary with `--application`. Source directories and container images are not accepted. |
| Init script | Guest initialization, application arguments, and environment. | Supply a shebang script with LF line endings through `--init-script`. This replaces all of guest `/init`, including mounts/initialization, rather than adding a post-start hook. |

**Building a custom bundle:** Start from the existing dependency-build script
linked in the references. It builds pinned sources with Rust/Cargo and Docker
and emits the complete bundle; it does not accept arbitrary prebuilt artifact
paths. Its local-source options require clean checkouts at the pinned revisions.
For customized components, maintain an explicit derivative build recipe or
replace artifacts in a private copy of a compatible bundle after building them
externally. Do not expect create to perform those builds.

Keep `dependency-manifest.v1.json` paths and SHA-256 hashes consistent with every
replaced artifact, and update accompanying build/provenance records and
`SHA256SUMS` when present. Do not retain default-build provenance for custom
outputs. The reader validates all manifest-listed artifacts; regenerating hashes
does not prove authenticity or boot compatibility.

**Building a custom application:** The sample `build-enclave-app.sh` builds the
Rust `enclave_app` package with `--release --locked`, defaulting to
`x86_64-unknown-linux-musl`. The script requires the sample workspace and build
dependencies, not just the script file. Customers can adapt that source/build
recipe or use another language/toolchain that produces the accepted static ELF.
Application reproducibility remains in the sample/customer build workflow.

For example, after building compatible inputs on Linux:

```bash
az ace create \
    --dependency-bundle "$HOME/custom-dependencies" \
    --application "$HOME/build/my-app" \
    --init-script "$HOME/my-init" \
    --workspace "$HOME/ace-build" \
    --output-file "$HOME/images/custom.igvm"
```

Omit a resource option to retain that resource's default. A custom bundle does
not select an application or init script implicitly. Use returned measurements
for the rebuilt image, then launch it using the existing command.

### Defaults and compatibility constraints

Values below are from the currently packaged bundle and CLI, not promised values
for every future dependency release.

| Source | Current values | Effect on customization |
| --- | --- | --- |
| Bundle IGVM profile | `guest_arch: x64`, `guest_svn: 1`, `max_vtl: 0`; SNP Linux-direct boot with `use_initrd: true`. | Custom images must remain within the supported x86-64 SNP profile and generator contract. |
| Bundle image resources | `processor_count: 1`, `memory_page_count: 40960` (160 MiB), `c_bit_position: 51`. | Changing image settings does not change launch's one-CPU/160-MiB runtime configuration. |
| Bundle kernel command line | `console=ttyS0 earlyprintk=serial earlycon panic=-1`. | Change through the bundle profile; preserve console/boot behavior needed for diagnosis. |
| Bundle SNP settings | `policy: 196608` (`0x30000`), `injection_type: normal`, `secure_avic: disabled`, `shared_gpa_boundary_bits: null`. | Advanced policy changes require compatibility validation. The bundle records `enable_debug: true`, but create forces it off unless CLI `--debug` is supplied. |
| Bundle measured PCIe layout | Segment 0, buses 0-1; ECAM base `0xf6e00000`; low MMIO base `0xf7000000`, size 16 MiB; high MMIO base `0x100000000`, size 1 MiB. | Keep the measured layout compatible with launch; editing the bundle does not reconfigure the runtime devices. |
| Default init/application startup | Application installed as `/bin/enclave_app`; script executes `serve-vsock 4050`. | A replacement executable must accept those arguments or use a matching custom init script. |
| CLI readiness | Port 4050; newline-delimited challenge/payload JSON with payload `hello world`; requires an SNP-shaped report. | This is enforced by launch, not merely an application default. Different protocols/ports require future readiness work. No attestation verification or key release is performed by launch. |
| CLI composition | `SOURCE_DATE_EPOCH` defaults to 0; normalized cpio metadata and timestamp-free gzip; unsigned unless `--signing-key` is supplied. | Control inputs/tool versions for reproducibility. Changes to image contents/configuration can change expected measurements. |

## 4. Required changes

| Area | Remaining work |
| --- | --- |
| Naming | Replace ACE terminology and identifiers with the eventual name; use CE provisionally. Coordinate command, package, path, help, documentation, and test changes, and define compatibility for existing users. |
| Resource retrieval | Add the `setup.py` placeholder first, then connect it to external releases during wheel construction, with integrity checks, recorded release identity, and explicit errors. The external release owns component versions; release/compatibility selection remains TBD. Verify retrieval in the wheel-build workflow, not during customer installation. |
| Default migration and overrides | Keep current defaults until retrieval works; replace locally maintained resource payloads with build-fetched resources while retaining their inclusion in the wheel. Package all required defaults published before extension release. Preserve per-resource parameter precedence and customer compatibility responsibilities. The stub must not pretend retrieval succeeded. |
| Pipeline and create contract | Run the existing dependency-build script in the pipeline and publish its bundle, including the triple, tooling/profile, and metadata. Reuse existing validation, composition, and optional signing; do not compile dependencies or applications in create. |
| Image-bound launch profile | Define the schema and shared mapping; export the adjacent sidecar bound to the final IGVM. Support explicit profile selection, automatic discovery, and the original hard-coded settings with a visible warning when no sidecar is found. Reject invalid profiles rather than falling back. Test both paths, conversions, unsupported layouts, mismatches, and guest boot. |
| Reproducibility and validation | Verify repository defaults and custom overrides, retrieval failures, output safety, retention, and signing. Compare controlled repeated initrd/IGVM builds and measurements; characterize signed outputs separately. Update help and run Linux image-build/guest tests. |
| Launch/destroy cleanup | Verify existing cleanup for success, failure/timeout, child exits, interrupted teardown, and repeated destroy; fix uncovered gaps. Confirm owned processes are reaped, sockets removed, guards released only after process exit, and logs/evidence retained. |

These are planned changes and validation criteria, not completed implementation
or findings that lifecycle bugs necessarily exist.

## 5. Deferred improvements and open questions

**Deferred:** Individual kernel/initrd/boot-shim overrides; resolved-input
provenance export; modular sample workload/transport/attestation libraries;
generic readiness; independent guest-debug/logging controls; and richer
multi-file or multi-process workloads. These are not prerequisites for the
agreed initial scope.

**Target host follow-up:** Validate the planned commercially available Azure
Linux 4/MSHV image when details arrive in November 2026. Current setup remains
Azure Linux 3.0-specific.

**Open implementation questions:**

- Which public repository/release assets and compatibility versions will be used?

## Appendix: Current command reference

These options describe today's CLI, not the target release-provider behavior.
No Azure login is required for local ACE operations. Customers install Azure
CLI and ACE before setup. Use the
[customer-testing fork](https://github.com/kayla-hirano/azure-cli-extensions)
initially; the intended source after merge is
[upstream](https://github.com/Azure/azure-cli-extensions).
With wheel installation, install the extension separately for the normal user
and root. Developer installations share the environment but use separate CLI
configuration directories. Follow the [README](README.rst) for exact installation
and privileged invocation instructions.

### `az ace setup`

```bash
sudo az ace setup [--yes]
```

Run as root on Azure Linux 3.0 L1 with a compatible kernel exposing `/dev/mshv`.
Setup installs `openvmm-0.2.0-2.azl3` through configured `tdnf` repositories if
absent, with confirmation, and verifies RPM version/integrity. Incompatible
installations require manual repair.

| Parameter | Required | Default | Behavior |
| --- | --- | --- | --- |
| `--yes`, `-y` | No | `false` | Approve package installation without prompting; validation remains required. |

Returns status, OS, kernel, and runtime path. It does not provision the host,
install a kernel, add repositories, reboot, install developer tools, or start
OpenVMM. Host readiness does not establish guest boot or attestation.

### `az ace create`

```bash
az ace create --workspace "$HOME/ace-build" \
    --output-file "$HOME/images/enclave.igvm"
```

Run as a normal user on Linux or WSL with GNU cpio and gzip. No L1 runtime is
needed for image creation. Create does not compile inputs or install build tools.

| Parameter | Required | Default | Behavior |
| --- | --- | --- | --- |
| `--workspace` | Yes | None | Parent for a private workspace; the parent may already exist. |
| `--output-file` | Yes | None | IGVM destination; existing files are never overwritten. |
| `--dependency-bundle` | No | Packaged `dependencies/dependencies.tar.gz` | Trusted `measured-pcie-v1` bundle directory or tar/tar.gz archive. |
| `--application` | No | Packaged `dependencies/enclave_app` | One prebuilt static x86-64 ELF, installed as `/bin/enclave_app`. |
| `--init-script` | No | Packaged `dependencies/initramfs/enclave-init` | Full guest `/init` replacement; must have a shebang and LF line endings. |
| `--signing-key` | No | No signing | Unencrypted PEM ECDSA P-384 key for SNP ID-block signing; requires OpenSSL. Failure never publishes an unsigned fallback. |
| `--keep-intermediates` | No | `false` | Retain private workspace and stage logs on success or failure. |
| Global `--debug` | No | Disabled | Enables SNP guest debugging **and** diagnostic logging, regardless of the bundle's debug setting. |
| Global `--verbose` | No | Disabled | Logging only; does not enable guest debugging. |

Logging flags do not retain intermediates. Returns `igvm` and expected SNP
`measurements`; signing the ID block does not change the expected measurement.

#### Input contents and customization

| Input | Contents and limits |
| --- | --- |
| Dependency triple within the bundle | `kernel/bzImage`, `initrd/base-initrd.cpio.gz`, and `openvmm/snp_bootshim`. The kernel is the L2 guest kernel, not the host kernel. Customers can replace these through a complete compatible bundle today. |
| Tooling/profile within the bundle | `openvmm/igvmfilegen` and `manifests/snp-linux-direct.json`. The tool executes on the build host; it is not OpenVMM. Profile changes must remain compatible with launch's fixed resources/devices and generator interface. |
| Bundle metadata | Required `dependency-manifest.v1.json` maps artifact names to paths/hashes. Schema 1, `measured-pcie-v1`, x86-64, and an x64 measured-PCIe configuration are required. Archives place this at the root or inside one top-level directory. |
| Supplementary bundle records | Default bundle includes `kernel/config`, `kernel/manifest.txt`, the measured-PCIe patch, and `SHA256SUMS`. The CLI checks manifest artifact hashes, not `SHA256SUMS`, and does not apply the patch. |
| Application | One static little-endian x86-64 ELF executable without `PT_INTERP`; not source, a container, or a dependency archive. No compilation, library installation, or arguments are provided by this option. |
| Init script | Owns guest mounts, environment, arguments, and startup. Default mounts guest filesystems and executes `/bin/enclave_app serve-vsock 4050`. An override replaces all of `/init`, not an extra hook. |

Required artifact names are `kernel`, `base_initrd`, `snp_bootshim`,
`igvmfilegen`, and `igvm_manifest`. All manifest-listed artifacts, including
optional records, must exist and match their hashes. Hashes do not authenticate
the publisher or prove boot compatibility; bundled tools must be trusted.

Application, init script, and bundle are independent selections. A custom
bundle does not automatically supply the selected application/script. Extra
guest files currently belong in a custom base initrd; composition overwrites
`/bin/enclave_app` and `/init`. Changed contents can change measurements.

### `az ace launch`

```bash
sudo az ace launch --name demo-1 --igvm "$HOME/images/enclave.igvm"
```

Run as root on Linux L1 with `/dev/mshv` and a compatible runtime. Launch starts
a detached supervisor and OpenVMM child, creates/resumes the VM through local
gRPC, and probes the guest through the vsock relay. Both processes outlive the CLI.

| Parameter | Required | Default | Behavior |
| --- | --- | --- | --- |
| `--name`, `-n` | Yes | None | 1-32 characters; starts with a letter/digit, then letters/digits/underscores/hyphens. Existing instance directories cannot be reused, even after destroy. |
| `--igvm` | Yes | None | Existing compatible IGVM on L1; create output or a customer-provided image. |
| `--openvmm` | No | `openvmm` | Executable path or name resolved through the executable search path. |
| `--state-dir` | No | `/var/lib/ace/instances` | Private root-owned state root with mode `0700`. Symlinked paths are rejected; length must fit Unix socket limits. |
| `--timeout` | No | `120` seconds | Finite positive operation timeout; fractional seconds accepted. |
| `--allow-untested-runtime` | No | `false` | Permit a runtime outside the expected Azure Linux `0.2.0-2.azl3` package; equivalent SNP IGVM RPC and low-ECAM support are still required. |

Only one managed runtime is allowed per L1, regardless of name/state root.
The guard remains held while either runtime process survives. VM resources are
fixed at one CPU and 160 MiB with the supported PCIe/vsock layout.

Readiness connects using `CONNECT 4050`, expects `OK`, and sends newline-delimited
JSON with a generated challenge and `hello world` payload. The response must
contain `evidence.tee: snp` and a hex-encoded 1184-byte `evidence.report`.
**This is shape checking, not signature, measurement, challenge-binding, or
certificate-chain verification. Launch performs no key release.**

Returns instance/status/IGVM details, supervisor PID, sockets, and log/evidence
paths. State under `<state-dir>/<name>` retains `instance.json`, `response.json`
after successful readiness, `openvmm.log`, `supervisor.log`, and `com1.log`.

### `az ace destroy`

```bash
sudo az ace destroy --name demo-1
```

Run as root on the same L1 with the original state root. Destroy tears down the
VM through RPC, quits OpenVMM, finishes owned-process cleanup, and removes owned
sockets. Input IGVM, instance record, logs, and saved evidence remain.

| Parameter | Required | Default | Behavior |
| --- | --- | --- | --- |
| `--name`, `-n` | Yes | None | Existing instance name, using launch's naming rules. |
| `--state-dir` | No | `/var/lib/ace/instances` | Must match launch; same privacy/path constraints. |
| `--timeout` | No | `30` seconds | Finite positive timeout; fractional seconds accepted. Cleanup has additional bounded grace periods. |

Repeated destruction of a confirmed destroyed instance is supported; new launches
require new names. Failed destruction is not proof of shutdown: inspect logs and
retry. Returned socket paths are informational after successful cleanup.
Always destroy before changing the environment/extension. Closing the shell or
deactivating does not stop instances; reboot does not automatically restart them.

### References

- [Installation and usage](README.rst)
- [Arguments](azext_ace/_params.py), [help](azext_ace/_help.py), and [handlers](azext_ace/custom.py)
- [Image builder](azext_ace/_image_builder.py), [host setup](azext_ace/_setup.py), and [lifecycle](azext_ace/_lifecycle.py)
- [Dependency build script](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/ace/scripts/build-igvm-dependencies.sh&version=GBfeature/modular-igvmfile-generation)
- [Application build script](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/ace/scripts/build-enclave-app.sh&version=GBusers/kpostiglione/ace-skr-demo)
