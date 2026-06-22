#!/usr/bin/env python3
"""Rebuild the Ros2ForUnity Jazzy Ubuntu WSL2 artifact zip."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import time
import zipfile


DEFAULT_ARTIFACT_BASENAME = "Ros2ForUnity_jazzy_standalone_ubuntu_wsl2_x86_64"
DEFAULT_PLATFORM = "ubuntu_wsl2_x86_64"
DEFAULT_ROS_DISTRO = "jazzy"
PACKAGE_KIND = "standalone_unity_asset_zip"


def workspace_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def default_output_dir() -> pathlib.Path:
    return workspace_root() / "artifacts" / "ros2-for-unity" / DEFAULT_ROS_DISTRO / DEFAULT_PLATFORM


def default_logs_dir() -> pathlib.Path:
    return workspace_root() / ".build" / "reports" / "r2fu-ubuntu-wsl2"


def default_temp_dir() -> pathlib.Path:
    return workspace_root() / ".build" / "wsl2-r2fu"


def run_text(command: list[str], *, cwd: pathlib.Path | None = None, check: bool = True) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def run_git(repo: pathlib.Path, *args: str) -> str | None:
    try:
        return run_text(["git", "-C", str(repo), *args])
    except (OSError, subprocess.CalledProcessError):
        return None


def git_info(repo: pathlib.Path) -> dict[str, object]:
    status_short = run_git(repo, "status", "--short")
    return {
        "path": str(repo),
        "branch": run_git(repo, "branch", "--show-current"),
        "commit": run_git(repo, "rev-parse", "HEAD"),
        "shortCommit": run_git(repo, "rev-parse", "--short", "HEAD"),
        "statusShort": status_short,
        "dirty": bool(status_short),
    }


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_existing_outputs(paths: list[pathlib.Path], output_dir: pathlib.Path) -> pathlib.Path | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None

    backup_dir = output_dir / f"backup_{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in existing:
        shutil.move(str(path), str(backup_dir / path.name))
    return backup_dir


def wsl_args(distro: str | None) -> list[str]:
    args = ["wsl.exe"]
    if distro:
        args.extend(["-d", distro])
    return args


def wsl_command(distro: str | None, *command: str) -> list[str]:
    return [*wsl_args(distro), "--", *command]


def wsl_path(path: pathlib.Path, *, distro: str | None) -> str:
    return run_text(wsl_command(distro, "wslpath", "-a", str(path.resolve()).replace("\\", "/")))


def run_streamed(command: list[str], *, cwd: pathlib.Path, log_path: pathlib.Path, name: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print("")
    print(f"==> {name}")
    print(f"cmd: {' '.join(command)}")
    print(f"log: {log_path}")

    started = time.monotonic()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        last_output = time.monotonic()
        for line in process.stdout:
            print(line, end="")
            log.write(line)
            log.flush()
            last_output = time.monotonic()
        while process.poll() is None:
            if time.monotonic() - last_output > 15:
                print(f"[still running] {name} elapsed={int(time.monotonic() - started)}s log={log_path}")
                last_output = time.monotonic()
            time.sleep(0.5)

    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command)


def shell_quote(value: str | pathlib.Path) -> str:
    return shlex.quote(str(value).replace("\\", "/"))


def write_lf_script(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def build_wsl_script(
    *,
    workspace_name: str,
    r2fu_source_wsl: str,
    ros2cs_source_wsl: str,
    r2fu_commit: str,
    ros2cs_commit: str,
    output_dir_wsl: str,
    logs_dir_wsl: str,
    artifact_basename: str,
    parallel_workers: int,
    clean_workspace: bool,
    skip_tests: bool,
    keep_workspace: bool,
) -> str:
    clean_flag = "1" if clean_workspace else "0"
    skip_tests_flag = "1" if skip_tests else "0"
    keep_workspace_flag = "1" if keep_workspace else "0"
    return f"""#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_NAME={shlex.quote(workspace_name)}
R2FU_SOURCE={shlex.quote(r2fu_source_wsl)}
ROS2CS_SOURCE={shlex.quote(ros2cs_source_wsl)}
R2FU_COMMIT={shlex.quote(r2fu_commit)}
ROS2CS_COMMIT={shlex.quote(ros2cs_commit)}
OUTPUT_DIR={shlex.quote(output_dir_wsl)}
LOGS_DIR={shlex.quote(logs_dir_wsl)}
ARTIFACT_BASENAME={shlex.quote(artifact_basename)}
PARALLEL_WORKERS={parallel_workers}
CLEAN_WORKSPACE={clean_flag}
SKIP_TESTS={skip_tests_flag}
KEEP_WORKSPACE={keep_workspace_flag}
WORK_ROOT="$HOME/$WORKSPACE_NAME"
R2FU_WORK="$WORK_ROOT/ros2-for-unity"
export DOTNET_ROOT="${{DOTNET_ROOT:-$HOME/.dotnet}}"
export PATH="$DOTNET_ROOT:$HOME/.local/bin:$PATH"

require_command() {{
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 2
  fi
}}

echo "==> wsl environment check"
echo "WSL_DISTRO_NAME=${{WSL_DISTRO_NAME:-unknown}}"
uname -a
cat /etc/os-release
test -f /opt/ros/jazzy/setup.bash || {{ echo "Missing /opt/ros/jazzy/setup.bash" >&2; exit 2; }}
for cmd in git python3 colcon dotnet patchelf ninja; do
  require_command "$cmd"
done
if ! command -v zip >/dev/null 2>&1; then
  echo "zip not found; python3 -m zipfile fallback will be used"
fi

mkdir -p "$OUTPUT_DIR" "$LOGS_DIR"
if [ "$CLEAN_WORKSPACE" = "1" ]; then
  echo "==> clean wsl workspace: $WORK_ROOT"
  case "$WORK_ROOT" in
    "$HOME"/r2fu-*) rm -rf "$WORK_ROOT" ;;
    *) echo "Refusing unsafe clean target: $WORK_ROOT" >&2; exit 2 ;;
  esac
fi
mkdir -p "$WORK_ROOT"

echo "==> clone ros2-for-unity"
if [ ! -d "$R2FU_WORK/.git" ]; then
  git clone "file://$R2FU_SOURCE" "$R2FU_WORK"
fi
git -C "$R2FU_WORK" fetch --all --tags --prune
git -C "$R2FU_WORK" checkout --detach "$R2FU_COMMIT"

set +u
source /opt/ros/jazzy/setup.bash
set -u
echo "ROS_DISTRO=$ROS_DISTRO"
python3 --version
dotnet --version
patchelf --version

echo "==> pull repositories"
cd "$R2FU_WORK"
./pull_repositories.sh
echo "==> replace src/ros2cs with current local ros2cs commit"
git -C src/ros2cs fetch "file://$ROS2CS_SOURCE" "$ROS2CS_COMMIT"
git -C src/ros2cs checkout --detach "$ROS2CS_COMMIT"

export ROS2CS_PARALLEL_WORKERS="$PARALLEL_WORKERS"
export ROS2CS_EVENT_HANDLER="${{ROS2CS_EVENT_HANDLER:-console_direct+}}"

echo "==> r2fu standalone build"
BUILD_ARGS=(--standalone --clean-install)
if [ "$SKIP_TESTS" != "1" ]; then
  BUILD_ARGS+=(--with-tests)
fi
./build.sh "${{BUILD_ARGS[@]}}"

if [ "$SKIP_TESTS" != "1" ]; then
  echo "==> ros2cs tests"
  colcon test --build-base build --install-base install --packages-select ros2cs_tests --event-handlers console_direct+
  colcon test-result --test-result-base build --verbose --all
fi

echo "==> linux asset closure"
ASSET="$R2FU_WORK/install/asset/Ros2ForUnity"
LINUX_PLUGINS="$ASSET/Plugins/Linux/x86_64"
test -d "$ASSET"
test -d "$LINUX_PLUGINS"
test -f "$ASSET/metadata_ros2_for_unity.xml"
test -f "$ASSET/Plugins/metadata_ros2cs.xml"
test -f "$ASSET/Plugins/ros2cs_common.dll"
test -f "$ASSET/Plugins/ros2cs_core.dll"

for pattern in "librcl.so*" "librmw_implementation.so*" "librcutils.so*" "libfastrtps.so*" "libfastcdr.so*" "libspdlog.so*" "libssl.so*" "libcrypto.so*"; do
  if ! compgen -G "$LINUX_PLUGINS/$pattern" >/dev/null; then
    echo "Missing required Linux plugin pattern: $pattern" >&2
    exit 3
  fi
done

LDD_LOG="$LOGS_DIR/ldd-linux-x86_64.log"
: > "$LDD_LOG"
while IFS= read -r -d '' lib; do
  echo "### $lib" >> "$LDD_LOG"
  ldd "$lib" >> "$LDD_LOG" 2>&1 || true
done < <(find "$LINUX_PLUGINS" -maxdepth 1 -type f -name "*.so*" -print0)
if grep -q "not found" "$LDD_LOG"; then
  echo "ldd found missing dependencies; see $LDD_LOG" >&2
  grep "not found" "$LDD_LOG" >&2
  exit 3
fi

ZIP_PATH="$OUTPUT_DIR/$ARTIFACT_BASENAME.zip"
SHA_PATH="$OUTPUT_DIR/$ARTIFACT_BASENAME.sha256.txt"
MANIFEST_INPUT="$LOGS_DIR/$ARTIFACT_BASENAME.wsl-summary.json"
rm -f "$ZIP_PATH" "$SHA_PATH" "$MANIFEST_INPUT"
cd "$R2FU_WORK/install/asset"
echo "==> package artifact: $ZIP_PATH"
if command -v zip >/dev/null 2>&1; then
  zip -q -r "$ZIP_PATH" Ros2ForUnity
else
  python3 -m zipfile -c "$ZIP_PATH" Ros2ForUnity
fi
sha256sum "$ZIP_PATH" | sed "s#  .*#  $ARTIFACT_BASENAME.zip#" > "$SHA_PATH"

export ASSET LINUX_PLUGINS MANIFEST_INPUT R2FU_WORK ZIP_PATH LDD_LOG
python3 - <<'PY'
import json
import os
import pathlib
import platform
import subprocess

asset = pathlib.Path(os.environ.get("ASSET", ""))
plugins = pathlib.Path(os.environ.get("LINUX_PLUGINS", ""))
summary_path = pathlib.Path(os.environ["MANIFEST_INPUT"])
r2fu_work = pathlib.Path(os.environ["R2FU_WORK"])
zip_path = pathlib.Path(os.environ["ZIP_PATH"])
ldd_log = pathlib.Path(os.environ["LDD_LOG"])

def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

files = [p for p in asset.rglob("*") if p.is_file()]
native = [p for p in plugins.glob("*") if p.is_file()]
summary = {{
    "wslDistroName": os.environ.get("WSL_DISTRO_NAME"),
    "uname": platform.uname()._asdict(),
    "ubuntuPrettyName": next((line.split("=", 1)[1].strip().strip('"') for line in pathlib.Path("/etc/os-release").read_text().splitlines() if line.startswith("PRETTY_NAME=")), None),
    "rosDistro": os.environ.get("ROS_DISTRO"),
    "assetFileCount": len(files),
    "nativePluginFileCount": len(native),
    "managedPluginFileCount": len([p for p in (asset / "Plugins").glob("*.dll")]),
    "metadataFileCount": len([p for p in asset.rglob("metadata_*.xml")]),
    "lddLog": str(ldd_log),
    "lddMissingDependencies": False,
    "zipPath": str(zip_path),
    "r2fu": {{
        "commit": git(r2fu_work, "rev-parse", "HEAD"),
        "shortCommit": git(r2fu_work, "rev-parse", "--short", "HEAD"),
    }},
    "ros2cs": {{
        "commit": git(r2fu_work / "src" / "ros2cs", "rev-parse", "HEAD"),
        "shortCommit": git(r2fu_work / "src" / "ros2cs", "rev-parse", "--short", "HEAD"),
    }},
}}
summary_path.write_text(json.dumps(summary, indent=2) + "\\n", encoding="utf-8")
PY

if [ "$KEEP_WORKSPACE" != "1" ]; then
  echo "==> cleanup wsl workspace: $WORK_ROOT"
  case "$WORK_ROOT" in
    "$HOME"/r2fu-*) rm -rf "$WORK_ROOT" ;;
    *) echo "Refusing unsafe cleanup target: $WORK_ROOT" >&2; exit 2 ;;
  esac
fi
"""


def zip_entry_count(zip_path: pathlib.Path) -> int:
    with zipfile.ZipFile(zip_path, "r") as archive:
        return len(archive.infolist())


def write_manifest(
    *,
    manifest_path: pathlib.Path,
    artifact_name: str,
    zip_path: pathlib.Path,
    sha256: str,
    source_r2fu: dict[str, object],
    source_ros2cs: dict[str, object],
    wsl_summary: dict[str, object],
    log_path: pathlib.Path,
    backup_dir: pathlib.Path | None,
) -> None:
    manifest = {
        "artifactName": artifact_name,
        "artifactPath": str(zip_path),
        "createdAtLocal": dt.datetime.now().astimezone().isoformat(),
        "rosDistro": DEFAULT_ROS_DISTRO,
        "platform": DEFAULT_PLATFORM,
        "packageKind": PACKAGE_KIND,
        "sha256": sha256,
        "sizeBytes": zip_path.stat().st_size,
        "zipEntryCount": zip_entry_count(zip_path),
        "source": {
            "ros2ForUnity": source_r2fu,
            "ros2cs": source_ros2cs,
        },
        "buildEnvironment": {
            "host": "Windows orchestrating WSL2",
            "wslDistroName": wsl_summary.get("wslDistroName"),
            "ubuntuPrettyName": wsl_summary.get("ubuntuPrettyName"),
            "uname": wsl_summary.get("uname"),
            "rosDistro": wsl_summary.get("rosDistro"),
        },
        "validation": {
            "verdict": "UBUNTU_WSL2_ARTIFACT_BUILD_GREEN",
            "networkVerdict": "NOT_CLAIMED_WSL2_NAT",
            "assetFileCount": wsl_summary.get("assetFileCount"),
            "managedPluginFileCount": wsl_summary.get("managedPluginFileCount"),
            "nativePluginFileCount": wsl_summary.get("nativePluginFileCount"),
            "metadataFileCount": wsl_summary.get("metadataFileCount"),
            "lddMissingDependencies": wsl_summary.get("lddMissingDependencies"),
            "lddLog": wsl_summary.get("lddLog"),
            "buildLog": str(log_path),
        },
        "boundaries": [
            "Built inside WSL2 Linux filesystem and packaged as a Linux x86_64 Unity asset.",
            "WSL2 validates Linux build/package closure only.",
            "DDS discovery, Foxglove connectivity, native Ubuntu runtime, and product readiness are not claimed by this artifact script.",
        ],
    }
    if backup_dir is not None:
        manifest["previousArtifactBackup"] = str(backup_dir)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distro", default=os.environ.get("R2FU_WSL_DISTRO"), help="Optional WSL distro name. Defaults to the default WSL distro.")
    parser.add_argument("--workspace-name", default="r2fu-wsl2-jazzy-artifact")
    parser.add_argument("--parallel-workers", type=int, default=max(1, os.cpu_count() or 1))
    parser.add_argument("--output-dir", type=pathlib.Path, default=default_output_dir())
    parser.add_argument("--logs-dir", type=pathlib.Path, default=default_logs_dir())
    parser.add_argument("--temp-dir", type=pathlib.Path, default=default_temp_dir())
    parser.add_argument("--artifact-basename", default=DEFAULT_ARTIFACT_BASENAME)
    parser.add_argument("--skip-tests", action="store_true", help="Build and package only; skip ros2cs_tests.")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep the WSL workspace after completion.")
    parser.add_argument("--no-clean-workspace", action="store_true", help="Reuse the WSL workspace instead of deleting it first.")
    parser.add_argument("--no-backup", action="store_true", help="Overwrite current zip/sha/manifest instead of moving them to backup_<timestamp>.")
    parser.add_argument("--dry-run", action="store_true", help="Print the generated WSL script path and skip execution.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = workspace_root()
    r2fu_repo = root / "ros2-for-unity"
    ros2cs_repo = root / "ros2cs"
    if args.parallel_workers < 1:
        raise ValueError("--parallel-workers must be positive")

    source_r2fu = git_info(r2fu_repo)
    source_ros2cs = git_info(ros2cs_repo)
    r2fu_commit = str(source_r2fu.get("commit") or "")
    ros2cs_commit = str(source_ros2cs.get("commit") or "")
    if not r2fu_commit or not ros2cs_commit:
        raise RuntimeError("Could not resolve source commits for ros2-for-unity and ros2cs.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.logs_dir.mkdir(parents=True, exist_ok=True)
    args.temp_dir.mkdir(parents=True, exist_ok=True)

    r2fu_source_wsl = wsl_path(r2fu_repo, distro=args.distro)
    ros2cs_source_wsl = wsl_path(ros2cs_repo, distro=args.distro)
    output_dir_wsl = wsl_path(args.output_dir, distro=args.distro)
    logs_dir_wsl = wsl_path(args.logs_dir, distro=args.distro)

    zip_path = args.output_dir / f"{args.artifact_basename}.zip"
    sha_path = args.output_dir / f"{args.artifact_basename}.sha256.txt"
    manifest_path = args.output_dir / f"{args.artifact_basename}.manifest.json"
    summary_path = args.logs_dir / f"{args.artifact_basename}.wsl-summary.json"
    run_log_path = args.logs_dir / f"{args.artifact_basename}.build.log"
    script_path = args.temp_dir / f"{args.artifact_basename}.sh"

    backup_dir = None
    if not args.dry_run and not args.no_backup:
        backup_dir = backup_existing_outputs([zip_path, sha_path, manifest_path], args.output_dir)

    script = build_wsl_script(
        workspace_name=args.workspace_name,
        r2fu_source_wsl=r2fu_source_wsl,
        ros2cs_source_wsl=ros2cs_source_wsl,
        r2fu_commit=r2fu_commit,
        ros2cs_commit=ros2cs_commit,
        output_dir_wsl=output_dir_wsl,
        logs_dir_wsl=logs_dir_wsl,
        artifact_basename=args.artifact_basename,
        parallel_workers=args.parallel_workers,
        clean_workspace=not args.no_clean_workspace,
        skip_tests=args.skip_tests,
        keep_workspace=args.keep_workspace,
    )
    write_lf_script(script_path, script)
    script_wsl = wsl_path(script_path, distro=args.distro)

    print("Prepared Ubuntu WSL2 artifact rebuild:")
    print(f"R2FU:   {source_r2fu.get('shortCommit')} {source_r2fu.get('branch')}")
    print(f"ros2cs: {source_ros2cs.get('shortCommit')} {source_ros2cs.get('branch')}")
    print(f"script: {script_path}")
    print(f"output: {zip_path}")

    if args.dry_run:
        print("Dry run complete; WSL build skipped.")
        return 0

    run_streamed(wsl_command(args.distro, "bash", script_wsl), cwd=root, log_path=run_log_path, name="r2fu ubuntu wsl2 rebuild")

    if not zip_path.exists():
        raise FileNotFoundError(f"Expected artifact zip was not created: {zip_path}")
    if not summary_path.exists():
        raise FileNotFoundError(f"Expected WSL summary was not created: {summary_path}")

    digest = sha256_file(zip_path)
    sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    wsl_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    write_manifest(
        manifest_path=manifest_path,
        artifact_name=zip_path.name,
        zip_path=zip_path,
        sha256=digest,
        source_r2fu=source_r2fu,
        source_ros2cs=source_ros2cs,
        wsl_summary=wsl_summary,
        log_path=run_log_path,
        backup_dir=backup_dir,
    )

    print("")
    print("Rebuilt and packaged Ubuntu WSL2 artifact:")
    print(f"ZIP:      {zip_path}")
    print(f"SHA256:   {digest}")
    print(f"SHA FILE: {sha_path}")
    print(f"MANIFEST: {manifest_path}")
    print("Network runtime: NOT_CLAIMED_WSL2_NAT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
