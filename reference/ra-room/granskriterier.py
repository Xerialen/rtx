"""RA-rummets kanoniska gränskriterier och klipplogik (Xerial-godkända
2026-08-14, se README.md @ 91a6e34). ENDA implementationen — facit_reclip.py
och mätharnesset (ra_kanon.py) importerar härifrån; dubbelimplementera ALDRIG.

Klipp: UT = sista topp-tick -> första gränspassage; IN = sista gränspassage
-> första topp-tick; 25 s-tak; en emission per toppbesök."""
import gzip, json, math

RA_TOPP = [250, -703, 328]

def dxy(o, p):
    return math.hypot(o[0] - p[0], o[1] - p[1])

def at_topp(o):    return o[2] >= 320 and dxy(o, [250, -703]) < 70
# skarpta efter granskning: ring-dorren vid x~479 (grok a), vast kraver
# passage i den visade korridoren, inte hela halvplanet (deepseek/kimi)
def at_ring(o):    return 40 <= o[2] <= 90 and o[0] > 450 and o[1] >= -421
def at_tunnel(o):  return o[2] < 20 and dxy(o, [30, -479]) < 48
def at_vast(o):    return o[2] < 20 and o[0] <= -373 and abs(o[1] + 709) < 80

BORDERS = [("ring", at_ring), ("tunnel", at_tunnel), ("väst/sng", at_vast)]

def ticks(path, ent=2):
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
            if p.get("ent") == ent:
                out.append((d["t"], p["origin"]))
    return out

def klipp(tk, cap=25, max_hopp=None):
    """Klipp en tick-serie [(t, origin), ...] mot kanonens gränser.
    Returnerar {"ut": {gräns: [(dt, i0, i1), ...]}, "in": {...}} där
    i0/i1 är index i tk. max_hopp (u): förkasta klipp som innehåller en
    positionsdiskontinuitet större än så mellan två ticks (teleport-vakt;
    None = av, facit-klippningens historiska beteende)."""
    def _ren(i0, i1):
        if max_hopp is None:
            return True
        return all(math.dist(tk[k][1], tk[k + 1][1]) <= max_hopp
                   for k in range(i0, i1))
    clips = {"ut": {}, "in": {}}
    last_topp = None
    last_border = {}
    ut_open = {name: False for name, _ in BORDERS}
    in_open = False
    for i, (t, o) in enumerate(tk):
        if at_topp(o):
            if not in_open:
                for name in list(last_border):
                    j, tj = last_border[name]
                    if t - tj < cap and _ren(j, i):
                        clips["in"].setdefault(name, []).append(
                            (round(t - tj, 2), j, i))
                last_border = {}
                in_open = True
            last_topp = (i, t)
            for name, _ in BORDERS:
                ut_open[name] = True
            continue
        in_open = False
        for name, crit in BORDERS:
            if crit(o):
                if last_topp and ut_open[name] and t - last_topp[1] < cap \
                        and _ren(last_topp[0], i):
                    clips["ut"].setdefault(name, []).append(
                        (round(t - last_topp[1], 2), last_topp[0], i))
                    ut_open[name] = False
                last_border[name] = (i, t)
    return clips
