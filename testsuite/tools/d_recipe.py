#!/usr/bin/env python3
"""Load the west-shelf recipe fixture. ON expected is never invented from observed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_RECIPE = HERE / "recept" / "west-shelf.json"
DEFAULT_GATES = HERE / "recept" / "west-shelf-gates.json"

STAMP_KEYS = ("cells", "links", "rj_links", "graph_stamp", "graph_content_hash")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_recipe(path: Path | None = None) -> dict[str, Any]:
    doc = load_json(path or DEFAULT_RECIPE)
    if doc.get("id") != "west-shelf":
        raise ValueError(f"only west-shelf is registered, got {doc.get('id')!r}")
    off = doc.get("off")
    if not isinstance(off, dict) or any(k not in off for k in STAMP_KEYS):
        raise ValueError("recipe.off is not a complete stamp block")
    return doc


def on_expected(recipe: dict[str, Any]) -> dict[str, Any]:
    """Facit §1: ON expected lives in the fixture. Never copy observed into it."""
    on = recipe.get("on_expected")
    if not isinstance(on, dict) or any(k not in on or on[k] in (None, "") for k in STAMP_KEYS):
        raise ValueError(
            "ON expected missing from recipe fixture — refusing to invent it from observed"
        )
    return dict(on)


def load_gates(path: Path | None = None) -> dict[str, Any]:
    return load_json(path or DEFAULT_GATES)


def gates_registered(gates: dict[str, Any], off_stamp: str) -> str | None:
    """Return an invalidity reason, or None if the gate file is complete."""
    if gates.get("graph_stamp") != off_stamp:
        return "gate file graph_stamp does not match recipe OFF pin"
    ws = (gates.get("gates") or {}).get("west-shelf")
    if not isinstance(ws, dict):
        return "gate file missing gates.west-shelf"
    cells = ws.get("cell_ids") or []
    if not cells:
        return "west-shelf gate cell_ids not pre-registered (facit §2)"
    return None
