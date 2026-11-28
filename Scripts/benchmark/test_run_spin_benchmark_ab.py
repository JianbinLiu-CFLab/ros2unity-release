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

    def test_execution_order_alternates_and_rejects_zero(self):
        module = load_module()
        self.assertEqual(module.execution_order(1), ("A", "B"))
        self.assertEqual(module.execution_order(2), ("B", "A"))
        self.assertEqual(module.execution_order(3), ("A", "B"))
        with self.assertRaises(ValueError):
            module.execution_order(0)

    def test_pair_summary_uses_median_for_relative_gate(self):
        module = load_module()
        summary = module.summarize_pair(
            [100.0, 100.0, 100.0, 100.0, 10000.0],
            [90.0, 90.0, 90.0, 90.0, 8000.0],
            lower_is_better=True,
        )
        self.assertEqual(summary["statistics"], "median")
        self.assertEqual(summary["relativeChangePercent"], -10.0)
        self.assertNotEqual(summary["meanRelativeChangePercent"], -10.0)
        self.assertEqual(summary["differenceCandidateMinusBaseline"]["bootstrapStatistic"], "median")

    def test_pair_summary_reports_iqr_outliers(self):
        module = load_module()
        summary = module.summarize_pair(
            [1.0, 1.0, 2.0, 2.0, 100.0],
            [1.0, 1.0, 2.0, 2.0, 2.0],
            lower_is_better=True,
        )
        self.assertEqual(summary["baseline"]["outliers"], [{"repetition": 5, "value": 100.0}])
        self.assertEqual(summary["candidate"]["outliers"], [])

    def test_repetitions_require_protocol_minimum(self):
        module = load_module()
        with self.assertRaises(SystemExit):
            module.parse_args([
                "--source-a", ".",
                "--source-b", ".",
                "--install-a", "a",
                "--install-b", "b",
                "--output-dir", "out",
                "--repetitions", "9",
            ])

    def test_launcher_command_reapplies_install_overlay(self):
        module = load_module()
        command = module.make_launcher_command(
            Path("D:/tools/Enter-Ros2JazzyEnv.py"),
            ["dotnet", "benchmark.dll", "--output", "D:/results/run one.json"],
            Path("D:/installs/variant-a"),
        )
        script = command[-1]
        self.assertIn("$env:AMENT_PREFIX_PATH", script)
        self.assertIn("$env:COLCON_PREFIX_PATH", script)
        self.assertIn("variant-a", script)
        self.assertIn("'D:/results/run one.json'", script)

    def test_dirty_source_requires_label(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / ".git").write_text("gitdir: metadata", encoding="utf-8")
            original = module.run_git
            try:
                module.run_git = lambda _root, *args: (
                    " M source.cs" if args == ("status", "--porcelain")
                    else "true" if args == ("rev-parse", "--is-inside-work-tree")
                    else "abc"
                )
                with self.assertRaisesRegex(RuntimeError, "dirty source requires"):
                    module.source_metadata(source, None)
            finally:
                module.run_git = original

    def test_spin_duration_capacity_must_not_expand(self):
        module = load_module()
        result = {"metrics": {"spinDurationCapacityExpansions": 0}}
        module.validate_spin_duration_capacity(result, "A", 10, "low")
        with self.assertRaisesRegex(RuntimeError, "expanded"):
            module.validate_spin_duration_capacity(
                {"metrics": {"spinDurationCapacityExpansions": 1}},
                "B",
                500,
                "few-active",
            )
        with self.assertRaisesRegex(RuntimeError, "invalid"):
            module.validate_spin_duration_capacity({"metrics": {}}, "A", 1, "idle")


if __name__ == "__main__":
    unittest.main()
