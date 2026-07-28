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
    }
    if level == "t0":
        common.update(modules=[], qualityFloors=[], total={"tests": None, "passed": None})
    elif level == "t1":
        common["data"] = {
            "drills": [],
            "dash": {"peaks": [], "peak": None, "floor": None, "informative": True},
            "note": None,
        }
    elif level == "t2":
        common["stats"] = {
            "quad_takes": None,
            "quad_lay_avg": None,
            "pent_takes": None,
            "pent_lay_avg": None,
            "speed_1s": None,
            "speed_100ms": None,
            "peak_100m": None,
            "still_s_per_bot": None,
            "stall_firings": None,
            "duration_s": None,
        }
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
        )
    elif level == "t4":
        common.update(
            reached=None,
            rungs=[
                {"skill": skill, "state": "unplayed", "for": None, "against": None}
                for skill in LADDER_SKILLS
            ],
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
        attempts = scenario.get("attempts") if isinstance(scenario.get("attempts"), list) else []
        verdict = scenario.get("verdict")
        passed_scenarios += verdict == "PASS"
        drills.append(
            {
                "name": text(scenario.get("name"), "okänt scenario"),
                "threshold": number(threshold.get("required")),
                "of": number(threshold.get("of")),
                "results": attempts,
                "verdict": verdict,
            }
        )
    regime_note = payload.get("regime_note")
    regime_note = regime_note if isinstance(regime_note, str) and regime_note else None
    verdict = payload.get("verdict") if payload.get("verdict") in {"PASS", "FAIL"} else "OFULLSTÄNDIG"
    dash = payload.get("dash") if isinstance(payload.get("dash"), dict) else {}
    item.update(
        verdict=verdict,
        key=f"{passed_scenarios}/{len(drills)} drillar",
        regimeNote=regime_note,
        comparisonKey=f"t1:{regime_note or 'full'}",
        data={
            "drills": drills,
            "dash": {
                "peaks": dash.get("peaks") if isinstance(dash.get("peaks"), list) else [],
                "peak": number(dash.get("peak")),
                "floor": number(dash.get("floor")),
                "informative": dash.get("informative") is True,
            },
            "note": payload.get("note") if isinstance(payload.get("note"), str) else None,
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
    snap_id = f"{snapshot_id}:t2"
    item.update(
        verdict="MÄTT",
        key=f"{number(raw_stats.get('stall_firings')) or 0} stall",
        stats=stats,
        regimeNote=regime_note,
        comparisonKey=f"t2:{regime_note or 'full'}",
        snapshotIds=[snap_id] if cells else [],
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
        snapshotIds=snapshot_ids,
    )
    return item, snapshots


def t4_level(envelope: dict[str, Any]) -> dict[str, Any]:
    item = base_level("t4", envelope)
    if item["status"] != "complete":
        return item
    payload = envelope["payload"]
    if (
        not isinstance(payload.get("ladder"), list)
        or number(payload.get("reached")) is None
        or payload.get("verdict") != "COMPLETE"
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
        }
    rungs = [
        played.get(
            skill,
            {"skill": skill, "state": "unplayed", "for": None, "against": None},
        )
        for skill in LADDER_SKILLS
    ]
    reached = number(payload.get("reached"))
    verdict = payload.get("verdict") if payload.get("verdict") == "COMPLETE" else "OFULLSTÄNDIG"
    item.update(
        verdict=verdict,
        key=f"nådde skill {reached if reached is not None else '–'}",
        reached=reached,
        rungs=rungs,
    )
    return item


def group_evidence(
    evidence: Iterable[LoadedEvidence],
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
            chosen = choices[-1].document
            if level == "t0":
                levels[level] = t0_level(chosen)
            elif level == "t1":
                levels[level] = t1_level(chosen)
            elif level == "t2":
                levels[level], new_snapshots = t2_level(
                    chosen, group_id, snapshot_number, branch, group_build
                )
                snapshots.extend(new_snapshots)
                snapshot_number += len(new_snapshots)
            elif level == "t3":
                levels[level], new_snapshots = t3_level(
                    chosen, group_id, snapshot_number, branch, group_build
                )
                snapshots.extend(new_snapshots)
                snapshot_number += len(new_snapshots)
            elif level == "t4":
                levels[level] = t4_level(chosen)

        attempts = [
            {
                "runId": loaded.document.get("run_id"),
                "tier": loaded.document.get("tier"),
                "status": loaded.document.get("status"),
                "startedUtc": loaded.document.get("started_utc"),
            }
            for loaded in items
        ]
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
    runs, snapshots, primary_map = group_evidence(evidence)
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
        assert '"verdict":"COMPLETE"' in html
        assert '"kind":"aggregate"' in html
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
