#!/usr/bin/env python3
"""RAM self-proof: knockback K1–K6 + prevention P1/P2 + heldout H1–H4.

Consumes facit-ram-paket-r3: knockback zone is the UNION of east-floor
cells 698–704 (not per-slot circles). A stationary grounded bot in that
union is post_recovery_idle, not a trap. §4a attribution runs before
prevention/heldout totals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from d_kvitto import (  # noqa: E402
    astar_path,
    make_kvitto,
    recipe_cvars,
    recipe_kvitto_paths,
    refuse_shared_kvitto_dir,
    write_attempt_raw_file,
    write_exclusive,
    write_kvitto,
)
from d_failclosed import change_freeze_reason, validate_anchors
from d_recipe import load_recipe, on_expected  # noqa: E402
from d_strata import (  # noqa: E402
    FORBIDDEN_CTL,
    FORBIDDEN_GAME,
    in_avsett_geometry,
    pair_start_vel_ok,
)
from d_turnering import raw_from_jsonl as _raw_from_jsonl  # noqa: E402

HERE = Path(__file__).resolve().parent
RAIL_GATES = HERE / "recept" / "ram-rail-gates.json"
RAIL_V2_GATES = HERE / "recept" / "ram-rail-v2-gates.json"
PREVENT_GATES = HERE / "recept" / "ram-prevent-gates.json"
RAIL_CELLS = {5977, 5978, 5979, 5980, 5981, 5982}
LAND = [-352.0, -672.0, -16.0]
RAIL_IDS = frozenset({"ram-rail", "ram-rail-v2"})
EAST_FLOOR_CELLS = (698, 699, 700, 701, 702, 703, 704)
EAST_FLOOR_Y = (-800.0, -768.0, -736.0, -704.0, -672.0, -640.0, -608.0)
LOCUS_TOL = 32.0
TIME_TOL = 0.01
IDLE_SPEED = 1.0
GATE_TOL = 24.0
CHAIN_IDS = frozenset({"P1", "P2", "H1", "H2", "H3", "H4"})
PREVENT_IDS = frozenset({"P1", "P2"})
HELDOUT_IDS = frozenset({"H1", "H2", "H3", "H4"})
AVSETT_STRATA = frozenset({"P1", "P2", "H1", "H2"})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


def default_knockback_zone() -> dict:
    return {
        "cells": list(EAST_FLOOR_CELLS),
        "origins": [[-288.0, float(y), -16.0] for y in EAST_FLOOR_Y],
        "union": {
            "x": -288.0,
            "x_tol": 32.0,
            "y_lo": -816.0,
            "y_hi": -592.0,
            "z": -16.0,
            "z_tol": 24.0,
        },
    }


def knockback_zone(gates: dict | None) -> dict:
    """r3: union of stamped east-floor cells 698–704, not per-slot circles."""
    if not isinstance(gates, dict):
        return default_knockback_zone()
    gmap = gates.get("gates") or {}
    for key in ("ram-rail-v2", "ram-rail", "ram-prevent"):
        block = gmap.get(key) or {}
        z = block.get("knockback_zone")
        if isinstance(z, dict) and (z.get("cells") or z.get("union")):
            return z
    top = gates.get("knockback_zone")
    if isinstance(top, dict) and (top.get("cells") or top.get("union")):
        return top
    return default_knockback_zone()


def _origin_of(obj: dict | None) -> list[float] | None:
    if not isinstance(obj, dict):
        return None
    o = obj.get("origin")
    if o and len(o) >= 3:
        return [float(x) for x in o[:3]]
    if all(k in obj for k in ("x", "y", "z")):
        return [float(obj["x"]), float(obj["y"]), float(obj["z"])]
    return None


def origin_in_zone(origin: list[float] | None, zone: dict) -> bool:
    if not origin or len(origin) < 3:
        return False
    u = zone.get("union") or {}
    try:
        x, y, z = float(origin[0]), float(origin[1]), float(origin[2])
        return (
            abs(x - float(u.get("x", -288.0))) <= float(u.get("x_tol", 32.0))
            and float(u.get("y_lo", -816.0)) <= y <= float(u.get("y_hi", -592.0))
            and abs(z - float(u.get("z", -16.0))) <= float(u.get("z_tol", 24.0))
        )
    except (TypeError, ValueError):
        return False


def cell_in_zone(cell: Any, zone: dict) -> bool:
    cells = {int(c) for c in (zone.get("cells") or []) if isinstance(c, int) or str(c).isdigit()}
    return isinstance(cell, int) and cell in cells


def in_knockback_zone(cell: Any, origin: list[float] | None, zone: dict) -> bool:
    return cell_in_zone(cell, zone) or origin_in_zone(origin, zone)


def nearest_east_floor_cell(origin: list[float]) -> int:
    best, bd = EAST_FLOOR_CELLS[0], float("inf")
    for cell, y in zip(EAST_FLOOR_CELLS, EAST_FLOOR_Y):
        d = math.hypot(float(origin[0]) - (-288.0), float(origin[1]) - float(y))
        if d < bd:
            best, bd = int(cell), d
    return best


def dist3(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) < 3 or len(b) < 3:
        return float("inf")
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def raw_from_jsonl(path: Path) -> dict:
    """Rebuild exec_* raw from live JSONL, keeping samples for zone membership."""
    raw = _raw_from_jsonl(path)
    samples: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") == "sample":
            samples.append(row)
    raw["samples"] = samples
    t_stall = None
    t_arrive = None
    for ev in raw.get("events") or []:
        if ev.get("ev") == "bot_stall" and t_stall is None:
            t_stall = ev.get("t") if ev.get("t") is not None else ev.get("rel_t")
        if ev.get("ev") == "arrived" and t_arrive is None:
            t_arrive = ev.get("t") if ev.get("t") is not None else ev.get("rel_t")
    if raw.get("t_stall_gate") is None and t_stall is not None:
        raw["t_stall_gate"] = float(t_stall)
    if raw.get("t_arrive") is None and t_arrive is not None:
        raw["t_arrive"] = float(t_arrive)
    return raw


def score_knockback_raw(raw: dict, zone: dict, budget_s: float = 2.0) -> dict:
    """Recompute land_hit against the 698–704 union. Classify post_recovery_idle.

    A stall after land_hit, grounded, speed ≤ 1, still in the union is idle,
    not a fail. Mocks without cell/origin evidence fall back to raw.land_hit.
    """
    events = list(raw.get("events") or [])
    samples = list(raw.get("samples") or [])
    t_land: float | None = None
    land_cell: int | None = None
    land_origin: list[float] | None = None

    for samp in samples:
        if not samp.get("on_ground"):
            continue
        o = _origin_of(samp)
        t = samp.get("t")
        if o and origin_in_zone(o, zone) and t is not None and float(t) <= float(budget_s):
            t_land = float(t)
            land_origin = o
            break

    stall_evs = [ev for ev in events if ev.get("ev") == "bot_stall"]
    for ev in events:
        o = _origin_of(ev)
        cell = ev.get("cell")
        t = ev.get("t") if ev.get("t") is not None else ev.get("rel_t")
        if in_knockback_zone(cell, o, zone):
            if cell_in_zone(cell, zone) and land_cell is None:
                land_cell = int(cell)
            if land_origin is None and o is not None:
                land_origin = o
            if t_land is None and t is not None and ev.get("ev") in {"bot_stall", "arrived"}:
                # stall-after-land is not the land instant; samples own t_land
                if ev.get("ev") == "arrived":
                    t_land = float(t)

    if land_cell is None:
        for ev in stall_evs:
            if cell_in_zone(ev.get("cell"), zone):
                land_cell = int(ev["cell"])
                if land_origin is None:
                    land_origin = _origin_of(ev)
                break
    if land_cell is None and cell_in_zone(raw.get("landing_cell"), zone):
        land_cell = int(raw["landing_cell"])
    if land_cell is None and land_origin is not None:
        land_cell = nearest_east_floor_cell(land_origin)
        if not cell_in_zone(land_cell, zone):
            land_cell = None

    evidence = bool(
        land_cell is not None
        or land_origin is not None
        or any(in_knockback_zone(ev.get("cell"), _origin_of(ev), zone) for ev in events)
        or any(origin_in_zone(_origin_of(s), zone) for s in samples)
    )
    if not evidence:
        land_hit = bool(raw.get("land_hit") or raw.get("t_arrive") is not None)
        stall = raw.get("t_stall_gate") is not None or bool(stall_evs)
        t_arr = raw.get("t_arrive")
        return {
            "land_hit": land_hit,
            "stall": stall,
            "post_recovery_idle": False,
            "landing_cell": None,
            "t_land": None if t_arr is None else float(t_arr),
        }

    land_hit = land_cell is not None and cell_in_zone(land_cell, zone)
    if land_hit and t_land is None:
        t_arr = raw.get("t_arrive")
        if t_arr is not None:
            t_land = float(t_arr)
        elif stall_evs:
            t = stall_evs[0].get("t") if stall_evs[0].get("t") is not None else stall_evs[0].get("rel_t")
            if t is not None:
                t_land = float(t)

    idle = False
    fail_stall = False
    for ev in stall_evs:
        t = ev.get("t") if ev.get("t") is not None else ev.get("rel_t")
        t_s = float(t) if t is not None else 0.0
        o = _origin_of(ev)
        cell = ev.get("cell")
        spd_raw = ev.get("speed")
        spd = float(spd_raw) if spd_raw is not None else 0.0
        in_z = in_knockback_zone(cell, o, zone)
        grounded = o is not None and abs(float(o[2]) - (-16.0)) <= 24.0
        after_land = land_hit and (t_land is None or t_s + 1e-9 >= t_land)
        if after_land and in_z and spd <= IDLE_SPEED and grounded:
            idle = True
        else:
            fail_stall = True
    if not stall_evs and raw.get("t_stall_gate") is not None and not land_hit:
        fail_stall = True

    return {
        "land_hit": bool(land_hit),
        "stall": bool(fail_stall),
        "post_recovery_idle": bool(idle),
        "landing_cell": land_cell,
        "t_land": t_land,
    }


def failure_events(raw: dict, *, avsett: dict | None, stratum: str) -> list[dict]:
    """Deduped ON fall/stall sequence. Avsett peak_drop is not a failure."""
    out: list[dict] = []
    seen_drop = False
    allow = avsett is not None and stratum in set(avsett.get("applies_to") or AVSETT_STRATA)
    for ev in raw.get("events") or []:
        name = ev.get("ev")
        if name == "peak_drop_150":
            if seen_drop:
                continue
            seen_drop = True
            if allow and in_avsett_geometry(
                avsett, origin=_origin_of(ev), cell_id=ev.get("cell") if isinstance(ev.get("cell"), int) else None
            ):
                continue
            out.append(ev)
        elif name == "bot_stall":
            out.append(ev)
    return out


def path_links(blob: dict | None) -> list[int]:
    if not isinstance(blob, dict):
        return []
    path = blob.get("path") if "path" in blob and isinstance(blob.get("path"), dict) else blob
    if not isinstance(path, dict):
        return []
    return [int(x) for x in (path.get("links") or [])]


def path_cells(blob: dict | None) -> list[int]:
    if not isinstance(blob, dict):
        return []
    path = blob.get("path") if "path" in blob and isinstance(blob.get("path"), dict) else blob
    if not isinstance(path, dict):
        return []
    return [int(x) for x in (path.get("cells") or [])]


def path_cost(blob: dict | None) -> float | None:
    if not isinstance(blob, dict):
        return None
    path = blob.get("path") if "path" in blob and isinstance(blob.get("path"), dict) else blob
    if not isinstance(path, dict):
        return None
    c = path.get("cost")
    return None if c is None else float(c)


def recipe_mutation_cells(recipe: dict) -> set[int]:
    """Cells the recipe adds (rail plants) or uses as new drop sources."""
    rid = recipe.get("id")
    if rid in RAIL_IDS:
        return set(RAIL_CELLS)
    out: set[int] = set()
    for row in recipe.get("source") or []:
        if isinstance(row, dict) and isinstance(row.get("cell"), int):
            out.add(int(row["cell"]))
    return out


def recipe_drop_pairs(recipe: dict) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    srcs = [r.get("cell") for r in (recipe.get("source") or []) if isinstance(r, dict)]
    tgts = [r.get("cell") for r in (recipe.get("target") or []) if isinstance(r, dict)]
    drops = recipe.get("drops") or []
    if drops and srcs and tgts and len(srcs) == len(tgts) == len(drops):
        for a, b in zip(srcs, tgts):
            if isinstance(a, int) and isinstance(b, int):
                pairs.add((a, b))
    return pairs


HARDKATALOG_SHA256 = "55a608426226522c45dd217c3741c9b9ff13d2d302e84511ffd810be506ad1fe"
HARDKATALOG_PATH = HERE / "recept" / "ram-hardkatalog.jsonl"
RAILKATALOG_SHA256 = "567d2ca0813adcf0eeadf122f8075965021f1d4dfbceb9d30c69bda6671986f9"
RAILKATALOG_PATH = HERE / "recept" / "ram-railkatalog.jsonl"
FACIT_RAM_R5_SHA256 = "088ee90971efe5574bc92c29e2491e645f947c0aa50cff01d6fbc408a1f4010e"
LEDGER_SCHEMA = "verktygslada/ram-r5-attribution-ledger/1"
KLASS_TO_EV = {"fall": "peak_drop_150", "fastnad": "bot_stall"}


def load_hardkatalog(
    path: Path | None = None,
    *,
    expected_sha: str | None = HARDKATALOG_SHA256,
) -> tuple[list[dict] | None, str]:
    """Load the pinned RAM härdkatalog. SHA mismatch ⇒ unusable (M1 only)."""
    p = Path(path or HARDKATALOG_PATH)
    if not p.is_file():
        return None, "missing"
    digest = sha256_file(p)
    if expected_sha and digest != expected_sha:
        return None, "sha_mismatch"
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows, "ok"


def catalog_row_allowed(recipe_id: str, row: dict) -> bool:
    """Cross-catalog forbid: rail rows never corroborate prevent, and vice versa."""
    rid = str(recipe_id or "")
    row_id = str((row or {}).get("id") or "")
    if rid in RAIL_IDS:
        return row_id.startswith("RAIL-")
    if rid == "ram-prevent":
        return row_id.startswith("RAM-GK")
    return False


def load_recipe_catalog(
    recipe_id: str,
    path: Path | None = None,
    *,
    expected_sha: str | None = None,
) -> tuple[list[dict] | None, str]:
    """Recipe-specific LED III source. SHA mismatch ⇒ unused (M1 only)."""
    rid = str(recipe_id or "")
    if rid in RAIL_IDS:
        return load_hardkatalog(
            path or RAILKATALOG_PATH,
            expected_sha=RAILKATALOG_SHA256 if expected_sha is None else expected_sha,
        )
    if rid == "ram-prevent":
        return load_hardkatalog(
            path or HARDKATALOG_PATH,
            expected_sha=HARDKATALOG_SHA256 if expected_sha is None else expected_sha,
        )
    return None, "no_catalog"


def _catalog_falt(row: dict) -> dict:
    ev = row.get("evidens") if isinstance(row, dict) else None
    falt = (ev or {}).get("falt") if isinstance(ev, dict) else None
    return falt if isinstance(falt, dict) else {}


def _catalog_cell(falt: dict) -> int | None:
    c = falt.get("cell")
    if c is None or c == "":
        return None
    try:
        return int(c)
    except (TypeError, ValueError):
        return None


def _catalog_locus(falt: dict) -> list[float] | None:
    loc = falt.get("locus")
    if not isinstance(loc, (list, tuple)) or len(loc) < 3:
        return None
    return [float(x) for x in loc[:3]]


def _route_first_last_ok(falt: dict, live_cells: list[int] | None) -> bool:
    rc = [int(x) for x in (falt.get("rutt_celler") or []) if str(x).lstrip("-").isdigit()]
    if not rc or not live_cells:
        return False
    return rc[0] == int(live_cells[0]) and rc[-1] == int(live_cells[-1])


def _route_goal_ok(falt: dict, live_goal: list[float] | None) -> bool:
    mal = falt.get("rutt_mal")
    if not isinstance(mal, (list, tuple)) or len(mal) < 3 or not live_goal or len(live_goal) < 3:
        return False
    return dist3([float(x) for x in mal[:3]], [float(x) for x in live_goal[:3]]) <= 1e-6


def _route_exact_ok(falt: dict, live_cells: list[int] | None, live_goal: list[float] | None) -> bool:
    rc = [int(x) for x in (falt.get("rutt_celler") or []) if str(x).lstrip("-").isdigit()]
    if list(live_cells or []) != rc:
        return False
    return _route_goal_ok(falt, live_goal)


def catalog_corroborate(
    rows: list[dict] | None,
    *,
    recipe_id: str,
    stratum: str,
    off_ev: dict | None,
    on_ev: dict | None,
    live_cells: list[int] | None,
    live_goal: list[float] | None,
) -> dict | None:
    """LED III via the recipe-owned catalog. Fail-closed on every listed prohibition."""
    if not rows:
        return None
    if str(stratum or "").startswith("K"):
        return None
    if str(recipe_id or "") not in RAIL_IDS and str(recipe_id or "") != "ram-prevent":
        return None
    if not isinstance(off_ev, dict) or not isinstance(on_ev, dict):
        return None
    ev_name = on_ev.get("ev")
    if ev_name != off_ev.get("ev"):
        return None
    off_cell = off_ev.get("cell") if isinstance(off_ev.get("cell"), int) else None
    on_cell = on_ev.get("cell") if isinstance(on_ev.get("cell"), int) else None
    off_o = _origin_of(off_ev)
    on_o = _origin_of(on_ev)

    for row in rows:
        if not catalog_row_allowed(recipe_id, row):
            continue
        falt = _catalog_falt(row)
        if str(falt.get("arm") or "").strip().lower() != "mixed":
            continue
        if KLASS_TO_EV.get(str(falt.get("handelse_klass") or "")) != ev_name:
            continue
        cat_cell = _catalog_cell(falt)
        cat_loc = _catalog_locus(falt)

        if off_cell is None and on_cell is None:
            if cat_loc is None or off_o is None or on_o is None:
                continue
            if dist3(off_o, cat_loc) > 16.0 or dist3(on_o, cat_loc) > 16.0:
                continue
            if not _route_exact_ok(falt, live_cells, live_goal):
                continue
            return {"id": row.get("id"), "cell": cat_cell, "via": "catalog_cell_unknown"}

        if off_cell is None or on_cell is None:
            continue
        if off_cell != on_cell or cat_cell != on_cell:
            continue
        if cat_loc is None or on_o is None or dist3(on_o, cat_loc) > LOCUS_TOL:
            continue
        if off_o is not None and dist3(off_o, cat_loc) > LOCUS_TOL:
            continue
        if not _route_first_last_ok(falt, live_cells) or not _route_goal_ok(falt, live_goal):
            continue
        return {"id": row.get("id"), "cell": cat_cell, "via": "catalog"}
    return None


def astar_blob(raw: dict, receipt: dict | None = None) -> tuple[dict, dict]:
    before = raw.get("astar_before") or {}
    after = raw.get("astar_after") or {}
    if receipt:
        ast = receipt.get("astar") or {}
        if not before:
            before = ast.get("before") or {}
        if not after:
            after = ast.get("after") or {}
    return before, after


def attribute_pair(
    off_raw: dict,
    on_raw: dict,
    *,
    stratum: str,
    off_id: str,
    on_id: str,
    recipe: dict,
    avsett: dict | None,
    clusters: list[dict],
    off_receipt: dict | None = None,
    on_receipt: dict | None = None,
    gate_tol: float = GATE_TOL,
    catalog: list[dict] | None = None,
    live_goal: list[float] | None = None,
) -> dict:
    """§4a four-leg test. All four required. Missing field → not attributed."""
    legs = {"same_zone": False, "no_route_effect": False, "m1": False, "separated": False}
    off_sig = failure_events(off_raw, avsett=avsett, stratum=stratum)
    on_sig = failure_events(on_raw, avsett=avsett, stratum=stratum)
    reasons: list[str] = []
    if [ev.get("ev") for ev in off_sig] != [ev.get("ev") for ev in on_sig] or not on_sig:
        reasons.append("event signature mismatch or empty")
    else:
        ok = True
        for a, b in zip(off_sig, on_sig):
            oa, ob = _origin_of(a), _origin_of(b)
            if dist3(oa, ob) > LOCUS_TOL:
                ok = False
                reasons.append("locus > 32 u")
                break
            ta = a.get("t") if a.get("t") is not None else a.get("rel_t")
            tb = b.get("t") if b.get("t") is not None else b.get("rel_t")
            if ta is not None and tb is not None and abs(float(ta) - float(tb)) > TIME_TOL:
                ok = False
                reasons.append("timestamp > 0.01 s")
                break
        legs["same_zone"] = ok

    on_before, on_after = astar_blob(on_raw, on_receipt)
    off_before, off_after = astar_blob(off_raw, off_receipt)
    # Prefer ON before/after; fall back to OFF receipt if ON lacks a path.
    before, after = on_before, on_after
    if not path_links(before) and path_links(off_before):
        before, after = off_before, off_after
    lb, la = path_links(before), path_links(after)
    cb, ca = path_cost(before), path_cost(after)
    cells = path_cells(after) or path_cells(before)
    mutated = False
    added = recipe_mutation_cells(recipe)
    if added and (set(cells) & added or set(path_cells(before)) & added):
        mutated = True
    pairs = recipe_drop_pairs(recipe)
    if pairs:
        seq = path_cells(after) or path_cells(before)
        for i in range(len(seq) - 1):
            if (seq[i], seq[i + 1]) in pairs:
                mutated = True
                break
    if not lb or lb != la or cb is None or ca is None or abs(cb - ca) > 1e-9 or mutated:
        reasons.append("A* before/after mismatch or recipe mutation")
    else:
        legs["no_route_effect"] = True

    episode_origin = None
    episode_cell = None
    for ev in on_sig:
        episode_origin = _origin_of(ev) or episode_origin
        if isinstance(ev.get("cell"), int):
            episode_cell = int(ev["cell"])
    hit = None
    for cl in clusters:
        co = cl.get("origin")
        if not isinstance(co, (list, tuple)) or len(co) < 3:
            continue
        if episode_origin is not None and dist3(episode_origin, [float(x) for x in co[:3]]) <= LOCUS_TOL:
            hit = cl
            break
        if episode_cell is not None and cl.get("cell") == episode_cell:
            hit = cl
            break
    live_cells = path_cells(after) or path_cells(before)
    cat_hit = catalog_corroborate(
        catalog,
        recipe_id=str(recipe.get("id") or ""),
        stratum=stratum,
        off_ev=off_sig[0] if off_sig else None,
        on_ev=on_sig[0] if on_sig else None,
        live_cells=live_cells,
        live_goal=live_goal,
    )
    if hit is None and cat_hit is None:
        reasons.append("no M1/baseline cluster or catalog mixed row within 32 u")
    else:
        legs["m1"] = True

    recipe_cells = set(added)
    for row in (recipe.get("target") or []):
        if isinstance(row, dict) and isinstance(row.get("cell"), int):
            # existing targets (669/670) are not recipe-added cells
            pass
    min_d = float("inf")
    if episode_cell is not None and episode_cell in recipe_cells:
        min_d = 0.0
    for cid in recipe_cells:
        # no origin for rail cells here; cell match already handled
        pass
    if avsett and episode_origin is not None:
        if in_avsett_geometry(avsett, origin=episode_origin, cell_id=episode_cell):
            min_d = 0.0
        else:
            corridor = avsett.get("corridor") or {}
            try:
                x, y, z = episode_origin
                dx = 0.0
                if x < float(corridor["xmin"]):
                    dx = float(corridor["xmin"]) - x
                elif x > float(corridor["xmax"]):
                    dx = x - float(corridor["xmax"])
                dy = 0.0
                if y < float(corridor["ymin"]):
                    dy = float(corridor["ymin"]) - y
                elif y > float(corridor["ymax"]):
                    dy = y - float(corridor["ymax"])
                dz = 0.0
                if z < float(corridor["zmin"]):
                    dz = float(corridor["zmin"]) - z
                elif z > float(corridor["zmax"]):
                    dz = z - float(corridor["zmax"])
                min_d = min(min_d, math.sqrt(dx * dx + dy * dy + dz * dz))
            except (KeyError, TypeError, ValueError):
                reasons.append("avsett corridor unreadable")
    if episode_origin is None and episode_cell is None:
        reasons.append("episode has no locus")
    elif min_d <= gate_tol:
        reasons.append(f"not separated from recipe/avsett ({min_d:.1f} ≤ {gate_tol})")
    else:
        legs["separated"] = True

    ok = all(legs.values())
    post = {
        "kind": "preexisting_hazard",
        "off_attempt": off_id,
        "on_attempt": on_id,
        "stratum": stratum,
        "event_signature": [ev.get("ev") for ev in on_sig],
        "off_events": [
            {"ev": ev.get("ev"), "t": ev.get("t"), "cell": ev.get("cell"), "origin": _origin_of(ev)}
            for ev in off_sig
        ],
        "on_events": [
            {"ev": ev.get("ev"), "t": ev.get("t"), "cell": ev.get("cell"), "origin": _origin_of(ev)}
            for ev in on_sig
        ],
        "loci": {"off": _origin_of(off_sig[0]) if off_sig else None, "on": episode_origin},
        "path_links": la or lb,
        "path_cost": ca if ca is not None else cb,
        "m1_cluster": hit,
        "catalog_row": cat_hit,
        "min_distance": None if min_d == float("inf") else min_d,
        "legs": dict(legs),
        "attributed": ok,
        "reasons": reasons,
        "raw_off": off_raw.get("raw_pointer"),
        "raw_on": on_raw.get("raw_pointer"),
    }
    return {"ok": ok, "legs": legs, "post": post, "reasons": reasons}


def empty_arm_counts() -> dict:
    return {
        "on_ok": True,
        "attempted": 0,
        "eligible": 0,
        "preexisting_hazards": 0,
        "non_attributed_failures": 0,
    }


def knockback_fields(spec: dict, raw: dict, land_hit: bool) -> dict:
    """K-punkt-id, inkommande velocity, land_hit, marknivåtid."""
    t_land = raw.get("t_arrive") if land_hit else None
    vel = raw.get("commanded_vel") or raw.get("vel") or spec.get("velocity") or []
    return {
        "point": str(spec.get("id") or ""),
        "incoming_velocity": [float(x) for x in vel[:3]] if vel else list(spec.get("velocity") or []),
        "land_hit": bool(land_hit),
        "t_land": None if t_land is None else float(t_land),
    }


def knockback_points(gates: dict) -> list[dict]:
    gmap = gates.get("gates") or {}
    block = gmap.get("ram-rail-v2") or gmap.get("ram-rail") or {}
    drop_tos = list(block.get("drop_to_origins") or [])
    default_land = list((block.get("land") or {}).get("origin") or LAND)
    rows = []
    for i, kb in enumerate(gates.get("knockback") or []):
        land = list(drop_tos[i]) if i < len(drop_tos) else default_land
        rows.append({
            "id": str(kb["id"]),
            "y": float(kb["y"]),
            "cell": int(kb["cell"]),
            "velocity": [float(x) for x in kb["velocity"]],
            "budget_s": float(kb.get("budget_s") or 2.0),
            "pos": [-360.0, float(kb["y"]), 128.03125],
            "land": land,
            "n_pairs": int(gates.get("n_pairs") or 2),
        })
    return rows


def prevention_specs(gates: dict) -> list[dict]:
    prev = gates.get("prevention") or {}
    start = list((prev.get("start") or {}).get("origin") or [256.0, -704.0, 328.0])
    goal = list((prev.get("goal") or {}).get("aim") or (prev.get("goal") or {}).get("origin") or [-593.0, -677.0, -16.0])
    out = []
    for key, vh in (("P1", (80.0, 160.0, False)), ("P2", (160.0, 260.0, True))):
        row = prev.get(key) or {}
        out.append({
            "id": key,
            "kind": "heldout",
            "start": start,
            "goal": goal,
            "vh_lo": float(row.get("vh_lo") or vh[0]),
            "vh_hi": float(row.get("vh_hi") or vh[1]),
            "vh_hi_inclusive": vh[2],
            "dir_min": 0.8,
            "budget_s": 25.0,
            "n_pairs": int(row.get("n_pairs") or 5),
            "population": "kedjad",
        })
    return out


def heldout_specs() -> list[dict]:
    """H1–H4: same geometry as D r2 A1–A4, distinct attempt ids."""
    ut = [256.0, -704.0, 328.0]
    inn = [-593.0, -677.0, -16.0]
    rows = [
        ("H1", ut, inn, 80.0, 160.0, False),
        ("H2", ut, inn, 160.0, 260.0, True),
        ("H3", inn, ut, 80.0, 160.0, False),
        ("H4", inn, ut, 160.0, 260.0, True),
    ]
    out = []
    for sid, start, goal, lo, hi, inc in rows:
        out.append({
            "id": sid,
            "kind": "heldout",
            "start": start,
            "goal": goal,
            "vh_lo": lo,
            "vh_hi": hi,
            "vh_hi_inclusive": inc,
            "dir_min": 0.8,
            "budget_s": 25.0,
            "n_pairs": 5,
            "population": "kedjad",
        })
    return out


def intended_drop(raw: dict, land: list[float]) -> bool:
    for ev in raw.get("events") or []:
        if ev.get("ev") != "peak_drop_150":
            continue
        o = ev.get("origin")
        if o and abs(float(o[2]) - land[2]) <= 24.0:
            if abs(float(o[0]) - land[0]) <= 48.0 and abs(float(o[1]) - land[1]) <= 48.0:
                continue
            return False
    return True


@dataclass
class RAttempt:
    attempt_id: str
    stratum: str
    arm: str
    valid: bool
    reason: str
    stall: bool = False
    arrived: bool = False
    land_hit: bool = False
    post_recovery_idle: bool = False
    attributed: bool = False
    start_vel: list[float] | None = None
    t_land: float | None = None
    incoming_vel: list[float] | None = None
    landing_cell: int | None = None
    seq: int = 0


@dataclass
class RamReport:
    recipe: str
    valid: bool
    invalid_reasons: list[str]
    attempts: list[RAttempt]
    knockback_off_ok: bool = False
    knockback_on_ok: bool = False
    prevent_on_ok: bool = False
    heldout_on_ok: bool = False
    pass_ok: bool = False
    prevention_counts: dict = field(default_factory=empty_arm_counts)
    heldout_counts: dict = field(default_factory=empty_arm_counts)
    hazard_posts: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        prev = dict(self.prevention_counts)
        prev["on_ok"] = self.prevent_on_ok
        held = dict(self.heldout_counts)
        held["on_ok"] = self.heldout_on_ok
        return {
            "kind": "ram-sjalvbevis",
            "recipe": self.recipe,
            "valid": self.valid,
            "invalid_reasons": list(self.invalid_reasons),
            "godkand": self.valid and self.pass_ok,
            "knockback": {"off_ok": self.knockback_off_ok, "on_ok": self.knockback_on_ok},
            "prevention": prev,
            "heldout": held,
            "preexisting_hazard_posts": list(self.hazard_posts),
            "attempts": [
                {
                    "id": a.attempt_id,
                    "stratum": a.stratum,
                    "arm": a.arm,
                    "valid": a.valid,
                    "reason": a.reason,
                    "stall": a.stall,
                    "arrived": a.arrived,
                    "land_hit": a.land_hit,
                    "post_recovery_idle": a.post_recovery_idle,
                    "attributed": a.attributed,
                    "landing_cell": a.landing_cell,
                }
                for a in self.attempts
            ],
        }


class RamRunner:
    def __init__(
        self,
        *,
        recipe: dict,
        rail_gates: dict,
        prevent_gates: dict | None = None,
        exec_knockback: Callable[..., dict],
        exec_trial: Callable[..., dict],
        ctl_port: int,
        game_port: int,
        ensure_arm: Callable[[str], None] | None = None,
        n_knock: int | None = None,
        n_chain: int | None = None,
        on_attempt: Callable[[RAttempt, dict], None] | None = None,
        kvitto_dir: Path | None = None,
        demo_file: str | None = None,
        binaries: dict[str, str] | None = None,
        fixture_sha256: str | None = None,
        allow_shared: bool = False,
        catalog_path: Path | None = None,
        catalog_sha: str | None = None,
    ) -> None:
        self.recipe = recipe
        self.rail_gates = rail_gates
        self.prevent_gates = prevent_gates or {}
        self.exec_knockback = exec_knockback
        self.exec_trial = exec_trial
        self.ctl_port = ctl_port
        self.game_port = game_port
        self.ensure_arm = ensure_arm
        self.on_attempt = on_attempt
        self.kvitto_dir = Path(kvitto_dir) if kvitto_dir else None
        if self.kvitto_dir is not None:
            refuse_shared_kvitto_dir(
                self.kvitto_dir, str(recipe.get("id") or ""), allow_shared=allow_shared,
            )
        self.demo_file = demo_file
        self.binaries = dict(binaries or {})
        self.fixture_sha256 = fixture_sha256 or ""
        self.last_raw: dict = {}
        self.zone = knockback_zone(rail_gates)
        self.avsett = (
            (self.prevent_gates.get("avsett_drop") if self.prevent_gates else None)
            or (rail_gates.get("avsett_drop") if rail_gates else None)
        )
        self.clusters = list(
            (self.prevent_gates.get("baseline_clusters") if self.prevent_gates else None)
            or (rail_gates.get("baseline_clusters") if rail_gates else None)
            or []
        )
        self.catalog, self.catalog_status = load_recipe_catalog(
            str(recipe.get("id") or ""),
            catalog_path,
            expected_sha=catalog_sha,
        )
        self.kb = knockback_points(rail_gates)
        self.p = prevention_specs(self.prevent_gates) if recipe.get("id") == "ram-prevent" else []
        self.h = heldout_specs() if recipe.get("id") in {"ram-rail", "ram-rail-v2", "ram-prevent"} else []
        if recipe.get("id") in RAIL_IDS:
            self.p = []
        if n_knock is not None:
            for s in self.kb:
                s["n_pairs"] = int(n_knock)
        if n_chain is not None:
            for s in self.p + self.h:
                s["n_pairs"] = int(n_chain)
        self.live_goals = {
            s["id"]: list(s.get("goal") or [])
            for s in (self.p + self.h)
            if s.get("id")
        }
        self._raw_by_id: dict[str, dict] = {}
        self.hazard_posts: list[dict] = []

    def preflight(self) -> list[str]:
        reasons = []
        frozen = change_freeze_reason()
        if frozen:
            reasons.append(frozen)
        if self.ctl_port in FORBIDDEN_CTL or self.game_port in FORBIDDEN_GAME:
            reasons.append("RA/main endpoint")
        aw = validate_anchors(self.recipe)
        if aw:
            reasons.append(aw)
        rid = self.recipe.get("id")
        if rid not in {"ram-rail", "ram-rail-v2", "ram-prevent"}:
            reasons.append(f"not a ram recipe: {rid!r}")
        try:
            on_expected(self.recipe)
        except ValueError as exc:
            reasons.append(str(exc))
        if rid in RAIL_IDS and len(self.kb) != 6:
            reasons.append(f"knockback points {len(self.kb)} != 6")
        ids = [s["id"] for s in self.kb]
        if rid in RAIL_IDS and ids != ["K1", "K2", "K3", "K4", "K5", "K6"]:
            reasons.append(f"knockback ids {ids} != K1–K6")
        return reasons

    def _run_kb(self, spec: dict, arm: str, seq: int) -> RAttempt:
        if self.ensure_arm is not None:
            self.ensure_arm(arm)
        raw = self.exec_knockback(
            pos=spec["pos"],
            vel=spec["velocity"],
            land=spec["land"],
            window_s=spec["budget_s"],
            seq=seq,
            arm=arm,
            stratum_id=spec["id"],
        )
        scored = score_knockback_raw(raw, self.zone, float(spec.get("budget_s") or 2.0))
        stall = bool(scored["stall"])
        land_hit = bool(scored["land_hit"])
        idle = bool(scored["post_recovery_idle"])
        planned = intended_drop(raw, spec["land"]) or (
            any(
                ev.get("ev") == "peak_drop_150"
                and in_knockback_zone(ev.get("cell"), _origin_of(ev), self.zone)
                for ev in (raw.get("events") or [])
            )
            or not any(ev.get("ev") == "peak_drop_150" for ev in (raw.get("events") or []))
        )
        att = RAttempt(
            attempt_id=f"{spec['id']}-{arm.upper()}-{seq:02d}",
            stratum=spec["id"],
            arm=arm,
            valid=True,
            reason="ok",
            stall=stall,
            arrived=land_hit,
            land_hit=land_hit,
            post_recovery_idle=idle,
            landing_cell=scored.get("landing_cell"),
            start_vel=list(spec["velocity"]),
            t_land=scored.get("t_land"),
            incoming_vel=list(spec["velocity"]),
            seq=seq,
        )
        if arm == "off":
            raw_stall = stall or raw.get("t_stall_gate") is not None or any(
                ev.get("ev") == "bot_stall" for ev in (raw.get("events") or [])
            )
            if not raw_stall:
                att.valid = False
                att.reason = "OFF knockback must bot_stall on the rail"
            else:
                att.stall = True
        else:
            if not land_hit:
                att.valid = False
                att.reason = "ON knockback must stand on land within 2.0 s"
            elif stall:
                att.valid = False
                att.reason = "ON knockback must not stall"
            elif not planned:
                att.valid = False
                att.reason = "unplanned peak_drop (not in the 698-704 union)"
            elif idle:
                att.reason = "post_recovery_idle"
        raw = dict(raw)
        raw["knockback"] = knockback_fields(spec, raw, land_hit)
        if att.landing_cell is not None:
            raw["landing_cell"] = att.landing_cell
        self.last_raw = raw
        self._raw_by_id[att.attempt_id] = raw
        self._emit(att, raw)
        return att

    def _run_chain(self, spec: dict, arm: str, seq: int, match_vel=None) -> RAttempt:
        if self.ensure_arm is not None:
            self.ensure_arm(arm)
        raw = self.exec_trial(
            stratum_id=spec["id"], arm=arm, spec=spec, seq=seq, match_vel=match_vel,
            window_s=float(spec["budget_s"]),
        )
        stall = raw.get("t_stall_gate") is not None or any(
            ev.get("ev") == "bot_stall" for ev in (raw.get("events") or [])
        )
        t_arr = raw.get("t_arrive")
        if t_arr is None:
            for ev in raw.get("events") or []:
                if ev.get("ev") == "arrived":
                    t_arr = ev.get("t") if ev.get("t") is not None else ev.get("rel_t")
                    break
        arrived = t_arr is not None and float(t_arr) <= spec["budget_s"]
        rail_entry = raw.get("start_cell") in RAIL_CELLS or raw.get("gate_cell") in RAIL_CELLS
        att = RAttempt(
            attempt_id=f"{spec['id']}-{arm.upper()}-{seq:02d}",
            stratum=spec["id"],
            arm=arm,
            valid=raw.get("stamp_ok", True) is not False,
            reason=raw.get("stamp_reason") or "ok",
            stall=stall,
            arrived=arrived,
            start_vel=list(raw.get("measured_vel") or raw.get("vel") or []),
            t_land=None if t_arr is None else float(t_arr),
            seq=seq,
        )
        if arm == "on" and att.valid:
            if not arrived or stall or rail_entry:
                att.valid = False
                att.reason = "ON chain must arrive, no stall, no rail entry"
        raw = dict(raw)
        raw["raw_pointer"] = raw.get("raw_pointer")
        self.last_raw = raw
        self._raw_by_id[att.attempt_id] = raw
        self._emit(att, raw)
        return att

    def _emit(self, att: RAttempt, raw: dict) -> None:
        if self.on_attempt is not None:
            self.on_attempt(att, raw)
        elif self.kvitto_dir is not None:
            self._write_attempt_kvitto(att, raw)

    def _write_attempt_kvitto(self, att: RAttempt, raw: dict) -> Path:
        """Same make_kvitto path as the tournament runner. kvitto-dir is the contract."""
        if self.kvitto_dir is None:
            raise RuntimeError("kvitto_dir is not set")
        rid = str(self.recipe.get("id") or "")
        path, raw_path = recipe_kvitto_paths(self.kvitto_dir, rid, att.attempt_id)
        empty = astar_path(found=False)
        after = raw.get("astar_after") or empty
        before = raw.get("astar_before") or empty
        nb = raw.get("astar_next_best") or empty
        off = dict(self.recipe.get("off") or {})
        try:
            on = on_expected(self.recipe)
        except ValueError:
            on = dict(off)
        qw = self.binaries.get("qwprogs_sha256") or "00" * 32
        mv = self.binaries.get("mvdsv_sha256") or "00" * 32
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        landing = raw.get("landing_cell") if raw.get("landing_cell") is not None else att.landing_cell
        selected = raw.get("selected_link")
        kb = raw.get("knockback") if att.stratum.startswith("K") else None
        gate_vel = None
        if kb and kb.get("incoming_velocity"):
            gate_vel = list(kb["incoming_velocity"])
        elif att.incoming_vel:
            gate_vel = list(att.incoming_vel)
        elif raw.get("gate_velocity"):
            gate_vel = list(raw["gate_velocity"])
        doc = make_kvitto(
            riglock_owner="fable",
            riglock_issued_at=now,
            riglock_valid_from=now,
            riglock_valid_to=now,
            riglock_path="lab/.rig-lock",
            run_started_at=now,
            run_ended_at=now,
            endpoint_host="127.0.0.1",
            endpoint_ctl_port=self.ctl_port,
            endpoint_game_port=self.game_port,
            map_name="dm3",
            binary_sha256=qw,
            commit="unrun",
            stamps_off_expected=off,
            stamps_off_observed=off,
            stamps_on_expected=on,
            stamps_on_observed=on,
            stamps_undo_expected=off,
            stamps_undo_observed=off,
            recipe={
                "id": self.recipe["id"],
                "taxonomy_class": self.recipe.get("taxonomy_class") or "carve_origin",
                "evidence": self.recipe.get("evidence") or "ram self-proof attempt",
            },
            seed=0,
            stratum={"id": att.stratum, "attempt": att.attempt_id},
            raw_pointer=str(raw_path.resolve()),
            astar_before=before if isinstance(before, dict) else empty,
            astar_after=after if isinstance(after, dict) else empty,
            astar_next_best=nb if isinstance(nb, dict) else empty,
            gate_velocity=gate_vel,
            gate_cell=raw.get("gate_cell"),
            demo_file=self.demo_file or "qw/demos/unrun.mvd",
            fixture_sha256=self.fixture_sha256 or None,
            candidate=str(self.recipe.get("id")),
            landing_cell=int(landing) if isinstance(landing, int) else None,
            selected_link=int(selected) if isinstance(selected, int) else None,
            knockback=kb if isinstance(kb, dict) else None,
            cvars=recipe_cvars(self.recipe),
        )
        doc["binaries"] = {"qwprogs_sha256": qw, "mvdsv_sha256": mv}
        write_attempt_raw_file(raw_path, raw, exclusive=True)
        write_kvitto(path, doc, exclusive=True, verify_first=True)
        return path

    def run(self) -> RamReport:
        reasons = self.preflight()
        attempts: list[RAttempt] = []
        if reasons:
            return RamReport(str(self.recipe.get("id")), False, reasons, attempts)
        rid = self.recipe.get("id")
        extra: list[str] = []
        if rid in RAIL_IDS:
            for spec in self.kb:
                for i in range(1, int(spec["n_pairs"]) + 1):
                    attempts.append(self._run_kb(spec, "off", i))
                    attempts.append(self._run_kb(spec, "on", i))
            for spec in self.h:
                need = int(spec["n_pairs"])
                for i in range(1, need + 1):
                    off = self._run_chain(spec, "off", i)
                    on = self._run_chain(spec, "on", i, match_vel=off.start_vel)
                    pok, pwhy = pair_start_vel_ok(off.start_vel, on.start_vel)
                    if not pok:
                        off.valid = on.valid = False
                        on.reason = pwhy
                    attempts.append(off)
                    attempts.append(on)
        else:
            for spec in self.p + self.h:
                need = int(spec["n_pairs"])
                for i in range(1, need + 1):
                    off = self._run_chain(spec, "off", i)
                    on = self._run_chain(spec, "on", i, match_vel=off.start_vel)
                    pok, _ = pair_start_vel_ok(off.start_vel, on.start_vel)
                    if not pok:
                        off.valid = on.valid = False
                    attempts.append(off)
                    attempts.append(on)

        kb_off = [a for a in attempts if a.stratum.startswith("K") and a.arm == "off" and a.valid]
        kb_on = [a for a in attempts if a.stratum.startswith("K") and a.arm == "on" and a.valid]
        kb_off_ok = rid not in RAIL_IDS or (len(kb_off) == 12 and all(a.stall for a in kb_off))
        kb_on_ok = rid not in RAIL_IDS or (
            len(kb_on) == 12 and all(a.land_hit and not a.stall for a in kb_on)
        )
        if rid in RAIL_IDS and (n_knock := self.kb[0]["n_pairs"]) != 2:
            kb_off_ok = len(kb_off) == 6 * n_knock and all(a.stall for a in kb_off)
            kb_on_ok = len(kb_on) == 6 * n_knock and all(a.land_hit and not a.stall for a in kb_on)

        posts = self._attribute_chain(attempts)
        p_counts = self._arm_counts(attempts, PREVENT_IDS)
        h_counts = self._arm_counts(attempts, HELDOUT_IDS)
        want_p = sum(s["n_pairs"] for s in self.p)
        want_h = sum(s["n_pairs"] for s in self.h)
        if rid in RAIL_IDS:
            p_ok = True
            p_counts = empty_arm_counts()
        else:
            p_ok = p_counts["attempted"] == want_p and p_counts["non_attributed_failures"] == 0
        if want_h == 0:
            h_ok = True
            h_counts = empty_arm_counts()
        else:
            h_ok = h_counts["attempted"] == want_h and h_counts["non_attributed_failures"] == 0
        valid = not extra
        pass_ok = valid and kb_off_ok and kb_on_ok and p_ok and h_ok
        return RamReport(
            str(rid), valid, extra, attempts,
            knockback_off_ok=kb_off_ok, knockback_on_ok=kb_on_ok,
            prevent_on_ok=p_ok, heldout_on_ok=h_ok, pass_ok=pass_ok,
            prevention_counts=p_counts, heldout_counts=h_counts,
            hazard_posts=posts,
        )

    def _attribute_chain(self, attempts: list[RAttempt]) -> list[dict]:
        """§4a: run before prevention/heldout totals. Mandatory on every ON fall/stall."""
        posts: list[dict] = []
        by_key: dict[tuple[str, int], dict[str, RAttempt]] = {}
        for a in attempts:
            if a.stratum not in CHAIN_IDS:
                continue
            by_key.setdefault((a.stratum, a.seq), {})[a.arm] = a
        for (stratum, _seq), pair in by_key.items():
            on = pair.get("on")
            off = pair.get("off")
            if on is None or off is None:
                continue
            on_raw = self._raw_by_id.get(on.attempt_id) or {}
            fails = failure_events(on_raw, avsett=self.avsett, stratum=stratum)
            if not fails and on.arrived and not on.stall:
                continue
            if not fails and not on.valid:
                # timeout / rail-entry without a fall/stall sequence stays a failure
                continue
            if not fails:
                continue
            off_raw = self._raw_by_id.get(off.attempt_id) or {}
            result = attribute_pair(
                off_raw,
                on_raw,
                stratum=stratum,
                off_id=off.attempt_id,
                on_id=on.attempt_id,
                recipe=self.recipe,
                avsett=self.avsett,
                clusters=self.clusters,
                catalog=self.catalog,
                live_goal=self.live_goals.get(stratum),
            )
            posts.append(result["post"])
            if result["ok"]:
                on.attributed = True
                on.valid = True
                on.reason = "preexisting_hazard"
        self.hazard_posts = posts
        return posts

    def _arm_counts(self, attempts: list[RAttempt], ids: set[str]) -> dict:
        on = [a for a in attempts if a.stratum in ids and a.arm == "on"]
        attempted = len(on)
        attributed = [a for a in on if a.attributed]
        eligible = [a for a in on if not a.attributed]
        failures = [
            a for a in eligible
            if not a.valid or not a.arrived or a.stall
        ]
        return {
            "on_ok": attempted == 0 or (len(failures) == 0 and len(eligible) == attempted - len(attributed)),
            "attempted": attempted,
            "eligible": len(eligible),
            "preexisting_hazards": len(attributed),
            "non_attributed_failures": len(failures),
        }


def _parse_attempt_stem(stem: str) -> tuple[str, str, int] | None:
    parts = str(stem or "").split("-")
    if len(parts) < 3:
        return None
    arm = parts[1].lower()
    if arm not in {"off", "on"}:
        return None
    try:
        seq = int(parts[2])
    except ValueError:
        return None
    stratum = parts[0]
    if not stratum or stratum.startswith("K"):
        return None
    if stratum not in CHAIN_IDS:
        return None
    return stratum, arm, seq


def discover_receipt_files(kvitto_dir: Path, recipe_id: str) -> dict[str, Path]:
    """Existing receipts only. Never invents stems or pairs."""
    roots: list[Path] = []
    nested = Path(kvitto_dir) / str(recipe_id)
    if nested.is_dir():
        roots.append(nested)
    if Path(kvitto_dir).is_dir():
        roots.append(Path(kvitto_dir))
    found: dict[str, Path] = {}
    for root in roots:
        for rec in sorted(root.glob("*.json")):
            if rec.stem in found:
                continue
            parsed = _parse_attempt_stem(rec.stem)
            if parsed is None:
                continue
            found[rec.stem] = rec
    return found


def _raw_path_of(doc: dict, receipt_path: Path) -> Path | None:
    raw_ptr = doc.get("raw_pointer")
    if not isinstance(raw_ptr, str) or not raw_ptr.strip():
        sibling = receipt_path.with_suffix(".jsonl")
        return sibling if sibling.is_file() else None
    head = raw_ptr.split("#", 1)[0]
    p = Path(head)
    if p.is_file():
        return p
    sibling = receipt_path.with_suffix(".jsonl")
    return sibling if sibling.is_file() else None


def _inject_receipt_astar(raw: dict, doc: dict, raw_path: Path) -> dict:
    ast = doc.get("astar") or {}
    if not raw.get("astar_before"):
        raw["astar_before"] = ast.get("before") or {}
    if not raw.get("astar_after"):
        raw["astar_after"] = ast.get("after") or {}
    raw["raw_pointer"] = str(raw_path)
    return raw


def _existing_pairs(receipts: dict[str, Path]) -> list[tuple[str, int, Path, Path]]:
    pairs: list[tuple[str, int, Path, Path]] = []
    seen: set[tuple[str, int]] = set()
    for stem, off_path in sorted(receipts.items()):
        parsed = _parse_attempt_stem(stem)
        if parsed is None or parsed[1] != "off":
            continue
        stratum, _arm, seq = parsed
        on_stem = f"{stratum}-ON-{seq:02d}"
        on_path = receipts.get(on_stem)
        if on_path is None:
            continue
        key = (stratum, seq)
        if key in seen:
            continue
        seen.add(key)
        pairs.append((stratum, seq, off_path, on_path))
    return pairs


def rescore_kvitton(
    kvitto_dir: Path,
    *,
    recipe_id: str,
    ledger_path: Path,
    recipe: dict | None = None,
    catalog: list[dict] | None = None,
    catalog_status: str | None = None,
    catalog_sha: str | None = None,
    prevent_gates: dict | None = None,
    rail_gates: dict | None = None,
) -> dict:
    """Offline §4a only. Receipts and raw JSONL are immutable.

    Verify-fail or missing raw ⇒ that pair is refused (no attribution).
    Missing partners are skipped, never synthesised. Ledger is O_EXCL.
    """
    from verify_d_kvitto import verify  # lazy: verify_d_kvitto does not import us

    rid = str(recipe_id or "")
    if recipe is None:
        recipe = load_recipe(HERE / "recept" / f"{rid}.json")
    if rail_gates is None:
        rail_path = RAIL_V2_GATES if rid == "ram-rail-v2" and RAIL_V2_GATES.is_file() else RAIL_GATES
        rail_gates = json.loads(rail_path.read_text(encoding="utf-8")) if rail_path.is_file() else {}
    if prevent_gates is None:
        prevent_gates = (
            json.loads(PREVENT_GATES.read_text(encoding="utf-8")) if PREVENT_GATES.is_file() else {}
        )
    avsett = (
        (prevent_gates.get("avsett_drop") if prevent_gates else None)
        or (rail_gates.get("avsett_drop") if rail_gates else None)
    )
    clusters = list(
        (prevent_gates.get("baseline_clusters") if prevent_gates else None)
        or (rail_gates.get("baseline_clusters") if rail_gates else None)
        or []
    )
    if catalog_status is None:
        loaded, catalog_status = load_recipe_catalog(rid, expected_sha=catalog_sha)
        if catalog is None:
            catalog = loaded
    live_goals = {s["id"]: list(s.get("goal") or []) for s in heldout_specs() + prevention_specs(prevent_gates)}

    receipts = discover_receipt_files(Path(kvitto_dir), rid)
    pairs = _existing_pairs(receipts)
    episodes: list[dict] = []
    for stratum, seq, off_path, on_path in pairs:
        off_text = off_path.read_text(encoding="utf-8")
        on_text = on_path.read_text(encoding="utf-8")
        off_doc = json.loads(off_text)
        on_doc = json.loads(on_text)
        off_sha = sha256_file(off_path)
        on_sha = sha256_file(on_path)
        base = {
            "kind": "ram_r5_episode",
            "stratum": stratum,
            "seq": seq,
            "off_attempt": off_path.stem,
            "on_attempt": on_path.stem,
            "off_kvitto_sha256": off_sha,
            "on_kvitto_sha256": on_sha,
            "off_raw_sha256": None,
            "on_raw_sha256": None,
            "legs": None,
            "attributed": False,
            "catalog_row": None,
            "reasons": [],
            "refused": None,
        }
        off_err = verify(off_doc)
        on_err = verify(on_doc)
        if off_err or on_err:
            base["refused"] = "verify_failed"
            base["verify_errors"] = {"off": off_err, "on": on_err}
            base["reasons"] = ["verify failed — no attribution"]
            episodes.append(base)
            continue
        off_raw_path = _raw_path_of(off_doc, off_path)
        on_raw_path = _raw_path_of(on_doc, on_path)
        if off_raw_path is None or on_raw_path is None:
            base["refused"] = "raw_missing"
            base["reasons"] = ["missing raw JSONL — no attribution"]
            episodes.append(base)
            continue
        base["off_raw_sha256"] = sha256_file(off_raw_path)
        base["on_raw_sha256"] = sha256_file(on_raw_path)
        off_raw = _inject_receipt_astar(raw_from_jsonl(off_raw_path), off_doc, off_raw_path)
        on_raw = _inject_receipt_astar(raw_from_jsonl(on_raw_path), on_doc, on_raw_path)
        result = attribute_pair(
            off_raw,
            on_raw,
            stratum=stratum,
            off_id=off_path.stem,
            on_id=on_path.stem,
            recipe=recipe,
            avsett=avsett,
            clusters=clusters,
            off_receipt=off_doc,
            on_receipt=on_doc,
            catalog=catalog,
            live_goal=live_goals.get(stratum),
        )
        post = result["post"]
        base["legs"] = dict(result["legs"])
        base["attributed"] = bool(result["ok"])
        base["catalog_row"] = post.get("catalog_row")
        base["reasons"] = list(result["reasons"])
        base["m1_cluster"] = post.get("m1_cluster")
        episodes.append(base)

    header = {
        "kind": "ram_r5_rescore_header",
        "schema": LEDGER_SCHEMA,
        "facit_ram_r5_sha256": FACIT_RAM_R5_SHA256,
        "prevent_katalog_sha256": HARDKATALOG_SHA256,
        "rail_katalog_sha256": RAILKATALOG_SHA256,
        "recipe_id": rid,
        "kvitto_dir": str(Path(kvitto_dir)),
        "catalog_status": catalog_status,
        "n_pairs_seen": len(pairs),
        "n_attributed": sum(1 for e in episodes if e.get("attributed")),
        "n_refused": sum(1 for e in episodes if e.get("refused")),
    }
    lines = [json.dumps(header, sort_keys=True)]
    lines.extend(json.dumps(ep, sort_keys=True) for ep in episodes)
    write_exclusive(Path(ledger_path), "\n".join(lines) + "\n")
    return {"header": header, "episodes": episodes, "ledger": str(Path(ledger_path))}



def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--game-port", type=int, default=27595)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--recipe", default="ram-rail", choices=("ram-rail", "ram-rail-v2", "ram-prevent"))
    ap.add_argument("--fixture", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--lock", type=Path)
    ap.add_argument("--commit", default="")
    ap.add_argument("--kvitto-dir", type=Path)
    ap.add_argument(
        "--allow-shared",
        action="store_true",
        help="permit a non-empty --kvitto-dir that already holds another recipe",
    )
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument(
        "--rescore",
        action="store_true",
        help="offline §4a rescore of existing receipts; no rig, receipts immutable",
    )
    ap.add_argument(
        "--ledger",
        type=Path,
        help="O_EXCL attribution ledger path for --rescore",
    )
    args = ap.parse_args(argv)
    if args.rescore:
        if args.run or args.smoke or args.port:
            print("--rescore refuses --run/--smoke/--port (no rig)", file=sys.stderr)
            return 2
        if not args.kvitto_dir or not args.ledger:
            print("--rescore requires --kvitto-dir and --ledger", file=sys.stderr)
            return 2
        try:
            result = rescore_kvitton(
                args.kvitto_dir,
                recipe_id=args.recipe,
                ledger_path=args.ledger,
            )
        except FileExistsError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        text_out = json.dumps(result["header"], indent=2, sort_keys=True)
        if args.out:
            args.out.write_text(text_out + "\n", encoding="utf-8")
        print(text_out)
        return 0 if result["header"].get("n_refused", 0) == 0 else 1
    fixture = args.fixture or (HERE / "recept" / f"{args.recipe}.json")
    recipe = load_recipe(fixture)
    fx_sha = sha256_file(Path(fixture))
    rail_path = RAIL_V2_GATES if args.recipe == "ram-rail-v2" and RAIL_V2_GATES.is_file() else RAIL_GATES
    rail = json.loads(rail_path.read_text(encoding="utf-8"))
    prev = json.loads(PREVENT_GATES.read_text(encoding="utf-8")) if PREVENT_GATES.is_file() else {}
    n_knock = 1 if args.smoke else None
    n_chain = 0 if args.smoke else None

    def stub_kb(**k):
        arm = k.get("arm", "off")
        return {
            "t_stall_gate": 0.2 if arm == "off" else None,
            "t_arrive": None if arm == "off" else 0.8,
            "land_hit": arm == "on",
            "events": [{"ev": "bot_stall"}] if arm == "off" else [{"ev": "arrived", "t": 0.8}],
            "samples": [],
            "stamp_ok": True,
        }

    def stub_trial(**k):
        return {
            "vel": [100.0, 0.0, 0.0],
            "measured_vel": [100.0, 0.0, 0.0],
            "stamp_ok": True,
            "t_arrive": 5.0,
            "t_stall_gate": None,
            "events": [{"ev": "arrived", "t": 5.0}],
            "samples": [],
        }

    if not args.port:
        runner = RamRunner(
            recipe=recipe,
            rail_gates=rail,
            prevent_gates=prev,
            exec_knockback=stub_kb,
            exec_trial=stub_trial,
            ctl_port=args.port,
            game_port=args.game_port,
            n_knock=n_knock,
            n_chain=n_chain,
            kvitto_dir=args.kvitto_dir,
            demo_file="qw/demos/unrun.mvd",
            fixture_sha256=fx_sha,
            allow_shared=args.allow_shared,
        )
        report = runner.run()
        text = json.dumps(report.as_dict(), indent=2, sort_keys=True)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if report.as_dict().get("godkand") else 1

    # Live path. Never stub against a real ctl port (P5 was false-green).
    from d_live_driver import (  # noqa: E402
        DEFAULT_LOCK,
        DEFAULT_MVDSV,
        DEFAULT_QWPROGS,
        LiveTrialDriver,
        file_sha256 as sha_file,
        parse_lock,
        refuse_ra,
    )

    why = refuse_ra(args.port, args.game_port)
    if why:
        print(why, file=sys.stderr)
        return 2
    if args.port in FORBIDDEN_CTL or args.game_port in FORBIDDEN_GAME:
        print("RA/main endpoint", file=sys.stderr)
        return 2
    lock_path = args.lock or DEFAULT_LOCK
    if not lock_path.is_file():
        print(f"no {lock_path} — refuse --port without lock; never stub", file=sys.stderr)
        return 2
    lock = parse_lock(lock_path)
    qw = sha_file(DEFAULT_QWPROGS) if DEFAULT_QWPROGS.is_file() else "00" * 32
    mv = sha_file(DEFAULT_MVDSV) if DEFAULT_MVDSV.is_file() else "00" * 32
    commit = (args.commit or "").strip()
    if args.run and not commit:
        print("judged --run requires --commit", file=sys.stderr)
        return 2

    sys.path.insert(0, str(HERE.parent))
    from runner.control import Control  # noqa: E402

    ctl = Control(args.host, args.port)
    gate = (rail.get("gates") or {}).get(args.recipe) or (rail.get("gates") or {}).get("ram-rail") or {}
    driver = LiveTrialDriver(
        ctl,
        gate=gate,
        recipe=recipe,
        lock_token=lock["token"],
        qwprogs_sha=qw,
        mvdsv_sha=mv,
        commit=commit or "unrun",
        host=args.host,
        ctl_port=args.port,
        game_port=args.game_port,
        lock_path=lock_path,
    )
    try:
        driver.prepare()
        ident = driver.confirm("off")
        if not args.smoke and not args.run:
            payload = {
                "live": True,
                "identity": ident,
                "recipe": args.recipe,
                "binaries": {"qwprogs_sha256": qw, "mvdsv_sha256": mv},
                "hint": "pass --smoke or --run (judged, fable-qa)",
            }
            text = json.dumps(payload, indent=2, sort_keys=True)
            if args.out:
                args.out.write_text(text + "\n", encoding="utf-8")
            print(text)
            return 0
        if args.smoke:
            driver.start_demo(smoke=True)
        else:
            driver.start_demo(smoke=False)
        binaries = {"qwprogs_sha256": qw, "mvdsv_sha256": mv}
        if args.kvitto_dir:
            driver.measure_both_stamps()
            args.kvitto_dir.mkdir(parents=True, exist_ok=True)

        def on_attempt(att, raw):
            if not args.kvitto_dir:
                return
            started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec_path, raw_path = recipe_kvitto_paths(args.kvitto_dir, args.recipe, att.attempt_id)
            driver.write_attempt_raw(raw_path, raw, exclusive=True)
            kb = raw.get("knockback") if att.stratum.startswith("K") else None
            if kb is None and att.stratum.startswith("K"):
                kb = {
                    "point": att.stratum,
                    "incoming_velocity": list(att.incoming_vel or raw.get("commanded_vel") or raw.get("vel") or []),
                    "land_hit": bool(att.land_hit),
                    "t_land": att.t_land,
                }
            driver.write_attempt_kvitto(
                rec_path,
                attempt_id=att.attempt_id,
                stratum_id=att.stratum,
                raw_pointer=str(raw_path),
                started_at=started,
                ended_at=started,
                lock_owner=lock.get("owner") or lock["token"],
                lock_issued=lock.get("issued") or started,
                gate_velocity=raw.get("gate_velocity") or (kb or {}).get("incoming_velocity"),
                gate_cell=raw.get("gate_cell"),
                astar_before=raw.get("astar_before"),
                astar_after=raw.get("astar_after"),
                astar_next_best=raw.get("astar_next_best"),
                demo_file=driver.demo_file,
                fixture_sha256=fx_sha,
                candidate=args.recipe,
                landing_cell=raw.get("landing_cell"),
                selected_link=raw.get("selected_link"),
                knockback=kb,
                cvars=recipe_cvars(recipe),
                exclusive=True,
            )

        runner = RamRunner(
            recipe=recipe,
            rail_gates=rail,
            prevent_gates=prev,
            exec_knockback=driver.exec_knockback,
            exec_trial=driver.exec_trial,
            ctl_port=args.port,
            game_port=args.game_port,
            ensure_arm=driver.ensure_arm,
            n_knock=n_knock,
            n_chain=n_chain,
            on_attempt=on_attempt if args.kvitto_dir else None,
            kvitto_dir=args.kvitto_dir,
            demo_file=driver.demo_file,
            binaries=binaries,
            fixture_sha256=fx_sha,
            allow_shared=args.allow_shared,
        )
        report = runner.run()
        payload = report.as_dict()
        payload["binaries"] = {"qwprogs_sha256": qw, "mvdsv_sha256": mv}
        payload["demo_file"] = driver.demo_file
        payload["stamps"] = driver.last_stamps
        text = json.dumps(payload, indent=2, sort_keys=True)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if payload.get("godkand") else 1
    finally:
        try:
            driver.stop_demo()
        except Exception:
            pass
        if driver.arm == "on":
            try:
                driver.undo()
            except Exception as exc:
                print(f"undo-on-exit failed: {exc}", file=sys.stderr)
        try:
            driver.restore()
        except Exception:
            pass
        ctl.close()


if __name__ == "__main__":
    raise SystemExit(main())
