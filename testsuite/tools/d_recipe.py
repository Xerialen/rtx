#!/usr/bin/env python3
"""Load the west-shelf recipe fixture. ON expected is never invented from observed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from d_strata import STRATA, heldout_stratum_at  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_RECIPE = HERE / "recept" / "west-shelf.json"
DEFAULT_GATES = HERE / "recept" / "west-shelf-gates.json"
DEFAULT_AVSETT = HERE / "recept" / "west-shelf-avsett-drop.json"

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


def load_avsett_drop(path: Path | None = None) -> dict[str, Any]:
    """A1/A2 intended-drop fixture. Pinned under OFF-stamp; never from observed."""
    doc = load_json(path or DEFAULT_AVSETT)
    if doc.get("recipe") != "west-shelf":
        raise ValueError(f"avsett_drop recipe must be west-shelf, got {doc.get('recipe')!r}")
    if doc.get("id") != "west-shelf-avsett-drop":
        raise ValueError(f"unexpected avsett_drop id {doc.get('id')!r}")
    return doc


def _t0_budget_from_gates(gates: dict[str, Any]) -> float | None:
    v = gates.get("t0_budget_s")
    if v is None:
        ws = (gates.get("gates") or {}).get("west-shelf")
        if isinstance(ws, dict):
            v = ws.get("t0_budget_s")
    if v is None:
        return None
    return float(v)


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
    _, locus_err = heldout_stratum_at(gates)
    if locus_err:
        return locus_err
    try:
        tb = _t0_budget_from_gates(gates)
    except (TypeError, ValueError):
        return "t0_budget_s missing or unreadable in gate file (facit r3 §3)"
    if tb is None:
        return "t0_budget_s missing from gate file (facit r3 §3)"
    want = float(STRATA["T0"]["budget_s"])
    if abs(tb - want) > 1e-9:
        return f"t0_budget_s {tb} != STRATA T0 budget {want}"
    return None


def avsett_drop_registered(geom: dict[str, Any], off_stamp: str, off_hash: str | None = None) -> str | None:
    """Return an invalidity reason, or None if the A1/A2 pin is complete."""
    if not isinstance(geom, dict) or not geom:
        return "avsett_drop fixture missing (facit r3 §3)"
    if geom.get("graph_stamp") != off_stamp:
        return "avsett_drop graph_stamp does not match recipe OFF pin"
    if off_hash and geom.get("graph_content_hash") and geom.get("graph_content_hash") != off_hash:
        return "avsett_drop graph_content_hash does not match recipe OFF pin"
    if geom.get("route_id") in (None, ""):
        return "avsett_drop missing route_id"
    src, tgt = geom.get("source"), geom.get("target")
    if not isinstance(src, dict) or not isinstance(src.get("cell"), int) or src.get("origin") is None:
        return "avsett_drop missing source cell/origin"
    if not isinstance(tgt, dict) or not isinstance(tgt.get("cell"), int) or tgt.get("origin") is None:
        return "avsett_drop missing target cell/origin"
    if not (geom.get("drop_cells") or []):
        return "avsett_drop missing drop_cells"
    if not (geom.get("landing_cells") or []):
        return "avsett_drop missing landing_cells"
    corridor = geom.get("corridor") or {}
    for k in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax"):
        if k not in corridor:
            return f"avsett_drop corridor missing {k}"
    applies = set(geom.get("applies_to") or [])
    if applies != {"A1", "A2"}:
        return "avsett_drop must apply to A1/A2 only"
    return None
