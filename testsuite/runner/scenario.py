"""Load and strictly validate declarative rtx live-test scenarios."""
from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any


class ScenarioError(ValueError):
    """A scenario does not conform to rtx-scenario/1."""


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScenarioError(f"{path}: expected table")
    return value


def _fields(
    value: Any,
    path: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> dict[str, Any]:
    obj = _object(value, path)
    missing = sorted(required - obj.keys())
    unknown = sorted(obj.keys() - required - optional)
    if missing:
        raise ScenarioError(f"{path}: missing field(s): {', '.join(missing)}")
    if unknown:
        raise ScenarioError(f"{path}: unknown field(s): {', '.join(unknown)}")
    return obj


def _number(value: Any, path: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioError(f"{path}: expected number")
    if positive and value <= 0:
        raise ScenarioError(f"{path}: expected a positive number")
    return float(value)


def _integer(value: Any, path: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioError(f"{path}: expected integer")
    if positive and value <= 0:
        raise ScenarioError(f"{path}: expected a positive integer")
    return value


def _vec3(value: Any, path: str) -> None:
    if not isinstance(value, list) or len(value) != 3:
        raise ScenarioError(f"{path}: expected three coordinates")
    for index, coordinate in enumerate(value):
        _number(coordinate, f"{path}[{index}]")


# A drill is either one of the map's fixed challenges — the routes and jumps
# a bot has to own to play dm3 at all — or a probe of one navmesh cell.
CATEGORIES = {"grunddrill", "cellprov", "fart"}


def validate_scenario(document: Any, source: str = "<scenario>") -> dict[str, Any]:
    root = _fields(
        document,
        source,
        {
            "schema",
            "name",
            "map",
            "kind",
            "category",
            "place",
            "description",
            "run",
            "threshold",
        },
        {"setup", "fail", "workaround"},
    )
    if root["schema"] != "rtx-scenario/1":
        raise ScenarioError(
            f"{source}.schema: unsupported schema {root['schema']!r}; "
            "expected 'rtx-scenario/1'"
        )
    for field in ("name", "map", "description", "place"):
        if not isinstance(root[field], str) or not root[field]:
            raise ScenarioError(f"{source}.{field}: expected non-empty string")
    if root["kind"] not in {"goto", "dash"}:
        raise ScenarioError(f"{source}.kind: expected 'goto' or 'dash'")
    if root["category"] not in CATEGORIES:
        raise ScenarioError(
            f"{source}.category: expected one of {', '.join(sorted(CATEGORIES))}"
        )
    if (root["kind"] == "dash") != (root["category"] == "fart"):
        raise ScenarioError(
            f"{source}.category: 'fart' is exactly the dash scenarios"
        )

    if "setup" in root:
        setup = _fields(root["setup"], f"{source}.setup", {"plant_links"})
        links = setup["plant_links"]
        if not isinstance(links, list) or not all(
            isinstance(link, str) and link for link in links
        ):
            raise ScenarioError(f"{source}.setup.plant_links: expected strings")
        if root["kind"] != "goto":
            raise ScenarioError(f"{source}.setup: only valid for goto scenarios")

    if root["kind"] == "goto":
        run = _fields(
            root["run"],
            f"{source}.run",
            {
                "start",
                "target",
                "attempts",
                "timeout_s",
                "pause_s",
                "arrive_box",
                "regoto_max",
            },
        )
        _vec3(run["start"], f"{source}.run.start")
        _vec3(run["target"], f"{source}.run.target")
        _integer(run["attempts"], f"{source}.run.attempts", positive=True)
        _number(run["timeout_s"], f"{source}.run.timeout_s", positive=True)
        _number(run["pause_s"], f"{source}.run.pause_s")
        _number(run["arrive_box"], f"{source}.run.arrive_box", positive=True)
        _integer(run["regoto_max"], f"{source}.run.regoto_max")
        threshold = _fields(
            root["threshold"], f"{source}.threshold", {"required"}
        )
        required = _integer(
            threshold["required"], f"{source}.threshold.required", positive=True
        )
        if required > run["attempts"]:
            raise ScenarioError(
                f"{source}.threshold.required: cannot exceed run.attempts"
            )
        if "workaround" in root:
            raise ScenarioError(f"{source}.workaround: only valid for dash scenarios")
    else:
        run = _fields(
            root["run"],
            f"{source}.run",
            {"start", "target", "dashes", "timeout_s"},
        )
        _vec3(run["start"], f"{source}.run.start")
        _vec3(run["target"], f"{source}.run.target")
        _integer(run["dashes"], f"{source}.run.dashes", positive=True)
        _number(run["timeout_s"], f"{source}.run.timeout_s", positive=True)
        threshold = _fields(
            root["threshold"], f"{source}.threshold", {"floor", "informative"}
        )
        _number(threshold["floor"], f"{source}.threshold.floor", positive=True)
        if not isinstance(threshold["informative"], bool):
            raise ScenarioError(
                f"{source}.threshold.informative: expected boolean"
            )
        if "workaround" in root:
            workaround = _fields(
                root["workaround"],
                f"{source}.workaround",
                {"cycle_bot_count"},
            )
            if not isinstance(workaround["cycle_bot_count"], bool):
                raise ScenarioError(
                    f"{source}.workaround.cycle_bot_count: expected boolean"
                )
        if "fail" in root or "setup" in root:
            raise ScenarioError(f"{source}: dash cannot contain fail/setup tables")

    if "fail" in root:
        fail = _fields(
            root["fail"], f"{source}.fail", set(), {"fall_gate", "crossing"}
        )
        if not fail:
            raise ScenarioError(f"{source}.fail: expected at least one failure gate")
        if "fall_gate" in fail:
            gate = _fields(
                fail["fall_gate"],
                f"{source}.fail.fall_gate",
                {"armed_z", "fail_z"},
            )
            _number(gate["armed_z"], f"{source}.fail.fall_gate.armed_z")
            _number(gate["fail_z"], f"{source}.fail.fall_gate.fail_z")
        if "crossing" in fail:
            crossing = _fields(
                fail["crossing"], f"{source}.fail.crossing", {"bowl_y"}
            )
            _number(crossing["bowl_y"], f"{source}.fail.crossing.bowl_y")
    return root


def load_scenario(path: str | Path) -> dict[str, Any]:
    scenario_path = Path(path)
    try:
        with scenario_path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ScenarioError(f"{scenario_path}: cannot read TOML: {exc}") from exc
    return validate_scenario(document, str(scenario_path))


def load_scenarios(path: str | Path) -> list[dict[str, Any]]:
    scenario_path = Path(path)
    files = (
        sorted(scenario_path.glob("*.toml"))
        if scenario_path.is_dir()
        else [scenario_path]
    )
    if not files:
        raise ScenarioError(f"{scenario_path}: no scenario TOML files found")
    scenarios = [load_scenario(file) for file in files]
    names = [scenario["name"] for scenario in scenarios]
    if len(names) != len(set(names)):
        raise ScenarioError(f"{scenario_path}: duplicate scenario name")
    return scenarios
