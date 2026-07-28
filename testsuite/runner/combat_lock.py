"""Combat lock from a qw-analyze full-view JSON.

Definition (per player B, 100 ms grid), ported from
qw-fasttrack/scripts/combat_lock.py:

1. UNDER FIRE: at least one damage event with victim=B and an enemy attacker
   within the trailing WINDOW_MS.
2. IN VIEW: the angle between B's view yaw and the bearing B->attacker is
   within POV_DEG/2 (positions and yaw interpolated from the streams).
3. NOT ANSWERING: B has no shot of their own in [t - WINDOW_MS, t + GRACE_MS].

All three together make the tick count as combat lock.

The original script expected a dedicated shots stream, which qw-analyze v21
does not emit. The fire signal here is instead an ammo-count decrease in any
of the shells/nails/rockets/cells streams — every QuakeWorld weapon except the
axe consumes ammo when fired. Respawn resets are filtered out by their
signature: several ammo streams changing at the same instant.
"""
from __future__ import annotations

import bisect
import math
from typing import Any

WINDOW_MS = 1200
GRACE_MS = 300
POV_DEG = 120.0
TICK_MS = 100
MAX_FIRE_DROP = 20  # a bigger single-stream drop is a reset, not a shot
AMMO_STREAMS = ("sh", "nl", "rk", "cl")


def _changepoints(stream: Any) -> list[tuple[int, int]]:
    if not isinstance(stream, list):
        return []
    points = []
    for entry in stream:
        if isinstance(entry, dict) and "t" in entry and "v" in entry:
            points.append((int(entry["t"]), int(entry["v"])))
    return points


def _fire_times(player: dict[str, Any]) -> list[int]:
    """Timestamps where an ammo count decreased — the observable shot signal."""
    streams = {key: _changepoints(player.get(key)) for key in AMMO_STREAMS}
    change_stamps: dict[int, int] = {}
    for points in streams.values():
        for stamp, _ in points[1:]:
            change_stamps[stamp] = change_stamps.get(stamp, 0) + 1
    fires: list[int] = []
    for points in streams.values():
        for (_, before), (stamp, after) in zip(points, points[1:]):
            if after >= before:
                continue
            if before - after > MAX_FIRE_DROP:
                continue
            if change_stamps.get(stamp, 0) >= 2:
                continue  # simultaneous multi-stream change = death/respawn
            fires.append(stamp)
    fires.sort()
    return fires


def _positions(player: dict[str, Any]) -> dict[str, list[float]] | None:
    pos = player.get("pos")
    if not isinstance(pos, dict):
        return None
    needed = ("t", "x", "y", "vya")
    if not all(isinstance(pos.get(key), list) and pos[key] for key in needed):
        return None
    return pos


def _sample(pos: dict[str, list[float]], t_ms: float) -> tuple[float, float, float] | None:
    stamps = pos["t"]
    index = bisect.bisect_right(stamps, t_ms)
    if index <= 0 or index >= len(stamps):
        return None
    t0, t1 = stamps[index - 1], stamps[index]
    k = 0.0 if t1 == t0 else (t_ms - t0) / (t1 - t0)
    x = pos["x"][index - 1] + (pos["x"][index] - pos["x"][index - 1]) * k
    y = pos["y"][index - 1] + (pos["y"][index] - pos["y"][index - 1]) * k
    # yaw is not interpolated across the wrap; take the nearer sample
    yaw = pos["vya"][index - 1] if k < 0.5 else pos["vya"][index]
    return x, y, yaw


def per_player_lock(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Compute {player: {team, lock_s, under_fire_s}} from a full-view JSON."""
    players: dict[str, dict[str, Any]] = {}
    for raw in document.get("streams", {}).get("players", []):
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        pos = _positions(raw)
        if not isinstance(name, str) or pos is None:
            continue
        base = name.split("#", 1)[0]
        players[base] = {
            "team": str(raw.get("team", "")),
            "pos": pos,
            "fires": _fire_times(raw),
        }

    hits: dict[str, list[tuple[int, str]]] = {}
    for event in document.get("damage", {}).get("events", []):
        if not isinstance(event, dict) or event.get("isEnv") or event.get("isSelf"):
            continue
        attacker, victim = event.get("attacker"), event.get("victim")
        if (
            attacker in players
            and victim in players
            and attacker != victim
            and players[attacker]["team"] != players[victim]["team"]
        ):
            hits.setdefault(victim, []).append((int(event["time"]), attacker))
    for victim in hits:
        hits[victim].sort()

    t_end = max(
        (player["pos"]["t"][-1] for player in players.values()), default=0
    )
    result: dict[str, dict[str, Any]] = {}
    for name, player in players.items():
        my_hits = hits.get(name, [])
        hit_times = [stamp for stamp, _ in my_hits]
        my_fires = player["fires"]
        lock_ms = 0
        fire_ms = 0
        for t in range(0, int(t_end), TICK_MS):
            index = bisect.bisect_right(hit_times, t)
            if index == 0 or t - hit_times[index - 1] > WINDOW_MS:
                continue
            fire_ms += TICK_MS
            attacker = my_hits[index - 1][1]
            me = _sample(player["pos"], t)
            enemy = _sample(players[attacker]["pos"], t)
            if me is None or enemy is None:
                continue
            bearing = math.degrees(math.atan2(enemy[1] - me[1], enemy[0] - me[0]))
            delta = (bearing - me[2] + 180.0) % 360.0 - 180.0
            if abs(delta) > POV_DEG / 2:
                continue
            j = bisect.bisect_left(my_fires, t - WINDOW_MS)
            answered = j < len(my_fires) and my_fires[j] <= t + GRACE_MS
            if not answered:
                lock_ms += TICK_MS
        result[name] = {
            "team": player["team"],
            "lock_s": round(lock_ms / 1000.0, 1),
            "under_fire_s": round(fire_ms / 1000.0, 1),
        }
    return result


def per_team_s_per_bot(document: dict[str, Any]) -> dict[str, float]:
    """Aggregate lock seconds per team, divided by that team's player count."""
    by_team: dict[str, list[float]] = {}
    for values in per_player_lock(document).values():
        by_team.setdefault(values["team"], []).append(values["lock_s"])
    return {
        team: round(sum(locks) / len(locks), 1)
        for team, locks in by_team.items()
        if locks
    }
