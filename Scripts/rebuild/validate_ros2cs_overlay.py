#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Purpose: Verify that a built ros2cs install tree is a complete, first-priority ROS 2 overlay.
#
# Modifications by Jianbin Liu:
# - Added release-gate validation for ros2cs generator metadata, managed assemblies, ROS C++ headers, and setup ordering.

"""Fail closed when a ros2cs build/install tree cannot act as a ROS 2 custom-interface overlay."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


OVERLAY_REQUIRED_PATHS = (
    "share/ament_index/resource_index/packages/ros2cs_common",
    "share/ament_index/resource_index/packages/rosidl_generator_cs",
    "share/ros2cs_common/cmake/ros2cs_commonConfig.cmake",
    "share/ros2cs_core/cmake/ros2cs_coreConfig.cmake",
    "share/rosidl_generator_cs/cmake/rosidl_generator_csConfig.cmake",
    "lib/dotnet/ros2cs_common.dll",
    "lib/dotnet/ros2cs_core.dll",
)

ROS2_HEADER_REQUIRED_PATHS = (
    "include/rcl/rcl/init.h",
    "include/rosidl_runtime_c/rosidl_runtime_c/message_type_support_struct.h",
    "include/rosidl_typesupport_c/rosidl_typesupport_c/message_type_support_dispatch.h",
)

PREFIX_VARIABLES = (
    "AMENT_PREFIX_PATH",
    "CMAKE_PREFIX_PATH",
)


def normalized_path(path: str | Path) -> str:
    """Normalize one Windows path for case-insensitive prefix-order comparison."""
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


def split_prefixes(value: str) -> list[str]:
    """Split one Windows semicolon-delimited prefix variable, dropping empty entries."""
    return [entry.strip() for entry in value.split(os.pathsep) if entry.strip()]


def parse_cmd_environment(output: str) -> dict[str, str]:
    """Parse `set` output from a cmd.exe child process without trusting unrelated stdout."""
    environment: dict[str, str] = {}
    for line in output.splitlines():
        name, separator, value = line.partition("=")
        if separator and name:
            environment[name.upper()] = value
    return environment


def capture_overlay_environment(install_base: Path, environment: dict[str, str]) -> dict[str, str]:
    """Call the installed setup.bat in a child cmd.exe and return its resulting environment."""
    setup_bat = install_base / "setup.bat"
    if not setup_bat.is_file():
        raise RuntimeError(f"ros2cs overlay is missing setup.bat: {setup_bat}")

    # Run a short batch file instead of embedding CALL in `cmd /c`: Python's Windows
    # argument quoting otherwise turns the setup path into a literal quoted filename.
    temp_root_value = environment.get("TEMP") or environment.get("TMP")
    if not temp_root_value:
        raise RuntimeError("ros2cs overlay validation requires a wrapper-provided TEMP or TMP directory.")
    temp_root = Path(temp_root_value)
    if not temp_root.is_dir():
        raise RuntimeError(f"ros2cs overlay validation temp directory does not exist: {temp_root}")

    script_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="ascii",
            newline="\r\n",
            suffix=".cmd",
            prefix="ros2cs_overlay_",
            dir=temp_root,
            delete=False,
        ) as script:
            script_path = Path(script.name)
            script.write("@echo off\n")
            script.write(f'call "{setup_bat}" >nul\n')
            script.write("if errorlevel 1 exit /b %errorlevel%\n")
            script.write("set\n")

        result = subprocess.run(
            ["cmd.exe", "/d", "/q", "/c", str(script_path)],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        if script_path is not None:
            try:
                script_path.unlink()
            except FileNotFoundError:
                pass
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"ros2cs overlay setup failed with exit code {result.returncode}: {details}")

    captured = parse_cmd_environment(result.stdout)
    if not captured:
        raise RuntimeError("ros2cs overlay setup did not return an environment snapshot.")
    return captured


def validate_required_paths(root: Path, relative_paths: tuple[str, ...], label: str) -> None:
    """Require every expected file/resource path below one closed build dependency root."""
    missing = [relative_path for relative_path in relative_paths if not (root / relative_path).is_file()]
    if missing:
        details = "\n".join(f"  - {root / relative_path}" for relative_path in missing)
        raise RuntimeError(f"{label} is missing required files:\n{details}")


def validate_overlay_prefix_order(environment: dict[str, str], install_base: Path) -> None:
    """Require the built overlay to precede the base ROS 2 installation for CMake and ament resolution."""
    expected = normalized_path(install_base)
    errors = []
    for variable in PREFIX_VARIABLES:
        entries = split_prefixes(environment.get(variable, ""))
        if not entries:
            errors.append(f"{variable} is empty after sourcing the ros2cs overlay.")
            continue
        actual = normalized_path(entries[0])
        if actual != expected:
            errors.append(f"{variable} starts with '{entries[0]}', expected '{install_base}'.")
    if errors:
        raise RuntimeError("ros2cs overlay prefix order is invalid:\n" + "\n".join(f"  - {error}" for error in errors))


def validate_overlay(install_base: Path, ros2_root: Path, environment: dict[str, str]) -> None:
    """Validate files and effective setup ordering required by custom ros2cs interface builds."""
    install_base = install_base.resolve()
    ros2_root = ros2_root.resolve()
    if not install_base.is_dir():
        raise RuntimeError(f"ros2cs overlay directory does not exist: {install_base}")
    if not ros2_root.is_dir():
        raise RuntimeError(f"base ROS 2 directory does not exist: {ros2_root}")

    validate_required_paths(install_base, OVERLAY_REQUIRED_PATHS, "ros2cs overlay")
    validate_required_paths(ros2_root, ROS2_HEADER_REQUIRED_PATHS, "base ROS 2 headers")
    validate_overlay_prefix_order(capture_overlay_environment(install_base, environment), install_base)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse explicit paths so release validation never infers an overlay from the ambient shell."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-base", type=Path, required=True)
    parser.add_argument("--ros2-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the overlay closure gate and print one stable success marker."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    validate_overlay(args.install_base, args.ros2_root, dict(os.environ))
    print(f"ROS2CS_OVERLAY_CLOSURE_PASS install={args.install_base.resolve()} ros2={args.ros2_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
