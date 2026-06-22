#!/usr/bin/env python3
"""
Prepare a clean ROS 2 Lyrical / R2FU build environment on Windows.

Python cannot mutate the already-running parent terminal environment. This
script instead builds a clean environment and uses it to run a command, run a
check, dump JSON, or open an interactive cmd.exe session. The environment pins
CMake's default generator to Ninja so colcon does not try to infer an unsupported
Visual Studio generator from newer VS toolchains.
"""

from __future__ import annotations

import argparse
import json
import locale
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


DEFAULT_ROS2_ROOT = workspace_root() / "ros2-windows" / "ros2_lyrical"
DEFAULT_TEMP_ROOT = workspace_root() / ".tmp"
DEFAULT_CMAKE_GENERATOR = "Ninja"

ROS_DISCOVERY_PASSTHROUGH = (
    "ROS_DOMAIN_ID",
    "ROS_DISCOVERY_SERVER",
    # Preserve an explicit caller RMW choice (e.g. rmw_zenoh_cpp) instead of forcing the
    # rmw_fastrtps_cpp default, so callers can validate alternate RMW implementations.
    # Callers that do not set RMW_IMPLEMENTATION still get the deterministic fastrtps default.
    "RMW_IMPLEMENTATION",
)

BLOCKED_PATH_TOKENS = (
    "anaconda",
    "miniconda",
    "conda",
    "mambaforge",
    "miniforge",
    "python27",
    "python36",
    "python37",
    "python38",
    "python39",
    "python310",
    "python311",
    "python312",
    "python313",
)


def require_existing_path(value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.exists():
        raise RuntimeError(f"{label} not found: {path}")
    return path.resolve()


def get_env(env: dict[str, str], name: str, default: str = "") -> str:
    wanted = name.upper()
    for key, value in env.items():
        if key.upper() == wanted:
            return value
    return default


def set_env(env: dict[str, str], name: str, value: str) -> None:
    drop_env(env, name)
    env[name] = value


def drop_env(env: dict[str, str], name: str) -> None:
    wanted = name.upper()
    for key in list(env):
        if key.upper() == wanted:
            del env[key]


def collect_ros_discovery_overrides(source_env: dict[str, str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for name in ROS_DISCOVERY_PASSTHROUGH:
        value = get_env(source_env, name)
        if value:
            overrides[name] = value
    return overrides


def is_contaminating_path_entry(entry: str) -> bool:
    lowered = entry.lower()
    return any(token in lowered for token in BLOCKED_PATH_TOKENS)


def merge_clean_path(pinned_entries: list[Path | str], existing_path: str) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    candidates = [str(entry) for entry in pinned_entries]
    candidates.extend(
        entry
        for entry in existing_path.split(os.pathsep)
        if entry and not is_contaminating_path_entry(entry)
    )

    for entry in candidates:
        normalized = entry.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return os.pathsep.join(merged)


def parse_cmd_set_output(output: str) -> dict[str, str]:
    captured: dict[str, str] = {}
    for line in output.splitlines():
        index = line.find("=")
        if index <= 0:
            continue
        captured[line[:index]] = line[index + 1 :]
    return captured


def call_batch_and_capture_env(
    batch_path: Path,
    args: list[str],
    env: dict[str, str],
    label: str,
) -> dict[str, str]:
    fd, script_name = tempfile.mkstemp(prefix="ros2env_", suffix=".cmd", text=True)
    script_path = Path(script_name)
    try:
        with os.fdopen(fd, "w", newline="\r\n") as script:
            script.write("@echo off\n")
            joined_args = " ".join(args)
            script.write(f'call "{batch_path}" {joined_args} >nul\n')
            script.write("if errorlevel 1 exit /b %errorlevel%\n")
            script.write("set\n")

        encoding = locale.getpreferredencoding(False)
        result = subprocess.run(
            ["cmd.exe", "/d", "/q", "/c", str(script_path)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=encoding,
            errors="replace",
        )
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"{label} failed with exit code {result.returncode}\n{details}")

        captured = dict(env)
        captured.update(parse_cmd_set_output(result.stdout))
        return captured
    finally:
        try:
            script_path.unlink()
        except FileNotFoundError:
            pass


def resolve_vs_dev_cmd(explicit_path: str) -> Path:
    if explicit_path:
        return require_existing_path(explicit_path, "VsDevCmd.bat")

    candidates: list[Path] = []
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if vswhere.exists():
        result = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=locale.getpreferredencoding(False),
            errors="replace",
        )
        install_path = result.stdout.strip()
        if install_path:
            candidates.append(Path(install_path) / "Common7" / "Tools" / "VsDevCmd.bat")

    candidates.extend(
        [
            Path(r"C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat"),
            Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat"),
            Path(r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"),
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    raise RuntimeError("VsDevCmd.bat not found. Install Visual Studio C++ tools or pass --vs-dev-cmd.")


def pin_ros_environment(
    env: dict[str, str],
    ros2_root: Path,
    pixi_root: Path,
    python: Path,
    temp_root: Path,
    ros_discovery_overrides: dict[str, str],
) -> None:
    pinned_entries = [
        ros2_root / "bin",
        ros2_root / "Scripts",
        pixi_root,
        pixi_root / "Library" / "bin",
        pixi_root / "Scripts",
    ]
    set_env(env, "PATH", merge_clean_path(pinned_entries, get_env(env, "PATH")))

    drop_env(env, "PYTHONHOME")
    set_env(env, "PYTHONPATH", str(ros2_root / "Lib" / "site-packages"))
    set_env(env, "COLCON_PYTHON_EXECUTABLE", str(python))
    set_env(env, "PYTHONUTF8", "1")
    set_env(env, "TEMP", str(temp_root))
    set_env(env, "TMP", str(temp_root))
    set_env(env, "ROS_VERSION", "2")
    set_env(env, "ROS_PYTHON_VERSION", "3")
    set_env(env, "ROS_DISTRO", "lyrical")
    set_env(env, "AMENT_PREFIX_PATH", str(ros2_root))
    set_env(env, "CMAKE_PREFIX_PATH", str(ros2_root))
    set_env(env, "COLCON_PREFIX_PATH", str(ros2_root))
    set_env(env, "ROS_DOMAIN_ID", "0")
    set_env(env, "RMW_IMPLEMENTATION", "rmw_fastrtps_cpp")
    set_env(env, "ROS_AUTOMATIC_DISCOVERY_RANGE", "SUBNET")
    set_env(env, "CMAKE_GENERATOR", DEFAULT_CMAKE_GENERATOR)
    drop_env(env, "ROS_LOCALHOST_ONLY")
    drop_env(env, "ROS_DISCOVERY_SERVER")

    # Preserve explicit discovery settings from the caller. The default remains
    # deterministic for builds, while WSL2 NAT validation can opt into unicast
    # discovery without the wrapper silently clearing those variables.
    for name, value in ros_discovery_overrides.items():
        set_env(env, name, value)


def validate_python(python: Path, env: dict[str, str]) -> None:
    result = subprocess.run(
        [str(python), "-c", "import sys, catkin_pkg, ament_package, em"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Pinned ROS 2 Python failed import checks.\n{details}")


def build_environment(args: argparse.Namespace) -> tuple[dict[str, str], dict[str, str]]:
    ros2_root = require_existing_path(args.ros2_root, "ROS 2 root")
    pixi_root = require_existing_path(ros2_root / ".pixi" / "envs" / "default", "ROS 2 pixi environment")
    python = require_existing_path(pixi_root / "python.exe", "ROS 2 pixi Python")
    colcon = require_existing_path(pixi_root / "Scripts" / "colcon.exe", "colcon")
    vcs = require_existing_path(pixi_root / "Scripts" / "vcs.exe", "vcs")
    local_setup = require_existing_path(ros2_root / "local_setup.bat", "ROS 2 local_setup.bat")

    temp_root = Path(args.temp_root).resolve()
    temp_root.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    ros_discovery_overrides = collect_ros_discovery_overrides(env)
    if args.no_vs_dev:
        vs_dev_cmd = None
    else:
        vs_dev_cmd = resolve_vs_dev_cmd(args.vs_dev_cmd)
        env = call_batch_and_capture_env(
            vs_dev_cmd,
            ["-arch=x64", "-host_arch=x64"],
            env,
            "VsDevCmd.bat",
        )

    pin_ros_environment(env, ros2_root, pixi_root, python, temp_root, ros_discovery_overrides)
    env = call_batch_and_capture_env(local_setup, [], env, "ROS 2 local_setup.bat")
    pin_ros_environment(env, ros2_root, pixi_root, python, temp_root, ros_discovery_overrides)
    validate_python(python, env)

    ninja = (
        shutil.which("ninja.exe", path=get_env(env, "PATH"))
        or shutil.which("ninja", path=get_env(env, "PATH"))
        or ""
    )
    if get_env(env, "CMAKE_GENERATOR") == DEFAULT_CMAKE_GENERATOR and not ninja:
        raise RuntimeError("CMAKE_GENERATOR=Ninja is selected, but ninja.exe was not found on PATH.")

    info = {
        "ROS_DISTRO": get_env(env, "ROS_DISTRO"),
        "ROS2_ROOT": str(ros2_root),
        "PYTHON": str(python),
        "COLCON": str(colcon),
        "VCS": str(vcs),
        "TEMP": get_env(env, "TEMP"),
        "VSDEVCMD": str(vs_dev_cmd) if vs_dev_cmd else "",
        "CL": shutil.which("cl.exe", path=get_env(env, "PATH")) or "",
        "NINJA": ninja,
        "CMAKE_GENERATOR": get_env(env, "CMAKE_GENERATOR"),
    }
    return env, info


def print_summary(info: dict[str, str]) -> None:
    print("ROS2_LYRICAL_ENV_READY")
    for key in (
        "ROS_DISTRO",
        "ROS2_ROOT",
        "PYTHON",
        "COLCON",
        "VCS",
        "TEMP",
        "VSDEVCMD",
        "CL",
        "NINJA",
        "CMAKE_GENERATOR",
    ):
        value = info.get(key, "")
        if value:
            print(f"{key}={value}")
        elif key in ("CL", "NINJA"):
            print(f"{key}=missing")


def resolve_command(command: list[str], env: dict[str, str]) -> list[str]:
    if not command:
        return command

    executable = command[0]
    if os.path.dirname(executable):
        return command

    resolved = shutil.which(executable, path=get_env(env, "PATH"))
    if not resolved:
        return command

    return [resolved] + command[1:]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run commands inside a clean ROS 2 Lyrical / R2FU Windows environment."
    )
    parser.add_argument("--ros2-root", default=DEFAULT_ROS2_ROOT)
    parser.add_argument("--temp-root", default=DEFAULT_TEMP_ROOT)
    parser.add_argument("--vs-dev-cmd", default="")
    parser.add_argument("--no-vs-dev", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--check", action="store_true", help="Build and validate the environment, then exit.")
    parser.add_argument("--dump-env-json", action="store_true", help="Print the prepared environment as JSON.")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --.")
    parsed = parser.parse_args(argv)
    if parsed.command and parsed.command[0] == "--":
        parsed.command = parsed.command[1:]
    return parsed


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        env, info = build_environment(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dump_env_json:
        print(json.dumps(dict(sorted(env.items())), indent=2))
        return 0

    if not args.quiet:
        print_summary(info)

    if args.check:
        return 0

    if args.command:
        return subprocess.call(resolve_command(args.command, env), env=env)

    if not args.quiet:
        print("Starting cmd.exe with ROS 2 Lyrical environment. Type exit to return.")
    return subprocess.call(["cmd.exe", "/k"], env=env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
