#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Modifications by Jianbin Liu:
# - Added regression coverage for the R2FU native-plugin bootstrap compile-surface gate.

"""Focused tests for R2FU custom-typesupport native plugin compile-surface validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_PATH = Path(__file__).with_name("verify_r2fu_native_plugin_bootstrap.py")


def load_module():
    """Load the validation script as a testable module without changing sys.path."""
    spec = importlib.util.spec_from_file_location("verify_r2fu_native_plugin_bootstrap", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class R2fuNativePluginBootstrapCompileSurfaceTest(unittest.TestCase):
    """Tests path validation, source-order checks, and generated probe shape without invoking dotnet."""

    def create_r2fu_sources(self, root: Path, *, seal_before_init: bool = True) -> tuple[Path, Path]:
        """Write the minimum source layout required by the validation helpers."""
        module = load_module()
        bootstrap = root / module.BOOTSTRAP_RELATIVE_PATH
        initializer = root / module.INITIALIZER_RELATIVE_PATH
        bootstrap.parent.mkdir(parents=True, exist_ok=True)
        initializer.parent.mkdir(parents=True, exist_ok=True)
        bootstrap.write_text("public static class Ros2ForUnityNativePluginBootstrap { }\n", encoding="utf-8")
        ordered_calls = (
            "Ros2ForUnityNativePluginBootstrap.SealNativeLibraryRegistration();\nRos2cs.Init();"
            if seal_before_init
            else "Ros2cs.Init();\nRos2ForUnityNativePluginBootstrap.SealNativeLibraryRegistration();"
        )
        initializer.write_text(ordered_calls, encoding="utf-8")
        return bootstrap, initializer

    def test_source_paths_and_initializer_order_accept_valid_layout(self):
        """Accept a valid bootstrap/initializer pair with seal-before-init ordering."""
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bootstrap, initializer = self.create_r2fu_sources(root)

            actual_bootstrap, actual_initializer = module.require_source_paths(root)
            module.validate_initializer_order(actual_initializer)

            self.assertEqual(actual_bootstrap, bootstrap)
            self.assertEqual(actual_initializer, initializer)

    def test_initializer_order_rejects_late_seal(self):
        """Reject a source layout that would allow a native load before registration is sealed."""
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            _, initializer = self.create_r2fu_sources(Path(directory), seal_before_init=False)

            with self.assertRaisesRegex(RuntimeError, "after Ros2cs.Init"):
                module.validate_initializer_order(initializer)

    def test_compile_project_references_real_bootstrap_and_generated_catalog_probe(self):
        """Generate a project whose compilation fails if the public registration facade disappears."""
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bootstrap, _ = self.create_r2fu_sources(root)
            project_root = root / "scratch"
            project_root.mkdir()

            project_path = module.write_compile_surface_project(project_root, bootstrap)
            project = project_path.read_text(encoding="utf-8")
            probe = (project_root / "OptionalCatalogCompileProbe.cs").read_text(encoding="utf-8")

            self.assertIn(bootstrap.resolve().as_posix(), project)
            self.assertIn("UNITY_EDITOR", project)
            self.assertIn("EnableDefaultCompileItems>false", project)
            self.assertIn(
                "Ros2ForUnityNativePluginBootstrap.RegisterEditorPackagePluginDirectory",
                probe,
            )


if __name__ == "__main__":
    unittest.main()
