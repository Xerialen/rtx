#!/usr/bin/env python3
"""Harled grafidentiteten EFTER teleportfixen — fore bygget, ur dumpen.

Fixen ror bara adjacensmedlemskapet for teleportlankar. Cellantal, lankantal och
lank-id ar oforandrade, sa nivå-1 ar oforandrad. Nivå-2 andras genom att T pa
teleportlanken gar 0 -> 1. Det ar exakt vad nivå-2 finns for.

Positiv kontroll forst: raknaren maste reproducera dagens nivå-2 ur dumpen.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"

g = json.loads(DUMP.read_bytes())
cells, links, lids = g["cells"], g["links"], g["link_ids"]

lr_nu = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in links]
h_nu = kanon.niva2(cells, lr_nu)
print("positiv kontroll: raknad", h_nu)
print("                  motorns", FACIT)
if h_nu != FACIT:
    print("STOPP: raknaren reproducerar inte motorn.")
    raise SystemExit(2)
print("PASS")

tp = [(i, lids[i], l) for i, l in enumerate(links) if l["kind"] == "teleport"]
print("\nteleportlankar:", [(lid, l["T"]) for _, lid, l in tp])

lr_ny = [(l["from"], l["to_cell"], l["kind"], 1 if l["kind"] == "teleport" else l["T"])
         for l in links]
h_ny = kanon.niva2(cells, lr_ny)
s = kanon.stamp(g["map"], len(cells), len(links), 0)
n_pr_nu = sum(1 for l in links if l["T"] == 0)
n_pr_ny = sum(1 for l in lr_ny if l[3] == 0)

print("\nHARLETT EFTER FIXEN (fore bygget):")
print("  celler %d · lankar %d · rj 0   (oforandrat)" % (len(cells), len(links)))
print("  nivå-1 %s   (OFORANDRAD — antalen ror sig inte)" % s)
print("  nivå-2 %s" % h_ny)
print("  prunade: %d -> %d" % (n_pr_nu, n_pr_ny))

Path("/home/xerial/hopptraning/harlett-teleportfix.json").write_text(json.dumps({
    "fore": {"cells": len(cells), "links": len(links), "rj_links": 0,
             "graph_stamp": s, "graph_content_hash": h_nu, "prunade": n_pr_nu},
    "efter": {"cells": len(cells), "links": len(links), "rj_links": 0,
              "graph_stamp": s, "graph_content_hash": h_ny, "prunade": n_pr_ny},
    "andring": "T 0->1 for teleportlankar (dm3: id 36314, cell 4633 -> 1330)",
}, indent=1))
print("\nskrev harlett-teleportfix.json")
