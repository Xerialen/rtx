#!/usr/bin/env python3
"""Facit §2 strata + §3 trap/fall predicates. Pure — no rig."""

from __future__ import annotations

import math
from typing import Any, Iterable

# trap_repro default target (T0 goal).
T0_GOAL = [-864.0, -96.0, -16.0]

STRATA: dict[str, dict[str, Any]] = {
    "T0": {
        "kind": "trap",
        "start": [-865.0, -48.0, 90.0],
        "goal": list(T0_GOAL),
        "n_off": 4,
        "n_on": 8,
        "budget_s": 1.10,
        "off_window_s": 40.0,
        "rest_speed_max": 1.0,
        "population": "teleport_drill",
    },
    "A1": {
        "kind": "heldout",
        "start": [256.0, -704.0, 328.0],
        "goal": [-593.0, -677.0, -16.0],
        "n_off": 5,
        "n_on": 5,
        "vh_lo": 80.0,
        "vh_hi": 160.0,
        "vh_hi_inclusive": False,
        "dir_min": 0.8,
        "budget_s": 25.0,
        "population": "kedjad",
    },
    "A2": {
        "kind": "heldout",
        "start": [256.0, -704.0, 328.0],
        "goal": [-593.0, -677.0, -16.0],
        "n_off": 5,
        "n_on": 5,
        "vh_lo": 160.0,
        "vh_hi": 260.0,
        "vh_hi_inclusive": True,
        "dir_min": 0.8,
        "budget_s": 25.0,
        "population": "kedjad",
    },
    "A3": {
        "kind": "heldout",
        "start": [-593.0, -677.0, -16.0],
        "goal": [256.0, -704.0, 328.0],
        "n_off": 5,
        "n_on": 5,
        "vh_lo": 80.0,
        "vh_hi": 160.0,
        "vh_hi_inclusive": False,
        "dir_min": 0.8,
        "budget_s": 25.0,
        "population": "kedjad",
    },
    "A4": {
        "kind": "heldout",
        "start": [-593.0, -677.0, -16.0],
        "goal": [256.0, -704.0, 328.0],
        "n_off": 5,
        "n_on": 5,
        "vh_lo": 160.0,
        "vh_hi": 260.0,
        "vh_hi_inclusive": True,
        "dir_min": 0.8,
        "budget_s": 25.0,
        "population": "kedjad",
    },
}

HELDOUT_IDS = ("A1", "A2", "A3", "A4")
FORBIDDEN_CTL = {27990, 27993}
FORBIDDEN_GAME = {27540, 27570}
PEAK_DROP = 150.0
# r2/r3 §2: ON/OFF pair start-vel within ±5 u/s per component. Unchanged in r3.
PAIR_VEL_TOL = 5.0
# r3 §2: an out-of-box pair is not counted; replace it as a new full pair.
# 5 needed + this many extra pair attempts per heldout stratum.
HELDOUT_PAIR_REPLACE_CAP = 5
AVSETT_DROP_STRATA = frozenset({"A1", "A2"})
# Terra facit revision (ongoing): (i) "gate" = |vh| at west-shelf-gate;
# (ii) "start" = |vh| at teleport start. Missing key is fail-closed.
STRATUM_AT_GATE = "gate"
STRATUM_AT_START = "start"
STRATUM_AT_VALUES = {STRATUM_AT_GATE, STRATUM_AT_START}


def vh(vel: Iterable[float]) -> float:
    vx, vy, *_ = list(vel) + [0.0, 0.0]
    return math.hypot(float(vx), float(vy))


def speed(vel: Iterable[float]) -> float:
    v = list(vel)
    while len(v) < 3:
        v.append(0.0)
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def direction_dot(vel: Iterable[float], start: Iterable[float], goal: Iterable[float]) -> float:
    s, g = list(start), list(goal)
    hx, hy = g[0] - s[0], g[1] - s[1]
    hn = math.hypot(hx, hy)
    vn = vh(vel)
    if hn == 0.0 or vn == 0.0:
        return 0.0
    vx, vy, *_ = list(vel) + [0.0]
    return (vx * hx + vy * hy) / (vn * hn)


def stratum_ok(stratum_id: str, vel: Iterable[float], start: Iterable[float], goal: Iterable[float]) -> tuple[bool, str]:
    spec = STRATA[stratum_id]
    if spec["kind"] == "trap":
        if speed(vel) <= spec["rest_speed_max"]:
            return True, "rest"
        return False, f"T0 rest speed {speed(vel):.3f} > {spec['rest_speed_max']}"
    lo, hi = spec["vh_lo"], spec["vh_hi"]
    band = vh(vel)
    if spec["vh_hi_inclusive"]:
        in_band = lo <= band <= hi
        band_s = f"{lo} <= |vh| <= {hi}"
    else:
        in_band = lo <= band < hi
        band_s = f"{lo} <= |vh| < {hi}"
    if not in_band:
        return False, f"|vh|={band:.2f} outside {band_s}"
    dot = direction_dot(vel, start, goal)
    if dot < spec["dir_min"]:
        return False, f"direction dot {dot:.3f} < {spec['dir_min']}"
    return True, "ok"


def heldout_stratum_at(gates: dict) -> tuple[str | None, str | None]:
    """Return (locus, error). error is set when the gate file omits i|ii."""
    v = gates.get("heldout_stratum_at")
    if v is None:
        ws = (gates.get("gates") or {}).get("west-shelf")
        if isinstance(ws, dict):
            v = ws.get("heldout_stratum_at")
    if v is None:
        return None, "heldout_stratum_at missing from gate file (facit i=gate|ii=start)"
    if v not in STRATUM_AT_VALUES:
        return None, f"heldout_stratum_at {v!r} not in {{gate, start}}"
    return str(v), None


def gate_passage(
    gate: dict, *, gate_cell: int | None = None, origin: Iterable[float] | None = None
) -> tuple[bool, str]:
    """True if the bot hit a registered cell_id or a documented aim-point."""
    cells = gate.get("cell_ids") or []
    if isinstance(gate_cell, int) and gate_cell in cells:
        return True, "cell"
    if origin is not None and in_gate(gate, origin=origin, cell_id=None):
        return True, "aim"
    return False, "no gate_cell/aim hit at the gate"


def in_gate(gate: dict, *, origin: Iterable[float] | None = None, cell_id: int | None = None) -> bool:
    cells = gate.get("cell_ids") or []
    if cell_id is not None and cell_id in cells:
        return True
    if origin is None:
        return False
    o = list(origin)
    tol_xy = float(gate.get("tolerance_xy", 24.0))
    tol_z = float(gate.get("tolerance_z", 16.0))
    for aim in gate.get("aim_points") or []:
        if math.hypot(o[0] - aim[0], o[1] - aim[1]) <= tol_xy and abs(o[2] - aim[2]) <= tol_z:
            return True
    return False


def is_trap(events: list[dict], gate: dict, *, arrived_after_stall: bool) -> bool:
    """Facit §3: bot_stall in the registered west-shelf gate before arrived.

    arrived after a prior stall is still a trap.
    """
    stalled = False
    for ev in events:
        if ev.get("ev") != "bot_stall":
            continue
        cell = ev.get("cell")
        origin = ev.get("origin")
        if in_gate(gate, origin=origin, cell_id=cell if isinstance(cell, int) else None):
            stalled = True
            break
    return stalled


class FallTracker:
    """Harness peak_drop_150: Δz > 150 from running peak without ground."""

    def __init__(self) -> None:
        self.peak: float | None = None

    def update(self, z: float, on_ground: bool) -> bool:
        if on_ground:
            self.peak = z
            return False
        if self.peak is None:
            self.peak = z
            return False
        if z > self.peak:
            self.peak = z
        return (self.peak - z) > PEAK_DROP


def in_avsett_geometry(
    geom: dict | None,
    *,
    origin: Iterable[float] | None = None,
    cell_id: int | None = None,
) -> bool:
    """True if origin/cell sits in the pinned A1/A2 west-out corridor."""
    if not isinstance(geom, dict):
        return False
    cells: set[int] = set()
    for key in ("drop_cells", "landing_cells", "path_cells"):
        for c in geom.get(key) or []:
            if isinstance(c, int):
                cells.add(c)
    if isinstance(cell_id, int) and cell_id in cells:
        return True
    if origin is None:
        return False
    o = list(origin)
    if len(o) < 3:
        return False
    corridor = geom.get("corridor") or {}
    try:
        if (
            float(corridor["xmin"]) <= float(o[0]) <= float(corridor["xmax"])
            and float(corridor["ymin"]) <= float(o[1]) <= float(corridor["ymax"])
            and float(corridor["zmin"]) <= float(o[2]) <= float(corridor["zmax"])
        ):
            return True
    except (KeyError, TypeError, ValueError):
        return False
    return False


def pair_start_vel_ok(
    off_vel: Iterable[float] | None,
    on_vel: Iterable[float] | None,
    *,
    tol: float = PAIR_VEL_TOL,
) -> tuple[bool, str]:
    """r2 pair rule: same start profile, ±tol u/s per component."""
    if off_vel is None or on_vel is None:
        return False, "pair missing start velocity"
    a = list(off_vel) + [0.0, 0.0, 0.0]
    b = list(on_vel) + [0.0, 0.0, 0.0]
    for i, axis in enumerate("xyz"):
        delta = abs(float(a[i]) - float(b[i]))
        if delta > tol:
            return False, f"pair v{axis} delta {delta:.2f} > {tol} u/s"
    return True, "ok"


def profiles_ok(off: dict[str, str], on: dict[str, str]) -> str | None:
    """Only rtx_nav_patch may differ between arms."""
    keys = set(off) | set(on)
    for k in keys:
        if k == "rtx_nav_patch":
            continue
        if off.get(k) != on.get(k):
            return f"cvar {k} differs between arms ({off.get(k)!r} vs {on.get(k)!r})"
    return None
