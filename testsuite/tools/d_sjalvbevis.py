#!/usr/bin/env python3
"""West-shelf self-proof drill (facit-d-sjalvbevis.md §2–§4).

Turnkey for GAP 4. --port 0 is spec-only; --port N uses d_live_driver.
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
from d_recipe import (  # noqa: E402
    avsett_drop_registered,
    gates_registered,
    load_avsett_drop,
    load_gates,
    load_recipe,
    on_expected,
)
from d_strata import (  # noqa: E402
    AVSETT_DROP_STRATA,
    FORBIDDEN_CTL,
    FORBIDDEN_GAME,
    HELDOUT_IDS,
    HELDOUT_PAIR_REPLACE_CAP,
    STRATA,
    STRATUM_AT_GATE,
    STRATUM_AT_VALUES,
    FallTracker,
    gate_passage,
    heldout_stratum_at,
    in_avsett_geometry,
    is_trap,
    pair_start_vel_ok,
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
    avsett_drop: bool = False
    arrived: bool = False
    events: list[dict] = field(default_factory=list)
    start_vel: list[float] | None = None


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
                    "avsett_drop": a.avsett_drop,
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
    gate_velocity: list[float] | None = None,
    gate_cell: int | None = None,
    gate_origin: list[float] | None = None,
    stratum_at: str | None = None,
    avsett_drop: dict | None = None,
) -> Attempt:
    spec = STRATA[stratum_id]
    aid = f"{stratum_id}-{arm.upper()}"
    if spec["kind"] == "heldout":
        if stratum_at not in STRATUM_AT_VALUES:
            return Attempt(
                aid, stratum_id, arm, False,
                "heldout_stratum_at missing or invalid (facit i=gate|ii=start)",
                events=events,
            )
        if stratum_at == STRATUM_AT_GATE:
            hit, how = gate_passage(gate, gate_cell=gate_cell, origin=gate_origin)
            if not hit:
                return Attempt(aid, stratum_id, arm, False, how, events=events)
            if gate_velocity is None:
                return Attempt(
                    aid, stratum_id, arm, False,
                    "no gate_velocity at the gate (no start-vel fallback)",
                    events=events,
                )
            vel = list(gate_velocity)
    ok, why = stratum_ok(stratum_id, vel, spec["start"], spec["goal"])
    if not ok:
        return Attempt(aid, stratum_id, arm, False, why, events=events)
    arrived, reason = arrived_in_budget(spec, events, t_arrive)
    trap = is_trap(events, gate, arrived_after_stall=any(e.get("ev") == "arrived" for e in events))
    fall = False
    avsett = False
    tracker = FallTracker()
    allow_avsett = stratum_id in AVSETT_DROP_STRATA and avsett_drop is not None
    for s in samples:
        if not tracker.update(float(s["z"]), bool(s.get("on_ground"))):
            continue
        origin = [s.get("x"), s.get("y"), s.get("z")]
        cell = s.get("cell")
        if allow_avsett and in_avsett_geometry(
            avsett_drop,
            origin=origin if origin[0] is not None else None,
            cell_id=cell if isinstance(cell, int) else None,
        ):
            avsett = True
            continue
        fall = True
        break
    return Attempt(
        aid, stratum_id, arm, True, reason,
        trap=trap, fall=fall, avsett_drop=avsett, arrived=arrived, events=events,
    )


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
        ensure_arm: Callable[[str], None] | None = None,
        avsett_drop: dict | None = None,
    ) -> None:
        self.recipe = recipe
        self.gates = gates
        self.exec_trial = exec_trial
        self.ctl_port = ctl_port
        self.game_port = game_port
        self.off_profile = off_profile
        self.on_profile = on_profile
        self.ensure_arm = ensure_arm
        self.avsett_drop = avsett_drop

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
        geom = self.avsett_drop
        if geom is None:
            try:
                geom = load_avsett_drop()
                self.avsett_drop = geom
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                reasons.append(f"avsett_drop fixture unreadable: {exc}")
                geom = None
        if geom is not None:
            awhy = avsett_drop_registered(
                geom,
                self.recipe["off"]["graph_stamp"],
                self.recipe["off"].get("graph_content_hash"),
            )
            if awhy:
                reasons.append(awhy)
        return reasons

    def _gate(self) -> dict:
        return self.gates["gates"]["west-shelf"]

    def _run_one(
        self,
        stratum_id: str,
        arm: str,
        seq: int,
        match_vel: list[float] | None = None,
    ) -> Attempt:
        spec = STRATA[stratum_id]
        if self.ensure_arm is not None:
            self.ensure_arm(arm)
        raw = self.exec_trial(
            stratum_id=stratum_id, arm=arm, spec=spec, seq=seq, match_vel=match_vel,
        )
        locus, _ = heldout_stratum_at(self.gates)
        att = classify_trial(
            stratum_id=stratum_id,
            arm=arm,
            vel=raw.get("vel") or [0, 0, 0],
            events=raw.get("events") or [],
            samples=raw.get("samples") or [],
            gate=self._gate(),
            t_arrive=raw.get("t_arrive"),
            gate_velocity=raw.get("gate_velocity"),
            gate_cell=raw.get("gate_cell"),
            gate_origin=raw.get("gate_origin"),
            stratum_at=locus,
            avsett_drop=self.avsett_drop,
        )
        att.attempt_id = f"{stratum_id}-{arm.upper()}-{seq:02d}"
        raw_vel = raw.get("measured_vel") if raw.get("measured_vel") is not None else raw.get("vel")
        att.start_vel = list(raw_vel) if raw_vel is not None else None
        if raw.get("stamp_ok") is False:
            att.valid = False
            att.reason = raw.get("stamp_reason") or "start-vel stamp failed"
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

        # Heldout: 5 valid OFF/ON pairs per stratum. Out-of-box pairs are
        # replaced (new attempt ids), not counted (facit r3 §2).
        extra: list[str] = []
        for sid in HELDOUT_IDS:
            need = int(STRATA[sid]["n_on"])
            got = 0
            seq = 0
            cap = need + int(HELDOUT_PAIR_REPLACE_CAP)
            while got < need:
                seq += 1
                if seq > cap:
                    extra.append(f"{sid}: could not assemble {need} valid pairs ({seq - 1} attempts)")
                    break
                off_att = self._run_one(sid, "off", seq)
                if not off_att.valid:
                    # Fail-closed at OFF stamp: do not burn an ON watch.
                    attempts.append(off_att)
                    continue
                on_att = self._run_one(sid, "on", seq, match_vel=off_att.start_vel)
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

        # Incomplete / invalid trials make the whole run invalid (facit §4).
        wanted = 4 + 8 + 40
        valid = [a for a in attempts if a.valid]
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
        raw_pointer=f"/tmp/d_sjalvbevis/{attempt_id}.jsonl",
        astar_before=empty,
        astar_after=empty,
        astar_next_best=empty,
    )


def _git_commit(repo: Path) -> str:
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=0, help="ctl port (0 = spec-only / no connect)")
    ap.add_argument("--game-port", type=int, default=27592)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--fixture", type=Path)
    ap.add_argument("--gates", type=Path)
    ap.add_argument("--avsett-drop", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--kvitto-dir", type=Path)
    ap.add_argument("--lock", type=Path)
    ap.add_argument(
        "--commit",
        default="",
        help="kvitto commit SHA (required for --run if git lookup fails, e.g. git archive)",
    )
    ap.add_argument("--smoke", action="store_true", help="T0 OFF handful; not a judged run")
    ap.add_argument("--n-smoke", type=int, default=2)
    ap.add_argument("--smoke-window", type=float, default=3.0)
    ap.add_argument("--run", action="store_true", help="full T0+heldout drill (fable-qa)")
    args = ap.parse_args(argv)
    recipe = load_recipe(args.fixture)
    gates = load_gates(args.gates)
    avsett = load_avsett_drop(args.avsett_drop)

    from d_live_driver import (  # local: tools/ is already on sys.path
        DEFAULT_LOCK,
        DEFAULT_MVDSV,
        DEFAULT_QWPROGS,
        LiveTrialDriver,
        file_sha256,
        parse_lock,
        refuse_ra,
    )

    if args.port:
        why = refuse_ra(args.port, args.game_port)
        if why:
            print(why, file=sys.stderr)
            return 2

    if not args.port:
        runner = DrillRunner(
            recipe=recipe,
            gates=gates,
            exec_trial=lambda **k: {"vel": [0, 0, 0], "events": [], "samples": []},
            ctl_port=args.port,
            game_port=args.game_port,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
            avsett_drop=avsett,
        )
        report = runner.run()
        payload = report.as_dict()
        text = json.dumps(payload, indent=2, sort_keys=True)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if payload.get("godkand") else 1

    # Live path. --smoke = T0 OFF röktest. --run = judged drill (fable-qa).
    # Bare --port prints identity and refuses to start the drill.
    lock_path = args.lock or DEFAULT_LOCK
    if not lock_path.is_file():
        print(f"no {lock_path} — hold the lock before opening Control()", file=sys.stderr)
        return 2
    lock = parse_lock(lock_path)
    qw = file_sha256(DEFAULT_QWPROGS) if DEFAULT_QWPROGS.is_file() else "00" * 32
    mv = file_sha256(DEFAULT_MVDSV) if DEFAULT_MVDSV.is_file() else "00" * 32
    commit = (args.commit or "").strip() or _git_commit(Path(__file__).resolve().parents[2])
    if args.run and not commit:
        print(
            "judged --run requires --commit (git lookup failed; pass the SHA from a worktree)",
            file=sys.stderr,
        )
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from runner.control import Control  # noqa: E402

    from datetime import datetime, timezone

    ctl = Control(args.host, args.port)
    locus, _ = heldout_stratum_at(gates)
    driver = LiveTrialDriver(
        ctl,
        gate=gates["gates"]["west-shelf"],
        recipe=recipe,
        lock_token=lock["token"],
        qwprogs_sha=qw,
        mvdsv_sha=mv,
        commit=commit,
        host=args.host,
        ctl_port=args.port,
        game_port=args.game_port,
        lock_path=lock_path,
        stratum_at=locus,
    )
    try:
        driver.prepare()
        ident = driver.confirm("off")
        if args.smoke:
            return _run_smoke(driver, lock, args, ident)
        if args.run:
            return _run_live(driver, recipe, gates, lock, args)
        payload = {
            "live": True,
            "identity": ident,
            "binaries": {"qwprogs_sha256": qw, "mvdsv_sha256": mv},
            "commit": commit,
            "hint": "pass --smoke (T0 OFF roktest) or --run (judged drill, fable-qa)",
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    finally:
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


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_smoke(driver, lock: dict, args, ident: dict) -> int:
    """T0 OFF only. Not a judged run. Never apply."""
    spec = STRATA["T0"]
    rows = []
    started = _iso_now()
    for i in range(1, max(1, args.n_smoke) + 1):
        t0 = _iso_now()
        raw = driver.exec_trial(
            stratum_id="T0", arm="off", spec=spec, seq=i, window_s=args.smoke_window,
        )
        att = classify_trial(
            stratum_id="T0",
            arm="off",
            vel=raw.get("vel") or [0, 0, 0],
            events=raw.get("events") or [],
            samples=raw.get("samples") or [],
            gate=driver.gate,
            t_arrive=raw.get("t_arrive"),
        )
        att.attempt_id = f"T0-OFF-{i:02d}"
        ended = _iso_now()
        kvitto_path = None
        raw_path = None
        if args.kvitto_dir:
            raw_path = args.kvitto_dir / f"{att.attempt_id}.jsonl"
            driver.write_attempt_raw(raw_path, raw)
            # Smoke never applies — refuse to invent ON-observed from expected.
        rows.append({
            "id": att.attempt_id,
            "valid": att.valid,
            "reason": att.reason,
            "trap": att.trap,
            "arrived": att.arrived,
            "t_arrive": raw.get("t_arrive"),
            "t_stall_gate": raw.get("t_stall_gate"),
            "vel": raw.get("vel"),
            "measured_vel": raw.get("measured_vel"),
            "gate_velocity": raw.get("gate_velocity"),
            "gate_cell": raw.get("gate_cell"),
            "events": [
                {k: e.get(k) for k in ("ev", "t", "rel_t", "engine_t", "cell", "reason")}
                for e in (raw.get("events") or [])
            ],
            "kvitto": str(kvitto_path) if kvitto_path else None,
            "raw_pointer": str(raw_path) if raw_path else None,
        })
    after = driver.confirm("off")
    payload = {
        "kind": "roktest",
        "judged": False,
        "n": len(rows),
        "attempts": rows,
        "identity_before": ident,
        "identity_after": after,
        "rtx_nav_patch": driver.get_cvar("rtx_nav_patch"),
        "binaries": {
            "qwprogs_sha256": driver.qwprogs_sha,
            "mvdsv_sha256": driver.mvdsv_sha,
        },
        "commit": driver.commit,
        "started_at": started,
        "ended_at": _iso_now(),
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _run_live(driver, recipe: dict, gates: dict, lock: dict, args) -> int:
    driver.measure_both_stamps()
    last: dict = {}

    def write_one(att, raw):
        if not args.kvitto_dir:
            return
        jsonl = args.kvitto_dir / f"{att.attempt_id}.jsonl"
        driver.write_attempt_raw(jsonl, raw)
        origin = raw.get("gate_origin")
        _, how = gate_passage(
            driver.gate, gate_cell=raw.get("gate_cell"), origin=origin
        )
        driver.write_attempt_kvitto(
            args.kvitto_dir / f"{att.attempt_id}.json",
            attempt_id=att.attempt_id,
            stratum_id=att.stratum,
            raw_pointer=str(jsonl),
            started_at=raw.get("started_at") or _iso_now(),
            ended_at=raw.get("ended_at") or _iso_now(),
            lock_owner=lock["owner"],
            lock_issued=lock["issued"],
            gate_velocity=raw.get("gate_velocity"),
            gate_cell=raw.get("gate_cell"),
            gate_aim_hit=how == "aim",
        )

    def exec_trial(**k):
        t0 = _iso_now()
        raw = driver.exec_trial(**k)
        raw["started_at"] = t0
        raw["ended_at"] = _iso_now()
        last["raw"] = raw
        return raw

    class _Live(DrillRunner):
        def _run_one(self, stratum_id: str, arm: str, seq: int, match_vel=None):
            att = super()._run_one(stratum_id, arm, seq, match_vel=match_vel)
            write_one(att, last.get("raw") or {})
            return att

    runner = _Live(
        recipe=recipe,
        gates=gates,
        exec_trial=exec_trial,
        ctl_port=args.port,
        game_port=args.game_port,
        off_profile={"rtx_nav_patch": "0"},
        on_profile={"rtx_nav_patch": "1"},
        ensure_arm=driver.ensure_arm,
        avsett_drop=load_avsett_drop(args.avsett_drop),
    )
    report = runner.run()
    if driver.arm == "on":
        driver.undo()
    payload = report.as_dict()
    payload["binaries"] = {
        "qwprogs_sha256": driver.qwprogs_sha,
        "mvdsv_sha256": driver.mvdsv_sha,
    }
    payload["stamps"] = driver.last_stamps
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if payload.get("godkand") else 1


if __name__ == "__main__":
    raise SystemExit(main())
