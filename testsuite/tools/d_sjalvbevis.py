#!/usr/bin/env python3
"""West-shelf self-proof drill (facit-d-sjalvbevis.md §2–§4).

Turnkey for GAP 4. No live run in this cluster — pass a ctl-like object.
Heldout consumes the KEDJAD population (A1–A4). T0 stays the teleport drill.
ON expected is read from the recipe fixture and never copied from observed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from d_kvitto import astar_path, make_kvitto  # noqa: E402
from d_recipe import gates_registered, load_gates, load_recipe, on_expected  # noqa: E402
from d_strata import (  # noqa: E402
    FORBIDDEN_CTL,
    FORBIDDEN_GAME,
    HELDOUT_IDS,
    STRATA,
    FallTracker,
    is_trap,
    profiles_ok,
    stratum_ok,
)

Ctl = Any


@dataclass
class Attempt:
    attempt_id: str
    stratum: str
    arm: str
    valid: bool
    reason: str
    trap: bool = False
    fall: bool = False
    arrived: bool = False
    events: list[dict] = field(default_factory=list)


@dataclass
class DrillReport:
    valid: bool
    invalid_reasons: list[str]
    attempts: list[Attempt]
    t0_pass: bool = False
    heldout_pass: bool = False

    def as_dict(self) -> dict:
        t0_off = [a for a in self.attempts if a.stratum == "T0" and a.arm == "off" and a.valid]
        t0_on = [a for a in self.attempts if a.stratum == "T0" and a.arm == "on" and a.valid]
        h_off = [a for a in self.attempts if a.stratum in HELDOUT_IDS and a.arm == "off" and a.valid]
        h_on = [a for a in self.attempts if a.stratum in HELDOUT_IDS and a.arm == "on" and a.valid]
        return {
            "valid": self.valid,
            "invalid_reasons": list(self.invalid_reasons),
            "godkand": self.valid and self.t0_pass and self.heldout_pass,
            "t0": {
                "off_n": len(t0_off),
                "off_traps": sum(1 for a in t0_off if a.trap),
                "on_n": len(t0_on),
                "on_traps": sum(1 for a in t0_on if a.trap),
                "on_arrived": sum(1 for a in t0_on if a.arrived),
                "pass": self.t0_pass,
            },
            "heldout": {
                "off_n": len(h_off),
                "on_n": len(h_on),
                "on_ok": sum(1 for a in h_on if a.arrived and not a.fall and not a.trap),
                "pass": self.heldout_pass,
            },
            "attempts": [
                {
                    "id": a.attempt_id,
                    "stratum": a.stratum,
                    "arm": a.arm,
                    "valid": a.valid,
                    "reason": a.reason,
                    "trap": a.trap,
                    "fall": a.fall,
                    "arrived": a.arrived,
                }
                for a in self.attempts
            ],
        }


def score_t0(attempts: list[Attempt]) -> bool:
    off = [a for a in attempts if a.stratum == "T0" and a.arm == "off" and a.valid]
    on = [a for a in attempts if a.stratum == "T0" and a.arm == "on" and a.valid]
    return (
        len(off) == 4
        and all(a.trap for a in off)
        and len(on) == 8
        and not any(a.trap for a in on)
        and all(a.arrived for a in on)
    )


def score_heldout(attempts: list[Attempt]) -> bool:
    off = [a for a in attempts if a.stratum in HELDOUT_IDS and a.arm == "off" and a.valid]
    on = [a for a in attempts if a.stratum in HELDOUT_IDS and a.arm == "on" and a.valid]
    if len(off) != 20 or len(on) != 20:
        return False
    for sid in HELDOUT_IDS:
        if sum(1 for a in on if a.stratum == sid) != 5:
            return False
        if sum(1 for a in off if a.stratum == sid) != 5:
            return False
    return all(a.arrived and not a.fall and not a.trap for a in on)


def arrived_in_budget(spec: dict, events: list[dict], t_arrive: float | None) -> tuple[bool, str]:
    # Facit §3: arrived after budget_s is not a hit. Missing time cannot prove the budget.
    if not any(e.get("ev") == "arrived" for e in events):
        return False, "ok"
    if t_arrive is None:
        for e in events:
            if e.get("ev") == "arrived":
                t_arrive = e.get("t", e.get("rel_t"))
                break
    budget = float(spec["budget_s"])
    if t_arrive is None:
        return False, "arrived without time — cannot prove budget"
    if float(t_arrive) > budget:
        return False, f"arrived at {t_arrive}s > budget {budget}s"
    return True, "ok"


def classify_trial(
    *,
    stratum_id: str,
    arm: str,
    vel: list[float],
    events: list[dict],
    samples: list[dict],
    gate: dict,
    t_arrive: float | None = None,
) -> Attempt:
    spec = STRATA[stratum_id]
    ok, why = stratum_ok(stratum_id, vel, spec["start"], spec["goal"])
    aid = f"{stratum_id}-{arm.upper()}"
    if not ok:
        return Attempt(aid, stratum_id, arm, False, why, events=events)
    arrived, reason = arrived_in_budget(spec, events, t_arrive)
    trap = is_trap(events, gate, arrived_after_stall=any(e.get("ev") == "arrived" for e in events))
    fall = False
    tracker = FallTracker()
    for s in samples:
        if tracker.update(float(s["z"]), bool(s.get("on_ground"))):
            fall = True
            break
    return Attempt(aid, stratum_id, arm, True, reason, trap=trap, fall=fall, arrived=arrived, events=events)


class DrillRunner:
    """Orchestrates T0 then A1–A4. `exec_trial` is injected so tests stay offline."""

    def __init__(
        self,
        *,
        recipe: dict,
        gates: dict,
        exec_trial: Callable[..., dict],
        ctl_port: int,
        game_port: int,
        off_profile: dict[str, str],
        on_profile: dict[str, str],
    ) -> None:
        self.recipe = recipe
        self.gates = gates
        self.exec_trial = exec_trial
        self.ctl_port = ctl_port
        self.game_port = game_port
        self.off_profile = off_profile
        self.on_profile = on_profile

    def preflight(self) -> list[str]:
        reasons: list[str] = []
        if self.ctl_port in FORBIDDEN_CTL or self.game_port in FORBIDDEN_GAME:
            reasons.append("RA/main endpoint")
        try:
            on_expected(self.recipe)
        except ValueError as exc:
            reasons.append(str(exc))
        gwhy = gates_registered(self.gates, self.recipe["off"]["graph_stamp"])
        if gwhy:
            reasons.append(gwhy)
        pwhy = profiles_ok(self.off_profile, self.on_profile)
        if pwhy:
            reasons.append(pwhy)
        return reasons

    def _gate(self) -> dict:
        return self.gates["gates"]["west-shelf"]

    def _run_one(self, stratum_id: str, arm: str, seq: int) -> Attempt:
        spec = STRATA[stratum_id]
        raw = self.exec_trial(stratum_id=stratum_id, arm=arm, spec=spec, seq=seq)
        att = classify_trial(
            stratum_id=stratum_id,
            arm=arm,
            vel=raw.get("vel") or [0, 0, 0],
            events=raw.get("events") or [],
            samples=raw.get("samples") or [],
            gate=self._gate(),
            t_arrive=raw.get("t_arrive"),
        )
        att.attempt_id = f"{stratum_id}-{arm.upper()}-{seq:02d}"
        return att

    def run(self) -> DrillReport:
        reasons = self.preflight()
        attempts: list[Attempt] = []
        if reasons:
            return DrillReport(False, reasons, attempts)

        # T0: 4 OFF then 8 ON (isolated drill; not heldout pairs).
        for i in range(1, STRATA["T0"]["n_off"] + 1):
            attempts.append(self._run_one("T0", "off", i))
        for i in range(1, STRATA["T0"]["n_on"] + 1):
            attempts.append(self._run_one("T0", "on", i))

        # Heldout: 5 OFF/ON pairs per stratum, kedjad only. New ids, no T0 teleport start.
        for sid in HELDOUT_IDS:
            for i in range(1, STRATA[sid]["n_on"] + 1):
                attempts.append(self._run_one(sid, "off", i))
                attempts.append(self._run_one(sid, "on", i))

        # Incomplete / invalid trials make the whole run invalid (facit §4).
        wanted = 4 + 8 + 40
        valid = [a for a in attempts if a.valid]
        extra: list[str] = []
        if len(valid) != wanted:
            extra.append(f"incomplete pairs: {len(valid)} valid of {wanted}")
        t0_ok = score_t0(attempts)
        h_ok = score_heldout(attempts)
        ok = not extra
        return DrillReport(ok, extra, attempts, t0_pass=t0_ok, heldout_pass=h_ok)


def receipt_skeleton(recipe: dict, stratum_id: str, attempt_id: str) -> dict:
    """Per-attempt d-kvitto row. ON expected still comes from the fixture."""
    off = dict(recipe["off"])
    on = on_expected(recipe)
    empty = astar_path(found=False)
    return make_kvitto(
        riglock_owner="pending",
        riglock_issued_at="1970-01-01T00:00:00+00:00",
        riglock_valid_from="1970-01-01T00:00:00+00:00",
        riglock_valid_to="1970-01-01T00:00:00+00:00",
        riglock_path="/home/xerial/lab/.rig-lock",
        run_started_at="1970-01-01T00:00:00+00:00",
        run_ended_at="1970-01-01T00:00:00+00:00",
        endpoint_host="127.0.0.1",
        endpoint_ctl_port=27996,
        endpoint_game_port=27591,
        map_name="dm3",
        binary_sha256="00" * 32,
        commit="unrun",
        stamps_off_expected=off,
        stamps_off_observed=off,
        stamps_on_expected=on,
        stamps_on_observed=on,
        stamps_undo_expected=off,
        stamps_undo_observed=off,
        recipe={"id": recipe["id"], "taxonomy_class": recipe["taxonomy_class"], "evidence": recipe["evidence"]},
        seed=0,
        stratum={"id": stratum_id, "attempt": attempt_id},
        raw_pointer=f"d_sjalvbevis:{attempt_id}",
        astar_before=empty,
        astar_after=empty,
        astar_next_best=empty,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=0, help="ctl port (0 = spec-only / no connect)")
    ap.add_argument("--game-port", type=int, default=0)
    ap.add_argument("--fixture", type=Path)
    ap.add_argument("--gates", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    recipe = load_recipe(args.fixture)
    gates = load_gates(args.gates)
    if args.port:
        print("live GAP 4 is not this cluster — refusing to connect", file=sys.stderr)
        return 2
    runner = DrillRunner(
        recipe=recipe,
        gates=gates,
        exec_trial=lambda **k: {"vel": [0, 0, 0], "events": [], "samples": []},
        ctl_port=args.port,
        game_port=args.game_port,
        off_profile={"rtx_nav_patch": "0"},
        on_profile={"rtx_nav_patch": "1"},
    )
    report = runner.run()
    payload = report.as_dict()
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if payload.get("godkand") else 1


if __name__ == "__main__":
    raise SystemExit(main())
