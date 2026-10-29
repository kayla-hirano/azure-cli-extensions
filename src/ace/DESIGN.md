# ACE: Design for broader customer testing

**Status:** Design draft. The current design describes implemented behavior.
The proposed abstracted design and required changes are recommendations for
review, not implemented capabilities or a finalized CLI contract.

## Goal and scope

Evolve ACE from a demo-oriented extension into a tool that customers can use
to test and customize their enclave workloads. Preserve the existing command
baseline, then describe how image creation could accept individual dependency
artifacts without requiring customers to construct a complete dependency bundle.

This draft focuses on dependency selection, image composition, and the changes
needed to support that abstraction. It does not propose compiling dependencies
on every create invocation, arbitrary workload-format support, or new
attestation guarantees.

## Current design

`az ace` builds SNP enclave images and manages local enclave VMs using OpenVMM.
It operates on local files and host processes; no Azure login is required.
It does not provision an Azure VM or manage Azure resources.

The typical workflow is:

1. Install the extension and its Python dependencies.
2. Run `az ace setup` as root on the target L1 host.
3. Run `az ace create` as a normal user to build an IGVM, or supply an existing
   compatible IGVM and skip this step.
4. Run `az ace launch` as root to start the enclave and wait for the expected
   guest response.
5. Run `az ace destroy` as root on the same host when testing is complete.

The [README](README.rst) describes both editable developer installation and
wheel-based testing. With wheel installations, the extension is installed
separately for the normal user and root. With the developer environment, both
use the same extension installation but separate Azure CLI configuration
directories. The examples below assume the intended CLI and extension
installation are already selected; see the README for the required `sudo`
environment when using the developer setup.

### Command summary

| Command | Purpose | Execution context |
| --- | --- | --- |
| `az ace setup` | Validate host prerequisites and install or verify the supported OpenVMM package. | Root on Azure Linux 3.0 L1 with a compatible kernel exposing `/dev/mshv`. |
| `az ace create` | Package an application and guest startup script into an SNP IGVM using a trusted dependency bundle. | Normal user on Linux or WSL with GNU cpio and gzip. No L1 runtime is needed to build an image. |
| `az ace launch` | Start OpenVMM, create and resume a VM from an IGVM, and wait for a compatible guest response. | Root on the Linux L1 host with `/dev/mshv` and a compatible OpenVMM runtime. |
| `az ace destroy` | Stop the VM and runtime and remove owned sockets while retaining diagnostic artifacts. | Root on the same L1 host, using the instance's original state directory. |

### `az ace setup`

```bash
sudo az ace setup [--yes]
```

Setup validates Azure Linux 3.0 and the `/dev/mshv` character device. If
OpenVMM is absent, it prompts before installing `openvmm-0.2.0-2.azl3` using
`tdnf` and the host's configured repositories. It verifies the supported
runtime's RPM version and integrity, including for existing installations.
An incompatible or modified installation fails verification and requires
manual repair.

| Parameter | Required | Default | Current behavior |
| --- | --- | --- | --- |
| `--yes`, `-y` | No | `false` | Approve OpenVMM package installation without an interactive prompt. Host and runtime validation still apply. |

The result includes readiness status, OS, kernel version, and OpenVMM path.
Readiness here means host prerequisites passed, not that an enclave boot or
attestation was verified. Setup does not install the extension or its Python
dependencies, install developer tools, change the kernel, add repositories,
reboot, or start OpenVMM.

### `az ace create`

```bash
az ace create \
    --workspace "$HOME/ace-build" \
    --output-file "$HOME/images/enclave.igvm"
```

Create composes an image from prebuilt inputs; it does not compile an
application or dependencies, install build tools, or launch a VM.

| Parameter | Required | Default | Current behavior |
| --- | --- | --- | --- |
| `--workspace` | Yes | None | Parent directory for a private build workspace. The parent may already exist. |
| `--output-file` | Yes | None | Destination IGVM filename. Existing output files are never overwritten. |
| `--dependency-bundle` | No | Packaged `dependencies/dependencies.tar.gz` | Select a trusted `measured-pcie-v1` tar/tar.gz archive or existing bundle directory. Archives must contain `dependency-manifest.v1.json` and referenced artifacts at the root or within one top-level directory. |
| `--application` | No | Packaged `dependencies/enclave_app` | Supply a static x86-64 ELF binary. It is installed in the guest as `/bin/enclave_app`. |
| `--init-script` | No | Packaged `dependencies/initramfs/enclave-init` | Override the guest `/init` script. The script must have a shebang and LF line endings and start the application appropriately. |
| `--signing-key` | No | No signing key | Sign the SNP ID block using an unencrypted PEM ECDSA P-384 private key. Requires OpenSSL. Signing failure does not publish an unsigned fallback. |
| `--keep-intermediates` | No | `false` | Retain the private build workspace and stage logs on success or failure. |

Two Azure CLI global logging flags are particularly important for this command:

| Global flag | Current effect on create |
| --- | --- |
| `--debug` | Enables both SNP guest debugging in the generated image and diagnostic CLI logs. Guest debugging is otherwise disabled, regardless of the bundle setting. |
| `--verbose` | Changes logging only; it does not enable guest debugging. |

Neither logging flag retains the build workspace by itself; use
`--keep-intermediates` for retention.

The dependency bundle supplies the kernel, base initrd, SNP boot shim,
`igvmfilegen`, guest manifest, and artifact hashes. The default init script
mounts guest filesystems and starts `/bin/enclave_app serve-vsock 4050`.
A custom application may require a matching `--init-script`; replacing the
binary alone does not make an arbitrary workload compatible with launch.

The command returns the final IGVM path in `igvm` and expected SNP launch
measurements in `measurements`. Adding a signed SNP ID block does not change
the expected launch measurement.

### `az ace launch`

```bash
sudo az ace launch \
    --name demo-1 \
    --igvm "$HOME/images/enclave.igvm"
```

Launch accepts an image produced by create or another compatible IGVM. It
starts a detached Python supervisor, which starts OpenVMM as its child.
The CLI creates and resumes the VM through local gRPC, then probes the guest
application through OpenVMM's vsock relay. The supervisor and OpenVMM
continue running after the CLI exits.

| Parameter | Required | Default | Current behavior |
| --- | --- | --- | --- |
| `--name`, `-n` | Yes | None | Local instance name, 1-32 characters. Must start with a letter or digit; remaining characters may also include underscores or hyphens. An existing instance directory cannot be reused, even after destroy. |
| `--igvm` | Yes | None | Path to an existing compatible IGVM on the L1 host. |
| `--openvmm` | No | `openvmm` | OpenVMM executable path or command name. The default resolves through the executable search path. |
| `--state-dir` | No | `/var/lib/ace/instances` | Root directory for per-instance state. ACE requires private, root-owned directories with mode `0700`, rejects symlinked state paths, and limits path length to fit Unix socket paths. |
| `--timeout` | No | `120` seconds | Launch operation timeout. Must be a finite positive number; fractional seconds are accepted. |
| `--allow-untested-runtime` | No | `false` | Opt out of requiring the expected Azure Linux `0.2.0-2.azl3` runtime package. An alternative runtime must still provide equivalent SNP IGVM RPC and low-ECAM support. This is not a general compatibility guarantee. |

Launch uses a fixed CPU, memory, PCIe, and vsock configuration;
these are not exposed as workload configuration parameters.
Only one ACE-managed enclave may be active per L1 host, regardless of instance
name or state directory. The host-wide guard remains held while either
OpenVMM or its supervisor survives.

Guest readiness is tied to the demo application protocol:

1. Connect to the guest relay and request `CONNECT 4050`.
2. After an `OK` response, send newline-delimited JSON containing a generated
   challenge and the fixed payload `hello world`.
3. Expect a JSON response with `evidence.tee` equal to `snp` and
   `evidence.report` containing a hex-encoded 1184-byte report.

**Successful launch is not verified attestation.** ACE checks the report's
shape, not its signature, launch measurement, challenge binding, or certificate
chain. It performs no key release.

The returned instance details include name, status, IGVM information, vsock
port and relay socket, RPC socket, supervisor PID, and log/evidence paths.
After successful readiness, the guest response is saved as `response.json`
under `<state-dir>/<name>`. The instance directory also contains
`instance.json`, `openvmm.log`, `supervisor.log`, and `com1.log`.

### `az ace destroy`

```bash
sudo az ace destroy --name demo-1
```

Destroy tears down the VM through RPC, quits OpenVMM, finishes owned-process
cleanup, and removes owned sockets. It retains the input IGVM, instance
record, logs, and any saved guest response.

| Parameter | Required | Default | Current behavior |
| --- | --- | --- | --- |
| `--name`, `-n` | Yes | None | Name of the existing local instance, with the same naming rules as launch. |
| `--state-dir` | No | `/var/lib/ace/instances` | State root used at launch. A custom root must be supplied again; the same privacy and path constraints apply. |
| `--timeout` | No | `30` seconds | Teardown operation timeout. Must be a finite positive number; cleanup has additional bounded grace periods. |

Repeated destruction of a confirmed destroyed instance is supported.
A failed destroy is not proof that the VM stopped; inspect retained logs
and retry. A subsequent launch requires a new instance name.
Returned socket paths are informational after successful destruction because
the sockets have been removed.

Leaving the shell or deactivating the environment does not stop a running
instance. Always destroy instances before replacing their extension or
environment. A host reboot does not automatically restart them.

### Dependency production and image composition

The existing flow separates dependency production from application image creation:

```text
Pinned dependency sources + build tooling
    -> dependency build script
    -> bundle of prebuilt artifacts, configuration, and hashes

Default or customer-provided bundle + application + init script
    -> az ace create
    -> composed initrd + IGVM + expected launch measurements
```

The referenced
[build-igvm-dependencies.sh](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/ace/scripts/build-igvm-dependencies.sh&version=GBfeature/modular-igvmfile-generation)
builds the x86-64 measured-PCIe dependency baseline, not the application or final
IGVM. It clones pinned OpenVMM and openvmm-deps revisions, or accepts clean local
checkouts at those revisions. It applies the measured-PCIe patch, builds the SNP
boot shim and `igvmfilegen` using Rust/Cargo, and builds the guest kernel and base
initrd using Docker. It then stages the artifacts, generates a dependency
manifest and checksums, and publishes a bundle directory.

The script does **not** currently accept prebuilt kernel, boot-shim, or initrd
paths. It also depends on companion scripts, a patch, an IGVM configuration,
and shared repository logging code; it is not a standalone artifact packer
that create can invoke unchanged.

The CLI consumes the resulting artifact contract rather than invoking this
source-build script. Its dependency reader requires a kernel, base initrd,
SNP boot shim, `igvmfilegen`, and IGVM configuration. It validates the bundle's
profile, architecture, artifact hashes, and measured-PCIe configuration.
Create composes an initrd with the application and startup script, passes the
selected resources to `igvmfilegen`, and reads the expected SNP measurement
before publishing the IGVM.

### What the create inputs contain and what customers can customize

`--dependency-bundle`, `--application`, and `--init-script` are independent
inputs. Keeping their sources in the same public repository does not mean
they must be delivered through the same parameter.

#### `--dependency-bundle`: boot dependencies, tooling, and configuration

The parameter accepts a local bundle directory or tar/tar.gz archive, not a
source checkout or a single kernel file. The current packaged bundle contains
the following files. These are the default paths; the dependency manifest maps
artifact names to bundle-relative paths and their SHA-256 hashes.

| Component | Packaged path | Purpose and customization |
| --- | --- | --- |
| Guest kernel | `kernel/bzImage` | Linux kernel running inside the enclave. Customers can supply a compatible prebuilt kernel in a custom bundle. This is separate from the L1 host kernel/MSHV driver. |
| Base initrd | `initrd/base-initrd.cpio.gz` | Compressed cpio archive providing the initial guest filesystem. Customers can change its files and guest utilities. Create subsequently installs the selected application and replaces `/init`. |
| SNP boot shim | `openvmm/snp_bootshim` | Guest boot component used by the SNP Linux-direct image. Customers can replace it with a compatible prebuilt version. |
| Image-generation tool | `openvmm/igvmfilegen` | Host executable that builds the IGVM and measurement sidecars; it is not the OpenVMM runtime or guest application. Custom bundles can replace it, but it must support the builder's command and output contract. |
| IGVM configuration | `manifests/snp-linux-direct.json` | Describes the guest image, SNP settings, kernel command line, CPU/memory settings, and measured PCIe layout. Customers can supply compatible configuration, but launch still uses its fixed runtime resources and topology; editing the manifest does not reconfigure launch. Create explicitly sets guest debugging from its `--debug` flag. |
| Kernel build records | `kernel/config`, `kernel/manifest.txt` | Record kernel configuration/build information. Included in the default bundle, but not required artifact names for the current CLI dependency reader. |
| Tooling patch record | `patches/openvmm-igvmfilegen-measured-pcie.patch` | Records the measured-PCIe tooling patch. It is not applied or compiled by create. Included in the default bundle, but not a required artifact name for the dependency reader. |
| Dependency manifest | `dependency-manifest.v1.json` | Required schema/profile/architecture metadata and artifact path/hash inventory. Customers rebuilding or replacing artifacts must update the inventory and hashes. |
| Checksum inventory | `SHA256SUMS` | Additional checksum file produced by the dependency build pipeline. The CLI validates artifacts using the hashes in `dependency-manifest.v1.json`, not this file. |

The five required artifact names are `kernel`, `base_initrd`, `snp_bootshim`,
`igvmfilegen`, and `igvm_manifest`. Every artifact listed in the dependency
manifest, including supplementary records if present, must exist and match
its recorded hash. The current reader requires schema version 1,
`measured-pcie-v1`, x86-64 artifacts, and an x64 IGVM configuration with a
measured PCIe host bridge.

Today, customers customize these components by preparing a complete compatible
bundle and passing it to `--dependency-bundle`; individual kernel, boot-shim,
and base-initrd options do not exist. Hash validation detects mismatches but
does not authenticate a customer's bundle or prove its components will boot
together. The bundle includes executable tooling and must be trusted.

#### `--application`: the customer workload executable

This parameter accepts **one prebuilt static x86-64 ELF executable**, not source
code, a source directory, a container image, or an application dependency
archive. The builder checks for a little-endian ELF64 x86-64 executable without
a dynamic interpreter (`PT_INTERP`); this is a format check, not a full runtime
compatibility guarantee.

The selected binary is copied into the composed guest filesystem as
`/bin/enclave_app`, regardless of its original filename. It replaces the default
sample application, not the kernel, boot shim, or image-generation tool. The
option does not compile code, install libraries, or specify startup arguments.
Customers needing other guest files currently include them in a custom base
initrd supplied through the bundle.

The default `enclave_app` will live in the public dependencies repository, but
it is currently a separate packaged file rather than an artifact selected from
`dependencies.tar.gz`. Selecting a custom bundle does not automatically select
an application or init script from that bundle.

**How the referenced sample is built:** The
[build-enclave-app.sh](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/ace/scripts/build-enclave-app.sh&version=GBusers/kpostiglione/ace-skr-demo)
script runs Cargo against the ACE workspace to build the `enclave_app` package
with `--release --locked`. Its default target is
`x86_64-unknown-linux-musl`, overridable through `TARGET`, and it prints the
resulting binary path. It does not build the kernel, boot shim, base initrd,
or final IGVM. The lockfile fixes dependency resolution, but reproducing the
binary also requires controlling the toolchain and build environment.

The script is a build wrapper, not the application itself. In the referenced
sample source, `enclave_app` combines workload logic, HTTP/vsock request handling,
attestation evidence collection, and key-release/signing functionality.
The workspace already contains an `attested-protocol` crate with message types
and report-binding helpers. These source-level components are compiled into
the application; they are not separately selectable CLI inputs today.
The sample's key-release functionality does not mean `az ace launch` performs
key release or cryptographic attestation verification.

#### `--init-script`: guest initialization and application startup

The existing option replaces the entire guest `/init` with a customer-supplied
shebang script using LF line endings. The default script mounts guest
filesystems and runs `/bin/enclave_app serve-vsock 4050`. Customers can change
startup arguments, environment variables, and guest initialization through
their script, while supplying any required guest utilities separately.

All three inputs must remain compatible with the current launch behavior:
one virtual CPU, 160 MiB of memory, the configured PCIe/vsock layout, and the
port-4050 JSON readiness exchange returning an SNP-shaped report. Replacing
an application or startup script does not make launch generic. Changes to guest
image contents/configuration can change the expected launch measurement.

## Proposed component boundaries and required changes

The following recommendations describe how to expose smaller customer-controlled
components behind `--dependency-bundle` and `--application`. They are not new
CLI capabilities or resolutions of the open build/distribution questions below.

### Dependency customization behind `--dependency-bundle`

Separate **dependency production**, **artifact selection**, and **image
composition**. Keep source compilation in a documented build/release workflow.
Create can accept compatible prebuilt artifacts without also running the full
Docker/Rust dependency source-build pipeline.

| Component group | Proposed customer control | Required change |
| --- | --- | --- |
| Guest kernel, base initrd, and SNP boot shim | Replace selected prebuilt guest components while retaining compatible defaults for the rest. | Introduce per-artifact inputs and an explicit resolution/validation contract so customers need not reconstruct a complete bundle for one replacement. No such individual options exist today. |
| Image-generation tool and IGVM profile | Use a tested default tool/profile pair, with coordinated advanced replacement through a complete custom bundle. | Define and version the supported tool, image, and runtime compatibility contract. Do not treat arbitrary device-layout changes as compatible with launch. |
| Manifest, hashes, and provenance | Inspect the resolved input set without manually maintaining every internal packaging detail. | Stage selected artifacts privately and generate a resolved manifest. Preserve default provenance separately from customer overrides; computed hashes alone do not authenticate inputs. |

A bundle can remain the internal packaging format and the advanced full-set
override. The customization benefit comes from selecting its components,
not simply replacing an archive with loose files. Default distribution
(packaged versus downloaded) remains a separate decision.

### Application customization behind `--application`

**Recommended starting point:** Keep one final static executable, but separate
its source into reusable libraries and replaceable customer workload code.
Customers should not have to adopt the entire sample merely to reuse its
transport or evidence helpers.

| Component | Current sample responsibility | Proposed customer control and required change |
| --- | --- | --- |
| Workload handler | Payload hashing and signed, attested-hash responses. | Extract a handler boundary so customers replace the sample computation with their own workload. Specify request/result and error contracts. |
| Transport and dispatch | HTTP endpoints, newline-delimited JSON over vsock, and operation dispatch. | Provide a reusable default adapter while allowing a customer-owned transport. Separate transport code from workload operations. |
| Attestation helpers | Device access for SNP/TDX evidence and application-data binding into report data. | Extract reusable evidence helpers with a documented binding contract. Do not confuse evidence collection with verification or imply that the CLI supports every sample evidence backend. |
| Key-release/signing sample | Transport-key creation, released-key unwrapping, and JWT signing in `skr.rs`. | Make this an optional module/sample rather than a requirement for every workload. Keep key ownership and cryptographic binding explicit. |
| Startup | Application command modes, with the packaged init script selecting `serve-vsock 4050`. | Preserve `--init-script` for arguments, environment, and guest initialization, independently of workload logic. |
| Build recipe | A wrapper building one Cargo package for a default musl target. | Publish the sample source, lockfile, toolchain requirements, and build recipe in the public dependency/sample repository. Customers own their source/build pipeline and pass the resulting compatible executable to create. |

Two customer paths follow from this design: adapt the sample by replacing its
handler and reusing supported helpers, or bring an independently built compatible
application with a matching init script. Rust libraries are an optional
convenience for the first path, not a requirement for all customer applications.
The existing `attested-protocol` crate is a starting point for the reusable
contract, not proof that all of these boundaries are already implemented.

**Required CLI integration:** Application protocol customization must be paired
with a readiness design. The current launch probe requires the sample's
port-4050 challenge/payload exchange and SNP-shaped response. Either retain that
contract through a compatible adapter or introduce a separate configurable
readiness mechanism before claiming arbitrary workload-protocol support.

This recommendation does not require create to compile customer code. Splitting
the sample into multiple guest processes is also not required initially; that
would additionally need multi-file packaging, process supervision, IPC, and
explicit evidence/key ownership across processes. Component changes that alter
image contents require regenerating expected launch measurements.

## Current-state references

- [README and workflows](README.rst)
- [Command registration](azext_ace/commands.py)
- [Parameter registration](azext_ace/_params.py)
- [CLI help](azext_ace/_help.py)
- [Handlers, defaults, and returned results](azext_ace/custom.py)
- [Host setup](azext_ace/_setup.py)
- [Launch, guest readiness, and teardown](azext_ace/_lifecycle.py)
- [Instance validation and locking](azext_ace/_instance.py)
- [Image composition and dependency validation](azext_ace/_image_builder.py)
- [Dependency source-build script](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/ace/scripts/build-igvm-dependencies.sh&version=GBfeature/modular-igvmfile-generation)
- [Sample application build script](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/ace/scripts/build-enclave-app.sh&version=GBusers/kpostiglione/ace-skr-demo)
- [Sample application source](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/ace/enclave_app/src/main.rs&version=GBusers/kpostiglione/ace-skr-demo)
- [Shared sample protocol](https://msazure.visualstudio.com/One/_git/ACC-CVM-Tooling?path=/src/ace/protocol/src/lib.rs&version=GBusers/kpostiglione/ace-skr-demo)

## Open questions

### 1. How should default dependencies be distributed?

Should the defaults currently under `azext_ace/dependencies` be packaged with
the extension or retrieved from the public dependency/sample repository?

**Initial preference:** Retrieving them from the repository may make future
updates easier to manage.

### 2. What does the dependency triple include?

Does the OneNote item, "Dependency triple should be built and packaged into the
CLI," refer to the same artifacts we intend to store in the sample repository,
or a different set of dependencies?

### 3. Where should dependency and application builds happen?

`az ace create` accepts `--dependency-bundle` and `--application`, each with
specific format and content requirements. Reproducing compatible inputs may be
difficult without the scripts currently in `ACC-CVM-Tooling`.

Should those scripts move to the sample repository so customers can build their
inputs independently, or should create perform those builds? If builds become
part of create, the extension would need to pull all required scripts and
supporting files, with their build-tool prerequisites documented.

### 4. What responsibilities should create own?

Related to question 3, should create only compose an IGVM from prebuilt inputs,
or also build the dependency bundle, compile the enclave application, and
configure the guest? Define the boundary between dependency production,
application compilation, and image composition.

### 5. Should launch and destroy remain largely unchanged?

Can the abstraction work focus mainly on create while preserving the lifecycle
interface? Launch currently requires an IGVM and an instance name; destroy
requires the instance name and the original state root if customized.

Confirm whether the current launch configuration and guest-readiness contract
remain suitable for the intended customer workloads.
