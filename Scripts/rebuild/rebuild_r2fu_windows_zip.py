#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Modifications by Jianbin Liu:
# - Added opt-in release tag, source pin, and metadata provenance gates.
# - Added isolated source and run-root forwarding for parallel release matrices.
# - Forwarded bounded per-child native worker limits to the validation ladder.

"""Run the full Windows R2FU validation ladder, then package the zip."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "package"))
import package_r2fu_windows_zip as packager


ROS2CS_PIN_RE = re.compile(r"(?m)^\s*version:\s*([0-9a-f]{40})\s*$")


def workspace_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def validation_command(
    *,
    workspace_root: pathlib.Path,
    ros_distro: str,
    clean: bool,
    dry_run: bool,
    console_direct: bool,
    parallel_workers: int,
    r2fu_root: pathlib.Path | None = None,
    ros2cs_root: pathlib.Path | None = None,
    run_root: pathlib.Path | None = None,
) -> list[str]:
    """Build the validation command, optionally targeting one isolated release workspace."""
    script = workspace_root / "Scripts" / "rebuild" / "run_r2fu_windows_validation.ps1"
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-ParallelWorkers",
        str(parallel_workers),
    ]
    if ros_distro != "jazzy":
        command.extend(["-RosDistro", ros_distro])
    if clean:
        command.append("-Clean")
    if dry_run:
        command.append("-DryRun")
    if console_direct:
        command.append("-ConsoleDirect")
    if r2fu_root is not None:
        command.extend(["-R2fuRoot", str(r2fu_root)])
    if ros2cs_root is not None:
        command.extend(["-Ros2csRoot", str(ros2cs_root)])
    if run_root is not None:
        command.extend(["-RunRoot", str(run_root)])
    return command


def required_git_value(repo: pathlib.Path, *args: str) -> str:
    """Run one git query or stop before a release build can proceed."""
    value = packager.run_git(repo, *args)
    if not value:
        rendered = " ".join(args)
        raise RuntimeError(f"Cannot read git value from '{repo}': git {rendered}")
    return value


def read_ros2cs_pin(ros2_for_unity_root: pathlib.Path) -> str:
    """Read the only reproducible ros2cs commit pin from R2FU's repos file."""
    path = ros2_for_unity_root / "ros2cs.repos"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"Cannot read R2FU ros2cs pin '{path}': {error}") from error

    pins = ROS2CS_PIN_RE.findall(text)
    if len(pins) != 1:
        raise RuntimeError(f"Expected exactly one 40-character ros2cs pin in '{path}', found {len(pins)}.")
    return pins[0]


def validate_release_source_identity(
    *,
    ros2cs_root: pathlib.Path,
    ros2_for_unity_root: pathlib.Path,
    release_tag: str,
) -> dict[str, str]:
    """Require tagged, clean source trees with the R2FU pin aligned to ros2cs."""
    source_repos = [
        ("ros2cs", ros2cs_root),
        ("ros2-for-unity", ros2_for_unity_root),
    ]
    commits = {}
    for name, repo in source_repos:
        actual_tag = packager.run_git(repo, "describe", "--exact-match", "--tags", "HEAD")
        if actual_tag != release_tag:
            found_tag = actual_tag or "no exact tag"
            raise RuntimeError(
                f"Release source '{name}' must be tagged '{release_tag}' at HEAD, found {found_tag}."
            )
        status = packager.run_git(repo, "status", "--short")
        if status:
            raise RuntimeError(f"Release source '{name}' has uncommitted changes:\n{status}")
        commits[name] = required_git_value(repo, "rev-parse", "HEAD")

    ros2cs_pin = read_ros2cs_pin(ros2_for_unity_root)
    if ros2cs_pin != commits["ros2cs"]:
        raise RuntimeError(
            "R2FU ros2cs.repos pin does not match ros2cs HEAD: "
            f"pin '{ros2cs_pin}', HEAD '{commits['ros2cs']}'."
        )

    return {
        "releaseTag": release_tag,
        "ros2csSha": commits["ros2cs"],
        "ros2ForUnitySha": commits["ros2-for-unity"],
        "ros2csReposPin": ros2cs_pin,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ros-distro", choices=("humble", "jazzy", "lyrical"), default=packager.DEFAULT_ROS_DISTRO)
    parser.add_argument("--clean", action="store_true", help="Clean the short build/log/temp roots before rebuilding.")
    parser.add_argument("--dry-run", action="store_true", help="Run validation in dry-run mode and skip packaging.")
    parser.add_argument("--console-direct", action="store_true", help="Use console_direct+ output during the full build.")
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=None,
        help="Maximum native build workers for one isolated validation child.",
    )
    parser.add_argument("--r2fu-root", type=pathlib.Path, help="Optional isolated ros2-for-unity source root.")
    parser.add_argument("--ros2cs-root", type=pathlib.Path, help="Optional isolated ros2cs source root.")
    parser.add_argument("--run-root", type=pathlib.Path, help="Optional isolated .build run root.")
    parser.add_argument("--asset-dir", type=pathlib.Path, default=None)
    parser.add_argument("--output-dir", type=pathlib.Path, default=None)
    parser.add_argument("--artifact-basename", default=None)
    parser.add_argument("--no-backup", action="store_true", help="Overwrite current zip/sha/manifest instead of backing them up.")
    parser.add_argument(
        "--release-tag",
        help="Require tagged, pinned sources and matching package metadata before publishing a release artifact.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = workspace_root()
    workers = args.parallel_workers or max(1, (os.cpu_count() or 1))
    # Isolated worktrees keep per-distro metadata generation and Unity asset staging disjoint.
    r2fu_root = (args.r2fu_root or packager.source_repo("ros2-for-unity")).resolve()
    ros2cs_root = (args.ros2cs_root or packager.source_repo("ros2cs")).resolve()
    run_root = args.run_root.resolve() if args.run_root is not None else None
    asset_dir = (args.asset_dir or (r2fu_root / "install" / "asset" / "Ros2ForUnity")).resolve()
    release_provenance = None
    if args.release_tag:
        release_provenance = validate_release_source_identity(
            ros2cs_root=ros2cs_root,
            ros2_for_unity_root=r2fu_root,
            release_tag=args.release_tag,
        )
        print(
            "Release source gate passed: "
            f"{args.release_tag} / ros2cs {release_provenance['ros2csSha']} / "
            f"R2FU {release_provenance['ros2ForUnitySha']}"
        )
    command = validation_command(
        workspace_root=root,
        ros_distro=args.ros_distro,
        clean=args.clean,
        dry_run=args.dry_run,
        console_direct=args.console_direct,
        parallel_workers=workers,
        r2fu_root=r2fu_root,
        ros2cs_root=ros2cs_root,
        run_root=run_root,
    )

    print("Running full validation before packaging:")
    print(" ".join(command))
    subprocess.run(command, cwd=root, check=True)

    if args.dry_run:
        print("Dry run complete; packaging skipped.")
        return 0

    artifact_basename = args.artifact_basename or f"Ros2ForUnity_{args.ros_distro}_standalone_windows_x86_64"
    output_dir = args.output_dir or (
        root / "artifacts" / "ros2-for-unity" / args.ros_distro / packager.DEFAULT_PLATFORM
    )

    summary_path = None
    report_root = (run_root / "reports") if run_root is not None else (root / ".build" / "reports")
    for report_path in sorted(report_root.glob(f"r2fu-{args.ros_distro}-windows-full-validation-*.json")):
        summary_path = report_path

    if release_provenance is not None:
        release_provenance["metadata"] = packager.validate_release_metadata(
            asset_dir,
            ros_distro=args.ros_distro,
            release_tag=args.release_tag,
            ros2cs_sha=release_provenance["ros2csSha"],
            ros2_for_unity_sha=release_provenance["ros2ForUnitySha"],
        )
        print("Release metadata gate passed.")

    result = packager.package_asset(
        asset_dir=asset_dir,
        output_dir=output_dir,
        artifact_basename=artifact_basename,
        ros_distro=args.ros_distro,
        backup_existing=not args.no_backup,
        check_required=True,
        validation_summary_path=summary_path,
        release_provenance=release_provenance,
        ros2_for_unity_root=r2fu_root,
        ros2cs_root=ros2cs_root,
    )
    print("")
    print("Rebuilt and packaged artifact:")
    print(f"ZIP:      {result.zip_path}")
    print(f"SHA256:   {result.sha256}")
    print(f"SHA FILE: {result.sha256_path}")
    print(f"MANIFEST: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
