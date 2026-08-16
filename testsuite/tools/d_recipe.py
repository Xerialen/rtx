#!/usr/bin/env python3
"""Load a D-recipe fixture. ON expected is never invented from observed."""

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
REGISTERED_IDS = frozenset({
    "west-shelf",
    "ram-rail",
    "ram-rail-v2",
    "ram-prevent",
    "haz1462-k1",
    "haz1462-k2",
    "haz1462-k3",
})


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_recipe(path: Path | None = None) -> dict[str, Any]:
    doc = load_json(path or DEFAULT_RECIPE)
    rid = doc.get("id")
    if rid not in REGISTERED_IDS:
        raise ValueError(f"unknown recipe {rid!r}; registered={sorted(REGISTERED_IDS)}")
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


def recipe_gate(gates: dict[str, Any], recipe_id: str) -> dict[str, Any] | None:
    """The gate block for `recipe_id`, or None if the file has no such key."""
    block = (gates.get("gates") or {}).get(recipe_id)
    return block if isinstance(block, dict) else None


def load_avsett_drop(path: Path | None = None, recipe_id: str | None = None) -> dict[str, Any]:
    """Intended-drop fixture. Pinned under OFF-stamp; never from observed.

    Non-west-shelf recipes must pass an explicit path — defaulting to the
    west-shelf pin would silently score the wrong geometry.
    """
    want = recipe_id or "west-shelf"
    if path is None:
        if want != "west-shelf":
            raise ValueError(f"avsett_drop fixture missing for {want}")
        path = DEFAULT_AVSETT
    doc = load_json(path)
    if doc.get("recipe") != want:
        raise ValueError(f"avsett_drop recipe must be {want}, got {doc.get('recipe')!r}")
    if want == "west-shelf" and doc.get("id") != "west-shelf-avsett-drop":
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


def gates_registered(
    gates: dict[str, Any], off_stamp: str, recipe_id: str = "west-shelf"
) -> str | None:
    """Return an invalidity reason, or None if the gate file is complete."""
    if gates.get("graph_stamp") != off_stamp:
        return "gate file graph_stamp does not match recipe OFF pin"
    tag = gates.get("recipe")
    if tag not in (None, recipe_id) and tag != recipe_id.split("-")[0]:
        # allow family tag "haz1462" for haz1462-k1/k2/k3
        family = recipe_id.rsplit("-", 1)[0] if recipe_id.startswith("haz1462") else None
        if tag != family:
            return f"gate file recipe {tag!r} != {recipe_id!r}"
    ws = recipe_gate(gates, recipe_id)
    if ws is None:
        return f"gate file missing gates.{recipe_id}"
    cells = ws.get("cell_ids") or []
    if not cells:
        return f"{recipe_id} gate cell_ids not pre-registered (facit §2)"
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


def avsett_drop_registered(
    geom: dict[str, Any],
    off_stamp: str,
    off_hash: str | None = None,
    recipe_id: str = "west-shelf",
) -> str | None:
    """Return an invalidity reason, or None if the intended-drop pin is complete."""
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
    if recipe_id in {"ram-rail", "ram-rail-v2", "ram-prevent"}:
        if not applies & {"H1", "H2", "P1", "P2"}:
            return "ram avsett_drop must apply to H1/H2 (and P1/P2)"
    elif applies != {"A1", "A2"}:
        return "avsett_drop must apply to A1/A2 only"
    return None
