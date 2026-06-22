import importlib.util
import pathlib
import unittest


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "rebuild" / "rebuild_r2fu_windows_zip.py"


def load_module():
    spec = importlib.util.spec_from_file_location("rebuild_r2fu_windows_zip", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RebuildR2FUWindowsArtifactZipTest(unittest.TestCase):
    def test_validation_command_runs_full_ladder_by_default(self):
        module = load_module()
        command = module.validation_command(
            workspace_root=pathlib.Path("workspace"),
            ros_distro="jazzy",
            clean=False,
            dry_run=False,
            console_direct=False,
            parallel_workers=8,
        )
        command_text = " ".join(command)

        self.assertIn("run_r2fu_windows_validation.ps1", command_text)
        self.assertIn("-ParallelWorkers", command)
        self.assertIn("8", command)
        self.assertNotIn("-SkipBuild", command)
        self.assertNotIn("-SkipTests", command)
        self.assertNotIn("-SkipAssetSanity", command)
        self.assertNotIn("-DryRun", command)

    def test_validation_command_can_clean_and_dry_run(self):
        module = load_module()
        command = module.validation_command(
            workspace_root=pathlib.Path("workspace"),
            ros_distro="jazzy",
            clean=True,
            dry_run=True,
            console_direct=True,
            parallel_workers=4,
        )

        self.assertIn("-Clean", command)
        self.assertIn("-DryRun", command)
        self.assertIn("-ConsoleDirect", command)
        self.assertIn("4", command)


if __name__ == "__main__":
    unittest.main()
