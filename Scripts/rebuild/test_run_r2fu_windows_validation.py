import json
import pathlib
import shutil
import subprocess
import unittest


WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parents[2]
VALIDATION_SCRIPT = WORKSPACE_ROOT / "Scripts" / "rebuild" / "run_r2fu_windows_validation.ps1"


@unittest.skipUnless(__import__("os").name == "nt", "Windows PowerShell validation script")
class RunR2FUWindowsValidationTest(unittest.TestCase):
    def test_r2fu_build_script_bounds_nested_parallelism(self):
        build_script = WORKSPACE_ROOT / "third-party" / "ros2-for-unity" / "build.ps1"
        text = build_script.read_text(encoding="utf-8")

        self.assertIn("function Resolve-Ros2csParallelWorkers", text)
        self.assertIn("$Env:ROS2CS_PARALLEL_WORKERS", text)
        self.assertIn("--parallel-workers", text)
        self.assertRegex(text, r'"--parallel-workers",\s*"1"')
        self.assertIn('$env:MAKEFLAGS = "-j$ros2csParallelWorkers -l$ros2csParallelWorkers"', text)

    def test_dry_run_uses_an_isolated_run_root(self):
        run_root = WORKSPACE_ROOT / ".build" / "test-r2fu-isolated-validation"
        r2fu_root = WORKSPACE_ROOT / "third-party" / "ros2-for-unity"
        ros2cs_root = WORKSPACE_ROOT / "third-party" / "ros2cs"
        shutil.rmtree(run_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, run_root, True)

        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(VALIDATION_SCRIPT),
                "-DryRun",
                "-RosDistro",
                "jazzy",
                "-R2fuRoot",
                str(r2fu_root),
                "-Ros2csRoot",
                str(ros2cs_root),
                "-RunRoot",
                str(run_root),
            ],
            cwd=WORKSPACE_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        summaries = list((run_root / "reports").glob("r2fu-jazzy-windows-full-validation-*.json"))
        self.assertEqual(len(summaries), 1)
        summary = json.loads(summaries[0].read_text(encoding="utf-8-sig"))
        self.assertEqual(pathlib.Path(summary["runRoot"]), run_root)
        self.assertEqual(pathlib.Path(summary["r2fuRoot"]), r2fu_root)
        self.assertEqual(pathlib.Path(summary["ros2csRoot"]), ros2cs_root)


if __name__ == "__main__":
    unittest.main()
