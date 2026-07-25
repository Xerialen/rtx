#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Detached, resumable MLX match runner with generation-bound port leases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


STATE_TERMINAL = {"completed", "failed"}
PROTECTED_COMMAND_TOKENS = ("write_parquet.py", "vllm", "OpenHands", "rubrik", "PatrolAgent")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def demo_name(start_date: str, cell: str, index: int, terminal: str) -> str:
    safe_cell = re.sub(r"[^a-z0-9-]+", "-", cell.casefold()).strip("-")
    safe_terminal = re.sub(r"[^a-zA-Z0-9+-]+", "-", str(terminal)) or "status"
    return f"{start_date.replace('-', '')}_mlx_{safe_cell}_dm3_match-{index:04d}_{safe_terminal}.mvd"


def select_matches(matches: dict[str, dict[str, object]], mode: str) -> list[str]:
    if mode == "retry":
        selected = [key for key, value in matches.items() if value["state"] == "failed"]
    else:
        selected = []
        for key, value in matches.items():
            if value["state"] == "completed":
                continue
            if value["state"] in {"running", "failed"}:
                value["state"] = "planned"
            selected.append(key)
    return sorted(selected)


def proc_start_ticks(pid: int) -> str:
    return proc_identity(Path(f"/proc/{pid}/stat"))[2]


def proc_identity(stat_path: Path) -> tuple[int, int, str]:
    """Return PID, process group, and start-time nonce from Linux procfs."""
    raw = stat_path.read_text(encoding="utf-8")
    command_end = raw.rfind(")")
    if command_end < 0:
        raise ValueError(f"invalid proc stat: {stat_path}")
    pid = int(raw[: raw.index(" ")])
    fields_after_command = raw[command_end + 2 :].split()
    return pid, int(fields_after_command[2]), fields_after_command[19]


def lease_process_matches(lease: dict[str, object]) -> bool:
    """Require the lease PID, PGID, and process-start nonce to still agree."""
    try:
        pid = int(lease["pid"])
        expected_pgid = int(lease["pgid"])
        expected_ticks = str(lease["startTimeTicks"])
        actual_pid, actual_pgid, actual_ticks = proc_identity(Path(f"/proc/{pid}/stat"))
    except (
        FileNotFoundError,
        IndexError,
        KeyError,
        PermissionError,
        ProcessLookupError,
        TypeError,
        ValueError,
    ):
        return False
    return (actual_pid, actual_pgid, actual_ticks) == (pid, expected_pgid, expected_ticks)


def process_group_members(pgid: int) -> list[tuple[int, str]]:
    members: list[tuple[int, str]] = []
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            pid, process_pgid, _start_ticks = proc_identity(stat_path)
            if process_pgid != pgid:
                continue
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
            members.append((pid, command))
        except (FileNotFoundError, IndexError, ProcessLookupError, PermissionError, ValueError):
            continue
    return members


def orphan_sweep(job_dir: Path, new_runner_run_id: str, log) -> int:
    """Kill every live group leased by an older runner generation."""
    ports_dir = job_dir / "ports"
    ports_dir.mkdir(parents=True, exist_ok=True)
    swept = 0
    for lease_path in sorted(ports_dir.glob("*.lease")):
        lease = json.loads(lease_path.read_text(encoding="utf-8"))
        if lease.get("runnerRunId") == new_runner_run_id:
            continue
        pgid = int(lease["pgid"])
        members = process_group_members(pgid)
        if members:
            if not lease_process_matches(lease):
                raise RuntimeError(
                    f"refusing orphan sweep for {lease_path}: PID/PGID start-time nonce mismatch"
                )
            commands = [command for _pid, command in members]
            if any(token in command for token in PROTECTED_COMMAND_TOKENS for command in commands):
                raise RuntimeError(f"refusing orphan sweep of protected process group {pgid}: {commands}")
            if not any("mlx_server.py" in command or str(job_dir) in command for command in commands):
                raise RuntimeError(f"lease {lease_path} does not identify an MLX-owned group: {commands}")
            log(f"orphan-sweep SIGTERM pgid={pgid} lease={lease_path.name} members={members}")
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 5
            while process_group_members(pgid) and time.monotonic() < deadline:
                time.sleep(0.1)
            if process_group_members(pgid):
                log(f"orphan-sweep SIGKILL pgid={pgid}")
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 3
                while process_group_members(pgid) and time.monotonic() < deadline:
                    time.sleep(0.1)
            if process_group_members(pgid):
                raise RuntimeError(f"orphan process group survived sweep: {pgid}")
            swept += 1
        lease_path.unlink(missing_ok=True)
    return swept


def assert_ports_free(match_port: int, control_port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind(("127.0.0.1", match_port))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
        tcp.bind(("127.0.0.1", control_port))


def append_manifest(path: Path, document: dict[str, object]) -> None:
    import fcntl

    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(document, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def analysis_margin(analysis: dict[str, object]) -> tuple[str, dict[str, int]]:
    if analysis.get("schema") == "mlx.analysis-skipped.v1":
        consistency = analysis.get("consistency") or {}
        raw_scores = consistency.get("teamScores") or {}
        scores = {str(team): int(score) for team, score in raw_scores.items()}
        return str(int(consistency["margin"])), scores
    match = analysis.get("match") or {}
    teams = match.get("teams") or []
    scores = {str(team.get("name")): int(team.get("frags", 0)) for team in teams}
    if len(scores) != 2:
        return "status", scores
    ordered = sorted(scores.values(), reverse=True)
    return str(ordered[0] - ordered[1]), scores


def build_skipped_analysis(
    attempt_dir: Path, spec: dict[str, object]
) -> dict[str, object]:
    """Validate the owner's cheap consistency contract without parsing the MVD."""
    demo = attempt_dir / "demo.mvd"
    result = json.loads((attempt_dir / "match-result.json").read_text(encoding="utf-8"))
    ktxstats = json.loads((attempt_dir / "demo.txt").read_text(encoding="utf-8"))

    if result.get("schema") != "mlx.match-result.v1" or not result.get("ok"):
        raise ValueError("match-result is not a successful MLX result")
    if not result.get("portsReleased"):
        raise ValueError("match-result does not prove released ports")
    if ktxstats.get("version") != 3 or ktxstats.get("map") != "dm3":
        raise ValueError("KTX stats are not the expected DM3 v3 format")

    actual_bytes = demo.stat().st_size
    actual_sha = hashlib.sha256(demo.read_bytes()).hexdigest()
    if actual_bytes != int(result["demoBytes"]):
        raise ValueError("MVD size disagrees with match-result")
    if actual_sha != result["demoSha256"]:
        raise ValueError("MVD checksum disagrees with match-result")

    teams = ktxstats.get("teams")
    players = ktxstats.get("players")
    if not isinstance(teams, list) or len(teams) != 2:
        raise ValueError("KTX stats do not contain exactly two teams")
    if not isinstance(players, list) or len(players) != 8:
        raise ValueError("KTX stats do not contain exactly eight players")
    team_scores = {
        str(team): sum(
            int((player.get("stats") or {}).get("frags", 0))
            for player in players
            if player.get("team") == team
        )
        for team in teams
    }
    roster = {
        str(team): sum(1 for player in players if player.get("team") == team)
        for team in teams
    }
    if set(roster) != {"mlx", "frogs"} or any(count != 4 for count in roster.values()):
        raise ValueError("KTX stats do not contain four players per team")
    frogs = [player for player in players if player.get("team") == "frogs"]
    if any((player.get("bot") or {}).get("skill") != 20 for player in frogs):
        raise ValueError("KTX stats do not prove frogbot skill 20")
    ordered_scores = sorted(team_scores.values(), reverse=True)
    duration_seconds = int(ktxstats["duration"])
    expected_seconds = int(spec["timelimit"]) * 60
    if int(ktxstats.get("tl", 0)) != int(spec["timelimit"]):
        raise ValueError("KTX timelimit disagrees with the job spec")
    if abs(duration_seconds - expected_seconds) / expected_seconds >= 0.05:
        raise ValueError("KTX duration drift is not below five percent")

    started = datetime.fromisoformat(str(result["matchStartedAt"]).replace("Z", "+00:00"))
    ended = datetime.fromisoformat(str(result["matchEndedAt"]).replace("Z", "+00:00"))
    wall_duration_seconds = (ended - started).total_seconds()
    if abs(wall_duration_seconds - duration_seconds) / duration_seconds >= 0.05:
        raise ValueError("wall/game duration drift is not below five percent")
    return {
        "schema": "mlx.analysis-skipped.v1",
        "status": "skipped-by-owner-directive",
        "matchTag": spec["matchTag"],
        "consistency": {
            "teamScores": team_scores,
            "margin": ordered_scores[0] - ordered_scores[1],
            "durationSeconds": duration_seconds,
            "wallDurationSeconds": wall_duration_seconds,
            "mvdBytes": actual_bytes,
            "mvdSha256": actual_sha,
            "roster": roster,
        },
    }


def validate_analysis(analysis: dict[str, object]) -> None:
    match = analysis.get("match")
    if not isinstance(match, dict):
        raise ValueError("analysis has no match object")
    if not isinstance(match.get("players"), list) or len(match["players"]) != 8:
        raise ValueError("analysis does not contain exactly eight players")
    if not isinstance(match.get("teams"), list) or len(match["teams"]) != 2:
        raise ValueError("analysis does not contain exactly two teams")
    if match.get("duration") is None:
        raise ValueError("analysis match.duration is null")
    damage_by_player = (analysis.get("damage") or {}).get("byPlayer") or {}
    missing_damage = []
    for player in match["players"]:
        damage = damage_by_player.get(player.get("name")) or {}
        done = damage.get("given")
        taken = damage.get("taken")
        if done is None or taken is None:
            missing_damage.append(player.get("name"))
    if missing_damage:
        raise ValueError(f"analysis damage done/taken is null for: {missing_damage}")


def validate_metrics(metrics: dict[str, object]) -> None:
    if metrics.get("schema") != "mlx.metrics.v1":
        raise ValueError("metrics schema is not mlx.metrics.v1")
    for field in (
        "durationMs",
        "openingCensored",
        "openingFirstFragMs",
        "deathsAirborneMethod",
        "fightToImportantItemDefinition",
        "teams",
    ):
        if field not in metrics:
            raise ValueError(f"metrics missing {field}")
    teams = metrics["teams"]
    if not isinstance(teams, dict) or len(teams) != 2:
        raise ValueError("metrics does not contain exactly two teams")
    required_team_fields = (
        "score",
        "fragMargin",
        "frags",
        "deaths",
        "damageGiven",
        "damageTaken",
        "efficiency",
        "armorShare",
        "healthShare",
        "powerupShare",
        "itemTimings",
        "openingDamageGiven",
        "openingDamageTaken",
        "openingWin",
        "deathsAirborne",
        "deathsAirborneEvaluated",
        "fightToImportantItemSamples",
        "fightToImportantItemMedianMs",
        "fightToImportantItemCensored",
    )
    for team_name, team in teams.items():
        if not isinstance(team, dict):
            raise ValueError(f"metrics team {team_name} is not an object")
        for field in required_team_fields:
            if field not in team:
                raise ValueError(f"metrics team {team_name} missing {field}")
        if not metrics["openingCensored"] and any(
            team[field] is None for field in ("openingDamageGiven", "openingDamageTaken", "openingWin")
        ):
            raise ValueError(f"metrics team {team_name} has uncensored null opening fields")
        if not team["fightToImportantItemCensored"] and team["fightToImportantItemMedianMs"] is None:
            raise ValueError(f"metrics team {team_name} has uncensored null fight-to-item median")


def match_timeout_seconds(spec: dict[str, object]) -> int:
    """Return the locked 1.5x match-length watchdog, rejecting drift."""
    timelimit = int(spec["timelimit"])
    if timelimit < 1:
        raise ValueError("timelimit must be at least one minute")
    expected = timelimit * 60 * 3 // 2
    configured = int(spec.get("matchTimeoutSeconds", expected))
    if configured != expected:
        raise ValueError(
            f"matchTimeoutSeconds must equal 1.5 match lengths ({expected}s), got {configured}s"
        )
    return expected


def validate_job_spec(spec: dict[str, object]) -> None:
    count = int(spec["matches"])
    parallel = int(spec["parallel"])
    base_port = int(spec["basePort"])
    if count < 1 or parallel < 1 or parallel > count:
        raise ValueError("job matches/parallel are invalid")
    if base_port < 28600 or base_port + count - 1 > 28700:
        raise ValueError("job match ports exceed 28600-28700")

    mode = spec.get("analysisMode", "full")
    if mode not in {"full", "skipped-by-owner-directive"}:
        raise ValueError(f"unsupported analysisMode: {mode}")
    if mode == "skipped-by-owner-directive":
        if not isinstance(spec.get("matchTag"), str) or not spec["matchTag"]:
            raise ValueError("matchTag is required for skipped analysis")
    else:
        for field in ("analysisCommand", "metricsCommand", "summaryCommand"):
            if not isinstance(spec.get(field), list) or not spec[field]:
                raise ValueError(f"{field} is required")
    match_timeout_seconds(spec)


class JobRunner:
    def __init__(self, spec_path: Path, mode: str) -> None:
        self.spec_path = spec_path.resolve()
        self.spec = json.loads(self.spec_path.read_text(encoding="utf-8"))
        self.mode = mode
        self.job_dir = Path(self.spec["jobDir"]).resolve()
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.runner_run_id = str(uuid.uuid4())
        self.state_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.stop_heartbeat = threading.Event()
        self.status_path = self.job_dir / "status.json"
        self.manifest_path = self.job_dir / "manifest.jsonl"
        self.log_path = self.job_dir / "job.log"
        self.status: dict[str, object] = {}

    def log(self, message: str) -> None:
        line = f"{utc_now()} {message}"
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        print(line, flush=True)

    def heartbeat(self) -> None:
        heartbeat = self.job_dir / "heartbeat"
        while not self.stop_heartbeat.wait(30):
            heartbeat.touch()
        heartbeat.touch()

    def write_status(self) -> None:
        self.status["updatedAt"] = utc_now()
        atomic_json(self.status_path, self.status)

    def initialize(self) -> list[str]:
        validate_job_spec(self.spec)
        count = int(self.spec["matches"])

        config_path = self.job_dir / "config.json"
        if not config_path.exists():
            atomic_json(config_path, self.spec)
        self.log(f"runner-start mode={self.mode} runner_run_id={self.runner_run_id}")
        swept = orphan_sweep(self.job_dir, self.runner_run_id, self.log)
        self.log(f"orphan-sweep-complete count={swept}")

        if self.status_path.exists():
            self.status = json.loads(self.status_path.read_text(encoding="utf-8"))
        else:
            self.status = {
                "schema": "mlx.job-status.v1",
                "jobId": self.spec["jobId"],
                "createdAt": utc_now(),
                "matches": {
                    f"match-{index:04d}": {"state": "planned", "attempts": 0}
                    for index in range(1, count + 1)
                },
            }
        self.status["runnerRunId"] = self.runner_run_id
        self.status["runnerPid"] = os.getpid()
        self.status["state"] = "running"
        selected = select_matches(self.status["matches"], self.mode)
        self.write_status()
        atomic_json(
            self.job_dir / "pid",
            {"pid": os.getpid(), "startTimeTicks": proc_start_ticks(os.getpid()), "runnerRunId": self.runner_run_id},
        )
        (self.job_dir / "runner_run_id").write_text(self.runner_run_id + "\n", encoding="utf-8")
        (self.job_dir / "started_at").write_text(utc_now() + "\n", encoding="utf-8")
        (self.job_dir / "heartbeat").touch()
        return selected

    def update_match(self, key: str, **values: object) -> None:
        with self.state_lock:
            self.status["matches"][key].update(values)
            self.write_status()

    def analyze(self, demo: Path, analysis_path: Path, log_path: Path) -> dict[str, object]:
        argv = [str(value).replace("{demo}", str(demo)) for value in self.spec["analysisCommand"]]
        environment = os.environ.copy()
        environment.update({str(key): str(value) for key, value in self.spec.get("analysisEnv", {}).items()})
        process = subprocess.run(
            argv,
            capture_output=True,
            env=environment,
            timeout=int(self.spec.get("analysisTimeoutSeconds", 300)),
        )
        log_path.write_bytes(process.stderr)
        if process.returncode != 0:
            raise RuntimeError(f"analyzer exited {process.returncode}; see {log_path}")
        analysis_path.write_bytes(process.stdout)
        analysis = json.loads(process.stdout)
        validate_analysis(analysis)
        return analysis

    def derive_metrics(self, analysis_path: Path, metrics_path: Path, log_path: Path) -> dict[str, object]:
        argv = [
            str(value).replace("{analysis}", str(analysis_path))
            for value in self.spec["metricsCommand"]
        ]
        process = subprocess.run(
            argv,
            capture_output=True,
            timeout=int(self.spec.get("metricsTimeoutSeconds", 60)),
        )
        log_path.write_bytes(process.stderr)
        if process.returncode != 0:
            raise RuntimeError(f"metrics command exited {process.returncode}; see {log_path}")
        metrics_path.write_bytes(process.stdout)
        metrics = json.loads(process.stdout)
        validate_metrics(metrics)
        return metrics

    def prepare_analysis(self, attempt_dir: Path) -> dict[str, object]:
        if self.spec.get("analysisMode", "full") == "skipped-by-owner-directive":
            analysis = build_skipped_analysis(attempt_dir, self.spec)
            atomic_json(attempt_dir / "analysis.json", analysis)
            return analysis
        analysis = self.analyze(
            attempt_dir / "demo.mvd",
            attempt_dir / "analysis.json",
            attempt_dir / "analyzer.log",
        )
        self.derive_metrics(
            attempt_dir / "analysis.json",
            attempt_dir / "metrics.json",
            attempt_dir / "metrics.log",
        )
        return analysis

    def summarize(self) -> Path:
        output_dir = Path(self.spec["resultsDir"]).resolve()
        replacements = {"{jobDir}": str(self.job_dir), "{outputDir}": str(output_dir)}
        argv = []
        for value in self.spec["summaryCommand"]:
            argument = str(value)
            for placeholder, replacement in replacements.items():
                argument = argument.replace(placeholder, replacement)
            argv.append(argument)
        process = subprocess.run(
            argv,
            capture_output=True,
            timeout=int(self.spec.get("summaryTimeoutSeconds", 300)),
        )
        (self.job_dir / "summary.log").write_bytes(process.stdout + process.stderr)
        if process.returncode != 0:
            raise RuntimeError(f"summary command exited {process.returncode}; see {self.job_dir / 'summary.log'}")
        for name in ("summary.csv", "summary.parquet"):
            path = output_dir / name
            if not path.is_file() or path.stat().st_size == 0:
                raise FileNotFoundError(f"summary command did not create {path}")
        return output_dir

    def publish(
        self,
        key: str,
        index: int,
        attempt_dir: Path,
        analysis: dict[str, object],
    ) -> tuple[Path, Path]:
        margin, team_scores = analysis_margin(analysis)
        filename = demo_name(self.spec["startDate"], self.spec["cell"], index, margin)
        publication_name = filename.removesuffix(".mvd")
        outbox = Path(self.spec["outboxDir"]).resolve()
        outbox.mkdir(parents=True, exist_ok=True)
        temporary = outbox / f".{publication_name}.{self.runner_run_id}.tmp"
        target = outbox / publication_name
        match_result = json.loads((attempt_dir / "match-result.json").read_text(encoding="utf-8"))
        expected_sha = str(match_result["demoSha256"])

        for existing in sorted(outbox.iterdir()):
            if not existing.is_dir() or not (existing / ".ready").is_file():
                continue
            sidecars = list(existing.glob("*.mvd.json"))
            if len(sidecars) != 1:
                continue
            document = json.loads(sidecars[0].read_text(encoding="utf-8"))
            if document.get("experimentId") != self.spec["jobId"] or document.get("match") != key:
                continue
            if document.get("demoFile") != filename or document.get("sha256") != expected_sha:
                raise RuntimeError(f"ready publication conflicts with rerun of {key}: {existing}")
            return existing, sidecars[0]

        recovery_root = self.job_dir / "incomplete-publications"
        for existing in sorted(outbox.iterdir()):
            if not existing.is_dir() or (existing / ".ready").exists():
                continue
            matches_expected_name = existing.name == publication_name or existing.name.startswith(
                f".{publication_name}."
            )
            matches_sidecar = False
            for sidecar in existing.glob("*.mvd.json"):
                try:
                    document = json.loads(sidecar.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                matches_sidecar = (
                    document.get("experimentId") == self.spec["jobId"]
                    and document.get("match") == key
                )
                if matches_sidecar:
                    break
            if not matches_expected_name and not matches_sidecar:
                continue
            recovery_root.mkdir(parents=True, exist_ok=True)
            recovered = recovery_root / f"{existing.name.lstrip('.')}.{self.runner_run_id}"
            if recovered.exists():
                raise FileExistsError(f"refusing to overwrite recovered publication: {recovered}")
            existing.replace(recovered)

        if target.exists() or temporary.exists():
            raise FileExistsError(f"refusing to overwrite outbox publication: {target}")
        temporary.mkdir(parents=False)
        demo_target = temporary / filename
        shutil.copy2(attempt_dir / "demo.mvd", demo_target)
        shutil.copy2(attempt_dir / "analysis.json", temporary / "analysis.json")
        metrics_path = attempt_dir / "metrics.json"
        if metrics_path.is_file():
            shutil.copy2(metrics_path, temporary / "metrics.json")
        ktxstats_path = attempt_dir / "demo.txt"
        if ktxstats_path.is_file():
            shutil.copy2(ktxstats_path, temporary / "ktxstats.json")
        analysis_reference = (
            "skipped-by-owner-directive"
            if self.spec.get("analysisMode") == "skipped-by-owner-directive"
            else "analysis.json"
        )
        sidecar = {
            "schema": "mlx.demo-sidecar.v1",
            "demoFile": filename,
            "sha256": match_result["demoSha256"],
            "experimentId": self.spec["jobId"],
            "rexCommit": self.spec["rexCommit"],
            "analyzerCommit": self.spec["analyzerCommit"],
            "mlxVersion": self.spec["mlxVersion"],
            "serverConfig": self.spec["serverConfig"],
            "frogbotSkill": 20,
            "botSkill": int(self.spec.get("botSkill", 7)),
            "map": "dm3",
            "matchResult": {"margin": margin, "teams": team_scores},
            "analysis": analysis_reference,
            "benchmarkCell": self.spec["cell"],
            "recordingType": "MVD",
            "transferStatus": "outbox",
            "hubDestination": self.spec["hubDestination"],
            "runnerRunId": self.runner_run_id,
            "match": key,
            "createdAt": utc_now(),
        }
        if metrics_path.is_file():
            sidecar["metrics"] = "metrics.json"
        if ktxstats_path.is_file():
            sidecar["ktxStats"] = "ktxstats.json"
        if self.spec.get("matchTag"):
            sidecar["matchTag"] = self.spec["matchTag"]
        sidecar_path = temporary / f"{filename}.json"
        atomic_json(sidecar_path, sidecar)
        temporary.replace(target)
        (target / ".ready").write_text(utc_now() + "\n", encoding="utf-8")
        return target, target / sidecar_path.name

    def run_one(self, key: str) -> None:
        index = int(key.split("-")[1])
        record = self.status["matches"][key]
        attempt = int(record.get("attempts", 0)) + 1
        match_dir = self.job_dir / key
        attempt_dir = match_dir / f"attempt-{attempt:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=False)
        port = int(self.spec["basePort"]) + index - 1
        control_port = int(self.spec["baseControlPort"]) + index - 1
        assert_ports_free(port, control_port)
        self.update_match(
            key,
            state="running",
            attempts=attempt,
            port=port,
            controlPort=control_port,
            runnerRunId=self.runner_run_id,
            startedAt=utc_now(),
        )
        argv = [
            sys.executable,
            str(Path(__file__).with_name("mlx_server.py")),
            "--session-dir", str(attempt_dir),
            "--run-id", f"{self.spec['jobId']}-{key}-a{attempt}",
            "--serverdir", str(self.spec["serverDir"]),
            "--rtx-client", str(self.spec["rtxClient"]),
            "--qw-min-client", str(self.spec["qwMinClient"]),
            "--port", str(port),
            "--control-port", str(control_port),
            "--timelimit", str(self.spec["timelimit"]),
            "--skill", str(int(self.spec.get("botSkill", 7))),
            "--bhop", "1" if self.spec.get("bhop") else "0",
            "--inherit-process-group",
        ]
        runner_log = (attempt_dir / "match-runner.log").open("wb")
        process: subprocess.Popen[bytes] | None = None
        lease_path = self.job_dir / "ports" / f"{port}.lease"
        try:
            process = subprocess.Popen(
                argv,
                cwd=Path(__file__).resolve().parent.parent,
                stdout=runner_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            lease = {
                "schema": "mlx.port-lease.v1",
                "port": port,
                "controlPort": control_port,
                "pid": process.pid,
                "startTimeTicks": proc_start_ticks(process.pid),
                "pgid": os.getpgid(process.pid),
                "runnerRunId": self.runner_run_id,
                "match": key,
                "createdAt": utc_now(),
            }
            atomic_json(lease_path, lease)
            os.chmod(lease_path, 0o600)
            timeout = match_timeout_seconds(self.spec)
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(int(lease["pgid"]), signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(int(lease["pgid"]), signal.SIGKILL)
                    process.wait(timeout=3)
                raise TimeoutError(f"match exceeded {timeout}s timeout")
            if return_code != 0:
                raise RuntimeError(f"mlx_server exited {return_code}")
            result = json.loads((attempt_dir / "match-result.json").read_text(encoding="utf-8"))
            if not result.get("ok") or not result.get("portsReleased"):
                raise RuntimeError(f"match lifecycle did not complete cleanly: {result}")
            demo = attempt_dir / "demo.mvd"
            if not demo.is_file() or demo.stat().st_size == 0:
                raise FileNotFoundError("match has no non-empty demo.mvd")
            analysis = self.prepare_analysis(attempt_dir)
            publication, sidecar = self.publish(key, index, attempt_dir, analysis)
            (match_dir / ".ready").write_text(utc_now() + "\n", encoding="utf-8")
            append_manifest(
                self.manifest_path,
                {"event": "completed", "match": key, "attempt": attempt, "publication": str(publication), "time": utc_now()},
            )
            self.update_match(
                key,
                state="completed",
                completedAt=utc_now(),
                publication=str(publication),
                sidecar=str(sidecar),
                error=None,
            )
            self.log(f"{key} completed attempt={attempt} publication={publication.name}")
        except Exception as exc:
            append_manifest(
                self.manifest_path,
                {"event": "failed", "match": key, "attempt": attempt, "error": f"{type(exc).__name__}: {exc}", "time": utc_now()},
            )
            self.update_match(key, state="failed", failedAt=utc_now(), error=f"{type(exc).__name__}: {exc}")
            self.log(f"{key} failed attempt={attempt}: {type(exc).__name__}: {exc}")
        finally:
            runner_log.close()
            if process is not None and process.poll() is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            lease_path.unlink(missing_ok=True)

    def run(self) -> int:
        selected = self.initialize()
        heartbeat = threading.Thread(target=self.heartbeat, name="mlx-heartbeat", daemon=True)
        heartbeat.start()
        try:
            with ThreadPoolExecutor(max_workers=int(self.spec["parallel"])) as pool:
                futures = {pool.submit(self.run_one, key): key for key in selected}
                for future in as_completed(futures):
                    future.result()
            counts = {state: 0 for state in ("planned", "running", "completed", "failed")}
            for match in self.status["matches"].values():
                counts[match["state"]] += 1
            self.status["counts"] = counts
            summary_error = None
            if counts["completed"] and self.spec.get("summaryCommand"):
                try:
                    summary_dir = self.summarize()
                    self.status["summaryCsv"] = str(summary_dir / "summary.csv")
                    self.status["summaryParquet"] = str(summary_dir / "summary.parquet")
                except Exception as exc:
                    summary_error = f"{type(exc).__name__}: {exc}"
                    self.log(f"summary failed: {summary_error}")
            self.status["summaryError"] = summary_error
            self.status["state"] = (
                "completed"
                if counts["failed"] == 0 and counts["running"] == 0 and summary_error is None
                else "failed"
            )
            self.status["endedAt"] = utc_now()
            self.write_status()
            exit_code = 0 if self.status["state"] == "completed" else 1
            (self.job_dir / "exitstatus").write_text(str(exit_code) + "\n", encoding="utf-8")
            self.log(f"runner-end state={self.status['state']} counts={counts}")
            return exit_code
        finally:
            self.stop_heartbeat.set()
            heartbeat.join(timeout=2)
            (self.job_dir / "heartbeat").touch()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--mode", choices=("run", "resume", "retry"), default="run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return JobRunner(args.spec, args.mode).run()


if __name__ == "__main__":
    raise SystemExit(main())
