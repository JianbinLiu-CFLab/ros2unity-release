#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Purpose: Publish only release-gated Humble, Jazzy, and Lyrical R2FU Windows artifacts.
#
# Modifications by Jianbin Liu:
# - Validates ZIP, checksum, manifest, source provenance, and packaged metadata before upload.
# - Verifies release-critical DLL bytes inside the ZIP against the manifest.

"""Publish a provenance-verified Ros2ForUnity Windows GitHub release."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile


DEFAULT_REPOSITORY = "JianbinLiu-CFLab/ros2-for-unity"
DEFAULT_PLATFORM = "windows_x86_64"
REQUIRED_ROS_DISTROS = ("humble", "jazzy", "lyrical")
RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-[A-Za-z0-9_.-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ARCHIVE_ROOT = "Ros2ForUnity"
R2FU_METADATA_PATH = "metadata_ros2_for_unity.xml"
ROS2CS_METADATA_PATHS = (
    "metadata_ros2cs.xml",
    "Plugins/metadata_ros2cs.xml",
    "Plugins/Windows/x86_64/metadata_ros2cs.xml",
)
RUNTIME_BINARY_HASHES_KEY = "runtimeBinaryHashes"
REQUIRED_RUNTIME_BINARY_HASHES = (
    "Plugins/ros2cs_common.dll",
)


@dataclass(frozen=True)
class ReleaseArtifact:
    """One verified distro-specific ZIP and the source commits it embeds."""

    ros_distro: str
    zip_path: pathlib.Path
    sha256_path: pathlib.Path
    manifest_path: pathlib.Path
    ros2cs_sha: str
    ros2_for_unity_sha: str


def workspace_root() -> pathlib.Path:
    """Return the root of the release workspace containing this script."""
    return pathlib.Path(__file__).resolve().parents[2]


def default_artifact_root() -> pathlib.Path:
    """Return the common parent directory of release ZIPs."""
    return workspace_root() / "artifacts" / "ros2-for-unity"


def artifact_basename(ros_distro: str) -> str:
    """Return the stable filename stem for one Windows standalone release ZIP."""
    return f"Ros2ForUnity_{ros_distro}_standalone_windows_x86_64"


def sha256_file(path: pathlib.Path) -> str:
    """Calculate the lowercase SHA-256 digest of a packaged ZIP."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_text(mapping: dict[str, object], key: str, label: str) -> str:
    """Read one required non-empty string from JSON-derived metadata."""
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{label} is missing '{key}'.")
    return value


def require_mapping(mapping: dict[str, object], key: str, label: str) -> dict[str, object]:
    """Read one required object from JSON-derived metadata."""
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} is missing {key}.")
    return value


def read_sha256_sidecar(path: pathlib.Path) -> str:
    """Read the digest from a standard '<hash>  <filename>' sidecar file."""
    try:
        first_field = path.read_text(encoding="utf-8").split(maxsplit=1)[0].lower()
    except (OSError, IndexError) as error:
        raise RuntimeError(f"Cannot read SHA256 sidecar '{path}': {error}") from error
    if not SHA256_RE.fullmatch(first_field):
        raise RuntimeError(f"SHA256 sidecar '{path}' does not begin with a valid SHA-256 digest.")
    return first_field


def read_manifest(path: pathlib.Path) -> dict[str, object]:
    """Load an artifact manifest with a clear release-facing error message."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read manifest '{path}': {error}") from error
    if not isinstance(manifest, dict):
        raise RuntimeError(f"Manifest '{path}' must contain a JSON object.")
    return manifest


def require_runtime_binary_hashes(manifest: dict[str, object], label: str) -> dict[str, str]:
    """Read release-critical staged DLL hashes that must match the packaged ZIP entries."""
    hashes = require_mapping(manifest, RUNTIME_BINARY_HASHES_KEY, label)
    result: dict[str, str] = {}
    for relative_path in REQUIRED_RUNTIME_BINARY_HASHES:
        digest = require_text(hashes, relative_path, label).lower()
        if not SHA256_RE.fullmatch(digest):
            raise RuntimeError(f"{label} has an invalid runtime binary SHA256 for '{relative_path}'.")
        result[relative_path] = digest
    return result


def read_archive_metadata(archive: zipfile.ZipFile, relative_path: str) -> dict[str, str]:
    """Read one metadata identity from the packaged Unity asset, not its staging tree."""
    archive_path = f"{ARCHIVE_ROOT}/{relative_path}"
    try:
        root = ET.fromstring(archive.read(archive_path))
    except (KeyError, ET.ParseError) as error:
        raise RuntimeError(f"Release ZIP is missing or has invalid metadata '{archive_path}': {error}") from error
    values = {
        "root": root.tag,
        "rosDistro": (root.findtext("ros2") or "").strip(),
        "sha": (root.findtext("version/sha") or "").strip(),
        "releaseTag": (root.findtext("version/desc") or "").strip(),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            f"Release ZIP metadata '{archive_path}' is missing required field(s): {', '.join(missing)}."
        )
    return values


def validate_archive_metadata(
    zip_path: pathlib.Path,
    *,
    ros_distro: str,
    release_tag: str,
    ros2cs_sha: str,
    ros2_for_unity_sha: str,
) -> None:
    """Require all archived metadata copies to match their declared release provenance."""
    checks = [
        ("ros2-for-unity", R2FU_METADATA_PATH, "ros2_for_unity", ros2_for_unity_sha),
        *[("ros2cs", path, "ros2cs", ros2cs_sha) for path in ROS2CS_METADATA_PATHS],
    ]
    errors = []
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for component, relative_path, expected_root, expected_sha in checks:
                try:
                    identity = read_archive_metadata(archive, relative_path)
                except RuntimeError as error:
                    errors.append(str(error))
                    continue
                archive_path = f"{ARCHIVE_ROOT}/{relative_path}"
                if identity["root"] != expected_root:
                    errors.append(f"{archive_path}: expected root '{expected_root}', found '{identity['root']}'.")
                if identity["rosDistro"] != ros_distro:
                    errors.append(
                        f"{archive_path}: expected ROS distro '{ros_distro}', found '{identity['rosDistro']}'."
                    )
                if identity["sha"] != expected_sha:
                    errors.append(
                        f"{archive_path}: expected {component} SHA '{expected_sha}', found '{identity['sha']}'."
                    )
                if identity["releaseTag"] != release_tag:
                    errors.append(
                        f"{archive_path}: expected release tag '{release_tag}', found '{identity['releaseTag']}'."
                    )
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimeError(f"Cannot read release ZIP '{zip_path}': {error}") from error

    if errors:
        detail = "\n".join(f"  - {error}" for error in errors)
        raise RuntimeError(f"Release ZIP metadata validation failed for '{zip_path}':\n{detail}")


def validate_archive_binary_hashes(zip_path: pathlib.Path, expected_hashes: dict[str, str]) -> None:
    """Require each release-critical ZIP entry to match the staged DLL hash in its manifest."""
    errors = []
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for relative_path, expected_digest in expected_hashes.items():
                archive_path = f"{ARCHIVE_ROOT}/{relative_path}"
                try:
                    actual_digest = hashlib.sha256(archive.read(archive_path)).hexdigest()
                except KeyError:
                    errors.append(f"Release ZIP is missing runtime binary '{archive_path}'.")
                    continue
                if actual_digest != expected_digest:
                    errors.append(
                        f"{archive_path}: expected SHA256 '{expected_digest}', found '{actual_digest}'."
                    )
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimeError(f"Cannot read release ZIP '{zip_path}': {error}") from error

    if errors:
        detail = "\n".join(f"  - {error}" for error in errors)
        raise RuntimeError(f"Release ZIP runtime binary validation failed for '{zip_path}':\n{detail}")


def validate_release_artifact(
    artifact_root: pathlib.Path,
    ros_distro: str,
    release_tag: str,
) -> ReleaseArtifact:
    """Validate one ZIP, sidecar, manifest, and embedded metadata before upload."""
    output_dir = artifact_root / ros_distro / DEFAULT_PLATFORM
    basename = artifact_basename(ros_distro)
    zip_path = output_dir / f"{basename}.zip"
    sha256_path = output_dir / f"{basename}.sha256.txt"
    manifest_path = output_dir / f"{basename}.manifest.json"
    missing = [str(path) for path in (zip_path, sha256_path, manifest_path) if not path.is_file()]
    if missing:
        raise RuntimeError("Release artifact is missing required file(s):\n" + "\n".join(f"  - {path}" for path in missing))

    digest = sha256_file(zip_path)
    sidecar_digest = read_sha256_sidecar(sha256_path)
    if sidecar_digest != digest:
        raise RuntimeError(f"SHA256 sidecar mismatch for '{zip_path}': expected '{digest}', found '{sidecar_digest}'.")

    manifest = read_manifest(manifest_path)
    if require_text(manifest, "artifactName", str(manifest_path)) != zip_path.name:
        raise RuntimeError(f"Manifest '{manifest_path}' does not identify '{zip_path.name}'.")
    if require_text(manifest, "rosDistro", str(manifest_path)) != ros_distro:
        raise RuntimeError(f"Manifest '{manifest_path}' does not identify ROS distro '{ros_distro}'.")
    if require_text(manifest, "sha256", str(manifest_path)) != digest:
        raise RuntimeError(f"Manifest '{manifest_path}' SHA256 does not match '{zip_path}'.")
    runtime_binary_hashes = require_runtime_binary_hashes(manifest, str(manifest_path))

    release_value = manifest.get("release")
    if not isinstance(release_value, dict):
        raise RuntimeError(f"Manifest '{manifest_path}' is missing release provenance.")
    release = release_value
    if require_text(release, "releaseTag", str(manifest_path)) != release_tag:
        raise RuntimeError(f"Manifest '{manifest_path}' does not identify release tag '{release_tag}'.")
    ros2cs_sha = require_text(release, "ros2csSha", str(manifest_path))
    ros2_for_unity_sha = require_text(release, "ros2ForUnitySha", str(manifest_path))
    if not SOURCE_SHA_RE.fullmatch(ros2cs_sha) or not SOURCE_SHA_RE.fullmatch(ros2_for_unity_sha):
        raise RuntimeError(f"Manifest '{manifest_path}' contains an invalid release source SHA.")
    if require_text(release, "ros2csReposPin", str(manifest_path)) != ros2cs_sha:
        raise RuntimeError(f"Manifest '{manifest_path}' ros2cs pin does not match its ros2cs source SHA.")

    metadata = require_mapping(release, "metadata", str(manifest_path))
    # The rebuild gate stores the per-artifact distro inside metadata provenance.
    if require_text(metadata, "releaseTag", str(manifest_path)) != release_tag:
        raise RuntimeError(f"Manifest '{manifest_path}' metadata provenance has the wrong release tag.")
    if require_text(metadata, "rosDistro", str(manifest_path)) != ros_distro:
        raise RuntimeError(f"Manifest '{manifest_path}' metadata provenance has the wrong ROS distro.")

    validate_archive_metadata(
        zip_path,
        ros_distro=ros_distro,
        release_tag=release_tag,
        ros2cs_sha=ros2cs_sha,
        ros2_for_unity_sha=ros2_for_unity_sha,
    )
    validate_archive_binary_hashes(zip_path, runtime_binary_hashes)
    return ReleaseArtifact(ros_distro, zip_path, sha256_path, manifest_path, ros2cs_sha, ros2_for_unity_sha)


def validate_release_artifacts(artifact_root: pathlib.Path, release_tag: str) -> list[ReleaseArtifact]:
    """Validate the required release set and require one common source pair across it."""
    artifacts = [
        validate_release_artifact(artifact_root, ros_distro, release_tag)
        for ros_distro in REQUIRED_ROS_DISTROS
    ]
    ros2cs_shas = {artifact.ros2cs_sha for artifact in artifacts}
    if len(ros2cs_shas) != 1:
        raise RuntimeError("Release artifacts use different ros2cs source SHAs.")
    r2fu_shas = {artifact.ros2_for_unity_sha for artifact in artifacts}
    if len(r2fu_shas) != 1:
        raise RuntimeError("Release artifacts use different ros2-for-unity source SHAs.")
    return artifacts


def release_command(*, repo: str, release_tag: str, artifacts: list[ReleaseArtifact]) -> list[str]:
    """Build the explicit gh command that uploads every verified release asset."""
    notes = (
        "Windows standalone release for ROS 2 Humble, Jazzy, and Lyrical.\n\n"
        "Each ZIP is verified against its SHA256 sidecar, manifest provenance, packaged metadata, and ros2cs_common.dll identity before upload."
    )
    command = [
        "gh",
        "release",
        "create",
        release_tag,
        "--repo",
        repo,
        "--title",
        f"Ros2ForUnity {release_tag}",
        "--notes",
        notes,
    ]
    for artifact in artifacts:
        command.extend([str(artifact.zip_path), str(artifact.sha256_path), str(artifact.manifest_path)])
    return command


def ensure_remote_tag_exists(repo: str, release_tag: str) -> None:
    """Refuse publication when GitHub does not already expose the requested release tag."""
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/git/ref/tags/{release_tag}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Remote tag '{release_tag}' does not exist in '{repo}'.")


def ensure_release_absent(repo: str, release_tag: str) -> None:
    """Prevent an accidental overwrite or duplicate release upload."""
    result = subprocess.run(
        ["gh", "release", "view", release_tag, "--repo", repo],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        raise RuntimeError(f"Release '{release_tag}' already exists in '{repo}'.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse publisher arguments without accepting an implicit release version."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True, help="Existing R2FU tag to publish, for example v0.8.0.")
    parser.add_argument("--artifact-root", type=pathlib.Path, default=default_artifact_root())
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY)
    parser.add_argument("--dry-run", action="store_true", help="Validate all artifacts and print the upload command only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Validate artifacts first, then create one GitHub release with all nine assets."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not RELEASE_TAG_RE.fullmatch(args.release_tag):
        raise SystemExit(f"Invalid --release-tag: {args.release_tag}")
    artifacts = validate_release_artifacts(args.artifact_root, args.release_tag)
    print(f"Release artifact gate passed for {args.release_tag}:")
    for artifact in artifacts:
        print(f"  - {artifact.ros_distro}: {artifact.zip_path}")

    command = release_command(repo=args.repo, release_tag=args.release_tag, artifacts=artifacts)
    if args.dry_run:
        print("Dry run; GitHub release creation skipped.")
        print(subprocess.list2cmdline(command))
        return 0

    ensure_remote_tag_exists(args.repo, args.release_tag)
    ensure_release_absent(args.repo, args.release_tag)
    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
