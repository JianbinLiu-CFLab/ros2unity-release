#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Modifications by Jianbin Liu:
# - Added manifest coverage for release-critical staged DLL hashes.
# - Keeps package-test fixtures below the workspace-owned .build directory.

import importlib.util
import json
import pathlib
import tempfile
import unittest
import zipfile
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).with_name("package_r2fu_windows_zip.py")
WORKSPACE_BUILD_ROOT = SCRIPT_PATH.resolve().parents[2] / ".build"


def workspace_tempdir() -> tempfile.TemporaryDirectory:
    """Create package fixtures inside the workspace-owned build directory."""
    WORKSPACE_BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=WORKSPACE_BUILD_ROOT)


def write_required_asset_files(asset_dir: pathlib.Path):
    plugin_dir = asset_dir / "Plugins"
    native_dir = plugin_dir / "Windows" / "x86_64"
    native_dir.mkdir(parents=True)
    (asset_dir / "Scripts").mkdir(parents=True)
    for relative in [
        "Plugins/ros2cs_common.dll",
        "Plugins/ros2cs_core.dll",
        "Plugins/Windows/x86_64/rcl.dll",
        "Plugins/Windows/x86_64/rcutils.dll",
        "Plugins/Windows/x86_64/rmw_implementation.dll",
        "Plugins/Windows/x86_64/yaml.dll",
        "Plugins/Windows/x86_64/yaml-cpp.dll",
        "Plugins/Windows/x86_64/spdlog.dll",
        "Plugins/Windows/x86_64/fmt.dll",
        "Plugins/Windows/x86_64/libssl-3-x64.dll",
        "Plugins/Windows/x86_64/libcrypto-3-x64.dll",
    ]:
        (asset_dir / pathlib.PurePosixPath(relative)).write_text(relative, encoding="utf-8")
    (asset_dir / "metadata_ros2cs.xml").write_text("<ros2cs />", encoding="utf-8")
    (asset_dir / "Scripts" / "ROS2ForUnity.cs").write_text("script", encoding="utf-8")


def write_release_metadata(
    asset_dir: pathlib.Path,
    *,
    ros_distro: str,
    ros2cs_sha: str,
    ros2_for_unity_sha: str,
    release_tag: str,
):
    ros2_for_unity = (
        "<ros2_for_unity>"
        f"<ros2>{ros_distro}</ros2>"
        f"<version><sha>{ros2_for_unity_sha}</sha><desc>{release_tag}</desc></version>"
        "</ros2_for_unity>"
    )
    ros2cs = (
        "<ros2cs>"
        f"<ros2>{ros_distro}</ros2>"
        f"<version><sha>{ros2cs_sha}</sha><desc>{release_tag}</desc></version>"
        "</ros2cs>"
    )
    (asset_dir / "metadata_ros2_for_unity.xml").write_text(ros2_for_unity, encoding="utf-8")
    for relative in [
        "metadata_ros2cs.xml",
        "Plugins/metadata_ros2cs.xml",
        "Plugins/Windows/x86_64/metadata_ros2cs.xml",
    ]:
        path = asset_dir / pathlib.PurePosixPath(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ros2cs, encoding="utf-8")


def load_module():
    spec = importlib.util.spec_from_file_location("package_r2fu_windows_zip", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageR2FUWindowsArtifactZipTest(unittest.TestCase):
    def test_default_paths_are_workspace_relative(self):
        module = load_module()
        workspace_root = SCRIPT_PATH.resolve().parents[2]

        self.assertEqual(
            module.default_asset_dir(),
            workspace_root / "third-party" / "ros2-for-unity" / "install" / "asset" / "Ros2ForUnity",
        )
        self.assertEqual(
            module.default_output_dir(),
            workspace_root / "artifacts" / "ros2-for-unity" / "jazzy" / "windows_x86_64",
        )

    def test_package_asset_writes_zip_sha256_and_manifest(self):
        module = load_module()

        with workspace_tempdir() as temp_dir:
            root = pathlib.Path(temp_dir)
            asset_dir = root / "Ros2ForUnity"
            write_required_asset_files(asset_dir)
            validation_summary = root / "validation-summary.json"
            validation_summary.write_text("{}", encoding="utf-8")

            output_dir = root / "out"
            result = module.package_asset(
                asset_dir=asset_dir,
                output_dir=output_dir,
                check_required=False,
                validation_summary_path=validation_summary,
                release_provenance={"releaseTag": "v0.8.0"},
            )

            self.assertTrue(result.zip_path.exists())
            self.assertTrue(result.sha256_path.exists())
            self.assertTrue(result.manifest_path.exists())

            with zipfile.ZipFile(result.zip_path) as archive:
                self.assertEqual(
                    sorted(archive.namelist()),
                    sorted([
                        "Ros2ForUnity/Plugins/Windows/x86_64/fmt.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/libcrypto-3-x64.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/libssl-3-x64.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/rcl.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/rcutils.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/rmw_implementation.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/spdlog.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/yaml-cpp.dll",
                        "Ros2ForUnity/Plugins/Windows/x86_64/yaml.dll",
                        "Ros2ForUnity/Plugins/ros2cs_common.dll",
                        "Ros2ForUnity/Plugins/ros2cs_core.dll",
                        "Ros2ForUnity/Scripts/ROS2ForUnity.cs",
                        "Ros2ForUnity/metadata_ros2cs.xml",
                    ]),
                )

            sha_text = result.sha256_path.read_text(encoding="utf-8").strip()
            self.assertIn(result.zip_path.name, sha_text)
            self.assertIn(result.sha256, sha_text)

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["artifactName"], result.zip_path.name)
            self.assertEqual(manifest["assetFileCount"], 13)
            self.assertEqual(manifest["zipEntryCount"], 13)
            self.assertEqual(manifest["managedPluginFileCount"], 2)
            self.assertEqual(manifest["nativePluginFileCount"], 9)
            self.assertEqual(manifest["resourceIndexFileCount"], 0)
            self.assertEqual(manifest["metadataFileCount"], 1)
            self.assertEqual(manifest["sha256"], result.sha256)
            self.assertEqual(
                manifest["runtimeBinaryHashes"]["Plugins/ros2cs_common.dll"],
                module.sha256_file(asset_dir / "Plugins" / "ros2cs_common.dll"),
            )
            self.assertEqual(
                manifest["validation"]["runtimeBinaryHashes"],
                manifest["runtimeBinaryHashes"],
            )
            self.assertEqual(manifest["release"], {"releaseTag": "v0.8.0"})
            self.assertIn("commit", manifest["ros2_for_unity"])
            self.assertIn("dirty", manifest["ros2_for_unity"])
            self.assertIn("commit", manifest["ros2cs"])
            self.assertIn("dirty", manifest["ros2cs"])
            self.assertEqual(manifest["validation"]["summaryPath"], str(validation_summary))

    def test_required_check_reports_missing_closure_files(self):
        module = load_module()

        with workspace_tempdir() as temp_dir:
            root = pathlib.Path(temp_dir)
            asset_dir = root / "Ros2ForUnity"
            (asset_dir / "Plugins").mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "ros2cs_common.dll"):
                module.package_asset(
                    asset_dir=asset_dir,
                    output_dir=root / "out",
                    check_required=True,
                    backup_existing=False,
                )

    def test_package_asset_records_explicit_source_roots(self):
        module = load_module()

        with tempfile.TemporaryDirectory(dir=SCRIPT_PATH.resolve().parents[2] / ".build") as temp_dir:
            root = pathlib.Path(temp_dir)
            asset_dir = root / "Ros2ForUnity"
            write_required_asset_files(asset_dir)
            r2fu_root = root / "r2fu-worktree"
            ros2cs_root = root / "ros2cs-worktree"
            r2fu_root.mkdir()
            ros2cs_root.mkdir()

            def fake_git_info(path: pathlib.Path):
                return {"root": str(path)}

            try:
                with mock.patch.object(module, "git_info", side_effect=fake_git_info):
                    result = module.package_asset(
                        asset_dir=asset_dir,
                        output_dir=root / "out",
                        check_required=False,
                        backup_existing=False,
                        ros2_for_unity_root=r2fu_root,
                        ros2cs_root=ros2cs_root,
                    )
            except TypeError as error:
                self.fail(f"explicit source roots must be accepted: {error}")

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["source"]["ros2ForUnity"]["root"], str(r2fu_root))
            self.assertEqual(manifest["source"]["ros2cs"]["root"], str(ros2cs_root))

    def test_release_metadata_accepts_matching_tag_and_runtime_distro(self):
        module = load_module()

        with workspace_tempdir() as temp_dir:
            asset_dir = pathlib.Path(temp_dir) / "Ros2ForUnity"
            asset_dir.mkdir()
            write_release_metadata(
                asset_dir,
                ros_distro="lyrical",
                ros2cs_sha="a" * 40,
                ros2_for_unity_sha="b" * 40,
                release_tag="v0.8.0",
            )

            provenance = module.validate_release_metadata(
                asset_dir,
                ros_distro="lyrical",
                release_tag="v0.8.0",
                ros2cs_sha="a" * 40,
                ros2_for_unity_sha="b" * 40,
            )

            self.assertEqual(provenance["releaseTag"], "v0.8.0")
            self.assertEqual(provenance["rosDistro"], "lyrical")
            self.assertEqual(len(provenance["ros2csMetadataPaths"]), 3)

    def test_release_metadata_rejects_cross_distro(self):
        module = load_module()

        with workspace_tempdir() as temp_dir:
            asset_dir = pathlib.Path(temp_dir) / "Ros2ForUnity"
            asset_dir.mkdir()
            write_release_metadata(
                asset_dir,
                ros_distro="jazzy",
                ros2cs_sha="a" * 40,
                ros2_for_unity_sha="b" * 40,
                release_tag="v0.8.0",
            )

            with self.assertRaisesRegex(RuntimeError, "expected ROS distro 'lyrical'"):
                module.validate_release_metadata(
                    asset_dir,
                    ros_distro="lyrical",
                    release_tag="v0.8.0",
                    ros2cs_sha="a" * 40,
                    ros2_for_unity_sha="b" * 40,
                )

    def test_release_metadata_rejects_stale_duplicate_copy(self):
        module = load_module()

        with workspace_tempdir() as temp_dir:
            asset_dir = pathlib.Path(temp_dir) / "Ros2ForUnity"
            asset_dir.mkdir()
            write_release_metadata(
                asset_dir,
                ros_distro="lyrical",
                ros2cs_sha="a" * 40,
                ros2_for_unity_sha="b" * 40,
                release_tag="v0.8.0",
            )
            stale_copy = asset_dir / "Plugins" / "metadata_ros2cs.xml"
            stale_copy.write_text(
                "<ros2cs><ros2>lyrical</ros2><version>"
                f"<sha>{'a' * 40}</sha><desc>v0.6.0-jazzy-preview.1-59-gbe0cfe4</desc>"
                "</version></ros2cs>",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "expected release tag 'v0.8.0'"):
                module.validate_release_metadata(
                    asset_dir,
                    ros_distro="lyrical",
                    release_tag="v0.8.0",
                    ros2cs_sha="a" * 40,
                    ros2_for_unity_sha="b" * 40,
                )


if __name__ == "__main__":
    unittest.main()
