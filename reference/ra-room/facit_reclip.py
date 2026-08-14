"""Omklippning av facit mot Xerials exakta rumsgränser (visade live 2026-08-14):
  ra-topp:      [250,-703,328]  (= RA-spawn)
  ring-gräns:   [479,-421,56]   (56-planet öst; ring-området börjar y >= -421)
  tunnel-gräns: [30,-479,-16]   (tunnelöppningen)
  väst/sng:     [-373,-709,-16] (sng spawns börjar x <= -373)
Målet: in -> upp till ra-topp -> ut. Klipp = topp->gräns (UT) resp gräns->topp (IN).
Skannar ALLA fyra referensinspelningar; rapporterar varje funnet klipp."""
import gzip, json, math, os

REF = os.path.expanduser("~/rtx-cost-exp/reference/ra-room")
# AVDUBBLETTERAT (terra/grok/kimi-granskning): lab-kopiorna av 11/8-filerna
# ar byteidentiska med gz i reference/ — endast 10/8-sessionen ur lab.
EXTRA = ["/home/xerial/lab/xerial-ra-20260810.jsonl"]
FILES = [
    "xerial-ra-20260810.jsonl.gz",
    "xerial-ra2-20260811.jsonl.gz",
    "xerial-ra-down-20260811.jsonl.gz",
    "xerial-ra-ring-20260811.jsonl.gz",
]

def ticks(path):
    out = []
    op = gzip.open if path.endswith(".gz") else open
    try:
        f = op(path, "rt")
    except FileNotFoundError:
        return out
    for line in f:
        try:
            d = json.loads(line)
        except Exception:
            continue
        for p in d.get("players", []):
            if p.get("ent") == 2:
                out.append((d["t"], p["origin"]))
    return out

def dxy(o, p):
    return math.hypot(o[0] - p[0], o[1] - p[1])

def at_topp(o):    return o[2] >= 320 and dxy(o, [250, -703]) < 70
# skarpta efter granskning: ring-dorren vid x~479 (grok a), vast kraver
# passage i den visade korridoren, inte hela halvplanet (deepseek/kimi)
def at_ring(o):    return 40 <= o[2] <= 90 and o[0] > 450 and o[1] >= -421
def at_tunnel(o):  return o[2] < 20 and dxy(o, [30, -479]) < 48
def at_vast(o):    return o[2] < 20 and o[0] <= -373 and abs(o[1] + 709) < 80

BORDERS = [("ring", at_ring), ("tunnel", at_tunnel), ("väst/sng", at_vast)]

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
    # UT: sista topp-tick fore forsta granspassage; IN: sista granspassage fore topp
    last_topp = None
    last_border = {}
    ut_open = {name: False for name, _ in BORDERS}   # stangs vid emitterat klipp tills ny topp
    in_open = False
    for i, (t, o) in enumerate(tk):
        if at_topp(o):
            if not in_open:
                # IN-klipp: fran senaste granspassage till nu
                for name in list(last_border):
                    j, tj = last_border[name]
                    if t - tj < 25:
                        clips["in"].setdefault(name, []).append(
                            (round(t - tj, 2), fn, short, j, i))
                last_border = {}
                in_open = True
            last_topp = (i, t)
            for name, _ in BORDERS:
                ut_open[name] = True
            continue
        in_open = False
        for name, crit in BORDERS:
            if crit(o):
                if last_topp and ut_open[name] and t - last_topp[1] < 25:
                    clips["ut"].setdefault(name, []).append(
                        (round(t - last_topp[1], 2), fn, short, last_topp[0], i))
                    ut_open[name] = False
                last_border[name] = (i, t)

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
