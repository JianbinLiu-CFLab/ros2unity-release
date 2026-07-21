#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for ros2cs overlay release-gate validation.
#
# Modifications by Jianbin Liu:
# - Added focused regression coverage for overlay file and prefix-order validation.
# - Keeps temporary validation fixtures below the release workspace .build directory.

"""Focused regression tests for the ros2cs overlay validation helpers."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_PATH = Path(__file__).with_name("validate_ros2cs_overlay.py")
WORKSPACE_BUILD_ROOT = SCRIPT_PATH.resolve().parents[2] / ".build"


def workspace_tempdir() -> tempfile.TemporaryDirectory:
    """Create validation fixtures inside the workspace-owned build directory."""
    WORKSPACE_BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=WORKSPACE_BUILD_ROOT)


def load_module():
    spec = importlib.util.spec_from_file_location("validate_ros2cs_overlay", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ValidateRos2csOverlayTests(unittest.TestCase):
    """Validate path and prefix checks without requiring a local ROS installation."""

    def test_parse_cmd_environment_keeps_environment_assignments(self):
        module = load_module()

        environment = module.parse_cmd_environment("noise\nAMENT_PREFIX_PATH=C:\\overlay;C:\\ros\nCMAKE_PREFIX_PATH=C:\\overlay;C:\\ros\n")

        self.assertEqual(environment["AMENT_PREFIX_PATH"], "C:\\overlay;C:\\ros")
        self.assertEqual(environment["CMAKE_PREFIX_PATH"], "C:\\overlay;C:\\ros")

    def test_prefix_order_accepts_overlay_first(self):
        module = load_module()

        with workspace_tempdir() as directory:
            install_base = Path(directory) / "install"
            base_ros = Path(directory) / "ros"
            install_base.mkdir()
            base_ros.mkdir()
            overlay = str(install_base.resolve())
            module.validate_overlay_prefix_order(
                {
                    "AMENT_PREFIX_PATH": overlay + os.pathsep + str(base_ros.resolve()),
                    "CMAKE_PREFIX_PATH": overlay + os.pathsep + str(base_ros.resolve()),
                },
                install_base,
            )

    def test_prefix_order_rejects_base_ros_before_overlay(self):
        module = load_module()

        with workspace_tempdir() as directory:
            install_base = Path(directory) / "install"
            base_ros = Path(directory) / "ros"
            install_base.mkdir()
            base_ros.mkdir()

            with self.assertRaisesRegex(RuntimeError, "AMENT_PREFIX_PATH starts"):
                module.validate_overlay_prefix_order(
                    {
                        "AMENT_PREFIX_PATH": str(base_ros.resolve()) + os.pathsep + str(install_base.resolve()),
                        "CMAKE_PREFIX_PATH": str(install_base.resolve()) + os.pathsep + str(base_ros.resolve()),
                    },
                    install_base,
                )

    def test_required_paths_report_missing_overlay_member(self):
        module = load_module()

        with workspace_tempdir() as directory:
            root = Path(directory)

            with self.assertRaisesRegex(RuntimeError, "missing required files"):
                module.validate_required_paths(root, ("lib/dotnet/ros2cs_common.dll",), "ros2cs overlay")


if __name__ == "__main__":
    unittest.main()
