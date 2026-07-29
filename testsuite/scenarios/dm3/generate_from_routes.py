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
{extra}
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
