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
import os
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


#: Rounding step of the envelope's `round(..., 1)`, the third term of the gate.
ROUNDING_STEP_S = 0.05


def lead_is_censored(track: dict[str, Any]) -> bool:
    """Whether this track's first lay interval began at our first look.

    Read out of the post rather than assumed: `Track.initially_available()`
    seeds exactly this transition today, but a gate that hard-codes "the first
    one" would quietly become wrong if that constructor ever changed.
    """
    transitions = track.get("transitions") or []
    return bool(transitions) and transitions[0].get("at_s") == 0.0 and (
        transitions[0].get("available") is True
    )


def uncensored_intervals(track: dict[str, Any]) -> list[float]:
    """The lay intervals this instrument actually measured end to end.

    An interval we joined in the middle of is not a measurement of it. The
    runner drops the same one, on the same condition — censoring is a property
    of the measurement, not of an instrument, and dropping it on one side only
    makes the two means incomparable (`facit-t2-likhetsgrind-v1.md` §4.2).
    """
    intervals = list(track.get("lay_intervals_s") or [])
    if intervals and lead_is_censored(track):
        intervals = intervals[1:]
    return intervals


def recomputed_reference(track: dict[str, Any]) -> tuple[float | None, int]:
    """The observer's mean on the runner's basis, and how many it rests on.

    The observer's own post is never rewritten — `lay_intervals_s`, `lay_avg_s`
    and `transitions` stay raw. A gate that edits its own evidence is not a
    gate; this recomputes at comparison time out of data already recorded.
    """
    intervals = uncensored_intervals(track)
    if not intervals:
        return None, 0
    return round(sum(intervals) / len(intervals), 1), len(intervals)


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

    # The equality gate's own arithmetic. A track that began available has a
    # left-censored lead interval; both instruments drop it, so the reference
    # the envelope is compared against is not the raw mean.
    assert lead_is_censored(result) is True
    assert uncensored_intervals(result) == [1.25]
    assert recomputed_reference(result) == (1.2, 1)

    # 18/8's own numbers, the case that motivated all of this: dropping the
    # lead on one side only would have compared 0.25 against 4.5.
    night_pent = {
        "lay_intervals_s": [8.674, 0.25],
        "transitions": [{"at_s": 0.0, "available": True}],
    }
    assert recomputed_reference(night_pent) == (0.2, 1)
    night_quad = {
        "lay_intervals_s": [13.329, 5.266, 8.205],
        "transitions": [{"at_s": 0.0, "available": True}],
    }
    assert recomputed_reference(night_quad) == (6.7, 2)

    # A track whose first interval began at an observed edge is not censored,
    # so nothing is dropped — the rule is a condition, not a position.
    seen_edge = {
        "lay_intervals_s": [4.0, 6.0],
        "transitions": [{"at_s": 1.0, "available": False}, {"at_s": 2.0, "available": True}],
    }
    assert lead_is_censored(seen_edge) is False
    assert recomputed_reference(seen_edge) == (5.0, 2)

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

        # The envelope declares both instruments' poll periods so the gate can
        # read its own resolution instead of assuming one. The child cannot
        # know ours, so we hand it over.
        child_env = dict(os.environ, RTX_WATCH_POLL_S=str(args.interval))
        child = subprocess.Popen(args.command, env=child_env)
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
            "notes": [],
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
                        t_runner = stats.get("items_poll_s")
                        tolerance = (
                            None
                            if not isinstance(t_runner, (int, float))
                            else t_runner + args.interval + ROUNDING_STEP_S
                        )
                        comparison["tolerance_s"] = tolerance
                        comparison["items_poll_s"] = t_runner
                        comparison["watch_poll_s"] = args.interval
                        comparison["reference"] = {}
                        if tolerance is None:
                            comparison["mismatches"].append(
                                "envelope has no items_poll_s: the gate cannot read"
                                " its own resolution"
                            )
                        for kind in ("quad", "pent"):
                            track = powerup_results[kind]
                            ref_avg, ref_n = recomputed_reference(track)
                            comparison["reference"][kind] = {
                                "lay_avg_s": ref_avg,
                                "lay_n": ref_n,
                                "lay_censored": len(track.get("lay_intervals_s") or [])
                                - ref_n,
                            }
                            # Takes are observed edges, not estimates: they match
                            # exactly or the two instruments saw different worlds.
                            takes_key = f"{kind}_takes"
                            if stats.get(takes_key) != track["take_count"]:
                                comparison["mismatches"].append(
                                    f"{takes_key}: envelope={stats.get(takes_key)!r},"
                                    f" watch={track['take_count']!r}"
                                )
                            env_n = stats.get(f"{kind}_lay_n")
                            env_avg = stats.get(f"{kind}_lay_avg")
                            if not isinstance(env_n, int):
                                comparison["mismatches"].append(
                                    f"{kind}_lay_n missing from envelope"
                                )
                                continue
                            # A mean of one observation is not a mean. Say so
                            # rather than comparing something neither side has.
                            if env_n < 2 or ref_n < 2:
                                comparison["notes"].append(
                                    f"{kind}_lay_avg not compared: n<2"
                                    f" (envelope {env_n}, reference {ref_n})"
                                )
                                continue
                            if tolerance is None:
                                continue
                            if not isinstance(env_avg, (int, float)) or ref_avg is None:
                                comparison["mismatches"].append(
                                    f"{kind}_lay_avg: envelope={env_avg!r},"
                                    f" reference={ref_avg!r}"
                                )
                            elif abs(env_avg - ref_avg) > tolerance + 1e-9:
                                comparison["mismatches"].append(
                                    f"{kind}_lay_avg: envelope={env_avg!r},"
                                    f" reference={ref_avg!r},"
                                    f" tolerance={round(tolerance, 3)}"
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
