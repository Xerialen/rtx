#!/usr/bin/env python3
"""ANDRING 6 (hopp 3, varv 2): byt den raka gapkorsningen mot en curl med hogre v_req.

Obduktionsfynd hopp 3 varv 1 (3/10): mekanismen ar INTE hopp 1:s sidoavdrift utan
en FARTBRIST vid avfarten.

  lyckade  : avfart [431,181] / [432,182] med 430-432 u/s
  fallande : avfart [425,138] med 368,3 · [459,153] med 378,8 · [437,-198] med 383,3

Korsningen kraver v_req 405,9. De fallande lamnar marken 22-38 u/s UNDER kravet
och landar nagra enheter kort — tre av dem slutar pa [716,143,-200], precis vid
den bortre lappen.

Varfor slapps de igenom: motorns egen "don't leap to your death"-hallning har TVA
trosklar. En RAK speedjump haller bara medan `speed < v_req * 0.9` — dvs 365 u/s
har. En CURL haller tills `speed >= v_req * (1 - CURL_V_HOLD_TOL)` med
CURL_V_HOLD_TOL = 0,03, alltsa 0,97 * v_req. Avfarterna pa 368,3 ligger 3 u/s
over den raka trosklen och slapps igenom; mot curl-trosklen hade de hallits.

Andringen ar darfor: plantera samma korsning som en CURL med v_req 440 (band-golv
0,97*440 = 426,8 — precis de lyckades band) och stang den raka. Ingen motorandring,
ingen ny op-art. Bada stegens slutlagen ar HARLEDDA fore korningen.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa

H = json.loads(Path("/home/xerial/hopptraning/harlett-hopp3.json").read_text())
TOK = open("/home/xerial/hopptraning/.rig-lock").read().strip().splitlines()[0]

FRAN = [413.0, 136.0, 56.0]      # cell 1664, pa ringens ostkant
AVFART = [438.0, 142.0, 56.0]    # samma lapp som den raka korsningen anvande
MAL = [712.0, 144.0, 56.0]       # cell 2083, samma landning
V_REQ = 440.0
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
    "recept_id": "hopp3-curl-med-hogre-vreq-v1",
    "base": bas,
    "steps": [
        {"name": "plantera-curl-korsning",
         "op": {"PlanLink": {"from": FRAN, "takeoff": AVFART, "tgt": MAL, "v_req": V_REQ, "gain": GAIN}},
         "expect_before": bas, "expect_after": e1},
        {"name": "stang-raka-korsningen",
         "op": {"RemoveLinks": {"links": [{"id": 34503, "from": 1712, "to": 2083, "kind": "speedjump"}]}},
         "expect_before": e1, "expect_after": e2},
    ],
    "expect_final": e2,
    "lock_token": TOK,
}}
r = lab.request(cmd, timeout=60)
print("\nUTFALL:", r.get("outcome"), "|", r.get("reason"))
for st in r.get("steps") or []:
    print("  steg %-26s %-9s observed=%s" % (st.get("name"), st.get("outcome"),
          (st.get("observed") or {}).get("graph_content_hash")))
efter = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("\nEFTER  :", efter.get("cells"), efter.get("links"), efter.get("content_hash"))
print("HARLETT:", e2["cells"], e2["links"], e2["graph_content_hash"])
print("MATCHAR:", efter.get("content_hash") == e2["graph_content_hash"])
Path("/home/xerial/hopptraning/hopp3-recept-kvitto.json").write_text(
    json.dumps({"fore": fore, "kvitto": r, "efter": efter, "harlett": H}, indent=1))
