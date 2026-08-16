#!/usr/bin/env python3
"""Fail-closed verifier for `verktygslada/d-kvitto/1` (facit-d-sjalvbevis.md §4).

Refuses a receipt with missing fields, an after-the-fact lock, a forbidden
RA/main endpoint, or any stamp observed≠expected. Observed may never become
its own expected. Mirrors route-lab `verify_p8_focus.py` (pin sha/count).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = "verktygslada/d-kvitto/1"
FORBIDDEN_CTL = {27990, 27993}
FORBIDDEN_GAME = {27540, 27570}


def _port_num(value):
    """Accept int or decimal string so RA/main cannot slip through as "27990"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
    return None


WEST_SHELF_OFF = {
    "cells": 5977,
    "links": 48207,
    "rj_links": 0,
    "graph_stamp": "906595427771298736",
    "graph_content_hash": (
        "58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9"
    ),
}

STAMP_KEYS = ("cells", "links", "rj_links", "graph_stamp", "graph_content_hash")
ASTAR_KEYS = ("found", "cells", "links", "cost", "mask_links")


def _iso(value: Any, label: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label}: missing ISO-8601 timestamp")
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}: not ISO-8601: {value!r}")
        return None


def _require(doc: Any, path: str, errors: list[str]) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            errors.append(f"missing field {path}")
            return None
        cur = cur[part]
    if cur is None or cur == "":
        errors.append(f"empty field {path}")
        return None
    return cur


def _stamp_ok(block: Any, label: str, errors: list[str]) -> tuple[dict, dict] | None:
    if not isinstance(block, dict):
        errors.append(f"{label}: not an object")
        return None
    exp, obs = block.get("expected"), block.get("observed")
    if not isinstance(exp, dict) or not isinstance(obs, dict):
        errors.append(f"{label}: expected and observed are required objects")
        return None
    for side, name in ((exp, "expected"), (obs, "observed")):
        for key in STAMP_KEYS:
            if key not in side or side[key] in (None, ""):
                errors.append(f"{label}.{name}.{key}: missing")
        if "graph_content_hash" in side:
            h = str(side["graph_content_hash"])
            if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
                errors.append(f"{label}.{name}.graph_content_hash: not lowercase SHA-256 hex")
        if "graph_stamp" in side and not isinstance(side["graph_stamp"], str):
            errors.append(
                f"{label}.{name}.graph_stamp: must be a decimal string (u64 > 2^53)"
            )
    if exp and obs and all(k in exp and k in obs for k in STAMP_KEYS):
        if exp == obs and not exp:
            errors.append(f"{label}: empty stamp pair")
        for key in STAMP_KEYS:
            if exp.get(key) != obs.get(key):
                errors.append(
                    f"{label}: {key} observed {obs.get(key)!r} != expected {exp.get(key)!r}"
                )
    return exp, obs


def _astar_ok(block: Any, label: str, errors: list[str]) -> None:
    if not isinstance(block, dict):
        errors.append(f"{label}: missing A* dump")
        return
    for key in ASTAR_KEYS:
        if key not in block:
            errors.append(f"{label}.{key}: missing")
    if "found" in block and not isinstance(block["found"], bool):
        errors.append(f"{label}.found: must be bool")
    if block.get("found") is True:
        if not block.get("cells") or not block.get("links"):
            errors.append(f"{label}: found=true but cells/links empty")



def _chosen_path_links(astar: dict) -> list | None:
    """The path that next_best must mask: after if found, else before."""
    after = astar.get("after") if isinstance(astar.get("after"), dict) else {}
    before = astar.get("before") if isinstance(astar.get("before"), dict) else {}
    if after.get("found"):
        return list(after.get("links") or [])
    if before.get("found"):
        return list(before.get("links") or [])
    return None


def _next_best_masks_entire_chosen(astar: dict, errors: list[str]) -> None:
    """Counterfactual: mask_links must be the entire chosen path, not a single hop."""
    nb = astar.get("next_best")
    if not isinstance(nb, dict):
        return
    chosen = _chosen_path_links(astar)
    if chosen is None:
        return
    mask = list(nb.get("mask_links") or [])
    if mask != chosen:
        errors.append(
            "astar.next_best.mask_links must equal the entire chosen path "
            f"(got {mask!r}, want {chosen!r})"
        )

def verify(doc: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["receipt is not a JSON object"]

    schema = doc.get("schema")
    if schema != SCHEMA:
        errors.append(f"schema: expected {SCHEMA!r}, got {schema!r}")

    owner = _require(doc, "riglock.owner", errors)
    issued = _iso(_require(doc, "riglock.issued_at", errors), "riglock.issued_at", errors)
    valid_from = _iso(_require(doc, "riglock.valid_from", errors), "riglock.valid_from", errors)
    valid_to = _iso(_require(doc, "riglock.valid_to", errors), "riglock.valid_to", errors)
    _require(doc, "riglock.path", errors)

    started = _iso(_require(doc, "run.started_at", errors), "run.started_at", errors)
    ended = _iso(_require(doc, "run.ended_at", errors), "run.ended_at", errors)

    if issued and started and issued > started:
        errors.append(
            "riglock issued_at is after run.started_at (after-the-fact lock)"
        )
    if valid_from and valid_to and valid_from > valid_to:
        errors.append("riglock valid_from > valid_to")
    if started and ended and started > ended:
        errors.append("run.started_at > run.ended_at")
    if valid_from and started and started < valid_from:
        errors.append("run starts before lock valid_from")
    if valid_to and ended and ended > valid_to:
        errors.append("run ends after lock valid_to")

    _require(doc, "endpoint.host", errors)
    ctl = _require(doc, "endpoint.ctl_port", errors)
    game = _require(doc, "endpoint.game_port", errors)
    ctl_n, game_n = _port_num(ctl), _port_num(game)
    if ctl_n is not None and ctl_n in FORBIDDEN_CTL:
        errors.append(f"endpoint.ctl_port {ctl!r} is RA/main — dedicated D instance only")
    if game_n is not None and game_n in FORBIDDEN_GAME:
        errors.append(f"endpoint.game_port {game!r} is RA/main — dedicated D instance only")

    map_name = _require(doc, "map", errors)
    digest = _require(doc, "binary_sha256", errors)
    if isinstance(digest, str) and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        errors.append("binary_sha256: not lowercase SHA-256 hex")
    _require(doc, "commit", errors)

    off = _stamp_ok(doc.get("stamps", {}).get("off") if isinstance(doc.get("stamps"), dict) else None, "stamps.off", errors)
    on = _stamp_ok(doc.get("stamps", {}).get("on") if isinstance(doc.get("stamps"), dict) else None, "stamps.on", errors)
    undo = _stamp_ok(doc.get("stamps", {}).get("undo") if isinstance(doc.get("stamps"), dict) else None, "stamps.undo", errors)
    if off and undo and off[0] != undo[0]:
        errors.append("stamps.undo.expected must equal stamps.off.expected (bit-identical undo)")

    recipe_id = _require(doc, "recipe.id", errors)
    _require(doc, "recipe.taxonomy_class", errors)
    _require(doc, "recipe.evidence", errors)
    if recipe_id == "west-shelf":
        if map_name not in (None, "dm3"):
            errors.append(f"west-shelf map must be dm3, got {map_name!r}")
        if off and off[0] != WEST_SHELF_OFF:
            errors.append("west-shelf OFF expected must be the facit §1 base pin")

    seed = _require(doc, "seed", errors)
    if seed is not None and not isinstance(seed, int):
        errors.append("seed: must be int")
    stratum = _require(doc, "stratum", errors)
    if isinstance(stratum, dict) and not stratum.get("id"):
        errors.append("stratum.id: missing")
    _require(doc, "raw_pointer", errors)

    astar = doc.get("astar")
    if not isinstance(astar, dict):
        errors.append("astar: missing object")
    else:
        _astar_ok(astar.get("before"), "astar.before", errors)
        _astar_ok(astar.get("after"), "astar.after", errors)
        if "next_best" not in astar:
            errors.append("astar.next_best: missing")
        else:
            _astar_ok(astar.get("next_best"), "astar.next_best", errors)
            _next_best_masks_entire_chosen(astar, errors)

    return errors


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <kvitto.json>", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1])
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL {path}: {exc}", file=sys.stderr)
        sys.exit(1)
    errors = verify(doc)
    if errors:
        print(f"FAIL {path}:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    recipe = doc.get("recipe", {}).get("id", "?")
    print(
        f"d-kvitto OK: schema={SCHEMA} recipe={recipe} "
        f"commit={doc.get('commit')} ctl={doc.get('endpoint', {}).get('ctl_port')}"
    )


if __name__ == "__main__":
    main()
