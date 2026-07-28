"""Import an upstream cargo-test summary into a T0 result envelope."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .checks import ValidationError, validate_result
from .runlib import RunRecorder


def _read_summary(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source}: cannot read cargo summary JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{source}: expected a JSON object")
    unknown = sorted(
        document.keys() - {"modules", "total", "quality_floors", "verdict"}
    )
    missing = sorted({"modules", "quality_floors"} - document.keys())
    if missing:
        raise ValueError(f"{source}: missing field(s): {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{source}: unknown field(s): {', '.join(unknown)}")
    modules = document["modules"]
    floors = document["quality_floors"]
    if not isinstance(modules, list) or not isinstance(floors, list):
        raise ValueError(f"{source}: modules and quality_floors must be arrays")
    tests = passed = 0
    modules_ok = True
    for module in modules:
        if not isinstance(module, dict):
            raise ValueError(f"{source}: module entries must be objects")
        if set(module) != {"name", "tests", "passed"}:
            raise ValueError(f"{source}: module fields must be name/tests/passed")
        if (
            not isinstance(module["name"], str)
            or isinstance(module["tests"], bool)
            or not isinstance(module["tests"], int)
            or isinstance(module["passed"], bool)
            or not isinstance(module["passed"], int)
        ):
            raise ValueError(f"{source}: invalid module field type")
        tests += module["tests"]
        passed += module["passed"]
        modules_ok &= module["tests"] == module["passed"]
    floors_ok = all(
        isinstance(floor, dict) and floor.get("passed") is True for floor in floors
    )
    payload = {
        "modules": modules,
        "total": {"tests": tests, "passed": passed},
        "quality_floors": floors,
        "verdict": "PASS" if modules_ok and floors_ok else "FAIL",
    }
    return payload


def run(config: dict[str, Any], source: str | Path) -> Path:
    payload = _read_summary(source)
    with RunRecorder(config, "T0", "n/a", provenance="measured") as recorder:
        recorder.payload = payload
        # Validate while still inside the recorder so invalid input is persisted
        # as a failed run rather than producing a malformed complete file.
        probe = recorder.document()
        probe["ended_utc"] = probe["started_utc"]
        try:
            validate_result(probe, str(source))
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
    return recorder.path
