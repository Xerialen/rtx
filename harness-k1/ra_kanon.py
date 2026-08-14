#!/usr/bin/env python3
"""K1-harness: mäter EN fas (rutt) mot RA-rummets kanon (README @ 91a6e34).

Kör N försök: parkering -> teleport till låst start -> predikatverify ->
goto -> pollning av Status (serverns game-time = klocka) -> offlineklipp
med granskriterier.klipp (samma modul som facitklippningen).

Per-försöks-JSONL (valid4b-standarden) + summering med sann median över
lyckade, IQR, n_ok/n_timeout/n_kasserad, fall (fall_def=peak_drop_150)
och tic-vakt (game-dt vs wall-dt, >1 % per försök = ogiltigt försök).

Användning:
  RTX_PORT=27990 python3 ra_kanon.py --phase ut_ring --n 12 --out ~/lab/k1/A
Klippstate är per försök per konstruktion (grok krav 5): varje försök är
en egen inspelning; inga teleporter förekommer inuti en inspelning.
"""
import argparse, json, math, os, sys, time

sys.path.insert(0, "/home/xerial/rtx-tools")
sys.path.insert(0, "/home/xerial/rtx-cost-exp/reference/ra-room")
from labctl import Lab
from granskriterier import klipp, dxy, at_topp, at_ring, at_tunnel, at_vast

BOT = 1
TOPP = [256.0, -704.0, 328.0]
PARK = [237.0, -163.0, 56.0]   # ringsidan, utanför alla predikat (Xerial-punkt)
SETTLE = 0.6

# Låsta koordinater (plan v2 §9.1; ur Xerials spår ~1 s före/efter passage)
PHASES = {
    "ut_ring":   dict(rikt="ut", grans="ring",     start=TOPP,                 mal=[498.0, -304.0, 56.0],   cap=25.0),
    "ut_tunnel": dict(rikt="ut", grans="tunnel",   start=TOPP,                 mal=[-120.0, -424.0, -96.0], cap=25.0),
    "ut_vast":   dict(rikt="ut", grans="väst/sng", start=TOPP,                 mal=[-593.0, -677.0, -16.0], cap=25.0),
    # IN: cap är KLIPPFÖNSTRET (kanonens 25 s), räknat dynamiskt från första
    # gränspassagen; anflygningen har egen gräns approach (terra-k1-review p1)
    "in_ring":   dict(rikt="in", grans="ring",     start=[449.0, -338.0, 56.0], mal=TOPP,                   cap=25.0, approach=7.0),
    "in_tunnel": dict(rikt="in", grans="tunnel",   start=[-120.0, -424.0, -96.0], mal=TOPP,                 cap=25.0, approach=7.0),
    "in_vast":   dict(rikt="in", grans="väst/sng", start=[-593.0, -677.0, -16.0], mal=TOPP,                 cap=25.0, approach=7.0),
}
GRANS_PRED = {"ring": at_ring, "tunnel": at_tunnel, "väst/sng": at_vast}

def bortom(grans, o):
    """Är origin BORTOM gränsen (i grannområdet), inte bara utanför predikatet?"""
    if grans == "ring":
        return o[1] > -410.0
    if grans == "tunnel":
        return o[2] < 20.0 and o[0] < -60.0 and dxy(o, [30, -479]) > 60.0
    return o[0] < -385.0  # väst/sng

def fall_peak_drop_150(pts):
    """fall_def=peak_drop_150: Δz>150 från löpande peak, inget peak_z-golv."""
    peak, falls = -9e9, 0
    for _, o in pts:
        if o[2] > peak:
            peak = o[2]
        elif peak - o[2] > 150.0:
            falls += 1
            peak = o[2]
    return falls

def bot_state(lab):
    s = lab.status()
    b = [x for x in s.get("bots", []) if x["ent"] == BOT]
    return s["time"], (b[0] if b else None)

def forsok(lab, ph, idx, rec_path):
    """Ett försök. Returnerar resultatdict."""
    grans_pred = GRANS_PRED[ph["grans"]]
    # explicit reset: stop -> parkering -> settle -> start -> settle -> verify
    lab.stop(BOT)
    lab.teleport(BOT, PARK); time.sleep(0.4)
    lab.events.clear()
    lab.teleport(BOT, ph["start"]); time.sleep(SETTLE)
    _, b = bot_state(lab)
    if b is None:
        return {"utfall": "kasserad", "skal": "ingen bot"}
    o = b["origin"]
    if ph["rikt"] == "ut":
        if not at_topp(o):
            return {"utfall": "kasserad", "skal": "verify: ej at_topp %s" % o}
    else:
        # start ska vara bortom mål-gränsen; att stå INNE i mål-gränsens zon
        # är ok (väst-zonen är hela sng-korridoren — klippet blir ändå sista
        # zontick -> topp, samma semantik som facitklippen). Aldrig i någon
        # ANNAN gränszon och aldrig på toppen.
        andra = any(p(o) for namn, p in GRANS_PRED.items() if namn != ph["grans"])
        if andra or at_topp(o) or not bortom(ph["grans"], o):
            return {"utfall": "kasserad", "skal": "verify: fel sida %s" % o}
    if not b.get("on_ground", True):
        time.sleep(0.5)
        _, b = bot_state(lab)
        if b is None or not b.get("on_ground", True):
            return {"utfall": "kasserad", "skal": "verify: ej på marken"}

    rows = []
    t_game0, b = bot_state(lab)
    w0 = time.monotonic()
    rows.append((t_game0, b["origin"], b.get("on_ground"), 0.0))
    lab.goto(BOT, ph["mal"])
    arrived = [False]
    utfall, t_hit = "timeout", None
    grans_forsta = None   # första gränspassagen (IN): klippfönstret startar här
    while True:
        for ev in lab.events:
            k = next(iter(ev)) if isinstance(ev, dict) else str(ev)
            if k == "Arrived":
                f = ev[k] if isinstance(ev, dict) else {}
                if f.get("bot", BOT) == BOT:
                    arrived[0] = True
        lab.events.clear()
        tg, b = bot_state(lab)
        w = time.monotonic() - w0
        if b is None:
            utfall = "kasserad"
            break
        o = b["origin"]
        rows.append((tg, o, b.get("on_ground"), round(w, 4)))
        if ph["rikt"] == "ut" and grans_pred(o):
            utfall, t_hit = "ok", tg
            break
        if ph["rikt"] == "in" and at_topp(o):
            utfall, t_hit = "ok", tg
            break
        if arrived[0] and ph["rikt"] == "ut" and not grans_pred(o):
            utfall = "arrived_utan_klipp"
            break
        if ph["rikt"] == "in":
            if grans_forsta is None and grans_pred(o):
                grans_forsta = tg
            if grans_forsta is None and tg - t_game0 > ph["approach"]:
                utfall = "kasserad_anflygning"   # nådde aldrig gränsen
                break
            if grans_forsta is not None and tg - grans_forsta > ph["cap"]:
                break   # kanonens 25 s-fönster slut = timeout
        elif tg - t_game0 > ph["cap"]:
            break
    # kort svans för rent klipp
    t_tail = time.monotonic() + 0.3
    while time.monotonic() < t_tail:
        tg, b = bot_state(lab)
        if b is not None:
            rows.append((tg, b["origin"], b.get("on_ground"),
                         round(time.monotonic() - w0, 4)))
    lab.stop(BOT)

    with open(rec_path, "w") as f:
        for tg, o, g, w in rows:
            f.write(json.dumps({"t": tg, "wall": w, "players": [
                {"ent": BOT, "origin": [round(v, 2) for v in o],
                 "on_ground": g}]}, separators=(",", ":")) + "\n")

    pts = [(tg, o) for tg, o, _, _ in rows]
    game_dt = pts[-1][0] - pts[0][0]
    wall_dt = rows[-1][3] - rows[0][3]
    drift = (game_dt / wall_dt - 1.0) * 100 if wall_dt > 0.5 else 0.0
    hz = round((len(rows) - 1) / wall_dt, 1) if wall_dt > 0 else 0
    max_step = max((math.dist(pts[k][1], pts[k + 1][1])
                    for k in range(len(pts) - 1)), default=0.0)

    kl = klipp(pts)
    dt = None
    tider = [c[0] for c in kl[ph["rikt"]].get(ph["grans"], [])]
    if tider:
        dt = tider[0]
        if len(tider) > 1:
            utfall = "kasserad"
    ovriga = {r: {g: [c[0] for c in cs] for g, cs in kl[r].items()
                  if not (r == ph["rikt"] and g == ph["grans"])}
              for r in ("ut", "in")}
    ovriga = {r: g for r, g in ovriga.items() if g}
    res = {"utfall": utfall, "klipp_s": dt, "falls": fall_peak_drop_150(pts),
           "tic_drift_pct": round(drift, 2), "poll_hz": hz,
           "max_steg_u": round(max_step, 1), "n_rader": len(rows),
           "fil": os.path.basename(rec_path)}
    if utfall == "ok" and dt is None:
        # nådde målet men inget klipp: antingen >25s-taket (timeout enligt
        # kanonen) eller missad gränspassage i samplingen (kasserad)
        passage = any(GRANS_PRED[ph["grans"]](o) for _, o in pts)
        if ph["rikt"] == "in" and passage:
            res["utfall"] = "timeout_klipp"; res["skal"] = "passage->topp över 25s-taket"
        else:
            res["utfall"] = "kasserad"; res["skal"] = "predikat träffat men inget klipp emitterat (samplingsmiss?)"
    if abs(drift) > 1.0:
        res["utfall"] = "ogiltig_tic"; res["skal"] = "tic-drift %.2f%%" % drift
    if max_step > 300:
        res["utfall"] = "kasserad"; res["skal"] = "diskontinuitet %du" % round(max_step)
    if ovriga:
        res["anomali_klipp"] = ovriga
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=sorted(PHASES))
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", type=float, default=300.0,
                    help="fasbudget i s väggklocka; nås den startas inga nya försök (fasen INKOMPLETT)")
    args = ap.parse_args()
    ph = PHASES[args.phase]
    outdir = os.path.join(os.path.expanduser(args.out), args.phase)
    os.makedirs(outdir, exist_ok=True)
    lab = Lab(port=int(os.environ.get("RTX_PORT", "27990")))
    lab.set("rtx_telemetry", "1")

    resultat = []
    t_fas0 = time.monotonic()
    for i in range(args.n):
        if time.monotonic() - t_fas0 > args.budget:
            break
        rec = os.path.join(outdir, "attempt_%02d.jsonl" % (i + 1))
        r = forsok(lab, ph, i + 1, rec)
        r["nr"] = i + 1
        resultat.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)

    ok = sorted(r["klipp_s"] for r in resultat if r["utfall"] == "ok")
    def q(xs, f):
        if not xs:
            return None
        k = (len(xs) - 1) * f
        lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
        return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 2)
    summ = {"fas": args.phase, "N_planerat": args.n, "n_attempt": len(resultat),
            "inkomplett": len(resultat) < args.n,
            "n_ok": len(ok),
            "n_timeout": sum(r["utfall"] in ("timeout", "timeout_klipp") for r in resultat),
            "n_kasserad": sum(r["utfall"] in ("kasserad", "arrived_utan_klipp",
                                              "kasserad_anflygning") for r in resultat),
            "n_ogiltig_tic": sum(r["utfall"] == "ogiltig_tic" for r in resultat),
            "falls_tot": sum(r.get("falls", 0) for r in resultat),
            "basta": ok[0] if ok else None,
            "median": q(ok, 0.5), "iqr": [q(ok, 0.25), q(ok, 0.75)],
            "alla_ok": ok, "fall_def": "peak_drop_150",
            "koordinater": {"start": ph["start"], "mal": ph["mal"], "cap": ph["cap"]},
            "forsok": resultat}
    with open(os.path.join(outdir, "summary.json"), "w") as f:
        json.dump(summ, f, ensure_ascii=False, indent=1)
    print("== %s: %d/%d ok, median %s, bästa %s, fall %d" % (
        args.phase, summ["n_ok"], summ["n_attempt"], summ["median"],
        summ["basta"], summ["falls_tot"]), flush=True)

if __name__ == "__main__":
    main()
