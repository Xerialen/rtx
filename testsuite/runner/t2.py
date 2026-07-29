"""T2 pacifist free-play measurement."""
from __future__ import annotations

from collections import Counter, defaultdict
import math
from pathlib import Path
import time
from typing import Any

from . import analyzer as analyzer_mod
from . import evidence as evidence_mod
from .control import Control, ControlError
from .runlib import (
    CvarRestore,
    RigLock,
    RunRecorder,
    config_path,
    connect,
    engine_declares,
    nav_preflight,
    nav_stamp,
)

NOLINK = 4_294_967_295

# Declared when the engine binary under test does not register `rtx_telemetry`.
# Everything T2 reads off `status` and the demo is unaffected; only what the
# engine would have had to emit is missing.
TELEMETRY_ABSENT = {
    "telemetry": False,
    "unavailable": ["stall_firings", "cells"],
    "note": "engine binary does not register rtx_telemetry; stall events are not emitted by this build",
}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


# The Items reply identifies powerups by Quake classname.
POWERUP_CLASSNAMES = {"quad": "super_damage", "pent": "invulnerability"}


def _item_name(item: dict[str, Any]) -> str:
    for key in ("name", "kind", "classname", "item"):
        value = item.get(key)
        if isinstance(value, str):
            return value.lower()
    return ""


def _observe_powerups(
    items: Any,
    now: float,
    powerups: dict[str, dict[str, Any]],
) -> None:
    if not isinstance(items, list):
        return
    for name, state in powerups.items():
        fragment = POWERUP_CLASSNAMES[name]
        item = next(
            (
                candidate
                for candidate in items
                if isinstance(candidate, dict) and fragment in _item_name(candidate)
            ),
            None,
        )
        if item is None or not isinstance(item.get("available"), bool):
            continue
        if item["available"]:
            if state["available_since"] is None:
                state["available_since"] = now
        elif state["available_since"] is not None:
            state["takes"].append(round(now - state["available_since"], 1))
            state["available_since"] = None


def _summarize_cells(stalls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cells: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "n": 0,
            "pos": None,
            "reasons": Counter(),
            "links": Counter(),
            "first_at_s": None,
            "first_ent": None,
        }
    )
    for event in stalls:
        identifier = f"m{int(event['cell'])}"
        cell = cells[identifier]
        cell["n"] += 1
        if cell["first_at_s"] is None and event.get("_at_s") is not None:
            cell["first_at_s"] = event["_at_s"]
            entity = event.get("ent")
            cell["first_ent"] = int(entity) if isinstance(entity, int) else None
        cell["reasons"][str(event["reason"])] += 1
        origin = event.get("origin")
        if cell["pos"] is None and isinstance(origin, list) and len(origin) == 3:
            cell["pos"] = [round(float(value), 1) for value in origin]
        link = int(event.get("link", NOLINK))
        if link != NOLINK:
            cell["links"][str(link)] += 1
    output = []
    for identifier, cell in sorted(
        cells.items(), key=lambda item: (-item[1]["n"], item[0])
    ):
        output.append(
            {
                "id": identifier,
                "pos": cell["pos"] or [0.0, 0.0, 0.0],
                "n": cell["n"],
                "reasons": dict(cell["reasons"]),
                "links": dict(cell["links"].most_common(4)),
                "first_at_s": cell["first_at_s"],
                "first_ent": cell["first_ent"],
            }
        )
    return output


def _collect(
    control: Control,
    duration_s: int,
    recording: evidence_mod.Recording | None = None,
    telemetry: bool = True,
) -> dict[str, Any]:
    speed_samples: list[float] = []
    per_second: list[float] = []
    still_s = 0.0
    still_streak: dict[int, tuple[float, float]] = {}
    longest_still: tuple[float, int, float] | None = None
    # Every metric we measure ourselves owes the reader a moment in the demo it
    # can be checked against. For the two speeds that is the fastest sample we
    # saw and which bot produced it.
    fastest_sample: tuple[float, int, float] | None = None
    fastest_second: tuple[float, int, float] | None = None
    bot_previous: dict[int, tuple[list[float], float]] = {}
    second_accumulator: dict[int, list[float | int]] = {}
    measured_bots = 0
    stalls: list[dict[str, Any]] = []
    powerups = {
        "quad": {"takes": [], "available_since": None},
        "pent": {"takes": [], "available_since": None},
    }
    began = time.monotonic()
    last_second = began
    last_telemetry_assert = began
    last_items_poll = 0.0
    polls = 0
    while time.monotonic() - began < duration_s:
        loop_began = time.monotonic()
        try:
            status = control.request("status", timeout=8.0)["data"]
        except Exception:
            time.sleep(0.5)
            continue
        polls += 1
        alive_bots = [
            bot for bot in status.get("bots", []) if bot.get("alive")
        ]
        measured_bots = max(measured_bots, len(alive_bots))
        for bot in status.get("bots", []):
            entity = int(bot["ent"])
            origin = bot["origin"]
            previous = bot_previous.get(entity)
            if bot.get("alive") and previous is not None:
                elapsed = loop_began - previous[1]
                if 0.01 < elapsed < 0.6:
                    speed = (
                        math.hypot(
                            origin[0] - previous[0][0],
                            origin[1] - previous[0][1],
                        )
                        / elapsed
                    )
                    if speed < 1500:
                        speed_samples.append(speed)
                        if fastest_sample is None or speed > fastest_sample[0]:
                            fastest_sample = (speed, entity, loop_began)
                        accumulator = second_accumulator.setdefault(
                            entity, [0.0, 0]
                        )
                        accumulator[0] += speed * elapsed
                        accumulator[1] += 1
                        if speed < 16:
                            still_s += elapsed
                            began_at, length = still_streak.get(
                                entity, (loop_began, 0.0)
                            )
                            length += elapsed
                            still_streak[entity] = (began_at, length)
                            if longest_still is None or length > longest_still[0]:
                                longest_still = (length, entity, began_at)
                        else:
                            still_streak.pop(entity, None)
            bot_previous[entity] = (origin, loop_began)
        if loop_began - last_second >= 1.0:
            elapsed = loop_began - last_second
            for entity, (distance, samples) in second_accumulator.items():
                if samples:
                    average = float(distance) / elapsed
                    per_second.append(average)
                    if fastest_second is None or average > fastest_second[0]:
                        fastest_second = (average, entity, last_second)
            second_accumulator = {}
            last_second = loop_began
        if loop_began - last_items_poll >= 0.5:
            last_items_poll = loop_began
            try:
                _observe_powerups(
                    control.request("items", timeout=8.0)["data"], loop_began, powerups
                )
            except ControlError:
                pass
        if telemetry and loop_began - last_telemetry_assert >= 10.0:
            control.request("set rtx_telemetry 1", timeout=4.0)
            last_telemetry_assert = loop_began
        for event in control.events:
            if event.get("ev") == "bot_stall":
                if recording is not None:
                    event = {**event, "_at_s": recording.at()}
                stalls.append(event)
        control.events.clear()
        time.sleep(max(0.0, 0.1 - (time.monotonic() - loop_began)))
    cells = _summarize_cells(stalls)
    bots = measured_bots
    if bots == 0:
        raise RuntimeError("T2 observed no bots")
    stats = {
        "quad_takes": len(powerups["quad"]["takes"]),
        "quad_lay_avg": _mean(powerups["quad"]["takes"]),
        "pent_takes": len(powerups["pent"]["takes"]),
        "pent_lay_avg": _mean(powerups["pent"]["takes"]),
        "speed_1s": _mean(per_second),
        "speed_100ms": _mean(speed_samples),
        "still_s_per_bot": round(still_s / bots, 1),
        # No instrumentation means no count, not a count of none: a zero here
        # would put the build that cannot see stalls at the top of the column.
        "stall_firings": len(stalls) if telemetry else None,
        "polls": polls,
        "bots": bots,
    }
    # One moment per metric we measured ourselves, each pointing at the sample
    # that produced the number: the longest stand-still, the fastest bot, and
    # the first firing in the zone that stalled most.
    own_moments: list[dict[str, Any]] = []

    def note(metric: str, peak: tuple[float, int, float] | None, detail: str) -> None:
        if peak is None or recording is None:
            return
        _, entity, at = peak
        own_moments.append(
            {"metric": metric, "entity": entity, "at_s": recording.at(at), "detail": detail}
        )

    if longest_still is not None:
        note("still_s_per_bot", longest_still, "längsta stillastående")
    note("speed_100ms", fastest_sample, "snabbaste 100 ms-provet")
    note("speed_1s", fastest_second, "snabbaste sekunden")
    if cells and cells[0]["first_at_s"] is not None and recording is not None:
        own_moments.append(
            {
                "metric": "stall_firings",
                "entity": cells[0]["first_ent"],
                "at_s": cells[0]["first_at_s"],
                "detail": f"första fyrningen i {cells[0]['id']}, den mest drabbade zonen",
            }
        )
    return {"stats": stats, "cells": cells, "own_moments": own_moments}


def _analyzer_metrics(
    config: dict[str, Any],
    recording: evidence_mod.Recording,
    stats: dict[str, Any],
    roster: list[dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Let the analyzer own every metric it can read off the demo.

    Whatever it answers replaces our own sampling of the same thing and says
    so in `sources`; whatever it cannot answer on this demo (a lab rig has no
    KTX block, so no server-side speeds) leaves our measurement standing.
    """
    sources: dict[str, str] = {}
    moments: list[dict[str, Any]] = []
    analyzer = analyzer_mod.open_analyzer(config, config_path)
    if analyzer is None or recording.path is None:
        return sources, moments
    try:
        demo_id = analyzer.plant(recording.path)
        measurements, skipped = analyzer.measure(demo_id)
    except analyzer_mod.AnalyzerError as exc:
        print(f"analyzer unavailable: {exc}", flush=True)
        return sources, moments
    for name, reason in skipped.items():
        print(f"analyzer skipped {name}: {reason}", flush=True)
    for key, measurement in measurements.items():
        if key in stats:
            stats[key] = measurement.value
            sources[key] = measurement.source
        for moment in measurement.moments:
            link = recording.link(
                moment.t_s, evidence_mod.userid_for_name(roster, moment.who or "")
            )
            if link is None:
                continue
            moments.append(
                {
                    "demo": recording.demo_name,
                    "metric": key,
                    "at_s": round(moment.t_s, 1),
                    "who": moment.who,
                    "link": link,
                }
            )
    return sources, moments


def _wait_for_bots(
    control: Control, wanted: int, timeout_s: float = 40.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = control.request("status")["data"]
        bots = status.get("bots", [])
        if sum(bool(bot.get("alive")) for bot in bots) >= wanted:
            return status
        time.sleep(1.0)
    raise TimeoutError(f"server did not expose {wanted} live bot(s)")


def run(
    config: dict[str, Any],
    *,
    duration_s: int | None = None,
    map_name: str = "dm3",
) -> Path:
    duration = config["t2"]["duration_s"] if duration_s is None else duration_s
    if duration <= 0:
        raise ValueError("T2 duration must be positive")
    port = config["server"]["control_port"]
    with RigLock(port):
        control = connect(config)
        initial_status = control.request("status")["data"]
        with RunRecorder(
            config, "T2", map_name, server_status=initial_status
        ) as recorder:
            try:
                with CvarRestore(
                    control,
                    ["rtx_telemetry", "rtx_bot_pacifist", "rtx_bot_count"],
                    baseline=config.get("restore", {}),
                ):
                    telemetry = engine_declares(
                        config, "rtx_telemetry", initial_status
                    ) is not False
                    if not telemetry:
                        recorder.capabilities = dict(TELEMETRY_ABSENT)
                        print(
                            "rtx_telemetry: build does not expose it — stall "
                            "firings will be reported as unmeasured",
                            flush=True,
                        )
                    else:
                        control.request("set rtx_telemetry 1")
                    control.request("set rtx_bot_pacifist 1")
                    # T2 counts navmesh cells, so a run started on a building
                    # graph would report zero of precisely the thing it exists
                    # to count. Wanting bots is what starts the build (the
                    # graph is built lazily), so the count has to be set before
                    # there is anything to verify.
                    control.request("set rtx_bot_count 4")
                    nav_status, waited_s = nav_preflight(control, map_name)
                    recorder.nav = nav_stamp(nav_status, waited_s)
                    _wait_for_bots(control, 4)
                    probe = control.request("items", timeout=8.0)["data"]
                    if not isinstance(probe, list):
                        raise RuntimeError(
                            "the Items control verb did not return an item list "
                            "required for T2 powerup statistics"
                        )
                    for name, fragment in POWERUP_CLASSNAMES.items():
                        if not any(
                            isinstance(i, dict) and fragment in _item_name(i)
                            for i in probe
                        ):
                            raise RuntimeError(
                                f"map has no {name} ({fragment}) in the Items reply"
                            )
                    recording = evidence_mod.open_recording(
                        control, recorder.run_id, config, map_name, config_path
                    )
                    recording.start()
                    try:
                        measured = _collect(
                            control, duration, recording, telemetry=telemetry
                        )
                    finally:
                        recording.stop()
                    roster = (
                        evidence_mod.players(recording.path)
                        if recording.path is not None
                        else []
                    )
                    stats = measured["stats"]
                    cells = measured["cells"]
                    own_moments = measured.pop("own_moments", [])
                    sources, moments = _analyzer_metrics(
                        config, recording, stats, roster
                    )
                    for cell in cells:
                        at_s = cell.pop("first_at_s", None)
                        cell.pop("first_ent", None)
                        link = recording.link(at_s)
                        cell["evidence"] = (
                            None
                            if link is None
                            else {
                                "demo": recording.demo_name,
                                "metric": "stall_firings",
                                "at_s": round(float(at_s), 1),
                                "link": link,
                            }
                        )
                    for own in own_moments:
                        entity = own.get("entity")
                        link = recording.link(
                            own["at_s"],
                            None
                            if entity is None
                            else evidence_mod.userid_for_slot(roster, entity - 1),
                        )
                        if link is not None:
                            moments.append(
                                {
                                    "demo": recording.demo_name,
                                    "metric": own["metric"],
                                    "at_s": round(float(own["at_s"]), 1),
                                    "detail": own.get("detail"),
                                    "link": link,
                                }
                            )
                    whole = recording.link(0.0, lead_s=0.0)
                    recorder.payload = {
                        "duration_s": duration,
                        "regime_note": None if duration == 600 else "smoke",
                        "stats": stats,
                        "cells": cells,
                        "demo": recording.demo_name,
                        "evidence": (
                            None
                            if whole is None
                            else {
                                "demo": recording.demo_name,
                                "at_s": 0.0,
                                "link": whole,
                            }
                        ),
                        "moments": moments,
                        "sources": sources,
                        "verdict": "MEASURED",
                    }
            finally:
                control.close()
    return recorder.path
