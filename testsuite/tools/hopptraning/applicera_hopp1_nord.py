#!/usr/bin/env python3
"""ANDRING 7 (hopp 1, varv 6): agarens egen nordkorsning planterad + den karmsiktade stangd.

Ankarna ar MATTA, inte gissade — ur agarens egen nordkorsning i hexagonvarvet
(t = 8,09 s), den enda av hans nio demon som korsar norrsidan med full ansats:

  avfart   [422,6  157,9  56]   fart 435,2 u/s   kurs -34,05 grad   74 u fore x=496
  passage  x=496  y=117,1  z=92,5    -- mitt i den MATTA fria lanan (y 96..140)
  landning [699,6  153,4  56]

Plantering:
  from    cell 1559 [352,192,56]  -- ansatslinjens bearing -25,8 grad mot lappen,
                                     78 u ansats; den cell vars linje ligger
                                     narmast agarens egen kurs in mot lappen
  takeoff [422,6 157,9 56]        -- agarens lapp, ordagrant
  tgt     cell 2085 [705,161,56]  -- 9,3 u fran agarens landningspunkt
  v_req   430,0                   -- strax under hans matta 435,2
  gain    12,0                    -- curl: agaren lamnar lappen 33 grader av kordan
                                     och kurvar tillbaka; det ar den formen

Varfor v_req 430 och inte hogre: hallningen slapper vid 0,97*v_req = 417,1, och
avbrottet fyrar vid 0,85*v_req = 365,5. Botens goda ansats levererar 430-435, sa
den haller kvar de langsamma avfarterna utan att utlosa avbrottet. Hopp 3:s varv 2
visade vad som hander nar v_req satts for hogt: avbrottet fyrar i stallet.

Stangning: 34503, den RAKA korsningen vars egen korda passerar x=496 pa y=142,4 —
i karmen. 34501 lamnas KVAR; dess korda gar mitt i den sodra lanan.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa

H = json.loads(Path("/home/xerial/hopptraning/harlett-hopp1-nord.json").read_text())
TOK = open("/home/xerial/hopptraning/.rig-lock").read().strip().splitlines()[0]

FRAN = [352.0, 192.0, 56.0]
AVFART = [422.6, 157.9, 56.0]
MAL = [705.0, 161.0, 56.0]
V_REQ = 430.0
GAIN = 12.0


def ident(d):
    return {k: d[k] for k in ("cells", "links", "rj_links", "graph_stamp", "graph_content_hash")}


bas, e1, e2 = ident(H["bas"]), ident(H["efter_plantering"]), ident(H["efter_stangning"])
lab = hoppa.Lab()
fore = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("FORE:", fore.get("cells"), fore.get("links"), fore.get("content_hash"))
if fore.get("content_hash") != bas["graph_content_hash"]:
    print("STOPP: riggen star inte pa basen.")
    raise SystemExit(2)

cmd = {"Komponat": {
    "recept_id": "hopp1-agarens-nordkorsning-v1",
    "base": bas,
    "steps": [
        {"name": "plantera-agarens-nordkorsning",
         "op": {"PlanLink": {"from": FRAN, "takeoff": AVFART, "tgt": MAL, "v_req": V_REQ, "gain": GAIN}},
         "expect_before": bas, "expect_after": e1},
        {"name": "stang-karmsiktad-raka",
         "op": {"RemoveLinks": {"links": [{"id": 34503, "from": 1712, "to": 2083, "kind": "speedjump"}]}},
         "expect_before": e1, "expect_after": e2},
    ],
    "expect_final": e2,
    "lock_token": TOK,
}}
r = lab.request(cmd, timeout=60)
print("\nUTFALL:", r.get("outcome"), "|", r.get("reason"))
for st in r.get("steps") or []:
    print("  steg %-32s %-9s link=%s observed=%s" % (
        st.get("name"), st.get("outcome"), st.get("link"),
        (st.get("observed") or {}).get("graph_content_hash")))
efter = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("\nEFTER  :", efter.get("cells"), efter.get("links"), efter.get("content_hash"))
print("HARLETT:", e2["cells"], e2["links"], e2["graph_content_hash"])
print("MATCHAR:", efter.get("content_hash") == e2["graph_content_hash"])
Path("/home/xerial/hopptraning/hopp1-nord-kvitto.json").write_text(
    json.dumps({"fore": fore, "kvitto": r, "efter": efter, "harlett": H,
                "ankare_ur_agarens_demo": {
                    "demo": "hexagonvarvet", "t_s": 8.09,
                    "avfart": AVFART, "fart": 435.2, "kurs_grad": -34.05,
                    "passage_x496": {"y": 117.1, "z": 92.5},
                    "landning": [699.6, 153.4, 56.0]}}, indent=1))
