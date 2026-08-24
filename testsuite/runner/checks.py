"""Hand-written validators for rtx-testflow/1 and rtx-scenario/1."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import t4_dom
from .scenario import ScenarioError, validate_scenario


class ValidationError(ValueError):
    """A result document violates the versioned contract."""


def _fail(path: str, message: str) -> None:
    raise ValidationError(f"{path}: {message}")


def _dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "expected object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "expected array")
    return value


def _fields(
    value: Any,
    path: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> dict[str, Any]:
    obj = _dict(value, path)
    missing = sorted(required - obj.keys())
    unknown = sorted(obj.keys() - required - optional)
    if missing:
        _fail(path, f"missing field(s): {', '.join(missing)}")
    if unknown:
        _fail(path, f"unknown field(s): {', '.join(unknown)}")
    return obj


def _str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        _fail(path, "expected string")
    return value


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(path, "expected boolean")
    return value


def _int(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(path, f"expected integer >= {minimum}")
    return value


def _num(value: Any, path: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        _fail(path, f"expected number >= {minimum}")
    return value


def _num_or_null(value: Any, path: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (int, float))
    ):
        _fail(path, "expected number or null")


def _evidence(value: Any, path: str) -> None:
    """A demo link that opens the moment a number is about.

    Null is legitimate: a rig without a readable demo directory still
    measures, it just cannot show its work.
    """
    if value is None:
        return
    item = _fields(
        value,
        path,
        {"demo", "at_s", "link"},
        {"attempt", "status", "dash", "metric", "who", "detail"},
    )
    _str(item["demo"], f"{path}.demo")
    _num_or_null(item["at_s"], f"{path}.at_s")
    link = _str(item["link"], f"{path}.link")
    if not link.startswith("/"):
        _fail(f"{path}.link", "expected a host-relative demo-player link")
    # The whole point of the link is the three seconds of run-up, so the lead
    # is part of the contract rather than a detail of how the URL was built.
    start = parse_qs(urlparse(link).query).get("from")
    if item["at_s"] is not None and not start:
        # Without `from` the player opens at zero, so a link that carries a
        # moment but no start is not evidence of that moment at all.
        _fail(f"{path}.link", "carries a moment but no 'from' to open at")
    if start and item["at_s"] is not None:
        try:
            lead = float(item["at_s"]) - float(start[0])
        except ValueError:
            _fail(f"{path}.link", "'from' must be a number of seconds")
        else:
            # `from` is the floor of at_s minus three, while the recorded at_s
            # is rounded to a tenth, so the two disagree by up to that tenth at
            # either end of the window.
            if float(start[0]) > 0 and not 2.95 <= lead <= 4.05:
                _fail(
                    f"{path}.link",
                    f"must open three seconds before the moment, opens {lead:.2f} s before",
                )


# The hub game page's own columns. Team rows and player rows carry the same
# line, because the hub renders them through the same row.
SCORE_LINE = (
    "frags", "efficiency", "kills", "spawn_frags", "deaths", "suicides", "tk",
    "dmg_given", "dmg_taken", "dmg_enemy_weapons", "taken_to_die",
    "ga", "ya", "ra", "mh", "quad", "pent", "ring",
    "sg_acc", "lg_acc", "rl_direct",
    "lg_taken", "lg_kills", "lg_dropped", "rl_taken", "rl_kills", "rl_dropped",
)
SCORE_EXTRAS = ("ping", "top_color", "bottom_color", "speed_max", "speed_avg", "spree_max")


def _percentages(line: dict[str, Any], path: str) -> None:
    """Efficiency and the two accuracies are shares; a team row is no exception."""
    for field in ("efficiency", "sg_acc", "lg_acc"):
        share = line.get(field)
        if share is not None and not 0 <= share <= 100:
            _fail(f"{path}.{field}", "a percentage outside 0-100")


def _scoreboard(value: Any, path: str) -> None:
    """The match card: the match as KTX's own scoreboard saw it.

    Null when no analyzer or no KTX block was available for that match.
    """
    if value is None:
        return
    card = _fields(
        value,
        path,
        {"teams", "players", "source"},
        {"map", "duration_s", "demo", "link", "mode", "hostname", "date"},
    )
    _str(card["source"], f"{path}.source")
    for index, team in enumerate(_list(card["teams"], f"{path}.teams")):
        team_path = f"{path}.teams[{index}]"
        item = _fields(team, team_path, {"name", "frags"}, set(SCORE_LINE) | set(SCORE_EXTRAS))
        _str(item["name"], f"{team_path}.name")
        for field in SCORE_LINE:
            _num_or_null(item.get(field), f"{team_path}.{field}")
        _percentages(item, team_path)
    for index, player in enumerate(_list(card["players"], f"{path}.players")):
        player_path = f"{path}.players[{index}]"
        item = _fields(
            player,
            player_path,
            {"name", "team", "frags"},
            set(SCORE_LINE) | set(SCORE_EXTRAS) | {"link"},
        )
        _str(item["name"], f"{player_path}.name")
        _str(item["team"], f"{player_path}.team")
        for field in SCORE_LINE + SCORE_EXTRAS:
            _num_or_null(item.get(field), f"{player_path}.{field}")
        _percentages(item, player_path)
        if item.get("link") is not None:
            link = _str(item["link"], f"{player_path}.link")
            if not link.startswith("/"):
                _fail(f"{player_path}.link", "expected a host-relative link")


# Everything that exists only because the engine emits stall telemetry. Naming
# any of these is the same statement as `telemetry: false`, so the two have to
# agree — a block that contradicts itself explains nothing.
TELEMETRY_DERIVED = frozenset({"stall_firings", "cells", "t1:stall"})


def _capabilities(value: Any, path: str) -> dict[str, Any]:
    """What the build under test could not be asked about, and why.

    The block only exists to explain an absence, so an empty one is not a
    weaker claim — it is noise, and it is rejected. Checked here rather than in
    each tier: a self-contradictory declaration is wrong whichever payload it
    travels with, and T1 was accepting ones T2 already refused.
    """
    block = _fields(value, path, {"telemetry", "unavailable", "note"})
    _bool(block["telemetry"], f"{path}.telemetry")
    names = _list(block["unavailable"], f"{path}.unavailable")
    if not names:
        _fail(f"{path}.unavailable", "a capabilities block with nothing missing")
    for index, name in enumerate(names):
        _str(name, f"{path}.unavailable[{index}]")
    if len(set(names)) != len(names):
        _fail(f"{path}.unavailable", "duplicate entries")
    if not _str(block["note"], f"{path}.note").strip():
        _fail(f"{path}.note", "an absence has to be explained, not just declared")
    derived = TELEMETRY_DERIVED & set(names)
    if block["telemetry"] and derived:
        _fail(
            f"{path}.unavailable",
            f"names {', '.join(sorted(derived))} as missing while"
            " capabilities.telemetry says the engine emits it",
        )
    if not block["telemetry"] and not derived:
        _fail(
            f"{path}.unavailable",
            "says the engine emits no telemetry but names nothing that"
            f" depends on it (expected one of {', '.join(sorted(TELEMETRY_DERIVED))})",
        )
    return block


def _build(value: Any, path: str) -> None:
    build = _fields(
        value, path, {"branch", "commit", "digest_md5", "dirty"}
    )
    _str(build["branch"], f"{path}.branch")
    commit = _str(build["commit"], f"{path}.commit")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        _fail(f"{path}.commit", "expected full 40-character lowercase git hash")
    if build["digest_md5"] is not None:
        _str(build["digest_md5"], f"{path}.digest_md5")
    _bool(build["dirty"], f"{path}.dirty")


def _nav(value: Any, path: str, envelope_map: str) -> None:
    """The preflight's stamp: which graph was ready when the run measured it.

    T1 and T2 both wait on this before touching a bot, so the block is the
    receipt for that wait, not a measurement in its own right. Every check
    here is one the reader would otherwise have to reconstruct from a raw
    `status` poll by hand.
    """
    block = _fields(
        value, path, {"map", "state", "cells", "links", "rj_links", "waited_s"}
    )
    if block["map"] != envelope_map:
        _fail(
            f"{path}.map",
            "a ready graph for another map is the wrong graph, and drills"
            " against it would fail for a reason that has nothing to do"
            " with the bot",
        )
    if block["state"] != "ready":
        _fail(
            f"{path}.state",
            "the runner refuses to measure on a graph that is not ready, so"
            " any other value in a written envelope is a stamp nothing produced",
        )
    if (
        isinstance(block["cells"], bool)
        or not isinstance(block["cells"], int)
        or block["cells"] <= 0
    ):
        _fail(f"{path}.cells", "a ready graph with zero cells is not a graph")
    if (
        isinstance(block["links"], bool)
        or not isinstance(block["links"], int)
        or block["links"] <= 0
    ):
        _fail(f"{path}.links", "a ready graph with zero links is not a graph")
    if (
        isinstance(block["rj_links"], bool)
        or not isinstance(block["rj_links"], int)
        or block["rj_links"] < 0
    ):
        _fail(
            f"{path}.rj_links",
            "a build with no rocket-jump links is a legitimate build, and a"
            " negative count could not describe one",
        )
    if (
        isinstance(block["waited_s"], bool)
        or not isinstance(block["waited_s"], (int, float))
        or block["waited_s"] < 0
    ):
        _fail(
            f"{path}.waited_s",
            "waited_s is the record that the preflight happened, and a"
            " preflight cannot have waited a negative amount of time",
        )


def _t0(payload: Any, path: str, capabilities: dict[str, Any] | None) -> None:
    data = _fields(
        payload, path, {"modules", "total", "quality_floors", "verdict"}
    )
    module_pass = True
    test_sum = passed_sum = 0
    for index, value in enumerate(_list(data["modules"], f"{path}.modules")):
        item_path = f"{path}.modules[{index}]"
        item = _fields(value, item_path, {"name", "tests", "passed"})
        _str(item["name"], f"{item_path}.name")
        tests = _int(item["tests"], f"{item_path}.tests")
        passed = _int(item["passed"], f"{item_path}.passed")
        if passed > tests:
            _fail(item_path, "passed cannot exceed tests")
        module_pass &= passed == tests
        test_sum += tests
        passed_sum += passed
    total = _fields(data["total"], f"{path}.total", {"tests", "passed"})
    if (
        _int(total["tests"], f"{path}.total.tests") != test_sum
        or _int(total["passed"], f"{path}.total.passed") != passed_sum
    ):
        _fail(f"{path}.total", "must equal the sum of modules")
    floors_pass = True
    for index, value in enumerate(
        _list(data["quality_floors"], f"{path}.quality_floors")
    ):
        item_path = f"{path}.quality_floors[{index}]"
        item = _fields(value, item_path, {"name", "floor", "unit", "passed"})
        _str(item["name"], f"{item_path}.name")
        if isinstance(item["floor"], bool) or not isinstance(
            item["floor"], (int, float)
        ):
            _fail(f"{item_path}.floor", "expected number")
        _str(item["unit"], f"{item_path}.unit")
        floors_pass &= _bool(item["passed"], f"{item_path}.passed")
    expected = "PASS" if module_pass and floors_pass else "FAIL"
    if data["verdict"] != expected:
        _fail(f"{path}.verdict", f"expected {expected}")


def _t1(payload: Any, path: str, capabilities: dict[str, Any] | None) -> None:
    # T1 reads arrival off `status`, so every drill is graded the same on a
    # build with no telemetry. What changes is that the `stall` outcome cannot
    # occur, and an attempt the engine would have called a stall is recorded as
    # a timeout instead. That reclassification has to be stated in this
    # envelope's own terms — a block that only names T2's fields leaves a
    # reader of the drills none the wiser.
    if capabilities is not None and capabilities.get("telemetry") is False:
        if "t1:stall" not in (capabilities.get("unavailable") or []):
            _fail(
                f"{path}",
                "a T1 run on a build without telemetry must declare 't1:stall'"
                " as unavailable: its stalls were recorded as timeouts",
            )
    data = _fields(
        payload, path, {"scenarios", "dash", "verdict"}, {"regime_note", "demo"}
    )
    if data.get("demo") is not None:
        _str(data["demo"], f"{path}.demo")
    if "regime_note" in data and data["regime_note"] != "quick":
        _fail(f"{path}.regime_note", "expected 'quick'")
    all_pass = True
    withheld: list[str] = []
    scenarios = _list(data["scenarios"], f"{path}.scenarios")
    if not scenarios:
        _fail(f"{path}.scenarios", "expected at least one scenario")
    for index, value in enumerate(scenarios):
        item_path = f"{path}.scenarios[{index}]"
        item = _fields(
            value,
            item_path,
            {
                "name",
                "category",
                "place",
                "attempts",
                "threshold",
                "passed",
                "verdict",
                "evidence",
            },
            {"arrived", "best_time_s", "requires"},
        )
        _str(item["name"], f"{item_path}.name")
        _str(item["place"], f"{item_path}.place")
        if item["category"] not in {"grunddrill", "cellprov"}:
            _fail(f"{item_path}.category", "expected 'grunddrill' or 'cellprov'")
        _evidence(item["evidence"], f"{item_path}.evidence")
        requires = item.get("requires")
        if requires is not None:
            requires = _fields(
                requires,
                f"{item_path}.requires",
                {"capability", "engine_cvar", "note", "state"},
            )
            for field in ("capability", "engine_cvar", "note"):
                if not _str(requires[field], f"{item_path}.requires.{field}").strip():
                    _fail(
                        f"{item_path}.requires.{field}",
                        "expected non-empty string",
                    )
            if requires["state"] not in {"present", "absent", "unknown"}:
                _fail(
                    f"{item_path}.requires.state",
                    "expected 'present', 'absent' or 'unknown'",
                )
        attempts = _list(item["attempts"], f"{item_path}.attempts")
        # A drill can go ungraded, but only by saying which capability the
        # build was missing — otherwise a run that simply crashed halfway could
        # present itself as a principled abstention.
        if item["verdict"] is None:
            if requires is None or requires["state"] != "absent":
                _fail(
                    f"{item_path}.verdict",
                    "a drill without a verdict has to name the capability the"
                    " build was missing",
                )
            if attempts:
                _fail(f"{item_path}.attempts", "a withheld drill was never run")
            withheld_threshold = _fields(
                item["threshold"],
                f"{item_path}.threshold",
                {"required", "of"},
                {"reference_time_s", "max_time_s"},
            )
            if _int(withheld_threshold["of"], f"{item_path}.threshold.of") != 0:
                _fail(f"{item_path}.threshold.of", "no attempt was made")
            # Nothing measured is null, never zero — except the counts of
            # attempts, which are honestly zero because there were none.
            if _int(item["passed"], f"{item_path}.passed") != 0:
                _fail(f"{item_path}.passed", "a withheld drill passed nothing")
            if item.get("arrived") not in (None, 0):
                _fail(f"{item_path}.arrived", "a withheld drill arrived nowhere")
            if item.get("best_time_s") is not None:
                _fail(f"{item_path}.best_time_s", "a withheld drill has no time")
            if item["evidence"] is not None:
                _fail(
                    f"{item_path}.evidence",
                    "a withheld drill has nothing to watch",
                )
            withheld.append(_str(item["name"], f"{item_path}.name"))
            continue
        passed_count = 0
        arrived_count = 0
        for attempt_index, attempt_value in enumerate(attempts):
            attempt_path = f"{item_path}.attempts[{attempt_index}]"
            attempt = _fields(
                attempt_value,
                attempt_path,
                {"status", "time_s"},
                {"demo_t_s", "min_possible_s"},
            )
            if attempt.get("demo_t_s") is not None:
                _num_or_null(attempt["demo_t_s"], f"{attempt_path}.demo_t_s")
            # The lower bound exists for exactly one status: `abandoned`, the
            # attempt the impossibility check cut short while it was still
            # travelling. An attempt that arrived has a real time instead, and
            # every other way of not arriving has no bound to report — it
            # never had one, so a number there would be invented.
            if "min_possible_s" in attempt:
                if attempt["status"] in {"passed", "slow"}:
                    _fail(
                        f"{attempt_path}.min_possible_s",
                        "an attempt that arrived carries its time, not a bound",
                    )
                elif attempt["status"] != "abandoned":
                    _fail(
                        f"{attempt_path}.min_possible_s",
                        "a bound only exists for an attempt abandoned as impossible",
                    )
                elif not isinstance(attempt["min_possible_s"], (int, float)) or isinstance(
                    attempt["min_possible_s"], bool
                ):
                    # Present-but-null passed here for as long as the key
                    # check below only caught absence. An abandoned attempt
                    # exists BECAUSE a bound was computed; null is the same
                    # thrown-away knowledge as no key at all.
                    _fail(
                        f"{attempt_path}.min_possible_s",
                        "an abandoned attempt's bound is a number, not a null"
                        " wearing the key",
                    )
            elif attempt.get("status") == "abandoned":
                _fail(
                    f"{attempt_path}.min_possible_s",
                    "an abandoned attempt without its bound has thrown away"
                    " the only thing it knew",
                )
            if attempt["status"] not in {
                "passed",
                "slow",
                "fell",
                "timeout",
                # The impossibility bound cut this attempt short while it was
                # still travelling — a weaker claim than a timeout, and the
                # bound it could not have beaten (`min_possible_s`) is the
                # whole reason it is not folded into `timeout`. Unlike
                # `rocketjump` and `offroute` below, this is not void: it is
                # a real failure to arrive, same as `timeout`, just cut early.
                "abandoned",
                "stall",
                "loop",
                "detoured",
                "died",
                # The bot rocket-jumped on a drill that handed it no rockets,
                # so it picked them up on the way. The attempt answered a
                # different question than the one asked and counts as neither
                # an arrival nor a failure to arrive.
                "rocketjump",
                # The bot reached the target without passing the route's
                # `via` waypoints in order — it answered where, never how.
                # Void, the same shape as `rocketjump`: neither an arrival
                # nor a failure to arrive.
                "offroute",
            }:
                _fail(f"{attempt_path}.status", "unknown outcome")
            # `stall` is read off the telemetry event, so a build that emits
            # none cannot have produced one; such an attempt was recorded as a
            # timeout instead, which is what the declaration is there to say.
            if attempt["status"] == "stall" and capabilities is not None and \
                    capabilities.get("telemetry") is False:
                _fail(
                    f"{attempt_path}.status",
                    "the build emits no stall events, so this cannot be one",
                )
            _num_or_null(attempt["time_s"], f"{attempt_path}.time_s")
            if (attempt["status"] in {"passed", "slow"}) != (
                attempt["time_s"] is not None
            ):
                _fail(attempt_path, "time_s is present exactly for attempts that arrived")
            passed_count += attempt["status"] == "passed"
            arrived_count += attempt["status"] in {"passed", "slow"}
        threshold = _fields(
            item["threshold"],
            f"{item_path}.threshold",
            {"required", "of"},
            {"reference_time_s", "max_time_s"},
        )
        for field in ("reference_time_s", "max_time_s"):
            _num_or_null(threshold.get(field), f"{item_path}.threshold.{field}")
        if ("max_time_s" in threshold) != ("reference_time_s" in threshold):
            _fail(f"{item_path}.threshold", "the two time fields belong together")
        limit = threshold.get("max_time_s")
        if limit is not None:
            for attempt_index, attempt_value in enumerate(attempts):
                status = attempt_value.get("status")
                elapsed = attempt_value.get("time_s")
                if status == "passed" and elapsed is not None and elapsed > limit:
                    _fail(
                        f"{item_path}.attempts[{attempt_index}]",
                        "an arrival slower than max_time_s is 'slow', not 'passed'",
                    )
                if status == "slow" and elapsed is not None and elapsed <= limit:
                    _fail(
                        f"{item_path}.attempts[{attempt_index}]",
                        "an arrival within max_time_s is 'passed', not 'slow'",
                    )
        elif any(a.get("status") == "slow" for a in attempts):
            _fail(item_path, "'slow' requires a max_time_s to be slow against")
        if "arrived" in item and _int(item["arrived"], f"{item_path}.arrived") != arrived_count:
            _fail(f"{item_path}.arrived", "must equal attempts that reached the target")
        best = item.get("best_time_s")
        _num_or_null(best, f"{item_path}.best_time_s")
        # It has to be the fastest arrival that is actually in the list, or the
        # number the dashboard shows is not from this run.
        times = [a["time_s"] for a in attempts if a.get("time_s") is not None]
        if best is None and times:
            _fail(f"{item_path}.best_time_s", "attempts arrived but no best time")
        if best is not None:
            if not times:
                _fail(f"{item_path}.best_time_s", "a best time with no arrival")
            elif abs(best - min(times)) > 0.011:
                _fail(
                    f"{item_path}.best_time_s",
                    f"must be the fastest arrival ({min(times)}), not {best}",
                )
        required = _int(
            threshold["required"], f"{item_path}.threshold.required", 1
        )
        if _int(threshold["of"], f"{item_path}.threshold.of", 1) != len(attempts):
            _fail(f"{item_path}.threshold.of", "must equal attempt count")
        if required > len(attempts):
            _fail(f"{item_path}.threshold.required", "cannot exceed attempt count")
        # Quick cuts to three, but a drill may pin its own quick count
        # (`run.quick_attempts`), and the pin lives in the scenario file the
        # envelope deliberately does not embed — so an exact count is no
        # longer checkable here. What remains enforceable is the floor: quick
        # exists to spend less rig time, never to grade a drill on fewer than
        # the three attempts the cut guarantees. The ceiling is the writer's
        # (`of` must equal the attempt count, checked above, and the schema
        # rejects a pin above the full count at load).
        if data.get("regime_note") == "quick" and len(attempts) < 3:
            _fail(
                f"{item_path}.attempts",
                "a quick run grades no drill on fewer than three attempts",
            )
        if _int(item["passed"], f"{item_path}.passed") != passed_count:
            _fail(f"{item_path}.passed", "must equal passed attempts")
        expected = "PASS" if passed_count >= required else "FAIL"
        if item["verdict"] != expected:
            _fail(f"{item_path}.verdict", f"expected {expected}")
        all_pass &= expected == "PASS"
    # The drill and the envelope have to tell the same story. A drill withheld
    # in silence would leave the column reading `5/8 drillar` with nothing to
    # say the eighth was never asked; a declaration naming a drill that ran
    # would excuse a number the run actually produced.
    declared = set((capabilities or {}).get("unavailable") or [])
    for name in withheld:
        if f"t1:{name}" not in declared:
            _fail(
                f"{path}.scenarios",
                f"{name} was withheld for a missing capability, but"
                f" capabilities.unavailable does not name 't1:{name}'",
            )
    for value in scenarios:
        name = value.get("name") if isinstance(value, dict) else None
        if isinstance(name, str) and name not in withheld and f"t1:{name}" in declared:
            _fail(
                f"{path}.scenarios",
                f"capabilities.unavailable names 't1:{name}' as missing, yet"
                " the drill was run and graded",
            )
    dash = _fields(
        data["dash"],
        f"{path}.dash",
        {"peaks", "peak", "floor", "informative"},
        {"verdict", "place", "evidence"},
    )
    _evidence(dash.get("evidence"), f"{path}.dash.evidence")
    peaks = _list(dash["peaks"], f"{path}.dash.peaks")
    for index, peak in enumerate(peaks):
        _int(peak, f"{path}.dash.peaks[{index}]")
    expected_peak = max(peaks) if peaks else None
    if dash["peak"] != expected_peak:
        _fail(f"{path}.dash.peak", "must be the maximum peak or null")
    if isinstance(dash["floor"], bool) or not isinstance(
        dash["floor"], (int, float)
    ):
        _fail(f"{path}.dash.floor", "expected number")
    if not isinstance(dash["informative"], bool):
        _fail(f"{path}.dash.informative", "expected boolean")
    dash_pass = True
    if not dash["informative"]:
        # A graded dash owns a verdict of its own and counts toward T1.
        expected_dash = (
            "PASS"
            if dash["peak"] is not None and dash["peak"] >= dash["floor"]
            else "FAIL"
        )
        if dash.get("verdict") != expected_dash:
            _fail(f"{path}.dash.verdict", f"expected {expected_dash}")
        dash_pass = expected_dash == "PASS"
    elif dash.get("verdict") is not None:
        _fail(f"{path}.dash.verdict", "an informative dash carries no verdict")
    expected_verdict = "PASS" if all_pass and dash_pass else "FAIL"
    if data["verdict"] != expected_verdict:
        _fail(f"{path}.verdict", f"expected {expected_verdict}")


def _t2(payload: Any, path: str, capabilities: dict[str, Any] | None) -> None:
    data = _fields(
        payload,
        path,
        {"duration_s", "regime_note", "stats", "cells", "verdict"},
        {"demo", "evidence", "moments", "sources"},
    )
    if data.get("demo") is not None:
        _str(data["demo"], f"{path}.demo")
    _evidence(data.get("evidence"), f"{path}.evidence")
    for index, moment in enumerate(_list(data.get("moments", []), f"{path}.moments")):
        _evidence(moment, f"{path}.moments[{index}]")
    for name, source in _dict(data.get("sources", {}), f"{path}.sources").items():
        _str(source, f"{path}.sources.{name}")
    duration = _int(data["duration_s"], f"{path}.duration_s", 1)
    expected_note = None if duration == 600 else "smoke"
    if data["regime_note"] != expected_note:
        _fail(f"{path}.regime_note", f"expected {expected_note!r}")
    stats = _fields(
        data["stats"],
        f"{path}.stats",
        {
            "quad_takes",
            "quad_lay_avg",
            "pent_takes",
            "pent_lay_avg",
            "speed_1s",
            "speed_100ms",
            "still_s_per_bot",
            "stall_firings",
            "polls",
            "bots",
        },
        # The equality gate's fields. Known to the schema and type-checked when
        # present, so they can never be silently discarded — but optional, so
        # that every T2 envelope recorded before the gate existed still
        # validates. RUNBOOK §10 asks the whole corpus to pass this, and a
        # schema change that retroactively invalidates history is a worse fault
        # than the one it fixes. Presence is enforced where it actually
        # matters: `powerup_watch.py` refuses to compare an envelope that does
        # not declare `items_poll_s`, because a gate that cannot read its own
        # resolution is guessing.
        {
            "quad_lay_n",
            "quad_lay_censored",
            "pent_lay_n",
            "pent_lay_censored",
            "items_poll_s",
            "watch_poll_s",
            "items_polls",
            "items_poll_gap_max_s",
        },
    )
    # A build with no stall instrumentation cannot report a stall count, and a
    # zero there would read as the best column on the page. Null is the only
    # honest value, and it has to come with the reason attached — otherwise a
    # missing number is indistinguishable from a dropped one.
    #
    # The declaration is checked against the field it is supposed to be about,
    # not merely against the `telemetry` flag: a block that says telemetry is
    # gone while naming something else does not excuse this null, and one that
    # names this field while a number sits in it is claiming both that the
    # measurement happened and that it could not.
    telemetry = capabilities is None or capabilities.get("telemetry") is not False
    declared = "stall_firings" in (
        (capabilities or {}).get("unavailable") or []
    )
    if stats["stall_firings"] is None:
        if not declared:
            _fail(
                f"{path}.stats.stall_firings",
                "a measurement that did not happen must be named in"
                " capabilities.unavailable",
            )
        if telemetry:
            _fail(
                f"{path}.stats.stall_firings",
                "declared unmeasurable while capabilities.telemetry says the"
                " build can emit stall events",
            )
    else:
        if declared:
            _fail(
                f"{path}.stats.stall_firings",
                "named in capabilities.unavailable, yet a count is reported",
            )
        if not telemetry:
            _fail(
                f"{path}.stats.stall_firings",
                "the build cannot emit stall events, so it cannot have counted them",
            )
    for field in ("quad_takes", "pent_takes", "polls"):
        _int(stats[field], f"{path}.stats.{field}")
    for field in (
        "quad_lay_n",
        "quad_lay_censored",
        "pent_lay_n",
        "pent_lay_censored",
        "items_polls",
    ):
        if field in stats:
            _int(stats[field], f"{path}.stats.{field}")
    for field in ("items_poll_s", "items_poll_gap_max_s"):
        if field in stats:
            _num(stats[field], f"{path}.stats.{field}")
    # Null when T2 ran without the independent observer.
    if "watch_poll_s" in stats:
        _num_or_null(stats["watch_poll_s"], f"{path}.stats.watch_poll_s")
    # A censored interval is still a take, so the parts must account for it
    # exactly — checked only when the envelope carries both parts.
    for name in ("quad", "pent"):
        parts = (f"{name}_lay_n", f"{name}_lay_censored")
        if all(field in stats for field in parts):
            if stats[parts[0]] + stats[parts[1]] != stats[f"{name}_takes"]:
                _fail(
                    f"{path}.stats.{name}_takes",
                    f"expected {name}_lay_n + {name}_lay_censored",
                )
    if stats["stall_firings"] is not None:
        _int(stats["stall_firings"], f"{path}.stats.stall_firings")
    _int(stats["bots"], f"{path}.stats.bots", 1)
    for field in (
        "quad_lay_avg",
        "pent_lay_avg",
        "speed_1s",
        "speed_100ms",
        "still_s_per_bot",
    ):
        _num_or_null(stats[field], f"{path}.stats.{field}")
    count_sum = reason_sum = kind_sum = 0
    for index, value in enumerate(_list(data["cells"], f"{path}.cells")):
        item_path = f"{path}.cells[{index}]"
        item = _fields(
            value,
            item_path,
            {"id", "pos", "n", "reasons", "kinds", "links"},
            {"evidence"},
        )
        _evidence(item.get("evidence"), f"{item_path}.evidence")
        _str(item["id"], f"{item_path}.id")
        pos = _list(item["pos"], f"{item_path}.pos")
        if len(pos) != 3:
            _fail(f"{item_path}.pos", "expected three coordinates")
        for coordinate in pos:
            _num_or_null(coordinate, f"{item_path}.pos")
            if coordinate is None:
                _fail(f"{item_path}.pos", "coordinates cannot be null")
        count_sum += _int(item["n"], f"{item_path}.n")
        reasons = _dict(item["reasons"], f"{item_path}.reasons")
        reason_sum += sum(
            _int(count, f"{item_path}.reasons.{reason}")
            for reason, count in reasons.items()
        )
        kinds = _dict(item["kinds"], f"{item_path}.kinds")
        kind_sum += sum(
            _int(count, f"{item_path}.kinds.{kind}")
            for kind, count in kinds.items()
        )
        links = _dict(item["links"], f"{item_path}.links")
        for link, count in links.items():
            _str(link, f"{item_path}.links key")
            _int(count, f"{item_path}.links.{link}")
    firings = stats["stall_firings"]
    if firings is None:
        # Nothing was counted, so nothing can have a place on the map either.
        if data["cells"]:
            _fail(
                f"{path}.cells",
                "zones from a build that emits no stall events",
            )
    elif firings != count_sum or firings != reason_sum:
        _fail(
            path,
            "invariant failed: stall_firings != sum(cells.n) != sum(reasons)",
        )
    elif firings != kind_sum:
        _fail(
            path,
            "invariant failed: stall_firings != sum(kinds) — every firing"
            " happened on exactly one kind of route leg, offroute included",
        )
    if data["verdict"] != "MEASURED":
        _fail(f"{path}.verdict", "expected 'MEASURED'")


def _t3(payload: Any, path: str, capabilities: dict[str, Any] | None) -> None:
    data = _fields(
        payload,
        path,
        {
            "duration_s",
            "sides",
            "result",
            "readiness",
            "scoreboard",
            "combat_lock",
            "replicate_of",
            "verdict",
        },
    )
    _int(data["duration_s"], f"{path}.duration_s", 1)
    sides = _list(data["sides"], f"{path}.sides")
    if len(sides) != 2:
        _fail(f"{path}.sides", "expected branch and reference")
    seen = set()
    for index, value in enumerate(sides):
        item_path = f"{path}.sides[{index}]"
        item = _fields(
            value, item_path, {"side", "build", "frags", "stats", "cells"}
        )
        if item["side"] not in {"branch", "reference"}:
            _fail(f"{item_path}.side", "unknown side")
        seen.add(item["side"])
        _build(item["build"], f"{item_path}.build")
        _int(item["frags"], f"{item_path}.frags", -10_000)
        _dict(item["stats"], f"{item_path}.stats")
        _list(item["cells"], f"{item_path}.cells")
    if seen != {"branch", "reference"}:
        _fail(f"{path}.sides", "branch and reference must both be present")
    result = _fields(
        data["result"],
        f"{path}.result",
        {"diff", "winner", "oracle", "mvd"},
    )
    if isinstance(result["diff"], bool) or not isinstance(result["diff"], int):
        _fail(f"{path}.result.diff", "expected integer")
    if result["winner"] not in {"branch", "reference", "draw"}:
        _fail(f"{path}.result.winner", "unknown winner")
    _str(result["oracle"], f"{path}.result.oracle")
    _str(result["mvd"], f"{path}.result.mvd")
    frags = {item["side"]: item["frags"] for item in sides}
    expected_diff = frags["branch"] - frags["reference"]
    expected_winner = (
        "branch"
        if expected_diff > 0
        else "reference"
        if expected_diff < 0
        else "draw"
    )
    if result["diff"] != expected_diff:
        _fail(f"{path}.result.diff", f"expected {expected_diff}")
    if result["winner"] != expected_winner:
        _fail(f"{path}.result.winner", f"expected {expected_winner}")
    _scoreboard(data["scoreboard"], f"{path}.scoreboard")
    ready = _fields(
        data["readiness"],
        f"{path}.readiness",
        {"seats_ok", "gate", "passed"},
    )
    _int(ready["seats_ok"], f"{path}.readiness.seats_ok")
    _str(ready["gate"], f"{path}.readiness.gate")
    if _bool(ready["passed"], f"{path}.readiness.passed") is not True:
        _fail(f"{path}.readiness.passed", "complete T3 must pass readiness")
    if data["combat_lock"] is not None:
        _dict(data["combat_lock"], f"{path}.combat_lock")
    if data["replicate_of"] is not None:
        _str(data["replicate_of"], f"{path}.replicate_of")
    if data["verdict"] != "PIPELINE-OK":
        _fail(f"{path}.verdict", "single-match T3 must be 'PIPELINE-OK'")


#: The inventory of every T4 envelope that existed when the five-value verdict
#: was introduced, and the sha256 of that inventory. The file is pinned here
#: rather than merely read: an inventory anybody can extend is not a closed
#: list, and `COMPLETE` is only still accepted because the list is closed.
_LEGACY_T4_INVENTORY = (
    Path(__file__).resolve().parent.parent / "schema" / "legacy-t4-inventering.json"
)
_LEGACY_T4_INVENTORY_SHA256 = (
    "4146c26f2c87f0c7c1ea683d212216f582711e75d19e6d6a17dc6a44a3ca1f04"
)
_legacy_t4_cache: dict[str, str] | None = None


def legacy_t4_inventory() -> dict[str, str]:
    """`{filename: sha256}` for the 27 grandfathered T4 envelopes.

    Every failure mode here is fatal, not a shrug: an unreadable or altered
    inventory means nothing can be shown to be grandfathered, and the only
    safe answer to "is this envelope one of the 27?" is then no.
    """
    global _legacy_t4_cache
    if _legacy_t4_cache is not None:
        return _legacy_t4_cache
    try:
        raw = _LEGACY_T4_INVENTORY.read_bytes()
    except OSError as exc:
        _fail(str(_LEGACY_T4_INVENTORY), f"legacy T4 inventory is unreadable: {exc}")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != _LEGACY_T4_INVENTORY_SHA256:
        _fail(
            str(_LEGACY_T4_INVENTORY),
            f"legacy T4 inventory sha256 is {digest}, pinned"
            f" {_LEGACY_T4_INVENTORY_SHA256}",
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        _fail(str(_LEGACY_T4_INVENTORY), f"legacy T4 inventory is not JSON: {exc}")
    entries = document.get("envelopes")
    if not isinstance(entries, dict) or not entries:
        _fail(str(_LEGACY_T4_INVENTORY), "legacy T4 inventory lists no envelopes")
    inventory = {
        name: entry["sha256"]
        for name, entry in entries.items()
        if isinstance(entry, dict) and isinstance(entry.get("sha256"), str)
    }
    if len(inventory) != len(entries):
        _fail(str(_LEGACY_T4_INVENTORY), "legacy T4 inventory entry without a sha256")
    _legacy_t4_cache = inventory
    return inventory


def _source_file(path: str) -> str:
    """The document's own path, recovered from the payload path.

    `validate_result` builds every payload path as `<source>.payload`, and the
    legacy gate is about a *file* — its name and its bytes — not about the
    object in memory. A document handed in without a real path (`<result>`)
    simply cannot be shown to be one of the 27, which is the fail-closed
    answer.
    """
    return path[: -len(".payload")] if path.endswith(".payload") else path


def _t4_legacy_grandfathered(path: str) -> None:
    source = _source_file(path)
    name = Path(source).name
    inventory = legacy_t4_inventory()
    expected = inventory.get(name)
    if expected is None:
        _fail(
            f"{path}.verdict",
            "'COMPLETE' is the retired T4 vocabulary and is accepted only for"
            f" the inventoried envelopes; {name!r} is not one of them",
        )
    try:
        digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    except OSError as exc:
        _fail(
            f"{path}.verdict",
            f"'COMPLETE' needs the inventoried file to check against: {exc}",
        )
    if digest != expected:
        _fail(
            f"{path}.verdict",
            f"{name} is inventoried as {expected} but hashes to {digest}",
        )


def _t4_ladder(data: dict[str, Any], path: str) -> int:
    """The rungs, and the highest *won* skill they add up to.

    Shared by both vocabularies: the ladder's own rules (the fixed skills, the
    stop at the first non-win, win/draw/loss agreeing with the frags) did not
    change when the verdict did.
    """
    _int(data["duration_s_per_match"], f"{path}.duration_s_per_match", 1)
    expected_skills = [10, 12, 14, 16, 18, 20]
    ladder = _list(data["ladder"], f"{path}.ladder")
    if not ladder:
        _fail(f"{path}.ladder", "complete ladder must contain a match")
    stopped = False
    for index, value in enumerate(ladder):
        item_path = f"{path}.ladder[{index}]"
        item = _fields(
            value,
            item_path,
            {"skill", "frags_for", "frags_against", "win", "mvd"},
            {"draw", "scoreboard", "measured"},
        )
        if "measured" in item:
            measured = _fields(
                item["measured"],
                f"{item_path}.measured",
                set(t4_dom.RUNG_MEASURED_FIELDS),
            )
            for field in t4_dom.RUNG_MEASURED_FIELDS:
                if measured[field] is not None:
                    _num(measured[field], f"{item_path}.measured.{field}", 0)
        if index >= len(expected_skills) or item["skill"] != expected_skills[index]:
            _fail(f"{item_path}.skill", "ladder must use 10,12,14,16,18,20")
        if stopped:
            _fail(item_path, "ladder continues after a loss or draw")
        _int(item["frags_for"], f"{item_path}.frags_for", -10_000)
        _int(item["frags_against"], f"{item_path}.frags_against", -10_000)
        win = _bool(item["win"], f"{item_path}.win")
        draw = item.get("draw", False)
        if "draw" in item:
            _bool(draw, f"{item_path}.draw")
        if draw and win:
            _fail(item_path, "draw cannot be a win")
        if draw != (item["frags_for"] == item["frags_against"]):
            _fail(item_path, "draw must match equal frag scores")
        if win != (item["frags_for"] > item["frags_against"]):
            _fail(item_path, "win must match the frag scores")
        if not win and not draw and item["frags_for"] >= item["frags_against"]:
            _fail(item_path, "loss must have fewer frags_for")
        if not win:
            stopped = True
        _str(item["mvd"], f"{item_path}.mvd")
        _scoreboard(item.get("scoreboard"), f"{item_path}.scoreboard")
    # Recomputed from the rungs rather than read: `reached` is the highest WON
    # skill, and an envelope that wrote the last *played* one instead would
    # otherwise promote a draw or a loss into an achievement.
    reached = t4_dom.reached_from_ladder(ladder)
    if _int(data["reached"], f"{path}.reached") != reached:
        _fail(
            f"{path}.reached",
            f"expected {reached} — the highest won skill, 0 if none was won",
        )
    _str(data["skill_verified_by"], f"{path}.skill_verified_by")
    return reached


def _t4_measurements(value: Any, path: str) -> dict[str, Any]:
    block = _fields(
        value,
        path,
        {
            "shots_fired",
            "teamkills",
            "kills_total",
            "still_s_per_bot_max",
            "item_pickups",
        },
    )
    for field, minimum in (
        ("shots_fired", 0),
        ("teamkills", 0),
        ("kills_total", 0),
        ("still_s_per_bot_max", 0),
        ("item_pickups", 0),
    ):
        entry = block[field]
        if entry is None:
            continue
        _num(entry, f"{path}.{field}", minimum)
    if block["teamkills"] is None and block["kills_total"] is not None:
        _fail(
            f"{path}.kills_total",
            "half a ratio is not a measurement: teamkills is unavailable",
        )
    return block


def _t4_sampling(value: Any, path: str, measurements: dict[str, Any]) -> None:
    """The live channels' own receipts, and the gap discipline over them (§3).

    A gap wider than the ceiling is not smoothed over: the field it feeds goes
    unavailable. Checking it here means an envelope cannot report a measurement
    the sampling it declares could not have produced.
    """
    limits = t4_dom.thresholds()
    block = _fields(
        value,
        path,
        {
            "still_interval_s",
            "still_gap_max_s",
            "items_poll_s",
            "items_poll_gap_max_s",
        },
    )
    for field, expected in (
        ("still_interval_s", limits["still_sample_interval_s"]),
        ("items_poll_s", limits["items_poll_s"]),
    ):
        if block[field] != expected:
            _fail(f"{path}.{field}", f"expected {expected}")
    for gap_field, ceiling, measurement, capability in (
        (
            "still_gap_max_s",
            limits["still_sample_gap_max_s"],
            "still_s_per_bot_max",
            t4_dom.CAP_STILL,
        ),
        (
            "items_poll_gap_max_s",
            limits["items_poll_gap_max_s"],
            "item_pickups",
            t4_dom.CAP_ITEMS,
        ),
    ):
        gap = block[gap_field]
        measured = measurements.get(measurement) is not None
        if gap is None:
            if measured:
                _fail(
                    f"{path}.{gap_field}",
                    f"{measurement} was measured, so its sampling gap is known",
                )
            continue
        _num(gap, f"{path}.{gap_field}", 0)
        if gap > ceiling and measured:
            _fail(
                f"{path}.{gap_field}",
                f"gap {gap} s exceeds the {ceiling} s ceiling, so"
                f" {capability} is unavailable, not measured",
            )


def _t4_v2(data: dict[str, Any], path: str, capabilities: dict[str, Any] | None) -> None:
    if data["t4_schema"] != t4_dom.T4_SCHEMA:
        _fail(f"{path}.t4_schema", f"expected {t4_dom.T4_SCHEMA}")
    reached = _t4_ladder(data, path)
    limits = _dict(data["thresholds"], f"{path}.thresholds")
    canonical = t4_dom.thresholds()
    if limits != canonical:
        _fail(
            f"{path}.thresholds",
            "a run cannot restate its own gate: expected the calibrated"
            f" constants {canonical}",
        )
    measurements = _t4_measurements(data["measurements"], f"{path}.measurements")
    _t4_sampling(data["sampling"], f"{path}.sampling", measurements)
    # When every rung shows its own contribution, the ladder's four numbers
    # are checkable rather than declared: they must be the fold of the rungs.
    # A rung that measured nothing makes the ladder's field unavailable, and a
    # sum over the rungs that happened to answer is a number about a different
    # ladder.
    if all(isinstance(rung, dict) and "measured" in rung for rung in data["ladder"]):
        folded = t4_dom.measure_ladder(data["ladder"])
        if measurements != folded:
            _fail(
                f"{path}.measurements",
                f"expected {folded} from the rungs' own measured blocks",
            )
        receipt = t4_dom.sampling_receipt(data["ladder"])
        if data["sampling"] != receipt:
            _fail(
                f"{path}.sampling",
                f"expected {receipt} from the rungs' own measured blocks",
            )
    verdict = _str(data["verdict"], f"{path}.verdict")
    if verdict not in t4_dom.VERDICTS:
        _fail(
            f"{path}.verdict",
            f"expected one of {', '.join(t4_dom.VERDICTS)}",
        )
    dom = _fields(
        data["dom"], f"{path}.dom", {"failed_gates", "missing", "reason", "labels"}
    )
    for field in ("failed_gates", "missing", "labels"):
        for index, entry in enumerate(_list(dom[field], f"{path}.dom.{field}")):
            _str(entry, f"{path}.dom.{field}[{index}]")
    if not _str(dom["reason"], f"{path}.dom.reason").strip():
        _fail(f"{path}.dom.reason", "a verdict has to say what produced it")
    # The verdict is recomputed, not read. An envelope whose own numbers say
    # something else is the whole reason this validator exists.
    outcome = t4_dom.ladder_outcome(data["ladder"])
    recomputed = t4_dom.adjudicate(measurements, outcome, limits)
    if list(dom["failed_gates"]) != recomputed["failed_gates"]:
        _fail(
            f"{path}.dom.failed_gates",
            f"expected {recomputed['failed_gates']} from the measurements",
        )
    if list(dom["missing"]) != recomputed["missing"]:
        _fail(
            f"{path}.dom.missing",
            f"expected {recomputed['missing']} from the measurements",
        )
    if verdict != recomputed["verdict"]:
        _fail(
            f"{path}.verdict",
            f"expected {recomputed['verdict']}: {recomputed['reason']}",
        )
    if verdict == "VINST" and not (reached == 20 and outcome["won_top"]):
        _fail(f"{path}.verdict", "VINST requires reached 20 and a win on level 20")
    # Whatever could not be measured has to be declared where every other tier
    # declares it, or the absence is silent again.
    declared = set()
    if capabilities is not None:
        declared = set(capabilities["unavailable"]) & set(t4_dom.T4_CAPABILITIES)
    if declared != set(recomputed["missing"]):
        _fail(
            f"{path}.dom.missing",
            "capabilities.unavailable must name exactly the unmeasured T4"
            f" fields: {sorted(recomputed['missing'])}, not {sorted(declared)}",
        )
    if (measurements["item_pickups"] is not None) != (
        t4_dom.LABEL_ITEM_PROXY in dom["labels"]
    ):
        _fail(
            f"{path}.dom.labels",
            f"a judged (d) outcome carries the {t4_dom.LABEL_ITEM_PROXY!r}"
            " label, and an unjudged one does not",
        )
    alarm = data.get("cross_alarm")
    if verdict == "FAIL":
        if not _str(alarm, f"{path}.cross_alarm").strip():
            _fail(
                f"{path}.cross_alarm",
                "a FAIL names the nearest preceding T1/T3 run of the same"
                f" commit, or the literal {t4_dom.NO_CROSS_ALARM!r}",
            )
    elif alarm is not None:
        _fail(f"{path}.cross_alarm", "only a FAIL raises the cross alarm")
    semantics = data.get("draw_semantik")
    if verdict == "OAVGJORD":
        if semantics != t4_dom.DRAW_SEMANTICS:
            _fail(
                f"{path}.draw_semantik",
                f"an OAVGJORD envelope carries {t4_dom.DRAW_SEMANTICS!r}: the"
                " draw semantics is an open owner question, not a decision"
                " this run made",
            )
    elif semantics is not None:
        _fail(f"{path}.draw_semantik", "only OAVGJORD carries the draw question")


def _t4(payload: Any, path: str, capabilities: dict[str, Any] | None) -> None:
    """T4 in two vocabularies: the five-value verdict, and the retired one.

    `t4_schema: 2` is the live contract. An envelope without it is from before
    the change, and is accepted only if it is one of the inventoried 27 — see
    `schema/legacy-t4-inventering.json`. Everything else that still says
    `COMPLETE` is refused whatever its date, because a vocabulary nobody writes
    any more cannot be produced by a run that happened after it was retired.
    """
    if isinstance(payload, dict) and "t4_schema" in payload:
        data = _fields(
            payload,
            path,
            {
                "t4_schema",
                "duration_s_per_match",
                "ladder",
                "reached",
                "skill_verified_by",
                "verdict",
                "measurements",
                "sampling",
                "thresholds",
                "dom",
            },
            {"cross_alarm", "draw_semantik"},
        )
        _t4_v2(data, path, capabilities)
        return
    data = _fields(
        payload,
        path,
        {
            "duration_s_per_match",
            "ladder",
            "reached",
            "skill_verified_by",
            "verdict",
        },
    )
    _t4_ladder(data, path)
    if data["verdict"] != "COMPLETE":
        _fail(
            f"{path}.verdict",
            "expected 'COMPLETE' — an envelope without t4_schema is legacy,"
            f" and the live vocabulary needs t4_schema {t4_dom.T4_SCHEMA}",
        )
    _t4_legacy_grandfathered(path)


_PAYLOAD_CHECKS: dict[str, Callable[[Any, str, "dict[str, Any] | None"], None]] = {
    "T0": _t0,
    "T1": _t1,
    "T2": _t2,
    "T3": _t3,
    "T4": _t4,
}


def validate_result(document: Any, source: str = "<result>") -> dict[str, Any]:
    root = _fields(
        document,
        source,
        {
            "schema",
            "run_id",
            "tier",
            "status",
            "started_utc",
            "ended_utc",
            "map",
            "build",
            "config_digest",
            "runner_version",
            "provenance",
            "payload",
        },
        {"error", "capabilities", "nav"},
    )
    if root["schema"] != "rtx-testflow/1":
        _fail(
            f"{source}.schema",
            f"unsupported schema {root['schema']!r}; expected 'rtx-testflow/1'",
        )
    tier = root["tier"]
    if tier not in _PAYLOAD_CHECKS:
        _fail(f"{source}.tier", f"unsupported tier {tier!r}")
    for field in ("run_id", "started_utc", "ended_utc", "map", "runner_version"):
        _str(root[field], f"{source}.{field}")
    if not re.fullmatch(
        rf"{tier.lower()}-\d{{8}}T\d{{6}}Z-[0-9a-f]{{8}}",
        root["run_id"],
    ):
        _fail(f"{source}.run_id", "does not match tier-startstamp-commit8")
    try:
        started = datetime.fromisoformat(root["started_utc"].replace("Z", "+00:00"))
        ended = datetime.fromisoformat(root["ended_utc"].replace("Z", "+00:00"))
    except ValueError:
        _fail(source, "started_utc and ended_utc must be ISO-8601 timestamps")
    if not root["started_utc"].endswith("Z") or not root["ended_utc"].endswith("Z"):
        _fail(source, "timestamps must be UTC with a Z suffix")
    if ended < started:
        _fail(f"{source}.ended_utc", "cannot precede started_utc")
    if root["status"] not in {"complete", "failed", "aborted"}:
        _fail(f"{source}.status", "unknown status")
    if root["status"] == "complete" and "error" in root:
        _fail(source, "complete result cannot contain error")
    if root["status"] != "complete":
        if "error" not in root:
            _fail(source, "non-complete result requires error")
        _str(root["error"], f"{source}.error")
    _build(root["build"], f"{source}.build")
    # The stamp is singular, and only T1 and T2 measure against exactly one
    # graph. T0 never connects at all; T3 and T4 do, but to two client builds
    # at once, each of which builds its own navmesh after joining — one block
    # beside a single `build` could not say which side it described. T1 and T2
    # both gate on the preflight before they measure anything, so a complete
    # run of either has to carry the receipt; a non-complete one may have died
    # before the preflight finished and so may legitimately have nothing to
    # show.
    if "nav" in root:
        if tier not in {"T1", "T2"}:
            _fail(
                f"{source}.nav",
                "only T1 and T2 measure against exactly one graph; T0 has"
                " none and a two-sided tier has two, so a single stamp here"
                " could not say which one it described",
            )
        _nav(root["nav"], f"{source}.nav", root["map"])
    elif tier in {"T1", "T2"} and root["status"] == "complete":
        _fail(
            f"{source}.nav",
            "a complete T1/T2 run measured against a graph, and the"
            " envelope has to name which one",
        )
    digest = _str(root["config_digest"], f"{source}.config_digest")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        _fail(f"{source}.config_digest", "expected sha256:<64 lowercase hex>")
    if root["provenance"] not in {"measured", "derived", "synthetic"}:
        _fail(f"{source}.provenance", "unknown provenance")
    capabilities = (
        _capabilities(root["capabilities"], f"{source}.capabilities")
        if "capabilities" in root
        else None
    )
    if root["status"] == "complete":
        _PAYLOAD_CHECKS[tier](root["payload"], f"{source}.payload", capabilities)
    else:
        _dict(root["payload"], f"{source}.payload")
    return root


def validate_scenario_result(document: Any, source: str = "<scenario>") -> dict[str, Any]:
    try:
        return validate_scenario(document, source)
    except ScenarioError as exc:
        raise ValidationError(str(exc)) from exc
