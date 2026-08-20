#!/usr/bin/env python3
"""ANDRING 10 (hopp 1, varv 9): agarens lapp med SHIFT-kompensation och mjuk curl.

Rannsakan gav det mina varv 6-8 saknade. `~/rtx-tools/plant_ra_climb.py`, som
planterade RA-rummets uppvag mot agarens egen klattersekvens, bokfor tva saker i
sin egen docstring:

  shift — "motorn hoppar upp till bhop::LIP_REACH = 28u *fore* den planterade
           takeoff-linjen. Planteras linjen `shift` units bortom manniskans lip
           avfyras hoppet vid ratt kant."
  gain  — "Motorns default 12 ar for hard for de har hoppen."   (de kor 6-8)

Mina planteringar i varv 6 och 7 satte linjen PA agarens lapp och korde gain 12.
Boten fyrade darfor upp till 28 u for tidigt, med for hard kurva.

Kontrollrakning som binder modellen: agarens lapp [422,6 157,9] plus 24 u langs
flygriktningen ger [446,6 158,2] — vilket ar i praktiken exakt den certifierade
avfarten for 35738 [446,18 161,82], den enda korsning som ger traffar. Motorns
egen fungerande lank ligger alltsa pa agarens lapp PLUS shift. Det ar inte en
gissning, det ar tva oberoende harledningar som moter varandra.

  from    cell 1613 [368,76,56]     botens egen infartsaxel (matt i varv 7)
  takeoff [446,6 158,2 56]          agarens lapp + 24 u shift
  tgt     cell 2085 [705,161,56]    agarens landningspunkt
  v_req   419,33                    35738:s eget, bevisade varde
  gain    6,0                       35738:s eget; 12 ar for hart (RA-precedens)

Ingen stangning i detta varv: spaken isolerad.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa
import kanon

DUMP = Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
FACIT = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
FRAN_CELL, MAL_CELL = 1613, 2085
FRAN = [368.0, 76.0, 56.0]
AVFART = [446.6, 158.2, 56.0]
MAL = [705.0, 161.0, 56.0]
V_REQ, GAIN = 419.33, 6.0

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
    "recept_id": "hopp1-agarlapp-med-shift-v1",
    "base": bas,
    "steps": [{"name": "plantera-agarlapp-plus-shift",
               "op": {"PlanLink": {"from": FRAN, "takeoff": AVFART, "tgt": MAL, "v_req": V_REQ, "gain": GAIN}},
               "expect_before": bas, "expect_after": e1}],
    "expect_final": e1, "lock_token": TOK}}, timeout=60)
print("\nUTFALL:", r.get("outcome"), "|", r.get("reason"))
for st in r.get("steps") or []:
    print("  %-34s %-9s link=%s" % (st.get("name"), st.get("outcome"), st.get("link")))
efter = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("EFTER  :", efter.get("links"), efter.get("content_hash"))
print("MATCHAR:", efter.get("content_hash") == h1)
Path("/home/xerial/hopptraning/hopp1-varv9-kvitto.json").write_text(json.dumps(
    {"kvitto": r, "efter": efter, "harlett": {"bas": bas, "steg1": e1},
     "ankare": {"agarlapp": [422.6, 157.9, 56.0], "shift_u": 24.0, "planterad_lapp": AVFART,
                "kontroll_35738_certifierad_avfart": [446.18, 161.82, 56.0],
                "v_req": V_REQ, "gain": GAIN}}, indent=1))
