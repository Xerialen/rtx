#!/usr/bin/env python3
"""Arm 3: applicera K2-receptet pa referensservern.

K2 = K1b-receptet (uppvagens fyra plana hopp P1-P4, ~/lab/ra_climb_planted.json)
+ vasthyllans V296 (~/lab/vast_296_planted.json). Bada filerna ar de sparade
facit som planteringskorningarna sjalva skrev, sa receptet ateranvands exakt
som det certades — inga nya parametrar hittas pa har.

Ren plantering: PlanLink per steg, samma anrop som plant_ra_climb.py gor
(carried=True). Ingen borttagning, ingen kostnadsandring. Efterat ska
lanktalet ha okat med exakt antalet steg.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/rtx-tools")
from labctl import Lab

PORT = int(os.environ.get("RTX_PORT", "27970"))
KLATTER = Path("/home/xerial/lab/ra_climb_planted.json")
VAST = Path("/home/xerial/lab/vast_296_planted.json")


def steg():
    ut = []
    d = json.loads(KLATTER.read_text())
    for namn in ("P1 z=60", "P2 z=155", "P3 z=267", "P4 z=331"):
        if namn in d:
            s = d[namn]
            ut.append((namn, s["frm"], s["takeoff"], s["tgt"], s["v_req"], s["gain"]))
    v = json.loads(VAST.read_text())
    for namn, s in v.items():
        ut.append((namn, s["from"], s["takeoff"], s["tgt"], s["v_req"], s["gain"]))
    return ut


def main():
    lab = Lab(port=PORT)
    s0 = lab.status()
    print("FORE: %d celler / %d lankar" % (s0["cells"], s0["links"]))
    planterade = []
    for namn, frm, takeoff, tgt, v_req, gain in steg():
        r = lab.request({"PlanLink": {
            "from": [float(x) for x in frm],
            "takeoff": [float(x) for x in takeoff],
            "tgt": [float(x) for x in tgt],
            "v_req": float(v_req), "gain": float(gain), "carried": True}},
            timeout=30)
        d = r.get("PlanLink") or r
        lank = d.get("link")
        print("  %-16s lank %-6s  cell %s -> %s  v_req %.0f gain %.1f"
              % (namn, lank, d.get("from_cell"), d.get("to_cell"), v_req, gain))
        if lank is None:
            print("    STOPP: motorn gav ingen lank — %s" % json.dumps(d)[:200])
            raise SystemExit(2)
        planterade.append({"namn": namn, "link": lank,
                           "from_cell": d.get("from_cell"), "to_cell": d.get("to_cell")})
    s1 = lab.status()
    okning = s1["links"] - s0["links"]
    print("EFTER: %d celler / %d lankar  (+%d, vantat +%d)"
          % (s1["cells"], s1["links"], okning, len(planterade)))
    Path("/home/xerial/hopptraning/k2_ref_planterat.json").write_text(json.dumps(
        {"port": PORT, "fore": s0["links"], "efter": s1["links"],
         "steg": planterade}, indent=1))
    if okning != len(planterade):
        raise SystemExit("STOPP: lanktalet stammer inte")
    print("OK — receptet sitter.")


if __name__ == "__main__":
    main()
