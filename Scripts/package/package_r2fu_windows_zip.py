#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Modifications by Jianbin Liu:
# - Added opt-in release metadata provenance validation.

"""Package the staged Ros2ForUnity Windows asset as a release zip."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile


DEFAULT_ARTIFACT_BASENAME = "Ros2ForUnity_jazzy_standalone_windows_x86_64"
DEFAULT_ROS_DISTRO = "jazzy"
DEFAULT_PLATFORM = "windows_x86_64"
PACKAGE_KIND = "standalone_unity_asset_zip"
# A release zip carries one R2FU identity file and three deployed ros2cs copies.
ROS2_FOR_UNITY_METADATA_PATH = pathlib.PurePosixPath("metadata_ros2_for_unity.xml")
ROS2CS_METADATA_PATHS = [
    pathlib.PurePosixPath("metadata_ros2cs.xml"),
    pathlib.PurePosixPath("Plugins/metadata_ros2cs.xml"),
    pathlib.PurePosixPath("Plugins/Windows/x86_64/metadata_ros2cs.xml"),
]

COMMON_REQUIRED_FILES = [
    "Plugins/ros2cs_common.dll",
    "Plugins/ros2cs_core.dll",
    "Plugins/composition_interfaces_assembly.dll",
    "Plugins/lifecycle_msgs_assembly.dll",
    "Plugins/statistics_msgs_assembly.dll",
    "Plugins/stereo_msgs_assembly.dll",
    "Plugins/Windows/x86_64/static_transform_broadcaster_node.dll",
    "Plugins/Windows/x86_64/tf2.dll",
    "Plugins/Windows/x86_64/tf2_ros.dll",
    "Plugins/Windows/x86_64/metadata_ros2cs.xml",
    "Plugins/Windows/x86_64/class_loader.dll",
    "Plugins/Windows/x86_64/rcl.dll",
    "Plugins/Windows/x86_64/rcutils.dll",
    "Plugins/Windows/x86_64/rmw_implementation.dll",
    "Plugins/Windows/x86_64/yaml.dll",
    "Plugins/Windows/x86_64/yaml-cpp.dll",
    "Plugins/Windows/x86_64/spdlog.dll",
    "Plugins/Windows/x86_64/share/ament_index/resource_index/packages/rmw_implementation",
    "Plugins/Windows/x86_64/share/ament_index/resource_index/rmw_typesupport/rmw_fastrtps_cpp",
    "StreamingAssets/Ros2ForUnity/share/ament_index/resource_index/packages/rmw_implementation",
    "StreamingAssets/Ros2ForUnity/share/ament_index/resource_index/rmw_typesupport/rmw_fastrtps_cpp",
]
JAZZY_REQUIRED_FILES = [
    "Plugins/Windows/x86_64/fmt.dll",
    "Plugins/Windows/x86_64/fastrtps-2.14.dll",
    "Plugins/Windows/x86_64/libssl-3-x64.dll",
    "Plugins/Windows/x86_64/libcrypto-3-x64.dll",
    "Plugins/Windows/x86_64/rcl_logging_spdlog.dll",
    "Plugins/type_description_interfaces_assembly.dll",
]
HUMBLE_REQUIRED_FILES = [
    "Plugins/actionlib_msgs_assembly.dll",
    "Plugins/Windows/x86_64/fastrtps-2.6.dll",
    "Plugins/Windows/x86_64/libssl-1_1-x64.dll",
    "Plugins/Windows/x86_64/libcrypto-1_1-x64.dll",
    "Plugins/Windows/x86_64/rcl_logging_spdlog.dll",
]
LYRICAL_REQUIRED_FILES = [
    "Plugins/Windows/x86_64/fmt.dll",
    "Plugins/Windows/x86_64/fastdds-3.6.dll",
    "Plugins/Windows/x86_64/libssl-3-x64.dll",
    "Plugins/Windows/x86_64/libcrypto-3-x64.dll",
    "Plugins/Windows/x86_64/rcl_logging_implementation.dll",
    "Plugins/Windows/x86_64/rosidl_buffer_backend_registry.dll",
    "Plugins/Windows/x86_64/rosidl_dynamic_typesupport_fastrtps.dll",
    "Plugins/type_description_interfaces_assembly.dll",
    "Plugins/Windows/x86_64/share/ament_index/resource_index/packages/rosidl_buffer_backend",
    "Plugins/Windows/x86_64/share/ament_index/resource_index/packages/rosidl_dynamic_typesupport_fastrtps",
    "StreamingAssets/Ros2ForUnity/share/ament_index/resource_index/packages/rosidl_buffer_backend",
    "StreamingAssets/Ros2ForUnity/share/ament_index/resource_index/packages/rosidl_dynamic_typesupport_fastrtps",
    # rmw_zenoh_cpp runtime (Lyrical only). FastRTPS stays the default RMW; zenoh is purely
    # additive and selectable at init via RMW_IMPLEMENTATION=rmw_zenoh_cpp. The two DLLs come
    # from the ros2cs standalone deploy; the rmw_typesupport ament entry must be present for the
    # rmw_implementation shim to discover zenoh at runtime. If the build fails on the ament
    # entries, the ros2-for-unity asset share-tree copy must be extended to include rmw_zenoh_cpp.
    "Plugins/Windows/x86_64/rmw_zenoh_cpp.dll",
    "Plugins/Windows/x86_64/zenohc.dll",
    "Plugins/Windows/x86_64/share/ament_index/resource_index/rmw_typesupport/rmw_zenoh_cpp",
    "StreamingAssets/Ros2ForUnity/share/ament_index/resource_index/rmw_typesupport/rmw_zenoh_cpp",
    "Plugins/Windows/x86_64/share/rmw_zenoh_cpp/config/DEFAULT_RMW_ZENOH_SESSION_CONFIG.json5",
    "Plugins/Windows/x86_64/share/rmw_zenoh_cpp/config/DEFAULT_RMW_ZENOH_ROUTER_CONFIG.json5",
    "StreamingAssets/Ros2ForUnity/share/rmw_zenoh_cpp/config/DEFAULT_RMW_ZENOH_SESSION_CONFIG.json5",
    "StreamingAssets/Ros2ForUnity/share/rmw_zenoh_cpp/config/DEFAULT_RMW_ZENOH_ROUTER_CONFIG.json5",
]


class PackageResult:
    def __init__(self, zip_path: pathlib.Path, sha256_path: pathlib.Path, manifest_path: pathlib.Path, sha256: str):
        self.zip_path = zip_path
        self.sha256_path = sha256_path
        self.manifest_path = manifest_path
        self.sha256 = sha256


def workspace_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def source_repo(name: str) -> pathlib.Path:
    root = workspace_root()
    third_party = root / "third-party" / name
    if third_party.exists():
        return third_party
    return root / name


def default_asset_dir() -> pathlib.Path:
    return source_repo("ros2-for-unity") / "install" / "asset" / "Ros2ForUnity"


def default_output_dir() -> pathlib.Path:
    return workspace_root() / "artifacts" / "ros2-for-unity" / DEFAULT_ROS_DISTRO / DEFAULT_PLATFORM


def run_git(repo: pathlib.Path, *args: str) -> str | None:
    if not repo.exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def git_info(repo: pathlib.Path) -> dict[str, str | None]:
    status_short = run_git(repo, "status", "--short")
    return {
        "branch": run_git(repo, "branch", "--show-current"),
        "commit": run_git(repo, "rev-parse", "HEAD"),
        "statusShort": status_short,
        "dirty": bool(status_short),
    }


def iter_package_files(asset_dir: pathlib.Path) -> list[pathlib.Path]:
    roots = [asset_dir]
    streaming_assets = asset_dir.parent / "StreamingAssets"
    if streaming_assets.is_dir():
        roots.append(streaming_assets)
    return sorted(path for root in roots for path in root.rglob("*") if path.is_file())


def iter_asset_files(asset_dir: pathlib.Path) -> list[pathlib.Path]:
    return sorted(path for path in asset_dir.rglob("*") if path.is_file())


def relative_zip_name(asset_dir: pathlib.Path, file_path: pathlib.Path) -> str:
    if file_path == asset_dir or asset_dir in file_path.parents:
        relative = file_path.relative_to(asset_dir).as_posix()
        return f"{asset_dir.name}/{relative}"

    streaming_assets = asset_dir.parent / "StreamingAssets"
    relative = file_path.relative_to(streaming_assets).as_posix()
    return f"StreamingAssets/{relative}"


def write_zip(asset_dir: pathlib.Path, zip_path: pathlib.Path) -> int:
    files = iter_package_files(asset_dir)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file_path in files:
            archive.write(file_path, relative_zip_name(asset_dir, file_path))
    return len(files)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_managed_plugins(files: list[pathlib.Path], asset_dir: pathlib.Path) -> int:
    plugin_root = asset_dir / "Plugins"
    return sum(
        1
        for path in files
        if path.suffix.lower() == ".dll"
        and plugin_root in path.parents
        and "Windows" not in path.relative_to(plugin_root).parts
    )


def count_native_plugins(files: list[pathlib.Path], asset_dir: pathlib.Path) -> int:
    native_root = asset_dir / "Plugins" / "Windows" / "x86_64"
    return sum(1 for path in files if native_root in path.parents)


def count_resource_index_files(files: list[pathlib.Path], asset_dir: pathlib.Path) -> int:
    return sum(
        1
        for path in files
        if "share" in path.relative_to(asset_dir).parts
        and "ament_index" in path.relative_to(asset_dir).parts
        and "resource_index" in path.relative_to(asset_dir).parts
    )


def count_metadata_files(files: list[pathlib.Path]) -> int:
    return sum(1 for path in files if path.name.startswith("metadata_") and path.suffix.lower() == ".xml")


def required_files_for_distro(ros_distro: str) -> list[str]:
    if ros_distro == "lyrical":
        return COMMON_REQUIRED_FILES + LYRICAL_REQUIRED_FILES
    if ros_distro == "humble":
        return COMMON_REQUIRED_FILES + HUMBLE_REQUIRED_FILES
    return COMMON_REQUIRED_FILES + JAZZY_REQUIRED_FILES


def missing_required_files(asset_dir: pathlib.Path, ros_distro: str) -> list[str]:
    missing = []
    for item in required_files_for_distro(ros_distro):
        relative = pathlib.PurePosixPath(item)
        root = asset_dir.parent if relative.parts[0] == "StreamingAssets" else asset_dir
        if not (root / relative).exists():
            missing.append(item)
    return missing


def validate_runtime_closure(asset_dir: pathlib.Path, ros_distro: str) -> None:
    missing = missing_required_files(asset_dir, ros_distro)
    files = iter_asset_files(asset_dir)
    resource_index_count = count_resource_index_files(files, asset_dir)
    metadata_count = count_metadata_files(files)
    if resource_index_count == 0 and metadata_count == 0:
        missing.append("share/ament_index/resource_index or metadata_*.xml")

    if missing:
        missing_list = "\n".join(f"  - {item}" for item in missing)
        raise RuntimeError(
            "Required artifact files are missing under "
            f"{asset_dir}:\n{missing_list}"
        )


def read_metadata_identity(path: pathlib.Path) -> dict[str, str]:
    """Read the ROS distro and source identity embedded in one metadata file."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise RuntimeError(f"Cannot parse release metadata '{path}': {error}") from error

    values = {
        "root": root.tag,
        "rosDistro": root.findtext("ros2", default="").strip(),
        "sha": root.findtext("version/sha", default="").strip(),
        "releaseTag": root.findtext("version/desc", default="").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            f"Release metadata '{path}' is missing required field(s): {', '.join(missing)}."
        )
    return values


def validate_release_metadata(
    asset_dir: pathlib.Path,
    *,
    ros_distro: str,
    release_tag: str,
    ros2cs_sha: str,
    ros2_for_unity_sha: str,
) -> dict[str, object]:
    """Fail closed unless every packaged metadata copy identifies this release."""
    checks = [
        ("ros2-for-unity", ROS2_FOR_UNITY_METADATA_PATH, "ros2_for_unity", ros2_for_unity_sha),
        *[("ros2cs", path, "ros2cs", ros2cs_sha) for path in ROS2CS_METADATA_PATHS],
    ]
    errors = []
    for component, relative_path, expected_root, expected_sha in checks:
        path = asset_dir / relative_path
        try:
            identity = read_metadata_identity(path)
        except RuntimeError as error:
            errors.append(str(error))
            continue

        if identity["root"] != expected_root:
            errors.append(
                f"{path}: expected root '{expected_root}', found '{identity['root']}'."
            )
        if identity["rosDistro"] != ros_distro:
            errors.append(
                f"{path}: expected ROS distro '{ros_distro}', found '{identity['rosDistro']}'."
            )
        if identity["sha"] != expected_sha:
            errors.append(
                f"{path}: expected {component} SHA '{expected_sha}', found '{identity['sha']}'."
            )
        if identity["releaseTag"] != release_tag:
            errors.append(
                f"{path}: expected release tag '{release_tag}', found '{identity['releaseTag']}'."
            )

    if errors:
        detail = "\n".join(f"  - {error}" for error in errors)
        raise RuntimeError(f"Release metadata validation failed:\n{detail}")

    return {
        "releaseTag": release_tag,
        "rosDistro": ros_distro,
        "ros2ForUnityMetadataPath": str(ROS2_FOR_UNITY_METADATA_PATH),
        "ros2csMetadataPaths": [str(path) for path in ROS2CS_METADATA_PATHS],
    }


def backup_existing_outputs(paths: list[pathlib.Path], output_dir: pathlib.Path) -> pathlib.Path | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None

    backup_dir = output_dir / f"backup_{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in existing:
        shutil.move(str(path), str(backup_dir / path.name))
    return backup_dir


def write_manifest(
    manifest_path: pathlib.Path,
    *,
    asset_dir: pathlib.Path,
    output_dir: pathlib.Path,
    artifact_name: str,
    zip_path: pathlib.Path,
    sha256: str,
    zip_entry_count: int,
    ros_distro: str,
    platform: str,
    backup_dir: pathlib.Path | None,
    validation_summary_path: pathlib.Path | None,
    release_provenance: dict[str, object] | None,
) -> None:
    files = iter_asset_files(asset_dir)
    root = workspace_root()
    ros2_for_unity = git_info(source_repo("ros2-for-unity"))
    ros2cs = git_info(source_repo("ros2cs"))
    resource_index_count = count_resource_index_files(files, asset_dir)
    metadata_count = count_metadata_files(files)
    manifest = {
        "artifactName": artifact_name,
        "artifactPath": str(zip_path),
        "createdAtLocal": dt.datetime.now().astimezone().isoformat(),
        "rosDistro": ros_distro,
        "platform": platform,
        "packageKind": PACKAGE_KIND,
        "sha256": sha256,
        "sizeBytes": zip_path.stat().st_size,
        "zipEntryCount": zip_entry_count,
        "assetFileCount": len(files),
        "managedPluginFileCount": count_managed_plugins(files, asset_dir),
        "nativePluginFileCount": count_native_plugins(files, asset_dir),
        "resourceIndexFileCount": resource_index_count,
        "metadataFileCount": metadata_count,
        "ros2_for_unity": ros2_for_unity,
        "ros2cs": ros2cs,
        "source": {
            "ros2ForUnity": ros2_for_unity,
            "ros2cs": ros2cs,
        },
        "validation": {
            "assetSanity": "required managed/native DLL closure spot checks passed",
            "requiredFiles": required_files_for_distro(ros_distro),
            "resourceIndexFileCount": resource_index_count,
            "metadataFileCount": metadata_count,
            "summaryPath": str(validation_summary_path) if validation_summary_path else None,
        },
        "boundaries": [
            "Zip was generated from ros2-for-unity/install/asset/Ros2ForUnity.",
            "SHA256 identifies this packaging snapshot.",
            "Unity Editor Play/Stop runtime smoke is not run by this packaging script.",
        ],
    }
    if backup_dir is not None:
        manifest["previousArtifactBackup"] = str(backup_dir)
    if release_provenance is not None:
        manifest["release"] = release_provenance

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def package_asset(
    *,
    asset_dir: pathlib.Path,
    output_dir: pathlib.Path,
    artifact_basename: str = DEFAULT_ARTIFACT_BASENAME,
    ros_distro: str = DEFAULT_ROS_DISTRO,
    platform: str = DEFAULT_PLATFORM,
    backup_existing: bool = True,
    check_required: bool = False,
    validation_summary_path: pathlib.Path | None = None,
    release_provenance: dict[str, object] | None = None,
) -> PackageResult:
    asset_dir = asset_dir.resolve()
    output_dir = output_dir.resolve()
    if not asset_dir.is_dir():
        raise FileNotFoundError(f"Asset directory not found: {asset_dir}")

    if check_required:
        validate_runtime_closure(asset_dir, ros_distro)

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{artifact_basename}.zip"
    sha256_path = output_dir / f"{artifact_basename}.sha256.txt"
    manifest_path = output_dir / f"{artifact_basename}.manifest.json"

    backup_dir = None
    if backup_existing:
        backup_dir = backup_existing_outputs([zip_path, sha256_path, manifest_path], output_dir)

    zip_entry_count = write_zip(asset_dir, zip_path)
    digest = sha256_file(zip_path)
    sha256_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    write_manifest(
        manifest_path,
        asset_dir=asset_dir,
        output_dir=output_dir,
        artifact_name=zip_path.name,
        zip_path=zip_path,
        sha256=digest,
        zip_entry_count=zip_entry_count,
        ros_distro=ros_distro,
        platform=platform,
        backup_dir=backup_dir,
        validation_summary_path=validation_summary_path,
        release_provenance=release_provenance,
    )
    return PackageResult(zip_path, sha256_path, manifest_path, digest)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-dir", type=pathlib.Path, default=default_asset_dir())
    parser.add_argument("--output-dir", type=pathlib.Path, default=default_output_dir())
    parser.add_argument("--artifact-basename", default=DEFAULT_ARTIFACT_BASENAME)
    parser.add_argument("--ros-distro", default=DEFAULT_ROS_DISTRO)
    parser.add_argument("--platform", default=DEFAULT_PLATFORM)
    parser.add_argument("--no-backup", action="store_true", help="Overwrite current outputs instead of moving them to backup_<timestamp>.")
    parser.add_argument("--skip-required-check", action="store_true", help="Do not fail when the standard Windows DLL spot-check files are absent.")
    parser.add_argument("--validation-summary-path", type=pathlib.Path, default=None, help="Optional full validation summary JSON to record in the manifest.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = package_asset(
        asset_dir=args.asset_dir,
        output_dir=args.output_dir,
        artifact_basename=args.artifact_basename,
        ros_distro=args.ros_distro,
        platform=args.platform,
        backup_existing=not args.no_backup,
        check_required=not args.skip_required_check,
        validation_summary_path=args.validation_summary_path,
    )
    print(f"ZIP:      {result.zip_path}")
    print(f"SHA256:   {result.sha256}")
    print(f"SHA FILE: {result.sha256_path}")
    print(f"MANIFEST: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
