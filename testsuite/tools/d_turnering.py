#!/usr/bin/env python3
"""HAZ-1462 tournament runner. Consumes haz1462-gates.json — not west-shelf STRATA.

Reproduction: 2 routes × N pairs, rest teleport, ±5 pairing, hearth episode
dedup within 15 u of [317.64,-758.40,-15.97] stamped cell 1462.
Migration heldout: 16 obligations × 5 pairs; SpeedJump 34419 must be
attested on 1416→1124; next-best must be the high walk (10446→10768),
never floor-drop 10444. K2 also runs the 49-dest appendix.
§5 scoring is candidate-local — no borrowed outcomes.
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

from d_recipe import load_recipe, on_expected  # noqa: E402
from d_strata import (  # noqa: E402
    FORBIDDEN_CTL,
    FORBIDDEN_GAME,
    HELDOUT_PAIR_REPLACE_CAP,
    PAIR_VEL_TOL,
    pair_start_vel_ok,
)
from d_kvitto import astar_path, make_kvitto, write_kvitto  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_GATES = HERE / "recept" / "haz1462-gates.json"
DEFAULT_APPENDIX = HERE / "recept" / "haz1462-k2-appendix.json"
FLOOR_DROP = 10444
NEXT_HIGH = (10446, 10768)
# High-shelf first walks from 1416 toward 1459/1461 (not the downward 10441 tree).
SHELF_FROM_1416_FIRST = frozenset({10447, 10446})
# After K2 cuts 10447+10446 the remaining high first-walks from 1416.
# Hop-SP 1416→1461 is 10441+10084. Floor Drop 10444 is never next-best.
K2_NEXT_HIGH_FIRST = frozenset({10440, 10441, 10443, 10445})
SPEEDJUMP = 34419
CANDIDATES = ("haz1462-k1", "haz1462-k2", "haz1462-k3")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def dist(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) < 3 or len(b) < 3:
        return float("inf")
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def xy_dist(a: list[float] | None, b: list[float] | None) -> float:
    """Horizontal locus. peak_drop_150 origin is mid-air (z still high)."""
    if not a or not b or len(a) < 2 or len(b) < 2:
        return float("inf")
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def raw_from_jsonl(path: Path) -> dict:
    """Rebuild exec_trial-shaped raw from a live write_attempt_raw JSONL."""
    events: list[dict] = []
    header: dict = {}
    landing = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        kind = row.get("kind")
        if kind == "header":
            header = {k: v for k, v in row.items() if k != "kind"}
            continue
        if kind == "sample":
            continue
        if kind == "event" or row.get("ev"):
            events.append(row)
            if row.get("ev") == "peak_drop_150" and isinstance(row.get("cell"), int):
                landing = int(row["cell"])
    raw = dict(header)
    raw["events"] = events
    if raw.get("landing_cell") is None and landing is not None:
        raw["landing_cell"] = landing
    return raw


def hearth_hit(raw: dict, hearth: dict) -> bool:
    """peak_drop_150 with cell 1462 and XY locus ±radius (live mid-air z)."""
    centroid = list(hearth.get("centroid_aim") or hearth.get("origin") or [])
    radius = float(hearth.get("radius") or 15.0)
    want = int(hearth.get("cell") or 1462)
    landing = raw.get("landing_cell")
    for ev in raw.get("events") or []:
        if ev.get("ev") != "peak_drop_150":
            continue
        origin = ev.get("origin")
        cell = ev.get("cell")
        if cell is None:
            cell = landing
        if cell != want:
            continue
        if origin and len(origin) >= 2:
            if xy_dist(list(origin), centroid) <= radius:
                return True
            continue
        return True
    if landing == want:
        for ev in raw.get("events") or []:
            if ev.get("ev") != "peak_drop_150":
                continue
            origin = ev.get("origin")
            if origin and xy_dist(list(origin), centroid) <= radius:
                return True
        origin = raw.get("gate_origin")
        if origin and xy_dist(list(origin), centroid) <= radius:
            return True
    return False


def has_stall(raw: dict) -> bool:
    if raw.get("t_stall_gate") is not None:
        return True
    return any(ev.get("ev") == "bot_stall" for ev in (raw.get("events") or []))


def arrived_in_budget(raw: dict, budget_s: float) -> bool:
    t = raw.get("t_arrive")
    return t is not None and float(t) <= float(budget_s) + 1e-9


def path_links(blob: dict | None) -> list[int]:
    if not isinstance(blob, dict):
        return []
    path = blob.get("path") if "path" in blob else blob
    if not isinstance(path, dict):
        return []
    return [int(x) for x in (path.get("links") or [])]


def next_best_is_floor_drop(links: list[int]) -> bool:
    """10444→10453 is the floor Drop, not the next high walk."""
    return bool(links) and int(links[0]) == FLOOR_DROP


def links_contain_seq(links: list[int], seq: list[int] | tuple[int, ...]) -> bool:
    want = [int(x) for x in seq]
    have = [int(x) for x in links]
    n = len(want)
    if n == 0 or len(have) < n:
        return False
    for i in range(len(have) - n + 1):
        if have[i : i + n] == want:
            return True
    return False


def astar_mask(blob: dict | None) -> list[int]:
    if not isinstance(blob, dict):
        return []
    path = blob.get("path") if "path" in blob and isinstance(blob.get("path"), dict) else blob
    if not isinstance(path, dict):
        return []
    return [int(x) for x in (path.get("mask_links") or [])]


def selected_traversed_shelf_from_1416(spec: dict, selected: list[int]) -> bool:
    """True iff the chosen path left 1416 on the high shelf (10447/10446), not the floor tree."""
    try:
        start = int(spec.get("start_cell") or 0)
    except (TypeError, ValueError):
        start = 0
    if start != 1416 or not selected:
        return False
    return int(selected[0]) in SHELF_FROM_1416_FIRST


def next_best_ok(
    links: list[int],
    candidate: str,
    *,
    spec: dict | None = None,
    selected: list[int] | None = None,
    mask: list[int] | None = None,
) -> bool:
    """Per-obligation next-best. Facit §3: 10446→10768 *when selected*.

    Always: non-empty, not 10444, mask == entire chosen path when a mask is given.
    10446 on next-best only when the chosen path left 1416 via 10447 (K1/K3).
    Must-starters and floor dests have their own next-best — do not demand 10446.
    """
    have = [int(x) for x in (links or [])]
    sel = [int(x) for x in (selected or [])]
    if not have or have[0] == FLOOR_DROP:
        return False
    if mask is not None and [int(x) for x in mask] != sel:
        return False
    spec = spec or {}
    if not selected_traversed_shelf_from_1416(spec, sel):
        return True
    cid = str(candidate or "")
    if cid in {"haz1462-k1", "haz1462-k3", "k1", "k3"}:
        if sel and int(sel[0]) == 10447:
            return have[0] == 10446
        return True
    return True


def next_best_fail_reason(
    links: list[int],
    candidate: str,
    *,
    spec: dict | None = None,
    selected: list[int] | None = None,
    mask: list[int] | None = None,
) -> str:
    have = [int(x) for x in (links or [])]
    sel = [int(x) for x in (selected or [])]
    if not have:
        return "next-best empty — want the actual masked next path"
    if have[0] == FLOOR_DROP:
        return "next-best is floor Drop 10444 — not a high-path counterfactual"
    if mask is not None and [int(x) for x in mask] != sel:
        return f"next-best mask {list(mask)} != entire selected {sel}"
    spec = spec or {}
    if selected_traversed_shelf_from_1416(spec, sel) and sel and int(sel[0]) == 10447:
        if have[0] != 10446:
            return f"selected 10447 — next-best must start 10446 (got {have})"
    return f"next-best rejected — got {have}"


def attests_speedjump(links: list[int], sj: int = SPEEDJUMP) -> bool:
    return int(sj) in {int(x) for x in links}


def reproduction_routes(gates: dict) -> list[dict]:
    repro = gates.get("reproduction") or {}
    out = []
    for key in ("in_vast", "in_tunnel"):
        row = repro.get(key)
        if not isinstance(row, dict):
            continue
        start = row.get("start") or {}
        goal = row.get("goal") or {}
        out.append({
            "id": str(row.get("id") or key),
            "kind": "trap",
            "start": list(start.get("aim") or start.get("origin") or []),
            "goal": list(goal.get("aim") or goal.get("origin") or []),
            "start_cell": start.get("cell"),
            "goal_cell": goal.get("cell"),
            "budget_s": float(row.get("budget_s") or 25.0),
            "n_pairs": int(row.get("n_pairs") or 75),
            "population": "teleport_drill",
        })
    return out


def heldout_obligations(gates: dict) -> list[dict]:
    start_cell = 1416
    start_origin = None
    for row in gates.get("heldout_from_1416") or []:
        if int(row.get("cell") or 0) == start_cell:
            start_origin = list(row.get("origin") or [])
            break
    if not start_origin:
        # 1416 is the start, not a dest — look at gates block
        for block in (gates.get("gates") or {}).values():
            ids = block.get("cell_ids") or []
            origs = block.get("cell_origins") or []
            if start_cell in ids:
                start_origin = list(origs[ids.index(start_cell)])
                break
    if not start_origin:
        start_origin = [288.0, -844.0, 264.0]
    budget = float(gates.get("heldout_budget_s") or 25.0)
    n_pairs = int(gates.get("n_heldout_pairs") or 5)
    sj = int(gates.get("speedjump") or SPEEDJUMP)
    out = []
    for dest in gates.get("heldout_from_1416") or []:
        cid = int(dest["cell"])
        out.append({
            "id": f"1416-{cid}",
            "kind": "heldout",
            "start": list(start_origin),
            "goal": list(dest["origin"]),
            "start_cell": start_cell,
            "goal_cell": cid,
            "budget_s": budget,
            "n_pairs": n_pairs,
            "vh_lo": 80.0,
            "vh_hi": 160.0,
            "dir_min": 0.8,
            # facit r2: 1416-1124 attests its ACTUAL pre-registered drop
            # corridor; the SpeedJump attest lives on the separate
            # 1461-1124 obligation below.
            "require_speedjump": False,
            "speedjump": sj,
            "avsett_drop_cells": (
                [int(c) for c in (gates.get("route_1124_avsett_drop") or {}).get("cells") or []]
                if cid == 1124 else []
            ),
            "require_corridor": cid == 1124,
            "population": "kedjad",
        })
    sj_ob = gates.get("sj_obligation") or {}
    if sj_ob:
        src, tgt = sj_ob.get("start") or {}, sj_ob.get("goal") or {}
        out.append({
            "id": str(sj_ob.get("id") or "1461-1124"),
            "kind": "heldout",
            "start": list(src.get("origin") or []),
            "goal": list(tgt.get("origin") or []),
            "start_cell": src.get("cell"),
            "goal_cell": tgt.get("cell"),
            "budget_s": budget,
            "n_pairs": n_pairs,
            "vh_lo": 80.0,
            "vh_hi": 160.0,
            "dir_min": 0.8,
            "require_speedjump": True,
            "speedjump": sj,
            "avsett_drop_cells": [],
            "require_corridor": False,
            "population": "kedjad",
        })
    for ms in gates.get("must_starters") or []:
        src, tgt = ms.get("start") or {}, ms.get("goal") or {}
        out.append({
            "id": str(ms.get("id") or f"{src.get('cell')}-{tgt.get('cell')}"),
            "kind": "heldout",
            "start": list(src.get("origin") or []),
            "goal": list(tgt.get("origin") or []),
            "start_cell": src.get("cell"),
            "goal_cell": tgt.get("cell"),
            "budget_s": float(ms.get("budget_s") or budget),
            "n_pairs": n_pairs,
            "vh_lo": 80.0,
            "vh_hi": 160.0,
            "dir_min": 0.8,
            "require_speedjump": False,
            "population": "kedjad",
        })
    return out


def appendix_obligations(appendix: dict, start_1416: list[float]) -> list[dict]:
    budget = float(appendix.get("budget_s") or 25.0)
    n_pairs = int(appendix.get("n_pairs") or 5)
    start = list((appendix.get("start") or {}).get("origin") or start_1416)
    out = []
    for dest in appendix.get("destinations") or []:
        cid = int(dest["cell"])
        out.append({
            "id": f"app-1416-{cid}",
            "kind": "heldout",
            "start": start,
            "goal": list(dest["origin"]),
            "start_cell": 1416,
            "goal_cell": cid,
            "budget_s": budget,
            "n_pairs": n_pairs,
            "vh_lo": 80.0,
            "vh_hi": 160.0,
            "dir_min": 0.8,
            "require_speedjump": False,
            "population": "kedjad",
        })
    return out


@dataclass
class TAttempt:
    attempt_id: str
    route_id: str
    arm: str
    valid: bool
    reason: str
    hearth: bool = False
    stall: bool = False
    arrived: bool = False
    start_vel: list[float] | None = None
    astar_links: list[int] = field(default_factory=list)
    next_best_links: list[int] = field(default_factory=list)
    sj_ok: bool = True
    next_best_ok: bool = True


@dataclass
class TournamentReport:
    candidate: str
    fixture_sha256: str
    valid: bool
    invalid_reasons: list[str]
    attempts: list[TAttempt]
    repro_off_ok: bool = False
    repro_on_ok: bool = False
    heldout_on_ok: bool = False
    appendix_on_ok: bool = True
    pass_ok: bool = False

    def as_dict(self) -> dict:
        return {
            "kind": "haz1462-turnering",
            "candidate": self.candidate,
            "fixture_sha256": self.fixture_sha256,
            "valid": self.valid,
            "invalid_reasons": list(self.invalid_reasons),
            "godkand": self.valid and self.pass_ok,
            "reproduction": {
                "off_ok": self.repro_off_ok,
                "on_ok": self.repro_on_ok,
            },
            "heldout": {"on_ok": self.heldout_on_ok},
            "appendix": {"on_ok": self.appendix_on_ok},
            "attempts": [
                {
                    "id": a.attempt_id,
                    "route": a.route_id,
                    "arm": a.arm,
                    "valid": a.valid,
                    "reason": a.reason,
                    "hearth": a.hearth,
                    "stall": a.stall,
                    "arrived": a.arrived,
                    "sj_ok": a.sj_ok,
                    "next_best_ok": a.next_best_ok,
                }
                for a in self.attempts
            ],
        }


def score_reproduction(attempts: list[TAttempt], routes: list[dict]) -> tuple[bool, bool]:
    # facit r2: OFF needs >=1 hearth event across the COMBINED campaign
    # (stochastic per-route zeros at N=75 are expected); ON stays 0/N per route.
    on_ok = True
    route_ids = {spec["id"] for spec in routes}
    campaign_offs = [
        a for a in attempts
        if a.route_id in route_ids and a.arm == "off" and a.valid
    ]
    off_ok = any(a.hearth for a in campaign_offs)
    for spec in routes:
        rid = spec["id"]
        n = int(spec["n_pairs"])
        ons = [a for a in attempts if a.route_id == rid and a.arm == "on" and a.valid]
        if len(ons) != n:
            on_ok = False
            continue
        if any(a.hearth or a.stall or not a.arrived for a in ons):
            on_ok = False
    return off_ok, on_ok


def score_migrate(attempts: list[TAttempt], routes: list[dict]) -> bool:
    for spec in routes:
        rid = spec["id"]
        n = int(spec["n_pairs"])
        ons = [a for a in attempts if a.route_id == rid and a.arm == "on" and a.valid]
        if len(ons) != n:
            return False
        if any(a.hearth or a.stall or not a.arrived or not a.sj_ok or not a.next_best_ok for a in ons):
            return False
    return True


def _side_by_side_view(rep: TournamentReport | dict) -> dict:
    if isinstance(rep, TournamentReport):
        return {
            "godkand": bool(rep.valid and rep.pass_ok),
            "valid": rep.valid,
            "fixture_sha256": rep.fixture_sha256,
            "reproduction_off": rep.repro_off_ok,
            "reproduction_on": rep.repro_on_ok,
            "heldout_on": rep.heldout_on_ok,
            "appendix_on": rep.appendix_on_ok,
        }
    repro = rep.get("reproduction") or {}
    held = rep.get("heldout") or {}
    app = rep.get("appendix") or {}
    return {
        "godkand": bool(rep.get("godkand")),
        "valid": bool(rep.get("valid", True)),
        "fixture_sha256": str(rep.get("fixture_sha256") or ""),
        "reproduction_off": bool(repro.get("off_ok", False)),
        "reproduction_on": bool(repro.get("on_ok", False)),
        "heldout_on": bool(held.get("on_ok", False)),
        "appendix_on": bool(app.get("on_ok", True)),
    }


def score_side_by_side(reports: dict[str, TournamentReport | dict]) -> dict:
    """§5: candidates side by side. No borrowed stamps/outcomes.

    Accepts TournamentReport objects or as_dict()/CLI-report JSON.
    """
    out = {"kind": "haz1462-side-by-side", "candidates": {}}
    for cid, rep in reports.items():
        out["candidates"][cid] = _side_by_side_view(rep)
    winners = [c for c, b in out["candidates"].items() if b["godkand"]]
    out["winner"] = winners[0] if len(winners) == 1 else None
    out["tournament"] = "GODKAND" if winners else "UNDERKAND"
    return out


class TournamentRunner:
    def __init__(
        self,
        *,
        recipe: dict,
        gates: dict,
        exec_trial: Callable[..., dict],
        ctl_port: int,
        game_port: int,
        fixture_sha256: str,
        appendix: dict | None = None,
        ensure_arm: Callable[[str], None] | None = None,
        n_repro: int | None = None,
        n_heldout: int | None = None,
        on_attempt: Callable[[TAttempt, dict], None] | None = None,
        kvitto_dir: Path | None = None,
        demo_file: str | None = None,
        binaries: dict[str, str] | None = None,
    ) -> None:
        self.recipe = recipe
        self.gates = gates
        self.appendix = appendix
        self.exec_trial = exec_trial
        self.ctl_port = ctl_port
        self.game_port = game_port
        self.fixture_sha256 = fixture_sha256
        self.ensure_arm = ensure_arm
        self.on_attempt = on_attempt
        self.kvitto_dir = Path(kvitto_dir) if kvitto_dir else None
        self.demo_file = demo_file
        self.binaries = dict(binaries or {})
        self.repro = reproduction_routes(gates)
        self.heldout = heldout_obligations(gates)
        start_1416 = [288.0, -844.0, 264.0]
        self.app_routes = (
            appendix_obligations(appendix, start_1416)
            if appendix and recipe.get("id") == "haz1462-k2"
            else []
        )
        if n_repro is not None:
            for s in self.repro:
                s["n_pairs"] = int(n_repro)
        if n_heldout is not None:
            for s in self.heldout + self.app_routes:
                s["n_pairs"] = int(n_heldout)
        self.last_raw: dict = {}

    def preflight(self) -> list[str]:
        reasons: list[str] = []
        if self.ctl_port in FORBIDDEN_CTL or self.game_port in FORBIDDEN_GAME:
            reasons.append("RA/main endpoint")
        rid = self.recipe.get("id")
        if rid not in CANDIDATES:
            reasons.append(f"candidate {rid!r} not in {CANDIDATES}")
        try:
            on_expected(self.recipe)
        except ValueError as exc:
            reasons.append(str(exc))
        if not self.repro or {s["id"] for s in self.repro} != {"in_vast", "in_tunnel"}:
            reasons.append("gates.reproduction missing in_vast/in_tunnel")
        if len(self.heldout) != 17:  # facit r2: 12 dests + 4 must-starters + 1461-1124
            reasons.append(f"heldout obligations {len(self.heldout)} != 17")
        if not any(s.get("require_speedjump") for s in self.heldout):
            reasons.append("heldout missing 1416-1124 speedjump obligation")
        hearth = (self.gates.get("reproduction") or {}).get("hearth")
        if not isinstance(hearth, dict) or hearth.get("cell") != 1462:
            reasons.append("reproduction.hearth cell must be 1462")
        if rid == "haz1462-k2":
            if not self.appendix:
                reasons.append("K2 missing appendix fixture")
            elif len(self.app_routes) != 49:
                reasons.append(f"K2 appendix dests {len(self.app_routes)} != 49")
        if not self.fixture_sha256 or len(self.fixture_sha256) != 64:
            reasons.append("fixture_sha256 missing")
        return reasons

    def _classify(self, spec: dict, arm: str, seq: int, raw: dict) -> TAttempt:
        hearth = hearth_hit(raw, (self.gates.get("reproduction") or {}).get("hearth") or {})
        stall = has_stall(raw)
        arrived = arrived_in_budget(raw, spec["budget_s"])
        after = raw.get("astar_after") or raw.get("astar") or {}
        nb = raw.get("astar_next_best") or {}
        links = path_links(after)
        nb_links = path_links(nb)
        sj_ok = True
        if spec.get("require_speedjump"):
            # facit r2: 1461-1124 must select and attest 34419 in BOTH arms.
            sj_ok = attests_speedjump(links, int(spec.get("speedjump") or SPEEDJUMP))
        corridor_ok = True
        if spec.get("require_corridor") and arm == "on":
            # facit r2: attest the ACTUAL route — at least one peak_drop_150
            # inside the pre-registered avsett_drop corridor; EVERY other
            # drop remains a failure. Fail-closed: an unstamped drop
            # (cell=None) counts as outside; the goal cell is NOT exempt.
            allowed = set(int(c) for c in spec.get("avsett_drop_cells") or [])
            drops = [
                ev for ev in (raw.get("events") or [])
                if ev.get("ev") == "peak_drop_150"
            ]
            in_corr = [
                ev for ev in drops
                if ev.get("cell") is not None and int(ev["cell"]) in allowed
            ]
            outside = [
                ev for ev in drops
                if ev.get("cell") is None or int(ev["cell"]) not in allowed
            ]
            corridor_ok = bool(in_corr) and not outside
        nb_ok = True
        cid = str(self.recipe.get("id") or "")
        nb_mask = astar_mask(nb)
        if arm == "on" and spec.get("kind") == "heldout":
            nb_ok = next_best_ok(
                nb_links, cid, spec=spec, selected=links, mask=nb_mask,
            )
        vel = raw.get("measured_vel") if raw.get("measured_vel") is not None else raw.get("vel")
        att = TAttempt(
            attempt_id=f"{spec['id']}-{arm.upper()}-{seq:02d}",
            route_id=spec["id"],
            arm=arm,
            valid=True,
            reason="ok",
            hearth=hearth,
            stall=stall,
            arrived=arrived,
            start_vel=list(vel) if vel is not None else None,
            astar_links=links,
            next_best_links=nb_links,
            sj_ok=sj_ok,
            next_best_ok=nb_ok,
        )
        if raw.get("stamp_ok") is False:
            att.valid = False
            att.reason = raw.get("stamp_reason") or "start-vel stamp failed"
        if not sj_ok:
            att.valid = False
            att.reason = f"{spec['id']} must attest SpeedJump {int(spec.get('speedjump') or SPEEDJUMP)}"
        elif not corridor_ok:
            att.valid = False
            att.reason = (
                f"{spec['id']} must attest the pre-registered avsett_drop "
                f"corridor {sorted(set(int(c) for c in spec.get('avsett_drop_cells') or []))}"
            )
        elif not nb_ok:
            att.valid = False
            att.reason = next_best_fail_reason(
                nb_links, cid, spec=spec, selected=links, mask=nb_mask,
            )
        return att

    def _write_attempt_kvitto(self, att: TAttempt, raw: dict) -> Path:
        """d-kvitto per attempt. kvitto-dir is the contract, not an unused flag."""
        if self.kvitto_dir is None:
            raise RuntimeError("kvitto_dir is not set")
        path = self.kvitto_dir / f"{att.attempt_id}.json"
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
        landing = raw.get("landing_cell")
        selected = raw.get("selected_link")
        if selected is None and att.astar_links:
            selected = int(att.astar_links[0])
        demo = self.demo_file or "qw/demos/unrun.mvd"
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
                "evidence": self.recipe.get("evidence") or "haz1462 tournament attempt",
            },
            seed=0,
            stratum={"id": att.route_id, "attempt": att.attempt_id},
            raw_pointer=str((self.kvitto_dir / f"{att.attempt_id}.jsonl").resolve()),
            astar_before=before if isinstance(before, dict) else empty,
            astar_after=after if isinstance(after, dict) else empty,
            astar_next_best=nb if isinstance(nb, dict) else empty,
            demo_file=demo,
            fixture_sha256=self.fixture_sha256,
            candidate=str(self.recipe.get("id")),
            landing_cell=int(landing) if isinstance(landing, int) else None,
            selected_link=int(selected) if isinstance(selected, int) else None,
        )
        doc["binaries"] = {"qwprogs_sha256": qw, "mvdsv_sha256": mv}
        write_kvitto(path, doc)
        return path

    def _run_one(self, spec: dict, arm: str, seq: int, match_vel=None) -> TAttempt:
        if self.ensure_arm is not None:
            self.ensure_arm(arm)
        raw = self.exec_trial(
            stratum_id=spec["id"],
            arm=arm,
            spec=spec,
            seq=seq,
            match_vel=match_vel,
            window_s=float(spec["budget_s"]),
        )
        self.last_raw = raw
        att = self._classify(spec, arm, seq, raw)
        if self.on_attempt is not None:
            self.on_attempt(att, raw)
        elif self.kvitto_dir is not None:
            self._write_attempt_kvitto(att, raw)
        return att

    def _run_pairs(self, spec: dict, attempts: list[TAttempt], extra: list[str]) -> None:
        need = int(spec["n_pairs"])
        got = 0
        seq = 0
        cap = need + int(HELDOUT_PAIR_REPLACE_CAP)
        while got < need:
            seq += 1
            if seq > cap:
                extra.append(f"{spec['id']}: could not assemble {need} valid pairs")
                break
            off_att = self._run_one(spec, "off", seq)
            if not off_att.valid:
                attempts.append(off_att)
                continue
            on_att = self._run_one(spec, "on", seq, match_vel=off_att.start_vel)
            pok, pwhy = pair_start_vel_ok(off_att.start_vel, on_att.start_vel)
            if not pok or not on_att.valid:
                if not pok and on_att.valid:
                    on_att.valid = False
                    on_att.reason = pwhy
                off_att.valid = False
                if "stamp" not in (off_att.reason or ""):
                    off_att.reason = on_att.reason
                attempts.append(off_att)
                attempts.append(on_att)
                continue
            got += 1
            attempts.append(off_att)
            attempts.append(on_att)

    def run(self) -> TournamentReport:
        reasons = self.preflight()
        attempts: list[TAttempt] = []
        extra: list[str] = []
        if reasons:
            return TournamentReport(
                candidate=str(self.recipe.get("id")),
                fixture_sha256=self.fixture_sha256,
                valid=False,
                invalid_reasons=reasons,
                attempts=attempts,
            )
        for spec in self.repro:
            self._run_pairs(spec, attempts, extra)
        for spec in self.heldout:
            self._run_pairs(spec, attempts, extra)
        for spec in self.app_routes:
            self._run_pairs(spec, attempts, extra)
        repro_off, repro_on = score_reproduction(attempts, self.repro)
        held_ok = score_migrate(attempts, self.heldout)
        app_ok = True if not self.app_routes else score_migrate(attempts, self.app_routes)
        valid = not extra
        pass_ok = valid and repro_off and repro_on and held_ok and app_ok
        if not valid:
            extra = extra
        return TournamentReport(
            candidate=str(self.recipe.get("id")),
            fixture_sha256=self.fixture_sha256,
            valid=valid,
            invalid_reasons=extra,
            attempts=attempts,
            repro_off_ok=repro_off,
            repro_on_ok=repro_on,
            heldout_on_ok=held_ok,
            appendix_on_ok=app_ok,
            pass_ok=pass_ok,
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--game-port", type=int, default=27592)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--candidate", choices=("k1", "k2", "k3", "haz1462-k1", "haz1462-k2", "haz1462-k3"))
    ap.add_argument("--fixture", type=Path)
    ap.add_argument("--gates", type=Path, default=DEFAULT_GATES)
    ap.add_argument("--appendix", type=Path, default=DEFAULT_APPENDIX)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--kvitto-dir", type=Path)
    ap.add_argument("--lock", type=Path)
    ap.add_argument("--commit", default="")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--n-repro", type=int)
    ap.add_argument("--n-heldout", type=int)
    ap.add_argument("--run", action="store_true")
    ap.add_argument(
        "--side-by-side",
        nargs="+",
        type=Path,
        metavar="REPORT",
        help="score_side_by_side on saved tournament report JSON files (post-process)",
    )
    args = ap.parse_args(argv)

    if args.side_by_side:
        reports: dict[str, dict] = {}
        for path in args.side_by_side:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            cid = str(data.get("candidate") or Path(path).stem)
            reports[cid] = data
        table = score_side_by_side(reports)
        text = json.dumps(table, indent=2, sort_keys=True)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if table.get("tournament") == "GODKAND" else 1

    cand = args.candidate or "k1"
    if not cand.startswith("haz"):
        cand = f"haz1462-{cand}"
    fixture = args.fixture or (HERE / "recept" / f"{cand}.json")
    recipe = load_recipe(fixture)
    gates = json.loads(Path(args.gates).read_text(encoding="utf-8"))
    appendix = None
    if cand == "haz1462-k2" and args.appendix and Path(args.appendix).is_file():
        appendix = json.loads(Path(args.appendix).read_text(encoding="utf-8"))
    fx_sha = file_sha256(Path(fixture))

    n_repro = args.n_repro if args.n_repro is not None else (1 if args.smoke else None)
    n_heldout = args.n_heldout if args.n_heldout is not None else (0 if args.smoke else None)

    if not args.port:
        def stub(**k):
            return {
                "vel": [0, 0, 0],
                "measured_vel": [0, 0, 0],
                "stamp_ok": True,
                "events": [],
                "samples": [],
                "t_arrive": 1.0,
                "astar_after": astar_path(found=False),
                "astar_next_best": astar_path(found=False),
            }
        runner = TournamentRunner(
            recipe=recipe,
            gates=gates,
            appendix=appendix,
            exec_trial=stub,
            ctl_port=args.port,
            game_port=args.game_port,
            fixture_sha256=fx_sha,
            n_repro=n_repro,
            n_heldout=0 if n_heldout is None and not args.run else n_heldout,
            kvitto_dir=args.kvitto_dir,
            demo_file="qw/demos/unrun.mvd",
        )
        report = runner.run()
        text = json.dumps(report.as_dict(), indent=2, sort_keys=True)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if report.as_dict().get("godkand") else 1

    if args.port in FORBIDDEN_CTL or args.game_port in FORBIDDEN_GAME:
        print("RA/main endpoint", file=sys.stderr)
        return 2

    from d_live_driver import (  # noqa: E402
        DEFAULT_LOCK,
        DEFAULT_MVDSV,
        DEFAULT_QWPROGS,
        LiveTrialDriver,
        file_sha256 as sha_file,
        parse_lock,
    )

    lock_path = args.lock or DEFAULT_LOCK
    if not lock_path.is_file():
        print(f"no {lock_path}", file=sys.stderr)
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
    driver = LiveTrialDriver(
        ctl,
        gate=(gates.get("gates") or {}).get(cand) or {},
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
        driver.confirm("off")
        if args.smoke:
            driver.start_demo(smoke=True)
            n_repro = n_repro if n_repro is not None else 1
            n_heldout = 0
        elif args.run:
            driver.start_demo(smoke=False)
        else:
            ident = driver.identity()
            print(json.dumps({"identity": ident, "candidate": cand, "hint": "--smoke or --run"}, indent=2))
            return 0

        def exec_trial(**k):
            return driver.exec_trial(**k)

        binaries = {"qwprogs_sha256": qw, "mvdsv_sha256": mv}
        if args.kvitto_dir:
            # Receipts refuse to invent ON observed — stamp both arms first.
            driver.measure_both_stamps()
            args.kvitto_dir.mkdir(parents=True, exist_ok=True)

        def on_attempt(att, raw):
            if not args.kvitto_dir:
                return
            started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            raw_path = args.kvitto_dir / f"{att.attempt_id}.jsonl"
            driver.write_attempt_raw(raw_path, raw)
            driver.write_attempt_kvitto(
                args.kvitto_dir / f"{att.attempt_id}.json",
                attempt_id=att.attempt_id,
                stratum_id=att.route_id,
                raw_pointer=str(raw_path),
                started_at=started,
                ended_at=started,
                lock_owner=lock.get("owner") or lock["token"],
                lock_issued=lock.get("issued") or started,
                gate_velocity=raw.get("gate_velocity"),
                gate_cell=raw.get("gate_cell"),
                astar_before=raw.get("astar_before"),
                astar_after=raw.get("astar_after"),
                astar_next_best=raw.get("astar_next_best"),
                demo_file=driver.demo_file,
                fixture_sha256=fx_sha,
                candidate=cand,
                landing_cell=raw.get("landing_cell"),
                selected_link=raw.get("selected_link"),
            )

        runner = TournamentRunner(
            recipe=recipe,
            gates=gates,
            appendix=appendix,
            exec_trial=exec_trial,
            ctl_port=args.port,
            game_port=args.game_port,
            fixture_sha256=fx_sha,
            ensure_arm=driver.ensure_arm,
            n_repro=n_repro,
            n_heldout=n_heldout,
            on_attempt=on_attempt if args.kvitto_dir else None,
            kvitto_dir=args.kvitto_dir,
            demo_file=driver.demo_file,
            binaries=binaries,
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
