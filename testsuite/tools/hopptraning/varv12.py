#!/usr/bin/env python3
"""ANDRING 13 (hopp 1, varv 12): stang VASTINFARTEN till avfartscellen 1714.

Coachorder ur 18.1-matningen. Den daliga korridorens sista led ar
1666 [416,160] -> 1714 [446,161], en WALK-lank, id 12255.

Verifierat fore appliceringen:
  * id 12255 ar INTE 34503 och inte tidigare provad i kampanjen
    (provade: 34501, 34503, 35592, samt tva planteringar)
  * cell 1714 har 7 infarter, varav den NORRA 1668 -> 1714 (id 12270) ar den
    goda korridorens; efter stangning aterstar 6, inklusive den norra
  * cell 1714 behaller alla 9 utfarter, daribland avfarten
  * cell 1666 kontrollraknas nedan

Stryper stangningen nagon cell, eller visar det sig att lanken redan var provad,
ar det BLOCKED — inte en applicering.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
BORT = 12255
PROVADE = {34501, 34503, 35592}

g = json.loads(DUMP.read_bytes())
cells, links, lids = g["cells"], g["links"], g["link_ids"]
i = lids.index(BORT)
ank = {"id": BORT, "from": links[i]["from"], "to": links[i]["to_cell"], "kind": links[i]["kind"]}
print("ankare:", ank, " T =", links[i]["T"])

if BORT in PROVADE:
    print("BLOCKED: lanken ar redan provad i kampanjen.")
    raise SystemExit(3)

# --- kombinationen: in/ut per rord cell, fore och efter -------------------
print("\nKOMBINATION (in/ut per rord cell, T=1):")
strypt = False
for c in sorted({ank["from"], ank["to"]}):
    ut_f = [lids[j] for j, l in enumerate(links) if l["from"] == c and l["T"] == 1]
    in_f = [lids[j] for j, l in enumerate(links) if l["to_cell"] == c and l["T"] == 1]
    ut_e = [x for x in ut_f if x != BORT]
    in_e = [x for x in in_f if x != BORT]
    print("  cell %-5d %-18s ut %2d -> %2d   in %2d -> %2d" % (c, str(cells[c]), len(ut_f), len(ut_e), len(in_f), len(in_e)))
    if not ut_e or not in_e:
        print("     STRYPT")
        strypt = True
# norra infarten maste finnas kvar
norr = [lids[j] for j, l in enumerate(links)
        if l["to_cell"] == 1714 and l["from"] == 1668 and l["T"] == 1 and lids[j] != BORT]
print("  norra infarten 1668 -> 1714 kvar:", norr or "SAKNAS")
if not norr:
    strypt = True
if strypt:
    print("\nBLOCKED: stangningen stryper en cell eller tar bort norra infarten.")
    raise SystemExit(3)
print("  ingen cell strypt, norra infarten kvar -> OK att applicera")

# --- harled + applicera ---------------------------------------------------
lab = hoppa.Lab()
TOK = open("/home/xerial/hopptraning/.rig-lock").read().strip().splitlines()[0]
for _ in range(4):
    k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
    if k.get("outcome") == "empty":
        break
    lab.request({"Fixa": {"recipe": k.get("recipe") or "komponat", "mode": "undo", "lock_token": TOK}}, timeout=60)
k = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
if k.get("content_hash") != FACIT:
    raise SystemExit("STOPP: riggen star inte pa basen")
lr0 = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in links]
if kanon.niva2(cells, lr0) != FACIT:
    raise SystemExit("STOPP: positiv kontroll misslyckades")
print("\npositiv kontroll: PASS · bas ater:", k.get("cells"), k.get("links"))

s0 = kanon.stamp(g["map"], len(cells), len(links), 0)
kvar = [(l["from"], l["to_cell"], l["kind"], 1) for l, lid in zip(links, lids) if lid != BORT]
h1 = kanon.niva2(cells, kvar)
s1 = kanon.stamp(g["map"], len(cells), len(kvar), 0)
bas = {"cells": len(cells), "links": len(links), "rj_links": 0, "graph_stamp": s0, "graph_content_hash": FACIT}
e1 = {"cells": len(cells), "links": len(kvar), "rj_links": 0, "graph_stamp": s1, "graph_content_hash": h1}
print("harlett efter stangning:", e1["links"], h1)

r = lab.request({"Komponat": {
    "recept_id": "hopp1-stang-vastinfarten-till-1714-v1",
    "base": bas,
    "steps": [{"name": "stang-12255-vastinfart", "op": {"RemoveLinks": {"links": [ank]}},
               "expect_before": bas, "expect_after": e1}],
    "expect_final": e1, "lock_token": TOK}}, timeout=60)
print("UTFALL:", r.get("outcome"), "|", r.get("reason"))
efter = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("EFTER  :", efter.get("links"), efter.get("content_hash"))
print("MATCHAR:", efter.get("content_hash") == h1)
Path("/home/xerial/hopptraning/hopp1-varv12-kvitto.json").write_text(json.dumps(
    {"kvitto": r, "efter": efter, "harlett": {"bas": bas, "steg1": e1}, "ankare": ank}, indent=1))
