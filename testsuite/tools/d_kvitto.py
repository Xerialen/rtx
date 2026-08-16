#!/usr/bin/env python3
"""Turnkey writer for a D-receipt (`verktygslada/d-kvitto/1`).

Builds the document facit-d-sjalvbevis.md §4 requires. Does not talk to a
rig — callers supply stamps, A* dumps and lock metadata. Live apply is GAP 4.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SCHEMA = "verktygslada/d-kvitto/1"

# Facit r2 arm profile: only these two cvars may differ OFF vs ON.
CVAR_ARM_DIFF_OK = frozenset({"rtx_nav_patch", "rtx_r1_lite"})


def recipe_cvars(recipe: dict | None) -> dict | None:
    cv = (recipe or {}).get("cvars")
    if isinstance(cv, dict) and isinstance(cv.get("off"), dict) and isinstance(cv.get("on"), dict):
        return {"off": dict(cv["off"]), "on": dict(cv["on"])}
    return None

# Facit §1 OFF-bas. ON expected is *not* invented here — the fixture must
# carry it before a live run (observed must never become its own expected).
WEST_SHELF_OFF = {
    "cells": 5977,
    "links": 48207,
    "rj_links": 0,
    "graph_stamp": "906595427771298736",
    "graph_content_hash": (
        "58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9"
    ),
}

WEST_SHELF_RECIPE = {
    "id": "west-shelf",
    "taxonomy_class": "carve_origin",
    "evidence": (
        "dm3 machinery shelf west of SNG: walkable strip narrower than the "
        "column-carve GRID and out of phase with it; plant_cell + plant_drop "
        "restore an honest standing cell and a north-lip Drop. See "
        "nav_patch::PATCHES west-shelf."
    ),
}


def stamp_block(expected: dict, observed: dict) -> dict:
    return {"expected": dict(expected), "observed": dict(observed)}


def astar_path(
    *,
    found: bool,
    cells: list[int] | None = None,
    links: list[int] | None = None,
    cost: float | None = None,
    mask_links: list[int] | None = None,
) -> dict:
    return {
        "found": found,
        "cells": list(cells or []),
        "links": list(links or []),
        "cost": None if cost is None else float(cost),
        "mask_links": list(mask_links or []),
    }


def astar_from_route_resp(data: dict, *, mask_links: list[int] | None = None) -> dict:
    """Lift a ctl `Route` reply (live or query) into the receipt's astar path object."""
    astar = data.get("astar") or {}
    legs = data.get("legs") or []
    links = [int(leg["link"]) for leg in legs]
    cells: list[int] = []
    if legs:
        cells.append(int(legs[0]["src_cell"]))
        cells.extend(int(leg["tgt_cell"]) for leg in legs)
    found = bool(astar.get("found", bool(legs)))
    return astar_path(
        found=found,
        cells=cells,
        links=links,
        cost=astar.get("cost"),
        mask_links=list(mask_links if mask_links is not None else astar.get("mask_links") or []),
    )


def make_kvitto(
    *,
    riglock_owner: str,
    riglock_issued_at: str,
    riglock_valid_from: str,
    riglock_valid_to: str,
    riglock_path: str,
    run_started_at: str,
    run_ended_at: str,
    endpoint_host: str,
    endpoint_ctl_port: int,
    endpoint_game_port: int,
    map_name: str,
    binary_sha256: str,
    commit: str,
    stamps_off_expected: dict,
    stamps_off_observed: dict,
    stamps_on_expected: dict,
    stamps_on_observed: dict,
    stamps_undo_expected: dict,
    stamps_undo_observed: dict,
    recipe: dict,
    seed: int,
    stratum: dict,
    raw_pointer: str,
    astar_before: dict,
    astar_after: dict,
    astar_next_best: dict,
    gate_velocity: list[float] | None = None,
    gate_cell: int | None = None,
    gate_aim_hit: bool = False,
    demo_file: str | None = None,
    fixture_sha256: str | None = None,
    candidate: str | None = None,
    landing_cell: int | None = None,
    selected_link: int | None = None,
    knockback: dict | None = None,
    cvars: dict | None = None,
) -> dict[str, Any]:
    """Assemble a §4-complete receipt. Missing kwargs are a TypeError (fail closed).

    `demo_file` is an optional pointer to a server MVD (`qw/demos/….mvd`).
    Omitted when None. Presence is not proof the file still exists — demos
    rotate after 7 days; the receipt is permanent.
    """
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "riglock": {
            "owner": riglock_owner,
            "issued_at": riglock_issued_at,
            "valid_from": riglock_valid_from,
            "valid_to": riglock_valid_to,
            "path": riglock_path,
        },
        "run": {"started_at": run_started_at, "ended_at": run_ended_at},
        "endpoint": {
            "host": endpoint_host,
            "ctl_port": int(endpoint_ctl_port),
            "game_port": int(endpoint_game_port),
        },
        "map": map_name,
        "binary_sha256": binary_sha256,
        "commit": commit,
        "stamps": {
            "off": stamp_block(stamps_off_expected, stamps_off_observed),
            "on": stamp_block(stamps_on_expected, stamps_on_observed),
            "undo": stamp_block(stamps_undo_expected, stamps_undo_observed),
        },
        "recipe": {
            "id": recipe["id"],
            "taxonomy_class": recipe["taxonomy_class"],
            "evidence": recipe["evidence"],
        },
        "seed": int(seed),
        "stratum": dict(stratum),
        "raw_pointer": raw_pointer,
        "gate": {
            "velocity": None if gate_velocity is None else [float(x) for x in gate_velocity],
            "cell": gate_cell,
            "aim_hit": bool(gate_aim_hit),
        },
        "astar": {
            "before": dict(astar_before),
            "after": dict(astar_after),
            "next_best": dict(astar_next_best),
        },
    }
    if demo_file is not None:
        doc["demo_file"] = demo_file
    if fixture_sha256 is not None:
        doc["fixture_sha256"] = fixture_sha256
    if candidate is not None:
        doc["candidate"] = candidate
    if landing_cell is not None:
        doc["landing_cell"] = int(landing_cell)
    if selected_link is not None:
        doc["selected_link"] = int(selected_link)
    if knockback is not None:
        doc["knockback"] = dict(knockback)
    if cvars is not None:
        doc["cvars"] = dict(cvars)
    return doc


def write_exclusive(path: str | Path, text: str) -> Path:
    """Create `path` with O_CREAT|O_EXCL. Refuse to clobber an existing file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        raise FileExistsError(f"refuse overwrite of existing kvitto {path}") from None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def write_kvitto(
    path: str | Path,
    doc: dict,
    *,
    exclusive: bool = False,
    verify_first: bool = False,
) -> None:
    if verify_first:
        from verify_d_kvitto import verify  # lazy: verify_d_kvitto does not import us

        errors = verify(doc)
        if errors:
            raise RuntimeError("kvitto verify failed: " + "; ".join(errors))
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if exclusive:
        write_exclusive(path, text)
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_recipe_id(owner_id: str) -> str:
    return safe_owner_id(owner_id)


def safe_owner_id(owner_id: str) -> str:
    rid = str(owner_id or "").strip()
    if not rid or rid in {".", ".."} or "/" in rid or "\\" in rid:
        raise ValueError(f"refusing unsafe kvitto owner id: {owner_id!r}")
    return rid


def recipe_kvitto_paths(kvitto_dir: Path, owner_id: str, attempt_id: str) -> tuple[Path, Path]:
    """Per-owner subdirectory: `--kvitto-dir/<recipe-or-candidate>/<attempt>.json`.

    Same attempt id (H1-OFF-01, 1416-1124-ON-01) must not collide across
    recipes or tournament candidates.
    """
    root = Path(kvitto_dir) / safe_owner_id(owner_id)
    stem = str(attempt_id)
    if not stem or "/" in stem or "\\" in stem or stem in {".", ".."}:
        raise ValueError(f"refusing unsafe attempt id for kvitto path: {attempt_id!r}")
    return root / f"{stem}.json", root / f"{stem}.jsonl"


def foreign_kvitto_entries(kvitto_dir: Path, owner_id: str) -> list[str]:
    """Entries in --kvitto-dir that are not this owner's subdirectory."""
    root = Path(kvitto_dir)
    if not root.is_dir():
        return []
    owner = safe_owner_id(owner_id)
    out: list[str] = []
    for p in sorted(root.iterdir(), key=lambda x: x.name):
        if p.name.startswith("."):
            continue
        if p.name == owner and p.is_dir():
            continue
        out.append(p.name)
    return out


def refuse_shared_kvitto_dir(
    kvitto_dir: Path | None,
    owner_id: str,
    *,
    allow_shared: bool = False,
) -> None:
    """Fail-closed: a non-empty shared --kvitto-dir needs --allow-shared."""
    if allow_shared or kvitto_dir is None:
        return
    root = Path(kvitto_dir)
    foreign = foreign_kvitto_entries(root, owner_id)
    if foreign:
        shown = foreign[:8]
        raise RuntimeError(
            f"refuse non-empty shared --kvitto-dir {root} "
            f"(foreign={shown!r}) without --allow-shared"
        )


def format_attempt_raw(raw: dict) -> str:
    """JSONL text for one attempt (header + events + samples)."""
    header = {
        "kind": "header",
        "stratum_id": raw.get("stratum_id"),
        "arm": raw.get("arm"),
        "seq": raw.get("seq"),
        "gate_velocity": raw.get("gate_velocity"),
        "gate_cell": raw.get("gate_cell"),
        "gate_origin": raw.get("gate_origin"),
        "commanded_vel": raw.get("commanded_vel"),
        "measured_vel": raw.get("measured_vel"),
        "vel_tries": raw.get("vel_tries"),
        "stamp_ok": raw.get("stamp_ok"),
        "stamp_reason": raw.get("stamp_reason"),
        "match_vel": raw.get("match_vel"),
    }
    lines = [json.dumps(header, sort_keys=True)]
    for ev in raw.get("events") or []:
        row = dict(ev)
        row["kind"] = "event"
        lines.append(json.dumps(row, sort_keys=True))
    for samp in raw.get("samples") or []:
        row = dict(samp)
        row["kind"] = "sample"
        lines.append(json.dumps(row, sort_keys=True))
    return "\n".join(lines) + "\n"


def write_attempt_raw_file(path: Path, raw: dict, *, exclusive: bool = False) -> Path:
    text = format_attempt_raw(raw)
    if exclusive:
        return write_exclusive(path, text)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
