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
from typing import BinaryIO, Dict, List, NamedTuple, Optional, Sequence


__all__ = ["IgvmBuildError", "IgvmBuildResult", "build_igvm"]


class IgvmBuildResult(NamedTuple):
    """Published image and expected measurements, not attestation evidence."""

    igvm: Path
    measurements: Dict[str, str]


class IgvmBuildError(RuntimeError):
    """A failed build stage, with a retained workspace when available."""

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


def _dependencies(bundle: Path) -> Dict[str, Path]:
    manifest = _read_json(_input_file(
        bundle / "dependency-manifest.v1.json"))
    if (type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or manifest.get("profile") != "measured-pcie-v1"
            or manifest.get("guest_arch") != "x86_64"):
        raise ValueError(
            "Expected schema 1, measured-pcie-v1, x86_64 dependencies")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("Dependency manifest has no artifacts object")
    required = {
        "kernel", "base_initrd", "snp_bootshim", "igvmfilegen",
        "igvm_manifest",
    }
    if not required.issubset(artifacts):
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
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != artifact["sha256"]:
            raise ValueError("Dependency checksum mismatch: {}".format(name))
        paths[name] = path

    if not os.access(paths["igvmfilegen"], os.X_OK):
        raise ValueError("Dependency igvmfilegen is not executable")
    config = _read_json(paths["igvm_manifest"])
    guests = config.get("guest_configs")
    if (config.get("guest_arch") != "x64"
            or not isinstance(guests, list) or len(guests) != 1
            or not isinstance(guests[0], dict)):
        raise ValueError("Expected one x64 measured-PCIe guest configuration")
    image = guests[0].get("image")
    linux = image.get("snp_linux_direct") if isinstance(image, dict) else None
    if (not isinstance(linux, dict)
            or not isinstance(linux.get("pcie_host_bridge"), dict)
            or not linux["pcie_host_bridge"]):
        raise ValueError("IGVM manifest is missing measured PCIe settings")
    return paths


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


def _sign_igvm(
    generator: Path, image: Path, key: Path, work: Path,
    logger: Optional[logging.Logger],
) -> Path:
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


def build_igvm(
    *,
    dependency_bundle: Path,
    application: Path,
    init_script: Path,
    workspace: Path,
    output: Path,
    signing_key: Optional[Path] = None,
    debug: bool = False,
    enable_debug: bool = False,
    logger: Optional[logging.Logger] = None,
) -> IgvmBuildResult:
    """Build one IGVM without compiling dependencies or changing inputs.

    ``dependency_bundle`` is a trusted bundle directory or tar/tar.gz archive.
    Archives are extracted into the private workspace. Links, special files,
    duplicate files and unsafe paths are rejected; dependency hashes are
    checked before executing bundled tools.

    ``application`` is an ELF64 x86-64 executable without PT_INTERP (including
    static PIE). This is a basic format check, not a complete ABI verifier.
    ``init_script`` is the caller's LF-terminated shebang script; it owns
    mounts and arguments and must ultimately execute /bin/enclave_app.
    The CLI supplies dependencies/initramfs/enclave-init.
    No app arguments are generic defaults.

    With ``signing_key=None``, no SNP ID block is embedded. Otherwise,
    ``signing_key`` is the caller's PEM ECDSA P-384 private key with no
    passphrase. OpenSSL derives its public key and signs the generated
    .idblock payload with SHA-384; igvmfilegen embeds the payload, DER
    signature, and public key before publication. The key is not copied into
    the workspace or image. Password prompts and key generation are not
    supported. Signing failure never falls back to an unsigned image.

    A private directory under ``workspace`` holds rootfs, initrd, generator
    sidecars, resources, and stage logs. It is removed on success/failure,
    unless ``debug=True``. A retained failure directory is exposed through
    IgvmBuildError.workspace. Timestamps follow SOURCE_DATE_EPOCH, defaulting
    to zero as in the existing packaging script.

    An optional caller-configured ``logger`` receives INFO progress and DEBUG
    log paths. Tool output stays in stage files; no handlers are installed.
    ``debug`` controls workspace retention, not the logging level.
    ``enable_debug`` controls SNP guest debugging, defaulting to disabled
    regardless of the dependency bundle's setting.

    Returns the image path and expected SNP launch measurement from the
    generator's JSON sidecar. This is not an attestation report or
    verification.
    Missing or malformed measurements fail the build before publication.
    Only ``output`` is published. Its parent filesystem must support hard
    links, used to publish a completed file atomically without overwriting.
    Workspace and output may be on different filesystems. No drive is required.
    """
    stage = "validate"
    work = None
    staged_output = None
    failure = None
    try:
        if logger is not None:
            logger.info("Validating dependencies and application")
        if not sys.platform.startswith("linux"):
            raise ValueError("IGVM packaging requires Linux")
        bundle = Path(dependency_bundle).resolve(strict=True)
        app = _input_file(Path(application))
        init = _input_file(Path(init_script))
        key = (_input_file(Path(signing_key))
               if signing_key is not None else None)
        parent = Path(workspace).resolve()
        requested_output = Path(output)
        if requested_output.exists() or requested_output.is_symlink():
            raise ValueError("Output already exists: {}".format(output))
        result = requested_output.parent.resolve() / requested_output.name
        if (_inside(parent, bundle) or _inside(result, bundle)
                or _inside(parent, result)
                or result in (app, init, key)
                or (key is not None and _inside(parent, key))):
            raise ValueError("Workspace/output overlaps a protected input")
        dependencies = _dependencies(bundle) if bundle.is_dir() else None
        _validate_application(app)
        with init.open("rb") as source:
            entrypoint = source.read()
        if not entrypoint.startswith(b"#!") or b"\r" in entrypoint:
            raise ValueError("init_script must be a shebang script with LF "
                             "line endings")
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
        if not 0 <= epoch <= 0xffffffff:
            raise ValueError("SOURCE_DATE_EPOCH must fit a newc timestamp")
        commands = ("cpio", "gzip", "openssl") if key else ("cpio", "gzip")
        for command in commands:
            if shutil.which(command) is None:
                raise ValueError("Required command not found: " + command)

        parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="igvm-build-", dir=str(parent)))
        if logger is not None:
            logger.info("Build workspace: %s", work)
        if dependencies is None:
            stage = "extract-dependencies"
            extracted = _extract_bundle(bundle, work / "dependencies")
            stage = "validate-dependencies"
            dependencies = _dependencies(extracted)
        stage = "compose-initrd"
        initrd = _compose_initrd(
            dependencies["base_initrd"], app, init, work, epoch, logger)

        stage = "generate-igvm"
        manifest = work / "igvm-manifest.json"
        config = _read_json(dependencies["igvm_manifest"])
        isolation = config["guest_configs"][0].get("isolation_type")
        snp = isolation.get("snp") if isinstance(isolation, dict) else None
        if not isinstance(snp, dict):
            raise ValueError("IGVM manifest is missing SNP isolation settings")
        snp["enable_debug"] = enable_debug
        manifest.write_text(json.dumps(config, indent=2) + "\n",
                            encoding="utf-8")
        resources = work / "resources.json"
        resources.write_text(json.dumps({
            "resources": {
                "linux_kernel": str(dependencies["kernel"]),
                "linux_initrd": str(initrd),
                "snp_bootshim": str(dependencies["snp_bootshim"]),
            }
        }, indent=2) + "\n", encoding="utf-8")
        generated = work / "image.igvm"
        _run(
            [str(dependencies["igvmfilegen"]), "manifest",
             "--manifest", str(manifest), "--resources", str(resources),
             "--output", str(generated)],
            stage, work, logger=logger)
        if (generated.is_symlink() or not generated.is_file()
                or generated.stat().st_size == 0):
            raise IgvmBuildError(
                "igvmfilegen did not produce a nonempty IGVM", stage=stage)

        stage = "read-measurements"
        measurements = {"snp": _snp_measurement(generated)}

        if key is not None:
            stage = "sign-id-block"
            generated = _sign_igvm(
                dependencies["igvmfilegen"], generated, key, work, logger)
            stage = "add-snp-id-block"
            if (generated.is_symlink() or not generated.is_file()
                    or generated.stat().st_size == 0):
                raise IgvmBuildError(
                    "igvmfilegen did not produce a nonempty signed IGVM",
                    stage=stage)

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
        if not debug:
            stage = "cleanup"
            if logger is not None:
                logger.info("Removing build workspace")
            shutil.rmtree(work)
            work = None
        stage = "publish"
        # link() fails if another caller published this output meanwhile.
        os.link(staged_output, result)
        if logger is not None:
            logger.info("Created IGVM: %s", result)
        return IgvmBuildResult(result, measurements)
    except IgvmBuildError as error:
        failure = error
        if debug:
            error.workspace = work
        raise
    except (OSError, ValueError, UnicodeError, struct.error, tarfile.TarError) as error:
        failure = IgvmBuildError(
            str(error), stage=stage, workspace=work if debug else None)
        raise failure from error
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            if staged_output is not None:
                staged_output.unlink()
            if work is not None and not debug:
                shutil.rmtree(work)
        except OSError as error:
            detail = "Cleanup failed; inspect workspace {}: {}".format(
                work, error)
            if failure is None:
                raise IgvmBuildError(
                    detail, stage="cleanup", workspace=work) from error
            failure.args = ("{}\n{}".format(failure, detail),)
            if isinstance(failure, IgvmBuildError):
                failure.workspace = work
