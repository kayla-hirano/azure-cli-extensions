# ---------------------------------------------------------------------
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# ---------------------------------------------------------------------

"""Compose an application IGVM from a trusted dependency-builder bundle.

Requires Linux, GNU cpio, gzip, and the bundle's executable igvmfilegen.
Optional SNP ID-block signing also requires OpenSSL and a caller-owned key.
The bundle contains executable code and is trusted, not a sandboxed input.
There is no dependency on the repository's scripts or CLI implementation.
"""

import errno
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import tempfile
from typing import BinaryIO, Dict, List, NamedTuple, Optional, Sequence, Tuple
from ._isolation import IsolationConfiguration, load_isolation, validate_configuration


__all__ = ["IgvmBuildError", "IgvmBuildResult", "SnpBuildOptions", "build_igvm", "validate_build_configuration"]


@dataclass(frozen=True)
class SnpBuildOptions:
    """Caller-selected SNP build settings, separate from packaged configuration."""

    signing_key: Optional[Path] = None


class IgvmBuildResult(NamedTuple):
    """Published image and expected measurements, not attestation evidence."""

    igvm: Path
    measurements: Dict[str, str]


class IgvmBuildError(RuntimeError):
    """A failed build stage, with a retained or residual workspace when available."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        workspace: Optional[Path] = None,
    ) -> None:
        super().__init__("{}: {}".format(stage, message))
        self.stage = stage
        self.workspace = workspace


def _inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _input_file(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError("Expected a nonempty regular file: {}".format(path))
    return resolved


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object: {}".format(path))
    return value


def _snp_measurement(image: Path) -> str:
    document = _read_json(_input_file(
        image.with_name(image.stem + "-snp.json")))
    series = document.get("series")
    if (not isinstance(series, list) or len(series) != 1
            or not isinstance(series[0], dict)):
        raise ValueError("Expected one SNP launch measurement")
    reference = series[0].get("reference")
    digest = reference.get("snp_ld") if isinstance(reference, dict) else None
    if (not isinstance(digest, str) or len(digest) != 96
            or len(bytes.fromhex(digest)) != 48):
        raise ValueError(
            "Expected a 48-byte hexadecimal SNP launch measurement")
    return digest


def _extract_bundle(archive_path: Path, destination: Path) -> Path:
    """Unpack regular files only, without trusting archive paths or metadata."""
    destination.mkdir(mode=0o700)
    with tarfile.open(str(archive_path), mode="r:*") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            if (relative.is_absolute() or ".." in relative.parts
                    or "\\" in member.name or ":" in member.name):
                raise ValueError("Unsafe dependency archive path: {}".format(
                    member.name))
            if not member.isdir() and not member.isfile():
                raise ValueError(
                    "Dependency archives may contain only regular files "
                    "and directories: {}".format(member.name))
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Cannot read dependency archive member: {}"
                                 .format(member.name))
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    manifest = "dependency-manifest.v1.json"
    if (destination / manifest).is_file():
        return destination
    children = list(destination.iterdir())
    if (len(children) == 1 and children[0].is_dir()
            and (children[0] / manifest).is_file()):
        return children[0]
    raise ValueError(
        "Dependency archive must contain dependency-manifest.v1.json "
        "at its root or inside one top-level directory")


def _recipe(path: Path, configuration: IsolationConfiguration) -> dict:
    """Load a single-guest recipe and validate its architecture and isolation-specific shape."""
    config = _read_json(path)
    architecture = config.get("guest_arch")
    if architecture != "x64":
        raise ValueError("IGVM recipe architecture is not implemented: {}".format(architecture))
    guests = config.get("guest_configs")
    if (not isinstance(guests, list) or len(guests) != 1
            or not isinstance(guests[0], dict)):
        raise ValueError("Expected one IGVM guest configuration")
    if configuration.isolation_type == "snp":
        _snp_recipe(guests[0])
    else:
        raise ValueError("IGVM recipe handling is not implemented for isolation type: " + configuration.isolation_type)
    return config


def _snp_recipe(guest: dict) -> None:
    """Require SNP Linux-direct resource fields."""
    isolation = guest.get("isolation_type")
    if (not isinstance(isolation, dict) or set(isolation) != {"snp"}
            or not isinstance(isolation["snp"], dict)):
        raise ValueError("IGVM manifest is missing SNP isolation settings")
    image = guest.get("image")
    if not isinstance(image, dict) or set(image) != {"snp_linux_direct"}:
        raise ValueError("Expected an SNP Linux-direct IGVM recipe")
    linux = image["snp_linux_direct"]
    if not isinstance(linux, dict):
        raise ValueError("Expected an SNP Linux-direct IGVM recipe")
    bridge = linux.get("pcie_host_bridge")
    fields = {
        "segment", "start_bus", "end_bus", "ecam_base",
        "low_mmio_base", "low_mmio_size", "high_mmio_base", "high_mmio_size",
    }
    if (not isinstance(bridge, dict) or set(bridge) != fields
            or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in bridge.values())):
        raise ValueError("IGVM manifest has missing or invalid measured PCIe settings")
    for field in ("processor_count", "memory_page_count"):
        value = linux.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("IGVM manifest has invalid " + field)


def _dependencies(bundle: Path, configuration: Optional[IsolationConfiguration] = None) -> Dict[str, Path]:
    """Resolve bundle artifacts after checking the support contract, paths, hashes, tools, and recipe."""
    configuration = configuration if configuration is not None else load_isolation()
    contract = configuration.dependency_bundle
    manifest = _read_json(_input_file(
        bundle / "dependency-manifest.v1.json"))
    version = manifest.get("schema_version")
    if (not isinstance(version, int) or isinstance(version, bool)
            or version != contract.schema_version
            or manifest.get("profile") != contract.profile
            or manifest.get("guest_arch") != configuration.supported_configuration.architecture):
        raise ValueError(
            "Expected schema {}, {}, {} dependencies".format(
                contract.schema_version, contract.profile, configuration.supported_configuration.architecture))

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("Dependency manifest has no artifacts object")
    if not set(contract.required_artifacts).issubset(artifacts):
        raise ValueError("Dependency manifest is missing required artifacts")

    paths = {}
    for name, artifact in artifacts.items():
        if (not isinstance(artifact, dict)
                or not isinstance(artifact.get("path"), str)
                or not isinstance(artifact.get("sha256"), str)):
            raise ValueError("Invalid dependency artifact: {}".format(name))
        relative = Path(artifact["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Artifact path must be bundle-relative: {}"
                             .format(relative))
        path = _input_file(bundle / relative)
        if not _inside(path, bundle):
            raise ValueError("Artifact escapes dependency bundle: {}"
                             .format(relative))
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda source=source: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != artifact["sha256"]:
            raise ValueError("Dependency checksum mismatch: {}".format(name))
        paths[name] = path

    if not os.access(paths["igvmfilegen"], os.X_OK):
        raise ValueError(
            "Dependency igvmfilegen is not executable; check file permissions or select another --workspace.")
    _recipe(paths["igvm_manifest"], configuration)
    return paths


def validate_build_configuration(
    configuration: IsolationConfiguration, *, isolation_options: Optional[SnpBuildOptions] = None,
    enable_guest_debug: bool = False,
) -> SnpBuildOptions:
    """Validate support and resolve type-specific defaults before reading inputs."""
    if isolation_options is not None and not isinstance(isolation_options, SnpBuildOptions):
        raise ValueError("Unsupported isolation build options: " + type(isolation_options).__name__)
    if configuration.isolation_type == "snp":
        validate_configuration(configuration)
        return isolation_options if isolation_options is not None else SnpBuildOptions()
    if isolation_options is not None and isolation_options.signing_key is not None:
        raise ValueError("SNP ID-block signing is not supported for isolation type: " + configuration.isolation_type)
    if enable_guest_debug:
        raise ValueError(
            "Guest debug configuration is not implemented for isolation type: " + configuration.isolation_type)
    if isolation_options is not None:
        raise ValueError("SNP build options are not supported for isolation type: " + configuration.isolation_type)
    raise ValueError("Image creation is not implemented for isolation type: " + configuration.isolation_type)


def _generation_inputs(
    dependencies: Dict[str, Path], initrd: Path, configuration: IsolationConfiguration,
    enable_guest_debug: bool,
) -> Tuple[dict, dict]:
    """Return a private recipe with the debug override and isolation-specific generator resource paths."""
    config = _recipe(dependencies["igvm_manifest"], configuration)
    if configuration.isolation_type == "snp":
        guest = config["guest_configs"][0]
        guest["isolation_type"]["snp"]["enable_debug"] = enable_guest_debug
        resources = {
            "linux_kernel": str(dependencies["kernel"]),
            "linux_initrd": str(initrd),
            "snp_bootshim": str(dependencies["snp_bootshim"]),
        }
    else:
        raise ValueError(
            "IGVM generation inputs are not implemented for isolation type: " + configuration.isolation_type)
    return config, {"resources": resources}


def _measurements(image: Path, isolation_type: str) -> Dict[str, str]:
    """Read generator measurements through the selected isolation implementation."""
    if isolation_type == "snp":
        return {"snp": _snp_measurement(image)}
    raise ValueError("Image measurements are not implemented for isolation type: " + isolation_type)


def _validate_application(application: Path) -> None:
    with application.open("rb") as source:
        header = source.read(64)
        if (len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01"):
            raise ValueError("Application must be a little-endian ELF64 file")
        fields = struct.unpack("<16sHHIQQQIHHHHHH", header)
        if fields[1] not in (2, 3) or fields[2] != 62 or fields[3] != 1:
            raise ValueError("Application must be an x86-64 ELF executable")
        offset, header_size, entry_size, count = (
            fields[5], fields[8], fields[9], fields[10])
        size = application.stat().st_size
        if (header_size != 64 or entry_size != 56
                or count in (0, 65535) or offset < 64
                or offset + entry_size * count > size):
            raise ValueError("Malformed ELF program-header table")
        loadable = False
        for index in range(count):
            source.seek(offset + index * entry_size)
            program = struct.unpack("<IIQQQQQQ", source.read(entry_size))
            if program[0] == 3:
                raise ValueError(
                    "Application has PT_INTERP; supply a static executable")
            if program[2] + program[5] > size:
                raise ValueError("ELF segment extends past the application")
            loadable = loadable or program[0] == 1
        if not loadable:
            raise ValueError("Application has no loadable ELF segment")


def _run(
    argv: Sequence[str],
    stage: str,
    work: Path,
    *,
    cwd: Optional[Path] = None,
    stdin: Optional[BinaryIO] = None,
    stdout: Optional[BinaryIO] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    log = work / (stage + ".log")
    if logger is not None:
        logger.info("Stage: %s", stage)
        logger.debug("Stage log: %s", log)
    environment = os.environ.copy()
    environment["TMPDIR"] = str(work)
    with log.open("wb") as output:
        with subprocess.Popen(
            argv, cwd=str(cwd or work), env=environment,
            stdin=stdin if stdin is not None else subprocess.DEVNULL,
            stdout=stdout if stdout is not None else output,
            stderr=output, start_new_session=True,
        ) as process:
            try:
                status = process.wait()
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
    if status:
        with log.open("rb") as source:
            source.seek(max(0, log.stat().st_size - 4096))
            detail = source.read().decode("utf-8", errors="replace").strip()
        raise IgvmBuildError(
            "Command exited with status {}: {}\n{}".format(
                status, argv[0], detail), stage=stage)


def _archive_paths(root: Path) -> List[bytes]:
    paths = [b"."]
    for directory, directories, files in os.walk(root):
        for name in directories + files:
            path = Path(directory) / name
            paths.append(os.fsencode(
                "./" + path.relative_to(root).as_posix()))
    return sorted(paths)


def _compose_initrd(
    base: Path, application: Path, init: Path, work: Path, epoch: int,
    logger: Optional[logging.Logger],
) -> Path:
    archive = work / "base.cpio"
    with archive.open("wb") as output:
        _run(["gzip", "-dc", "--", str(base)], "unpack-gzip", work,
             stdout=output, logger=logger)
    root = work / "rootfs"
    root.mkdir()
    with archive.open("rb") as source:
        _run(
            ["cpio", "--extract", "--make-directories", "--unconditional",
             "--no-absolute-filenames", "--no-preserve-owner", "--quiet"],
            "unpack-cpio", work, cwd=root, stdin=source, logger=logger)

    if logger is not None:
        logger.info("Installing application and init script")
    for source, relative in (
        (application, "bin/enclave_app"), (init, "init"),
    ):
        destination = root / relative
        if not _inside(destination.parent.resolve(), root):
            raise IgvmBuildError(
                "Overlay directory escapes rootfs: {}".format(relative),
                stage="compose-initrd")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            destination.unlink()
        shutil.copyfile(source, destination)
        destination.chmod(0o755)

    for relative in ("bin/appliance", "appliance"):
        legacy = root / relative
        if not _inside(legacy.parent.resolve(), root):
            raise IgvmBuildError(
                "Legacy application directory escapes rootfs",
                stage="compose-initrd")
        if legacy.exists() or legacy.is_symlink():
            legacy.unlink()

    paths = _archive_paths(root)
    for relative in paths:
        os.utime(root / os.fsdecode(relative),
                 (epoch, epoch), follow_symlinks=False)
    listing = work / "archive-files"
    listing.write_bytes(b"\0".join(paths) + b"\0")
    composed = work / "application.cpio"
    with listing.open("rb") as source, composed.open("wb") as output:
        _run(
            ["cpio", "--null", "--create", "--format=newc",
             "--owner=0:0", "--renumber-inodes", "--ignore-devno", "--quiet"],
            "pack-cpio", work, cwd=root, stdin=source, stdout=output,
            logger=logger)
    result = work / "application-initrd.cpio.gz"
    with result.open("wb") as output:
        _run(["gzip", "-n", "-9", "-c", "--", str(composed)],
             "pack-gzip", work, stdout=output, logger=logger)
    return result


def _sign_snp_id_block(
    generator: Path, image: Path, key: Path, work: Path,
    logger: Optional[logging.Logger],
) -> Path:
    """Sign the SNP ID block and return a new image embedding its signature and public key."""
    payload = _input_file(image.with_name(image.stem + "-snp.idblock"))
    public_key = work / "id-public-key.pem"
    signature = work / "id-signature.der"
    signed = work / "image-signed.igvm"
    _run(
        ["openssl", "pkey", "-in", str(key), "-passin", "pass:",
         "-pubout", "-out", str(public_key)],
        "export-id-public-key", work, logger=logger)
    _input_file(public_key)
    _run(
        ["openssl", "dgst", "-sha384", "-sign", str(key),
         "-passin", "pass:", "-out", str(signature), str(payload)],
        "sign-id-block", work, logger=logger)
    _input_file(signature)
    _run(
        [str(generator), "add-snp-id-block",
         "--input", str(image), "--output", str(signed),
         "--id-block", str(payload),
         "--id-signature", str(signature),
         "--id-public-key", str(public_key)],
        "add-snp-id-block", work, logger=logger)
    return signed


def _validate_output_paths(
    bundle: Path, app: Path, init: Path, key: Optional[Path],
    parent: Path, requested_output: Path,
) -> Path:
    if requested_output.exists() or requested_output.is_symlink():
        raise ValueError("Output already exists: {}".format(requested_output))
    result = requested_output.parent.resolve() / requested_output.name
    if (_inside(parent, bundle) or _inside(result, bundle)
            or _inside(parent, result)
            or result in (app, init, key)
            or (key is not None and _inside(parent, key))):
        raise ValueError("Workspace/output overlaps a protected input")
    return result


def _validate_init_and_tools(init: Path) -> int:
    """Validate the init script and shared packaging tools, and return the archive timestamp."""
    with init.open("rb") as source:
        entrypoint = source.read()
    if not entrypoint.startswith(b"#!") or b"\r" in entrypoint:
        raise ValueError("init_script must be a shebang script with LF "
                         "line endings")
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    if not 0 <= epoch <= 0xffffffff:
        raise ValueError("SOURCE_DATE_EPOCH must fit a newc timestamp")
    for command in ("cpio", "gzip"):
        if shutil.which(command) is None:
            raise ValueError("Required command not found: " + command)
    return epoch


def _validate_generated_image(image: Path, stage: str, *, signed: bool = False) -> None:
    if image.is_symlink() or not image.is_file() or image.stat().st_size == 0:
        raise IgvmBuildError(
            "igvmfilegen did not produce a nonempty {}IGVM".format("signed " if signed else ""),
            stage=stage)


def _cleanup_build(work: Optional[Path], staged_output: Optional[Path]) -> None:
    """Remove staging first, retaining the workspace if either cleanup fails."""
    errors = []
    try:
        if staged_output is not None:
            staged_output.unlink()
    except OSError as error:
        errors.append("Cannot remove publication staging file {}: {}".format(staged_output, error))
    if not errors and work is not None:
        try:
            shutil.rmtree(work)
        except OSError as error:
            errors.append("Cannot remove build workspace {}: {}".format(work, error))
        else:
            work = None
    if errors:
        raise IgvmBuildError("Cleanup failed: " + "\n".join(errors), stage="cleanup", workspace=work)


def build_igvm(
    *,
    dependency_bundle: Path,
    application: Path,
    init_script: Path,
    output: Path,
    workspace: Optional[Path] = None,
    keep_intermediates: bool = False,
    enable_guest_debug: bool = False,
    isolation_options: Optional[SnpBuildOptions] = None,
    logger: Optional[logging.Logger] = None,
    configuration: Optional[IsolationConfiguration] = None,
) -> IgvmBuildResult:
    """Build and publish one IGVM without compiling dependencies or changing inputs.

    Requires Linux, cpio/gzip, an ELF64 x86-64 application without PT_INTERP,
    and an LF shebang init script that ultimately executes /bin/enclave_app.
    The trusted dependency bundle may be a directory or tar/tar.gz archive;
    artifact paths and hashes are checked before bundled tools run.

    Packaged metadata defines support and dependency requirements. The bundle's
    recipe owns image settings; launch uses snp.json manifest
    independently. External release validation must keep both compatible.
    Guest debug is overridden to disabled unless enable_guest_debug is true.

    isolation_options defaults to the selected type's settings. SNP signing
    optionally uses an unencrypted PEM P-384 key; it is never copied, and
    signing failures never publish an unsigned fallback.

    Output is never overwritten; its filesystem must support hard links.
    A private build workspace is retained on failure/interruption or when
    keep_intermediates is true. IgvmBuildError.workspace identifies retained
    failure files, if workspace creation was reached.
    Returns the image path and expected measurements, not verified attestation.
    """
    stage = "validate"
    work = None
    staged_output = None
    failure = None
    try:
        configuration = configuration if configuration is not None else load_isolation()
        isolation_options = validate_build_configuration(
            configuration, isolation_options=isolation_options, enable_guest_debug=enable_guest_debug)
        if logger is not None:
            logger.info("Validating dependencies and application")
        if not sys.platform.startswith("linux"):
            raise ValueError("IGVM packaging requires Linux")
        bundle = Path(dependency_bundle).resolve(strict=True)
        app = _input_file(Path(application))
        init = _input_file(Path(init_script))
        key = None
        if configuration.isolation_type == "snp" and isolation_options.signing_key is not None:
            key = _input_file(Path(isolation_options.signing_key))
            if shutil.which("openssl") is None:
                raise ValueError("Required command not found: openssl")
        parent = Path(workspace if workspace is not None else tempfile.gettempdir()).resolve()
        result = _validate_output_paths(bundle, app, init, key, parent, Path(output))
        dependencies = _dependencies(bundle, configuration) if bundle.is_dir() else None
        _validate_application(app)
        epoch = _validate_init_and_tools(init)

        parent.mkdir(parents=True, exist_ok=True)
        stage = "prepare-workspace"
        work = Path(tempfile.mkdtemp(prefix=".enclave-build-", dir=str(parent)))
        if logger is not None:
            logger.debug("Build workspace: %s", work)
        if dependencies is None:
            stage = "extract-dependencies"
            extracted = _extract_bundle(bundle, work / "dependencies")
            stage = "validate-dependencies"
            dependencies = _dependencies(extracted, configuration)
        stage = "compose-initrd"
        initrd = _compose_initrd(
            dependencies["base_initrd"], app, init, work, epoch, logger)

        stage = "generate-igvm"
        manifest = work / "igvm-manifest.json"
        config, resource_config = _generation_inputs(dependencies, initrd, configuration, enable_guest_debug)
        manifest.write_text(json.dumps(config, indent=2) + "\n",
                            encoding="utf-8")
        resources = work / "resources.json"
        resources.write_text(json.dumps(resource_config, indent=2) + "\n", encoding="utf-8")
        generated = work / "image.igvm"
        _run(
            [str(dependencies["igvmfilegen"]), "manifest",
             "--manifest", str(manifest), "--resources", str(resources),
             "--output", str(generated)],
            stage, work, logger=logger)
        _validate_generated_image(generated, stage)

        stage = "read-measurements"
        measurements = _measurements(generated, configuration.isolation_type)

        if configuration.isolation_type == "snp" and key is not None:
            stage = "sign-id-block"
            generated = _sign_snp_id_block(
                dependencies["igvmfilegen"], generated, key, work, logger)
            stage = "add-snp-id-block"
            _validate_generated_image(generated, stage, signed=True)

        stage = "publish"
        result.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".igvm-", suffix=".tmp", dir=str(result.parent),
            delete=False,
        ) as destination:
            staged_output = Path(destination.name)
            with generated.open("rb") as source:
                shutil.copyfileobj(source, destination)
        staged_output.chmod(0o644)
        # link() fails if another caller published this output meanwhile.
        os.link(staged_output, result)
        if logger is not None:
            logger.info("Created IGVM: %s", result)
        return IgvmBuildResult(result, measurements)
    except IgvmBuildError as error:
        failure = error
        error.workspace = work
        raise
    except (OSError, ValueError, UnicodeError, struct.error, tarfile.TarError) as error:
        detail = str(error)
        if isinstance(error, OSError) and error.errno in (
                errno.ENOSPC, errno.EDQUOT, errno.EACCES, errno.ENOEXEC, errno.EROFS):
            detail += "\nCheck build storage space, permissions and execution support; select another --workspace."
        failure = IgvmBuildError(detail, stage=stage, workspace=work)
        raise failure from error
    except BaseException as error:
        failure = error
        raise
    finally:
        retain_work = keep_intermediates or failure is not None
        try:
            if not retain_work and work is not None and logger is not None:
                logger.info("Removing build workspace")
            _cleanup_build(None if retain_work else work, staged_output)
        except IgvmBuildError as error:
            retain_work = True
            error.workspace = work
            if failure is None:
                raise
            failure.args = ("{}\n{}".format(failure, error),)
            if isinstance(failure, IgvmBuildError):
                failure.workspace = error.workspace or failure.workspace
            elif logger is not None:
                logger.error("%s", error)
        finally:
            if retain_work and work is not None:
                (logger if logger is not None else logging.getLogger(__name__)).warning(
                    "Build workspace retained at: %s", work)
