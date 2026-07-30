"""Offline schema conformance test over versioned fixtures."""
from __future__ import annotations

import json
from pathlib import Path
import tomllib

from .checks import ValidationError, validate_result, validate_scenario_result


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
    if failures:
        raise AssertionError("\n".join(failures))
    return accepted, rejected
