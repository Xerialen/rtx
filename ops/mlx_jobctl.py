#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator commands for detached MLX jobs."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

from mlx_run_job import atomic_json, orphan_sweep, utc_now


MLX_ROOT = Path(os.environ.get("MLX_ROOT", "~/mlx")).expanduser().resolve()
JOBS_ROOT = MLX_ROOT / "jobs"


def tmux_name(job_id: str) -> str:
    return f"mlx-{job_id}"


def tmux_running(job_id: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", tmux_name(job_id)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def heartbeat_age(job_dir: Path) -> float | None:
    heartbeat = job_dir / "heartbeat"
    return round(time.time() - heartbeat.stat().st_mtime, 1) if heartbeat.exists() else None


def one_status(job_dir: Path) -> dict[str, object]:
    status_path = job_dir / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    matches = status.get("matches") or {}
    counts = {state: 0 for state in ("planned", "running", "completed", "failed")}
    for match in matches.values():
        counts[str(match.get("state", "planned"))] = counts.get(str(match.get("state")), 0) + 1
    age = heartbeat_age(job_dir)
    leases = list((job_dir / "ports").glob("*.lease")) if (job_dir / "ports").is_dir() else []
    active = status.get("state") in {"running", "stopped"}
    return {
        "jobId": job_dir.name,
        "state": status.get("state", "uninitialized"),
        "counts": counts,
        "tmux": tmux_running(job_dir.name),
        "heartbeatAgeSeconds": age,
        "stale": active and (bool(age is not None and age > 300) or (bool(leases) and not tmux_running(job_dir.name))),
        "leases": len(leases),
        "runnerRunId": status.get("runnerRunId"),
        "updatedAt": status.get("updatedAt"),
    }


def status_command(_args: argparse.Namespace) -> int:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(MLX_ROOT)
    jobs = [one_status(path) for path in sorted(JOBS_ROOT.iterdir()) if path.is_dir()]
    gpu = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
    )
    result = {
        "schema": "mlx.status.v1",
        "time": utc_now(),
        "jobs": jobs,
        "diskFreeGiB": round(disk.free / 1024**3, 1),
        "diskWarning": disk.free < 100 * 1024**3,
        "gpuProcesses": [line for line in gpu.stdout.splitlines() if line.strip()],
        "outboxReady": len(list((MLX_ROOT / "demos" / "outbox").glob("*/.ready"))),
        "synced": len(list((MLX_ROOT / "demos" / "synced").glob("*/.ready"))),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def launch(job_id: str, mode: str) -> int:
    job_dir = JOBS_ROOT / job_id
    spec = job_dir / "job.spec.json"
    if not spec.is_file():
        raise FileNotFoundError(f"missing job spec: {spec}")
    print(json.dumps(one_status(job_dir), indent=2, sort_keys=True), flush=True)
    if tmux_running(job_id):
        raise RuntimeError(f"tmux session already exists: {tmux_name(job_id)}")
    runner = Path(__file__).resolve().with_name("mlx_run_job.py")
    command = shlex.join([sys.executable, str(runner), "--spec", str(spec), "--mode", mode])
    subprocess.run(
        [
            "tmux", "new-session", "-d", "-s", tmux_name(job_id),
            "-c", str(runner.parent.parent),
            command,
        ],
        check=True,
    )
    print(f"started {tmux_name(job_id)} mode={mode}")
    return 0


def attach_command(args: argparse.Namespace) -> int:
    os.execvp("tmux", ["tmux", "attach-session", "-t", tmux_name(args.job_id)])
    return 0


def stop_command(args: argparse.Namespace) -> int:
    job_dir = JOBS_ROOT / args.job_id
    print(json.dumps(one_status(job_dir), indent=2, sort_keys=True), flush=True)
    if tmux_running(args.job_id):
        subprocess.run(["tmux", "kill-session", "-t", tmux_name(args.job_id)], check=True)
    generation = f"stop-{uuid.uuid4()}"

    def log(message: str) -> None:
        line = f"{utc_now()} {message}"
        with (job_dir / "job.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line)

    swept = orphan_sweep(job_dir, generation, log)
    status_path = job_dir / "status.json"
    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        for match in status.get("matches", {}).values():
            if match.get("state") == "running":
                match["state"] = "planned"
        status["state"] = "stopped"
        status["updatedAt"] = utc_now()
        atomic_json(status_path, status)
    print(f"stopped {args.job_id}; orphan groups swept={swept}")
    return 0


def export_command(args: argparse.Namespace) -> int:
    job_dir = JOBS_ROOT / args.job_id
    export_dir = MLX_ROOT / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    target = export_dir / f"{args.job_id}-metadata.tar.gz"
    allowed = ("job.spec.json", "config.json", "status.json", "manifest.jsonl", "job.log", "exitstatus")
    with tarfile.open(target, "w:gz") as archive:
        for name in allowed:
            path = job_dir / name
            if path.is_file():
                archive.add(path, arcname=f"{args.job_id}/{name}")
    print(target)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    executable = Path(sys.argv[0]).name
    implicit = {
        "mlx-status": "status",
        "mlx-attach": "attach",
        "mlx-resume": "resume",
        "mlx-retry-failed": "retry",
        "mlx-stop": "stop",
        "mlx-export": "export",
    }.get(executable)
    if implicit:
        argv = [implicit, *argv]
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("status")
    for command in ("attach", "resume", "retry", "stop", "export"):
        child = subcommands.add_parser(command)
        child.add_argument("job_id")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "status":
        return status_command(args)
    if args.command == "attach":
        return attach_command(args)
    if args.command == "resume":
        return launch(args.job_id, "resume")
    if args.command == "retry":
        return launch(args.job_id, "retry")
    if args.command == "stop":
        return stop_command(args)
    if args.command == "export":
        return export_command(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
