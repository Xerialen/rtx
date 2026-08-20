#!/usr/bin/env python3
"""Harled bada stegen for hopp 1:s nordrecept — plantering + stangning.

Ankarna ar MATTA ur agarens egen nordkorsning (hexagonvarvet t=8,09):
  avfart  [422,6 157,9 56]  fart 435,2 u/s  kurs -34,05 grad   (74 u fore x=496)
  passage x=496 vid y=117,1  z=92,5        (mitt i den matta fria lanan 96..140)
  landning [699,6 153,4 56]

Ansatscell 1559 [352,192,56]: bearing -25,8 grad mot lappen, 78 u ansats — den
cell vars linje ligger narmast agarens egen kurs in mot lappen.
Landningscell 2085 [705,161,56]: 9,3 u fran agarens landningspunkt.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
FRAN_CELL, MAL_CELL, BORT = 1559, 2085, 34503

g = json.loads(DUMP.read_bytes())
cells, links, lids = g["cells"], g["links"], g["link_ids"]

lr0 = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in links]
h0 = kanon.niva2(cells, lr0)
if h0 != FACIT:
    print("STOPP: positiv kontroll misslyckades")
    raise SystemExit(2)
print("positiv kontroll: PASS")
s0 = kanon.stamp(g["map"], len(cells), len(links), 0)

lr1 = lr0 + [(FRAN_CELL, MAL_CELL, "speedjump", 1)]
h1 = kanon.niva2(cells, lr1)
s1 = kanon.stamp(g["map"], len(cells), len(links) + 1, 0)

kvar = [(l["from"], l["to_cell"], l["kind"], 1) for l, lid in zip(links, lids) if lid != BORT]
kvar.append((FRAN_CELL, MAL_CELL, "speedjump", 1))
h2 = kanon.niva2(cells, kvar)
s2 = kanon.stamp(g["map"], len(cells), len(kvar), 0)

print("bas    : %d/%d  %s" % (len(cells), len(links), h0))
print("steg 1 : %d/%d  %s" % (len(cells), len(links) + 1, h1))
print("steg 2 : %d/%d  %s" % (len(cells), len(kvar), h2))

Path("/home/xerial/hopptraning/harlett-hopp1-nord.json").write_text(json.dumps({
    "bas": {"cells": len(cells), "links": len(links), "rj_links": 0,
            "graph_stamp": s0, "graph_content_hash": h0},
    "efter_plantering": {"cells": len(cells), "links": len(links) + 1, "rj_links": 0,
                         "graph_stamp": s1, "graph_content_hash": h1},
    "efter_stangning": {"cells": len(cells), "links": len(kvar), "rj_links": 0,
                        "graph_stamp": s2, "graph_content_hash": h2},
}, indent=1))
print("\nskrev harlett-hopp1-nord.json")
