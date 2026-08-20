#!/usr/bin/env python3
"""ANDRING 8 (hopp 1, varv 7): samma agarlapp, men ansatsaxeln lagd pa BOTENS verkliga infart.

Mattt i varv 6: planteringen ANVANDES (4 av 10 rutter) och foll 4 av 4. Orsaken ar
varken kostnad eller acceptans utan ansatsgeometri:

  boten gar in i ansatscellen 1559 pa kurs +53..+58 grad (sydvast ifran)
  ansatsaxeln 1559 -> agarens lapp pekar -25,8 grad
  => 79 grad fel, och 78 u ansats racker inte for att svanga in

Foljden: boten korsar avfartslinjen 47 u vid sidan av axeln, vid [430,2 205,1]
i stallet for agarens lapp [422,6 157,9], och passerar oppningen pa y=142,1 z=65,8
mot agarens y=117,1 z=92,5.

Andringen ar minsta mojliga: SAMMA lapp, samma mal, samma v_req och gain — bara
ansatscellen flyttad till den vars axel matchar botens uppmatta infartskurs.

  from  cell 1613 [368,76,56]  axel +56,31 grad (botens infart +53..+58), 98,4 u ansats
  lapp  [422,6 157,9 56]       agarens egen, ordagrant
  mal   cell 2085 [705,161,56] 9,3 u fran agarens landningspunkt
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
FRAN_CELL, MAL_CELL, BORT = 1613, 2085, 34503
FRAN = [368.0, 76.0, 56.0]
AVFART = [422.6, 157.9, 56.0]
MAL = [705.0, 161.0, 56.0]
V_REQ, GAIN = 430.0, 12.0

lab = hoppa.Lab()
TOK = open("/home/xerial/hopptraning/.rig-lock").read().strip().splitlines()[0]

# 1. Rulla tillbaka varv 6:s recept.
for _ in range(3):
    k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
    if k.get("outcome") == "empty":
        break
    lab.request({"Fixa": {"recipe": k.get("recipe") or "komponat", "mode": "undo", "lock_token": TOK}}, timeout=60)
k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("bas ater:", k.get("cells"), k.get("links"), k.get("content_hash") == FACIT)
if k.get("content_hash") != FACIT:
    raise SystemExit("STOPP: riggen star inte pa basen")

# 2. Harled bada stegen FORE appliceringen.
g = json.loads(DUMP.read_bytes())
cells, links, lids = g["cells"], g["links"], g["link_ids"]
lr0 = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in links]
if kanon.niva2(cells, lr0) != FACIT:
    raise SystemExit("STOPP: positiv kontroll misslyckades")
print("positiv kontroll: PASS")
s0 = kanon.stamp(g["map"], len(cells), len(links), 0)
lr1 = lr0 + [(FRAN_CELL, MAL_CELL, "speedjump", 1)]
h1, s1 = kanon.niva2(cells, lr1), kanon.stamp(g["map"], len(cells), len(links) + 1, 0)
kvar = [(l["from"], l["to_cell"], l["kind"], 1) for l, lid in zip(links, lids) if lid != BORT]
kvar.append((FRAN_CELL, MAL_CELL, "speedjump", 1))
h2, s2 = kanon.niva2(cells, kvar), kanon.stamp(g["map"], len(cells), len(kvar), 0)
bas = {"cells": len(cells), "links": len(links), "rj_links": 0, "graph_stamp": s0, "graph_content_hash": FACIT}
e1 = {"cells": len(cells), "links": len(links) + 1, "rj_links": 0, "graph_stamp": s1, "graph_content_hash": h1}
e2 = {"cells": len(cells), "links": len(kvar), "rj_links": 0, "graph_stamp": s2, "graph_content_hash": h2}
print("harlett steg 1:", e1["links"], h1)
print("harlett steg 2:", e2["links"], h2)

# 3. Applicera.
r = lab.request({"Komponat": {
    "recept_id": "hopp1-agarlapp-botaxel-v2",
    "base": bas,
    "steps": [
        {"name": "plantera-agarlapp-pa-botaxel",
         "op": {"PlanLink": {"from": FRAN, "takeoff": AVFART, "tgt": MAL, "v_req": V_REQ, "gain": GAIN}},
         "expect_before": bas, "expect_after": e1},
        {"name": "stang-karmsiktad-raka",
         "op": {"RemoveLinks": {"links": [{"id": BORT, "from": 1712, "to": 2083, "kind": "speedjump"}]}},
         "expect_before": e1, "expect_after": e2},
    ],
    "expect_final": e2,
    "lock_token": TOK,
}}, timeout=60)
print("\nUTFALL:", r.get("outcome"), "|", r.get("reason"))
for st in r.get("steps") or []:
    print("  %-34s %-9s link=%s" % (st.get("name"), st.get("outcome"), st.get("link")))
efter = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("EFTER  :", efter.get("links"), efter.get("content_hash"))
print("MATCHAR:", efter.get("content_hash") == h2)
Path("/home/xerial/hopptraning/hopp1-varv7-kvitto.json").write_text(json.dumps(
    {"kvitto": r, "efter": efter, "harlett": {"bas": bas, "steg1": e1, "steg2": e2},
     "ankare": {"lapp": AVFART, "fran_cell": FRAN_CELL, "axel_grad": 56.31,
                "botens_infart_grad": "53..58", "ansats_u": 98.4}}, indent=1))
