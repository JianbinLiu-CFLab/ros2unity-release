#!/usr/bin/env python3
"""Build and run a Ros2ForUnity Windows Player smoke from a release ZIP.

This script intentionally generates a disposable Unity project under a scratch
directory. It does not modify the production artifact or require the Unity
Player to be launched from a ROS-sourced shell.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable


MARKER_EDITOR_LOAD = "R2FU_UNITY_EDITOR_LOAD_PASS"
MARKER_PLAYER_BUILD = "R2FU_UNITY_PLAYER_BUILD_PASS"
MARKER_PLAYER_RUN = "R2FU_UNITY_PLAYER_RUN_PASS"
MARKER_INTERNAL_PUBSUB = "R2FU_INTERNAL_PUBSUB_PASS"
MARKER_EXTERNAL_ECHO = "R2FU_EXTERNAL_ROS2_ECHO_PASS"

SANITIZED_ROS_ENV_KEYS = (
    "ROS_DISTRO",
    "AMENT_PREFIX_PATH",
    "COLCON_PREFIX_PATH",
    "PYTHONPATH",
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ZIP = (
    WORKSPACE_ROOT
    / "artifacts"
    / "ros2-for-unity"
    / "jazzy"
    / "windows_x86_64"
    / "Ros2ForUnity_jazzy_standalone_windows_x86_64.zip"
)
DEFAULT_SCRATCH_ROOT = WORKSPACE_ROOT / ".build" / "r2fu-player-smoke"
DEFAULT_ENTER_JAZZY = WORKSPACE_ROOT / "Scripts" / "env" / "Enter-Ros2JazzyEnv.py"
DEFAULT_ENTER_HUMBLE = WORKSPACE_ROOT / "Scripts" / "env" / "Enter-Ros2HumbleEnv.py"
DEFAULT_ENTER_LYRICAL = WORKSPACE_ROOT / "Scripts" / "env" / "Enter-Ros2LyricalEnv.py"
DEFAULT_HUMBLE_ROOT = WORKSPACE_ROOT / "ros2-windows" / "ros2_humble"
DEFAULT_JAZZY_ROOT = WORKSPACE_ROOT / "ros2-windows" / "ros2_jazzy"
DEFAULT_LYRICAL_ROOT = WORKSPACE_ROOT / "ros2-windows" / "ros2_lyrical"
DEFAULT_ROS2_PYTHON = DEFAULT_JAZZY_ROOT / ".pixi" / "envs" / "default" / "python.exe"
DEFAULT_ROS2_SCRIPT = DEFAULT_JAZZY_ROOT / "Scripts" / "ros2-script.py"

DISTRO_DEFAULTS = {
    "humble": {
        "artifact_zip": WORKSPACE_ROOT
        / "artifacts"
        / "ros2-for-unity"
        / "humble"
        / "windows_x86_64"
        / "Ros2ForUnity_humble_standalone_windows_x86_64.zip",
        "enter_env": DEFAULT_ENTER_HUMBLE,
        "ros2_python": DEFAULT_HUMBLE_ROOT / ".pixi" / "envs" / "default" / "python.exe",
        "ros2_script": DEFAULT_HUMBLE_ROOT / "Scripts" / "ros2-script.py",
        "verdict": "R2FU_HUMBLE_PLAYER_SMOKE_EXTERNAL_ECHO_PASS",
    },
    "jazzy": {
        "artifact_zip": DEFAULT_ARTIFACT_ZIP,
        "enter_env": DEFAULT_ENTER_JAZZY,
        "ros2_python": DEFAULT_ROS2_PYTHON,
        "ros2_script": DEFAULT_ROS2_SCRIPT,
        "verdict": "R2FU_PLAYER_SMOKE_EXTERNAL_ECHO_PASS",
    },
    "lyrical": {
        "artifact_zip": WORKSPACE_ROOT
        / "artifacts"
        / "ros2-for-unity"
        / "lyrical"
        / "windows_x86_64"
        / "Ros2ForUnity_lyrical_standalone_windows_x86_64.zip",
        "enter_env": DEFAULT_ENTER_LYRICAL,
        "ros2_python": DEFAULT_LYRICAL_ROOT / ".pixi" / "envs" / "default" / "python.exe",
        "ros2_script": DEFAULT_LYRICAL_ROOT / "Scripts" / "ros2-script.py",
        "verdict": "R2FU_LYRICAL_PLAYER_SMOKE_EXTERNAL_ECHO_PASS",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a Ros2ForUnity Windows ZIP in a generated Unity Player."
    )
    parser.add_argument("--ros-distro", choices=sorted(DISTRO_DEFAULTS), default="jazzy")
    parser.add_argument("--artifact-zip", type=Path, default=None)
    parser.add_argument("--unity-editor", type=Path, default=None)
    parser.add_argument("--scratch-root", type=Path, default=DEFAULT_SCRATCH_ROOT)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--enter-jazzy", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--enter-env", type=Path, default=None)
    parser.add_argument("--ros2-python", type=Path, default=None)
    parser.add_argument("--ros2-script", type=Path, default=None)
    parser.add_argument("--no-clean", action="store_true", help="Keep any previous run directory.")
    parser.add_argument(
        "--rmw-implementation",
        default=None,
        help="RMW to select for the player and external echo (e.g. rmw_zenoh_cpp). "
        "Unset keeps the artifact default (FastRTPS).",
    )
    parser.add_argument(
        "--zenoh-router",
        type=Path,
        default=None,
        help="Path to rmw_zenohd.exe. When set with --rmw-implementation rmw_zenoh_cpp, the "
        "Zenoh router is started for the run (listens on localhost:7447 by default).",
    )
    parser.add_argument(
        "--scripting-backend",
        choices=("mono", "il2cpp"),
        default="mono",
        help="Unity scripting backend for the player build (default mono).",
    )
    args = parser.parse_args()
    defaults = DISTRO_DEFAULTS[args.ros_distro]
    args.artifact_zip = args.artifact_zip or defaults["artifact_zip"]
    args.enter_env = args.enter_env or args.enter_jazzy or defaults["enter_env"]
    args.ros2_python = args.ros2_python or defaults["ros2_python"]
    args.ros2_script = args.ros2_script or defaults["ros2_script"]
    args.success_verdict = defaults["verdict"]
    return args


def find_unity_editor() -> Path:
    hub_root = Path(r"C:\Program Files\Unity\Hub\Editor")
    candidates: list[Path] = []
    if hub_root.exists():
        for unity in hub_root.glob(r"*\Editor\Unity.exe"):
            candidates.append(unity)

    def sort_key(path: Path) -> tuple[int, str]:
        version = path.parts[-3] if len(path.parts) >= 3 else ""
        preferred = 0 if version.startswith("6000.") else 1
        return (preferred, version)

    candidates.sort(key=sort_key, reverse=False)
    if not candidates:
        raise FileNotFoundError(
            "Unity Editor was not found. Pass --unity-editor or install Unity through Unity Hub."
        )
    return candidates[0]


def require_file(path: Path, label: str) -> Path:
    full = path.resolve()
    if not full.is_file():
        raise FileNotFoundError(f"{label} not found: {full}")
    return full


def require_dir(path: Path, label: str) -> Path:
    full = path.resolve()
    if not full.is_dir():
        raise FileNotFoundError(f"{label} not found: {full}")
    return full


def ensure_under(path: Path, root: Path, label: str) -> Path:
    full = path.resolve()
    root_full = root.resolve()
    if full != root_full and root_full not in full.parents:
        raise ValueError(f"{label} must stay under {root_full}. Got {full}")
    return full


def run_logged(
    name: str,
    command: list[str],
    cwd: Path,
    log_path: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"==> {name}\n")
        log.write(f"cwd: {cwd}\n")
        log.write("cmd: " + " ".join(command) + "\n\n")
        log.flush()
        process = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
        )
        log.write(process.stdout or "")
        log.write(f"\nexitCode: {process.returncode}\n")
        log.write(f"elapsedSeconds: {time.monotonic() - started:.3f}\n")
    if process.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {process.returncode}. See {log_path}")
    return process


def sanitized_unity_env() -> tuple[dict[str, str], dict[str, str | None]]:
    env = os.environ.copy()
    removed: dict[str, str | None] = {}
    for key in SANITIZED_ROS_ENV_KEYS:
        removed[key] = env.pop(key, None)
    return env, removed


def env_with_rmw(env: dict[str, str], rmw: str | None) -> dict[str, str]:
    if not rmw:
        return env
    env = dict(env)
    env["RMW_IMPLEMENTATION"] = rmw
    if rmw == "rmw_zenoh_cpp":
        # Retry the router-connect check at session init so a slightly-late router start
        # does not strand the session outside the router network (default is 1 attempt).
        env.setdefault("ZENOH_ROUTER_CHECK_ATTEMPTS", "10")
    return env


def path_evidence(env: dict[str, str], ros_distro: str) -> dict[str, list[str]]:
    path_entries = env.get("PATH", "").split(os.pathsep)
    ros_entries = [
        entry
        for entry in path_entries
        if "ros2" in entry.lower() or ros_distro.lower() in entry.lower()
    ]
    return {"rosLikePathEntries": ros_entries}


def unzip_artifact(artifact_zip: Path, project_root: Path) -> Path:
    assets_root = project_root / "Assets"
    with zipfile.ZipFile(artifact_zip) as archive:
        archive.extractall(assets_root)
    package_root = assets_root / "Ros2ForUnity"
    require_dir(package_root, "Extracted Ros2ForUnity package")
    require_file(package_root / "Plugins" / "ros2cs_core.dll", "ros2cs_core.dll")
    require_file(package_root / "Plugins" / "std_msgs_assembly.dll", "std_msgs_assembly.dll")
    require_file(package_root / "Plugins" / "Windows" / "x86_64" / "rcl.dll", "rcl.dll")
    return package_root


def write_project_version(project_root: Path) -> None:
    settings_root = project_root / "ProjectSettings"
    settings_root.mkdir(parents=True, exist_ok=True)
    project_version = settings_root / "ProjectVersion.txt"
    if not project_version.exists():
        project_version.write_text("m_EditorVersion: 6000.0.0f1\n", encoding="utf-8")


def write_smoke_sources(project_root: Path, topic: str, message: str) -> None:
    smoke_root = project_root / "Assets" / "R2FUPlayerSmoke"
    editor_root = project_root / "Assets" / "Editor"
    smoke_root.mkdir(parents=True, exist_ok=True)
    editor_root.mkdir(parents=True, exist_ok=True)

    (smoke_root / "R2FUPlayerSmokeBehaviour.cs").write_text(
        f"""using System;
using System.IO;
using UnityEngine;
using ROS2;

public sealed class R2FUPlayerSmokeBehaviour : MonoBehaviour
{{
    private ROS2UnityComponent ros2Unity;
    private ROS2Node node;
    private IPublisher<std_msgs.msg.String> publisher;
    private ISubscription<std_msgs.msg.String> subscription;
    private std_msgs.msg.String message;
    private bool initialized;
    private bool internalPass;
    private float nextPublishTime;
    private float quitAt;
    private string doneFile;
    private const string TopicName = "{topic}";
    private const string Payload = "{message}";

    private void Start()
    {{
        Debug.Log("{MARKER_PLAYER_RUN}");
        ros2Unity = GetComponent<ROS2UnityComponent>();
        if (ros2Unity == null)
        {{
            ros2Unity = gameObject.AddComponent<ROS2UnityComponent>();
        }}
        doneFile = GetArg("-r2fuSmokeDoneFile");
        quitAt = Time.realtimeSinceStartup + 90.0f;
    }}

    private void Update()
    {{
        try
        {{
            if (!initialized && ros2Unity != null && ros2Unity.Ok())
            {{
                node = ros2Unity.CreateNode("r2fu_player_smoke_node");
                publisher = node.CreatePublisher<std_msgs.msg.String>(TopicName);
                subscription = node.CreateSubscription<std_msgs.msg.String>(
                    TopicName,
                    msg =>
                    {{
                        if (msg.Data == Payload && !internalPass)
                        {{
                            internalPass = true;
                            Debug.Log("{MARKER_INTERNAL_PUBSUB}");
                        }}
                    }});
                message = new std_msgs.msg.String();
                message.Data = Payload;
                initialized = true;
                Debug.Log("R2FU_EXTERNAL_TOPIC_READY " + TopicName);
            }}

            if (initialized && Time.realtimeSinceStartup >= nextPublishTime)
            {{
                message.Data = Payload;
                publisher.Publish(message);
                nextPublishTime = Time.realtimeSinceStartup + 0.2f;
            }}

            if (Time.realtimeSinceStartup >= quitAt)
            {{
                Application.Quit(internalPass ? 0 : 2);
            }}

            if (internalPass && !string.IsNullOrEmpty(doneFile) && File.Exists(doneFile))
            {{
                Application.Quit(0);
            }}
        }}
        catch (Exception e)
        {{
            Debug.LogException(e);
            Application.Quit(3);
        }}
    }}

    private static string GetArg(string name)
    {{
        var args = Environment.GetCommandLineArgs();
        for (int i = 0; i < args.Length - 1; i++)
        {{
            if (args[i] == name)
            {{
                return args[i + 1];
            }}
        }}
        return null;
    }}
}}
""",
        encoding="utf-8",
    )

    (editor_root / "R2FUPlayerSmokeBuilder.cs").write_text(
        f"""using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEditor.SceneManagement;
using ROS2;

public static class R2FUPlayerSmokeBuilder
{{
    public static void Build()
    {{
        Debug.Log("{MARKER_EDITOR_LOAD}");
        var scenePath = "Assets/R2FUPlayerSmoke/R2FUPlayerSmoke.unity";
        var buildPath = GetArg("-r2fuSmokeBuildPath");
        if (string.IsNullOrEmpty(buildPath))
        {{
            Debug.LogError("Missing -r2fuSmokeBuildPath argument.");
            EditorApplication.Exit(10);
            return;
        }}

        Directory.CreateDirectory(Path.GetDirectoryName(buildPath));
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var smoke = new GameObject("R2FU Player Smoke");
        smoke.AddComponent<ROS2UnityComponent>();
        smoke.AddComponent<R2FUPlayerSmokeBehaviour>();
        EditorSceneManager.SaveScene(scene, scenePath);

        var options = new BuildPlayerOptions
        {{
            scenes = new[] {{ scenePath }},
            locationPathName = buildPath,
            target = BuildTarget.StandaloneWindows64,
            options = BuildOptions.None
        }};

        var scriptingBackend = GetArg("-r2fuScriptingBackend");
        if (scriptingBackend == "il2cpp")
        {{
            PlayerSettings.SetScriptingBackend(BuildTargetGroup.Standalone, ScriptingImplementation.IL2CPP);
            Debug.Log("R2FU_SCRIPTING_BACKEND_IL2CPP");
        }}

        var report = BuildPipeline.BuildPlayer(options);
        if (report.summary.result != BuildResult.Succeeded)
        {{
            Debug.LogError("Unity Player build failed: " + report.summary.result);
            EditorApplication.Exit(11);
            return;
        }}

        Debug.Log("{MARKER_PLAYER_BUILD}");
        EditorApplication.Exit(0);
    }}

    private static string GetArg(string name)
    {{
        var args = System.Environment.GetCommandLineArgs();
        for (int i = 0; i < args.Length - 1; i++)
        {{
            if (args[i] == name)
            {{
                return args[i + 1];
            }}
        }}
        return null;
    }}
}}
""",
        encoding="utf-8",
    )


def prepare_project(run_root: Path, artifact_zip: Path, topic: str, message: str) -> Path:
    project_root = run_root / "UnityProject"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "Assets").mkdir(exist_ok=True)
    (project_root / "Packages").mkdir(exist_ok=True)
    write_project_version(project_root)
    unzip_artifact(artifact_zip, project_root)
    write_smoke_sources(project_root, topic, message)
    return project_root


def wait_for_marker(log_path: Path, marker: str, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if marker in text:
                return True
        time.sleep(0.5)
    return False


def launch_player(
    player_exe: Path,
    player_log: Path,
    done_file: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> subprocess.Popen[str]:
    command = [
        str(player_exe),
        "-batchmode",
        "-nographics",
        "-logFile",
        str(player_log),
        "-r2fuSmokeDoneFile",
        str(done_file),
    ]
    return subprocess.Popen(
        command,
        cwd=str(player_exe.parent),
        env=env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def launch_zenoh_router(
    enter_env: Path,
    router_exe: Path,
    router_log: Path,
) -> subprocess.Popen[str]:
    router_log.parent.mkdir(parents=True, exist_ok=True)
    # Launch via the ROS env wrapper so zenohc.dll and friends are on PATH. The router
    # listens on localhost:7447 by default, which the default rmw_zenoh session config
    # connects to, so co-located peers discover each other without further config.
    log = router_log.open("w", encoding="utf-8", errors="replace")
    command = [sys.executable, str(enter_env), "--quiet", "--", str(router_exe)]
    return subprocess.Popen(
        command,
        cwd=str(WORKSPACE_ROOT),
        text=True,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def kill_process_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def run_bounded_to_file(
    name: str,
    command: list[str],
    cwd: Path,
    log_path: Path,
    timeout_seconds: int,
    env: dict[str, str] | None,
) -> subprocess.Popen[str]:
    # Write child output to a file (not a PIPE) and bound the run by killing the whole
    # process tree on timeout. This avoids subprocess.run() blocking forever in communicate()
    # when a lingering grandchild (e.g. a zenoh session) keeps the inherited stdout pipe open.
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"==> {name}\n")
        log.write(f"cwd: {cwd}\n")
        log.write("cmd: " + " ".join(command) + "\n\n")
        log.flush()
        proc = subprocess.Popen(
            command, cwd=str(cwd), env=env, text=True, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            kill_process_tree(proc.pid)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        log.write(f"\nexitCode: {proc.returncode}\n")
    return proc


def run_external_echo(
    args: argparse.Namespace,
    topic: str,
    log_path: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    enter_env = require_file(args.enter_env, f"Enter-Ros2{args.ros_distro.capitalize()}Env.py")
    if args.ros2_python.is_file() and args.ros2_script.is_file():
        ros_command = [
            str(args.ros2_python.resolve()),
            str(args.ros2_script.resolve()),
            "topic",
            "echo",
            "--once",
            topic,
            "std_msgs/msg/String",
            "--no-daemon",
            "--spin-time",
            "15",
        ]
    else:
        ros_command = [
            "ros2",
            "topic",
            "echo",
            "--once",
            topic,
            "std_msgs/msg/String",
            "--no-daemon",
            "--spin-time",
            "15",
        ]

    # The env carries RMW_IMPLEMENTATION (via env_with_rmw); the ROS env wrapper now preserves
    # an explicit caller RMW choice, so the external echo runs on the same transport as the player.
    command = [sys.executable, str(enter_env), "--quiet", "--"] + ros_command
    return run_bounded_to_file(
        "external ros2 topic echo", command, WORKSPACE_ROOT, log_path, timeout_seconds, env
    )


def write_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def collect_markers(paths: Iterable[Path]) -> dict[str, bool]:
    text = ""
    for path in paths:
        if path.exists():
            text += path.read_text(encoding="utf-8", errors="replace")
    return {
        MARKER_EDITOR_LOAD: MARKER_EDITOR_LOAD in text,
        MARKER_PLAYER_BUILD: MARKER_PLAYER_BUILD in text,
        MARKER_PLAYER_RUN: MARKER_PLAYER_RUN in text,
        MARKER_INTERNAL_PUBSUB: MARKER_INTERNAL_PUBSUB in text,
        MARKER_EXTERNAL_ECHO: MARKER_EXTERNAL_ECHO in text,
    }


def main() -> int:
    args = parse_args()
    artifact_zip = require_file(args.artifact_zip, "Artifact ZIP")
    unity_editor = require_file(args.unity_editor, "Unity Editor") if args.unity_editor else find_unity_editor()
    scratch_root = ensure_under(args.scratch_root, WORKSPACE_ROOT, "scratch root")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_root = scratch_root / run_id

    if run_root.exists() and not args.no_clean:
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    logs_root = run_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)

    topic = f"/r2fu_player_smoke_{run_id.replace('-', '_')}"
    payload = f"r2fu-player-smoke-{run_id}"
    unity_env, removed_env = sanitized_unity_env()
    summary: dict = {
        "runId": run_id,
        "rosDistro": args.ros_distro,
        "artifactZip": str(artifact_zip),
        "unityEditor": str(unity_editor),
        "scratchRoot": str(run_root),
        "topic": topic,
        "payload": payload,
        "rmwImplementation": args.rmw_implementation,
        "scriptingBackend": args.scripting_backend,
        "removedUnityEnv": removed_env,
        "unityPathEvidence": path_evidence(unity_env, args.ros_distro),
        "logs": {},
        "markers": {},
        "exitCodes": {},
    }

    unity_editor_log = logs_root / "unity-editor-load.log"
    unity_build_log = logs_root / "unity-player-build.log"
    unity_player_log = logs_root / "unity-player-run.log"
    ros_echo_log = logs_root / "ros2-topic-echo.log"
    zenoh_router_log = logs_root / "zenoh-router.log"
    done_file = run_root / "external_echo_done.txt"
    summary_path = logs_root / "validation-summary.json"

    summary["logs"] = {
        "unityEditorLoad": str(unity_editor_log),
        "unityPlayerBuild": str(unity_build_log),
        "unityPlayerRun": str(unity_player_log),
        "ros2TopicEcho": str(ros_echo_log),
        "zenohRouter": str(zenoh_router_log),
        "summary": str(summary_path),
    }

    player = None
    zenoh_router = None
    try:
        project_root = prepare_project(run_root, artifact_zip, topic, payload)
        player_exe = run_root / "Player" / "R2FUPlayerSmoke.exe"
        summary["projectRoot"] = str(project_root)
        summary["playerPath"] = str(player_exe)

        build_command = [
            str(unity_editor),
            "-projectPath",
            str(project_root),
            "-batchmode",
            "-nographics",
            "-quit",
            "-executeMethod",
            "R2FUPlayerSmokeBuilder.Build",
            "-r2fuSmokeBuildPath",
            str(player_exe),
            "-r2fuScriptingBackend",
            args.scripting_backend,
            "-logFile",
            str(unity_build_log),
        ]
        # IL2CPP transpiles to C++ and compiles natively, which is far slower than Mono.
        build_timeout = max(args.timeout_seconds, 1800 if args.scripting_backend == "il2cpp" else 300)
        run_logged(
            "unity player build",
            build_command,
            project_root,
            unity_editor_log,
            build_timeout,
            env=unity_env,
        )
        if not unity_build_log.exists():
            raise RuntimeError(f"Unity build log was not written: {unity_build_log}")
        if MARKER_EDITOR_LOAD not in unity_build_log.read_text(encoding="utf-8", errors="replace"):
            raise RuntimeError(f"Missing {MARKER_EDITOR_LOAD} in {unity_build_log}")
        if MARKER_PLAYER_BUILD not in unity_build_log.read_text(encoding="utf-8", errors="replace"):
            raise RuntimeError(f"Missing {MARKER_PLAYER_BUILD} in {unity_build_log}")

        if args.rmw_implementation == "rmw_zenoh_cpp" and args.zenoh_router:
            router_exe = require_file(args.zenoh_router, "rmw_zenohd.exe")
            enter_env = require_file(args.enter_env, "Enter-Ros2 env wrapper")
            zenoh_router = launch_zenoh_router(enter_env, router_exe, zenoh_router_log)
            # The router binds tcp/[::]:7447 (IPv6); gate on its own startup log line rather
            # than a TCP probe so we do not depend on the bind address or v4/v6 stack.
            if not wait_for_marker(zenoh_router_log, "Started Zenoh router", 120):
                raise RuntimeError("Zenoh router did not report startup within 120s")

        player = launch_player(
            player_exe,
            unity_player_log,
            done_file,
            args.timeout_seconds,
            env_with_rmw(unity_env, args.rmw_implementation),
        )
        if not wait_for_marker(unity_player_log, MARKER_INTERNAL_PUBSUB, args.timeout_seconds):
            raise RuntimeError(f"Missing {MARKER_INTERNAL_PUBSUB} in {unity_player_log}")

        echo = run_external_echo(
            args,
            topic,
            ros_echo_log,
            args.timeout_seconds,
            env=env_with_rmw(os.environ.copy(), args.rmw_implementation),
        )
        summary["exitCodes"]["externalEcho"] = echo.returncode
        echo_text = ros_echo_log.read_text(encoding="utf-8", errors="replace")
        if payload not in echo_text:
            raise RuntimeError(f"External echo did not contain expected payload '{payload}'. See {ros_echo_log}")
        with ros_echo_log.open("a", encoding="utf-8") as log:
            log.write(f"\n{MARKER_EXTERNAL_ECHO}\n")
        done_file.write_text("external echo pass\n", encoding="utf-8")
        try:
            player.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass

        summary["verdict"] = args.success_verdict
        return_code = 0
    except Exception as exc:
        summary["verdict"] = "R2FU_PLAYER_SMOKE_EXTERNAL_ECHO_FAIL"
        summary["error"] = str(exc)
        return_code = 1
    finally:
        if player is not None:
            if player.poll() is None:
                player.terminate()
                try:
                    player.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    player.kill()
                    player.wait(timeout=10)
            summary["exitCodes"]["player"] = player.returncode
        if zenoh_router is not None:
            if zenoh_router.poll() is None:
                # The router runs as rmw_zenohd under the ROS env-wrapper python; terminate()
                # would only kill the wrapper and orphan rmw_zenohd (holding port 7447). Kill
                # the whole tree.
                kill_process_tree(zenoh_router.pid)
                try:
                    zenoh_router.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            summary["exitCodes"]["zenohRouter"] = zenoh_router.returncode
        summary["markers"] = collect_markers(
            [unity_editor_log, unity_build_log, unity_player_log, ros_echo_log]
        )
        write_summary(summary_path, summary)
        print(f"Validation summary written to: {summary_path}")
        print(f"Verdict: {summary['verdict']}")
        if "error" in summary:
            print(f"Error: {summary['error']}", file=sys.stderr)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
