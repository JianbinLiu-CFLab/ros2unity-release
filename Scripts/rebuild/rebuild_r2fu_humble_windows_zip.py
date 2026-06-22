#!/usr/bin/env python3
"""Rebuild and package the Ros2ForUnity Humble Windows artifact."""

from __future__ import annotations

import pathlib
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    script = pathlib.Path(__file__).resolve().parent / "rebuild_r2fu_windows_zip.py"
    command = [sys.executable, str(script), "--ros-distro", "humble"]
    command.extend(sys.argv[1:] if argv is None else argv)
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
