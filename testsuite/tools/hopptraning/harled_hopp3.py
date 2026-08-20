#!/usr/bin/env python3
"""Harled bada stegen for hopp 3:s recept — plantering + stangning.

Steg 1 planterar en curl-korsning (cell 1664 -> cell 2083). En plantering ror
bara sin egen lank: `plant_speed_jump` push_link:ar den och lamnar ovriga
adjacenser i fred, sa de 15 prunade forblir prunade.

Steg 2 tar bort 34503. Da galler motorns pinnade remove-semantik: adjacensen
byggs om och ALLA kvarvarande lankar blir T=1.

Positiv kontroll forst.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
FRAN_CELL, MAL_CELL = 1664, 2083
BORT = 34503

g = json.loads(DUMP.read_bytes())
cells, links, lids = g["cells"], g["links"], g["link_ids"]

lr0 = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in links]
h0 = kanon.niva2(cells, lr0)
print("positiv kontroll:", "PASS" if h0 == FACIT else "STOPP")
if h0 != FACIT:
    raise SystemExit(2)

s0 = kanon.stamp(g["map"], len(cells), len(links), 0)
print("bas    : %d/%d  stamp %s  nivå-2 %s" % (len(cells), len(links), s0, h0))

# Steg 1: plantering. Ny lank cell 1664 -> cell 2083, speedjump, i adjacensen.
lr1 = lr0 + [(FRAN_CELL, MAL_CELL, "speedjump", 1)]
h1 = kanon.niva2(cells, lr1)
s1 = kanon.stamp(g["map"], len(cells), len(links) + 1, 0)
print("steg 1 : %d/%d  stamp %s  nivå-2 %s" % (len(cells), len(links) + 1, s1, h1))

# Steg 2: ta bort 34503. Alla kvarvarande blir T=1 (pinnad semantik).
kvar = [(l["from"], l["to_cell"], l["kind"], 1) for l, lid in zip(links, lids) if lid != BORT]
kvar.append((FRAN_CELL, MAL_CELL, "speedjump", 1))
h2 = kanon.niva2(cells, kvar)
s2 = kanon.stamp(g["map"], len(cells), len(kvar), 0)
print("steg 2 : %d/%d  stamp %s  nivå-2 %s" % (len(cells), len(kvar), s2, h2))

ut = {
    "bas": {"cells": len(cells), "links": len(links), "rj_links": 0,
            "graph_stamp": s0, "graph_content_hash": h0},
    "efter_plantering": {"cells": len(cells), "links": len(links) + 1, "rj_links": 0,
                         "graph_stamp": s1, "graph_content_hash": h1},
    "efter_stangning": {"cells": len(cells), "links": len(kvar), "rj_links": 0,
                        "graph_stamp": s2, "graph_content_hash": h2},
}
Path("/home/xerial/hopptraning/harlett-hopp3.json").write_text(json.dumps(ut, indent=1))
print("\nskrev harlett-hopp3.json")
