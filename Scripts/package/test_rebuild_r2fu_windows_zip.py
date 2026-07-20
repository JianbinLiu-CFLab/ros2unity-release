import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock


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

    def test_release_tag_is_parsed(self):
        module = load_module()

        args = module.parse_args(["--release-tag", "v0.8.0"])

        self.assertEqual(args.release_tag, "v0.8.0")

    def test_release_source_identity_requires_matching_tags_and_pin(self):
        module = load_module()
        ros2cs_sha = "a" * 40
        ros2_for_unity_sha = "b" * 40

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            ros2cs_root = root / "ros2cs"
            ros2_for_unity_root = root / "ros2-for-unity"
            ros2cs_root.mkdir()
            ros2_for_unity_root.mkdir()
            (ros2_for_unity_root / "ros2cs.repos").write_text(
                "repositories:\n"
                "  src/ros2cs/:\n"
                "    version: " + ros2cs_sha + "\n",
                encoding="utf-8",
            )

            def fake_run_git(repo, *args):
                if args == ("describe", "--exact-match", "--tags", "HEAD"):
                    return "v0.8.0"
                if args == ("rev-parse", "HEAD"):
                    return ros2cs_sha if repo == ros2cs_root else ros2_for_unity_sha
                return None

            with mock.patch.object(module.packager, "run_git", side_effect=fake_run_git):
                identity = module.validate_release_source_identity(
                    ros2cs_root=ros2cs_root,
                    ros2_for_unity_root=ros2_for_unity_root,
                    release_tag="v0.8.0",
                )

            self.assertEqual(identity["ros2csSha"], ros2cs_sha)
            self.assertEqual(identity["ros2ForUnitySha"], ros2_for_unity_sha)

    def test_release_source_identity_rejects_wrong_ros2cs_pin(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            ros2cs_root = root / "ros2cs"
            ros2_for_unity_root = root / "ros2-for-unity"
            ros2cs_root.mkdir()
            ros2_for_unity_root.mkdir()
            (ros2_for_unity_root / "ros2cs.repos").write_text(
                "repositories:\n"
                "  src/ros2cs/:\n"
                "    version: " + "c" * 40 + "\n",
                encoding="utf-8",
            )

            def fake_run_git(repo, *args):
                if args == ("describe", "--exact-match", "--tags", "HEAD"):
                    return "v0.8.0"
                if args == ("rev-parse", "HEAD"):
                    return "a" * 40 if repo == ros2cs_root else "b" * 40
                return None

            with mock.patch.object(module.packager, "run_git", side_effect=fake_run_git):
                with self.assertRaisesRegex(RuntimeError, "does not match ros2cs HEAD"):
                    module.validate_release_source_identity(
                        ros2cs_root=ros2cs_root,
                        ros2_for_unity_root=ros2_for_unity_root,
                        release_tag="v0.8.0",
                    )

    def test_release_source_identity_rejects_wrong_tag(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            ros2cs_root = root / "ros2cs"
            ros2_for_unity_root = root / "ros2-for-unity"
            ros2cs_root.mkdir()
            ros2_for_unity_root.mkdir()
            (ros2_for_unity_root / "ros2cs.repos").write_text(
                "repositories:\n"
                "  src/ros2cs/:\n"
                "    version: " + "a" * 40 + "\n",
                encoding="utf-8",
            )

            def fake_run_git(repo, *args):
                if args == ("describe", "--exact-match", "--tags", "HEAD"):
                    return "v0.7.0" if repo == ros2cs_root else "v0.8.0"
                if args == ("rev-parse", "HEAD"):
                    return "a" * 40 if repo == ros2cs_root else "b" * 40
                return None

            with mock.patch.object(module.packager, "run_git", side_effect=fake_run_git):
                with self.assertRaisesRegex(RuntimeError, "must be tagged 'v0.8.0'"):
                    module.validate_release_source_identity(
                        ros2cs_root=ros2cs_root,
                        ros2_for_unity_root=ros2_for_unity_root,
                        release_tag="v0.8.0",
                    )

    def test_release_source_identity_rejects_untagged_head(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            ros2cs_root = root / "ros2cs"
            ros2_for_unity_root = root / "ros2-for-unity"
            ros2cs_root.mkdir()
            ros2_for_unity_root.mkdir()
            (ros2_for_unity_root / "ros2cs.repos").write_text(
                "repositories:\n"
                "  src/ros2cs/:\n"
                "    version: " + "a" * 40 + "\n",
                encoding="utf-8",
            )

            def fake_run_git(repo, *args):
                if args == ("describe", "--exact-match", "--tags", "HEAD"):
                    return None
                if args == ("rev-parse", "HEAD"):
                    return "a" * 40 if repo == ros2cs_root else "b" * 40
                return None

            with mock.patch.object(module.packager, "run_git", side_effect=fake_run_git):
                with self.assertRaisesRegex(RuntimeError, "found no exact tag"):
                    module.validate_release_source_identity(
                        ros2cs_root=ros2cs_root,
                        ros2_for_unity_root=ros2_for_unity_root,
                        release_tag="v0.8.0",
                    )


if __name__ == "__main__":
    unittest.main()
