#!/usr/bin/env python3
"""Las SNG-vaktens kriterium mot den UPPMATTA envelopen, fore forsta domda varvet.

Tre matningar pa basgrafen efter att apparatfelen rattats:
  bas3  natta 4/4  fall 0
  bas4  natta 3/4  fall 0
Fallraknaren ar stabil pa 0. Malraknaren varierar (4/4, 3/4) — samma monster som
RA-vakten. Kriteriet satts darfor pa envelopen, inte pa en enda observation:

    GRON = fall <= 0  OCH  natta >= 75 % av forsoken
"""
import json
import pathlib

p = pathlib.Path("/home/xerial/hopptraning/sng-vakt/baslinje.json")
p.write_text(json.dumps({
    "matningar": {"bas3": {"natta": 4, "n": 4, "fall": 0},
                  "bas4": {"natta": 3, "n": 4, "fall": 0}},
    "fall": 0,
    "natta_frac": 0.75,
    "not": "fallraknaren stabil pa 0; malraknaren varierar 3-4 av 4. Kriteriet ar "
           "skrivet FORE forsta domda varvet."
}, indent=1))

s = pathlib.Path("/home/xerial/hopptraning/sng_vakt.py").read_text()
old = '''        gron = alla_fall <= BASLINJE["fall"] and natta >= BASLINJE["natta_min"]'''
new = '''        krav = -(-int(BASLINJE["natta_frac"] * len(rader)) // 1)
        gron = alla_fall <= BASLINJE["fall"] and natta >= krav'''
assert s.count(old) == 1
s = s.replace(old, new, 1)
s = s.replace('''        print("  baslinje  : fall <= %d, natta >= %d" % (BASLINJE["fall"], BASLINJE["natta_min"]))''',
              '''        print("  baslinje  : fall <= %d, natta >= %d av %d" % (BASLINJE["fall"], krav, len(rader)))''', 1)
pathlib.Path("/home/xerial/hopptraning/sng_vakt.py").write_text(s)
print("SNG-vaktens kriterium last: fall <= 0, natta >= 75 %")
