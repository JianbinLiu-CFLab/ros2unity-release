#!/usr/bin/env python3
# Copyright (c) 2026 Jianbin Liu.
# SPDX-License-Identifier: Apache-2.0
#
# Purpose: Synchronize ros2cs and ros2-for-unity release references.
# Usage:
#   python .\Scripts\release\bump_ros2unity_versions.py \
#     --ros2cs-version v0.7.0 \
#     --r2fu-version v0.7.0
#
# This script edits documentation and reproducible dependency pins only. It does
# not create git commits, tags, GitHub releases, or artifact uploads.

"""Synchronize ros2cs and ros2-for-unity release references."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


EXIT_SUCCESS = 0
WORKSPACE_ROOT_PARENT_DEPTH = 2
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_TAG_PATTERN = r"v\d+\.\d+\.\d+(?:-[A-Za-z0-9_.-]+)?"
RELEASE_TAG_RE = re.compile(rf"^{RELEASE_TAG_PATTERN}$")


@dataclass
class PlannedChange:
    """Records one file update planned or written by the script."""

    path: Path
    action: str


class VersionSync:
    """Coordinates release-reference edits across both repositories."""

    def __init__(
        self,
        workspace_root: Path,
        ros2cs_version: str,
        r2fu_version: str,
        ros2cs_sha: str,
        dry_run: bool,
    ) -> None:
        self.workspace_root = workspace_root
        self.ros2cs_root = self.source_repo("ros2cs")
        self.r2fu_root = self.source_repo("ros2-for-unity")
        self.ros2cs_version = ros2cs_version
        self.r2fu_version = r2fu_version
        self.ros2cs_sha = ros2cs_sha
        self.dry_run = dry_run
        self.changes: list[PlannedChange] = []

    def source_repo(self, name: str) -> Path:
        """Return the local checkout path, preferring the release workspace layout."""
        third_party = self.workspace_root / "third-party" / name
        if third_party.exists():
            return third_party
        return self.workspace_root / name

    @property
    def r2fu_semver(self) -> str:
        """Return v-prefixed semantic version from the R2FU release tag."""
        match = re.match(r"^(v\d+\.\d+\.\d+)", self.r2fu_version)
        if not match:
            raise ValueError(f"Cannot parse semantic version from {self.r2fu_version}")
        return match.group(1)

    def rel(self, path: Path) -> str:
        """Format a path relative to the workspace root for console output."""
        return path.relative_to(self.workspace_root).as_posix()

    def read(self, path: Path) -> str:
        """Read a UTF-8 text file."""
        return path.read_text(encoding="utf-8")

    def write_if_changed(self, path: Path, content: str, action: str) -> None:
        """Record and optionally write a file when content changes."""
        original = self.read(path)
        if original == content:
            return
        self.changes.append(PlannedChange(path, action))
        if not self.dry_run:
            path.write_text(content, encoding="utf-8", newline="\n")

    def require_file(self, path: Path) -> None:
        """Fail early when an expected repository file is missing."""
        if not path.exists():
            raise FileNotFoundError(f"Missing expected file: {self.rel(path)}")

    def replace_exact(self, text: str, old: str, new: str, path: Path, label: str) -> str:
        """Replace at least one exact string, failing when the old value is absent."""
        if old not in text:
            raise ValueError(f"Cannot find {label} in {self.rel(path)}: {old}")
        return text.replace(old, new)

    def detect_r2fu_old_version(self, text: str, path: Path) -> str:
        """Read the current latest R2FU release tag from README.md."""
        match = re.search(
            rf"Latest source release: \[`(?P<tag>{RELEASE_TAG_PATTERN})`\]",
            text,
        )
        if not match:
            raise ValueError(f"Cannot detect current R2FU release tag in {self.rel(path)}")
        return match.group("tag")

    def detect_ros2cs_old_version(self, text: str, path: Path) -> str:
        """Read the current ros2cs release tag from README.md."""
        match = re.search(rf"version: (?P<tag>{RELEASE_TAG_PATTERN})", text)
        if not match:
            raise ValueError(f"Cannot detect current ros2cs release tag in {self.rel(path)}")
        return match.group("tag")

    def update_ros2cs_readme(self) -> None:
        """Update ros2cs public release references."""
        path = self.ros2cs_root / "README.md"
        self.require_file(path)
        text = self.read(path)
        old_version = self.detect_ros2cs_old_version(text, path)
        text = self.replace_exact(text, old_version, self.ros2cs_version, path, "ros2cs release tag")
        self.write_if_changed(path, text, f"update ros2cs README release tag to {self.ros2cs_version}")

    def update_ros2cs_windows_readme(self) -> None:
        """Update ros2cs Windows release references."""
        path = self.ros2cs_root / "README-WINDOWS.md"
        self.require_file(path)
        text = self.read(path)
        text = re.sub(
            RELEASE_TAG_PATTERN,
            self.ros2cs_version,
            text,
        )
        self.write_if_changed(path, text, f"update ros2cs Windows release tag to {self.ros2cs_version}")

    def update_r2fu_repos_pin(self) -> None:
        """Update the reproducible ros2cs dependency pin used by R2FU builds."""
        path = self.r2fu_root / "ros2cs.repos"
        self.require_file(path)
        text = self.read(path)
        updated, count = re.subn(
            r"(version:\s*)[0-9a-f]{40}",
            rf"\g<1>{self.ros2cs_sha}",
            text,
            count=1,
        )
        if count != 1:
            raise ValueError(f"Expected one ros2cs SHA pin in {self.rel(path)}, found {count}")
        self.write_if_changed(path, updated, f"pin R2FU ros2cs dependency to {self.ros2cs_sha}")

    def update_r2fu_readme(self) -> None:
        """Update R2FU release and ros2cs dependency references."""
        path = self.r2fu_root / "README.md"
        self.require_file(path)
        text = self.read(path)
        old_r2fu = self.detect_r2fu_old_version(text, path)
        old_r2fu_semver = old_r2fu.split("-jazzy-win64-preview.", maxsplit=1)[0]

        text = self.replace_exact(text, old_r2fu, self.r2fu_version, path, "R2FU release tag")
        # Stable tags already equal their semantic version and were replaced above.
        if old_r2fu_semver != old_r2fu:
            text = self.replace_exact(text, old_r2fu_semver, self.r2fu_semver, path, "R2FU semver")
        if old_r2fu != self.r2fu_version:
            text = re.sub(
                rf"- previous: \[`{RELEASE_TAG_PATTERN}`\]"
                r"\(https://github\.com/JianbinLiu-CFLab/ros2-for-unity/releases/tag/"
                rf"{RELEASE_TAG_PATTERN}\)",
                f"- previous: [`{old_r2fu}`](https://github.com/JianbinLiu-CFLab/ros2-for-unity/releases/tag/{old_r2fu})",
                text,
                count=1,
            )

        old_ros2cs = re.search(r"v\d+\.\d+\.\d+-jazzy-preview\.\d+", text)
        if old_ros2cs:
            text = text.replace(old_ros2cs.group(0), self.ros2cs_version)

        updated, count = re.subn(
            r"(https://github\.com/JianbinLiu-CFLab/ros2cs\.git\s*\nversion:\s*)[0-9a-f]{40}",
            rf"\g<1>{self.ros2cs_sha}",
            text,
            count=1,
        )
        if count != 1:
            raise ValueError(f"Expected one maintained ros2cs SHA block in {self.rel(path)}, found {count}")
        self.write_if_changed(path, updated, f"update R2FU README to {self.r2fu_version}")

    def update_r2fu_windows_readme(self) -> None:
        """Update the R2FU Windows validation snapshot release references."""
        path = self.r2fu_root / "README-WINDOWS.md"
        self.require_file(path)
        text = self.read(path)
        old_r2fu = re.search(RELEASE_TAG_PATTERN, text)
        if not old_r2fu:
            raise ValueError(f"Cannot detect current R2FU Windows release tag in {self.rel(path)}")
        old_r2fu_semver = re.match(r"^(v\d+\.\d+\.\d+)", old_r2fu.group(0)).group(1)

        text = text.replace(old_r2fu.group(0), self.r2fu_version)
        if old_r2fu_semver != old_r2fu.group(0):
            text = text.replace(old_r2fu_semver, self.r2fu_semver)
        text = re.sub(r"v\d+\.\d+\.\d+-jazzy-preview\.\d+", self.ros2cs_version, text)
        self.write_if_changed(path, text, f"update R2FU Windows snapshot to {self.r2fu_version}")

    def run(self) -> int:
        """Apply or report all release-reference edits."""
        self.update_ros2cs_readme()
        self.update_ros2cs_windows_readme()
        self.update_r2fu_repos_pin()
        self.update_r2fu_readme()
        self.update_r2fu_windows_readme()

        prefix = "[DRY-RUN]" if self.dry_run else "[bump_ros2unity_versions]"
        if not self.changes:
            print(f"{prefix} version references are already synchronized.")
            return EXIT_SUCCESS

        print(f"{prefix} planned changes:" if self.dry_run else f"{prefix} updated files:")
        for change in self.changes:
            print(f"  - {self.rel(change.path)}: {change.action}")
        return EXIT_SUCCESS


def git_head(repo: Path) -> str:
    """Return the current HEAD SHA for a repository."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the workspace version sync."""
    parser = argparse.ArgumentParser(description="Synchronize ros2cs and ros2-for-unity release references.")
    parser.add_argument("--ros2cs-version", required=True, help="ros2cs release tag, for example v0.7.0")
    parser.add_argument("--r2fu-version", required=True, help="R2FU release tag, for example v0.7.0")
    parser.add_argument("--ros2cs-sha", help="ros2cs commit SHA to pin; defaults to ros2cs HEAD")
    parser.add_argument("--workspace-root", type=Path, help="Workspace root containing ros2cs and ros2-for-unity.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing files.")
    return parser.parse_args()


def main() -> int:
    """Validate CLI input and run the version sync."""
    args = parse_args()
    if not RELEASE_TAG_RE.match(args.ros2cs_version):
        raise SystemExit(f"Invalid --ros2cs-version: {args.ros2cs_version}")
    if not RELEASE_TAG_RE.match(args.r2fu_version):
        raise SystemExit(f"Invalid --r2fu-version: {args.r2fu_version}")

    workspace_root = args.workspace_root or Path(__file__).resolve().parents[WORKSPACE_ROOT_PARENT_DEPTH]
    workspace_root = workspace_root.resolve()
    ros2cs_root = workspace_root / "third-party" / "ros2cs"
    if not ros2cs_root.exists():
        ros2cs_root = workspace_root / "ros2cs"
    ros2cs_sha = args.ros2cs_sha or git_head(ros2cs_root)
    if not SHA_RE.match(ros2cs_sha):
        raise SystemExit(f"Invalid ros2cs SHA: {ros2cs_sha}")

    return VersionSync(
        workspace_root=workspace_root,
        ros2cs_version=args.ros2cs_version,
        r2fu_version=args.r2fu_version,
        ros2cs_sha=ros2cs_sha,
        dry_run=args.dry_run,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
