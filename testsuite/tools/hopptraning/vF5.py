#!/usr/bin/env python3
"""vF4 — F3-lappinning riktad mot syd. Minsta ingrepp.

MATT UNDERLAG (vF3/vF3b): syds korridor ar identisk i alla forsok. Skillnaden ar
var boten lamnar marken. Motorn utloser sprangat nar `progress` — avstandet till
avfartslinjen langs ansatsaxeln — understiger LIP_REACH = 28 u.

  35738: fran cell 1450 [288,320] till avfart [446.18,161.82]
         ansatsaxel dir = (0.7071, -0.7071)   (-45 grader)

  missens punkt [433,187] : progress = 13.18*0.7071 + (-25.18)*(-0.7071) = 27.1
  traffens punkt [446,159]: progress = 0.18*0.7071 + 2.82*(-0.7071)      = -1.87

Missarna lyfter alltsa 0,9 u INNANFOR troskeln — de klarar den precis. Flyttas
avfartslinjen 6 u FRAMAT langs samma axel hamnar missens punkt pa 33,1 (utanfor,
inget tidigt lyft) medan traffens punkt blir 4,1 (fortfarande innanfor, lyfter
dar den ska).

  ny avfart = [446.18, 161.82] + 6 * (0.7071, -0.7071) = [450.4, 157.6]
  ligger val inne i cell 1714:s 32 u-fotavtryck [446,161] — fast mark.

Allt annat identiskt med 35738: from-cell 1450, mal cell 2083, v_req 419.33,
gain 6.0. Sedan tas originalet 35738 bort sa planeraren inte har bada.

RAMAR: nord 8/8 och ringitem 8/8 ar regressionsgolv. 1716 rors inte.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
VF3_BORT = [34501, 34503, 35683, 35761, 35762, 35592]
ORIGINAL = 35738
FRAN_CELL, MAL_CELL = 1450, 2083
FRAN = [288.0, 320.0, 56.0]
AVFART = [454.7, 153.3, 56.0]   # 35738:s avfart + 12 u langs ansatsaxeln
MAL = [712.0, 144.0, 56.0]
V_REQ, GAIN = 419.33, 6.0

lab = hoppa.Lab()
TOK = open("/home/xerial/hopptraning/.rig-lock").read().strip().splitlines()[0]
for _ in range(5):
    k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
    if k.get("outcome") == "empty":
        break
    lab.request({"Fixa": {"recipe": k.get("recipe") or "komponat", "mode": "undo", "lock_token": TOK}}, timeout=60)
k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
if k.get("content_hash") != FACIT:
    raise SystemExit("STOPP: riggen star inte pa basen")

g = json.loads(DUMP.read_bytes())
cells, links, lids = g["cells"], g["links"], g["link_ids"]
byid = {lid: links[i] for i, lid in enumerate(lids)}
lr0 = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in links]
if kanon.niva2(cells, lr0) != FACIT:
    raise SystemExit("STOPP: positiv kontroll misslyckades")
print("positiv kontroll: PASS")

# steg 1: plantera den lappinnade tvillingen
lr1 = lr0 + [(FRAN_CELL, MAL_CELL, "speedjump", 1)]
h1 = kanon.niva2(cells, lr1)
s1 = kanon.stamp(g["map"], len(cells), len(links) + 1, 0)
# steg 2: ta bort vF3:s sex + originalet 35738
BORT = set(VF3_BORT) | {ORIGINAL}
kvar = [(l["from"], l["to_cell"], l["kind"], 1) for l, lid in zip(links, lids) if lid not in BORT]
kvar.append((FRAN_CELL, MAL_CELL, "speedjump", 1))
h2 = kanon.niva2(cells, kvar)
s2 = kanon.stamp(g["map"], len(cells), len(kvar), 0)
bas = {"cells": len(cells), "links": len(links), "rj_links": 0,
       "graph_stamp": kanon.stamp(g["map"], len(cells), len(links), 0), "graph_content_hash": FACIT}
e1 = {"cells": len(cells), "links": len(links) + 1, "rj_links": 0, "graph_stamp": s1, "graph_content_hash": h1}
e2 = {"cells": len(cells), "links": len(kvar), "rj_links": 0, "graph_stamp": s2, "graph_content_hash": h2}
print("harlett steg 1: %d  %s" % (e1["links"], h1[:16]))
print("harlett steg 2: %d  %s" % (e2["links"], h2[:16]))

ank = []
for lid in sorted(BORT):
    l = byid[lid]
    ank.append({"id": lid, "from": l["from"], "to": l["to_cell"], "kind": l["kind"]})

r = lab.request({"Komponat": {
    "recept_id": "vF5-lappinning-12u",
    "base": bas,
    "steps": [
        {"name": "plantera-lappinnad-tvilling",
         "op": {"PlanLink": {"from": FRAN, "takeoff": AVFART, "tgt": MAL, "v_req": V_REQ, "gain": GAIN}},
         "expect_before": bas, "expect_after": e1},
        {"name": "ta-bort-vF3-sex-plus-originalet",
         "op": {"RemoveLinks": {"links": ank}},
         "expect_before": e1, "expect_after": e2},
    ],
    "expect_final": e2, "lock_token": TOK}}, timeout=60)
print("UTFALL:", r.get("outcome"), "|", r.get("reason"))
for st in r.get("steps") or []:
    print("  %-34s %-9s link=%s" % (st.get("name"), st.get("outcome"), st.get("link")))
ef = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("EFTER %d %s | MATCHAR %s" % (ef.get("links"), (ef.get("content_hash") or "")[:16],
                                    ef.get("content_hash") == h2))
