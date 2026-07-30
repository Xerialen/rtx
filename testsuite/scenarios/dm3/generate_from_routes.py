#!/usr/bin/env python3
"""Generate T1 drill scenarios from the owner's route manifest.

The manifest is produced from demos of the owner playing each route himself,
so the anchors are the coordinates a human actually used and the times are
what a human actually did. `min_acceptable_time_s` is the slowest run still
considered acceptable (the recorded time plus a margin) — that is what the
bot has to beat, not merely arriving.
"""
import json
import math
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
MANIFEST = HERE / "routes-v1.json"
OUT = HERE
# KTX's own dm3.loc, used to name each end of a route the way players do.
LOC_FILE = pathlib.Path(
    os.environ.get("DM3_LOC", HERE / "dm3.loc")
)

# The loc names KTX itself uses, so a place reads the way players say it.
LOC_WORDS = {
    "separator": " ", "pent": "pent", "ring": "ring", "ya": "YA", "box": "lådan",
    "quad": "quad", "ra": "RA", "rl": "RL", "mh": "mega", "gl": "GL", "lg": "LG",
    "sng": "SNG", "low": "låg", "high": "hög", "water": "vatten", "tele": "tele",
    "lift": "lift", "lifts": "lifts", "window": "window", "bridge": "bron",
    "tunnel": "tunneln", "stairs": "trappan", "entry": "ingången",
    "ledge": "hyllan", "hill": "kullen", "rox": "rox", "below": "under",
    "ssg": "SSG", "sg": "SG", "ng": "NG", "ga": "GA", "rj": "RJ",
}


def load_locs() -> list[tuple[float, float, float, str]]:
    points = []
    for line in LOC_FILE.read_text(errors="replace").splitlines():
        match = re.match(r"\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(.*)", line)
        if not match:
            continue
        raw = match.group(4)
        # Names are built from $loc_name_<word> tokens that run straight into
        # each other ("bridge$loc_name_separatorhigh"), so the longest known
        # word has to win or "separator" eats the word after it.
        known = sorted(LOC_WORDS, key=len, reverse=True)
        words: list[str] = []
        for index, part in enumerate(re.split(r"\$loc_name_", raw)):
            if index == 0:
                if part.strip():
                    words.append(part.strip())
                continue
            rest = part
            while rest:
                for token in known:
                    if rest.startswith(token):
                        words.append(LOC_WORDS[token])
                        rest = rest[len(token):]
                        break
                else:
                    words.append(rest.strip())
                    break
        name = " ".join(word for word in words if word.strip()).strip()
        name = re.sub(r"\s+", " ", name)
        points.append(
            (int(match.group(1)) / 8, int(match.group(2)) / 8, int(match.group(3)) / 8, name)
        )
    return points


# The .loc file spells some names in lowercase; players do not.
SHOUTED = {"rl": "RL", "lg": "LG", "gl": "GL", "sng": "SNG", "ssg": "SSG",
           "sg": "SG", "ng": "NG", "ya": "YA", "ra": "RA", "mh": "mega",
           "ga": "GA"}


def place_of(points, position) -> str:
    best = min(points, key=lambda point: math.dist(point[:3], position))
    name = best[3] or "okänd plats"
    return " ".join(SHOUTED.get(word.lower(), word) for word in name.split())


# The manifest says what the owner did on the route. It says nothing about the
# conditions the drill has to reproduce to be asking the same question, and
# those cannot live in the generated files: regenerating rewrites them whole,
# which had already quietly wiped a loadout once.
#
# Extra `[run]` keys per drill. Rockets are the permission to rocket-jump — a
# drill handed none is a drill where the jump is not sanctioned, which is every
# route on dm3 except the pent jump.
ROUTE_RUN: dict[str, dict[str, object]] = {
    "rj_pent_to_lifts_to_window_to_quad": {"prep_rockets": 100},
    # Its passing times sit in two bands, 7.50-7.91 and 8.09-8.19 against a
    # limit of 8.42, and it abandons intermittently — 2-3 of 15 in both arms
    # of the planted-links A/B. Three attempts with a required of two turns
    # that variance into a verdict that flips between runs; this is the drill
    # the quick cut is not allowed to shortchange.
    "ring_to_ratop": {"quick_attempts": 5},
}

# A capability the route needs that the build has to have been given, and the
# cvar that witnesses it. Without it the route is not in the graph at all, so
# the drill is withheld rather than graded — grading it would report the map's
# absence as the bot's failure.
ROUTE_REQUIRES: dict[str, dict[str, str]] = {
    "rj_pent_to_lifts_to_window_to_quad": {
        "capability": "navpatch:dm3-pentlift-rj",
        "engine_cvar": "rtx_rj_cost_scale",
        "note": "rutten går genom en raketskuttlänk som navpatchen planterar i"
                " pent-hissen; utan patchen finns länken inte i grafen och"
                " drillen mäter kartan i stället för botten",
    },
}

# Ordered waypoints an attempt must pass through before an arrival on that
# route counts — the fix for a drill that only ever checked where the bot
# ended up. Anchored on the owner's own 18 route demos (lanister:~/dm3-drillar,
# read with his qwd_v2 extractor): every point below is a position he actually
# occupied, in the order he occupied them, picked at the places his path
# genuinely departs from a straight line between the drill's start and target
# — never a point derived from the navmesh or from geometry.
#
# `box` is one half-width shared by every waypoint on a route rather than a
# value per waypoint (the schema allows either): it is set to ~70% of the
# tightest gap between any of that route's waypoints and a 200-point
# straight-line interpolation from start to target, rounded down to the
# nearest step of 8. That is deliberately short of the boundary where the
# straight line would start clipping a box by accident — the margin is there
# so the gate's behaviour does not hinge on the interpolation's resolution —
# while every route still keeps enough room for a bot that is not replaying
# the owner's exact path pixel for pixel.
#
# This table covers the routes the manifest describes. sng_mega is gated too,
# but in its own hand-written file rather than here: it is a *leg* of a longer
# route the owner recorded, so its waypoints come from the segment of that run
# between the drill's own start and target. The two cell
# probes carry no waypoints at all, and that is deliberate — a cell probe is
# the pair of cells, so there is no path to assert a different version of.
# Half-width of every waypoint cube, in units. One value for all routes: the
# per-route numbers this started with were the smallest that still failed a
# straight line from start to target, and that measure turned out to be nearly
# free — the owner's waypoints sit so far off the straight line that anything up
# to 256 passes it. What the number actually has to do is sit between two real
# failures. Too small and it asserts the exact line he ran: at 32 the gate
# called a bot that missed by 47 units off-route, which is not a shortcut, it is
# movement. Too large and it stops meaning "passed through here": past 128 a
# straight line starts clearing a route. Between 80 and 112 the three measures
# that matter hold steady — the owner passes all eighteen routes, a straight
# line fails all eighteen, and the same ten of the bot's nineteen arrivals are
# caught — so this sits in the middle of that band. It is also comfortably above
# the sixty units a bot at full speed covers between two polls, which is the
# floor below which a gate can simply be stepped over.
VIA_BOX = 96

# A via entry is normally a bare point sharing the route's box. An entry may
# instead be a ([x, y, z], box) pair when one waypoint has measured grounds
# for its own width — the reason belongs in a comment beside that entry, and
# the width still has to reject the straight line for its own route.

ROUTE_VIA: dict[str, dict[str, object]] = {
    "hex_quad_to_sng": {
        "box": VIA_BOX,
        "via": [
            [528.9, 115.6, 94.9],
            [346.6, 519.5, 98.5],
            [-90.9, 779.1, 120.0],
        ],
    },
    "hex_ratop_to_ssg": {
        "box": VIA_BOX,
        "via": [
            [418.6, -553.9, 56.0],
            [494.2, -192.5, 89.4],
            [1369.5, -944.0, 123.0],
        ],
    },
    "hex_sng_to_quad": {
        "box": VIA_BOX,
        "via": [
            [-356.8, 275.0, 140.9],
            [384.5, 633.4, 96.1],
            [527.2, 109.0, 91.0],
        ],
    },
    "hex_ssg_to_ratop": {
        "box": VIA_BOX,
        "via": [
            [1655.2, -909.0, 40.0],
            [527.1, -163.6, 99.5],
            [305.5, -536.5, 72.0],
            [29.5, -841.6, 152.0],
        ],
    },
    "spawn_lift_to_pent_to_pentmega": {
        "box": VIA_BOX,
        "via": [
            [601.9, 1049.0, 40.0],
            [1863.2, 672.8, -143.1],
        ],
    },
    "spawn_ra_tunnel_to_lg": {
        "box": VIA_BOX,
        "via": [
            [786.4, 172.2, -123.9],
            [1109.8, -53.9, -138.2],
        ],
    },
    "spawn_rarox_to_quad": {
        "box": VIA_BOX,
        "via": [
            [-612.9, -457.5, 27.5],
            [423.8, -225.4, 56.0],
            [847.1, -166.2, 99.0],
        ],
    },
    "spawn_rl_to_ratop_xer": {
        "box": VIA_BOX,
        "via": [
            # The bot corner-cuts this one at 97.4-103.7 on the worst axis in
            # all six recorded attempts while clearing the three waypoints
            # after it by 13-26 — and the ordered chain means a first-waypoint
            # miss silences the rest. 128 covers the corner-cut; the straight
            # line passes 311 away, so the gate keeps a 2.4x margin, and the
            # owner goes through at 0.1.
            ([1572.9, -82.4, -158.2], 128),
            [202.2, -99.2, -176.0],
            [-196.9, -222.5, -166.2],
            [-35.0, -725.5, 7.2],
        ],
    },
    "spawn_sngspawn_to_ring_to_ratop": {
        "box": VIA_BOX,
        "via": [
            [363.0, 57.4, 74.2],
            [109.6, -879.5, 184.0],
        ],
    },
    "highbridge_to_rl": {
        "box": VIA_BOX,
        "via": [
            [1316.0, -2.5, -24.0],
            [1538.2, 202.1, 19.0],
        ],
    },
    "lg_to_pent_to_pentmega": {
        "box": VIA_BOX,
        "via": [
            [1296.8, 719.2, -360.0],
            [972.2, 1002.0, -261.0],
        ],
    },
    "lifts_or_ring_to_sngmega": {
        "box": VIA_BOX,
        "via": [
            [483.8, 597.8, 56.0],
            [-180.4, 768.0, 132.5],
            [-459.0, 499.4, 120.0],
            [-856.5, 265.5, 207.2],
        ],
    },
    "ralow_to_ratop": {
        "box": VIA_BOX,
        "via": [
            [495.5, -813.9, 56.0],
            [31.6, -879.6, 152.0],
            [285.5, -879.9, 264.0],
            [46.0, -603.8, 341.6],
        ],
    },
    "ring_to_ratop": {
        "box": VIA_BOX,
        "via": [
            [72.2, -573.2, 152.0],
            [31.9, -877.6, 152.0],
            [303.0, -870.6, 264.0],
            [48.9, -563.2, 321.8],
        ],
    },
    "ring_to_rl": {
        "box": VIA_BOX,
        "via": [
            [702.5, 460.6, 74.2],
            [1459.1, 55.5, -8.5],
            [1610.4, 352.0, -31.1],
        ],
    },
    "rj_pent_to_lifts_to_window_to_quad": {
        "box": VIA_BOX,
        "via": [
            [610.4, 874.9, -55.0],
            [1184.4, 667.6, 96.1],
            [641.5, 364.8, 97.0],
        ],
    },
    "sngspawns_to_sngmega": {
        "box": VIA_BOX,
        "via": [
            [-942.0, 301.9, 104.0],
            [-646.4, 880.0, 120.0],
            [-473.5, 272.0, 149.6],
        ],
    },
    "window_to_rl": {
        "box": VIA_BOX,
        "via": [
            [1243.1, 393.8, 56.0],
            [1458.5, 56.5, -17.4],
        ],
    },
}


def toml_value(value: object) -> str:
    if isinstance(value, str):
        return '"' + value.replace('"', '\\"') + '"'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def scenario_name(route: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", route.lower()).strip("_")


def main() -> int:
    points = load_locs()
    manifest = json.loads(MANIFEST.read_text())
    written = []
    for demo in manifest["demos"]:
        fastest = demo.get("fastest")
        if not fastest:
            print(f"hoppar över {demo['route']}: ingen mätt körning", file=sys.stderr)
            continue
        name = scenario_name(demo["route"])
        start = [round(value, 1) for value in fastest["start_pos"]]
        target = [round(value, 1) for value in fastest["end_pos"]]
        reference = round(float(fastest["travel_time_s"]), 2)
        limit = round(float(fastest["min_acceptable_time_s"]), 2)
        # Room for a bot that is slow but still arriving: that must read as
        # "too slow", not as "never got there".
        timeout = max(12, math.ceil(limit * 3 + 4))
        target_info = demo.get("target") or {}
        goal = target_info.get("item")
        peak = demo["runs"][0].get("peak_speed")
        description = (
            f"{demo['route']}: anchored on the owner's own run of the route "
            f"({reference:.2f} s, peak {peak:.0f} u/s"
            + (f", ending at {goal}" if goal else "")
            + "). The bot has to arrive within "
            f"{limit:.2f} s to pass."
        )
        requires = ROUTE_REQUIRES.get(name)
        requires_block = ""
        if requires:
            requires_block = "\n[requires]\n" + "".join(
                f"{key} = {toml_value(value)}\n" for key, value in requires.items()
            )
        extra = "".join(
            f"{key} = {toml_value(value)}\n"
            for key, value in ROUTE_RUN.get(name, {}).items()
        )
        via_data = ROUTE_VIA.get(name)
        route_block = ""
        if via_data:
            # A waypoint's name comes from the same loc lookup the start/target
            # place string uses, so it reads the way the owner would say it.
            # Two waypoints landing on the same nearest loc (a multi-stage
            # climb up the same structure, say) still each need a non-empty,
            # distinct-enough name, so a repeat gets numbered.
            seen: dict[str, int] = {}
            entries = []
            for entry in via_data["via"]:
                at, box = (
                    entry if isinstance(entry, tuple) else (entry, via_data["box"])
                )
                label = place_of(points, tuple(at))
                seen[label] = seen.get(label, 0) + 1
                if seen[label] > 1:
                    label = f"{label} {seen[label]}"
                coords = [round(value, 1) for value in at]
                entries.append(
                    f'  {{ at = {coords}, box = {box}, name = "{label}" }},'
                )
            route_block = (
                "\n[route]\n"
                "# Ordered waypoints the attempt must pass through, in this order,\n"
                "# before an arrival counts. Anchored on the owner's own run of the\n"
                "# route: points he actually occupied, in the order he occupied\n"
                "# them, at the places his path genuinely departs from a straight\n"
                "# line to the target — never points derived from the navmesh or\n"
                "# from geometry.\n"
                "via = [\n" + "\n".join(entries) + "\n]\n"
            )
        tail = extra + route_block
        text = f"""schema = "rtx-scenario/1"
name = "{name}"
map = "dm3"
kind = "goto"
category = "grunddrill"
place = "{place_of(points, start)} → {place_of(points, target)}"
description = "{description}"
{requires_block}
[run]
# Anchors and reference time come from {demo['demo']} via the owner's route
# manifest (see scenarios/dm3/routes-v1.json).
start = {start}
target = {target}
attempts = 5
timeout_s = {timeout}
pause_s = 1.0
arrive_box = 70
regoto_max = 6
{tail}
[threshold]
required = 4
reference_time_s = {reference}
max_time_s = {limit}
"""
        (OUT / f"{name}.toml").write_text(text)
        written.append(name)
    print(f"skrev {len(written)} drillar:", ", ".join(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
