#!/usr/bin/env python3
"""ANDRING 12 (hopp 1, varv 11): kombinationen {35592, 34503} — stang bada matningarna
till den daliga anflygningskorridoren.

Statusen efter varv 10 (35592 stangd, RA-vakt gron, 4/10 och syd 0/5 -> 2/5):

  GODA korridoren  1624 -> 1623 -> 1670 -> 1668 -> 1716 -> 1714
    avfart [446,3 160,3] fart 434,2 — TRAFF 8 av 8 over fyra varv, bit-identiskt
  DALIGA korridoren 1493 -> 1557 -> 1619 -> 1666 -> 1714
    avfart [447 161,8] kurs rakt ost — 0 av 7

De tva kvarvarande nordfallen i varv 10 (forsok 6 och 8) gar bada via 34503 in i
den daliga korridoren. 34503 ar dessutom den vars EGEN korda passerar oppningen
pa y=142,4 — i karmen (matt i varv 5).

Detta varv stanger bada matningarna som en KOMBINATION. In- och utlankar for
varje rord cell ar raknade tva ganger oberoende (kanon.py och recept_lint).
Ingen plantering: coachnoten sager styr rutterna, plantera inte fler avfarter.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
BORT = [34503, 35592]

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

ank = []
for lid in BORT:
    i = lids.index(lid)
    ank.append({"id": lid, "from": links[i]["from"], "to": links[i]["to_cell"], "kind": links[i]["kind"]})
print("ankare:", ank)

# Kombinationen: in/ut per rord cell, fore och efter.
rorda = sorted({a["from"] for a in ank} | {a["to"] for a in ank})
bs = set(BORT)
print("\nKOMBINATION (in/ut per rord cell):")
for c in rorda:
    ut_f = sum(1 for l in links if l["from"] == c and l["T"] == 1)
    in_f = sum(1 for l in links if l["to_cell"] == c and l["T"] == 1)
    ut_e = sum(1 for l, lid in zip(links, lids) if l["from"] == c and l["T"] == 1 and lid not in bs)
    in_e = sum(1 for l, lid in zip(links, lids) if l["to_cell"] == c and l["T"] == 1 and lid not in bs)
    flagga = "  VARNING: strypt" if ut_e == 0 or in_e == 0 else ""
    print("  cell %-5d %-18s ut %d->%d  in %d->%d%s" % (c, str(cells[c]), ut_f, ut_e, in_f, in_e, flagga))

s0 = kanon.stamp(g["map"], len(cells), len(links), 0)
kvar = [(l["from"], l["to_cell"], l["kind"], 1) for l, lid in zip(links, lids) if lid not in bs]
h1 = kanon.niva2(cells, kvar)
s1 = kanon.stamp(g["map"], len(cells), len(kvar), 0)
bas = {"cells": len(cells), "links": len(links), "rj_links": 0, "graph_stamp": s0, "graph_content_hash": FACIT}
e1 = {"cells": len(cells), "links": len(kvar), "rj_links": 0, "graph_stamp": s1, "graph_content_hash": h1}
print("\nharlett efter stangning:", e1["links"], h1)

r = lab.request({"Komponat": {
    "recept_id": "hopp1-stang-bada-matningarna-v1",
    "base": bas,
    "steps": [{"name": "stang-34503-och-35592", "op": {"RemoveLinks": {"links": ank}},
               "expect_before": bas, "expect_after": e1}],
    "expect_final": e1, "lock_token": TOK}}, timeout=60)
print("UTFALL:", r.get("outcome"), "|", r.get("reason"))
efter = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("EFTER  :", efter.get("links"), efter.get("content_hash"))
print("MATCHAR:", efter.get("content_hash") == h1)
Path("/home/xerial/hopptraning/hopp1-varv11-kvitto.json").write_text(json.dumps(
    {"kvitto": r, "efter": efter, "harlett": {"bas": bas, "steg1": e1}, "ankare": ank}, indent=1))
