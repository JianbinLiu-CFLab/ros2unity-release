#!/usr/bin/env python3
"""Unit tests for the Plan28 interleaved benchmark runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run_spin_benchmark_ab.py")


def load_module():
    spec = importlib.util.spec_from_file_location("run_spin_benchmark_ab", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RunSpinBenchmarkAbTests(unittest.TestCase):
    def test_configuration_matrix_is_fixed(self):
        module = load_module()
        self.assertEqual(len(module.CONFIGURATIONS), 16)
        self.assertIn((500, "few-active"), module.CONFIGURATIONS)

    def test_install_pair_rejects_same_and_nested_paths(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install_a = root / "a"
            install_b = root / "b"
            install_a.mkdir()
            install_b.mkdir()
            for install in (install_a, install_b):
                benchmark = install / "lib" / "ros2cs_spin_benchmarks" / "dotnet"
                benchmark.mkdir(parents=True)
                (benchmark / "ros2cs_spin_benchmark.dll").write_text("", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "different"):
                module.validate_install_pair(install_a, install_a)
            with self.assertRaisesRegex(RuntimeError, "nested"):
                module.validate_install_pair(install_a, install_a / "nested")

    def test_bootstrap_ci_is_deterministic(self):
        module = load_module()
        values = [float(index) for index in range(10)]
        self.assertEqual(module.bootstrap_ci(values), module.bootstrap_ci(values))
        self.assertEqual(len(module.bootstrap_ci(values)), 2)

    def test_dirty_source_requires_label(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / ".git").mkdir()
            original = module.run_git
            try:
                module.run_git = lambda _root, *args: " M source.cs" if args == ("status", "--porcelain") else "abc"
                with self.assertRaisesRegex(RuntimeError, "dirty source requires"):
                    module.source_metadata(source, None)
            finally:
                module.run_git = original


if __name__ == "__main__":
    unittest.main()
