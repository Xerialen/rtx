#!/usr/bin/env python3
"""Obduktion av ett varv ur flygregistratorn (motorns egna celler).

For varje misslyckande: hitta LAPPEN — sista bilden pa ringens plan (z >= 54)
fore lagsta punkten — och las av fart, topp, ben, runup, wp, lip, cell och
malcell dar. Klustrar pa (avfartscell, malcell, klass).
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

d = Path(sys.argv[1])
detalj = "--detalj" in sys.argv
rows = [json.loads(l) for l in (d / "forsok.jsonl").read_text().splitlines()]

PLAN_Z = 54.0
kluster = Counter()
farter = defaultdict(list)
lankar = Counter()

for r in rows:
    a = r.get("audit") or []
    hd = "forsok %2d %-9s %-8s tid=%-6s min_z=%-8s n=%d" % (
        r["i"], r["start_namn"], r["klass"], r.get("tid_s"), r.get("min_z"), len(a))
    if not a:
        print(hd, "(ingen registratordata)")
        continue
    lo = min(range(len(a)), key=lambda i: (a[i]["origin"] or [0, 0, 9e9])[2])
    lapp = None
    for i in range(lo, -1, -1):
        o = a[i]["origin"] or []
        if o and o[2] >= PLAN_Z:
            lapp = i
            break
    f = a[lapp] if lapp is not None else a[0]
    print(hd)
    print("   lapp   t=%.2f pos=%s cell=%s target=%s ben=%s fart=%.1f topp=%.1f runup=%s wp=%s lip=%s band=%s bhop=%s frozen=%s"
          % (f["t"], [round(v) for v in f["origin"]], f.get("cell"), f.get("target"), f.get("leg"),
             f.get("speed") or 0, f.get("peak") or 0, f.get("runup"), f.get("wp"), f.get("lip"),
             f.get("band"), f.get("bhop"), f.get("frozen")))
    print("   lagsta t=%.2f pos=%s cell=%s" % (a[lo]["t"], [round(v) for v in a[lo]["origin"]], a[lo].get("cell")))
    legs = (r.get("rutt0") or {}).get("legs") or []
    ej_walk = [(g["link"], g["kind"], g["src"], g["tgt"]) for g in legs if g["kind"] != "walk"]
    print("   rutt0: %d ben · icke-walk %s" % (len(legs), ej_walk))
    if r["klass"] != "lyckad":
        kluster[(f.get("cell"), f.get("target"), r["klass"])] += 1
        farter[(f.get("cell"), f.get("target"))].append(f.get("speed") or 0)
        for g in ej_walk:
            lankar[(g[0], g[1])] += 1
    if detalj:
        for i in range(max(0, lapp - 6), min(len(a), lo + 2)):
            g = a[i]
            print("      %8.3f %s air=%-6s ben=%-10s fart=%6.1f runup=%-8s wp=%-6s cell=%s->%s"
                  % (g["t"], [round(v) for v in g["origin"]], g.get("air"), g.get("leg"),
                     g.get("speed") or 0, g.get("runup"), g.get("wp"), g.get("cell"), g.get("target")))

print()
print("KLUSTER (avfartscell, malcell, klass) -> antal:")
for k, v in kluster.most_common():
    print("   %s -> %d" % (k, v))
print("FART VID LAPPEN per harde:")
for k, v in farter.items():
    print("   %s: n=%d min=%.1f median=%.1f max=%.1f" % (k, len(v), min(v), sorted(v)[len(v) // 2], max(v)))
print("ICKE-WALK-LANKAR i misslyckade forsoks startrutt:")
for k, v in lankar.most_common():
    print("   %s -> %d" % (k, v))
