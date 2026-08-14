"""Omklippning av facit mot Xerials exakta rumsgränser (visade live 2026-08-14):
  ra-topp:      [250,-703,328]  (= RA-spawn)
  ring-gräns:   [479,-421,56]   (56-planet öst; ring-området börjar y >= -421)
  tunnel-gräns: [30,-479,-16]   (tunnelöppningen)
  väst/sng:     [-373,-709,-16] (sng spawns börjar x <= -373)
Målet: in -> upp till ra-topp -> ut. Klipp = topp->gräns (UT) resp gräns->topp (IN).
Skannar ALLA fyra referensinspelningar; rapporterar varje funnet klipp.
Kriterier och klipplogik: granskriterier.py (delad med mätharnesset)."""
import json, os, sys

REF = os.path.expanduser("~/rtx-cost-exp/reference/ra-room")
sys.path.insert(0, REF)
from granskriterier import ticks, klipp

# AVDUBBLETTERAT (terra/grok/kimi-granskning): lab-kopiorna av 11/8-filerna
# ar byteidentiska med gz i reference/ — endast 10/8-sessionen ur lab.
EXTRA = ["/home/xerial/lab/xerial-ra-20260810.jsonl"]
FILES = [
    "xerial-ra-20260810.jsonl.gz",
    "xerial-ra2-20260811.jsonl.gz",
    "xerial-ra-down-20260811.jsonl.gz",
    "xerial-ra-ring-20260811.jsonl.gz",
]

def sample(tk, i0, i1, hz=20):
    out = []
    t0 = tk[i0][0]
    for t, o in tk[i0:i1 + 1]:
        tt = t - t0
        if out and tt - out[-1][0] < 1.0 / hz:
            continue
        out.append([round(tt, 2), [round(o[0], 1), round(o[1], 1), round(o[2], 1)]])
    return out

clips = {"ut": {}, "in": {}}
ALLA = [os.path.join(REF, f) for f in FILES] + EXTRA
for fn in ALLA:
    tk = ticks(fn)
    if not tk:
        print("saknas/tom:", fn)
        continue
    short = os.path.basename(fn).split("-2026")[0].replace("xerial-", "") + os.path.basename(fn)[-11:-9]
    kl = klipp(tk)
    for riktn in ("ut", "in"):
        for name, runs in kl[riktn].items():
            for dt, j, i in runs:
                clips[riktn].setdefault(name, []).append((dt, fn, short, j, i))

export = {}
print("=== UT (ra-topp -> gräns) ===")
for name, runs in clips["ut"].items():
    runs.sort()
    tider = [r[0] for r in runs]
    print("%-10s n=%d  bästa %.2f  alla: %s" % (name, len(runs), runs[0][0],
          [round(x, 1) for x in tider[:12]]))
    # exportera basta + median-runt klipp
    for label, r in [("bästa", runs[0]), ("median", runs[len(runs) // 2])]:
        tk = ticks(r[1])
        key = "[FACIT-NY] UT %s — %.1fs (%s, %s)" % (name, r[0], label, r[2])
        export[key] = sample(tk, r[3], r[4])
print("=== IN (gräns -> ra-topp) ===")
for name, runs in clips["in"].items():
    runs.sort()
    tider = [r[0] for r in runs]
    print("%-10s n=%d  bästa %.2f  alla: %s" % (name, len(runs), runs[0][0],
          [round(x, 1) for x in tider[:12]]))
    for label, r in [("bästa", runs[0]), ("median", runs[len(runs) // 2])]:
        tk = ticks(r[1])
        key = "[FACIT-NY] IN %s — %.1fs (%s, %s)" % (name, r[0], label, r[2])
        export[key] = sample(tk, r[3], r[4])

json.dump(export, open(os.path.expanduser("~/lab/facit_reclip.json"), "w"))
print("exporterade", len(export), "klipp")
