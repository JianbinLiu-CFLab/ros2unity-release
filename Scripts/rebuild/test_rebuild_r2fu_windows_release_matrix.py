import importlib.util
import io
import pathlib
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock


SCRIPT_PATH = pathlib.Path(__file__).with_name("rebuild_r2fu_windows_release_matrix.py")


def load_module():
    spec = importlib.util.spec_from_file_location("rebuild_r2fu_windows_release_matrix", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RebuildR2FUWindowsReleaseMatrixTest(unittest.TestCase):
    def test_release_matrix_script_exists(self):
        self.assertTrue(SCRIPT_PATH.is_file(), f"missing release matrix script: {SCRIPT_PATH}")

    def test_worker_plan_caps_active_children_and_splits_the_total_budget(self):
        module = load_module()
        self.assertTrue(hasattr(module, "worker_plan"))

        self.assertEqual(module.worker_plan(total_workers=8, requested_concurrency=3), (3, 2))
        self.assertEqual(module.worker_plan(total_workers=8, requested_concurrency=1), (1, 8))
        self.assertEqual(module.worker_plan(total_workers=2, requested_concurrency=3), (2, 1))

        with self.assertRaisesRegex(ValueError, "positive"):
            module.worker_plan(total_workers=0, requested_concurrency=1)

    def test_distro_paths_are_isolated_below_one_compact_matrix_root(self):
        module = load_module()
        self.assertTrue(hasattr(module, "plan_distro_paths"))
        run_root = pathlib.Path("workspace/.build/m/260721123456")

        humble = module.plan_distro_paths(run_root, "humble")
        jazzy = module.plan_distro_paths(run_root, "jazzy")
        lyrical = module.plan_distro_paths(run_root, "lyrical")

        self.assertEqual(humble.r2fu_worktree, run_root / "h" / "u")
        self.assertEqual(humble.ros2cs_worktree, run_root / "h" / "c")
        self.assertEqual(humble.validation_root, run_root / "h")
        self.assertEqual(humble.asset_dir, humble.r2fu_worktree / "install" / "asset" / "Ros2ForUnity")
        self.assertEqual(
            len({humble.r2fu_worktree, jazzy.r2fu_worktree, lyrical.r2fu_worktree}),
            3,
        )
        self.assertEqual(
            len({humble.ros2cs_worktree, jazzy.ros2cs_worktree, lyrical.ros2cs_worktree}),
            3,
        )
        self.assertEqual(
            len({humble.validation_root, jazzy.validation_root, lyrical.validation_root}),
            3,
        )

    def test_default_run_root_and_path_budget_prevent_windows_object_path_overflow(self):
        module = load_module()
        self.assertTrue(hasattr(module, "default_run_root"))
        self.assertTrue(hasattr(module, "assert_windows_path_budget"))

        default_root = module.default_run_root(pathlib.Path("workspace"))
        self.assertEqual(default_root.parent.name, "m")
        self.assertRegex(default_root.name, r"^\d{12}$")

        safe_paths = module.plan_distro_paths(
            pathlib.Path("D:/ros2unity/.build/m/260721123456"),
            "jazzy",
        )
        module.assert_windows_path_budget(safe_paths)

        overflowing_paths = module.plan_distro_paths(
            pathlib.Path("D:/ros2unity/.build/r2fu-release-matrix/20260721-035623"),
            "jazzy",
        )
        with self.assertRaisesRegex(RuntimeError, "path budget"):
            module.assert_windows_path_budget(overflowing_paths)

    def test_rebuild_command_passes_every_isolation_root(self):
        module = load_module()
        self.assertTrue(hasattr(module, "rebuild_command"))
        workspace_root = pathlib.Path("workspace")
        paths = module.plan_distro_paths(pathlib.Path("workspace/.build/matrix/run"), "lyrical")

        command = module.rebuild_command(
            workspace_root=workspace_root,
            paths=paths,
            release_tag="v0.8.1",
            parallel_workers=8,
        )

        self.assertEqual(command[command.index("--ros-distro") + 1], "lyrical")
        self.assertEqual(command[command.index("--release-tag") + 1], "v0.8.1")
        self.assertEqual(command[command.index("--parallel-workers") + 1], "8")
        self.assertEqual(command[command.index("--r2fu-root") + 1], str(paths.r2fu_worktree))
        self.assertEqual(command[command.index("--ros2cs-root") + 1], str(paths.ros2cs_worktree))
        self.assertEqual(command[command.index("--run-root") + 1], str(paths.validation_root))
        self.assertEqual(command[command.index("--asset-dir") + 1], str(paths.asset_dir))
        self.assertIn("--clean", command)

    def test_worktree_commands_use_tagged_sources_and_a_private_junction(self):
        module = load_module()
        self.assertTrue(hasattr(module, "worktree_commands"))
        paths = module.plan_distro_paths(pathlib.Path("workspace/.build/matrix/run"), "jazzy")
        ros2cs_repo = pathlib.Path("workspace/third-party/ros2cs")
        r2fu_repo = pathlib.Path("workspace/third-party/ros2-for-unity")

        commands = module.worktree_commands(
            ros2cs_repo=ros2cs_repo,
            r2fu_repo=r2fu_repo,
            paths=paths,
            release_tag="v0.8.1",
        )

        self.assertEqual(
            commands[0],
            [
                "git", "-C", str(ros2cs_repo), "worktree", "add", "--detach",
                str(paths.ros2cs_worktree), "v0.8.1",
            ],
        )
        self.assertEqual(
            commands[1],
            [
                "git", "-C", str(r2fu_repo), "worktree", "add", "--detach",
                str(paths.r2fu_worktree), "v0.8.1",
            ],
        )
        self.assertEqual(
            commands[2],
            [
                "cmd", "/d", "/c", "mklink", "/J",
                str(paths.r2fu_worktree / "src" / "ros2cs"), str(paths.ros2cs_worktree),
            ],
        )

    def test_run_root_must_stay_below_workspace_build_directory(self):
        module = load_module()
        self.assertTrue(hasattr(module, "resolve_run_root"))
        workspace_root = pathlib.Path("workspace").resolve()
        requested_root = pathlib.Path(".build/r2fu-release-matrix/run")

        resolved = module.resolve_run_root(
            workspace_root,
            requested_root,
        )

        self.assertEqual(resolved, workspace_root / requested_root)
        with self.assertRaisesRegex(RuntimeError, "must stay below"):
            module.resolve_run_root(workspace_root, pathlib.Path("D:/outside"))

    def test_matrix_cli_requires_a_tag_and_defaults_to_three_children(self):
        module = load_module()
        self.assertTrue(hasattr(module, "parse_args"))

        args = module.parse_args(["--release-tag", "v0.8.1"])

        self.assertEqual(args.release_tag, "v0.8.1")
        self.assertEqual(args.max_concurrency, 3)
        self.assertIsNone(args.run_root)
        self.assertFalse(args.keep_worktrees)
        self.assertFalse(args.dry_run)

    def test_dry_run_prints_three_isolated_child_commands(self):
        module = load_module()
        self.assertTrue(hasattr(module, "workspace_root"))
        workspace_root = pathlib.Path("workspace").resolve()
        output = io.StringIO()

        with mock.patch.object(module, "workspace_root", return_value=workspace_root), redirect_stdout(output):
            exit_code = module.main([
                "--release-tag", "v0.8.1",
                "--run-root", ".build/m/dryrun",
                "--dry-run",
            ])

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("--ros-distro humble", text)
        self.assertIn("--ros-distro jazzy", text)
        self.assertIn("--ros-distro lyrical", text)
        self.assertEqual(text.count("--r2fu-root"), 3)
        self.assertEqual(text.count("--ros2cs-root"), 3)
        self.assertEqual(text.count("--run-root"), 3)

    def test_prepare_child_worktree_runs_tagged_commands_in_order(self):
        module = load_module()
        self.assertTrue(hasattr(module, "prepare_child_worktree"))
        workspace_root = SCRIPT_PATH.resolve().parents[2]
        ros2cs_repo = workspace_root / "third-party" / "ros2cs"
        r2fu_repo = workspace_root / "third-party" / "ros2-for-unity"

        with tempfile.TemporaryDirectory(dir=workspace_root / ".build") as temp_dir:
            paths = module.plan_distro_paths(pathlib.Path(temp_dir), "humble")
            expected = module.worktree_commands(
                ros2cs_repo=ros2cs_repo,
                r2fu_repo=r2fu_repo,
                paths=paths,
                release_tag="v0.8.1",
            )

            with mock.patch.object(module.subprocess, "run") as run:
                module.prepare_child_worktree(
                    ros2cs_repo=ros2cs_repo,
                    r2fu_repo=r2fu_repo,
                    paths=paths,
                    release_tag="v0.8.1",
                )

            self.assertEqual([call.args[0] for call in run.call_args_list], expected)

    def test_cleanup_unlinks_private_junction_before_removing_worktrees(self):
        module = load_module()
        self.assertTrue(hasattr(module, "cleanup_child_worktree"))
        paths = module.plan_distro_paths(pathlib.Path("workspace/.build/matrix/run"), "lyrical")
        ros2cs_repo = pathlib.Path("workspace/third-party/ros2cs")
        r2fu_repo = pathlib.Path("workspace/third-party/ros2-for-unity")

        with mock.patch.object(module.subprocess, "run") as run:
            module.cleanup_child_worktree(
                ros2cs_repo=ros2cs_repo,
                r2fu_repo=r2fu_repo,
                paths=paths,
            )

        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["cmd", "/d", "/c", "rmdir", str(paths.r2fu_worktree / "src" / "ros2cs")],
                ["git", "-C", str(r2fu_repo), "worktree", "remove", "--force", str(paths.r2fu_worktree)],
                ["git", "-C", str(ros2cs_repo), "worktree", "remove", "--force", str(paths.ros2cs_worktree)],
            ],
        )

    def test_run_children_writes_one_log_per_isolated_distro(self):
        module = load_module()
        self.assertTrue(hasattr(module, "run_children"))
        workspace_root = SCRIPT_PATH.resolve().parents[2]

        with tempfile.TemporaryDirectory(dir=workspace_root / ".build") as temp_dir:
            run_root = pathlib.Path(temp_dir)
            paths = [module.plan_distro_paths(run_root, distro) for distro in module.REQUIRED_ROS_DISTROS]
            commands = [["python", "child", paths_item.ros_distro] for paths_item in paths]

            def fake_run(command, *, cwd, stdout, stderr, check):
                stdout.write(" ".join(command) + "\n")
                return types.SimpleNamespace(returncode=0)

            with mock.patch.object(module.subprocess, "run", side_effect=fake_run) as run:
                results = module.run_children(
                    workspace_root=workspace_root,
                    children=list(zip(paths, commands, strict=True)),
                    max_concurrency=3,
            )

            self.assertEqual([result.returncode for result in results], [0, 0, 0])
            self.assertEqual(sorted(call.args[0] for call in run.call_args_list), sorted(commands))
            for paths_item, command in zip(paths, commands, strict=True):
                log_path = paths_item.validation_root / "matrix-child.log"
                self.assertTrue(log_path.is_file())
                self.assertIn(" ".join(command), log_path.read_text(encoding="utf-8"))

    def test_execute_matrix_validates_only_after_all_children_pass_and_then_cleans_up(self):
        module = load_module()
        self.assertTrue(hasattr(module, "execute_matrix"))
        workspace_root = SCRIPT_PATH.resolve().parents[2]
        ros2cs_repo = workspace_root / "third-party" / "ros2cs"
        r2fu_repo = workspace_root / "third-party" / "ros2-for-unity"

        with tempfile.TemporaryDirectory(dir=workspace_root / ".build") as temp_dir:
            run_root = pathlib.Path(temp_dir) / "matrix"
            paths = [module.plan_distro_paths(run_root, distro) for distro in module.REQUIRED_ROS_DISTROS]
            results = [
                module.ChildResult(paths_item, ("python", "child", paths_item.ros_distro), paths_item.validation_root / "matrix-child.log", 0)
                for paths_item in paths
            ]

            with mock.patch.object(module, "prepare_child_worktree") as prepare, mock.patch.object(
                module, "run_children", return_value=results
            ) as run_children, mock.patch.object(module, "cleanup_child_worktree") as cleanup, mock.patch.object(
                module, "validate_release_artifacts", return_value=["humble", "jazzy", "lyrical"]
            ) as validate_artifacts:
                artifacts = module.execute_matrix(
                    workspace_root=workspace_root,
                    ros2cs_repo=ros2cs_repo,
                    r2fu_repo=r2fu_repo,
                    run_root=run_root,
                    release_tag="v0.8.1",
                    parallel_workers=8,
                    max_concurrency=3,
                    keep_worktrees=False,
                )

            self.assertEqual(artifacts, ["humble", "jazzy", "lyrical"])
            self.assertEqual(prepare.call_count, 3)
            self.assertEqual(run_children.call_count, 1)
            self.assertEqual(validate_artifacts.call_count, 1)
            self.assertEqual(cleanup.call_count, 3)
            self.assertEqual(run_children.call_args.kwargs["max_concurrency"], 3)
            for _, command in run_children.call_args.kwargs["children"]:
                self.assertEqual(command[command.index("--parallel-workers") + 1], "2")

    def test_main_preflights_tagged_sources_before_executing_matrix(self):
        module = load_module()
        self.assertTrue(hasattr(module, "validate_release_sources"))
        workspace_root = SCRIPT_PATH.resolve().parents[2]
        ros2cs_repo = workspace_root / "third-party" / "ros2cs"
        r2fu_repo = workspace_root / "third-party" / "ros2-for-unity"

        with tempfile.TemporaryDirectory(dir=workspace_root / ".build") as temp_dir:
            run_root = pathlib.Path(temp_dir) / "matrix"
            with mock.patch.object(module, "validate_release_sources") as validate_sources, mock.patch.object(
                module, "execute_matrix", return_value=[]
            ) as execute:
                exit_code = module.main([
                    "--release-tag", "v0.8.1",
                    "--parallel-workers", "8",
                    "--run-root", str(run_root),
                ])

            self.assertEqual(exit_code, 0)
            self.assertEqual(validate_sources.call_args.kwargs["ros2cs_repo"], ros2cs_repo)
            self.assertEqual(validate_sources.call_args.kwargs["r2fu_repo"], r2fu_repo)
            self.assertEqual(validate_sources.call_args.kwargs["release_tag"], "v0.8.1")
            self.assertEqual(execute.call_args.kwargs["run_root"], run_root)


if __name__ == "__main__":
    unittest.main()
