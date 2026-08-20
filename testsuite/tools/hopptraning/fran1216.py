#!/usr/bin/env python3
"""Ren isolering: kan routern ta boten fran cell 1216 in i 70u-disken?

Foregaende prov blandade tva saker — klattringen dit OCH steget in — och dess
tillstandslasning var grumlig (order=hold medan boten anda rorde sig). Har
teleporteras boten RAKT till 1216, ingen klattring alls, och varje tick loggar
position, cell, order, posture och planens forsta ben.

Tva korlagen som skiljer puppetorder fran botens egen vilja:
  --goto   Goto till toppen (puppetorder)
  --fri    Stop, dvs boten far folja sitt eget mal (RA-rustningen star pa disken)

Skillnaden mellan dem ar sjalva svaret: gar den in i det ena laget men inte i
det andra sitter felet i puppetstyrningen, inte i grafen.
"""
import json
import math
import sys
import time

sys.path.insert(0, "/home/xerial/rtx-tools")
from labctl import Lab

BOT = 1
C1216 = [160.0, -704.0, 330.0]     # nagot over golvet sa den landar i cellen
TOPP = [256.0, -704.0, 328.0]
CENTRUM = [250.0, -703.0]

g = json.load(open("/home/xerial/hopptraning/graf/mainref-live-graph.json"))
P = g["cells"]
INNE = {i for i, p in enumerate(P)
        if p[2] >= 320.0 and math.hypot(p[0] - CENTRUM[0], p[1] - CENTRUM[1]) < 70.0}


def cell_vid(p):
    return min(range(len(P)), key=lambda i: math.dist(P[i], p))


def prov(lab, lage, sekunder=12.0):
    lab.stop(BOT)
    lab.teleport(BOT, C1216)
    time.sleep(0.8)
    if lage == "goto":
        lab.goto(BOT, TOPP)
    else:
        lab.stop(BOT)
    t0 = time.time()
    spar, in_i_disken, forsta_plan = [], None, None
    while time.time() - t0 < sekunder:
        b = None
        for x in lab.bots():
            if x["ent"] == BOT:
                b = x
        if b is None:
            break
        try:
            legs = lab.route(BOT)["Route"].get("legs") or []
        except Exception:
            legs = []
        o = b["origin"]
        c = cell_vid(o)
        if legs and forsta_plan is None:
            forsta_plan = [(L["link"], L["kind"], L["src_cell"], L["tgt_cell"]) for L in legs[:4]]
        spar.append((round(time.time() - t0, 2), [round(v) for v in o], c,
                     b.get("order"), b.get("posture"), len(legs)))
        if c in INNE and in_i_disken is None:
            in_i_disken = round(time.time() - t0, 2)
            break
        time.sleep(0.25)
    return {"lage": lage, "in_i_disken_s": in_i_disken, "forsta_plan": forsta_plan,
            "spar": spar}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    lab = Lab(port=27970)
    allt = []
    for lage in ("goto", "fri"):
        print("== lage: %s ==" % lage)
        for i in range(n):
            r = prov(lab, lage)
            allt.append(r)
            sista = r["spar"][-1] if r["spar"] else None
            print("  %d: %s  slut %s cell %s order=%s posture=%s planben=%s"
                  % (i + 1,
                     ("IN I DISKEN pa %.2f s" % r["in_i_disken_s"]) if r["in_i_disken_s"] else "stannade utanfor",
                     sista[1], sista[2], sista[3], sista[4], sista[5]))
            if r["forsta_plan"]:
                print("      forsta plan: %s" % (r["forsta_plan"],))
    lab.teleport(BOT, [449.0, -338.0, 56.0])
    lab.hold(BOT)
    for lage in ("goto", "fri"):
        rs = [r for r in allt if r["lage"] == lage]
        print("%-5s: %d av %d kom in i disken" % (lage, sum(1 for r in rs if r["in_i_disken_s"]), len(rs)))
    json.dump(allt, open("/home/xerial/hopptraning/fran1216.json", "w"), indent=1)


if __name__ == "__main__":
    main()
