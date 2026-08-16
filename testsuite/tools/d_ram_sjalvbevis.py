#!/usr/bin/env python3
"""RAM self-proof: knockback K1–K6 + prevention P1/P2 + heldout H1–H4.

Knockback: teleport to [-360,y,128.03125] with the facit velocity, 2.0 s
budget. OFF must bot_stall on the rail; ON must stand on the pinned land
(cell 638) without stall or unplanned fall.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from d_recipe import load_recipe, on_expected  # noqa: E402
from d_strata import FORBIDDEN_CTL, FORBIDDEN_GAME, pair_start_vel_ok  # noqa: E402

HERE = Path(__file__).resolve().parent
RAIL_GATES = HERE / "recept" / "ram-rail-gates.json"
PREVENT_GATES = HERE / "recept" / "ram-prevent-gates.json"
RAIL_CELLS = {5977, 5978, 5979, 5980, 5981, 5982}
LAND = [-352.0, -672.0, -16.0]


def knockback_points(gates: dict) -> list[dict]:
    rows = []
    for kb in gates.get("knockback") or []:
        rows.append({
            "id": str(kb["id"]),
            "y": float(kb["y"]),
            "cell": int(kb["cell"]),
            "velocity": [float(x) for x in kb["velocity"]],
            "budget_s": float(kb.get("budget_s") or 2.0),
            "pos": [-360.0, float(kb["y"]), 128.03125],
            "land": list((gates.get("gates") or {}).get("ram-rail", {}).get("land", {}).get("origin") or LAND),
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
    start_vel: list[float] | None = None


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

    def as_dict(self) -> dict:
        return {
            "kind": "ram-sjalvbevis",
            "recipe": self.recipe,
            "valid": self.valid,
            "invalid_reasons": list(self.invalid_reasons),
            "godkand": self.valid and self.pass_ok,
            "knockback": {"off_ok": self.knockback_off_ok, "on_ok": self.knockback_on_ok},
            "prevention": {"on_ok": self.prevent_on_ok},
            "heldout": {"on_ok": self.heldout_on_ok},
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
    ) -> None:
        self.recipe = recipe
        self.rail_gates = rail_gates
        self.prevent_gates = prevent_gates or {}
        self.exec_knockback = exec_knockback
        self.exec_trial = exec_trial
        self.ctl_port = ctl_port
        self.game_port = game_port
        self.ensure_arm = ensure_arm
        self.kb = knockback_points(rail_gates)
        self.p = prevention_specs(self.prevent_gates) if recipe.get("id") == "ram-prevent" else []
        self.h = heldout_specs() if recipe.get("id") in {"ram-rail", "ram-prevent"} else []
        if recipe.get("id") == "ram-rail":
            self.p = []
        if n_knock is not None:
            for s in self.kb:
                s["n_pairs"] = int(n_knock)
        if n_chain is not None:
            for s in self.p + self.h:
                s["n_pairs"] = int(n_chain)

    def preflight(self) -> list[str]:
        reasons = []
        if self.ctl_port in FORBIDDEN_CTL or self.game_port in FORBIDDEN_GAME:
            reasons.append("RA/main endpoint")
        rid = self.recipe.get("id")
        if rid not in {"ram-rail", "ram-prevent"}:
            reasons.append(f"not a ram recipe: {rid!r}")
        try:
            on_expected(self.recipe)
        except ValueError as exc:
            reasons.append(str(exc))
        if rid == "ram-rail" and len(self.kb) != 6:
            reasons.append(f"knockback points {len(self.kb)} != 6")
        ids = [s["id"] for s in self.kb]
        if rid == "ram-rail" and ids != ["K1", "K2", "K3", "K4", "K5", "K6"]:
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
        stall = raw.get("t_stall_gate") is not None or any(
            ev.get("ev") == "bot_stall" for ev in (raw.get("events") or [])
        )
        land_hit = bool(raw.get("land_hit") or raw.get("t_arrive") is not None)
        planned = intended_drop(raw, spec["land"])
        att = RAttempt(
            attempt_id=f"{spec['id']}-{arm.upper()}-{seq:02d}",
            stratum=spec["id"],
            arm=arm,
            valid=True,
            reason="ok",
            stall=stall,
            arrived=land_hit,
            land_hit=land_hit,
            start_vel=list(spec["velocity"]),
        )
        if arm == "off":
            if not stall:
                att.valid = False
                att.reason = "OFF knockback must bot_stall on the rail"
        else:
            if not land_hit:
                att.valid = False
                att.reason = "ON knockback must stand on land within 2.0 s"
            elif stall:
                att.valid = False
                att.reason = "ON knockback must not stall"
            elif not planned:
                att.valid = False
                att.reason = "unplanned peak_drop (not the rail Drop to 638)"
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
        arrived = raw.get("t_arrive") is not None and float(raw["t_arrive"]) <= spec["budget_s"]
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
        )
        if arm == "on" and att.valid:
            if not arrived or stall or rail_entry:
                att.valid = False
                att.reason = "ON chain must arrive, no stall, no rail entry"
        return att

    def run(self) -> RamReport:
        reasons = self.preflight()
        attempts: list[RAttempt] = []
        if reasons:
            return RamReport(str(self.recipe.get("id")), False, reasons, attempts)
        rid = self.recipe.get("id")
        extra: list[str] = []
        if rid == "ram-rail":
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
        kb_off_ok = rid != "ram-rail" or (len(kb_off) == 12 and all(a.stall for a in kb_off))
        kb_on_ok = rid != "ram-rail" or (len(kb_on) == 12 and all(a.land_hit and not a.stall for a in kb_on))
        if rid == "ram-rail" and (n_knock := self.kb[0]["n_pairs"]) != 2:
            kb_off_ok = len(kb_off) == 6 * n_knock and all(a.stall for a in kb_off)
            kb_on_ok = len(kb_on) == 6 * n_knock and all(a.land_hit and not a.stall for a in kb_on)

        p_on = [a for a in attempts if a.stratum in {"P1", "P2"} and a.arm == "on" and a.valid]
        h_on = [a for a in attempts if a.stratum in {"H1", "H2", "H3", "H4"} and a.arm == "on" and a.valid]
        want_p = sum(s["n_pairs"] for s in self.p)
        want_h = sum(s["n_pairs"] for s in self.h)
        p_ok = rid == "ram-rail" or (len(p_on) == want_p and all(a.arrived and not a.stall for a in p_on))
        h_ok = len(h_on) == want_h and all(a.arrived and not a.stall for a in h_on) if want_h else True
        valid = not extra
        pass_ok = valid and kb_off_ok and kb_on_ok and p_ok and h_ok
        return RamReport(
            str(rid), valid, extra, attempts,
            knockback_off_ok=kb_off_ok, knockback_on_ok=kb_on_ok,
            prevent_on_ok=p_ok, heldout_on_ok=h_ok, pass_ok=pass_ok,
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--game-port", type=int, default=27595)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--recipe", default="ram-rail", choices=("ram-rail", "ram-prevent"))
    ap.add_argument("--fixture", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--lock", type=Path)
    ap.add_argument("--commit", default="")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args(argv)
    fixture = args.fixture or (HERE / "recept" / f"{args.recipe}.json")
    recipe = load_recipe(fixture)
    rail = json.loads(RAIL_GATES.read_text(encoding="utf-8"))
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
        file_sha256,
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
    qw = file_sha256(DEFAULT_QWPROGS) if DEFAULT_QWPROGS.is_file() else "00" * 32
    mv = file_sha256(DEFAULT_MVDSV) if DEFAULT_MVDSV.is_file() else "00" * 32
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
