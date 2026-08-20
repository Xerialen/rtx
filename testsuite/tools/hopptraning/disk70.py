#!/usr/bin/env python3
"""Finns det en GANGLANK in i 70u-toppdisken i den LEVANDE referensgrafen?

70u-fyndets §6 steg 1: finns lank 8992 (1216 -> 1265, walk) — eller nagon
walk-lank in i disken — i dagens mesh? Finns den inte ar det ett meshbyggefynd;
finns den ar det ett styrnings- eller kostnadsfynd.

Disken: z >= 320 och dxy([250,-703]) < 70.
"""
import json
import math
import sys

CENTRUM = [250.0, -703.0]
Z_MIN = 320.0
R = 70.0

g = json.load(open(sys.argv[1]))
celler = g["cells"]
lankar = g["links"]
lids = g.get("link_ids")

# Dumpformatet: cells kan vara listor [x,y,z] eller ordbocker.
def pos(c):
    if isinstance(c, dict):
        return [c.get("x", c.get("origin", [0, 0, 0])[0]),
                c.get("y", c.get("origin", [0, 0, 0])[1]),
                c.get("z", c.get("origin", [0, 0, 0])[2])] if "x" in c else c["origin"]
    return c


def dxy(p):
    return math.hypot(p[0] - CENTRUM[0], p[1] - CENTRUM[1])


P = [pos(c) for c in celler]
inne = {i for i, p in enumerate(P) if p[2] >= Z_MIN and dxy(p) < R}
print("celler i grafen        : %d" % len(P))
print("celler INNANFOR 70u    : %d" % len(inne))
if inne:
    nar = min(inne, key=lambda i: dxy(P[i]))
    print("  narmast centrum      : cell %d %s  %.1f u ut" % (nar, [round(v) for v in P[nar]], dxy(P[nar])))

# Vastkantscellerna ur briefen
for mal in ([160.0, -704.0, 328.0], [160.0, -684.0, 328.0]):
    tr = min(range(len(P)), key=lambda i: math.dist(P[i], mal))
    print("  vastkant %-18s -> cell %d %s  %.1f u fran centrum"
          % (str([round(v) for v in mal]), tr, [round(v) for v in P[tr]], dxy(P[tr])))

def lk(l):
    if isinstance(l, dict):
        return l.get("from"), l.get("to_cell", l.get("to")), l.get("kind")
    return l[0], l[1], l[2]

inkommande = []
for idx, l in enumerate(lankar):
    a, b, kind = lk(l)
    if b in inne and a not in inne:
        lid = lids[idx] if lids else idx
        inkommande.append((lid, idx, a, b, kind))

print()
print("INKOMMANDE lankar till disken utifran: %d" % len(inkommande))
sorter = {}
for lid, idx, a, b, kind in inkommande:
    sorter[kind] = sorter.get(kind, 0) + 1
print("  per sort:", sorter)
gang = [r for r in inkommande if r[4] == "walk"]
print("  varav walk: %d" % len(gang))
print()
print("%-8s %-7s %-7s %-24s %-7s %-24s %-9s" % ("lank-id", "idx", "fran", "franpos", "till", "tillpos", "kind"))
for lid, idx, a, b, kind in sorted(inkommande, key=lambda r: dxy(P[r[2]]))[:25]:
    print("%-8s %-7s %-7s %-24s %-7s %-24s %-9s"
          % (lid, idx, a, str([round(v) for v in P[a]]), b, str([round(v) for v in P[b]]), kind))

print()
print("=== sokt efter briefens exakta lank 8992 (1216 -> 1265, walk) ===")
if lids and 8992 in lids:
    i = lids.index(8992)
    a, b, kind = lk(lankar[i])
    print("  lank-id 8992 FINNS: %d %s -> %d %s  kind=%s"
          % (a, [round(v) for v in P[a]], b, [round(v) for v in P[b]], kind))
    print("  malet innanfor disken: %s (%.1f u)" % (b in inne, dxy(P[b])))
else:
    print("  lank-id 8992 finns INTE i denna graf")
for par in ((1216, 1265),):
    tr = [(lids[i] if lids else i, lk(l)) for i, l in enumerate(lankar) if lk(l)[0] == par[0] and lk(l)[1] == par[1]]
    print("  cellpar %s: %s" % (str(par), tr if tr else "ingen lank"))
