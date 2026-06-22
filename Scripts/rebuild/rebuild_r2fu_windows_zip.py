#!/usr/bin/env python3
"""Run the full Windows R2FU validation ladder, then package the zip."""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "package"))
import package_r2fu_windows_zip as packager


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
) -> list[str]:
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
    return command


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ros-distro", choices=("humble", "jazzy", "lyrical"), default=packager.DEFAULT_ROS_DISTRO)
    parser.add_argument("--clean", action="store_true", help="Clean the short build/log/temp roots before rebuilding.")
    parser.add_argument("--dry-run", action="store_true", help="Run validation in dry-run mode and skip packaging.")
    parser.add_argument("--console-direct", action="store_true", help="Use console_direct+ output during the full build.")
    parser.add_argument("--parallel-workers", type=int, default=None, help="Parallel workers for the validation build.")
    parser.add_argument("--asset-dir", type=pathlib.Path, default=packager.default_asset_dir())
    parser.add_argument("--output-dir", type=pathlib.Path, default=None)
    parser.add_argument("--artifact-basename", default=None)
    parser.add_argument("--no-backup", action="store_true", help="Overwrite current zip/sha/manifest instead of backing them up.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = workspace_root()
    workers = args.parallel_workers or max(1, (os.cpu_count() or 1))
    command = validation_command(
        workspace_root=root,
        ros_distro=args.ros_distro,
        clean=args.clean,
        dry_run=args.dry_run,
        console_direct=args.console_direct,
        parallel_workers=workers,
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
    for report_path in sorted((root / ".build" / "reports").glob(f"r2fu-{args.ros_distro}-windows-full-validation-*.json")):
        summary_path = report_path

    result = packager.package_asset(
        asset_dir=args.asset_dir,
        output_dir=output_dir,
        artifact_basename=artifact_basename,
        ros_distro=args.ros_distro,
        backup_existing=not args.no_backup,
        check_required=True,
        validation_summary_path=summary_path,
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
