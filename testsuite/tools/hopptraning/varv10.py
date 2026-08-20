#!/usr/bin/env python3
"""ANDRING 11 (hopp 1, varv 10): stang matningen till den DALIGA anflygningen.

Anflygningshypotesen ar nu bekraftad av data (v6/v7/v9, 30 forsok):

  GODA korridoren   1624 -> 1623 -> 1670 -> 1668 [-> 1716 -> 1714]
    kommer NED FRAN NORR, avfart [446,3 160,3] kurs -55 grad, fart 434,2
    -> passerar oppningen y=123,2 z=85,9  -> TRAFF, 6 av 6, bit-identiskt

  DALIGA korridoren 1493 -> 1557 -> 1619 -> 1666 -> 1714
    kommer RAKT VASTERIFRAN langs y=160, avfart [448,4 160,5] kurs -3,7 grad
    -> flyger rakt, passerar y=142,4  -> KARMEN, 0 av 5

Bada slutar pa samma cell 1714. Skillnaden ar kursen, och kursen kommer ur
korridoren. Grafen kan inte satta kursen — men den kan bestamma vilken korridor
som ar farbar.

35592 [448,-384] -> [382,129] landar precis soder om den daliga korridoren och
matar in i den. Den ar 0 av 5 i de tre varven. Detta varv stanger ENDAST den.
Ingen ny plantering: coachnoten sager sluta plantera avfarter.

RA-vakten kors efter appliceringen och fore hoppvarvet far domas.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
BORT = 35592

lab = hoppa.Lab()
TOK = open("/home/xerial/hopptraning/.rig-lock").read().strip().splitlines()[0]

for _ in range(4):
    k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
    if k.get("outcome") == "empty":
        break
    lab.request({"Fixa": {"recipe": k.get("recipe") or "komponat", "mode": "undo", "lock_token": TOK}}, timeout=60)
k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("bas ater:", k.get("cells"), k.get("links"), k.get("content_hash") == FACIT)
if k.get("content_hash") != FACIT:
    raise SystemExit("STOPP: riggen star inte pa basen")

g = json.loads(DUMP.read_bytes())
cells, links, lids = g["cells"], g["links"], g["link_ids"]
lr0 = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in links]
if kanon.niva2(cells, lr0) != FACIT:
    raise SystemExit("STOPP: positiv kontroll misslyckades")
print("positiv kontroll: PASS")
i = lids.index(BORT)
ank = {"id": BORT, "from": links[i]["from"], "to": links[i]["to_cell"], "kind": links[i]["kind"]}
print("ankare:", ank)

s0 = kanon.stamp(g["map"], len(cells), len(links), 0)
kvar = [(l["from"], l["to_cell"], l["kind"], 1) for l, lid in zip(links, lids) if lid != BORT]
h1 = kanon.niva2(cells, kvar)
s1 = kanon.stamp(g["map"], len(cells), len(kvar), 0)
bas = {"cells": len(cells), "links": len(links), "rj_links": 0, "graph_stamp": s0, "graph_content_hash": FACIT}
e1 = {"cells": len(cells), "links": len(kvar), "rj_links": 0, "graph_stamp": s1, "graph_content_hash": h1}
print("harlett efter stangning:", e1["links"], h1)

r = lab.request({"Komponat": {
    "recept_id": "hopp1-stang-matningen-till-daliga-korridoren-v1",
    "base": bas,
    "steps": [{"name": "stang-35592", "op": {"RemoveLinks": {"links": [ank]}},
               "expect_before": bas, "expect_after": e1}],
    "expect_final": e1, "lock_token": TOK}}, timeout=60)
print("\nUTFALL:", r.get("outcome"), "|", r.get("reason"))
efter = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("EFTER  :", efter.get("links"), efter.get("content_hash"))
print("MATCHAR:", efter.get("content_hash") == h1)
Path("/home/xerial/hopptraning/hopp1-varv10-kvitto.json").write_text(json.dumps(
    {"kvitto": r, "efter": efter, "harlett": {"bas": bas, "steg1": e1}, "ankare": ank}, indent=1))
