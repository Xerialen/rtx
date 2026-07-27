#!/usr/bin/env python3
"""Portable command-line entry point for the rtx test flow."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from runner import selftest, t0_import, t1, t2, t3
from runner.checks import ValidationError
from runner.runlib import ConfigError, RunAborted, load_config
from runner.scenario import ScenarioError

ROOT = Path(__file__).resolve().parent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or validate rtx live integration-test tiers."
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="runner TOML configuration (default: config.toml)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    t0_parser = commands.add_parser(
        "t0-import", help="import an upstream cargo-test JSON summary"
    )
    t0_parser.add_argument("summary", help="cargo-test summary JSON")

    t1_parser = commands.add_parser(
        "t1", help="run declarative movement drills and informative dash"
    )
    t1_parser.add_argument(
        "--scenarios",
        default=str(ROOT / "scenarios" / "dm3"),
        help="scenario TOML file or directory",
    )
    t1_parser.add_argument(
        "--quick",
        action="store_true",
        help="run three attempts and scale thresholds",
    )

    t2_parser = commands.add_parser(
        "t2", help="measure pacifist free-play navigation"
    )
    t2_parser.add_argument(
        "--secs",
        type=int,
        help="duration override; non-600 runs are marked smoke",
    )
    t2_parser.add_argument("--map", default="dm3", help="map label for evidence")

    commands.add_parser(
        "t3", help="run one branch-vs-reference match on the prepared KTX server"
    )
    commands.add_parser("t4", help="reserved for the frogbot ladder")
    selftest_parser = commands.add_parser(
        "selftest", help="run the offline schema conformance fixtures"
    )
    selftest_parser.add_argument(
        "--fixtures",
        default=str(ROOT / "schema" / "fixtures"),
        help="fixture directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "selftest":
            accepted, rejected = selftest.run(args.fixtures)
            print(
                f"selftest PASS: {accepted} valid fixture(s) accepted; "
                f"{rejected} broken fixture(s) rejected"
            )
            return 0

        config = load_config(args.config)
        if args.command == "t0-import":
            path = t0_import.run(config, args.summary)
            document = json.loads(path.read_text(encoding="utf-8"))
            print(path)
            return 0 if document["payload"]["verdict"] == "PASS" else 1
        if args.command == "t1":
            path = t1.run(config, args.scenarios, quick=args.quick)
            print(path)
            document = json.loads(path.read_text(encoding="utf-8"))
            return 0 if document["payload"]["verdict"] == "PASS" else 1
        if args.command == "t2":
            path = t2.run(config, duration_s=args.secs, map_name=args.map)
            print(path)
            return 0
        if args.command == "t3":
            path = t3.run(config)
            print(path)
            return 0
        if args.command == "t4":
            print("t4: not implemented until E4", file=sys.stderr)
            return 4
    except RunAborted:
        return 130
    except (
        ConfigError,
        ScenarioError,
        ValidationError,
        ValueError,
        RuntimeError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
