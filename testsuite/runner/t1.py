"""Scenario-driven T1 movement drills."""
from __future__ import annotations

import math
from pathlib import Path
import sys
import time
from typing import Any

from . import evidence as evidence_mod
from .control import Control, ControlError
from .runlib import (
    CvarRestore,
    RigLock,
    RunRecorder,
    config_path,
    connect,
    engine_declares,
)

from .scenario import load_scenarios

# Declared when the engine binary under test does not register
# `rtx_telemetry`: the drills all still run, but the `stall` outcome cannot
# occur on such a build.
TELEMETRY_ABSENT = {
    "telemetry": False,
    "unavailable": ["t1:stall"],
    "note": "engine binary does not register rtx_telemetry; a stalled attempt"
            " is recorded as a timeout",
}


def _coordinates(values: list[int | float]) -> str:
    return " ".join(f"{value:g}" for value in values)


def _wait_for_bots(
    control: Control, wanted: int, timeout_s: float = 40.0
) -> list[int]:
    control.request(f"set rtx_bot_count {wanted}")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        bots = control.request("status")["data"].get("bots", [])
        ids = sorted(int(bot["ent"]) for bot in bots if bot.get("alive"))
        if len(ids) >= wanted:
            return ids[:wanted]
        time.sleep(1.0)
    raise TimeoutError(f"server did not expose {wanted} live bot(s)")


def _outcome(
    control: Control,
    bot_id: int,
    scenario: dict[str, Any],
    recording: evidence_mod.Recording | None = None,
) -> dict[str, Any]:
    run = scenario["run"]
    start = run["start"]
    target = run["target"]
    control.request(f"stop {bot_id}")
    control.request(f"hold {bot_id}")
    control.request(f"teleport {bot_id} {_coordinates(start)}")
    time.sleep(1.0)
    control.events.clear()
    control.request(f"goto {bot_id} {_coordinates(target)}")
    began = time.monotonic()
    demo_t = recording.at(began) if recording is not None else None
    highest_z = -math.inf
    regotos = 0
    crossed = False
    fall_gate = scenario.get("fail", {}).get("fall_gate")
    crossing = scenario.get("fail", {}).get("crossing")
    # An attempt ends the moment it can no longer succeed, rather than when its
    # clock runs out. Two independent ways of knowing that:
    #
    # Impossible — from where the bot is and how fast anything has ever moved
    # here, it cannot reach the target before the deadline. Straight-line
    # distance understates the real path and the ceiling is above any speed
    # measured on this map, so the bound only ever fires when arriving is
    # genuinely out of reach.
    #
    # Wedged — the bound above goes quiet when a bot is stuck a few units from
    # the target, because the remaining distance is trivial. Ground covered
    # catches that: past the deadline's own time limit, a bot that has stopped
    # travelling has given up. It must be ground *covered*, not ground *gained*:
    # measuring distance to the target punished routes that swing wide before
    # they close and turned `slow 15 s` into a bare timeout.
    ceiling = run.get("speed_ceiling", 850.0)
    # Flat seconds, not a share of the limit: the cost being bounded is the
    # waiting, and waiting is measured in seconds. A multiplier would spend the
    # most extra time on the routes that are already the slowest, which is
    # exactly where the extra time is worth least.
    grace = run.get("give_up_grace_s", 5.0)
    limit = scenario["threshold"].get("max_time_s")
    deadline = limit + grace if limit is not None else run["timeout_s"]
    no_progress_s = run.get("no_progress_s", 4.0)
    late_after = limit if limit is not None else run["timeout_s"] / 2
    moved = 0.0
    window_began = began
    # Seeded with the point the bot was teleported to, so ground covered before
    # the first status sample counts. Starting from None credited the bot with
    # nothing until sample two and could cut a moving attempt on its first
    # window.
    previous_position: list[float] = list(start)

    # Height matters for arriving and not for giving up, so the two are
    # separate tests. `inside_column` is the old box: a square in X and Y of
    # unbounded height. Half the drills on dm3 have walkable ground on another
    # floor inside their own square — the RA targets have floor 344 units below
    # them — and a run of these drills put the bot in that square, on that
    # floor, twelve times. None of them was credited, but nothing here stopped
    # it: the only thing in the way was the engine's `arrived` not landing in
    # the same instant.
    arrive_z = run.get("arrive_z", 48.0)

    def inside_column(where: list[float]) -> bool:
        return (
            abs(where[0] - target[0]) < run["arrive_box"]
            and abs(where[1] - target[1]) < run["arrive_box"]
        )

    def inside_box(where: list[float]) -> bool:
        if not inside_column(where):
            return False
        # Zero means the drill is asking about a place rather than a floor, and
        # takes the old height-blind behaviour deliberately.
        return arrive_z <= 0 or abs(where[2] - target[2]) < arrive_z

    while time.monotonic() - began < run["timeout_s"]:
        bots = control.request("status")["data"].get("bots", [])
        bot = next(
            (entry for entry in bots if int(entry.get("ent", -1)) == bot_id),
            None,
        )
        if bot is None or not bot.get("alive"):
            return {"status": "died", "time_s": None, "demo_t_s": demo_t}
        position = bot["origin"]
        highest_z = max(highest_z, position[2])
        now = time.monotonic()
        # The give-up test itself waits until the bottom of the loop: an attempt
        # that fell, died or detoured should say so rather than be written off
        # as a bot that stopped moving.
        moved += math.dist(position, previous_position)
        previous_position = position
        if (
            fall_gate
            and highest_z >= fall_gate["armed_z"]
            and position[2] < fall_gate["fail_z"]
        ):
            return {"status": "fell", "time_s": None, "demo_t_s": demo_t}
        if crossing:
            bowl_y = crossing["bowl_y"]
            if (
                500 < position[0] < 652
                and abs(position[1] - bowl_y) < 130
                and position[2] > 40
                and not bot.get("on_ground")
            ):
                crossed = True
            if position[2] < 20 and 460 < position[0] < 692:
                return {"status": "fell", "time_s": None, "demo_t_s": demo_t}
        arrived = any(event.get("ev") == "arrived" for event in control.events)
        stalled = any(
            event.get("ev") == "goto_stall" for event in control.events
        )
        control.events.clear()
        if stalled:
            return {"status": "stall", "time_s": None, "demo_t_s": demo_t}
        if arrived:
            if inside_box(position):
                if crossing and not crossed:
                    return {"status": "detoured", "time_s": None, "demo_t_s": demo_t}
                elapsed = round(time.monotonic() - began, 2)
                # A timed drill is measured against a human run of the route:
                # arriving late is its own outcome, not a pass and not a miss.
                limit = scenario["threshold"].get("max_time_s")
                return {
                    "status": "slow" if limit is not None and elapsed > limit else "passed",
                    "time_s": elapsed,
                    "demo_t_s": demo_t,
                }
            if regotos >= run["regoto_max"]:
                return {"status": "loop", "time_s": None, "demo_t_s": demo_t}
            regotos += 1
            control.request(f"goto {bot_id} {_coordinates(target)}")
        # The soonest this attempt could still reach the target, if it travelled
        # the rest in a straight line at a speed nothing here has ever reached.
        # Distance is measured the way arrival is judged — on X and Y — so a bot
        # already standing in the arrive box cannot be written off over a height
        # difference while its `arrived` event is still in flight. That is why
        # both give-up tests ask `inside_column` and not `inside_box`: the
        # height requirement decides what counts as arriving, never what counts
        # as still trying.
        if ceiling > 0 and not inside_column(position):
            earliest = (now - began) + math.dist(position[:2], target[:2]) / ceiling
            if earliest > deadline:
                return {
                    "status": "timeout",
                    "time_s": None,
                    "demo_t_s": demo_t,
                    "min_possible_s": round(earliest, 2),
                }
        if no_progress_s > 0 and now - window_began >= no_progress_s:
            # A bot that is running covers hundreds of units in four seconds;
            # this floor only catches one that is wedged or spinning in place.
            # The window has to lie entirely after the arming point, or a bot
            # that stood still early would be cut on ground it was still
            # allowed to make up. A bot standing on the target is not wedged —
            # it is waiting for an `arrived` the engine has not sent — so the
            # same exemption the bound above has applies here too.
            if (
                window_began - began >= late_after
                and moved < 64.0
                and not inside_column(position)
            ):
                return {"status": "timeout", "time_s": None, "demo_t_s": demo_t}
            moved = 0.0
            window_began = now
        time.sleep(0.07)
    return {"status": "timeout", "time_s": None, "demo_t_s": demo_t}


def _scaled_required(required: int, full_attempts: int, attempts: int) -> int:
    if attempts == full_attempts:
        return required
    return max(1, round(attempts * required / full_attempts))


def _run_goto(
    control: Control,
    bot_id: int,
    scenario: dict[str, Any],
    quick: bool,
    recording: evidence_mod.Recording | None = None,
) -> dict[str, Any]:
    for link in scenario.get("setup", {}).get("plant_links", []):
        control.request(f"planlink {link}")
    full_attempts = scenario["run"]["attempts"]
    attempts_count = 3 if quick else full_attempts
    attempts = []
    for index in range(attempts_count):
        result = _outcome(control, bot_id, scenario, recording)
        attempts.append(result)
        suffix = (
            f" {result['time_s']}s" if result["time_s"] is not None else ""
        )
        print(
            f"  {scenario['name']} {index + 1}/{attempts_count}: "
            f"{result['status']}{suffix}",
            flush=True,
        )
        time.sleep(scenario["run"]["pause_s"])
    passed = sum(result["status"] == "passed" for result in attempts)
    arrived = sum(result["status"] in {"passed", "slow"} for result in attempts)
    times = [
        result["time_s"] for result in attempts if result["time_s"] is not None
    ]
    required = _scaled_required(
        scenario["threshold"]["required"], full_attempts, attempts_count
    )
    threshold = {"required": required, "of": attempts_count}
    for field in ("reference_time_s", "max_time_s"):
        if field in scenario["threshold"]:
            threshold[field] = scenario["threshold"][field]
    return {
        "name": scenario["name"],
        "category": scenario["category"],
        "place": scenario["place"],
        "attempts": attempts,
        "threshold": threshold,
        "passed": passed,
        "arrived": arrived,
        "best_time_s": min(times) if times else None,
        "verdict": "PASS" if passed >= required else "FAIL",
        "evidence": None,
    }


def _representative(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The attempt worth watching: the first failure, else the first pass.

    A drill that failed is explained by watching it fail; a drill that passed
    is evidenced by watching it work.
    """
    for attempt in attempts:
        if attempt["status"] != "passed":
            return attempt
    return attempts[0] if attempts else None


def _attach_evidence(
    results: list[dict[str, Any]],
    recording: evidence_mod.Recording | None,
    userid: int | None,
) -> None:
    """Give every drill a demo link, once the recording has been collected."""
    if recording is None or recording.demo_name is None:
        return
    for result in results:
        attempt = _representative(result["attempts"])
        if attempt is None:
            continue
        index = result["attempts"].index(attempt) + 1
        link = recording.link(attempt.get("demo_t_s"), userid)
        if link is None:
            continue
        result["evidence"] = {
            "demo": recording.demo_name,
            "attempt": index,
            "status": attempt["status"],
            "at_s": round(float(attempt["demo_t_s"]), 1),
            "link": link,
        }


def _reconnect(config: dict[str, Any], attempts: int = 12) -> Control:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return connect(config, timeout=20.0)
        except (OSError, ControlError) as exc:
            last_error = exc
            time.sleep(2.0)
    raise ConnectionError(f"control channel did not reconnect: {last_error}")


def _change_map(
    control: Control, config: dict[str, Any], map_name: str, settle_s: float
) -> Control:
    try:
        control.request(f"runcmd map {map_name}")
    except (ControlError, OSError):
        pass
    control.close()
    time.sleep(settle_s)
    return _reconnect(config)


def _run_dash(
    control: Control,
    config: dict[str, Any],
    scenario: dict[str, Any],
    run_id: str,
) -> tuple[dict[str, Any], Control]:
    control = _change_map(control, config, scenario["map"], 5.0)
    # The dash lives on its own map, so it gets its own demo: a recording
    # cannot survive the map change that brings us here.
    recording = evidence_mod.open_recording(
        control, f"t1dash-{run_id}", config, scenario["map"], config_path
    )
    recording.start()
    peaks = []
    peak_moments: list[float | None] = []
    for index in range(scenario["run"]["dashes"]):
        if scenario.get("workaround", {}).get("cycle_bot_count"):
            control.request("set rtx_bot_count 0")
            time.sleep(2.0)
            control.request("set rtx_bot_count 1")
            control.close()
            time.sleep(5.0)
            control = _reconnect(config)
            # The demo keeps running server-side; only our socket was recycled.
            recording.control = control
        bot_id = _wait_for_bots(control, 1)[0]
        control.request(
            f"teleport {bot_id} {_coordinates(scenario['run']['start'])}"
        )
        time.sleep(1.0)
        control.events.clear()
        control.request(
            f"goto {bot_id} {_coordinates(scenario['run']['target'])}"
        )
        began = time.monotonic()
        peak = 0.0
        peak_at: float | None = None
        while time.monotonic() - began < scenario["run"]["timeout_s"]:
            bots = control.request("status")["data"].get("bots", [])
            bot = next(
                (
                    entry
                    for entry in bots
                    if int(entry.get("ent", -1)) == bot_id
                ),
                None,
            )
            if bot is None:
                break
            speed = float(bot.get("speed", 0.0))
            if speed > peak:
                peak = speed
                peak_at = recording.at()
            if any(
                event.get("ev") in {"arrived", "goto_stall"}
                for event in control.events
            ):
                break
            control.events.clear()
            time.sleep(0.05)
        rounded = round(peak)
        peaks.append(rounded)
        peak_moments.append(peak_at)
        print(f"  {scenario['name']} {index + 1}: peak {rounded}", flush=True)
    control.request("set rtx_bot_count 0")
    recording.stop()
    best = max(range(len(peaks)), key=lambda i: peaks[i]) if peaks else None
    dash_evidence = None
    if best is not None and recording.demo_name is not None:
        roster = evidence_mod.players(recording.path) if recording.path else []
        link = recording.link(
            peak_moments[best], evidence_mod.userid_for_slot(roster, 0)
        )
        if link is not None:
            dash_evidence = {
                "demo": recording.demo_name,
                "dash": best + 1,
                "at_s": round(float(peak_moments[best]), 1),
                "link": link,
            }
    floor = scenario["threshold"]["floor"]
    informative = scenario["threshold"]["informative"]
    peak = max(peaks) if peaks else None
    return (
        {
            "peaks": peaks,
            "peak": peak,
            "floor": floor,
            "informative": informative,
            "verdict": (
                None
                if informative
                else ("PASS" if peak is not None and peak >= floor else "FAIL")
            ),
            "place": scenario["place"],
            "evidence": dash_evidence,
        },
        control,
    )


def _restore_with_reconnect(
    config: dict[str, Any],
    control: Control | None,
    snapshot: dict[str, str],
) -> None:
    if not snapshot:
        return
    errors = []
    if control is not None:
        try:
            for name, value in snapshot.items():
                control.request(f"set {name} {value}", timeout=8.0)
            return
        except Exception as exc:
            errors.append(str(exc))
    replacement = None
    try:
        replacement = _reconnect(config, attempts=3)
        for name, value in snapshot.items():
            replacement.request(f"set {name} {value}", timeout=8.0)
        return
    except Exception as exc:
        errors.append(str(exc))
    finally:
        if replacement is not None:
            replacement.close()
    raise RuntimeError("failed to restore cvars after T1: " + "; ".join(errors))


def run(
    config: dict[str, Any],
    scenarios_path: str | Path,
    *,
    quick: bool = False,
) -> Path:
    scenarios = load_scenarios(scenarios_path)
    goto_scenarios = [item for item in scenarios if item["kind"] == "goto"]
    dash_scenarios = [item for item in scenarios if item["kind"] == "dash"]
    if not goto_scenarios:
        raise ValueError("T1 requires at least one goto scenario")
    if len(dash_scenarios) != 1:
        raise ValueError("T1 requires exactly one informative dash scenario")
    maps = {item["map"] for item in goto_scenarios}
    if len(maps) != 1:
        raise ValueError("all goto scenarios in one T1 run must use the same map")
    main_map = next(iter(maps))
    port = config["server"]["control_port"]
    control: Control | None = None
    snapshot: dict[str, str] = {}
    with RigLock(port):
        control = connect(config)
        initial_status = control.request("status")["data"]
        with RunRecorder(
            config, "T1", main_map, server_status=initial_status
        ) as recorder:
            try:
                restorer = CvarRestore(
                    control,
                    ["rtx_telemetry", "rtx_bot_count"],
                    baseline=config.get("restore", {}),
                )
                restorer.__enter__()
                snapshot = restorer.restorable()
                if engine_declares(config, "rtx_telemetry", initial_status) is not False:
                    control.request("set rtx_telemetry 1")
                else:
                    # Arrival is read off `status`, so every drill is still
                    # graded exactly as before. What changes is that an attempt
                    # the engine would have called a stall lands as a timeout,
                    # and a reader comparing columns has to be told that.
                    recorder.capabilities = dict(TELEMETRY_ABSENT)
                    print(
                        "rtx_telemetry: build does not expose it — a stalled "
                        "attempt will be recorded as a timeout",
                        flush=True,
                    )
                bot_id = _wait_for_bots(control, 1)[0]
                recording = evidence_mod.open_recording(
                    control, recorder.run_id, config, main_map, config_path
                )
                recording.start()
                results = [
                    _run_goto(control, bot_id, scenario, quick, recording)
                    for scenario in goto_scenarios
                ]
                control.request(f"stop {bot_id}")
                control.request(f"hold {bot_id}")
                recording.stop()
                roster = (
                    evidence_mod.players(recording.path)
                    if recording.path is not None
                    else []
                )
                _attach_evidence(
                    results,
                    recording,
                    evidence_mod.userid_for_slot(roster, bot_id - 1),
                )
                dash, control = _run_dash(
                    control, config, dash_scenarios[0], recorder.run_id
                )
                control = _change_map(control, config, main_map, 6.0)
                drills_pass = all(result["verdict"] == "PASS" for result in results)
                dash_pass = dash["verdict"] != "FAIL"
                payload = {
                    "scenarios": results,
                    "dash": dash,
                    "demo": recording.demo_name,
                    "verdict": "PASS" if drills_pass and dash_pass else "FAIL",
                }
                if quick:
                    payload["regime_note"] = "quick"
                recorder.payload = payload
            finally:
                active_failure = sys.exc_info()[0] is not None
                try:
                    _restore_with_reconnect(config, control, snapshot)
                except Exception as restore_error:
                    if not active_failure:
                        raise
                    print(
                        f"warning: cvar restoration also failed: {restore_error}",
                        file=sys.stderr,
                    )
                finally:
                    if control is not None:
                        control.close()
    return recorder.path
