#!/usr/bin/env python3
"""Ar offlineresolveringen kanslig for att dumpens cellkoordinater ar trunkerade?

Dumpen bar `c.origin.x as i32` — trunkering mot noll, alltsa upp till en enhet
fel per axel, med KAND riktning:

    c > 0  ->  o i [c, c+1)
    c < 0  ->  o i (c-1, c]
    c = 0  ->  o i (-1, 1)

For varje punkt i receptet raknas darfor ett intervall per kandidatcell:
d_max(vinnaren) mot d_min(narmaste utmanaren). Haller d_max(vinnare) <
d_min(utmanare) ar svaret robust for VARJE tillaten uppsattning sanna
koordinater — inte bara for de trunkerade.

Kors: python3 trunkeringsprov.py <dump.json> <recept.json> [<recept.json> ...]
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from applicera_recept import GRID, bygg_rutnat, steg_ur  # noqa: E402


def intervall(c_i, p_i):
    """(min, max) av |o - p| for o i cellens tillatna intervall pa en axel."""
    if c_i > 0:
        lo, hi = float(c_i), c_i + 1.0
    elif c_i < 0:
        lo, hi = c_i - 1.0, float(c_i)
    else:
        lo, hi = -1.0, 1.0
    if p_i < lo:
        return lo - p_i, hi - p_i
    if p_i > hi:
        return p_i - hi, p_i - lo
    return 0.0, max(p_i - lo, hi - p_i)


def d_intervall(c, p):
    lo2 = hi2 = 0.0
    for i in range(3):
        a, b = intervall(c[i], p[i])
        lo2 += a * a
        hi2 += b * b
    return math.sqrt(lo2), math.sqrt(hi2)


def kandidater(pos, celler, rut):
    gx, gy = math.floor(pos[0] / GRID), math.floor(pos[1] / GRID)
    sedda = {}
    basta = None
    for radie in range(0, 5):
        for dx in range(-radie, radie + 1):
            for dy in range(-radie, radie + 1):
                for cid in rut.get((gx + dx, gy + dy), ()):
                    if cid in sedda:
                        continue
                    c = celler[cid]
                    d = ((c[0] - pos[0]) ** 2 + (c[1] - pos[1]) ** 2 + (c[2] - pos[2]) ** 2)
                    sedda[cid] = d
                    if basta is None or d < basta[1]:
                        basta = (cid, d)
        if basta is not None and radie >= 1:
            break
    return basta[0], sedda


def main():
    dump, recept = sys.argv[1], sys.argv[2:]
    g = json.load(open(dump))
    celler = g["cells"]
    rut = bygg_rutnat(celler)
    alla_robusta = True
    for fil in recept:
        _, steg = steg_ur(fil)
        for s in steg:
            if s.get("op") != "PlanLink":
                continue
            for falt in ("from", "tgt"):
                pos = s[falt]
                vinnare, sedda = kandidater(pos, celler, rut)
                v_lo, v_hi = d_intervall(celler[vinnare], pos)
                utmanare = None
                for cid in sedda:
                    if cid == vinnare:
                        continue
                    lo, _ = d_intervall(celler[cid], pos)
                    if utmanare is None or lo < utmanare[1]:
                        utmanare = (cid, lo)
                robust = utmanare is None or v_hi < utmanare[1]
                alla_robusta = alla_robusta and robust
                print("%-24s %-4s cell %-5d d_max=%-7.3f  utmanare %-5s d_min=%-7.3f  %s"
                      % (s["namn"], falt, vinnare, v_hi,
                         utmanare[0] if utmanare else "-",
                         utmanare[1] if utmanare else float("inf"),
                         "ROBUST" if robust else "KANSLIG"))
    print()
    print("SAMLAT: %s" % ("robust for varje tillaten sann koordinat"
                          if alla_robusta else "MINST EN PUNKT AR KANSLIG"))
    return 0 if alla_robusta else 1


if __name__ == "__main__":
    sys.exit(main())
