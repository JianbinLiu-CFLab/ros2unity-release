#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Modifications by Jianbin Liu:
# - Added a Windows release matrix entrypoint for isolated Humble, Jazzy, and Lyrical rebuilds.
# - Compacted matrix worktree and build paths to remain below the Windows MSVC generated-object path limit.
# - Split the total native build worker budget across active distro children to prevent nested parallel oversubscription.

"""Rebuild the three R2FU Windows release artifacts from isolated source worktrees."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import datetime as dt
import os
import pathlib
import subprocess
import sys


REQUIRED_ROS_DISTROS = ("humble", "jazzy", "lyrical")
DISTRO_PATH_SEGMENTS = {"humble": "h", "jazzy": "j", "lyrical": "l"}
WINDOWS_GENERATED_OBJECT_PATH_BUDGET = 250
WINDOWS_GENERATED_OBJECT_PROBE_SUFFIX = pathlib.Path(
    "build/unique_identifier_msgs/CMakeFiles/"
    "unique_identifier_msgs_s__rosidl_typesupport_fastrtps_c.dir/"
    "aafacfe125e28cc4aa6fe9488e26b456/"
    "_unique_identifier_msgs_s.ep.rosidl_typesupport_fastrtps_c.c.obj"
)


@dataclass(frozen=True)
class DistroPaths:
    """Own every source and output path used by one matrix child process."""

    ros_distro: str
    r2fu_worktree: pathlib.Path
    ros2cs_worktree: pathlib.Path
    validation_root: pathlib.Path
    asset_dir: pathlib.Path


@dataclass(frozen=True)
class ChildResult:
    """Outcome and retained log location for one complete distro release ladder."""

    paths: DistroPaths
    command: tuple[str, ...]
    log_path: pathlib.Path
    returncode: int


def resolve_run_root(workspace_root: pathlib.Path, requested_root: pathlib.Path) -> pathlib.Path:
    """Resolve one matrix run root and reject every path outside the workspace build scratch area."""
    workspace_root = workspace_root.resolve()
    build_root = (workspace_root / ".build").resolve()
    candidate = requested_root.resolve() if requested_root.is_absolute() else (workspace_root / requested_root).resolve()
    try:
        candidate.relative_to(build_root)
    except ValueError as error:
        raise RuntimeError(f"Matrix run root must stay below '{build_root}', got '{candidate}'.") from error
    if candidate == build_root:
        raise RuntimeError(f"Matrix run root must be a child of '{build_root}', not the build root itself.")
    return candidate


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the fixed three-distro matrix inputs without accepting an implicit release version."""
    default_workers = max(1, os.cpu_count() or 1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True, help="Common tagged source revision, for example v0.8.1.")
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=default_workers,
        help="Total native build worker budget shared across active distro children.",
    )
    parser.add_argument("--max-concurrency", type=int, default=len(REQUIRED_ROS_DISTROS), help="Maximum active distro child processes.")
    parser.add_argument("--run-root", type=pathlib.Path, help="Optional matrix run root below workspace .build.")
    parser.add_argument("--keep-worktrees", action="store_true", help="Retain isolated worktrees under .build after the run.")
    parser.add_argument("--dry-run", action="store_true", help="Print the fixed matrix plan without creating worktrees or artifacts.")
    return parser.parse_args(argv)


def worker_plan(*, total_workers: int, requested_concurrency: int) -> tuple[int, int]:
    """Return active distro-child count and per-child native worker limit without exceeding the total budget."""
    if total_workers < 1:
        raise ValueError("total worker budget must be positive.")
    if requested_concurrency < 1:
        raise ValueError("requested child concurrency must be positive.")

    active_children = min(total_workers, requested_concurrency, len(REQUIRED_ROS_DISTROS))
    return active_children, total_workers // active_children


def workspace_root() -> pathlib.Path:
    """Return the release workspace root anchored from this script's location."""
    return pathlib.Path(__file__).resolve().parents[2]


def source_repo(root: pathlib.Path, name: str) -> pathlib.Path:
    """Resolve one canonical source checkout without creating or switching any repository state."""
    third_party = root / "third-party" / name
    return third_party if third_party.is_dir() else root / name


def git_text(repo: pathlib.Path, *args: str) -> str:
    """Read one git value with a source-specific diagnostic if the release preflight cannot continue."""
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed in '{repo}': {detail}")
    return completed.stdout.strip()


def validate_release_sources(
    *,
    ros2cs_repo: pathlib.Path,
    r2fu_repo: pathlib.Path,
    release_tag: str,
) -> dict[str, str]:
    """Require clean canonical sources and one locally resolvable tag before creating disposable worktrees."""
    commits: dict[str, str] = {}
    for name, repo in (("ros2cs", ros2cs_repo), ("ros2-for-unity", r2fu_repo)):
        if not repo.is_dir():
            raise RuntimeError(f"Release source repository is missing: {repo}")
        status = git_text(repo, "status", "--porcelain")
        if status:
            raise RuntimeError(f"Release source '{name}' has uncommitted changes:\n{status}")
        commits[name] = git_text(repo, "rev-parse", "--verify", f"refs/tags/{release_tag}^{{commit}}")
    return commits


def default_run_root(root: pathlib.Path) -> pathlib.Path:
    """Create a compact, collision-resistant matrix root below the workspace build scratch directory."""
    run_id = dt.datetime.now().strftime("%y%m%d%H%M%S")
    return root / ".build" / "m" / run_id


def plan_distro_paths(run_root: pathlib.Path, ros_distro: str) -> DistroPaths:
    """Derive short, non-overlapping worktree and staging paths for one supported ROS distro."""
    if ros_distro not in REQUIRED_ROS_DISTROS:
        raise ValueError(f"Unsupported ROS distro for release matrix: {ros_distro}")

    distro_root = run_root / DISTRO_PATH_SEGMENTS[ros_distro]
    r2fu_worktree = distro_root / "u"
    return DistroPaths(
        ros_distro=ros_distro,
        r2fu_worktree=r2fu_worktree,
        ros2cs_worktree=distro_root / "c",
        validation_root=distro_root,
        asset_dir=r2fu_worktree / "install" / "asset" / "Ros2ForUnity",
    )


def assert_windows_path_budget(paths: DistroPaths) -> None:
    """Reject a matrix root whose known generated MSVC object path is too close to MAX_PATH."""
    generated_object = paths.validation_root / WINDOWS_GENERATED_OBJECT_PROBE_SUFFIX
    generated_length = len(os.fspath(generated_object))
    if generated_length > WINDOWS_GENERATED_OBJECT_PATH_BUDGET:
        raise RuntimeError(
            f"Matrix {paths.ros_distro} path budget exceeded: generated object path is "
            f"{generated_length} characters (limit {WINDOWS_GENERATED_OBJECT_PATH_BUDGET}) at "
            f"'{generated_object}'. Use a shorter --run-root below the workspace .build directory."
        )


def rebuild_command(
    *,
    workspace_root: pathlib.Path,
    paths: DistroPaths,
    release_tag: str,
    parallel_workers: int,
) -> list[str]:
    """Build one full release-ladder command without falling back to shared staging paths."""
    script = workspace_root / "Scripts" / "rebuild" / "rebuild_r2fu_windows_zip.py"
    output_dir = workspace_root / "artifacts" / "ros2-for-unity" / paths.ros_distro / "windows_x86_64"
    return [
        sys.executable,
        str(script),
        "--ros-distro",
        paths.ros_distro,
        "--clean",
        "--parallel-workers",
        str(parallel_workers),
        "--release-tag",
        release_tag,
        "--r2fu-root",
        str(paths.r2fu_worktree),
        "--ros2cs-root",
        str(paths.ros2cs_worktree),
        "--run-root",
        str(paths.validation_root),
        "--asset-dir",
        str(paths.asset_dir),
        "--output-dir",
        str(output_dir),
    ]


def worktree_commands(
    *,
    ros2cs_repo: pathlib.Path,
    r2fu_repo: pathlib.Path,
    paths: DistroPaths,
    release_tag: str,
) -> list[list[str]]:
    """Return the tagged worktree and junction commands required for one distro child."""
    return [
        [
            "git", "-C", str(ros2cs_repo), "worktree", "add", "--detach",
            str(paths.ros2cs_worktree), release_tag,
        ],
        [
            "git", "-C", str(r2fu_repo), "worktree", "add", "--detach",
            str(paths.r2fu_worktree), release_tag,
        ],
        [
            "cmd", "/d", "/c", "mklink", "/J",
            str(paths.r2fu_worktree / "src" / "ros2cs"), str(paths.ros2cs_worktree),
        ],
    ]


def prepare_child_worktree(
    *,
    ros2cs_repo: pathlib.Path,
    r2fu_repo: pathlib.Path,
    paths: DistroPaths,
    release_tag: str,
) -> None:
    """Create one distro's disposable source worktrees and its private ros2cs junction."""
    junction = paths.r2fu_worktree / "src" / "ros2cs"
    for path in (paths.ros2cs_worktree, paths.r2fu_worktree, junction):
        if path.exists() or path.is_symlink():
            raise RuntimeError(f"Refusing to reuse existing matrix worktree path: {path}")

    paths.ros2cs_worktree.parent.mkdir(parents=True, exist_ok=True)
    for command in worktree_commands(
        ros2cs_repo=ros2cs_repo,
        r2fu_repo=r2fu_repo,
        paths=paths,
        release_tag=release_tag,
    ):
        subprocess.run(command, check=True)


def cleanup_child_worktree(
    *,
    ros2cs_repo: pathlib.Path,
    r2fu_repo: pathlib.Path,
    paths: DistroPaths,
) -> None:
    """Remove a disposable child safely, unlinking its junction before either worktree is removed."""
    junction = paths.r2fu_worktree / "src" / "ros2cs"
    cleanup_commands = [
        ["cmd", "/d", "/c", "rmdir", str(junction)],
        ["git", "-C", str(r2fu_repo), "worktree", "remove", "--force", str(paths.r2fu_worktree)],
        ["git", "-C", str(ros2cs_repo), "worktree", "remove", "--force", str(paths.ros2cs_worktree)],
    ]
    for command in cleanup_commands:
        subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_children(
    *,
    workspace_root: pathlib.Path,
    children: list[tuple[DistroPaths, list[str]]],
    max_concurrency: int,
) -> list[ChildResult]:
    """Run independent full distro ladders concurrently while retaining a separate durable log for each."""
    if not 1 <= max_concurrency <= len(children):
        raise ValueError("max_concurrency must be between one and the number of matrix children.")

    def run_one(index: int, paths: DistroPaths, command: list[str]) -> tuple[int, ChildResult]:
        paths.validation_root.mkdir(parents=True, exist_ok=True)
        log_path = paths.validation_root / "matrix-child.log"
        print(f"Starting {paths.ros_distro}: {subprocess.list2cmdline(command)}", flush=True)
        with log_path.open("w", encoding="utf-8", newline="\n") as log:
            completed = subprocess.run(
                command,
                cwd=workspace_root,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        print(f"Finished {paths.ros_distro}: exit {completed.returncode}; log {log_path}", flush=True)
        return index, ChildResult(paths, tuple(command), log_path, completed.returncode)

    results: list[ChildResult | None] = [None] * len(children)
    with ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="r2fu-release") as executor:
        futures = [
            executor.submit(run_one, index, paths, command)
            for index, (paths, command) in enumerate(children)
        ]
        for future in as_completed(futures):
            index, result = future.result()
            results[index] = result

    return [result for result in results if result is not None]


def validate_release_artifacts(*, workspace_root: pathlib.Path, release_tag: str) -> list[object]:
    """Reuse the publication gate to validate all three local ZIPs before any later upload step."""
    release_scripts = pathlib.Path(__file__).resolve().parents[1] / "release"
    if str(release_scripts) not in sys.path:
        sys.path.insert(0, str(release_scripts))
    import publish_r2fu_windows_release as publisher

    artifact_root = workspace_root / "artifacts" / "ros2-for-unity"
    return publisher.validate_release_artifacts(artifact_root, release_tag)


def execute_matrix(
    *,
    workspace_root: pathlib.Path,
    ros2cs_repo: pathlib.Path,
    r2fu_repo: pathlib.Path,
    run_root: pathlib.Path,
    release_tag: str,
    parallel_workers: int,
    max_concurrency: int,
    keep_worktrees: bool,
) -> list[object]:
    """Run the fixed matrix, validating artifacts only after every isolated child reports success."""
    if run_root.exists():
        raise RuntimeError(f"Refusing to reuse existing matrix run root: {run_root}")
    active_children, child_parallel_workers = worker_plan(
        total_workers=parallel_workers,
        requested_concurrency=max_concurrency,
    )
    paths = [plan_distro_paths(run_root, ros_distro) for ros_distro in REQUIRED_ROS_DISTROS]
    for child_paths in paths:
        assert_windows_path_budget(child_paths)
    run_root.mkdir(parents=True, exist_ok=False)
    try:
        for child_paths in paths:
            prepare_child_worktree(
                ros2cs_repo=ros2cs_repo,
                r2fu_repo=r2fu_repo,
                paths=child_paths,
                release_tag=release_tag,
            )

        children = [
            (
                child_paths,
                rebuild_command(
                    workspace_root=workspace_root,
                    paths=child_paths,
                    release_tag=release_tag,
                    parallel_workers=child_parallel_workers,
                ),
            )
            for child_paths in paths
        ]
        results = run_children(
            workspace_root=workspace_root,
            children=children,
            max_concurrency=active_children,
        )
        failures = [result for result in results if result.returncode != 0]
        if failures:
            details = "\n".join(
                f"  - {result.paths.ros_distro}: exit {result.returncode}; log {result.log_path}"
                for result in failures
            )
            raise RuntimeError(f"One or more R2FU release matrix children failed:\n{details}")

        return validate_release_artifacts(workspace_root=workspace_root, release_tag=release_tag)
    finally:
        if not keep_worktrees:
            for child_paths in paths:
                cleanup_child_worktree(
                    ros2cs_repo=ros2cs_repo,
                    r2fu_repo=r2fu_repo,
                    paths=child_paths,
                )


def main(argv: list[str] | None = None) -> int:
    """Print or execute the fixed Humble/Jazzy/Lyrical release matrix."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.parallel_workers < 1:
        raise RuntimeError("--parallel-workers must be positive.")
    if not 1 <= args.max_concurrency <= len(REQUIRED_ROS_DISTROS):
        raise RuntimeError(f"--max-concurrency must be between 1 and {len(REQUIRED_ROS_DISTROS)}.")

    active_children, child_parallel_workers = worker_plan(
        total_workers=args.parallel_workers,
        requested_concurrency=args.max_concurrency,
    )

    root = workspace_root().resolve()
    run_root = resolve_run_root(root, args.run_root or default_run_root(root))
    paths = [plan_distro_paths(run_root, ros_distro) for ros_distro in REQUIRED_ROS_DISTROS]
    for child_paths in paths:
        assert_windows_path_budget(child_paths)
    commands = [
        rebuild_command(
            workspace_root=root,
            paths=child_paths,
            release_tag=args.release_tag,
            parallel_workers=child_parallel_workers,
        )
        for child_paths in paths
    ]

    print(f"R2FU Windows release matrix: {args.release_tag}")
    print(f"Matrix run root: {run_root}")
    print(
        f"Native worker budget: {args.parallel_workers} total; "
        f"{active_children} active children x {child_parallel_workers} workers."
    )
    for child_paths, command in zip(paths, commands, strict=True):
        print(f"[{child_paths.ros_distro}] {subprocess.list2cmdline(command)}")

    if args.dry_run:
        print("Dry run; no worktrees, builds, artifacts, or cleanup actions were started.")
        return 0

    ros2cs_repo = source_repo(root, "ros2cs")
    r2fu_repo = source_repo(root, "ros2-for-unity")
    validate_release_sources(
        ros2cs_repo=ros2cs_repo,
        r2fu_repo=r2fu_repo,
        release_tag=args.release_tag,
    )
    artifacts = execute_matrix(
        workspace_root=root,
        ros2cs_repo=ros2cs_repo,
        r2fu_repo=r2fu_repo,
        run_root=run_root,
        release_tag=args.release_tag,
        parallel_workers=args.parallel_workers,
        max_concurrency=args.max_concurrency,
        keep_worktrees=args.keep_worktrees,
    )
    print(f"Release matrix validation passed for {args.release_tag}:")
    for artifact in artifacts:
        print(f"  - {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
