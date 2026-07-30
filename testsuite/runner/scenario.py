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
        {"setup", "fail", "workaround", "requires", "route"},
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

    # Some routes only exist in a navmesh the build has to have been given. A
    # drill anchored on one of those is not measuring the bot when the build
    # lacks it — it is measuring the absence, and reporting that as a failure
    # to walk the route says something untrue about the bot. So the drill names
    # what it needs and how to tell whether the build has it, and a run without
    # it is withheld instead of graded. Naming the capability explicitly rather
    # than deriving it from a route that turns out to have no links keeps the
    # two apart: a missing capability and a bot that cannot use one it has are
    # different findings, and only the first is the harness's fault.
    if "requires" in root:
        if root["kind"] != "goto":
            raise ScenarioError(f"{source}.requires: only valid for goto scenarios")
        requires = _fields(
            root["requires"],
            f"{source}.requires",
            {"capability", "engine_cvar", "note"},
        )
        for field in ("capability", "engine_cvar", "note"):
            value = requires[field]
            if not isinstance(value, str) or not value.strip():
                raise ScenarioError(
                    f"{source}.requires.{field}: expected non-empty string"
                )

    # An arrival says where the bot ended up and nothing about how it got
    # there: `spawn_lift_to_pent_to_pentmega` passed 5/5 by stepping off the
    # pent ledge and freefalling most of the descent, and the endpoint was
    # right while the run was worthless. `via` is the fix — ordered waypoints
    # anchored on points the owner actually occupied, never derived from the
    # navmesh or from geometry, because a box built from the route it is meant
    # to gate would gate nothing. It does not replace `fail.fall_gate` or
    # `fail.crossing`: those end an attempt early with an honest name of their
    # own, `via` decides whether an arrival counts.
    if "route" in root:
        if root["kind"] != "goto":
            raise ScenarioError(f"{source}.route: only valid for goto scenarios")
        route = _fields(root["route"], f"{source}.route", {"via"})
        via = route["via"]
        if not isinstance(via, list) or not via:
            raise ScenarioError(f"{source}.route.via: expected a non-empty array")
        for index, waypoint in enumerate(via):
            waypoint_path = f"{source}.route.via[{index}]"
            fields = _fields(waypoint, waypoint_path, {"at", "box", "name"})
            _vec3(fields["at"], f"{waypoint_path}.at")
            _number(fields["box"], f"{waypoint_path}.box", positive=True)
            if not isinstance(fields["name"], str) or not fields["name"]:
                raise ScenarioError(
                    f"{waypoint_path}.name: expected non-empty string"
                )

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
            {
                "no_progress_s",
                "speed_ceiling",
                "give_up_grace_s",
                "arrive_z",
                "prep_health",
                "prep_rockets",
                "quick_attempts",
            },
        )
        _vec3(run["start"], f"{source}.run.start")
        _vec3(run["target"], f"{source}.run.target")
        attempts = _integer(
            run["attempts"], f"{source}.run.attempts", positive=True
        )
        if "quick_attempts" in run:
            quick_attempts = _integer(
                run["quick_attempts"],
                f"{source}.run.quick_attempts",
                positive=True,
            )
            # Quick is a cut, never a raise: a drill asking quick to run MORE
            # attempts than its full regime would make the two regimes
            # incomparable in the direction nobody intends.
            if quick_attempts > attempts:
                raise ScenarioError(
                    f"{source}.run.quick_attempts: cannot exceed run.attempts"
                )
        _number(run["timeout_s"], f"{source}.run.timeout_s", positive=True)
        _number(run["pause_s"], f"{source}.run.pause_s")
        _number(run["arrive_box"], f"{source}.run.arrive_box", positive=True)
        _integer(run["regoto_max"], f"{source}.run.regoto_max")
        # `arrive_box` bounds the square; `arrive_z` bounds the height inside
        # it. Without one the box is a shaft: dm3 puts walkable ground 344
        # units under the RA targets and inside their own square, so a drill
        # could be credited from the wrong floor. Zero restores that on
        # purpose, for a drill that asks about a place rather than a floor.
        if "arrive_z" in run:
            _number(run["arrive_z"], f"{source}.run.arrive_z")
            if run["arrive_z"] < 0:
                raise ScenarioError(f"{source}.run.arrive_z: cannot be negative")
        # The loadout the attempt starts from. It is stated rather than
        # inherited because what the bot carries decides which routes the
        # planner will even consider — and `prep_rockets` doubles as the
        # permission: a drill that hands out no rockets is a drill where the
        # rocket jump is not allowed, and taking one anyway voids the attempt.
        for key in ("prep_health", "prep_rockets"):
            if key in run:
                _number(run[key], f"{source}.run.{key}")
                if run[key] < 0:
                    raise ScenarioError(f"{source}.run.{key}: cannot be negative")
        # An attempt ends when it can no longer succeed rather than when its
        # clock runs out. `speed_ceiling` must be above any speed the map has
        # produced, or the impossibility bound would cut attempts that were
        # still within reach; `give_up_grace_s` is how many seconds past the
        # time limit the bot is still allowed to aim for. Zeroes disable the
        # two tests.
        if "no_progress_s" in run:
            _number(run["no_progress_s"], f"{source}.run.no_progress_s")
        if "no_progress_s" in run and run["no_progress_s"] < 0:
            raise ScenarioError(f"{source}.run.no_progress_s: cannot be negative")
        if "speed_ceiling" in run:
            ceiling = _number(run["speed_ceiling"], f"{source}.run.speed_ceiling")
            # The bound is only sound while the ceiling is above anything the
            # engine can do; a low one would call reachable targets impossible.
            if 0 < ceiling < 500:
                raise ScenarioError(
                    f"{source}.run.speed_ceiling: must exceed any speed the map "
                    "produces (or be 0 to disable the bound)"
                )
        if "give_up_grace_s" in run:
            grace = _number(run["give_up_grace_s"], f"{source}.run.give_up_grace_s")
            if grace < 0:
                raise ScenarioError(
                    f"{source}.run.give_up_grace_s: cannot be negative — a "
                    "deadline before the limit would fail arrivals that passed"
                )
        threshold = _fields(
            root["threshold"],
            f"{source}.threshold",
            {"required"},
            {"max_time_s", "reference_time_s"},
        )
        required = _integer(
            threshold["required"], f"{source}.threshold.required", positive=True
        )
        # A timed drill is measured against a human run of the same route: the
        # reference is what the owner did, max_time_s the slowest still
        # acceptable. Arriving later than that is not a pass.
        for field in ("max_time_s", "reference_time_s"):
            if field in threshold:
                _number(threshold[field], f"{source}.threshold.{field}", positive=True)
        if ("max_time_s" in threshold) != ("reference_time_s" in threshold):
            raise ScenarioError(
                f"{source}.threshold: max_time_s and reference_time_s belong together"
            )
        if (
            "max_time_s" in threshold
            and threshold["max_time_s"] < threshold["reference_time_s"]
        ):
            raise ScenarioError(
                f"{source}.threshold.max_time_s: cannot be faster than the reference"
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
