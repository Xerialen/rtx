#!/usr/bin/env python3
"""Build the static rtx T0–T4 dashboard from evidence envelopes.

The builder intentionally knows only the public rtx-testflow/1 contract.  It
does not import runner code and it never treats a non-complete envelope as
payload data.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KNOWN_SCHEMA = re.compile(r"^rtx-testflow/(\d+)$")
LEVELS = ("t0", "t1", "t2", "t3", "t4")

# Written into the evidence directory by the runner, but not evidence:
# skipped without complaint rather than rejected as unknown.
CONTROL_SCHEMA = "rtx-testflow-control/1"
RETRACTION_SCHEMA = "retraction/1"
COMPANION_SCHEMAS = frozenset({"rtx-sweep/1", CONTROL_SCHEMA, RETRACTION_SCHEMA})
#: Sidokontrollpost: en oberoende grind som dömer ETT kuvert utan att röra det.
#: Kuvert redigeras aldrig; når en grind en annan slutsats än kuvertets egen
#: självdeklaration är det sidoposten som bär den, med sitt underlag namngivet.
LADDER_SKILLS = (10, 12, 14, 16, 18, 20)
MISSING = "EJ KÖRD"


@dataclass(frozen=True)
class LoadedEvidence:
    path: Path
    document: dict[str, Any]


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def json_literal(value: Any) -> str:
    """Return JSON safe inside an HTML script element."""
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).replace("<", "\\u003c")


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("top-level JSON value is not an object")
    return value


def discover(evidence_dir: Path) -> tuple[list[LoadedEvidence], list[str]]:
    """Load supported envelopes in lexical file order, warning on rejects."""
    loaded: list[LoadedEvidence] = []
    warnings: list[str] = []
    for path in sorted(evidence_dir.glob("*.json")):
        try:
            document = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            message = f"{path}: rejected: {exc}"
            warnings.append(message)
            warn(message)
            continue
        schema = document.get("schema")
        # A sweep manifest describes a set of runs rather than being one, and it
        # lives in the same directory by design. Warning about it every build
        # trains the reader to ignore the warnings that matter.
        if schema in COMPANION_SCHEMAS:
            continue
        match = KNOWN_SCHEMA.fullmatch(schema) if isinstance(schema, str) else None
        if not match:
            message = f"{path}: rejected unknown schema {schema!r}"
            warnings.append(message)
            warn(message)
            continue
        if int(match.group(1)) != 1:
            message = f"{path}: rejected unknown rtx-testflow major {match.group(1)}"
            warnings.append(message)
            warn(message)
            continue
        loaded.append(LoadedEvidence(path, document))
    return loaded, warnings


def iso_sort_key(value: Any) -> tuple[int, str]:
    if not isinstance(value, str):
        return (0, "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (1, parsed.astimezone(timezone.utc).isoformat())
    except ValueError:
        return (0, value)


def text(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def host_relative(value: Any) -> str | None:
    """Keep only host-relative links; anything else is not ours to render.

    `//host/path` also starts with a slash but is protocol-relative: the browser
    would fetch it from another origin entirely.
    """
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    return None if value.startswith("//") else value


EVIDENCE_EXTRAS = ("attempt", "status", "dash", "metric", "who", "detail")


def normalize_evidence(value: Any) -> dict[str, Any] | None:
    """A demo-player pointer for one number, or None when it cannot be shown."""
    if not isinstance(value, dict):
        return None
    link = host_relative(value.get("link"))
    if link is None:
        return None
    evidence: dict[str, Any] = {
        "demo": value.get("demo") if isinstance(value.get("demo"), str) else None,
        "at_s": number(value.get("at_s")),
        "link": link,
    }
    for key in EVIDENCE_EXTRAS:
        if isinstance(value.get(key), (str, int, float)) and not isinstance(
            value.get(key), bool
        ):
            evidence[key] = value[key]
    return evidence


# The hub game page's columns, in its own order, plus the two of our own
# (speed, spree) that ride along for the panels that want them.
SCOREBOARD_INTS = (
    "frags", "efficiency", "kills", "spawn_frags", "deaths", "suicides", "tk",
    "dmg_given", "dmg_taken", "dmg_enemy_weapons", "taken_to_die",
    "ga", "ya", "ra", "mh", "quad", "pent", "ring",
    "sg_acc", "lg_acc", "rl_direct",
    "lg_taken", "lg_kills", "lg_dropped", "rl_taken", "rl_kills", "rl_dropped",
    "ping", "top_color", "bottom_color", "spree_max",
)
SCOREBOARD_NUMBERS = ("speed_max", "speed_avg")


def normalize_scoreboard(value: Any) -> dict[str, Any] | None:
    """The match card: team result plus one line per player, or None."""
    if not isinstance(value, dict):
        return None
    teams: list[dict[str, Any]] = []
    for raw in value.get("teams") if isinstance(value.get("teams"), list) else []:
        if not isinstance(raw, dict):
            continue
        team = {"name": text(raw.get("name"), "?")}
        for field in SCOREBOARD_INTS + SCOREBOARD_NUMBERS:
            team[field] = number(raw.get(field))
        teams.append(team)
    players: list[dict[str, Any]] = []
    for raw in value.get("players") if isinstance(value.get("players"), list) else []:
        if not isinstance(raw, dict):
            continue
        player: dict[str, Any] = {
            "name": text(raw.get("name"), "?"),
            "team": text(raw.get("team"), ""),
            "link": host_relative(raw.get("link")),
        }
        for field in SCOREBOARD_INTS + SCOREBOARD_NUMBERS:
            player[field] = number(raw.get(field))
        players.append(player)
    if not teams and not players:
        return None
    return {
        "teams": teams,
        "players": players,
        "map": value.get("map") if isinstance(value.get("map"), str) else None,
        "duration_s": number(value.get("duration_s")),
        "mode": value.get("mode") if isinstance(value.get("mode"), str) else None,
        "hostname": value.get("hostname") if isinstance(value.get("hostname"), str) else None,
        "date": value.get("date") if isinstance(value.get("date"), str) else None,
        "demo": value.get("demo") if isinstance(value.get("demo"), str) else None,
        "source": value.get("source") if isinstance(value.get("source"), str) else None,
        "link": host_relative(value.get("link")),
    }


def unplayed_rung(skill: int) -> dict[str, Any]:
    return {
        "skill": skill,
        "state": "unplayed",
        "for": None,
        "against": None,
        "scoreboard": None,
    }


def default_level(level: str) -> dict[str, Any]:
    common: dict[str, Any] = {
        "status": "missing",
        "verdict": MISSING,
        "key": "saknas",
        "synthetic": False,
        "provenance": None,
        "runId": None,
        "startedUtc": None,
        "error": None,
        "regime": level.upper(),
        "regimeNote": None,
        "comparisonKey": f"{level}:full",
        "sources": [],
        "snapshotIds": [],
        # What the build behind this column could not be asked about. Null is
        # the common case and means everything the tier needed was there.
        "capabilities": None,
        # Satt av en sidokontrollpost nar en oberoende grind domt kuvertet.
        # Null = ingen grind har sagt emot kuvertets egen sjalvdeklaration.
        "gateNote": None,
        "gateSource": None,
        # Rubrik over noten. En kontrollpost behover inte vara en fallen grind:
        # den kan ocksa ratta ETIKETTEN pa tal som star kvar. Null = sidans
        # gamla formulering ("Likhetsgrind foll"), sa T2:s post ser ut som forr.
        "gateLabel": None,
        # Retraherade kuvert i samma grupp: visas som forsok, aldrig som valda.
        "retractions": [],
    }
    if level == "t0":
        common.update(modules=[], qualityFloors=[], total={"tests": None, "passed": None})
    elif level == "t1":
        common["data"] = {
            "drills": [],
            "dash": {
                "peaks": [],
                "peak": None,
                "floor": None,
                "informative": True,
                "verdict": None,
                "place": None,
                "evidence": None,
            },
            "note": None,
            "demo": None,
            # The graph the run measured against. Absent by default: every
            # envelope before this stamp existed, and every T1 that never
            # reached connect, has nothing to say here.
            "nav": None,
        }
    elif level == "t2":
        common["stats"] = {
            "quad_takes": None,
            "quad_lay_avg": None,
            "pent_takes": None,
            "pent_lay_avg": None,
            "speed_1s": None,
            "speed_100ms": None,
            "still_s_per_bot": None,
            "stall_firings": None,
            "duration_s": None,
        }
        common.update(
            demo=None,
            evidence=None,
            moments=[],
            cellEvidence=[],
            metricSources={},
            # Same absence-by-default as T1's data.nav — see there for why.
            nav=None,
        )
    elif level == "t3":
        empty_side = {
            "speed_1s": None,
            "still_s_per_bot": None,
            "stall_firings": None,
            "combat_lock_s_per_bot": None,
        }
        common.update(
            kind="pipeline",
            score={"branch": None, "main": None},
            sides={"branch": dict(empty_side), "main": dict(empty_side)},
            aggregate=None,
            scoreboard=None,
        )
    elif level == "t4":
        common.update(
            reached=None,
            rungs=[unplayed_rung(skill) for skill in LADDER_SKILLS],
        )
    return common


def sources_from(payload: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []

    def visit(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, child in sorted(value.items()):
                field = f"{prefix}.{key}" if prefix else key
                if key.endswith("_source") and isinstance(child, str) and child:
                    sources.append({"field": field, "source": child})
                else:
                    visit(child, field)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{prefix}[{index}]")

    visit(payload)
    return sources


def normalize_capabilities(value: Any) -> dict[str, Any] | None:
    """The declaration that some number in this column was never measured.

    A malformed block is dropped rather than half-trusted: a partial excuse
    would let the page show a missing measurement as if it were explained.
    """
    if not isinstance(value, dict):
        return None
    unavailable = [
        name for name in value.get("unavailable", []) if isinstance(name, str) and name
    ]
    note = value.get("note")
    if not unavailable or not isinstance(note, str) or not note.strip():
        return None
    return {
        "telemetry": value.get("telemetry") is not False,
        "unavailable": unavailable,
        "note": note.strip(),
    }


def normalize_requires(value: Any) -> dict[str, Any] | None:
    """Why a drill carries no verdict: a capability the build never had.

    Dropped rather than half-trusted, for the same reason as the capabilities
    block: an unexplained absence must not be able to look like an explained
    one. A drill whose requirement was met keeps the block too — it is what
    tells a reader the graded columns were graded against the same map.
    """
    if not isinstance(value, dict):
        return None
    capability = value.get("capability")
    if not isinstance(capability, str) or not capability.strip():
        return None
    if value.get("state") not in {"present", "absent", "unknown"}:
        return None
    note = value.get("note")
    return {
        "capability": capability.strip(),
        "state": value["state"],
        "note": note.strip() if isinstance(note, str) else "",
    }


def normalize_nav(value: Any) -> dict[str, Any] | None:
    """The navmesh a T1/T2 run was measured against — provenance, not a result.

    checks.py enforces the schema on envelopes before they are trusted (state
    must be "ready", cells must be positive, and so on); the dashboard does
    none of that here, it only checks the shape is one it can render. Every
    envelope written before this stamp existed has no `nav` at all, and that
    must render exactly as it always has — so absence and a wrong-shaped
    block both degrade to None rather than a half-drawn panel or a crash.
    """
    if not isinstance(value, dict):
        return None
    map_name = value.get("map")
    state = value.get("state")
    cells = number(value.get("cells"))
    links = number(value.get("links"))
    rj_links = number(value.get("rj_links"))
    waited_s = number(value.get("waited_s"))
    if (
        not isinstance(map_name, str)
        or not map_name
        or not isinstance(state, str)
        or not state
        or cells is None
        or links is None
        or rj_links is None
        or waited_s is None
    ):
        return None
    return {
        "map": map_name,
        "state": state,
        "cells": cells,
        "links": links,
        "rjLinks": rj_links,
        "waitedS": waited_s,
    }


def base_level(level: str, envelope: dict[str, Any]) -> dict[str, Any]:
    item = default_level(level)
    status = text(envelope.get("status"), "incomplete").lower()
    provenance = envelope.get("provenance")
    item.update(
        status=status,
        synthetic=provenance == "synthetic",
        provenance=provenance if isinstance(provenance, str) else None,
        runId=envelope.get("run_id"),
        startedUtc=envelope.get("started_utc"),
        error=envelope.get("error"),
        capabilities=normalize_capabilities(envelope.get("capabilities")),
    )
    if status != "complete":
        shown = status.upper() if status in {"failed", "aborted"} else "OFULLSTÄNDIG"
        item.update(verdict=shown, key=shown.lower())
        return item
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        item.update(status="incomplete", verdict="OFULLSTÄNDIG", key="payload saknas")
        return item
    item["sources"] = sources_from(payload)
    return item


def incomplete_level(
    level: str, envelope: dict[str, Any], reason: str
) -> dict[str, Any]:
    item = default_level(level)
    provenance = envelope.get("provenance")
    item.update(
        status="incomplete",
        verdict="OFULLSTÄNDIG",
        key="ofullständig",
        synthetic=provenance == "synthetic",
        provenance=provenance if isinstance(provenance, str) else None,
        runId=envelope.get("run_id"),
        startedUtc=envelope.get("started_utc"),
        error=reason,
    )
    return item


def t0_level(envelope: dict[str, Any]) -> dict[str, Any]:
    item = base_level("t0", envelope)
    if item["status"] != "complete":
        return item
    payload = envelope["payload"]
    if (
        not isinstance(payload.get("modules"), list)
        or not isinstance(payload.get("quality_floors"), list)
        or not isinstance(payload.get("total"), dict)
        or payload.get("verdict") not in {"PASS", "FAIL"}
    ):
        return incomplete_level("t0", envelope, "T0-payload följer inte kontraktet")
    modules = payload.get("modules") if isinstance(payload.get("modules"), list) else []
    floors = (
        payload.get("quality_floors")
        if isinstance(payload.get("quality_floors"), list)
        else []
    )
    total = payload.get("total") if isinstance(payload.get("total"), dict) else {}
    verdict = payload.get("verdict") if payload.get("verdict") in {"PASS", "FAIL"} else "OFULLSTÄNDIG"
    item.update(
        verdict=verdict,
        key=f"{number(total.get('passed')) or 0}/{number(total.get('tests')) or 0} tester",
        modules=modules,
        qualityFloors=floors,
        total=total,
    )
    return item


def t1_level(envelope: dict[str, Any]) -> dict[str, Any]:
    item = base_level("t1", envelope)
    if item["status"] != "complete":
        return item
    payload = envelope["payload"]
    if (
        not isinstance(payload.get("scenarios"), list)
        or not isinstance(payload.get("dash"), dict)
        or payload.get("verdict") not in {"PASS", "FAIL"}
    ):
        return incomplete_level("t1", envelope, "T1-payload följer inte kontraktet")
    scenarios = payload.get("scenarios") if isinstance(payload.get("scenarios"), list) else []
    drills: list[dict[str, Any]] = []
    passed_scenarios = 0
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        threshold = scenario.get("threshold") if isinstance(scenario.get("threshold"), dict) else {}
        attempts = [
            {
                "status": text(raw.get("status"), "okänt"),
                "time_s": number(raw.get("time_s")),
                "demo_t_s": number(raw.get("demo_t_s")),
                "min_possible_s": number(raw.get("min_possible_s")),
            }
            for raw in (scenario.get("attempts") or [])
            if isinstance(raw, dict)
        ]
        verdict = scenario.get("verdict")
        passed_scenarios += verdict == "PASS"
        category = scenario.get("category")
        drills.append(
            {
                "name": text(scenario.get("name"), "okänt scenario"),
                "threshold": number(threshold.get("required")),
                "of": number(threshold.get("of")),
                "results": attempts,
                "verdict": verdict,
                # Envelopes before the evidence contract carry no category; they
                # render as ordinary map drills instead of vanishing.
                "category": category if category in {"grunddrill", "cellprov"} else "grunddrill",
                "place": scenario.get("place") if isinstance(scenario.get("place"), str) else None,
                "evidence": normalize_evidence(scenario.get("evidence")),
                # The timed drills carry the owner's own time on the route and
                # the slowest arrival still counted as a pass. Older envelopes
                # have neither, and then the drill is scored on arrivals alone.
                "referenceTime": number(threshold.get("reference_time_s")),
                "maxTime": number(threshold.get("max_time_s")),
                "arrived": number(scenario.get("arrived")),
                "bestTime": number(scenario.get("best_time_s")),
                # Present only on drills that named a capability. `absent` is
                # the one that withholds the verdict; the others are recorded
                # so the page can say what the drill was graded against.
                "requires": normalize_requires(scenario.get("requires")),
            }
        )
    regime_note = payload.get("regime_note")
    regime_note = regime_note if isinstance(regime_note, str) and regime_note else None
    verdict = payload.get("verdict") if payload.get("verdict") in {"PASS", "FAIL"} else "OFULLSTÄNDIG"
    dash = payload.get("dash") if isinstance(payload.get("dash"), dict) else {}
    # The denominator is the drills that were actually asked. Counting a
    # withheld drill among them would report the build as failing one, which is
    # the reading the whole mechanism exists to prevent.
    graded = sum(1 for drill in drills if drill["verdict"] in {"PASS", "FAIL"})
    unasked = len(drills) - graded
    key = f"{passed_scenarios}/{graded} drillar"
    if unasked:
        key += f" · {unasked} avstådd" + ("a" if unasked > 1 else "")
    item.update(
        verdict=verdict,
        key=key,
        regimeNote=regime_note,
        comparisonKey=f"t1:{regime_note or 'full'}",
        data={
            "drills": drills,
            "dash": {
                "peaks": dash.get("peaks") if isinstance(dash.get("peaks"), list) else [],
                "peak": number(dash.get("peak")),
                "floor": number(dash.get("floor")),
                "informative": dash.get("informative") is True,
                "verdict": dash.get("verdict") if dash.get("verdict") in {"PASS", "FAIL"} else None,
                "place": dash.get("place") if isinstance(dash.get("place"), str) else None,
                "evidence": normalize_evidence(dash.get("evidence")),
            },
            "note": payload.get("note") if isinstance(payload.get("note"), str) else None,
            "demo": payload.get("demo") if isinstance(payload.get("demo"), str) else None,
            # Envelope-level, beside build — the graph the preflight waited
            # for, and found ready, before any drill ran.
            "nav": normalize_nav(envelope.get("nav")),
        },
    )
    return item


def dominant_reason(reasons: dict[str, Any]) -> str:
    numeric = [(number(value) or 0, key) for key, value in reasons.items()]
    return max(numeric, default=(0, "displacement"))[1]


def normalize_cells(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cells: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        reasons = raw.get("reasons") if isinstance(raw.get("reasons"), dict) else {}
        links = raw.get("links") if isinstance(raw.get("links"), dict) else {}
        cell = {
            "cell": raw.get("id", raw.get("cell")),
            "pos": raw.get("pos") if isinstance(raw.get("pos"), list) else None,
            "n": number(raw.get("n")) or 0,
            "reasons": {
                str(key): number(count) or 0 for key, count in reasons.items()
            },
            "links": links,
            "reason": text(raw.get("reason"), dominant_reason(reasons)),
            "samples": raw.get("samples") if isinstance(raw.get("samples"), list) else [],
            "evidence": normalize_evidence(raw.get("evidence")),
        }
        for optional in (
            "phases",
            "speed_med",
            "speed_max",
            "before_med",
            "before_slow",
        ):
            if optional in raw:
                cell[optional] = raw[optional]
        cells.append(cell)
    return cells


def snapshot(
    *,
    snapshot_id: str,
    run_number: int,
    envelope: dict[str, Any],
    label: str,
    branch: str,
    build: str,
    regime: str,
    stats: dict[str, Any],
    cells: list[dict[str, Any]],
) -> dict[str, Any]:
    link_totals: dict[str, int | float] = {}
    for cell in cells:
        for link_id, count in cell.get("links", {}).items():
            numeric = number(count)
            if numeric is not None:
                key = str(link_id)
                link_totals[key] = link_totals.get(key, 0) + numeric
    started = text(envelope.get("started_utc"), "")
    return {
        "id": snapshot_id,
        "run": run_number,
        "time": started[11:16] if len(started) >= 16 else "",
        "date": started[:10],
        "label": label,
        "branch": branch,
        "build": build,
        "regime": regime,
        "stats": stats,
        "cells": cells,
        "linktotals": link_totals,
    }


def t2_level(
    envelope: dict[str, Any],
    snapshot_id: str,
    snapshot_number: int,
    branch: str,
    build: str,
    control: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    item = base_level("t2", envelope)
    if item["status"] != "complete":
        return item, []
    payload = envelope["payload"]
    if (
        not isinstance(payload.get("stats"), dict)
        or not isinstance(payload.get("cells"), list)
        or payload.get("verdict") != "MEASURED"
    ):
        return (
            incomplete_level("t2", envelope, "T2-payload följer inte kontraktet"),
            [],
        )
    raw_stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    stats = dict(raw_stats)
    stats["duration_s"] = number(payload.get("duration_s"))
    regime_note = payload.get("regime_note")
    regime_note = regime_note if isinstance(regime_note, str) and regime_note else None
    cells = normalize_cells(payload.get("cells"))
    raw_moments = payload.get("moments") if isinstance(payload.get("moments"), list) else []
    moments = [
        evidence for evidence in map(normalize_evidence, raw_moments) if evidence
    ]
    cell_evidence = [cell["evidence"] for cell in cells if cell.get("evidence")]
    raw_sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    metric_sources = {
        str(name): source
        for name, source in raw_sources.items()
        if isinstance(source, str) and source
    }
    snap_id = f"{snapshot_id}:t2"
    firings = number(raw_stats.get("stall_firings"))
    # En sidokontrollpost kan ha dömt kuvertet utan att röra det. Kuvertets egen
    # `verdict: MEASURED` är en självdeklaration; föll en oberoende likhetsgrind
    # är tiern ett FÖRSÖK, och sidan får inte kalla den mätt. Talen står kvar —
    # det är slutsatsen om dem som ändras.
    gate_failed = bool(control) and text(control.get("result"), "").upper() != "PASS"
    gate_note = None
    if gate_failed:
        detail = control.get("mismatches")
        detail = ", ".join(str(x) for x in detail) if isinstance(detail, list) else ""
        gate_note = " · ".join(
            part
            for part in (
                text(control.get("gate"), "oberoende grind") + " föll",
                text(control.get("reason"), ""),
                detail,
            )
            if part
        )
    base_key = "stall ej mätbar" if firings is None else f"{firings} stall"
    item.update(
        verdict="FÖRSÖK" if gate_failed else "MÄTT",
        # The one-line summary is the first thing read, so it must not turn an
        # absent measurement into a best-in-class zero.
        key=f"{base_key} · likhetsgrind föll" if gate_failed else base_key,
        gateNote=gate_note,
        gateSource=text(control.get("source"), "") or None if control else None,
        stats=stats,
        regimeNote=regime_note,
        comparisonKey=f"t2:{regime_note or 'full'}",
        snapshotIds=[snap_id] if cells else [],
        demo=payload.get("demo") if isinstance(payload.get("demo"), str) else None,
        evidence=normalize_evidence(payload.get("evidence")),
        moments=moments,
        cellEvidence=cell_evidence,
        metricSources=metric_sources,
        # Envelope-level, beside build — T2 counts navmesh cells, so this is
        # the graph the count itself was taken from.
        nav=normalize_nav(envelope.get("nav")),
    )
    return item, [
        snapshot(
            snapshot_id=snap_id,
            run_number=snapshot_number,
            envelope=envelope,
            label="T2 pacifist",
            branch=branch,
            build=build,
            regime="T2",
            stats=stats,
            cells=cells,
        )
    ]


def side_metrics(side: dict[str, Any], combat: Any, side_name: str) -> dict[str, Any]:
    stats = side.get("stats") if isinstance(side.get("stats"), dict) else {}
    result = dict(stats)
    combat_values = (
        combat.get("s_per_bot")
        if isinstance(combat, dict) and isinstance(combat.get("s_per_bot"), dict)
        else {}
    )
    result["combat_lock_s_per_bot"] = number(combat_values.get(side_name))
    return result


def t3_level(
    envelope: dict[str, Any],
    snapshot_id: str,
    snapshot_number: int,
    fallback_branch: str,
    fallback_build: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    item = base_level("t3", envelope)
    tier = envelope.get("tier")
    if item["status"] != "complete":
        item["kind"] = "aggregate" if tier == "T3-agg" else "pipeline"
        return item, []
    payload = envelope["payload"]
    if tier == "T3-agg":
        replicates = payload.get("replicates") if isinstance(payload.get("replicates"), list) else []
        if payload.get("verdict") not in {"PASS", "FAIL"} or len(replicates) < 2:
            invalid = incomplete_level(
                "t3", envelope, "T3-agg kräver PASS/FAIL och minst två replikat"
            )
            invalid["kind"] = "aggregate"
            return invalid, []
        verdict = payload["verdict"]
        item.update(
            kind="aggregate",
            verdict=verdict,
            key=f"{len(replicates)} replikat",
            aggregate=payload,
        )
        return item, []
    raw_sides = payload.get("sides") if isinstance(payload.get("sides"), list) else []
    if payload.get("verdict") != "PIPELINE-OK" or len(raw_sides) != 2:
        return (
            incomplete_level("t3", envelope, "T3-payload följer inte kontraktet"),
            [],
        )
    sides = {
        side.get("side"): side
        for side in raw_sides
        if isinstance(side, dict) and side.get("side") in {"branch", "reference"}
    }
    branch_side = sides.get("branch", {})
    reference_side = sides.get("reference", {})
    combat = payload.get("combat_lock")
    branch_stats = side_metrics(branch_side, combat, "branch")
    reference_stats = side_metrics(reference_side, combat, "reference")
    branch_build = branch_side.get("build") if isinstance(branch_side.get("build"), dict) else {}
    reference_build = (
        reference_side.get("build") if isinstance(reference_side.get("build"), dict) else {}
    )
    branch_name = text(branch_build.get("branch"), fallback_branch)
    reference_name = text(reference_build.get("branch"), "reference")
    branch_digest = text(branch_build.get("digest_md5"), fallback_build)
    reference_digest = text(reference_build.get("digest_md5"), "okänt bygge")
    branch_cells = normalize_cells(branch_side.get("cells"))
    reference_cells = normalize_cells(reference_side.get("cells"))
    branch_snap = f"{snapshot_id}:t3:branch"
    reference_snap = f"{snapshot_id}:t3:reference"
    snapshot_ids = []
    snapshots = []
    if branch_cells:
        snapshot_ids.append(branch_snap)
        snapshots.append(
            snapshot(
                snapshot_id=branch_snap,
                run_number=snapshot_number,
                envelope=envelope,
                label="T3 gren",
                branch=branch_name,
                build=branch_digest,
                regime="T3",
                stats=branch_stats,
                cells=branch_cells,
            )
        )
    if reference_cells:
        snapshot_ids.append(reference_snap)
        snapshots.append(
            snapshot(
                snapshot_id=reference_snap,
                run_number=snapshot_number + 1,
                envelope=envelope,
                label="T3 reference",
                branch=reference_name,
                build=reference_digest,
                regime="T3",
                stats=reference_stats,
                cells=reference_cells,
            )
        )
    verdict = (
        payload.get("verdict")
        if payload.get("verdict") == "PIPELINE-OK"
        else "OFULLSTÄNDIG"
    )
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    item.update(
        kind="pipeline",
        verdict=verdict,
        key="pipeline klar" if verdict == "PIPELINE-OK" else "pipeline ofullständig",
        score={
            "branch": number(branch_side.get("frags")),
            "main": number(reference_side.get("frags")),
        },
        sides={"branch": branch_stats, "main": reference_stats},
        result=result,
        scoreboard=normalize_scoreboard(payload.get("scoreboard")),
        snapshotIds=snapshot_ids,
    )
    return item, snapshots


#: T4:s femvardesvokabular (SPEC T4-domen v6 §2), plus den pensionerade
#: literalen. `COMPLETE` lever kvar ENBART for de inventerade kuvert som fanns
#: fore bytet, och da med etiketten `legacy` — sidan far inte visa ett gammalt
#: kuvert som om det domts pa de nya grindarna.
T4_VERDICTS = ("VINST", "OK", "FAIL", "OMÄTT", "OAVGJORD")
T4_LEGACY_VERDICT = "COMPLETE"
#: Visningsklass per verdict. OMÄTT och OAVGJORD ar egna, ICKE-grona klasser:
#: ett omatt kuvert far aldrig hamna i en gron kolumn, och ett oavgjort ar inte
#: ett godkant resultat. Kartan speglar `verdictClass` i template.html.
T4_VERDICT_CLASS = {
    "VINST": "pass",
    "OK": "pass",
    T4_LEGACY_VERDICT: "pass",
    "FAIL": "fail",
    "OMÄTT": "unmeasured",
    "OAVGJORD": "draw",
}


def t4_level(envelope: dict[str, Any]) -> dict[str, Any]:
    item = base_level("t4", envelope)
    if item["status"] != "complete":
        return item
    payload = envelope["payload"]
    raw_verdict = payload.get("verdict")
    legacy = payload.get("t4_schema") is None
    known = raw_verdict in T4_VERDICTS or (
        legacy and raw_verdict == T4_LEGACY_VERDICT
    )
    if (
        not isinstance(payload.get("ladder"), list)
        or number(payload.get("reached")) is None
        or not known
    ):
        return incomplete_level("t4", envelope, "T4-payload följer inte kontraktet")
    ladder = payload.get("ladder") if isinstance(payload.get("ladder"), list) else []
    played: dict[int, dict[str, Any]] = {}
    for raw in ladder:
        if not isinstance(raw, dict) or number(raw.get("skill")) not in LADDER_SKILLS:
            continue
        skill = int(raw["skill"])
        state = "won" if raw.get("win") is True else "draw" if raw.get("draw") is True else "lost"
        played[skill] = {
            "skill": skill,
            "state": state,
            "for": number(raw.get("frags_for")),
            "against": number(raw.get("frags_against")),
            "scoreboard": normalize_scoreboard(raw.get("scoreboard")),
        }
    rungs = [played.get(skill, unplayed_rung(skill)) for skill in LADDER_SKILLS]
    reached = number(payload.get("reached"))
    verdict = raw_verdict
    dom = payload.get("dom") if isinstance(payload.get("dom"), dict) else {}
    missing = [text(name, "") for name in dom.get("missing") or [] if text(name, "")]
    failed = [text(name, "") for name in dom.get("failed_gates") or [] if text(name, "")]
    labels = [text(name, "") for name in dom.get("labels") or [] if text(name, "")]
    # Enradaren ar det forsta nagon laser, och for de tva icke-grona varderna
    # ar hogsta vunna skill inte det viktigaste den kan saga.
    if verdict == "OMÄTT":
        key = "omätt: " + (", ".join(missing) or "saknade fält ej namngivna")
    elif verdict == "FAIL":
        key = "fälld: " + (", ".join(failed) or "grind ej namngiven")
    elif verdict == "OAVGJORD":
        key = f"oavgjort på skill {reached if reached is not None else '–'} — draw-semantik: ägarbeslut saknas"
    else:
        key = f"nådde skill {reached if reached is not None else '–'}"
    item.update(
        verdict=verdict,
        verdictClass=T4_VERDICT_CLASS.get(verdict, "not-run"),
        legacy=legacy,
        key=key + (" · legacy-kuvert, dömt före femvärdesdomen" if legacy else ""),
        reached=reached,
        rungs=rungs,
        dom={
            "missing": missing,
            "failedGates": failed,
            "labels": labels,
            "reason": text(dom.get("reason"), "") or None,
            "crossAlarm": text(payload.get("cross_alarm"), "") or None,
            "drawSemantics": text(payload.get("draw_semantik"), "") or None,
        }
        if not legacy
        else None,
        measurements=payload.get("measurements")
        if isinstance(payload.get("measurements"), dict)
        else None,
    )
    return item


def load_controls(evidence_dir: Path) -> dict[str, dict[str, Any]]:
    """Sidokontrollposter, nycklade pa kuvertets filnamn.

    En kontrollpost ar en oberoende grind som dott ETT kuvert. Den ligger bredvid
    kuvertet i evidenskatalogen och heter `<kuvertstam>-control.json`.
    """
    controls: dict[str, dict[str, Any]] = {}
    for path in sorted(evidence_dir.glob("*-control.json")):
        try:
            doc = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            warn(f"{path}: kontrollpost avvisad: {exc}")
            continue
        if doc.get("schema") != CONTROL_SCHEMA:
            warn(f"{path}: kontrollpost med okant schema {doc.get('schema')!r}")
            continue
        target = text(doc.get("envelope"), "")
        if target:
            controls[target] = doc
    return controls


def apply_control(item: dict[str, Any], control: dict[str, Any] | None) -> dict[str, Any]:
    """Lagg en sidokontrollpost pa en tier som inte sjalv hanterar en.

    En kontrollpost gor en av tva saker, och skillnaden ar inte kosmetisk:

    * **faller en grind** — kuvertets sjalvdeklarerade verdict haller inte, och
      tiern ar ett forsok (T2:s likhetsgrind, som t2_level hanterar sjalv);
    * **rattar en etikett** — talen ar riktigt matta, men de betyder inte det
      sidan pastar att de betyder. Da star verdict kvar och bara texten andras.

    Kuvertet rors aldrig i nagotdera fallet. Fyra falt kan sattas, alla
    frivilliga: ``label`` (rubrik), ``reason`` (noten), ``key_override``
    (enradaren, det forsta nagon laser) och ``verdict_override``.
    """
    if not control:
        return item
    note = " · ".join(
        part
        for part in (text(control.get("gate"), ""), text(control.get("reason"), ""))
        if part
    )
    item["gateNote"] = note or None
    item["gateLabel"] = text(control.get("label"), "") or None
    item["gateSource"] = text(control.get("source"), "") or None
    override = text(control.get("verdict_override"), "")
    if override:
        item["verdict"] = override
    key_override = text(control.get("key_override"), "")
    if key_override:
        item["key"] = key_override
    return item


def load_retractions(evidence_dir: Path) -> list[dict[str, Any]]:
    """Retraktionsposter ur `retracted/`.

    `discover()` laser bara evidence/*.json, sa ett retraherat kuvert forsvinner
    annars spurlost fran sidan — och en retraktion som ingen ser ar ingen
    retraktion. Posterna lases men blir ALDRIG valda kuvert.
    """
    out: list[dict[str, Any]] = []
    for path in sorted((evidence_dir / "retracted").glob("*-retraction.json")):
        try:
            doc = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            warn(f"{path}: retraktionspost avvisad: {exc}")
            continue
        if doc.get("schema") != RETRACTION_SCHEMA:
            warn(f"{path}: retraktionspost med okant schema {doc.get('schema')!r}")
            continue
        out.append(doc)
    return out


def group_evidence(
    evidence: Iterable[LoadedEvidence],
    controls: dict[str, dict[str, Any]] | None = None,
    retractions: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    grouped: dict[tuple[str, str], list[LoadedEvidence]] = {}
    for loaded in evidence:
        build = loaded.document.get("build")
        build = build if isinstance(build, dict) else {}
        branch = text(build.get("branch"), "okänd branch")
        # The cross-tier build identity is the commit: tiers legitimately hash
        # different artifacts (engine .so for T1/T2, client binary for T3/T4),
        # so digest_md5 cannot group one build's evidence into one column.
        commit = text(build.get("commit"), "")[:8]
        group_build = commit or text(build.get("digest_md5"), "okänt bygge")
        if build.get("dirty") is True:
            group_build += "-dirty"
        grouped.setdefault((branch, group_build), []).append(loaded)

    map_counts = Counter(
        text(item.document.get("map"), "")
        for items in grouped.values()
        for item in items
        if text(item.document.get("map"), "") not in {"", "n/a"}
    )
    primary_map = map_counts.most_common(1)[0][0] if map_counts else None
    runs: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    snapshot_number = 1

    for (branch, group_build), items in grouped.items():
        items.sort(key=lambda item: iso_sort_key(item.document.get("started_utc")))
        latest_started = max(
            (text(item.document.get("started_utc"), "") for item in items), default=""
        )
        digests = sorted(
            {
                text(item.document.get("build", {}).get("digest_md5"), "")
                for item in items
                if isinstance(item.document.get("build"), dict)
            }
            - {""}
        )
        levels = {level: default_level(level) for level in LEVELS}
        candidates: dict[str, list[LoadedEvidence]] = {level: [] for level in LEVELS}
        for loaded in items:
            tier = loaded.document.get("tier")
            slot = "t3" if tier in {"T3", "T3-agg"} else str(tier).lower()
            if slot in candidates:
                candidates[slot].append(loaded)
            else:
                warn(f"{loaded.path}: ignored unknown tier {tier!r}")

        group_id = f"{branch}::{group_build}"
        for level, choices in candidates.items():
            if not choices:
                continue
            choices.sort(
                key=lambda item: (
                    item.document.get("tier") == "T3-agg",
                    iso_sort_key(item.document.get("started_utc")),
                )
            )
            # Statusmedvetet val: senaste KOMPLETTA kuvert vinner. Ett senare
            # misslyckat forsok far inte skugga ett tidigare komplett — det var
            # precis vad som hande T4 i natten 18->19/8, dar forsok 2 (failed)
            # slog forsok 1 (complete) bara for att det startade senare. Finns
            # inget komplett faller valet tillbaka pa det senaste over huvud
            # taget, sa en tier som bara har misslyckanden fortfarande visas som
            # misslyckad i stallet for att forsvinna.
            complete_choices = [
                item
                for item in choices
                if text(item.document.get("status"), "").lower() == "complete"
            ]
            chosen = (complete_choices or choices)[-1].document
            control = (controls or {}).get(f"{chosen.get('run_id')}.json")
            if level == "t0":
                levels[level] = apply_control(t0_level(chosen), control)
            elif level == "t1":
                levels[level] = apply_control(t1_level(chosen), control)
            elif level == "t2":
                levels[level], new_snapshots = t2_level(
                    chosen,
                    group_id,
                    snapshot_number,
                    branch,
                    group_build,
                    control,
                )
                snapshots.extend(new_snapshots)
                snapshot_number += len(new_snapshots)
            elif level == "t3":
                levels[level], new_snapshots = t3_level(
                    chosen, group_id, snapshot_number, branch, group_build
                )
                apply_control(levels[level], control)
                snapshots.extend(new_snapshots)
                snapshot_number += len(new_snapshots)
            elif level == "t4":
                levels[level] = apply_control(t4_level(chosen), control)

        attempts = [
            {
                "runId": loaded.document.get("run_id"),
                "tier": loaded.document.get("tier"),
                "status": loaded.document.get("status"),
                "startedUtc": loaded.document.get("started_utc"),
            }
            for loaded in items
        ]
        # Retraherade kuvert hor till gruppen aven om de inte langre ligger i
        # discovery-vagen. De listas som forsok med status "retraherad" — aldrig
        # som en nolla, aldrig som valt kuvert. En retraktion som ingen ser ar
        # ingen retraktion.
        # Ett run_id slutar pa byggets korta commit (t.ex. ...-b89bbd46), sa
        # suffixet ar gruppnyckeln. Enkel regel, och den galler aven nar
        # ersattaren saknas.
        group_retractions = []
        for record in retractions or []:
            run_id = text(record.get("run_id"), "")
            if not run_id or not run_id.endswith(f"-{group_build}"):
                continue
            tier = run_id.split("-", 1)[0].lower()
            replacement = text(record.get("replacement_run_id"), "")
            entry = {
                "runId": run_id,
                "tier": tier.upper(),
                "utc": record.get("utc"),
                "reason": text(record.get("reason"), ""),
                "operator": record.get("operator"),
                "replacementRunId": replacement or None,
                "tierStatus": record.get("tier_status"),
            }
            group_retractions.append(entry)
            attempts.append(
                {
                    "runId": run_id,
                    "tier": tier.upper(),
                    "status": "retraherad",
                    "startedUtc": record.get("utc"),
                }
            )
            if tier in levels:
                levels[tier]["retractions"] = levels[tier].get("retractions", []) + [entry]
        runs.append(
            {
                "id": group_id,
                "branch": branch,
                "build": group_build,
                "digests": digests,
                "date": latest_started[:10] if latest_started else "okänt datum",
                "startedUtc": latest_started,
                "map": primary_map,
                "levels": levels,
                "attempts": attempts,
            }
        )

    runs.sort(key=lambda run: iso_sort_key(run["startedUtc"]), reverse=True)
    # Chronological run numbers: the oldest group is #1, so a new run always
    # gets a higher number than every run before it. The list itself stays
    # newest-first.
    for position, run in enumerate(runs):
        run["number"] = len(runs) - position
    return runs, snapshots, primary_map


def load_map_assets(map_name: str | None, assets_dir: Path) -> tuple[Any, Any, Any]:
    if not map_name:
        return (
            {"grid": 32, "cells": [], "links": [], "cell_ids": [], "linkKinds": []},
            {"map": None, "entities": []},
            {},
        )
    map_dir = assets_dir / map_name
    graph_path = map_dir / "graph.json"
    entities_path = map_dir / "entities.json"
    try:
        graph, entities = load_json(graph_path), load_json(entities_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"map assets for {map_name!r} must provide graph.json and entities.json "
            f"under {map_dir}: {exc}"
        ) from exc
    # Link geometry is optional (the map degrades to cells-only without it), but a
    # present-yet-broken file is an error, not a silent downgrade.
    linkgeo: dict[str, Any] = {}
    for candidate in sorted(map_dir.glob("linkgeo*.json")):
        try:
            linkgeo.update(load_json(candidate))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"broken link geometry {candidate}: {exc}") from exc
    return graph, entities, linkgeo


def render_dashboard(
    evidence_dir: Path,
    output: Path,
    *,
    template_path: Path = HERE / "template.html",
    map_template_path: Path = HERE / "map-template.html",
    assets_dir: Path = HERE / "assets" / "maps",
) -> tuple[list[dict[str, Any]], list[str]]:
    evidence, warnings = discover(evidence_dir)
    controls = load_controls(evidence_dir)
    retractions = load_retractions(evidence_dir)
    runs, snapshots, primary_map = group_evidence(evidence, controls, retractions)
    if not runs:
        raise RuntimeError(f"no supported rtx-testflow/1 evidence in {evidence_dir}")
    graph, entities, linkgeo = load_map_assets(primary_map, assets_dir)
    template = template_path.read_text(encoding="utf-8")
    map_view = map_template_path.read_text(encoding="utf-8")
    map_literal = json_literal(map_view)
    replacements = {
        "/*__GRAPH__*/": json_literal(graph),
        "/*__ENTS__*/": json_literal(entities),
        "/*__SNAPSHOTS__*/": json_literal(snapshots),
        "/*__LINKGEO__*/": json_literal(linkgeo),
        "/*__MAP_VIEW__*/": map_literal,
        "/*__RUNS__*/": json_literal(runs),
        "__MAP_NAME__": html_lib.escape(primary_map or "ingen karta"),
    }
    html = template
    for marker, replacement in replacements.items():
        html = html.replace(marker, replacement)
    unreplaced = [marker for marker in replacements if marker in html]
    if unreplaced:
        raise RuntimeError(f"unreplaced build markers: {', '.join(unreplaced)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return runs, warnings


def selftest() -> None:
    fixtures = HERE / "fixtures"
    with tempfile.TemporaryDirectory(prefix="rtx-dashboard-selftest-") as temp_dir:
        output = Path(temp_dir) / "dashboard.html"
        runs, warnings = render_dashboard(fixtures, output)
        html = output.read_text(encoding="utf-8")
        assert "Testflöde T0–T4" in html
        assert "Förklaringar" in html
        assert "tema: system" in html
        assert "golden-complete" in html
        assert '"verdict":"MÄTT"' in html
        assert '"verdict":"PIPELINE-OK"' in html
        assert '"kind":"aggregate"' in html
        # T4:s femvärdesdom, alla fem på sidan samtidigt (SPEC v6 §2), plus den
        # pensionerade literalen som bara de inventerade kuverten bär.
        for verdict in T4_VERDICTS:
            assert f'"verdict":"{verdict}"' in html, verdict
        assert '"verdict":"COMPLETE"' in html
        assert '"key":"nådde skill 12"' in html
        assert '"synthetic":true' in html
        assert '"verdict":"FAILED"' in html
        assert '"verdict":"ABORTED"' in html
        assert '"verdict":"EJ KÖRD"' in html
        assert '"peak_100m_source"' in html
        assert "partial-must-not-render" not in html
        assert "must-not-render" not in html
        assert "unknown rtx-testflow major 2" in "\n".join(warnings)
        assert "http://" not in html
        assert "https://" not in html
        assert len(runs) >= 5
        # --- T4:s femvärdesdom (SPEC v6 §2, §5) ---------------------------
        # Vilka verdikt som får se gröna ut avgörs på två ställen — här och i
        # template.html:s VERDICT_CLASS — och de måste vara samma karta. En
        # framtida redigering som gör OMÄTT grönt fastnar på raderna nedan.
        assert T4_VERDICT_CLASS["OMÄTT"] != "pass"
        assert T4_VERDICT_CLASS["OAVGJORD"] != "pass"
        for verdict, klass in T4_VERDICT_CLASS.items():
            assert f'"{verdict}": "{klass}"' in html, verdict

        # NK 18: ett omätt kuvert är varken grönt eller OK, och domraden säger
        # vilka fält som saknades i stället för att tiga.
        unmeasured = next(run for run in runs if run["branch"] == "unmeasured-t4")
        level = unmeasured["levels"]["t4"]
        assert level["verdict"] == "OMÄTT"
        assert level["verdictClass"] == "unmeasured"
        assert level["legacy"] is False
        assert level["dom"]["missing"] == [
            "t4:shots_fired", "t4:teamkills", "t4:still_s", "t4:item_chase"
        ]
        assert level["key"].startswith("omätt: t4:shots_fired")
        assert level["measurements"]["shots_fired"] is None
        assert level["dom"]["labels"] == []
        for run in runs:
            t4 = run["levels"]["t4"]
            if t4.get("verdict") in {"OMÄTT", "OAVGJORD"}:
                assert t4["verdictClass"] != "pass", run["branch"]

        # NK 3/6: en fälld mätt grind är FAIL, och FAIL bär korslarmet.
        silent = next(run for run in runs if run["branch"] == "silent-t4")["levels"]["t4"]
        assert silent["verdict"] == "FAIL" and silent["verdictClass"] == "fail"
        assert silent["dom"]["failedGates"] == ["a:shots_fired"]
        assert silent["dom"]["crossAlarm"] == "no matching T1/T3 run found"

        # NK 4: draw med allt mätt och grönt är OAVGJORD, och sidan säger rakt
        # ut att draw-semantiken är en öppen ägarfråga.
        drew = next(run for run in runs if run["branch"] == "draw-t4")["levels"]["t4"]
        assert drew["verdict"] == "OAVGJORD" and drew["verdictClass"] == "draw"
        assert drew["dom"]["drawSemantics"] == "ägarbeslut saknas"
        assert "ägarbeslut saknas" in drew["key"]
        assert drew["dom"]["labels"] == ["item-pickups-proxy"]

        # NK 10: ett verkligt kuvert från inventeringen visas, men märkt — det
        # dömdes aldrig på de fyra grindarna.
        legacy = next(
            run for run in runs if run["levels"]["t4"].get("legacy") is True
        )["levels"]["t4"]
        assert legacy["verdict"] == "COMPLETE"
        assert legacy["dom"] is None
        assert "legacy-kuvert" in legacy["key"]

        # A drill the build could not be asked: no verdict, no attempts, and a
        # denominator that counts only the drills that were run. Counting it
        # among them would report the build as failing a drill nobody gave it.
        withheld = next(run for run in runs if run["branch"] == "withheld-drill")
        drills = withheld["levels"]["t1"]["data"]["drills"]
        assert [drill["verdict"] for drill in drills] == ["PASS", None]
        assert drills[1]["requires"]["state"] == "absent"
        assert drills[1]["requires"]["capability"] == "navpatch:dm3-pentlift-rj"
        assert drills[0]["requires"] is None
        assert withheld["levels"]["t1"]["key"] == "1/1 drillar · 1 avstådd"
        assert withheld["levels"]["t1"]["verdict"] == "PASS"
        assert withheld["levels"]["t1"]["capabilities"]["unavailable"] == [
            "t1:rj_pent_to_lifts_to_window_to_quad"
        ]
        # An attempt that reached the target without taking the route answered
        # a different question than the drill asked: it is void, not a failed
        # arrival. time_s stays null like every other non-arriving status, and
        # it does not count toward "arrived" or the pass threshold.
        offroute = next(run for run in runs if run["branch"] == "offroute-drill")
        offroute_drill = offroute["levels"]["t1"]["data"]["drills"][0]
        assert [result["status"] for result in offroute_drill["results"]] == [
            "passed", "offroute"
        ]
        assert offroute_drill["results"][1]["time_s"] is None
        assert offroute_drill["arrived"] == 1
        assert offroute_drill["verdict"] == "FAIL"
        # abandoned is a real non-arrival (unlike offroute), just one the
        # impossibility bound cut short instead of the clock: time_s stays
        # null like every other non-arriving status, but min_possible_s must
        # survive the trip through so the dashboard can put the bound on the
        # cell face instead of leaving it to read like an ordinary timeout.
        abandoned = next(run for run in runs if run["branch"] == "abandoned-drill")
        abandoned_drill = abandoned["levels"]["t1"]["data"]["drills"][0]
        assert [result["status"] for result in abandoned_drill["results"]] == [
            "passed", "abandoned", "abandoned"
        ]
        assert [result["time_s"] for result in abandoned_drill["results"][1:]] == [None, None]
        assert [result["min_possible_s"] for result in abandoned_drill["results"][1:]] == [
            8.4, 12.3
        ]
        assert abandoned_drill["arrived"] == 1
        assert abandoned_drill["verdict"] == "FAIL"
        rich = next(run for run in runs if run["branch"] == "evidence-rich")
        t1 = rich["levels"]["t1"]
        categories = [drill["category"] for drill in t1["data"]["drills"]]
        assert categories == ["grunddrill", "cellprov"]
        assert all(drill["place"] for drill in t1["data"]["drills"])
        assert all(
            drill["evidence"]["link"].startswith("/demo-player/")
            for drill in t1["data"]["drills"]
        )
        timed = t1["data"]["drills"][0]
        assert (timed["referenceTime"], timed["maxTime"]) == (4.9, 5.49)
        assert (timed["arrived"], timed["bestTime"]) == (2, 5.37)
        assert [result["status"] for result in timed["results"]] == ["passed", "slow"]
        # Cellproven är otidsatt: de nya fälten får inte hittas på, bara utebli.
        untimed = t1["data"]["drills"][1]
        assert untimed["referenceTime"] is None and untimed["maxTime"] is None
        assert t1["data"]["demo"].endswith(".mvd")
        assert t1["data"]["dash"]["informative"] is False
        assert t1["data"]["dash"]["verdict"] == "PASS"
        assert t1["data"]["dash"]["evidence"]["link"].startswith("/demo-player/")
        t2 = rich["levels"]["t2"]
        assert "peak_100m" not in t2["stats"]
        assert t2["evidence"]["link"].startswith("/demo-player/")
        assert [moment["metric"] for moment in t2["moments"]] == ["quad_takes"]
        assert [cell["metric"] for cell in t2["cellEvidence"]] == ["stall_firings"]
        assert t2["metricSources"]["quad_takes"] == "qw-analyze/items"
        board = rich["levels"]["t3"]["scoreboard"]
        assert [team["name"] for team in board["teams"]] == ["brch", "ref"]
        assert board["players"][0]["dmg_given"] == 3120
        assert board["players"][0]["link"].startswith("/demo-player/")
        assert board["players"][1]["link"] is None
        assert board["source"] == "qw-analyze/demoinfo"
        rungs = rich["levels"]["t4"]["rungs"]
        assert [rung["scoreboard"] is not None for rung in rungs] == [
            True, True, False, False, False, False
        ]
        # Older envelopes keep rendering: no evidence, no scoreboard, no crash.
        golden = next(run for run in runs if run["branch"] == "golden-complete")
        assert golden["levels"]["t1"]["data"]["drills"][0]["evidence"] is None
        assert golden["levels"]["t1"]["data"]["drills"][0]["category"] == "grunddrill"
        assert golden["levels"]["t1"]["data"]["dash"]["verdict"] is None
        assert golden["levels"]["t2"]["evidence"] is None
        assert golden["levels"]["t2"]["metricSources"] == {}
        assert golden["levels"]["t3"]["scoreboard"] is None
        assert all(rung["scoreboard"] is None for rung in golden["levels"]["t4"]["rungs"])
        # The navmesh a run was measured against: same figures on both tiers
        # that stamp one, since both fixtures record the same preflight.
        golden_nav = {
            "map": "dm3",
            "state": "ready",
            "cells": 4634,
            "links": 36956,
            "rjLinks": 2021,
            "waitedS": 0.0,
        }
        assert golden["levels"]["t1"]["data"]["nav"] == golden_nav
        assert golden["levels"]["t2"]["nav"] == golden_nav
        # The figures the JS renderer reads for the panel-header line, present
        # in the page's embedded run data (sort_keys, so the field order here
        # is fixed).
        assert (
            '"cells":4634,"links":36956,"map":"dm3","rjLinks":2021,'
            '"state":"ready","waitedS":0.0' in html
        )
        # Envelopes from before this stamp existed have no nav at all, and that
        # must keep rendering exactly as it always has: nothing, not a crash.
        assert withheld["levels"]["t1"]["data"]["nav"] is None
        # Host-relative demo links reach the page and open in a new tab.
        assert "/demo-player/?demoUrl=" in html
        assert 'target="_blank" rel="noopener"' in html
        assert runs == sorted(
            runs, key=lambda run: iso_sort_key(run["startedUtc"]), reverse=True
        )
        derived = next(run for run in runs if run["branch"] == "okänd branch")
        assert derived["levels"]["t0"]["synthetic"] is False
        assert derived["levels"]["t0"]["sources"] == [
            {"field": "verdict_source", "source": "t0-upstream-summary"}
        ]
        smoke = next(run for run in runs if run["branch"] == "null-fields")
        assert smoke["levels"]["t2"]["comparisonKey"] == "t2:smoke"
        # A build that could not be asked about stalls: the count stays null,
        # the map keeps no zones, and the summary line says so rather than
        # reading as the best column on the page.
        blind = next(run for run in runs if run["branch"] == "no-telemetry")
        blind_t2 = blind["levels"]["t2"]
        assert blind_t2["capabilities"]["telemetry"] is False
        assert blind_t2["capabilities"]["unavailable"] == ["stall_firings", "cells"]
        assert blind_t2["stats"]["stall_firings"] is None
        assert blind_t2["key"] == "stall ej mätbar"
        assert blind_t2["snapshotIds"] == []
        # T2 never connected to a control layer on this fixture either, so it
        # has nothing to stamp — same absence, different reason than golden's
        # pre-stamp envelopes.
        assert blind_t2["nav"] is None
        assert golden["levels"]["t2"]["capabilities"] is None
        # The two phrases the page owes such a column, in the code that renders
        # it; the browser check drives the page itself.
        assert "ej mätbar" in html and "ej jämförbar" in html
    print("dashboard selftest: PASS")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=ROOT / "evidence",
        help="directory containing evidence/*.json files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=HERE / "dashboard.html",
        help="static HTML output path",
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        default=HERE / "assets" / "maps",
        help="directory containing <map>/graph.json and entities.json",
    )
    parser.add_argument("--selftest", action="store_true", help="build and assert golden fixtures")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    runs, warnings = render_dashboard(
        args.evidence_dir, args.output, assets_dir=args.assets_dir
    )
    print(
        f"built {args.output} from {sum(len(run['attempts']) for run in runs)} "
        f"evidence files in {len(runs)} build groups ({len(warnings)} warnings)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
