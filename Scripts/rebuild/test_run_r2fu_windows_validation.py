# Modifications Copyright (c) 2026 Jianbin Liu-CFLab.
#
# Modifications by Jianbin Liu:
# - Added regression coverage for transparent Windows long-path subst-drive mapping.

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

    def test_r2fu_build_honors_a_subst_mapped_ros2cs_root_without_drive_root_scratch(self):
        build_script = WORKSPACE_ROOT / "third-party" / "ros2-for-unity" / "build.ps1"
        text = build_script.read_text(encoding="utf-8")

        self.assertIn("R2FU_ROS2CS_ROOT", text)
        self.assertIn("skip_ros2cs_clean", text)
        self.assertIn('Join-Path -Path $scriptPath -ChildPath ".build"', text)
        self.assertNotIn("GetPathRoot($scriptPath)", text)
        self.assertIn("New-OwnedSubstDrive", text)
        self.assertIn("Remove-OwnedSubstDrives", text)
        self.assertIn("default ros2cs source", text)
        self.assertIn("default ros2cs build/log", text)

        validation_text = VALIDATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("New-OwnedSubstDrive", validation_text)
        self.assertIn("Remove-OwnedSubstDrives", validation_text)
        self.assertNotIn("New-OwnedJunctionPath", validation_text)
        self.assertNotIn("CMAKE_OBJECT_PATH_MAX", validation_text)

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
                "-Clean",
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
        path_mapping = summary["pathMapping"]
        self.assertTrue(path_mapping["enabled"])
        self.assertEqual(path_mapping["mode"], "subst")
        self.assertEqual(pathlib.Path(path_mapping["physicalRunRoot"]), run_root)
        self.assertNotEqual(pathlib.Path(path_mapping["mappedRos2csBuildBase"]), run_root / "build")
        self.assertEqual(set(path_mapping["drives"]), {"build", "r2fu", "ros2cs"})
        rows_by_name = {row["name"]: row for row in summary["rows"]}
        self.assertEqual(
            pathlib.Path(rows_by_name["r2fu standalone build"]["cwd"]),
            pathlib.Path(path_mapping["mappedR2fuRoot"]),
        )
        self.assertEqual(
            pathlib.Path(rows_by_name["ros2cs_tests"]["cwd"]),
            pathlib.Path(path_mapping["mappedRos2csRoot"]),
        )
        self.assertIn(path_mapping["mappedRos2csBuildBase"], rows_by_name["ros2cs_tests"]["command"])
        self.assertIn(path_mapping["mappedR2fuRoot"], rows_by_name["r2fu native plugin compile surface"]["command"])
        subst_output = subprocess.run(
            ["subst"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.upper()
        for mapped_root in (
            path_mapping["mappedR2fuRoot"],
            path_mapping["mappedRos2csRoot"],
            path_mapping["mappedRos2csBuildBase"],
        ):
            drive = pathlib.PureWindowsPath(mapped_root).drive.upper()
            self.assertNotIn(f"{drive}\\: =>", subst_output)


if __name__ == "__main__":
    unittest.main()
