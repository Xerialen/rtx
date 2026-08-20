#!/usr/bin/env python3
"""Applicera en F-variant: lista lankar att ta bort som argv[1:]."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
namn = sys.argv[1]
BORT = [int(x) for x in sys.argv[2:]]

lab = hoppa.Lab()
TOK = open("/home/xerial/hopptraning/.rig-lock").read().strip().splitlines()[0]
for _ in range(4):
    k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
    if k.get("outcome") == "empty":
        break
    lab.request({"Fixa": {"recipe": k.get("recipe") or "komponat", "mode": "undo", "lock_token": TOK}}, timeout=60)

g = json.loads(DUMP.read_bytes())
cells, links, lids = g["cells"], g["links"], g["link_ids"]
lr0 = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in links]
if kanon.niva2(cells, lr0) != FACIT:
    raise SystemExit("STOPP: positiv kontroll misslyckades")
print("positiv kontroll: PASS")

if not BORT:
    print("inget att ta bort — riggen star pa bas")
    raise SystemExit(0)

ank = []
for lid in BORT:
    i = lids.index(lid)
    ank.append({"id": lid, "from": links[i]["from"], "to": links[i]["to_cell"], "kind": links[i]["kind"]})
    print("  tar bort id %-6d %-10s cell %d -> %d" % (lid, links[i]["kind"], links[i]["from"], links[i]["to_cell"]))

s0 = kanon.stamp(g["map"], len(cells), len(links), 0)
bs = set(BORT)
kvar = [(l["from"], l["to_cell"], l["kind"], 1) for l, lid in zip(links, lids) if lid not in bs]
h1 = kanon.niva2(cells, kvar)
s1 = kanon.stamp(g["map"], len(cells), len(kvar), 0)
bas = {"cells": len(cells), "links": len(links), "rj_links": 0, "graph_stamp": s0, "graph_content_hash": FACIT}
e1 = {"cells": len(cells), "links": len(kvar), "rj_links": 0, "graph_stamp": s1, "graph_content_hash": h1}
r = lab.request({"Komponat": {"recept_id": namn, "base": bas,
                              "steps": [{"name": namn, "op": {"RemoveLinks": {"links": ank}},
                                         "expect_before": bas, "expect_after": e1}],
                              "expect_final": e1, "lock_token": TOK}}, timeout=60)
ef = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("%s: %s | harlett %d/%s | motorn %d/%s | MATCHAR %s"
      % (namn, r.get("outcome"), e1["links"], h1[:16], ef.get("links"),
         (ef.get("content_hash") or "")[:16], ef.get("content_hash") == h1))
