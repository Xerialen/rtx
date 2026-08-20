#!/usr/bin/env python3
"""70u-fyndets §6 steg 2: planeras ganglanken in i disken, eller aldrig?

Kor IN ring pa referensservern och lasar botens LEVANDE plan (`Route`) medan den
gar. Fragan ar binar:

  * Ligger nagon av de 19 walk-lankarna in i 70u-disken NAGONSIN i planen?
      ja  -> STYRNINGSFYND: rutten finns, boten fullfoljer den inte
      nej -> ROUTERFYND: planeraren valjer aldrig steget

Kompletterande matning: nar boten star pa vastkanten (cell 1214/1216/1218),
vad sager planen DA? Det skiljer "planerades men slapptes" fran "planerades
aldrig".
"""
import json
import math
import sys
import time

sys.path.insert(0, "/home/xerial/rtx-tools")
from labctl import Lab

BOT = 1
START = [449.0, -338.0, 56.0]
TOPP = [256.0, -704.0, 328.0]
CENTRUM = [250.0, -703.0]
Z_MIN, R = 320.0, 70.0
VASTKANT = {1214, 1216, 1218}
BUDGET = 25.0

g = json.load(open("/home/xerial/hopptraning/graf/mainref-live-graph.json"))
P = g["cells"]
INNE = {i for i, p in enumerate(P) if p[2] >= Z_MIN and math.hypot(p[0] - CENTRUM[0], p[1] - CENTRUM[1]) < R}
# de 19 inkommande walk-lankarna: (lank-id, fran, till)
DISKLANKAR = {}
for idx, l in enumerate(g["links"]):
    if l["to_cell"] in INNE and l["from"] not in INNE:
        DISKLANKAR[g["link_ids"][idx]] = (l["from"], l["to_cell"], l["kind"])
print("disken: %d celler, %d inkommande lankar (%s)"
      % (len(INNE), len(DISKLANKAR), set(k for _, _, k in DISKLANKAR.values())))


def cell_vid(pos):
    return min(range(len(P)), key=lambda i: math.dist(P[i], pos))


def rutt(lab):
    # Status/Route ar enhetsvarianter respektive struct — labctl:s egna metoder
    # kanner formen; ett handrullat {"Status": {}} far aldrig svar.
    try:
        return lab.route(BOT)["Route"]
    except Exception:
        return None


def bot(lab):
    try:
        for b in lab.bots():
            if b["ent"] == BOT:
                return b
    except Exception:
        return None
    return None


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    lab = Lab(port=27970)
    lab.set("rtx_telemetry", "1")
    rader = []
    for i in range(n):
        lab.stop(BOT)
        lab.teleport(BOT, START)
        time.sleep(0.7)
        lab.goto(BOT, TOPP)
        t0 = time.time()
        planerad = {}          # lank-id -> forsta sekund den sags i planen
        pa_vastkant = []       # (sekund, cell, planens forsta ben)
        celler_bes = set()
        sista_plan, sista_pos = None, None
        while time.time() - t0 < BUDGET:
            b = bot(lab)
            if b is None:
                break
            o = b["origin"]
            sista_pos = o
            c = cell_vid(o)
            celler_bes.add(c)
            r = rutt(lab)
            legs = (r or {}).get("legs") or []
            if legs:
                sista_plan = legs
            for L in legs:
                if L["link"] in DISKLANKAR and L["link"] not in planerad:
                    planerad[L["link"]] = round(time.time() - t0, 2)
            if c in VASTKANT and o[2] >= 320:
                pa_vastkant.append((round(time.time() - t0, 2), c,
                                    [(L["link"], L["kind"], L["src_cell"], L["tgt_cell"]) for L in legs[:3]]))
            if cell_vid(o) in INNE:
                break
            time.sleep(0.25)
        slutcell = cell_vid(sista_pos) if sista_pos else None
        traff = slutcell in INNE
        rader.append({
            "i": i + 1, "slut_pos": [round(v) for v in (sista_pos or [0, 0, 0])],
            "slut_cell": slutcell, "i_disken": traff,
            "planerade_disklankar": planerad,
            "besokte_vastkant": sorted(c for c in celler_bes if c in VASTKANT),
            "vastkantsprov": pa_vastkant[:4],
            "sista_plan": [(L["link"], L["kind"], L["src_cell"], L["tgt_cell"]) for L in (sista_plan or [])[:6]],
        })
        print("  %2d/%d  slut %s cell %s %s | disklank i plan: %s | vastkant: %s"
              % (i + 1, n, rader[-1]["slut_pos"], slutcell,
                 "I DISKEN" if traff else "utanfor",
                 (list(planerad) or "INGEN"), rader[-1]["besokte_vastkant"] or "-"))
    lab.teleport(BOT, START)
    lab.hold(BOT)

    nagon = sum(1 for r in rader if r["planerade_disklankar"])
    idisk = sum(1 for r in rader if r["i_disken"])
    print()
    print("SUMMA %d forsok: %d natt in i disken, %d hade en disklank i planen nagon gang"
          % (len(rader), idisk, nagon))
    print("DOM: %s" % ("STYRNINGSFYND — steget planeras men fullfoljs inte" if nagon and not idisk
                       else "ROUTERFYND — steget planeras aldrig" if not nagon
                       else "boten nar disken; ingen av lagena"))
    json.dump(rader, open("/home/xerial/hopptraning/rutt1216.json", "w"), indent=1)


if __name__ == "__main__":
    main()
