#!/usr/bin/env python3
"""Watch quad/pent availability while running a T2 command.

The T2 runner starts bots before its collection loop and may later let a demo
analyzer replace live item counters. This independent control connection starts
before the child command, requires a bot-free/ready rig with both powerups
available, and records every availability transition at a fixed polling rate.

Example:
    python3 tools/powerup_watch.py --config config.toml \
      --output evidence/powerups.json --require-take -- \
      python3 testflow.py --config config.toml t2
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import tomllib
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runner.control import Control  # noqa: E402


POWERUPS = {
    "quad": "item_artifact_super_damage",
    "pent": "item_artifact_invulnerability",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def number(value: float) -> float:
    return round(value, 3)


@dataclass
class Track:
    available: bool
    available_since_s: float | None
    transitions: list[dict[str, Any]] = field(default_factory=list)
    lay_intervals_s: list[float] = field(default_factory=list)

    @classmethod
    def initially_available(cls) -> "Track":
        return cls(
            available=True,
            available_since_s=0.0,
            transitions=[{"at_s": 0.0, "available": True}],
        )

    def observe(self, available: bool, at_s: float) -> None:
        if available == self.available:
            return
        self.transitions.append({"at_s": number(at_s), "available": available})
        if available:
            self.available_since_s = at_s
        elif self.available_since_s is not None:
            self.lay_intervals_s.append(number(at_s - self.available_since_s))
            self.available_since_s = None
        self.available = available

    def result(self, ended_s: float) -> dict[str, Any]:
        average = (
            None
            if not self.lay_intervals_s
            else number(sum(self.lay_intervals_s) / len(self.lay_intervals_s))
        )
        return {
            "take_count": len(self.lay_intervals_s),
            "lay_intervals_s": self.lay_intervals_s,
            "lay_avg_s": average,
            "available_at_end": self.available,
            "open_lay_s_at_end": (
                None
                if self.available_since_s is None
                else number(ended_s - self.available_since_s)
            ),
            "transitions": self.transitions,
        }


def load_config(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    config = tomllib.loads(raw.decode("utf-8"))
    if config.get("schema") != "rtx-testflow-config/1":
        raise ValueError(f"{path}: expected schema rtx-testflow-config/1")
    return config, raw


def item_availability(control: Control) -> dict[str, bool]:
    items = control.request("items", timeout=4.0)["data"]
    if not isinstance(items, list):
        raise RuntimeError("Items control verb did not return a list")
    output: dict[str, bool] = {}
    for name, classname in POWERUPS.items():
        matches = [
            item
            for item in items
            if isinstance(item, dict) and item.get("classname") == classname
        ]
        if len(matches) != 1 or not isinstance(matches[0].get("available"), bool):
            raise RuntimeError(
                f"expected exactly one {name} ({classname}) with boolean availability"
            )
        output[name] = matches[0]["available"]
    return output


def write_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def config_relative_path(config_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else config_path.parent / path


def t2_evidence_files(config: dict[str, Any], config_path: Path) -> set[Path]:
    value = config.get("paths", {}).get("evidence_dir", "evidence")
    if not isinstance(value, str) or not value:
        return set()
    directory = config_relative_path(config_path, value)
    return {path.resolve() for path in directory.glob("t2-*.json")}


def git_commit(config: dict[str, Any], config_path: Path) -> str | None:
    repo = config.get("build", {}).get("repo_dir")
    if not isinstance(repo, str) or not repo:
        return None
    repo_path = config_relative_path(config_path, repo)
    try:
        return subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def selftest() -> None:
    track = Track.initially_available()
    track.observe(False, 2.5)
    track.observe(False, 3.0)
    track.observe(True, 10.0)
    track.observe(False, 11.25)
    result = track.result(12.0)
    assert result["take_count"] == 2
    assert result["lay_intervals_s"] == [2.5, 1.25]
    assert result["lay_avg_s"] == 1.875
    assert result["open_lay_s_at_end"] is None

    censored = Track.initially_available().result(7.0)
    assert censored["take_count"] == 0
    assert censored["lay_avg_s"] is None
    assert censored["open_lay_s_at_end"] == 7.0
    print("powerup-watch selftest: PASS")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="rtx testflow config")
    parser.add_argument("--output", type=Path, help="sidecar JSON output")
    parser.add_argument(
        "--interval",
        type=float,
        default=0.05,
        help="successful-poll target interval in seconds (default: 0.05)",
    )
    parser.add_argument(
        "--require-take",
        action="store_true",
        help="exit nonzero unless both quad and pent have a completed lay interval",
    )
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.selftest:
        return args
    if args.config is None or args.output is None or not args.command:
        parser.error("--config, --output, and a command after -- are required")
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command after -- is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.selftest:
        selftest()
        return 0

    config_path = args.config.resolve()
    config, config_raw = load_config(config_path)
    evidence_before = t2_evidence_files(config, config_path)
    server = config.get("server", {})
    host = str(server.get("host", "127.0.0.1"))
    port = int(server["control_port"])
    protocol = str(server.get("protocol", "auto"))
    control = Control(host, port, timeout=5.0, protocol=protocol)
    child: subprocess.Popen[Any] | None = None
    began = time.monotonic()
    began_utc = utc_now()
    tracks = {name: Track.initially_available() for name in POWERUPS}
    samples = 0
    errors = 0
    last_success = began
    max_gap_s = 0.0
    interrupted = False
    failure: str | None = None

    try:
        status = control.request("status", timeout=5.0)["data"]
        bots = status.get("bots", []) if isinstance(status, dict) else []
        if bots:
            raise RuntimeError(
                f"T2 preflight requires an empty bot roster; found {len(bots)} bot(s)"
            )
        if status.get("navmesh") != "ready":
            raise RuntimeError(
                f"T2 preflight requires navmesh ready; got {status.get('navmesh')!r}"
            )
        initial = item_availability(control)
        unavailable = [name for name, available in initial.items() if not available]
        if unavailable:
            raise RuntimeError(
                "T2 preflight requires initially available powerups; unavailable: "
                + ", ".join(unavailable)
            )

        child = subprocess.Popen(args.command)
        while child.poll() is None:
            try:
                current = item_availability(control)
                now = time.monotonic()
                elapsed = now - began
                gap = now - last_success
                max_gap_s = max(max_gap_s, gap)
                last_success = now
                samples += 1
                for name, available in current.items():
                    tracks[name].observe(available, elapsed)
            except Exception:
                errors += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        interrupted = True
        if child is not None and child.poll() is None:
            child.terminate()
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        if child is not None and child.poll() is None:
            child.terminate()
    finally:
        ended_s = time.monotonic() - began
        child_rc = None if child is None else child.wait()
        powerup_results = {
            name: track.result(ended_s) for name, track in tracks.items()
        }
        evidence_after = t2_evidence_files(config, config_path)
        new_evidence = sorted(evidence_after - evidence_before)
        comparison: dict[str, Any] = {
            "checked": False,
            "evidence": None,
            "mismatches": [],
        }
        if failure is None and child_rc == 0:
            if len(new_evidence) != 1:
                comparison["mismatches"].append(
                    f"expected one new T2 envelope, found {len(new_evidence)}"
                )
            else:
                evidence_path = new_evidence[0]
                comparison["evidence"] = str(evidence_path)
                try:
                    envelope = json.loads(evidence_path.read_text())
                    if envelope.get("status") != "complete":
                        comparison["mismatches"].append(
                            f"new T2 envelope status is {envelope.get('status')!r}"
                        )
                    else:
                        stats = envelope["payload"]["stats"]
                        expected = {
                            "quad_takes": powerup_results["quad"]["take_count"],
                            "quad_lay_avg": (
                                None
                                if powerup_results["quad"]["lay_avg_s"] is None
                                else round(powerup_results["quad"]["lay_avg_s"], 1)
                            ),
                            "pent_takes": powerup_results["pent"]["take_count"],
                            "pent_lay_avg": (
                                None
                                if powerup_results["pent"]["lay_avg_s"] is None
                                else round(powerup_results["pent"]["lay_avg_s"], 1)
                            ),
                        }
                        for key, truth in expected.items():
                            if stats.get(key) != truth:
                                comparison["mismatches"].append(
                                    f"{key}: envelope={stats.get(key)!r}, watch={truth!r}"
                                )
                        comparison["checked"] = True
                except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
                    comparison["mismatches"].append(f"cannot compare envelope: {exc}")
        document = {
            "schema": "rtx-powerup-watch/1",
            "build_commit": git_commit(config, config_path),
            "config_digest": "sha256:" + hashlib.sha256(config_raw).hexdigest(),
            "started_utc": began_utc,
            "ended_utc": utc_now(),
            "duration_s": number(ended_s),
            "poll_interval_s": args.interval,
            "successful_polls": samples,
            "poll_errors": errors,
            "max_success_gap_s": number(max_gap_s),
            "child_command": args.command,
            "child_exit": child_rc,
            "interrupted": interrupted,
            "error": failure,
            "powerups": powerup_results,
            "envelope_comparison": comparison,
        }
        write_atomic(args.output.resolve(), document)
        control.close()

    print(f"powerup watch: {args.output}")
    for name, result in document["powerups"].items():
        print(
            f"  {name}: {result['take_count']} take(s), "
            f"lay={result['lay_intervals_s']}, avg={result['lay_avg_s']}, "
            f"open={result['open_lay_s_at_end']}"
        )
    if interrupted:
        return 130
    if failure is not None:
        print(f"powerup watch: {failure}", file=sys.stderr)
        return 2
    if child_rc:
        return int(child_rc)
    if comparison["mismatches"]:
        print("powerup watch: envelope mismatch", file=sys.stderr)
        for mismatch in comparison["mismatches"]:
            print(f"  {mismatch}", file=sys.stderr)
        return 3
    if args.require_take and any(
        result["take_count"] == 0 for result in document["powerups"].values()
    ):
        print("powerup watch: required completed take missing", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
