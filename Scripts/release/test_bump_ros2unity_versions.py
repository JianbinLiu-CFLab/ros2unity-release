#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for stable and preview release-reference synchronization.

"""Regression tests for the release-reference synchronization script."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("bump_ros2unity_versions.py")
MODULE_SPEC = importlib.util.spec_from_file_location("bump_ros2unity_versions", SCRIPT_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Cannot load {SCRIPT_PATH}")
MODULE = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = MODULE
MODULE_SPEC.loader.exec_module(MODULE)


class VersionSyncStableTagTests(unittest.TestCase):
    """Verify a stable tag can advance without a duplicate replacement failure."""

    old_sha = "0" * 40
    new_sha = "1" * 40

    def create_workspace(self, root: Path) -> None:
        """Create the smallest workspace fixture accepted by VersionSync."""
        ros2cs = root / "third-party" / "ros2cs"
        r2fu = root / "third-party" / "ros2-for-unity"
        ros2cs.mkdir(parents=True)
        r2fu.mkdir(parents=True)

        (ros2cs / "README.md").write_text(
            "https://github.com/JianbinLiu-CFLab/ros2cs.git\n"
            "version: v0.7.0\n"
            "https://github.com/JianbinLiu-CFLab/ros2cs/releases/tag/v0.7.0\n",
            encoding="utf-8",
        )
        (ros2cs / "README-WINDOWS.md").write_text(
            "Latest public maintenance release: v0.7.0\n",
            encoding="utf-8",
        )
        (r2fu / "ros2cs.repos").write_text(
            "repositories:\n"
            "  src/ros2cs:\n"
            "    type: git\n"
            "    version: " + self.old_sha + "\n",
            encoding="utf-8",
        )
        (r2fu / "README.md").write_text(
            "https://github.com/JianbinLiu-CFLab/ros2cs.git\n"
            "version: " + self.old_sha + "\n\n"
            "- Latest source release: [`v0.7.0`]"
            "(https://github.com/JianbinLiu-CFLab/ros2-for-unity/releases/tag/v0.7.0).\n"
            "- Latest packaged Windows artifact: [`v0.7.0`]"
            "(https://github.com/JianbinLiu-CFLab/ros2-for-unity/releases/tag/v0.7.0).\n"
            "- previous: [`v0.6.0-jazzy-win64-preview.1`]"
            "(https://github.com/JianbinLiu-CFLab/ros2-for-unity/releases/tag/"
            "v0.6.0-jazzy-win64-preview.1)\n",
            encoding="utf-8",
        )
        (r2fu / "README-WINDOWS.md").write_text(
            "R2FU: v0.7.0\nros2cs: v0.7.0\nRelease: v0.7.0\n",
            encoding="utf-8",
        )

    def new_sync(self, root: Path, dry_run: bool) -> object:
        """Create the stable-tag synchronization under test."""
        return MODULE.VersionSync(
            workspace_root=root,
            ros2cs_version="v0.8.0",
            r2fu_version="v0.8.0",
            ros2cs_sha=self.new_sha,
            dry_run=dry_run,
        )

    def test_stable_tag_dry_run_plans_changes_without_writing(self) -> None:
        """A stable current tag should not be replaced twice during dry-run."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_workspace(root)
            readme = root / "third-party" / "ros2-for-unity" / "README.md"
            original = readme.read_text(encoding="utf-8")

            sync = self.new_sync(root, dry_run=True)

            self.assertEqual(MODULE.EXIT_SUCCESS, sync.run())
            self.assertEqual(original, readme.read_text(encoding="utf-8"))
            self.assertEqual(5, len(sync.changes))

    def test_stable_tag_write_updates_release_references_and_pin(self) -> None:
        """The non-dry-run path should write v0.8.0 and the supplied SHA pin."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_workspace(root)

            sync = self.new_sync(root, dry_run=False)

            self.assertEqual(MODULE.EXIT_SUCCESS, sync.run())
            r2fu = root / "third-party" / "ros2-for-unity"
            self.assertIn("v0.8.0", (r2fu / "README.md").read_text(encoding="utf-8"))
            self.assertIn("v0.8.0", (r2fu / "README-WINDOWS.md").read_text(encoding="utf-8"))
            self.assertIn(self.new_sha, (r2fu / "ros2cs.repos").read_text(encoding="utf-8"))
            self.assertEqual(5, len(sync.changes))


if __name__ == "__main__":
    unittest.main()
