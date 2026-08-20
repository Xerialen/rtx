#!/usr/bin/env python3
"""Obduktion av IN-ring-banden mot BADA malkriterierna.

Kanonens toppdisk  : z >= 320 OCH dxy([250,-703]) <  70
Topp-vid (T1h)     : z >= 320 OCH dxy([250,-703]) <= 130, minst 15
                     konsekutiva ticks (~0,3 s @ ~49 Hz)

Ingen ny korning: samma inspelade band domes om mot det andra kriteriet.
Skriver stopposition, hogsta z, narmaste avstand till toppcentrum medan
boten ar uppe (z>=320), och domen enligt bada kriterierna.
"""
import json
import math
import sys
from pathlib import Path

CENTRUM = [250.0, -703.0]
Z_MIN = 320.0
R_KANON = 70.0
R_TOPPVID = 130.0
KONSEQ = 15


def dxy(o, c):
    return math.hypot(o[0] - c[0], o[1] - c[1])


def punkter(p):
    ut = []
    for rad in Path(p).read_text().splitlines():
        if not rad.strip():
            continue
        d = json.loads(rad)
        for spelare in d.get("players", []):
            ut.append((d["t"], spelare["origin"]))
    return ut


def stadig(pts, radie, n=KONSEQ):
    """Forsta index dar en lopande strang inom radien nar n ticks."""
    start = None
    for i, (_, o) in enumerate(pts):
        if o[2] >= Z_MIN and dxy(o, CENTRUM) <= radie:
            if start is None:
                start = i
            if i - start + 1 >= n:
                return start
        else:
            start = None
    return None


def at_ring(o):
    return 40 <= o[2] <= 90 and o[0] > 450 and o[1] >= -421


def obducera(katalog, etikett):
    rader = []
    for f in sorted(Path(katalog).glob("attempt_*.jsonl")):
        pts = punkter(f)
        if not pts:
            continue
        slut = pts[-1][1]
        uppe = [(t, o) for t, o in pts if o[2] >= Z_MIN]
        max_z = max(o[2] for _, o in pts)
        if uppe:
            narmast = min(dxy(o, CENTRUM) for _, o in uppe)
            n_uppe = len(uppe)
        else:
            narmast = None
            n_uppe = 0
        # rorelselos slutposition: sista tickens avstand till centrum
        d_slut = dxy(slut, CENTRUM)

        kanon_i = stadig(pts, R_KANON, n=1)     # kanonen kraver bara en tick
        tv_i = stadig(pts, R_TOPPVID)           # topp-vid kraver 15
        # klipptid: fran sista ring-gransticken fore traffen till traffen
        klipp = None
        if tv_i is not None:
            g = [i for i in range(tv_i) if at_ring(pts[i][1])]
            if g:
                klipp = round(pts[tv_i][0] - pts[g[-1]][0], 2)
        rader.append(dict(fil=f.name, slut=[round(v) for v in slut],
                          d_slut=round(d_slut), max_z=round(max_z),
                          narmast=(round(narmast) if narmast is not None else None),
                          n_uppe=n_uppe, kanon=kanon_i is not None,
                          toppvid=tv_i is not None, klipp_s=klipp))
    print("== %s ==" % etikett)
    print("%-14s %-22s %6s %6s %8s %6s %8s %8s %7s"
          % ("band", "stopposition", "d_slut", "max_z", "narmast", "uppe", "kanon70", "toppvid", "klipp"))
    for r in rader:
        print("%-14s %-22s %6s %6s %8s %6s %8s %8s %7s"
              % (r["fil"], r["slut"], r["d_slut"], r["max_z"], r["narmast"],
                 r["n_uppe"], "TRAFF" if r["kanon"] else "miss",
                 "TRAFF" if r["toppvid"] else "miss",
                 r["klipp_s"] if r["klipp_s"] is not None else "-"))
    k = sum(r["kanon"] for r in rader)
    t = sum(r["toppvid"] for r in rader)
    aldrig_upp = sum(1 for r in rader if r["n_uppe"] == 0)
    tider = sorted(r["klipp_s"] for r in rader if r["klipp_s"] is not None)
    med = None
    if tider:
        m = len(tider) // 2
        med = tider[m] if len(tider) % 2 else round((tider[m - 1] + tider[m]) / 2, 2)
    print("  kanonens 70u-disk: %d/%d   topp-vid 130u: %d/%d   aldrig uppe: %d   "
          "mediantid (topp-vid): %s" % (k, len(rader), t, len(rader), aldrig_upp, med))
    print()
    return {"rader": rader, "kanon": k, "toppvid": t, "n": len(rader),
            "aldrig_upp": aldrig_upp, "median_s": med}


if __name__ == "__main__":
    allt = {}
    for kat, ett in [a.split("=", 1) for a in sys.argv[1:]]:
        allt[ett] = obducera(kat, ett)
    Path("/home/xerial/hopptraning/inring_obduktion.json").write_text(
        json.dumps(allt, indent=1))
