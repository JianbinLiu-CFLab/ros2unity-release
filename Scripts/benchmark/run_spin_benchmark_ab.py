#!/usr/bin/env python3
"""Run an interleaved A/B ros2cs spin benchmark with provenance and bootstrap statistics."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
from typing import Any


CONFIGURATIONS = tuple(
    (entities, load)
    for entities in (1, 10, 100, 500)
    for load in ("idle", "low", "high", "few-active")
)
METRICS = (
    ("allocationBytesPerSpin", True),
    ("throughputMessagesPerSecond", False),
    ("spinDurationP95Ms", True),
    ("latencyP95Ms", True),
)


@dataclass(frozen=True)
class Variant:
    name: str
    source_root: Path
    install_base: Path
    commit: str
    dirty: bool
    dirty_label: str


def run_git(source_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def source_metadata(source_root: Path, dirty_label: str | None) -> tuple[str, bool, str]:
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise RuntimeError(f"source root is not a git checkout: {source_root}")
    try:
        if run_git(source_root, "rev-parse", "--is-inside-work-tree") != "true":
            raise RuntimeError(f"source root is not a git checkout: {source_root}")
        commit = run_git(source_root, "rev-parse", "HEAD")
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"source root is not a git checkout: {source_root}") from error
    dirty = bool(run_git(source_root, "status", "--porcelain"))
    if dirty and not dirty_label:
        raise RuntimeError(f"dirty source requires --dirty-label: {source_root}")
    return commit, dirty, dirty_label or "clean"


def benchmark_dll(install_base: Path) -> Path:
    path = install_base.resolve() / "lib" / "ros2cs_spin_benchmarks" / "dotnet" / "ros2cs_spin_benchmark.dll"
    if not path.is_file():
        raise RuntimeError(f"benchmark executable is missing from install base: {path}")
    return path


def validate_install_pair(install_a: Path, install_b: Path) -> tuple[Path, Path]:
    resolved_a = install_a.resolve()
    resolved_b = install_b.resolve()
    if resolved_a == resolved_b:
        raise RuntimeError("A and B install bases must be different")
    if resolved_a in resolved_b.parents or resolved_b in resolved_a.parents:
        raise RuntimeError("A and B install bases must not be nested")
    if not resolved_a.is_dir() or not resolved_b.is_dir():
        raise RuntimeError("A and B install bases must already exist")
    benchmark_dll(resolved_a)
    benchmark_dll(resolved_b)
    return resolved_a, resolved_b


def make_variant(
    name: str,
    source_root: Path,
    install_base: Path,
    dirty_label: str | None,
) -> Variant:
    commit, dirty, label = source_metadata(source_root, dirty_label)
    return Variant(name, source_root.resolve(), install_base.resolve(), commit, dirty, label)


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def bootstrap_ci(values: list[float], seed: int = 28, samples: int = 5000) -> list[float] | None:
    if not values:
        return None
    generator = random.Random(seed)
    means = []
    for _ in range(samples):
        resample = [values[generator.randrange(len(values))] for _ in values]
        means.append(statistics.fmean(resample))
    return [percentile(means, 0.025), percentile(means, 0.975)]


def metric_value(result: dict[str, Any], metric: str) -> float | None:
    value = result.get("metrics", {}).get(metric)
    if value is None:
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def metric_is_applicable(metric: str, load: str) -> bool:
    return metric != "latencyP95Ms" or load != "idle"


def validate_spin_duration_capacity(result: dict[str, Any], variant: str, entities: int, load: str) -> None:
    value = result.get("metrics", {}).get("spinDurationCapacityExpansions")
    if type(value) is not int or value < 0:
        raise RuntimeError(
            f"{variant} benchmark reported invalid spin duration capacity expansion count "
            f"for {entities}/{load}"
        )
    if value != 0:
        raise RuntimeError(
            f"{variant} benchmark expanded spin duration samples for {entities}/{load}: {value}"
        )


def powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def make_launcher_command(
    env_launcher: Path,
    dotnet_command: list[str],
    install_base: Path,
) -> list[str]:
    install = str(install_base)
    dotnet_arguments = " ".join(powershell_literal(value) for value in dotnet_command[1:])
    script = "; ".join(
        (
            f"$install = {powershell_literal(install)}",
            "$env:AMENT_PREFIX_PATH = $install + ';' + $env:AMENT_PREFIX_PATH",
            "$env:COLCON_PREFIX_PATH = $install + ';' + $env:COLCON_PREFIX_PATH",
            "$env:CMAKE_PREFIX_PATH = $install + ';' + $env:CMAKE_PREFIX_PATH",
            "$env:PATH = $install + '\\bin;' + $install + '\\lib\\dotnet;' + $env:PATH",
            f"& {powershell_literal(dotnet_command[0])} {dotnet_arguments}",
            "exit $LASTEXITCODE",
        )
    )
    return [
        sys.executable,
        str(env_launcher),
        "--quiet",
        "--",
        "powershell",
        "-NoProfile",
        "-Command",
        script,
    ]


def summarize_pair(
    a_values: list[float],
    b_values: list[float],
    lower_is_better: bool,
) -> dict[str, Any]:
    differences = [b - a for a, b in zip(a_values, b_values)]
    baseline_mean = statistics.fmean(a_values) if a_values else None
    candidate_mean = statistics.fmean(b_values) if b_values else None
    relative_change = None
    if baseline_mean not in (None, 0):
        relative_change = (candidate_mean - baseline_mean) / baseline_mean * 100.0
    improvement = None if relative_change is None else (
        -relative_change if lower_is_better else relative_change
    )
    return {
        "status": "PASS",
        "baseline": {
            "count": len(a_values),
            "mean": baseline_mean,
            "median": statistics.median(a_values) if a_values else None,
        },
        "candidate": {
            "count": len(b_values),
            "mean": candidate_mean,
            "median": statistics.median(b_values) if b_values else None,
        },
        "differenceCandidateMinusBaseline": {
            "mean": statistics.fmean(differences) if differences else None,
            "median": statistics.median(differences) if differences else None,
            "bootstrap95Ci": bootstrap_ci(differences),
        },
        "relativeChangePercent": relative_change,
        "improvementPercent": improvement,
        "lowerIsBetter": lower_is_better,
    }


def run_one(
    variant: Variant,
    distro: str,
    mode: str,
    entities: int,
    load: str,
    warmup: int,
    iterations: int,
    output_path: Path,
    env_launcher: Path | None,
) -> dict[str, Any]:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    install = str(variant.install_base)
    for variable in ("AMENT_PREFIX_PATH", "COLCON_PREFIX_PATH", "CMAKE_PREFIX_PATH"):
        existing = environment.get(variable, "")
        environment[variable] = install + (os.pathsep + existing if existing else "")
    environment["PATH"] = (
        str(variant.install_base / "bin")
        + os.pathsep
        + str(variant.install_base / "lib" / "dotnet")
        + os.pathsep
        + environment.get("PATH", "")
    )
    environment["ROS2CS_BENCHMARK_SOURCE_COMMIT"] = variant.commit
    environment["ROS2CS_BENCHMARK_SOURCE_DIRTY"] = "true" if variant.dirty else "false"
    environment["ROS2CS_BENCHMARK_SOURCE_ROOT"] = str(variant.source_root)
    environment["ROS2CS_BENCHMARK_SOURCE_LABEL"] = variant.dirty_label
    command = [
        "dotnet",
        str(benchmark_dll(variant.install_base)),
        "--distro",
        distro,
        "--mode",
        mode,
        "--entities",
        str(entities),
        "--load",
        load,
        "--warmup",
        str(warmup),
        "--iterations",
        str(iterations),
        "--output",
        str(output_path),
    ]
    if env_launcher is not None:
        env_launcher = env_launcher.resolve()
        if not env_launcher.is_file():
            raise RuntimeError(f"environment launcher is missing: {env_launcher}")
        command = make_launcher_command(env_launcher, command, variant.install_base)
    completed = subprocess.run(
        command,
        cwd=variant.source_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path = output_path.with_suffix(".log")
    log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"{variant.name} benchmark failed for {entities}/{load}, see {log_path}")
    result = json.loads(output_path.read_text(encoding="utf-8"))
    if result.get("status") != "PASS":
        raise RuntimeError(
            f"{variant.name} benchmark status is {result.get('status')}: {result.get('statusReason')}"
        )
    expected_dirty = "true" if variant.dirty else "false"
    if result.get("commit") != variant.commit or result.get("sourceDirty") != expected_dirty:
        raise RuntimeError(f"{variant.name} benchmark provenance mismatch in {output_path}")
    validate_spin_duration_capacity(result, variant.name, entities, load)
    for metric, _ in METRICS:
        if metric_is_applicable(metric, load) and metric_value(result, metric) is None:
            raise RuntimeError(f"{variant.name} benchmark omitted {metric} for {entities}/{load}")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-a", type=Path, required=True)
    parser.add_argument("--source-b", type=Path, required=True)
    parser.add_argument("--install-a", type=Path, required=True)
    parser.add_argument("--install-b", type=Path, required=True)
    parser.add_argument("--dirty-label-a")
    parser.add_argument("--dirty-label-b")
    parser.add_argument("--distro", default="jazzy")
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-launcher", type=Path)
    args = parser.parse_args(argv)
    if args.repetitions < 10 or args.warmup < 0 or args.iterations < 1:
        parser.error("repetitions must be at least 10, iterations must be positive, warmup must be non-negative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    install_a, install_b = validate_install_pair(args.install_a, args.install_b)
    variant_a = make_variant("A", args.source_a, install_a, args.dirty_label_a)
    variant_b = make_variant("B", args.source_b, install_b, args.dirty_label_b)
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    for entities, load in CONFIGURATIONS:
        for repetition in range(1, args.repetitions + 1):
            for variant in (variant_a, variant_b):
                output = (
                    args.output_dir
                    / "runs"
                    / f"{entities}-{load}"
                    / f"rep-{repetition:02d}-{variant.name}.json"
                ).resolve()
                result = run_one(
                    variant,
                    args.distro,
                    "wait-set",
                    entities,
                    load,
                    args.warmup,
                    args.iterations,
                    output,
                    args.env_launcher,
                )
                runs.append({
                    "variant": variant.name,
                    "entities": entities,
                    "load": load,
                    "repetition": repetition,
                    "result": result,
                    "output": str(output),
                })

    summaries: dict[str, Any] = {}
    for entities, load in CONFIGURATIONS:
        key = f"{entities}/{load}"
        configuration_runs = [
            run for run in runs
            if run["entities"] == entities and run["load"] == load
        ]
        summaries[key] = {}
        for metric, lower_is_better in METRICS:
            if not metric_is_applicable(metric, load):
                summaries[key][metric] = {
                    "status": "NOT_APPLICABLE",
                    "reason": "idle profile publishes no messages",
                    "lowerIsBetter": lower_is_better,
                }
                continue
            by_repetition = {
                (run["variant"], run["repetition"]): run
                for run in configuration_runs
            }
            paired = []
            for repetition in range(1, args.repetitions + 1):
                a_value = metric_value(by_repetition[("A", repetition)]["result"], metric)
                b_value = metric_value(by_repetition[("B", repetition)]["result"], metric)
                if a_value is not None and b_value is not None:
                    paired.append((a_value, b_value))
            if len(paired) != args.repetitions:
                raise RuntimeError(
                    f"incomplete {metric} pairs for {key}: "
                    f"expected {args.repetitions}, got {len(paired)}"
                )
            summaries[key][metric] = summarize_pair(
                [pair[0] for pair in paired],
                [pair[1] for pair in paired],
                lower_is_better,
            )

    summary = {
        "schemaVersion": 1,
        "distro": args.distro,
        "repetitions": args.repetitions,
        "warmup": args.warmup,
        "iterations": args.iterations,
        "variantA": variant_a.__dict__,
        "variantB": variant_b.__dict__,
        "configurations": list(CONFIGURATIONS),
        "runs": runs,
        "summary": summaries,
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"AB benchmark summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
