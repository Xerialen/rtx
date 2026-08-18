#!/usr/bin/env python3
"""Live exec_trial driver for the west-shelf self-proof (GAP 4 / cluster 4).

Talks ctlproto on a dedicated D-instance (never RA/main). Does not invent
ON-expected from observed. Arm switching is fixa apply/undo with lock_token.

RESTART — documented fallback if undo has no AppliedTxn snapshot (this
process never applied, or the unit was started already-meshed), or after
a core-dump. Restart ONLY toolbox-d-test, never RA/main. After a failed
unit, reset-failed first or systemd-run refuses the name:

  systemctl --user reset-failed toolbox-d-test
  systemctl --user stop toolbox-d-test && systemd-run --user --unit=toolbox-d-test \\
    -p RuntimeMaxSec=10800 --working-directory=$HOME/.local/share/qw-fasttrack/runtime-tbx-d \\
    $HOME/.local/share/qw-fasttrack/runtime-tbx-d/mvdsv -port 27592 \\
    +set rtx_nav_patch 0 +exec fasttrack.cfg
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from pathlib import Path
from typing import Any, Callable

import json

import undo_bevis  # noqa: E402
from d_kvitto import (  # noqa: E402
    astar_from_route_resp,
    astar_path,
    make_kvitto,
    recipe_cvars,
    write_attempt_raw_file,
    write_kvitto,
)
from d_failclosed import guard_mutation, guard_plant, is_plant_command
from d_recipe import on_expected
from d_strata import (
    FORBIDDEN_CTL,
    FORBIDDEN_GAME,
    PAIR_VEL_TOL,
    STRATA,
    STRATUM_AT_START,
    FallTracker,
    in_gate,
    stratum_ok,
    vh,
)

DEFAULT_QWPROGS = Path.home() / ".local/share/qw-fasttrack/runtime-tbx-d/qw/qwprogs.so"
DEFAULT_MVDSV = Path.home() / ".local/share/qw-fasttrack/runtime-tbx-d/mvdsv"
DEFAULT_LOCK = Path.home() / "lab/.rig-lock"
POLL_S = 0.05
T0_SETTLE_S = 0.15
# Poll window between the two status samples. The *divisor* is server
# time (StatusResp.time), not this constant — R4's nominal-dt regression
# was v·(actual/nominal−1) on frame-quantized ticks.
VEL_SAMPLE_S = 0.04
VEL_ALIGN = 2.5  # leftover: command-align margin (pair rule is PAIR_VEL_TOL)
VEL_RETRIES = 8
PREP_CVARS = ("rtx_telemetry", "rtx_bot_pacifist")
# Telemetry on also emits Pmove at tick rate. Keep stall/arrived/drop only.
KEEP_EVENTS = {"bot_stall", "arrived", "goto_stall", "peak_drop_150"}

RESTART = (
    "systemctl --user reset-failed toolbox-d-test; "
    "systemctl --user stop toolbox-d-test && systemd-run --user --unit=toolbox-d-test "
    "-p RuntimeMaxSec=10800 --working-directory=$HOME/.local/share/qw-fasttrack/runtime-tbx-d "
    "$HOME/.local/share/qw-fasttrack/runtime-tbx-d/mvdsv -port 27592 "
    "+set rtx_nav_patch 0 +exec fasttrack.cfg"
)

# MVDSV 1.20-dev (runtime-tbx-d/mvdsv) records via console commands, not
# sv_autorecord. Path format in the binary is %s/%s/%s.mvd =
# gamedir / sv_demoDir / stem. We pin sv_demoDir=demos so the file is
# qw/demos/{stem}.mvd under the runtime cwd. Use sv_demostop, never the
# ctl verb `stop` (that stops the bot).
DEMO_RELDIR = "qw/demos"
DEMO_CMD_RECORD = "sv_demorecord"
DEMO_CMD_STOP = "sv_demostop"
_DEMO_STEM_OK = re.compile(r"[^A-Za-z0-9._-]+")


def compact_demo_ts(iso_ts: str) -> str:
    """2026-08-16T14:30:22Z / +00:00 → 20260816T143022Z."""
    s = (iso_ts or "").strip()
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    s = s.replace("-", "").replace(":", "")
    if not s.endswith("Z"):
        s += "Z"
    return s


def demo_stem(commit: str, started_at: str, *, smoke: bool = False) -> str:
    """{commit8}_{YYYYMMDDTHHMMSSZ}[_smoke] — no .mvd; MVDSV appends it."""
    sha = re.sub(r"[^0-9a-fA-F]", "", commit or "")[:8]
    if len(sha) < 8:
        sha = (sha + "00000000")[:8]
    stem = f"{sha.lower()}_{compact_demo_ts(started_at)}"
    if smoke:
        stem += "_smoke"
    return _DEMO_STEM_OK.sub("", stem)


def demo_relpath(stem: str) -> str:
    return f"{DEMO_RELDIR}/{stem}.mvd"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_lock(path: Path) -> dict[str, str]:
    body = path.read_text(encoding="utf-8", errors="replace").strip()
    if not body:
        raise RuntimeError(f"{path} is empty")
    parts = body.split()
    issued = next((p for p in parts if "T" in p and p.endswith("Z")), "1970-01-01T00:00:00Z")
    return {"token": parts[0], "owner": parts[0], "issued": issued, "body": body}


def lock_token_from_file(path: Path) -> str:
    return parse_lock(path)["token"]


def heading_vel(start: list[float], goal: list[float], speed: float) -> list[float]:
    dx, dy = goal[0] - start[0], goal[1] - start[1]
    n = math.hypot(dx, dy)
    if n == 0:
        return [0.0, 0.0, 0.0]
    return [speed * dx / n, speed * dy / n, 0.0]


def mid_band_speed(spec: dict) -> float:
    if spec.get("kind") == "trap":
        return 0.0
    lo, hi = float(spec["vh_lo"]), float(spec["vh_hi"])
    return 0.5 * (lo + hi)


def vel_from_pair(a: dict, b: dict, dt: float | None = None) -> list[float]:
    if dt is None:
        dt = float(b["t"]) - float(a["t"])
    if dt <= 1e-4:
        return [0.0, 0.0, 0.0]
    return [
        (b["x"] - a["x"]) / dt,
        (b["y"] - a["y"]) / dt,
        (b["z"] - a["z"]) / dt,
    ]


def origin_vel(o0: list[float], o1: list[float], dt: float) -> list[float]:
    if dt <= 1e-4:
        return [0.0, 0.0, 0.0]
    return [(float(o1[i]) - float(o0[i])) / dt for i in range(3)]


def status_dt(s0: dict, s1: dict) -> float:
    """Server-clock interval from two status samples (A-stamp pattern)."""
    t0 = s0.get("t", s0.get("time"))
    t1 = s1.get("t", s1.get("time"))
    if t0 is None or t1 is None:
        return 0.0
    return float(t1) - float(t0)


def vel_components_within(a: list[float], b: list[float], tol: float) -> bool:
    aa = list(a) + [0.0, 0.0, 0.0]
    bb = list(b) + [0.0, 0.0, 0.0]
    return all(abs(aa[i] - bb[i]) <= tol for i in range(3))


def refuse_ra(ctl_port: int, game_port: int) -> str | None:
    if ctl_port in FORBIDDEN_CTL:
        return f"ctl port {ctl_port} is RA/main — dedicated D instance only"
    if game_port in FORBIDDEN_GAME:
        return f"game port {game_port} is RA/main — dedicated D instance only"
    return None


def annotate_event(ev: dict, rel_t: float) -> dict:
    """Budget grind reads t / rel_t. Engine t is absolute server time — keep as engine_t."""
    row = dict(ev)
    if "t" in row:
        row["engine_t"] = row["t"]
    row["rel_t"] = float(rel_t)
    row["t"] = float(rel_t)
    return row


class LiveTrialDriver:
    """One ctl connection. exec_trial is the DrillRunner hook."""

    def __init__(
        self,
        ctl,
        *,
        gate: dict,
        recipe: dict,
        lock_token: str,
        qwprogs_sha: str,
        mvdsv_sha: str,
        commit: str,
        host: str = "127.0.0.1",
        ctl_port: int = 27996,
        game_port: int = 27592,
        lock_path: Path = DEFAULT_LOCK,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        stratum_at: str | None = None,
        runtime_dir: Path | None = None,
        bevis_ledger: Path | None = None,
    ) -> None:
        why = refuse_ra(ctl_port, game_port)
        if why:
            raise RuntimeError(why)
        self.ctl = ctl
        self.gate = gate
        self.recipe = recipe
        self.lock_token = lock_token
        self.qwprogs_sha = qwprogs_sha
        self.mvdsv_sha = mvdsv_sha
        self.commit = commit
        self.host = host
        self.ctl_port = ctl_port
        self.game_port = game_port
        self.lock_path = lock_path
        self.sleep = sleep
        self.now = now
        self.stratum_at = stratum_at or gate.get("heldout_stratum_at")
        self.runtime_dir = Path(runtime_dir) if runtime_dir else DEFAULT_MVDSV.parent
        # Armväxling undo:ar hundratals gånger per session, så bevisen tar radform
        # i stället för en fil per växling — samma fält, samma händelse-id, en
        # bokföring som går att läsa. Ingen undo utan rad.
        #
        # INGEN default: en outsagd liggarsökväg blev först runtime_dir, och första
        # testkörningen skrev nio rader in i tbx-d1:s LEVANDE runtime-katalog. En
        # bekväm default för bokföring skriver bokföringen på fel ställe tyst.
        self.bevis_ledger = Path(bevis_ledger) if bevis_ledger else None
        self.arm = "off"
        self._ent: int | None = None
        self.last_stamps: dict[str, dict] = {}
        self.astar_by: dict[tuple[str, str], dict] = {}
        self.cmds: list[str] = []
        self._saved_cvars: dict[str, str | None] = {}
        self.demo_file: str | None = None
        self._demo_stem: str | None = None

    def request(self, cmd: str) -> dict:
        if is_plant_command(cmd):
            guard_plant(self.recipe)
        self.cmds.append(cmd)
        return self.ctl.request(cmd)

    def start_demo(self, *, smoke: bool = False, started_at: str | None = None) -> str:
        """One MVD per drill. Name {commit8}_{UTC}[_smoke].mvd under qw/demos/."""
        if self._demo_stem:
            self.stop_demo()
        if started_at is None:
            from datetime import datetime, timezone
            started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stem = demo_stem(self.commit, started_at, smoke=smoke)
        if not stem:
            raise RuntimeError("demo stem empty")
        (self.runtime_dir / "qw" / "demos").mkdir(parents=True, exist_ok=True)
        # Pin dir so binary %s/%s/%s.mvd = qw/demos/{stem}.mvd.
        self.request("set sv_demoDir demos")
        self.request(f"runcmd {DEMO_CMD_RECORD} {stem}")
        self._demo_stem = stem
        self.demo_file = demo_relpath(stem)
        return self.demo_file

    def stop_demo(self) -> None:
        """sv_demostop via runcmd. Keeps demo_file as a pointer after stop."""
        if not self._demo_stem:
            return
        try:
            self.request(f"runcmd {DEMO_CMD_STOP}")
        finally:
            self._demo_stem = None

    def wait_ready(self, timeout: float = 90.0) -> dict:
        deadline = self.now() + timeout
        last = None
        while self.now() < deadline:
            last = self.request("status")["data"]
            if last.get("navmesh") == "ready" and last.get("bots"):
                self._ent = last["bots"][0]["ent"]
                return last
            self.sleep(0.5)
        raise RuntimeError("navmesh/bot never ready")

    def ent(self) -> int:
        if self._ent is None:
            self.wait_ready()
        assert self._ent is not None
        return self._ent

    def bot(self) -> dict:
        data = self.request("status")["data"]
        e = self.ent()
        row = next((b for b in data.get("bots") or [] if b.get("ent") == e), None)
        if not row:
            raise RuntimeError(f"bot {e} missing from status")
        out = dict(row)
        server_t = data.get("time", data.get("t"))
        if server_t is not None:
            out["t"] = float(server_t)
        return out

    def get_cvar(self, name: str) -> str | None:
        try:
            data = self.request(f"get {name}")["data"]
        except Exception:
            return None
        if isinstance(data, dict) and "string" in data:
            return str(data["string"])
        return None

    def prepare(self) -> None:
        """Enable BotStall/Arrived telemetry. Never touch rtx_nav_patch (fixa owns the arm)."""
        for name in PREP_CVARS:
            if name not in self._saved_cvars:
                self._saved_cvars[name] = self.get_cvar(name)
            self.request(f"set {name} 1")
        self.wait_ready()

    def restore(self) -> None:
        for name, value in self._saved_cvars.items():
            if value is not None:
                try:
                    self.request(f"set {name} {value}")
                except Exception:
                    continue

    def identity(self) -> dict:
        """Counts + both hash levels via dry-run (no mutation)."""
        rid = self.recipe.get("id") or "west-shelf"
        d = self.request(f"fixa {rid} dry-run")["data"]
        return {
            "cells": int(d["cells"]),
            "links": int(d["links"]),
            "rj_links": int(d["rj_links"]),
            "graph_stamp": str(d["stamp"]),
            "graph_content_hash": str(d["content_hash"]),
            "outcome": d.get("outcome"),
        }

    def confirm(self, want: str) -> dict:
        ident = self.identity()
        exp = self.recipe["off"] if want == "off" else on_expected(self.recipe)
        for k in ("cells", "links", "rj_links", "graph_stamp", "graph_content_hash"):
            if ident[k] != exp[k]:
                raise RuntimeError(
                    f"stamp {want} mismatch {k}: live {ident[k]!r} != expected {exp[k]!r}"
                )
        self.last_stamps[want] = {k: ident[k] for k in (
            "cells", "links", "rj_links", "graph_stamp", "graph_content_hash"
        )}
        self.arm = want
        return ident

    def quiesce(self) -> None:
        """Stop/hold the puppet before a graph swap so it cannot hold an ON-leg across undo."""
        try:
            e = self.ent()
        except Exception:
            return
        for verb in ("stop", "hold"):
            try:
                self.request(f"{verb} {e}")
            except Exception:
                continue

    def apply(self) -> dict:
        if not self.lock_token:
            raise RuntimeError("fixa apply requires lock_token")
        live = self.identity()
        guard_mutation("apply", recipe=self.recipe, live=live)
        self.quiesce()
        rid = self.recipe.get("id") or "west-shelf"
        cmd = f"fixa {rid} apply lock {self.lock_token}"
        d = self.request(cmd)["data"]
        if d.get("outcome") not in {"applied", "already_meshed"}:
            raise RuntimeError(f"fixa apply failed: {d}")
        ident = self.confirm("on")
        self._apply_arm_cvars("on")
        return ident

    def undo(self, *, handelse: str = "armväxling ON→OFF") -> dict:
        if not self.lock_token:
            raise RuntimeError("fixa undo requires lock_token")
        live = self.identity()
        guard_mutation("undo", recipe=self.recipe, live=live)
        self._apply_arm_cvars("off")
        self.quiesce()
        rid = self.recipe.get("id") or "west-shelf"
        cmd = f"fixa {rid} undo lock {self.lock_token}"
        # Liggarraden reserveras före mutationen och skrivs efter den. Ett undo som
        # inte lämnat en rad rapporteras inte som undone — en obevisad undo och en
        # undo som aldrig kördes ser likadana ut i efterhand.
        if self.bevis_ledger is None:
            raise RuntimeError(
                "undo kräver bevis_ledger — beviset är en del av operationen. "
                "Kör med --kvitto-dir, eller sätt bevis_ledger explicit"
            )
        res = undo_bevis.reservera(self.bevis_ledger, ledger=True)
        vantat = {
            k: (self.recipe.get("off") or {}).get(k)
            for k in ("cells", "links", "rj_links", "graph_stamp", "graph_content_hash")
            if (self.recipe.get("off") or {}).get(k) is not None
        }
        holder: dict = {}

        def _undo():
            holder["d"] = self.request(cmd)["data"]
            return holder["d"]

        ut = undo_bevis.undo_med_bevis(
            las_identitet=self.identity,
            gor_undo=_undo,
            reservation=res,
            unit=undo_bevis.UNIT_FOR_CTL.get(self.ctl_port, f"ctl-{self.ctl_port}"),
            ctl_port=self.ctl_port,
            handelse=handelse,
            variant=rid,
            forvantat=vantat or None,
            fore=live,
        )
        d = holder["d"]
        if ut["utfall"] != undo_bevis.UNDONE:
            raise RuntimeError(
                f"fixa undo failed ({ut['utfall']}): {d.get('reason')}. "
                f"Restart ONLY toolbox-d-test in OFF: {RESTART}"
            )
        return self.confirm("off")

    def _apply_arm_cvars(self, want: str) -> None:
        """rtx_r1_lite follows the recipe arm profile. Default off = no-op."""
        cv = ((self.recipe.get("cvars") or {}).get(want) or {})
        if "rtx_r1_lite" not in cv:
            return
        self.request(f"set rtx_r1_lite {cv['rtx_r1_lite']}")

    def ensure_arm(self, want: str) -> None:
        if want not in {"off", "on"}:
            raise ValueError(want)
        if self.arm == want:
            self.confirm(want)
            self._apply_arm_cvars(want)
            return
        if want == "on":
            self.apply()
        else:
            self.undo()

    def commanded_vel(self, spec: dict) -> list[float]:
        if spec.get("kind") == "trap":
            return [0.0, 0.0, 0.0]
        return heading_vel(spec["start"], spec["goal"], mid_band_speed(spec))

    def cell_at(self, origin: list[float]) -> int | None:
        try:
            d = self.request(f"cell {origin[0]} {origin[1]} {origin[2]}")["data"]
        except Exception:
            return None
        if isinstance(d, dict) and isinstance(d.get("cell"), int):
            return int(d["cell"])
        return None

    def _clear_events(self) -> None:
        ev = getattr(self.ctl, "events", None)
        if ev is not None:
            ev.clear()

    def _drain_events(self, rel_t: float, bot: int) -> list[dict]:
        raw = list(getattr(self.ctl, "events", []) or [])
        if hasattr(self.ctl, "events"):
            self.ctl.events.clear()
        out = []
        for ev in raw:
            if ev.get("bot") not in (None, bot):
                continue
            if ev.get("ev") not in KEEP_EVENTS:
                continue
            out.append(annotate_event(ev, rel_t))
        return out

    def watch(self, spec: dict, window_s: float) -> dict:
        e = self.ent()
        target = spec["goal"]
        t0 = self.now()
        self._clear_events()
        self.request(f"goto {e} {target[0]} {target[1]} {target[2]}")
        samples: list[dict] = []
        events: list[dict] = []
        gate_vel: list[float] | None = None
        gate_cell: int | None = None
        gate_origin: list[float] | None = None
        prev: dict | None = None
        t_arrive: float | None = None
        t_stall_gate: float | None = None
        fall = FallTracker()
        gate_cells = list(self.gate.get("cell_ids") or [])
        while self.now() - t0 < window_s:
            b = self.bot()
            t = self.now() - t0
            o = [float(x) for x in b["origin"]]
            on_ground = bool(b.get("on_ground"))
            samp = {
                "t": t,
                "x": o[0],
                "y": o[1],
                "z": o[2],
                "speed": float(b.get("speed") or 0.0),
                "on_ground": on_ground,
            }
            samples.append(samp)
            if fall.update(o[2], on_ground):
                events.append({
                    "ev": "peak_drop_150",
                    "t": t,
                    "rel_t": t,
                    "z": o[2],
                    "peak": fall.peak,
                    "drop_dz": fall.episode_dz,
                    "origin": o,
                    "cell": self.cell_at(o),
                })
            if prev is not None:
                v = vel_from_pair(prev, samp)
                near = in_gate(self.gate, origin=o, cell_id=None)
                cell = None
                if near or (gate_cells and in_gate(self.gate, origin=o, cell_id=None)):
                    cell = self.cell_at(o)
                if in_gate(self.gate, origin=o, cell_id=cell):
                    gate_vel = v
                    gate_origin = o
                    if isinstance(cell, int) and cell in gate_cells:
                        gate_cell = cell
            prev = samp
            for ev in self._drain_events(t, e):
                events.append(ev)
                cell = ev.get("cell")
                origin = ev.get("origin")
                if ev.get("ev") == "bot_stall" and in_gate(
                    self.gate,
                    origin=origin if isinstance(origin, (list, tuple)) else None,
                    cell_id=cell if isinstance(cell, int) else None,
                ):
                    if t_stall_gate is None:
                        t_stall_gate = float(ev.get("rel_t", t))
                    if gate_origin is None and isinstance(origin, (list, tuple)):
                        gate_origin = [float(x) for x in origin]
                    if isinstance(cell, int) and cell in gate_cells:
                        gate_cell = cell
                if ev.get("ev") == "arrived" and t_arrive is None:
                    t_arrive = float(ev.get("rel_t", t))
            if t_arrive is not None or t_stall_gate is not None:
                break
            self.sleep(POLL_S)
        try:
            self.request(f"stop {e}")
        except Exception:
            pass
        return {
            "samples": samples,
            "events": events,
            "t_arrive": t_arrive,
            "t_stall_gate": t_stall_gate,
            "gate_velocity": gate_vel,
            "gate_cell": gate_cell,
            "gate_origin": gate_origin,
        }

    def _teleport(self, e: int, pos: list[float], vel: list[float]) -> None:
        self.request(
            f"teleport {e} {pos[0]} {pos[1]} {pos[2]} {vel[0]} {vel[1]} {vel[2]}"
        )

    def measure_origin_vel(self) -> list[float]:
        """dxyz / (t1−t0) on StatusResp.time. Same pattern as A-stamping."""
        s0 = self.bot()
        self.sleep(VEL_SAMPLE_S)
        s1 = self.bot()
        return origin_vel(s0["origin"], s1["origin"], status_dt(s0, s1))

    def stamp_start_vel(
        self,
        e: int,
        start: list[float],
        cmd_vel: list[float],
        *,
        align_to: list[float] | None = None,
    ) -> tuple[list[float], int, bool]:
        """Place at `start` carrying `cmd_vel` and measure on the server clock.

        T0 rest is measured *after* the caller's settle window, in place —
        a re-teleport here restarts shelf-fall / leftover decel.

        Heldout OFF (`align_to is None`): one teleport+measure. Do not
        retry against the nominal command — the engine reshapes air and
        friction starts.

        Heldout ON (`align_to` = partner measured): re-stamp until each
        component is within PAIR_VEL_TOL of the partner. Exhaustion is
        fail-closed (ok=False) so the caller skips the watch.

        facit r3: `align_to` may be a BANK (list of measured vectors, the
        four pre-ON OFF launches on 1416-1124); a stamp that matches any
        member is ok. A single vector keeps the r2 behaviour.
        """
        rest = all(abs(float(c)) <= 1e-9 for c in cmd_vel)
        if rest:
            return self.measure_origin_vel(), 1, True
        if align_to is None:
            self._teleport(e, start, cmd_vel)
            return self.measure_origin_vel(), 1, True
        if align_to and isinstance(align_to[0], (list, tuple)):
            targets = [list(v) for v in align_to]
        else:
            targets = [list(align_to)]
        measured = [0.0, 0.0, 0.0]
        tries = 0
        for tries in range(1, VEL_RETRIES + 1):
            self._teleport(e, start, cmd_vel)
            measured = self.measure_origin_vel()
            if any(vel_components_within(measured, t, PAIR_VEL_TOL) for t in targets):
                return measured, tries, True
        return measured, tries, False

    def exec_trial(
        self,
        *,
        stratum_id: str,
        arm: str,
        spec: dict,
        seq: int,
        window_s: float | None = None,
        match_vel: list[float] | None = None,
    ) -> dict:
        e = self.ent()
        start = spec["start"]
        cmd_vel = self.commanded_vel(spec)
        try:
            self.request(f"stop {e}")
        except Exception:
            pass
        try:
            self.request(f"hold {e}")
        except Exception:
            pass
        self.request(f"prep {e} 100 0")
        # Land first so the vel-stamp is not fighting a fall from the previous trial.
        self._teleport(e, start, [0.0, 0.0, 0.0])
        self.sleep(T0_SETTLE_S if spec.get("kind") == "trap" else 0.05)
        stamp_reason = "ok"
        if spec.get("kind") == "trap":
            # T0: rest after settle, always watch. Do not fail-closed here.
            measured, vel_tries, stamp_ok = self.stamp_start_vel(e, start, cmd_vel)
            stamp_ok = True
        else:
            measured, vel_tries, stamp_ok = self.stamp_start_vel(
                e, start, cmd_vel, align_to=match_vel,
            )
            if stamp_ok and match_vel is None and stratum_id in STRATA:
                sok, swhy = stratum_ok(stratum_id, measured, spec["start"], spec["goal"])
                if not sok:
                    stamp_ok = False
                    stamp_reason = swhy
            elif not stamp_ok:
                stamp_reason = (
                    f"start-vel stamp exhausted vs partner ({vel_tries} tries, "
                    f"tol={PAIR_VEL_TOL})"
                )
        empty_watch = {
            "samples": [],
            "events": [],
            "t_arrive": None,
            "t_stall_gate": None,
            "gate_velocity": None,
            "gate_cell": None,
            "gate_origin": None,
        }
        # A* is graph-only; snapshot before watch so raw always carries
        # before/after/next-best even if the window is skipped.
        blob = self.snapshot_astar(spec, stratum_id)
        if spec.get("kind") != "trap" and not stamp_ok:
            watched = empty_watch
        else:
            if window_s is None:
                if spec.get("kind") == "trap":
                    window_s = float(spec["off_window_s"] if arm == "off" else spec["budget_s"] + 1.5)
                else:
                    window_s = float(spec["budget_s"] + 5.0)
            watched = self.watch(spec, float(window_s))
        if spec.get("kind") == "trap":
            vel = measured
        elif self.stratum_at == STRATUM_AT_START:
            vel = measured
        else:
            # locus=gate: never fall back to start-vel. classify_trial invalidates a miss.
            vel = watched.get("gate_velocity")
        start_cell = self.cell_at([float(x) for x in start])
        off_blob = self.astar_by.get((stratum_id, "off")) or {}
        on_blob = self.astar_by.get((stratum_id, "on")) or {}
        empty = astar_path(found=False)
        after = blob.get("path") or empty
        if self.arm == "on" and on_blob.get("path"):
            after = on_blob["path"]
        before = off_blob.get("path") or (after if self.arm == "off" else empty)
        next_best = blob.get("next_best") or on_blob.get("next_best") or off_blob.get("next_best") or empty
        landing_cell = None
        for ev in watched["events"]:
            if ev.get("ev") != "peak_drop_150":
                continue
            if ev.get("cell") is None and ev.get("origin"):
                ev["cell"] = self.cell_at(ev["origin"])
            if isinstance(ev.get("cell"), int):
                landing_cell = ev["cell"]
        selected = None
        if after.get("links"):
            selected = int(after["links"][0])
        return {
            "vel": vel,
            "commanded_vel": cmd_vel,
            "measured_vel": measured,
            "vel_tries": vel_tries,
            "stamp_ok": stamp_ok,
            "stamp_reason": stamp_reason,
            "match_vel": list(match_vel) if match_vel is not None else None,
            "gate_velocity": watched.get("gate_velocity"),
            "gate_cell": watched.get("gate_cell"),
            "gate_origin": watched.get("gate_origin"),
            "start_cell": start_cell,
            "landing_cell": landing_cell,
            "selected_link": selected,
            "astar_before": before,
            "astar_after": after,
            "astar_next_best": next_best,
            "events": watched["events"],
            "samples": watched["samples"],
            "t_arrive": watched.get("t_arrive"),
            "t_stall_gate": watched.get("t_stall_gate"),
            "vh": None if vel is None else vh(vel),
            "stratum_id": stratum_id,
            "arm": arm,
            "seq": seq,
        }

    def exec_knockback(
        self,
        *,
        pos: list[float],
        vel: list[float],
        land: list[float],
        window_s: float = 2.0,
        seq: int = 1,
        arm: str = "off",
        stratum_id: str = "K1",
    ) -> dict:
        """Teleport with incoming vel; no goto. Watch stall vs land for 2 s."""
        e = self.ent()
        try:
            self.request(f"stop {e}")
        except Exception:
            pass
        self.request(f"prep {e} 100 0")
        self._teleport(e, pos, list(vel))
        t0 = self.now()
        self._clear_events()
        samples: list[dict] = []
        events: list[dict] = []
        fall = FallTracker()
        t_arrive: float | None = None
        t_stall: float | None = None
        land_hit = False
        while self.now() - t0 < window_s:
            b = self.bot()
            t = self.now() - t0
            o = [float(x) for x in b["origin"]]
            on_ground = bool(b.get("on_ground"))
            samp = {"t": t, "x": o[0], "y": o[1], "z": o[2], "on_ground": on_ground}
            samples.append(samp)
            if fall.update(o[2], on_ground):
                events.append({
                    "ev": "peak_drop_150",
                    "t": t,
                    "rel_t": t,
                    "origin": o,
                    "z": o[2],
                    "cell": self.cell_at(o),
                })
            zone = (self.gate or {}).get("knockback_zone") or {}
            in_union = False
            if zone.get("cells") or zone.get("union"):
                u = zone.get("union") or {}
                try:
                    in_union = (
                        abs(o[0] - float(u.get("x", -288.0))) <= float(u.get("x_tol", 32.0))
                        and float(u.get("y_lo", -816.0)) <= o[1] <= float(u.get("y_hi", -592.0))
                        and abs(o[2] - float(u.get("z", -16.0))) <= float(u.get("z_tol", 24.0))
                    )
                except (TypeError, ValueError):
                    in_union = False
            elif (
                abs(o[0] - land[0]) <= 32.0
                and abs(o[1] - land[1]) <= 32.0
                and abs(o[2] - land[2]) <= 24.0
            ):
                in_union = True
            if in_union and on_ground:
                land_hit = True
                if t_arrive is None:
                    t_arrive = t
            for ev in self._drain_events(t, e):
                events.append(ev)
                if ev.get("ev") == "bot_stall" and t_stall is None:
                    t_stall = float(ev.get("rel_t", t))
                if ev.get("ev") == "arrived" and t_arrive is None:
                    t_arrive = float(ev.get("rel_t", t))
            if t_arrive is not None or t_stall is not None:
                break
            self.sleep(POLL_S)
        try:
            self.request(f"stop {e}")
        except Exception:
            pass
        return {
            "vel": list(vel),
            "commanded_vel": list(vel),
            "measured_vel": list(vel),
            "vel_tries": 1,
            "stamp_ok": True,
            "stamp_reason": "ok",
            "events": events,
            "samples": samples,
            "t_arrive": t_arrive,
            "t_stall_gate": t_stall,
            "land_hit": land_hit,
            "stratum_id": stratum_id,
            "arm": arm,
            "seq": seq,
        }

    def snapshot_astar(self, spec: dict, stratum_id: str) -> dict:
        """A* for THIS spec's start/goal on the current arm. Keyed (stratum, arm)."""
        start_cell = self.cell_at(spec["start"])
        goal_cell = self.cell_at(spec["goal"])
        empty = astar_path(found=False)
        key = (stratum_id, self.arm)
        if start_cell is None or goal_cell is None:
            blob = {"path": empty, "next_best": empty}
            self.astar_by[key] = blob
            return blob
        try:
            data = self.request(f"route query {start_cell} {goal_cell}")["data"]
            path = astar_from_route_resp(data)
        except Exception:
            path = empty
        next_best = empty
        if path.get("found") and path.get("links"):
            mask = ",".join(str(x) for x in path["links"])
            try:
                nb = self.request(
                    f"route query {start_cell} {goal_cell} mask {mask}"
                )["data"]
                next_best = astar_from_route_resp(nb, mask_links=list(path["links"]))
            except Exception:
                next_best = astar_path(found=False, mask_links=list(path["links"]))
        blob = {"path": path, "next_best": next_best}
        self.astar_by[key] = blob
        return blob

    def snapshot_all_strata(self) -> None:
        for sid, spec in STRATA.items():
            self.snapshot_astar(spec, sid)

    def measure_both_stamps(self) -> None:
        """Observe OFF and ON via apply/undo. Never copy expected into observed."""
        self.quiesce()
        self.confirm("off")
        self.snapshot_all_strata()
        self.apply()
        self.snapshot_all_strata()
        self.undo()

    def write_attempt_raw(self, path: Path, raw: dict, *, exclusive: bool = False) -> Path:
        """Per-attempt events/samples as JSONL. Pointer is this path."""
        return write_attempt_raw_file(path, raw, exclusive=exclusive)

    def write_attempt_kvitto(
        self,
        path: Path,
        *,
        attempt_id: str,
        stratum_id: str,
        raw_pointer: str,
        started_at: str,
        ended_at: str,
        lock_owner: str,
        lock_issued: str,
        gate_velocity: list[float] | None = None,
        gate_cell: int | None = None,
        gate_aim_hit: bool = False,
        astar_before: dict | None = None,
        astar_after: dict | None = None,
        astar_next_best: dict | None = None,
        demo_file: str | None = None,
        fixture_sha256: str | None = None,
        candidate: str | None = None,
        landing_cell: int | None = None,
        selected_link: int | None = None,
        knockback: dict | None = None,
        cvars: dict | None = None,
        exclusive: bool = False,
    ) -> dict:
        if "off" not in self.last_stamps:
            raise RuntimeError("OFF stamp not confirmed")
        if "on" not in self.last_stamps:
            raise RuntimeError(
                "ON stamp not confirmed — refusing to invent observed from expected"
            )
        off = dict(self.recipe["off"])
        on = on_expected(self.recipe)
        obs_off = self.last_stamps["off"]
        obs_on = self.last_stamps["on"]
        empty = astar_path(found=False)
        off_blob = self.astar_by.get((stratum_id, "off")) or {}
        on_blob = self.astar_by.get((stratum_id, "on")) or {}
        doc = make_kvitto(
            riglock_owner=lock_owner,
            riglock_issued_at=lock_issued,
            riglock_valid_from=lock_issued,
            riglock_valid_to=ended_at,
            riglock_path=str(self.lock_path),
            run_started_at=started_at,
            run_ended_at=ended_at,
            endpoint_host=self.host,
            endpoint_ctl_port=self.ctl_port,
            endpoint_game_port=self.game_port,
            map_name="dm3",
            binary_sha256=self.qwprogs_sha,
            commit=self.commit,
            stamps_off_expected=off,
            stamps_off_observed={k: obs_off[k] for k in off},
            stamps_on_expected=on,
            stamps_on_observed={k: obs_on[k] for k in on},
            stamps_undo_expected=off,
            stamps_undo_observed={k: obs_off[k] for k in off},
            recipe={
                "id": self.recipe["id"],
                "taxonomy_class": self.recipe["taxonomy_class"],
                "evidence": self.recipe["evidence"],
            },
            seed=0,
            stratum={"id": stratum_id, "attempt": attempt_id},
            raw_pointer=raw_pointer,
            astar_before=astar_before or off_blob.get("path") or empty,
            astar_after=astar_after or on_blob.get("path") or empty,
            astar_next_best=astar_next_best or on_blob.get("next_best") or off_blob.get("next_best") or empty,
            gate_velocity=gate_velocity,
            gate_cell=gate_cell,
            gate_aim_hit=gate_aim_hit,
            demo_file=self.demo_file if demo_file is None else demo_file,
            fixture_sha256=fixture_sha256,
            candidate=candidate,
            landing_cell=landing_cell,
            selected_link=selected_link,
            knockback=knockback,
            cvars=cvars if cvars is not None else recipe_cvars(self.recipe),
        )
        # Facit §1 "binärens SHA-256" = qwprogs.so (spellogik). mvdsv carried explicitly too.
        doc["binaries"] = {
            "qwprogs_sha256": self.qwprogs_sha,
            "mvdsv_sha256": self.mvdsv_sha,
        }
        write_kvitto(path, doc, exclusive=exclusive, verify_first=True)
        return doc
