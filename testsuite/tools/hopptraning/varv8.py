#!/usr/bin/env python3
"""ANDRING 9 (hopp 1, varv 8): gor den korsning som FUNGERAR till den billigaste.

Matt i varv 6 och 7, svaret pa Fables fraga "kostnad eller acceptans": KOSTNAD.

  35737 (motorns egen certifierade curl, cell 1450 -> 2083)   kostnad 1,581
  min plantering i varv 7 (cell 1613 -> 2085)                 kostnad 1,221
  => planeraren valde min billigare plantering och foll 4 av 4 pa den,
     medan varje traff i varv 6 och 7 kom pa 35737.

Motorn prissatter ansatsstracka som kostnad (runup/400 + airtime + 0,3). Alltsa ar
korsningarna MED riktig ansats de dyra, och planeraren dras mot de utan. Det ar
en inverterad drivkraft, och den ar mott.

Andringen ar minsta mojliga och isolerar just den drivkraften: plantera en trogen
kopia av 35737 — samma avfart, samma mal, samma v_req, samma gain — men med
ansatscellen flyttad till cell 1623, som ligger EXAKT pa 35737:s egen axel
(-45,00 grad) 87,9 u fran avfarten. Planterad kostnad 1,195: billigare an bade
35737 och allt annat i omradet, och over jitterbandet.

Ingen lank stangs i detta varv. Om kostnadsdrivkraften ar orsaken ska boten nu
valja denna varje gang och flyga exakt den bana som redan ger traffar.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
FRAN_CELL, MAL_CELL = 1623, 2083
FRAN = [384.0, 224.0, 56.0]
AVFART = [446.18, 161.82, 56.0]      # 35738:s certifierade avfart, ordagrant
MAL = [712.0, 144.0, 56.0]           # cell 2083, samma landning
V_REQ, GAIN = 419.33, 6.0            # 35738:s egna varden

lab = hoppa.Lab()
TOK = open("/home/xerial/hopptraning/.rig-lock").read().strip().splitlines()[0]

for _ in range(3):
    k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
    if k.get("outcome") == "empty":
        break
    lab.request({"Fixa": {"recipe": k.get("recipe") or "komponat", "mode": "undo", "lock_token": TOK}}, timeout=60)
k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("bas ater:", k.get("cells"), k.get("links"), k.get("content_hash") == FACIT)
if k.get("content_hash") != FACIT:
    raise SystemExit("STOPP: riggen star inte pa basen")

g = json.loads(DUMP.read_bytes())
cells, links = g["cells"], g["links"]
lr0 = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in links]
if kanon.niva2(cells, lr0) != FACIT:
    raise SystemExit("STOPP: positiv kontroll misslyckades")
print("positiv kontroll: PASS")
s0 = kanon.stamp(g["map"], len(cells), len(links), 0)
lr1 = lr0 + [(FRAN_CELL, MAL_CELL, "speedjump", 1)]
h1 = kanon.niva2(cells, lr1)
s1 = kanon.stamp(g["map"], len(cells), len(links) + 1, 0)
bas = {"cells": len(cells), "links": len(links), "rj_links": 0, "graph_stamp": s0, "graph_content_hash": FACIT}
e1 = {"cells": len(cells), "links": len(links) + 1, "rj_links": 0, "graph_stamp": s1, "graph_content_hash": h1}
print("harlett efter plantering:", e1["links"], h1)

r = lab.request({"Komponat": {
    "recept_id": "hopp1-billigaste-goda-korsningen-v1",
    "base": bas,
    "steps": [{"name": "plantera-kopia-av-den-som-fungerar",
               "op": {"PlanLink": {"from": FRAN, "takeoff": AVFART, "tgt": MAL, "v_req": V_REQ, "gain": GAIN}},
               "expect_before": bas, "expect_after": e1}],
    "expect_final": e1,
    "lock_token": TOK,
}}, timeout=60)
print("\nUTFALL:", r.get("outcome"), "|", r.get("reason"))
for st in r.get("steps") or []:
    print("  %-38s %-9s link=%s" % (st.get("name"), st.get("outcome"), st.get("link")))
efter = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("EFTER  :", efter.get("links"), efter.get("content_hash"))
print("MATCHAR:", efter.get("content_hash") == h1)

ny = r["steps"][0].get("link")
c = lab.request({"CellById": {"cell": FRAN_CELL}})
print("\nkostnad pa den nya lanken:",
      [(l["link"], l["kind"], round(l["cost"], 3), l["to_cell"]) for l in (c.get("out") or [])
       if l["kind"] != "walk"])
Path("/home/xerial/hopptraning/hopp1-varv8-kvitto.json").write_text(json.dumps(
    {"kvitto": r, "efter": efter, "harlett": {"bas": bas, "steg1": e1}, "ny_lank": ny}, indent=1))
