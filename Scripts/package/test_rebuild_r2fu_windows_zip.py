# Modifications Copyright (c) 2026 Jianbin Liu-CFLab.
#
# Modifications by Jianbin Liu:
# - Keeps release-script test fixtures below the workspace-owned .build directory.

import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "rebuild" / "rebuild_r2fu_windows_zip.py"
WORKSPACE_BUILD_ROOT = SCRIPT_PATH.resolve().parents[2] / ".build"


def workspace_tempdir() -> tempfile.TemporaryDirectory:
    """Create release-script fixtures inside the workspace-owned build directory."""
    WORKSPACE_BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=WORKSPACE_BUILD_ROOT)


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

    def test_validation_command_routes_isolated_source_and_run_roots(self):
        module = load_module()
        r2fu_root = pathlib.Path("workspace/.build/matrix/r2fu-humble")
        ros2cs_root = pathlib.Path("workspace/.build/matrix/ros2cs-humble")
        run_root = pathlib.Path("workspace/.build/matrix/runs/humble")

        command = module.validation_command(
            workspace_root=pathlib.Path("workspace"),
            ros_distro="humble",
            clean=True,
            dry_run=False,
            console_direct=False,
            parallel_workers=8,
            r2fu_root=r2fu_root,
            ros2cs_root=ros2cs_root,
            run_root=run_root,
        )

        self.assertEqual(command[command.index("-R2fuRoot") + 1], str(r2fu_root))
        self.assertEqual(command[command.index("-Ros2csRoot") + 1], str(ros2cs_root))
        self.assertEqual(command[command.index("-RunRoot") + 1], str(run_root))

    def test_release_tag_is_parsed(self):
        module = load_module()

        args = module.parse_args(["--release-tag", "v0.8.0"])

        self.assertEqual(args.release_tag, "v0.8.0")

    def test_isolated_release_roots_are_parsed(self):
        module = load_module()

        try:
            args = module.parse_args([
                "--r2fu-root", "workspace/.build/matrix/r2fu-humble",
                "--ros2cs-root", "workspace/.build/matrix/ros2cs-humble",
                "--run-root", "workspace/.build/matrix/runs/humble",
            ])
        except SystemExit as error:
            self.fail(f"isolated release roots must be accepted: {error}")

        self.assertEqual(args.r2fu_root, pathlib.Path("workspace/.build/matrix/r2fu-humble"))
        self.assertEqual(args.ros2cs_root, pathlib.Path("workspace/.build/matrix/ros2cs-humble"))
        self.assertEqual(args.run_root, pathlib.Path("workspace/.build/matrix/runs/humble"))

    def test_main_uses_isolated_roots_for_release_identity(self):
        module = load_module()

        with tempfile.TemporaryDirectory(dir=SCRIPT_PATH.resolve().parents[2] / ".build") as temp_dir:
            root = pathlib.Path(temp_dir)
            r2fu_root = root / "r2fu"
            ros2cs_root = root / "ros2cs"
            run_root = root / "run"
            asset_dir = root / "asset"
            output_dir = root / "out"
            r2fu_root.mkdir()
            ros2cs_root.mkdir()

            result = mock.Mock(
                zip_path=output_dir / "artifact.zip",
                sha256="a" * 64,
                sha256_path=output_dir / "artifact.sha256.txt",
                manifest_path=output_dir / "artifact.manifest.json",
            )
            with mock.patch.object(module, "validate_release_source_identity", return_value={
                "releaseTag": "v0.8.1",
                "ros2csSha": "a" * 40,
                "ros2ForUnitySha": "b" * 40,
                "ros2csReposPin": "a" * 40,
            }) as identity_gate, mock.patch.object(module.subprocess, "run") as subprocess_run, mock.patch.object(
                module.packager, "validate_release_metadata", return_value={}
            ), mock.patch.object(module.packager, "package_asset", return_value=result) as package_asset:
                exit_code = module.main([
                    "--release-tag", "v0.8.1",
                    "--r2fu-root", str(r2fu_root),
                    "--ros2cs-root", str(ros2cs_root),
                    "--run-root", str(run_root),
                    "--asset-dir", str(asset_dir),
                    "--output-dir", str(output_dir),
                ])

            self.assertEqual(exit_code, 0)
            identity_kwargs = identity_gate.call_args.kwargs
            self.assertIn("ros2_for_unity_root", identity_kwargs)
            self.assertIn("ros2cs_root", identity_kwargs)
            self.assertEqual(identity_kwargs["ros2_for_unity_root"], r2fu_root)
            self.assertEqual(identity_kwargs["ros2cs_root"], ros2cs_root)
            validation_command = subprocess_run.call_args.args[0]
            self.assertEqual(validation_command[validation_command.index("-R2fuRoot") + 1], str(r2fu_root))
            self.assertEqual(validation_command[validation_command.index("-Ros2csRoot") + 1], str(ros2cs_root))
            self.assertEqual(validation_command[validation_command.index("-RunRoot") + 1], str(run_root))
            package_kwargs = package_asset.call_args.kwargs
            self.assertIn("ros2_for_unity_root", package_kwargs)
            self.assertIn("ros2cs_root", package_kwargs)
            self.assertEqual(package_kwargs["ros2_for_unity_root"], r2fu_root)
            self.assertEqual(package_kwargs["ros2cs_root"], ros2cs_root)

    def test_release_source_identity_requires_matching_tags_and_pin(self):
        module = load_module()
        ros2cs_sha = "a" * 40
        ros2_for_unity_sha = "b" * 40

        with workspace_tempdir() as temp_dir:
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

        with workspace_tempdir() as temp_dir:
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

        with workspace_tempdir() as temp_dir:
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

        with workspace_tempdir() as temp_dir:
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
