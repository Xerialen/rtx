"""Hand-written validators for rtx-testflow/1 and rtx-scenario/1."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Callable

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
        {"attempt", "status", "dash", "metric", "who"},
    )
    _str(item["demo"], f"{path}.demo")
    _num_or_null(item["at_s"], f"{path}.at_s")
    link = _str(item["link"], f"{path}.link")
    if not link.startswith("/"):
        _fail(f"{path}.link", "expected a host-relative demo-player link")


def _scoreboard(value: Any, path: str) -> None:
    """The match card: final team scores and every player's line.

    Null when no analyzer or no KTX block was available for that match.
    """
    if value is None:
        return
    card = _fields(
        value,
        path,
        {"teams", "players", "source"},
        {"map", "duration_s", "demo", "link"},
    )
    _str(card["source"], f"{path}.source")
    for index, team in enumerate(_list(card["teams"], f"{path}.teams")):
        team_path = f"{path}.teams[{index}]"
        item = _fields(team, team_path, {"name", "frags"})
        _str(item["name"], f"{team_path}.name")
        _int(item["frags"], f"{team_path}.frags", -10_000)
    for index, player in enumerate(_list(card["players"], f"{path}.players")):
        player_path = f"{path}.players[{index}]"
        item = _fields(
            player,
            player_path,
            {"name", "team", "frags"},
            {
                "deaths",
                "kills",
                "tk",
                "dmg_given",
                "dmg_taken",
                "speed_max",
                "speed_avg",
                "spree_max",
                "link",
            },
        )
        _str(item["name"], f"{player_path}.name")
        _str(item["team"], f"{player_path}.team")
        for field in ("frags", "deaths", "kills", "tk", "spree_max"):
            if item.get(field) is not None:
                _int(item[field], f"{player_path}.{field}", -10_000)
        for field in ("dmg_given", "dmg_taken", "speed_max", "speed_avg"):
            _num_or_null(item.get(field), f"{player_path}.{field}")
        if item.get("link") is not None:
            link = _str(item["link"], f"{player_path}.link")
            if not link.startswith("/"):
                _fail(f"{player_path}.link", "expected a host-relative link")


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


def _t0(payload: Any, path: str) -> None:
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


def _t1(payload: Any, path: str) -> None:
    data = _fields(
        payload, path, {"scenarios", "dash", "verdict"}, {"regime_note", "demo"}
    )
    if data.get("demo") is not None:
        _str(data["demo"], f"{path}.demo")
    if "regime_note" in data and data["regime_note"] != "quick":
        _fail(f"{path}.regime_note", "expected 'quick'")
    all_pass = True
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
        )
        _str(item["name"], f"{item_path}.name")
        _str(item["place"], f"{item_path}.place")
        if item["category"] not in {"grunddrill", "cellprov"}:
            _fail(f"{item_path}.category", "expected 'grunddrill' or 'cellprov'")
        _evidence(item["evidence"], f"{item_path}.evidence")
        attempts = _list(item["attempts"], f"{item_path}.attempts")
        passed_count = 0
        for attempt_index, attempt_value in enumerate(attempts):
            attempt_path = f"{item_path}.attempts[{attempt_index}]"
            attempt = _fields(
                attempt_value, attempt_path, {"status", "time_s"}, {"demo_t_s"}
            )
            if attempt.get("demo_t_s") is not None:
                _num_or_null(attempt["demo_t_s"], f"{attempt_path}.demo_t_s")
            if attempt["status"] not in {
                "passed",
                "fell",
                "timeout",
                "stall",
                "loop",
                "detoured",
                "died",
            }:
                _fail(f"{attempt_path}.status", "unknown outcome")
            _num_or_null(attempt["time_s"], f"{attempt_path}.time_s")
            if (attempt["status"] == "passed") != (
                attempt["time_s"] is not None
            ):
                _fail(attempt_path, "time_s is present only for passed attempts")
            passed_count += attempt["status"] == "passed"
        threshold = _fields(
            item["threshold"], f"{item_path}.threshold", {"required", "of"}
        )
        required = _int(
            threshold["required"], f"{item_path}.threshold.required", 1
        )
        if _int(threshold["of"], f"{item_path}.threshold.of", 1) != len(attempts):
            _fail(f"{item_path}.threshold.of", "must equal attempt count")
        if required > len(attempts):
            _fail(f"{item_path}.threshold.required", "cannot exceed attempt count")
        if data.get("regime_note") == "quick" and len(attempts) != 3:
            _fail(f"{item_path}.attempts", "quick runs require three attempts")
        if _int(item["passed"], f"{item_path}.passed") != passed_count:
            _fail(f"{item_path}.passed", "must equal passed attempts")
        expected = "PASS" if passed_count >= required else "FAIL"
        if item["verdict"] != expected:
            _fail(f"{item_path}.verdict", f"expected {expected}")
        all_pass &= expected == "PASS"
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


def _t2(payload: Any, path: str) -> None:
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
    )
    for field in ("quad_takes", "pent_takes", "stall_firings", "polls"):
        _int(stats[field], f"{path}.stats.{field}")
    _int(stats["bots"], f"{path}.stats.bots", 1)
    for field in (
        "quad_lay_avg",
        "pent_lay_avg",
        "speed_1s",
        "speed_100ms",
        "still_s_per_bot",
    ):
        _num_or_null(stats[field], f"{path}.stats.{field}")
    count_sum = reason_sum = 0
    for index, value in enumerate(_list(data["cells"], f"{path}.cells")):
        item_path = f"{path}.cells[{index}]"
        item = _fields(
            value, item_path, {"id", "pos", "n", "reasons", "links"}, {"evidence"}
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
        links = _dict(item["links"], f"{item_path}.links")
        for link, count in links.items():
            _str(link, f"{item_path}.links key")
            _int(count, f"{item_path}.links.{link}")
    firings = stats["stall_firings"]
    if firings != count_sum or firings != reason_sum:
        _fail(
            path,
            "invariant failed: stall_firings != sum(cells.n) != sum(reasons)",
        )
    if data["verdict"] != "MEASURED":
        _fail(f"{path}.verdict", "expected 'MEASURED'")


def _t3(payload: Any, path: str) -> None:
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


def _t4(payload: Any, path: str) -> None:
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
    _int(data["duration_s_per_match"], f"{path}.duration_s_per_match", 1)
    expected_skills = [10, 12, 14, 16, 18, 20]
    ladder = _list(data["ladder"], f"{path}.ladder")
    if not ladder:
        _fail(f"{path}.ladder", "complete ladder must contain a match")
    reached = 0
    stopped = False
    for index, value in enumerate(ladder):
        item_path = f"{path}.ladder[{index}]"
        item = _fields(
            value,
            item_path,
            {"skill", "frags_for", "frags_against", "win", "mvd"},
            {"draw", "scoreboard"},
        )
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
        if win:
            reached = item["skill"]
        else:
            stopped = True
        _str(item["mvd"], f"{item_path}.mvd")
        _scoreboard(item.get("scoreboard"), f"{item_path}.scoreboard")
    if _int(data["reached"], f"{path}.reached") != reached:
        _fail(f"{path}.reached", f"expected {reached}")
    _str(data["skill_verified_by"], f"{path}.skill_verified_by")
    if data["verdict"] != "COMPLETE":
        _fail(f"{path}.verdict", "expected 'COMPLETE'")


_PAYLOAD_CHECKS: dict[str, Callable[[Any, str], None]] = {
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
        {"error"},
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
    digest = _str(root["config_digest"], f"{source}.config_digest")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        _fail(f"{source}.config_digest", "expected sha256:<64 lowercase hex>")
    if root["provenance"] not in {"measured", "derived", "synthetic"}:
        _fail(f"{source}.provenance", "unknown provenance")
    if root["status"] == "complete":
        _PAYLOAD_CHECKS[tier](root["payload"], f"{source}.payload")
    else:
        _dict(root["payload"], f"{source}.payload")
    return root


def validate_scenario_result(document: Any, source: str = "<scenario>") -> dict[str, Any]:
    try:
        return validate_scenario(document, source)
    except ScenarioError as exc:
        raise ValidationError(str(exc)) from exc
