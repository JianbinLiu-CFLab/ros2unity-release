#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Modifications by Jianbin Liu:
# - Added a compile-surface gate for the R2FU optional custom-typesupport bootstrap facade.

"""Compile the R2FU native-plugin bootstrap against an optional add-on catalog probe."""

from __future__ import annotations

import argparse
import html
from pathlib import Path
import subprocess
import sys
import tempfile


BOOTSTRAP_RELATIVE_PATH = Path("src/Ros2ForUnity/Scripts/Ros2ForUnityNativePluginBootstrap.cs")
INITIALIZER_RELATIVE_PATH = Path("src/Ros2ForUnity/Scripts/ROS2ForUnity.cs")


UNITY_STUBS = """namespace UnityEngine
{
    public enum RuntimePlatform
    {
        WindowsEditor,
        LinuxEditor,
    }

    public static class Application
    {
        public static RuntimePlatform platform { get { return RuntimePlatform.WindowsEditor; } }
    }
}

namespace ROS2
{
    public static class GlobalVariables
    {
        public static void RegisterNativeLibraryDirectory(string directory) { }
    }
}
"""


OPTIONAL_CATALOG_PROBE = """using System;

namespace OptionalCustomTypesupportCatalog
{
    internal static class GeneratedCatalogCompileProbe
    {
        internal static Func<string, bool> RegisterFacade()
        {
            return ROS2.Ros2ForUnityNativePluginBootstrap.RegisterEditorPackagePluginDirectory;
        }
    }
}
"""


def require_source_paths(r2fu_root: Path) -> tuple[Path, Path]:
    """Return the two R2FU source files that define and invoke the bootstrap contract."""
    bootstrap_source = r2fu_root / BOOTSTRAP_RELATIVE_PATH
    initializer_source = r2fu_root / INITIALIZER_RELATIVE_PATH
    missing = [path for path in (bootstrap_source, initializer_source) if not path.is_file()]
    if missing:
        rendered = "\n".join(f"  - {path}" for path in missing)
        raise RuntimeError(f"R2FU native plugin compile surface is missing source files:\n{rendered}")
    return bootstrap_source, initializer_source


def validate_initializer_order(initializer_source: Path) -> None:
    """Require the registration seal to occur before the first Ros2cs.Init call."""
    source = initializer_source.read_text(encoding="utf-8")
    seal = "Ros2ForUnityNativePluginBootstrap.SealNativeLibraryRegistration();"
    init = "Ros2cs.Init();"
    seal_index = source.find(seal)
    init_index = source.find(init)
    if seal_index < 0:
        raise RuntimeError(f"R2FU initializer does not seal native plugin registration: {initializer_source}")
    if init_index < 0:
        raise RuntimeError(f"R2FU initializer does not call Ros2cs.Init: {initializer_source}")
    if seal_index > init_index:
        raise RuntimeError("R2FU seals native plugin registration after Ros2cs.Init.")


def write_compile_surface_project(project_root: Path, bootstrap_source: Path) -> Path:
    """Write a minimal Unity stub project that compiles the public optional add-on facade."""
    source_path = html.escape(bootstrap_source.resolve().as_posix(), quote=True)
    project_path = project_root / "R2fuNativePluginCompileSurface.csproj"
    project_path.write_text(
        f"""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <OutputType>Library</OutputType>
    <DefineConstants>$(DefineConstants);UNITY_EDITOR</DefineConstants>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <ImplicitUsings>disable</ImplicitUsings>
    <Nullable>disable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="{source_path}" Link="Ros2ForUnityNativePluginBootstrap.cs" />
    <Compile Include="UnityStubs.cs" />
    <Compile Include="OptionalCatalogCompileProbe.cs" />
  </ItemGroup>
</Project>
""",
        encoding="utf-8",
    )
    (project_root / "UnityStubs.cs").write_text(UNITY_STUBS, encoding="utf-8")
    (project_root / "OptionalCatalogCompileProbe.cs").write_text(OPTIONAL_CATALOG_PROBE, encoding="utf-8")
    return project_path


def run_compile_surface(project_path: Path) -> None:
    """Build the probe and surface a concise compiler error when the public contract changes."""
    result = subprocess.run(
        ["dotnet", "build", str(project_path), "--nologo", "--verbosity", "minimal"],
        cwd=project_path.parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "R2FU native plugin compile-surface build failed:\n" + result.stdout.strip()
        )


def validate_compile_surface(r2fu_root: Path, scratch_root: Path) -> None:
    """Compile the real bootstrap source in a temporary workspace-owned project."""
    bootstrap_source, initializer_source = require_source_paths(r2fu_root)
    validate_initializer_order(initializer_source)
    if not scratch_root.is_dir():
        raise RuntimeError(f"R2FU native plugin compile-surface scratch directory does not exist: {scratch_root}")

    with tempfile.TemporaryDirectory(prefix="r2fu_native_plugin_", dir=scratch_root) as temporary_directory:
        project_path = write_compile_surface_project(Path(temporary_directory), bootstrap_source)
        run_compile_surface(project_path)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse explicit source and scratch roots so the validation never infers a workspace."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r2fu-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the compile-surface gate and print one stable success marker."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    validate_compile_surface(args.r2fu_root.resolve(), args.scratch_root.resolve())
    print("R2FU_NATIVE_PLUGIN_BOOTSTRAP_COMPILE_SURFACE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
