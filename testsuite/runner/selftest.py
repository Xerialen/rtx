"""Offline schema conformance test over versioned fixtures."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import tomllib

from .checks import ValidationError, validate_result, validate_scenario_result


def _t2_units() -> list[str]:
    """Unit checks for the T2 gate's own arithmetic.

    The fixtures prove the schema; these prove the two rules the schema cannot
    see — that a lay interval joined in the middle is counted as a take but not
    as a measurement, and that the analyzer cannot quietly zero a live one.
    """
    from . import t2

    failures: list[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    def observe(seq: list[bool]) -> dict[str, Any]:
        state = {
            "takes": [],
            "available_since": None,
            "seen": False,
            "censored_open": False,
            "censored": 0,
        }
        powerups = {"quad": state}
        for index, available in enumerate(seq):
            t2._observe_powerups(
                [{"name": "item_artifact_super_damage", "available": available}],
                float(index),
                powerups,
            )
        return t2._lay_summary(state, "quad")

    # Available from the first look: the take counts, the interval does not.
    lead = observe([True, True, False])
    check("censored.quad_takes", lead["quad_takes"], 1)
    check("censored.quad_lay_n", lead["quad_lay_n"], 0)
    check("censored.quad_lay_censored", lead["quad_lay_censored"], 1)
    check("censored.quad_lay_avg", lead["quad_lay_avg"], None)

    # Unavailable first, then an observed edge: nothing is censored.
    edge = observe([False, True, True, False])
    check("edge.quad_takes", edge["quad_takes"], 1)
    check("edge.quad_lay_n", edge["quad_lay_n"], 1)
    check("edge.quad_lay_censored", edge["quad_lay_censored"], 0)
    check("edge.quad_lay_avg", edge["quad_lay_avg"], 2.0)

    # Both together: takes is the sum of the parts, the mean is over the
    # uncensored one only.
    both = observe([True, False, True, True, False])
    check("both.quad_takes", both["quad_takes"], 2)
    check("both.quad_lay_n", both["quad_lay_n"], 1)
    check("both.quad_lay_censored", both["quad_lay_censored"], 1)
    check("both.quad_lay_avg", both["quad_lay_avg"], 2.0)

    # The analyzer merge. This is the rule six retracted envelopes were
    # retracted for, so it gets its three cases spelled out.
    class M:
        def __init__(self, value: Any, source: str = "qw-analyze/items") -> None:
            self.value, self.source, self.moments = value, source, []

    def merge(live: dict[str, Any], answers: dict[str, Any]) -> tuple[dict, dict, list]:
        stats, sources = dict(live), {}
        bad = t2._merge_analyzer(stats, sources, answers)
        return stats, sources, bad

    # 1. Analyzer zeroes a live take count: disagreement, nobody wins quietly.
    stats, _sources, bad = merge(
        {"quad_takes": 3, "quad_lay_avg": 8.5},
        {"quad_takes": M(0), "quad_lay_avg": M(None)},
    )
    check("a2.disagreement", bool(bad), True)
    check("a2.live_takes_kept", stats["quad_takes"], 3)

    # 2. Analyzer has no answer: live stands, and the source says so.
    stats, sources, bad = merge(
        {"quad_takes": 3, "quad_lay_avg": 8.5}, {"quad_lay_avg": M(None)}
    )
    check("a1.no_disagreement", bad, [])
    check("a1.live_kept", stats["quad_lay_avg"], 8.5)
    check("a1.source_marked", sources["quad_lay_avg"].startswith("runner/live"), True)

    # 3. Analyzer answers: it owns the value, as before.
    stats, sources, bad = merge({"quad_lay_avg": 8.5}, {"quad_lay_avg": M(9.1)})
    check("a3.analyzer_wins", stats["quad_lay_avg"], 9.1)
    check("a3.source", sources["quad_lay_avg"], "qw-analyze/items")

    # 4. Both saw nothing: a legitimate null, not a disagreement.
    stats, _sources, bad = merge(
        {"quad_takes": 0, "quad_lay_avg": None},
        {"quad_takes": M(0), "quad_lay_avg": M(None)},
    )
    check("a4.no_disagreement", bad, [])
    check("a4.takes", stats["quad_takes"], 0)

    return failures


def run(fixtures: str | Path) -> tuple[int, int]:
    root = Path(fixtures)
    accepted = rejected = 0
    failures: list[str] = []
    for path in sorted((root / "valid").iterdir()):
        try:
            if path.suffix == ".json":
                validate_result(
                    json.loads(path.read_text(encoding="utf-8")), str(path)
                )
            elif path.suffix == ".toml":
                with path.open("rb") as stream:
                    validate_scenario_result(tomllib.load(stream), str(path))
            else:
                continue
            accepted += 1
        except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
            failures.append(f"valid fixture rejected: {path}: {exc}")
    # Every broken fixture promises a reason in its name, and rejection for
    # any other reason is a silent lie: an edit to the validator could trip an
    # earlier, unrelated check and this test would still count the fixture
    # rejected. The expected fragment pins each one to its promise.
    expected = json.loads(
        (root / "broken" / "expected.json").read_text(encoding="utf-8")
    )
    for path in sorted((root / "broken").iterdir()):
        if path.name == "expected.json":
            continue
        try:
            if path.suffix == ".json":
                validate_result(
                    json.loads(path.read_text(encoding="utf-8")), str(path)
                )
            elif path.suffix == ".toml":
                with path.open("rb") as stream:
                    validate_scenario_result(tomllib.load(stream), str(path))
            else:
                continue
        except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
            fragment = expected.get(path.name)
            if fragment is None:
                failures.append(f"broken fixture not pinned in expected.json: {path.name}")
            elif fragment not in str(exc):
                failures.append(
                    f"broken fixture rejected for the wrong reason: {path.name}:"
                    f" wanted {fragment!r} in {exc}"
                )
            else:
                rejected += 1
        else:
            failures.append(f"broken fixture accepted: {path}")
    stale = set(expected) - {p.name for p in (root / "broken").iterdir()}
    if stale:
        failures.append(f"expected.json names missing fixtures: {sorted(stale)}")
    failures.extend(_t2_units())
    if failures:
        raise AssertionError("\n".join(failures))
    return accepted, rejected
