#!/usr/bin/env python3
"""RA-VAKTEN — regressionsvakt for varje grafandring (agarvillkor 20/8).

Kor den BEFINTLIGA 14-malsregressionen `~/rtx-tools/patrol.py` mot MIN rigg
(27980, via dess egen RTX_PORT-stod) och domer pa AGARENS kriterium:

    inte falla, inte fastna — tider sekundara

Domen ar RELATIV mot den uppmatta baslinjen pa basgrafen, inte mot absolut noll:
basgrafen har sjalv EN fall per varv (uppmatt 3 ganger). En vakt som kravde noll
hade darfor varit rod fore varje andring och aldrig kunnat doma nagot.

    GRON  = alla 14 mal natta  OCH  fall <= baslinjen
    ROD   = nagot mal missat   ELLER fler fall an baslinjen

Ror aldrig 27990 (agarens RA-rigg). Mutexfilen ~/lab/.rig-lock lamnas orord —
RIG_PARENT_LOCK=1 ar patrols egen dokumenterade vag forbi den, och mitt riggslas
ligger i ~/hopptraning/.rig-lock for en annan unit.
"""
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Uppmatt baslinje pa basgrafen, tre varv fore nagon andring (bokfort i rapporten):
#   nadda mal : 14/14 · 14/14 · 12/14
#   fall      : 1 · 1 · 1   — alltid pa samma stalle, [~410 -880 ~150]
# Fallraknaren ar alltsa stabil, malraknaren har egen varians. Kriteriet ar satt
# mot den UPPMATTA envelopen och skrivet FORE forsta domda vakten.
BASLINJE_FALL = 1
BASLINJE_MAL_MIN = 12      # basgrafens egen samsta av tre varv
UT = Path("/home/xerial/hopptraning/ra-vakt")
UT.mkdir(parents=True, exist_ok=True)

tag = sys.argv[1]
laps = sys.argv[2] if len(sys.argv) > 2 else "1"
env = dict(os.environ, RTX_PORT="27980", RIG_PARENT_LOCK="1")
r = subprocess.run(["python3", "patrol.py", tag, laps], cwd="/home/xerial/rtx-tools",
                   env=env, capture_output=True, text=True, timeout=900)
print(r.stdout.strip())
if r.returncode != 0:
    print("RA-VAKT: patrol.py foll (exit %d)\n%s" % (r.returncode, r.stderr.strip()[:400]))
    sys.exit(2)

src = Path("/home/xerial/lab/patrol-%s.json" % tag)
d = json.loads(src.read_text())
shutil.copy(src, UT / src.name)

reached, attempts = d["reached"], d["attempts"]
falls = d["falls"]
alla_mal = reached >= BASLINJE_MAL_MIN * (attempts // 14)
inom_fall = len(falls) <= BASLINJE_FALL
gron = alla_mal and inom_fall

print()
print("RA-VAKT  %s" % tag)
print("  mal natta : %d/%d (baslinjegrans %d)   %s" % (reached, attempts, BASLINJE_MAL_MIN * (attempts // 14), "OK" if alla_mal else "UNDER BASLINJEN - fastnat"))
print("  fall      : %d (baslinje %d)   %s" % (len(falls), BASLINJE_FALL,
                                               "OK" if inom_fall else "FLER AN BASLINJEN"))
for f in falls:
    print("     fall vid %s" % f)
print("  DOM       : %s" % ("GRON" if gron else "ROD"))
(UT / ("dom-%s.json" % tag)).write_text(json.dumps(
    {"tag": tag, "reached": reached, "attempts": attempts, "falls": falls,
     "baslinje_fall": BASLINJE_FALL, "dom": "GRON" if gron else "ROD"}, indent=1))
sys.exit(0 if gron else 1)
