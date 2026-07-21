#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for release artifact provenance validation before GitHub publication.
#
# Modifications by Jianbin Liu:
# - Rejects ZIPs whose release-critical DLL bytes differ from the manifest.

"""Regression tests for the R2FU Windows release publisher."""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
import zipfile


SCRIPT_PATH = pathlib.Path(__file__).with_name("publish_r2fu_windows_release.py")


def load_module():
    spec = importlib.util.spec_from_file_location("publish_r2fu_windows_release", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_metadata(root: str, ros_distro: str, sha: str, release_tag: str) -> str:
    return (
        f"<{root}><ros2>{ros_distro}</ros2><version>"
        f"<sha>{sha}</sha><desc>{release_tag}</desc>"
        f"</version></{root}>"
    )


def write_release_artifact(
    module,
    artifact_root: pathlib.Path,
    *,
    ros_distro: str,
    release_tag: str,
    ros2cs_sha: str,
    ros2_for_unity_sha: str,
) -> None:
    artifact_name = f"Ros2ForUnity_{ros_distro}_standalone_windows_x86_64.zip"
    output_dir = artifact_root / ros_distro / "windows_x86_64"
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / artifact_name
    r2fu_metadata = write_metadata("ros2_for_unity", ros_distro, ros2_for_unity_sha, release_tag)
    ros2cs_metadata = write_metadata("ros2cs", ros_distro, ros2cs_sha, release_tag)
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("Ros2ForUnity/metadata_ros2_for_unity.xml", r2fu_metadata)
        archive.writestr("Ros2ForUnity/metadata_ros2cs.xml", ros2cs_metadata)
        archive.writestr("Ros2ForUnity/Plugins/metadata_ros2cs.xml", ros2cs_metadata)
        archive.writestr("Ros2ForUnity/Plugins/Windows/x86_64/metadata_ros2cs.xml", ros2cs_metadata)
        archive.writestr("Ros2ForUnity/Plugins/ros2cs_common.dll", b"ros2cs-common-assembly")

    digest = module.sha256_file(zip_path)
    (output_dir / artifact_name.replace(".zip", ".sha256.txt")).write_text(
        f"{digest}  {artifact_name}\n",
        encoding="utf-8",
    )
    manifest = {
        "artifactName": artifact_name,
        "rosDistro": ros_distro,
        "sha256": digest,
        "runtimeBinaryHashes": {
            "Plugins/ros2cs_common.dll": module.hashlib.sha256(b"ros2cs-common-assembly").hexdigest(),
        },
        "release": {
            "releaseTag": release_tag,
            "ros2csSha": ros2cs_sha,
            "ros2ForUnitySha": ros2_for_unity_sha,
            "ros2csReposPin": ros2cs_sha,
            "metadata": {
                "releaseTag": release_tag,
                "rosDistro": ros_distro,
                "ros2ForUnityMetadataPath": "metadata_ros2_for_unity.xml",
                "ros2csMetadataPaths": [
                    "metadata_ros2cs.xml",
                    "Plugins/metadata_ros2cs.xml",
                    "Plugins/Windows/x86_64/metadata_ros2cs.xml",
                ],
            },
        },
    }
    (output_dir / artifact_name.replace(".zip", ".manifest.json")).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def replace_archive_entry(zip_path: pathlib.Path, archive_path: str, contents: bytes) -> None:
    """Rewrite one ZIP entry without leaving duplicate names behind."""
    temporary_path = zip_path.with_suffix(".replacement.zip")
    with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(temporary_path, "w") as target:
        for entry in source.infolist():
            target.writestr(entry, contents if entry.filename == archive_path else source.read(entry.filename))
    os.replace(temporary_path, zip_path)


class PublishR2FUWindowsReleaseTests(unittest.TestCase):
    """Verify that publication only accepts gated Humble/Jazzy/Lyrical artifacts."""

    release_tag = "v0.8.0"
    ros2cs_sha = "a" * 40
    ros2_for_unity_sha = "b" * 40

    def write_all_artifacts(self, module, artifact_root: pathlib.Path) -> None:
        for ros_distro in module.REQUIRED_ROS_DISTROS:
            write_release_artifact(
                module,
                artifact_root,
                ros_distro=ros_distro,
                release_tag=self.release_tag,
                ros2cs_sha=self.ros2cs_sha,
                ros2_for_unity_sha=self.ros2_for_unity_sha,
            )

    def test_accepts_three_consistent_gated_artifacts(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as directory:
            artifact_root = pathlib.Path(directory)
            self.write_all_artifacts(module, artifact_root)

            artifacts = module.validate_release_artifacts(artifact_root, self.release_tag)

            self.assertEqual([artifact.ros_distro for artifact in artifacts], list(module.REQUIRED_ROS_DISTROS))
            self.assertEqual({artifact.ros2cs_sha for artifact in artifacts}, {self.ros2cs_sha})
            self.assertEqual({artifact.ros2_for_unity_sha for artifact in artifacts}, {self.ros2_for_unity_sha})

    def test_rejects_artifacts_without_release_provenance(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as directory:
            artifact_root = pathlib.Path(directory)
            self.write_all_artifacts(module, artifact_root)
            manifest_path = (
                artifact_root
                / "jazzy"
                / "windows_x86_64"
                / "Ros2ForUnity_jazzy_standalone_windows_x86_64.manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["release"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "missing release provenance"):
                module.validate_release_artifacts(artifact_root, self.release_tag)

    def test_rejects_cross_distro_source_mix(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as directory:
            artifact_root = pathlib.Path(directory)
            self.write_all_artifacts(module, artifact_root)
            write_release_artifact(
                module,
                artifact_root,
                ros_distro="lyrical",
                release_tag=self.release_tag,
                ros2cs_sha="c" * 40,
                ros2_for_unity_sha=self.ros2_for_unity_sha,
            )

            with self.assertRaisesRegex(RuntimeError, "different ros2cs source SHAs"):
                module.validate_release_artifacts(artifact_root, self.release_tag)

    def test_rejects_archive_with_stale_metadata_tag(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as directory:
            artifact_root = pathlib.Path(directory)
            self.write_all_artifacts(module, artifact_root)
            write_release_artifact(
                module,
                artifact_root,
                ros_distro="jazzy",
                release_tag="v0.6.0-jazzy-preview.1-59-gbe0cfe4",
                ros2cs_sha=self.ros2cs_sha,
                ros2_for_unity_sha=self.ros2_for_unity_sha,
            )
            manifest_path = (
                artifact_root
                / "jazzy"
                / "windows_x86_64"
                / "Ros2ForUnity_jazzy_standalone_windows_x86_64.manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["release"]["releaseTag"] = self.release_tag
            manifest["release"]["metadata"]["releaseTag"] = self.release_tag
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "expected release tag 'v0.8.0'"):
                module.validate_release_artifacts(artifact_root, self.release_tag)

    def test_rejects_archive_with_stale_ros2cs_common_dll(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as directory:
            artifact_root = pathlib.Path(directory)
            self.write_all_artifacts(module, artifact_root)
            output_dir = artifact_root / "jazzy" / "windows_x86_64"
            zip_path = output_dir / "Ros2ForUnity_jazzy_standalone_windows_x86_64.zip"
            replace_archive_entry(
                zip_path,
                "Ros2ForUnity/Plugins/ros2cs_common.dll",
                b"stale-ros2cs-common-assembly",
            )
            digest = module.sha256_file(zip_path)
            (output_dir / "Ros2ForUnity_jazzy_standalone_windows_x86_64.sha256.txt").write_text(
                f"{digest}  {zip_path.name}\n",
                encoding="utf-8",
            )
            manifest_path = output_dir / "Ros2ForUnity_jazzy_standalone_windows_x86_64.manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["sha256"] = digest
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "runtime binary validation failed"):
                module.validate_release_artifacts(artifact_root, self.release_tag)

    def test_release_command_uploads_zip_checksums_and_manifests(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as directory:
            artifact_root = pathlib.Path(directory)
            self.write_all_artifacts(module, artifact_root)
            artifacts = module.validate_release_artifacts(artifact_root, self.release_tag)

            command = module.release_command(
                repo="JianbinLiu-CFLab/ros2-for-unity",
                release_tag=self.release_tag,
                artifacts=artifacts,
            )

            self.assertEqual(command[:3], ["gh", "release", "create"])
            self.assertIn(self.release_tag, command)
            self.assertEqual(sum(path.endswith(".zip") for path in command), 3)
            self.assertEqual(sum(path.endswith(".sha256.txt") for path in command), 3)
            self.assertEqual(sum(path.endswith(".manifest.json") for path in command), 3)


if __name__ == "__main__":
    unittest.main()
