"""Scenario-driven T1 movement drills."""
from __future__ import annotations

import math
from pathlib import Path
import sys
import time
from typing import Any

from .control import Control, ControlError
from .runlib import CvarRestore, RigLock, RunRecorder, connect
from .scenario import load_scenarios


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
    highest_z = -math.inf
    regotos = 0
    crossed = False
    fall_gate = scenario.get("fail", {}).get("fall_gate")
    crossing = scenario.get("fail", {}).get("crossing")
    while time.monotonic() - began < run["timeout_s"]:
        bots = control.request("status")["data"].get("bots", [])
        bot = next(
            (entry for entry in bots if int(entry.get("ent", -1)) == bot_id),
            None,
        )
        if bot is None or not bot.get("alive"):
            return {"status": "died", "time_s": None}
        position = bot["origin"]
        highest_z = max(highest_z, position[2])
        if (
            fall_gate
            and highest_z >= fall_gate["armed_z"]
            and position[2] < fall_gate["fail_z"]
        ):
            return {"status": "fell", "time_s": None}
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
                return {"status": "fell", "time_s": None}
        arrived = any(event.get("ev") == "arrived" for event in control.events)
        stalled = any(
            event.get("ev") == "goto_stall" for event in control.events
        )
        control.events.clear()
        if stalled:
            return {"status": "stall", "time_s": None}
        if arrived:
            if (
                abs(position[0] - target[0]) < run["arrive_box"]
                and abs(position[1] - target[1]) < run["arrive_box"]
            ):
                if crossing and not crossed:
                    return {"status": "detoured", "time_s": None}
                return {
                    "status": "passed",
                    "time_s": round(time.monotonic() - began, 2),
                }
            if regotos >= run["regoto_max"]:
                return {"status": "loop", "time_s": None}
            regotos += 1
            control.request(f"goto {bot_id} {_coordinates(target)}")
        time.sleep(0.07)
    return {"status": "timeout", "time_s": None}


def _scaled_required(required: int, full_attempts: int, attempts: int) -> int:
    if attempts == full_attempts:
        return required
    return max(1, round(attempts * required / full_attempts))


def _run_goto(
    control: Control,
    bot_id: int,
    scenario: dict[str, Any],
    quick: bool,
) -> dict[str, Any]:
    for link in scenario.get("setup", {}).get("plant_links", []):
        control.request(f"planlink {link}")
    full_attempts = scenario["run"]["attempts"]
    attempts_count = 3 if quick else full_attempts
    attempts = []
    for index in range(attempts_count):
        result = _outcome(control, bot_id, scenario)
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
    required = _scaled_required(
        scenario["threshold"]["required"], full_attempts, attempts_count
    )
    return {
        "name": scenario["name"],
        "attempts": attempts,
        "threshold": {"required": required, "of": attempts_count},
        "passed": passed,
        "verdict": "PASS" if passed >= required else "FAIL",
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
) -> tuple[dict[str, Any], Control]:
    control = _change_map(control, config, scenario["map"], 5.0)
    peaks = []
    for index in range(scenario["run"]["dashes"]):
        if scenario.get("workaround", {}).get("cycle_bot_count"):
            control.request("set rtx_bot_count 0")
            time.sleep(2.0)
            control.request("set rtx_bot_count 1")
            control.close()
            time.sleep(5.0)
            control = _reconnect(config)
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
            peak = max(peak, float(bot.get("speed", 0.0)))
            if any(
                event.get("ev") in {"arrived", "goto_stall"}
                for event in control.events
            ):
                break
            control.events.clear()
            time.sleep(0.05)
        rounded = round(peak)
        peaks.append(rounded)
        print(f"  {scenario['name']} {index + 1}: peak {rounded}", flush=True)
    return (
        {
            "peaks": peaks,
            "peak": max(peaks) if peaks else None,
            "floor": scenario["threshold"]["floor"],
            "informative": True,
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
                snapshot = restorer.snapshot
                control.request("set rtx_telemetry 1")
                bot_id = _wait_for_bots(control, 1)[0]
                results = [
                    _run_goto(control, bot_id, scenario, quick)
                    for scenario in goto_scenarios
                ]
                control.request(f"stop {bot_id}")
                control.request(f"hold {bot_id}")
                dash, control = _run_dash(control, config, dash_scenarios[0])
                control = _change_map(control, config, main_map, 6.0)
                payload = {
                    "scenarios": results,
                    "dash": dash,
                    "verdict": (
                        "PASS"
                        if all(result["verdict"] == "PASS" for result in results)
                        else "FAIL"
                    ),
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
