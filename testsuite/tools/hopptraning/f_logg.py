#!/usr/bin/env python3
"""De fyra loggfalten per forsok + snabbkriterierna, ur ett varvs rundata.

  (a) forsta SJ-lank i planen (rutt0, innan boten rort sig)
  (b) korsning ja/nej   — passerar banan x=496 osterut i luften over z=50
  (c) luftmal vid sista SpeedJump — audit `target` pa sista bilden med leg=SpeedJump
  (d) plansvanslangd efter hopplanken — antal ben i rutt0 efter forsta SJ-lanken

Plus stordningskontroll: forsok som blockerats av annan bot pa startpunkten.
"""
import json
import sys
from collections import Counter
from pathlib import Path

SJ_GOD = {35736, 35737, 35738}          # BAS-id
SJ_FORBJUDNA = {34501, 34503}           # BAS-id
d = Path(sys.argv[1])
BORT = {int(x) for x in sys.argv[2:]}   # bas-id som tagits bort i detta varv

_g = json.loads(Path("/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json").read_bytes())
_kvar = [i for i in sorted(_g["link_ids"]) if i not in BORT]

def bas(live):
    """Live-index -> bas-id. Lankarrayen komprimeras vid remove, sa id:n skiftar."""
    return _kvar[live] if live is not None and live < len(_kvar) else live
rows = [json.loads(l) for l in (d / "forsok.jsonl").read_text().splitlines()]

forsta_sj, mal_luft, korsningar, blockerade = Counter(), Counter(), 0, 0
per_start = {}
print("%-3s %-11s %-8s %-9s %-6s %-9s %-6s" % ("i", "vinkel", "klass", "(a)1:a SJ", "(b)kors", "(c)luftmal", "(d)svans"))
for r in rows:
    if r["klass"] == "start_blockerad":
        blockerade += 1
    legs = (r.get("rutt0") or {}).get("legs") or []
    sj = [(n, g["link"]) for n, g in enumerate(legs) if g["kind"] == "speedjump"]
    a = bas(sj[0][1]) if sj else None
    dsv = (len(legs) - 1 - sj[0][0]) if sj else None
    a_ = r.get("audit") or []
    kors = False
    for i in range(1, len(a_)):
        if a_[i - 1]["origin"][0] < 496 <= a_[i]["origin"][0] and a_[i]["origin"][2] > 50:
            kors = True
            break
    c = None
    for f in a_:
        if f.get("leg") == "SpeedJump":
            c = f.get("target")
    if a is not None:
        forsta_sj[a] += 1
    if c is not None:
        mal_luft[c] += 1
    korsningar += int(kors)
    ps = per_start.setdefault(r["start_namn"], {"n": 0, "ok": 0, "kors": 0})
    ps["n"] += 1
    ps["ok"] += int(r["klass"] == "lyckad")
    ps["kors"] += int(kors)
    print("%-3d %-11s %-8s %-9s %-6s %-9s %-6s" % (r["i"], r["start_namn"], r["klass"],
                                                   a, "JA" if kors else "nej", c, dsv))

n = len(rows)
ok = sum(1 for r in rows if r["klass"] == "lyckad")
god = sum(v for k, v in forsta_sj.items() if k in SJ_GOD)
forbj = sum(v for k, v in forsta_sj.items() if k in SJ_FORBJUDNA)
print()
print("SUMMA %s: %d/%d over (lyckade), %d korsningar" % (d.name, ok, n, korsningar))
print("  per vinkel:", json.dumps(per_start))
print("  (a) forsta SJ-lank:", forsta_sj.most_common())
print("  (c) luftmal       :", mal_luft.most_common())
print("  stordning (start_blockerad):", blockerade)
print()
print("SNABBKRITERIER")
print("  F1 bitit? god SJ-familj i %d/%d  (krav >=9/10 => %.0f %%)  | forbjudna 34501/34503: %d (krav 0)"
      % (god, n, 90.0, forbj))
print("     -> %s" % ("JA" if (god >= 0.9 * n and forbj == 0) else "NEJ"))
m2411 = mal_luft.get(2411, 0)
tot = sum(mal_luft.values())
print("  F6 bitit? luftmal 2411 i %d av %d SJ-forsok" % (m2411, tot))
syd = per_start.get("syd", {})
print("  Syd-fallan? syd korsningar = %s (far inte vara 0)" % syd.get("kors"))
