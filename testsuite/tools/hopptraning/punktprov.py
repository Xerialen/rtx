#!/usr/bin/env python3
"""Ar planfallet POSITIONSBUNDET?

Flygskrivaren visar att rutten dor pa en enda bild nar boten star pa
x = 151-155, y = -700, z = 328 — alltsa 5-9 u VAST om cell 1216:s origin
[160,-704,328]. Isoleringen som lyckades startade pa sjalva origin.

Provet: teleportera till en rad punkter langs samma anflygning, ge Goto till
toppen, och las om en rutt alls uppstar och om den gar in i disken. Samma mal,
samma order, enda skillnaden ar startpunkten.
"""
import json
import math
import sys
import time

sys.path.insert(0, "/home/xerial/rtx-tools")
from labctl import Lab

BOT = 1
TOPP = [256.0, -704.0, 328.0]
CENTRUM = [250.0, -703.0]
PUNKTER = [
    ("1191 origin", [141.0, -701.0, 330.0]),
    ("x=144", [144.0, -701.0, 330.0]),
    ("x=148", [148.0, -701.0, 330.0]),
    ("x=151 (dodpunkt)", [151.0, -701.0, 330.0]),
    ("x=155 (dodpunkt)", [155.0, -700.0, 330.0]),
    ("x=158", [158.0, -702.0, 330.0]),
    ("1216 origin", [160.0, -704.0, 330.0]),
    ("1218 origin", [160.0, -684.0, 330.0]),
]

g = json.load(open("/home/xerial/hopptraning/graf/mainref-live-graph.json"))
P = g["cells"]
INNE = {i for i, p in enumerate(P)
        if p[2] >= 320.0 and math.hypot(p[0] - CENTRUM[0], p[1] - CENTRUM[1]) < 70.0}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    lab = Lab(port=27970)
    ut = []
    print("%-20s %-6s %-8s %-9s %-30s %s" % ("startpunkt", "cell", "rutt?", "in i disk", "forsta benet", "slut"))
    for namn, p in PUNKTER:
        rader = []
        for k in range(n):
            lab.stop(BOT)
            lab.teleport(BOT, p)
            time.sleep(0.7)
            lab.goto(BOT, TOPP)
            t0 = time.time()
            basta_len, forsta_ben, inne, slut = 0, None, None, None
            while time.time() - t0 < 4.0:
                b = None
                for x in lab.bots():
                    if x["ent"] == BOT:
                        b = x
                if b is None:
                    break
                o = b["origin"]
                slut = [round(v) for v in o]
                try:
                    legs = lab.route(BOT)["Route"].get("legs") or []
                except Exception:
                    legs = []
                if len(legs) > basta_len:
                    basta_len = len(legs)
                    forsta_ben = (legs[0]["link"], legs[0]["kind"], legs[0]["src_cell"], legs[0]["tgt_cell"])
                c = min(range(len(P)), key=lambda i: math.dist(P[i], o))
                if c in INNE:
                    inne = round(time.time() - t0, 2)
                    break
                time.sleep(0.2)
            try:
                cell = lab.cell([float(v) for v in p])["Cell"].get("cell")
            except Exception:
                cell = None
            rader.append({"k": k + 1, "max_ben": basta_len, "forsta_ben": forsta_ben,
                          "in_i_disken_s": inne, "slut": slut, "cell": cell})
        ok = sum(1 for r in rader if r["in_i_disken_s"])
        med_rutt = sum(1 for r in rader if r["max_ben"] > 0)
        print("%-20s %-6s %-8s %-9s %-30s %s"
              % (namn, rader[0]["cell"], "%d/%d" % (med_rutt, n), "%d/%d" % (ok, n),
                 str(rader[0]["forsta_ben"]), rader[-1]["slut"]))
        ut.append({"namn": namn, "punkt": p, "rader": rader})
    lab.teleport(BOT, [449.0, -338.0, 56.0])
    lab.hold(BOT)
    json.dump(ut, open("/home/xerial/hopptraning/punktprov.json", "w"), indent=1)


if __name__ == "__main__":
    main()
