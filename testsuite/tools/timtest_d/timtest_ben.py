#!/usr/bin/env python3
"""T1h TIMTEST — benmätaren (kedjad regim).

Mäter ENA ben ur rörelse (KONTINUERLIG drift, INTE teleport-protokollet).
En cykel = sex ben i ordning; varje ben startar ur föregående bens slutläge
(INGEN teleport på friska ben). Felet på ett ben (fall eller fastnad)
bokförs på det benet och boten teleporteras till NÄSTA bens start — nästa
bens `start`-flagga sätts då till `teleport_efter_fel` (REVISION 1, ds 4 +
grok 7: teleport-till-nästa-behålls, stratifiering är tvingande).

Mål:
  * UT-ben: kanongränserna i granskriterier.py — ÅTERANVÄNDS, klipplogiken
    dupliceras ALDRIG.
  * IN-ben: NYA topp-vid-målet (z≥320 OCH dxy(toppcentrum [250,-703])≤130)
    med kvarvaro = ≥15 KONSEKUTIVA ticks (REVISION 1, ds 1a/1b).

Cap 25 s utan mål = fastnad (i nämnaren). peak_drop_150 = fall; IN-ben
räknas som felsignal, UT-bens avsedda nedhopp undantas (som i K2).

Per-frame JSONL, EN FIL per ben×cykel×arm (samma radformat som K2-rådata).
Ben-metadata: {start: kedjad|teleport_efter_fel, utfall, tid, falls}.

Användning (körs under taskset av orkestern; INGA rigg-anrop här):
  RTX_PORT=27990 python3 timtest_ben.py --arm A --out ~/lab/t1h/A
"""
import argparse
import json
import math
import os
import sys
import time

# granskriterier ÅTERANVÄNDS (klipplogiken dupliceras aldrig) — samma sökväg
# som k1_orkester.py / ra_kanon.py pekar på.
sys.path.insert(0, "/home/xerial/rtx-tools")
sys.path.insert(0, "/home/xerial/rtx-cost-exp/reference/ra-room")
from labctl import Lab
from granskriterier import dxy, at_topp, at_ring, at_tunnel, at_vast

BOT = 1
TOPP = [250.0, -703.0, 328.0]

# Nya IN-målet (Xerials order 2026-08-15, REVISION 1): "topp-vid".
# Framme = första tick med z≥320 OCH dxy(toppcentrum [250,-703])≤130, med
# kvarvaro ≥ IN_KONSEQ_TICKS konsekutiva ticks (≈0,3 s @ ~49 Hz). Radie 130
# + 0,3 s STÅR (1b-spotcheck: västkantens vilofläck dxy≈100, längsta
# konsekutiva kvarvaro 83 s ⇒ transienta max 190 under klättring filtreras).
IN_TOPP_CENTRUM = [250.0, -703.0]
IN_Z_MIN = 320.0
IN_RADIE = 130.0
IN_KONSEQ_TICKS = 15

# Cykelordning (planens "en cykel = ..."); UT-ben först, IN-ben tillbaka.
CYKEL = ["ut_ring", "in_ring", "ut_tunnel", "in_tunnel", "ut_vast", "in_vast"]

# Per ben: rikt, gräns-namn (kanon), mal (goto-mål), start (teleport-läge
# efter fel), cap (25 s = fastnad), ut_undanta (avsett nedhopp undantas).
BEN = {
    # UT: start på toppen, mal utanför gränsen; gränspassage = framme.
    "ut_ring":   dict(rikt="ut", grans="ring",     mal=[498.0, -304.0, 56.0],
                      start=TOPP, cap=25.0, undanta=True),
    "in_ring":   dict(rikt="in", grans="ring",     mal=TOPP,
                      start=[449.0, -338.0, 56.0], cap=25.0, undanta=False),
    "ut_tunnel": dict(rikt="ut", grans="tunnel",   mal=[-120.0, -424.0, -96.0],
                      start=TOPP, cap=25.0, undanta=True),
    "in_tunnel": dict(rikt="in", grans="tunnel",   mal=TOPP,
                      start=[-120.0, -424.0, -96.0], cap=25.0, undanta=False),
    "ut_vast":   dict(rikt="ut", grans="väst/sng", mal=[-593.0, -677.0, -16.0],
                      start=TOPP, cap=25.0, undanta=True),
    "in_vast":   dict(rikt="in", grans="väst/sng", mal=TOPP,
                      start=[-593.0, -677.0, -16.0], cap=25.0, undanta=False),
}
GRANS_PRED = {"ring": at_ring, "tunnel": at_tunnel, "väst/sng": at_vast}


def in_topp_vid(o):
    """NYA IN-predikatet: topp-vid. z≥320 OCH dxy(toppcentrum)≤130."""
    return o[2] >= IN_Z_MIN and dxy(o, IN_TOPP_CENTRUM) <= IN_RADIE


def in_topp_vid_stadig(pts, n=IN_KONSEQ_TICKS):
    """Längsta LÖPANDE sträng av ticks inom topp-vid-målet, och index (i0,i1)
    på den förstrången som uppnår ≥n konsekutiva (eller None)."""
    best_i0 = best_i1 = None
    cur_i0 = None
    for i, (_, o) in enumerate(pts):
        if in_topp_vid(o):
            if cur_i0 is None:
                cur_i0 = i
            cur_i1 = i
            if cur_i1 - cur_i0 + 1 >= n and best_i0 is None:
                best_i0, best_i1 = cur_i0, i
        else:
            cur_i0 = None
    return best_i0, best_i1


def fall_peak_drop_150(pts, undanta_ut=False):
    """peak_drop_150: Δz>150 från löpande peak. UT-bens avsedda nedhopp
    undantas (undanta_ut) som i K2; IN-ben räknas alltid som felsignal."""
    peak, falls = -9e9, 0
    for _, o in pts:
        if o[2] > peak:
            peak = o[2]
        elif peak - o[2] > 150.0 and not undanta_ut:
            falls += 1
            peak = o[2]
    return falls


class KontrollportDod(Exception):
    """Kontrollporten svarar inte (timeout/frusen) — benet kasseras, ingen evig loop."""

def bot_state(lab, timeout=8.0):
    """status() med timeout; TimeoutError/ConnectionError => KontrollportDod
    (F4/F10: frusen kontrollport => kasserat ben, inte häng)."""
    try:
        s = lab.request("Status", timeout=timeout)["Status"]
    except (TimeoutError, ConnectionError, OSError) as exc:
        raise KontrollportDod(str(exc))
    b = [x for x in s.get("bots", []) if x["ent"] == BOT]
    return s["time"], (b[0] if b else None)


def ben_forsok(lab, ben, idx, rec_path, start_mode):
    """Kör ENA ben ur rörelse och skriver per-frame JSONL.

    Returnerar ben-metadata {start, utfall, tid, falls, ...}.
    `start_mode` = "kedjad" | "teleport_efter_fel" (sätts av orkestern).
    Tic-vakt (F3): game-dt vs wall-dt; >1 % => benet = ogiltig_tic."""
    spec = BEN[ben]
    rikt = spec["rikt"]
    grans = spec["grans"]
    grans_pred = GRANS_PRED[grans]

    rows = []
    try:
        t_game0, b = bot_state(lab)
    except KontrollportDod as exc:
        return _meta(start_mode, "kasserad", None, 0, "kontrollport: %s" % exc)
    if b is None:
        return _meta(start_mode, "kasserad", None, 0, "ingen bot")
    w0 = time.monotonic()
    rows.append((t_game0, b["origin"], b.get("on_ground"), 0.0))

    try:
        lab.goto(BOT, spec["mal"])
    except (TimeoutError, ConnectionError, OSError) as exc:
        return _meta(start_mode, "kasserad", None, 0, "goto: %s" % exc)
    utfall, t_hit = "fastnad", None
    in_i0 = in_i1 = None
    # Cap räknas ur rörelse (kedjad) — för IN startar fönstret vid första
    # gränspassagen (approachen har ingen egen gräns i kedjad regim; boten
    # är redan på väg in). 25 s utan mål = fastnad.
    approach_done = (rikt == "ut")
    t_approach = None
    streak = 0
    streak_i0 = None
    while True:
        try:
            tg, b = bot_state(lab)
        except KontrollportDod:
            utfall = "kasserad"
            skal = "kontrollport svarade inte i benloopen"
            break
        w = time.monotonic() - w0
        if b is None:
            utfall = "kasserad"
            break
        o = b["origin"]
        rows.append((tg, o, b.get("on_ground"), round(w, 4)))

        if rikt == "ut":
            if grans_pred(o):
                utfall, t_hit = "framme", tg
                break
            if tg - t_game0 > spec["cap"]:
                utfall, t_hit = "fastnad", tg
                break
        else:
            # IN: gränsen passeras på vägen in; topp-vid-målet = framme.
            # F4: cap:en gäller från t_game0 ÄVEN om gränsen aldrig
            # passeras — annars evig loop + hängd arm + kvarliggande riglock.
            if tg - t_game0 > spec["cap"]:
                utfall, t_hit = "fastnad", tg
                break
            if not approach_done and grans_pred(o):
                approach_done = True
                t_approach = tg
            if approach_done:
                if in_topp_vid(o):
                    streak = streak + 1 if streak_i0 is not None else 1
                    if streak_i0 is None:
                        streak_i0 = len(rows) - 1
                    if streak >= IN_KONSEQ_TICKS:
                        utfall = "framme"
                        # tid = första tick av den bekräftade strängen
                        t_hit = rows[streak_i0][0]
                        in_i0, in_i1 = streak_i0, len(rows) - 1
                        break
                else:
                    streak = 0
                    streak_i0 = None
                # sekundärt: gräns-till-mål-fönstret (kanonens 25 s)
                if tg - (t_approach if t_approach is not None else t_game0) > spec["cap"]:
                    utfall, t_hit = "fastnad", tg
                    break

    # kort svans för rent klipp (samma som ra_kanon)
    t_tail = time.monotonic() + 0.3
    while time.monotonic() < t_tail:
        try:
            tg, b = bot_state(lab)
        except KontrollportDod:
            b = None
        if b is not None:
            rows.append((tg, b["origin"], b.get("on_ground"),
                         round(time.monotonic() - w0, 4)))
    try:
        lab.stop(BOT)
    except (TimeoutError, ConnectionError, OSError, KontrollportDod):
        pass

    with open(rec_path, "w") as f:
        for tg, o, g, w in rows:
            f.write(json.dumps({"t": tg, "wall": w, "players": [
                {"ent": BOT, "origin": [round(v, 2) for v in o],
                 "on_ground": g}]}, separators=(",", ":")) + "\n")

    pts = [(tg, o) for tg, o, _, _ in rows]
    # tid mot målet:
    #  UT = första gränspassagen (kanongräns, återanvänd)
    #  IN = första tick av den bekräftade ≥15-ticks strängen (topp-vid)
    tid = None
    if utfall == "framme":
        if rikt == "ut":
            for i, (_, o) in enumerate(pts):
                if grans_pred(o):
                    tid = round(pts[i][0] - t_game0, 3)
                    break
        else:
            if in_i0 is not None:
                tid = round(pts[in_i0][0] - t_game0, 3)

    falls = fall_peak_drop_150(pts, undanta_ut=spec["undanta"])
    # KASSERAD vinner över allt (utfallsprioritet, mini-varv 3 / F13 + tillägg):
    #   kasserad > ogiltig_tic > {fall, fall_efter_framme, fastnad,
    #   fall_plus_fastnad} > framme
    # Ett kasserat ben (ofullständig data, t.ex. kontrollport/frusen port)
    # omklassas ALDRIG — ej till "fall" (falls>0) och ej till ogiltig_tic.
    kasserad = (utfall == "kasserad")

    # utfallsläge enligt grok 8 (låst) — fall efter framme = miss.
    # (Kasserade ben skippar omklassning — ofullständig data.)
    if not kasserad:
        if falls > 0 and utfall == "framme":
            utfall = "fall_efter_framme"      # miss (täljarens nämnare, ej täljare)
        if falls > 0 and utfall == "fastnad":
            utfall = "fall_plus_fastnad"       # EN miss
        if falls > 0 and utfall not in ("fall_efter_framme", "fall_plus_fastnad"):
            utfall = "fall"                    # fall utan framme

    # TIC-VAKT (F3, rev 1: «tic-vakten är domare»): game-dt vs wall-dt.
    # >1 % => benet = ogiltig_tic (exkluderas ur täljare OCH nämnare).
    game_dt = pts[-1][0] - pts[0][0] if len(pts) > 1 else 0.0
    wall_dt = rows[-1][3] - rows[0][3] if len(rows) > 1 else 0.0
    drift_pct = round((game_dt / wall_dt - 1.0) * 100.0, 2) if wall_dt > 0.5 else 0.0
    # ogiltig_tic vinner över fall/fastnad/framme, MEN EJ över kasserad (F13).
    ogiltig_tic = (abs(drift_pct) > 1.0) and not kasserad

    meta = _meta(start_mode, utfall, tid, falls)
    meta["t_hit"] = round(t_hit, 3) if t_hit is not None else None
    meta["n_rader"] = len(rows)
    meta["max_steg_u"] = round(max(
        (math.dist(pts[k][1], pts[k + 1][1]) for k in range(len(pts) - 1)),
        default=0.0), 1)
    meta["tic_drift_pct"] = drift_pct
    meta["game_dt_s"] = round(game_dt, 3)
    meta["wall_dt_s"] = round(wall_dt, 3)
    # bevara skäl från loopen (kontrollport/fastnad)
    if locals().get("skal") and not meta.get("skal"):
        meta["skal"] = skal
    if ogiltig_tic:
        utfall = "ogiltig_tic"
        meta["utfall"] = utfall
        meta["skal"] = meta.get("skal") or ("tic-drift %.2f%% > 1%%" % drift_pct)
    return meta


def _meta(start, utfall, tid, falls, skal=None):
    return {"start": start, "utfall": utfall, "tid": tid, "falls": falls,
            "skal": skal, "tic_drift_pct": None}


def koda_arm(lab, arm, outdir, minuter, dry=False, logf=None):
    """Kör ben efter ben, cykel efter cykel, i kedjad regim, i `minuter`
    väggklocka (eller 1 cykel i --dry). Skriver JSONL per ben×cykel×arm."""
    if logf is None:
        logf = sys.stdout
    t0 = time.monotonic()
    cykel = 0
    kedjad = True                      # första benet i första cykeln är kedjad
    while True:
        if not dry and (time.monotonic() - t0) / 60.0 >= minuter:
            break                      # pågående cykel kastas (trunkering)
        cykel += 1
        cykeldir = os.path.join(outdir, "c%03d" % cykel)
        os.makedirs(cykeldir, exist_ok=True)
        for j, ben in enumerate(CYKEL):
            rec = os.path.join(cykeldir, "%s.jsonl" % ben)
            start_mode = "kedjad" if kedjad else "teleport_efter_fel"
            meta = ben_forsok(lab, ben, cykel, rec, start_mode)
            meta["cykel"] = cykel
            meta["ben"] = ben
            with open(rec.replace(".jsonl", "_meta.json"), "w") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
            print(json.dumps(meta, ensure_ascii=False), flush=True)
            # kedjningen: nästa ben är kedjad OMVISSA det här benet lyckades
            # (framme utan fall). Annars teleport till nästa bens start.
            lyckades = meta["utfall"] == "framme" and meta["falls"] == 0
            if lyckades:
                kedjad = True
            else:
                # TELEPORT till nästa bens start (REVISION 1, ds 4). Nästa
                # ben körs ur stillastående = teleport_efter_fel.
                # F12: teleporten skyddas som goto/status — frusen port =>
                # kasserad nästa ben + bryt cykeln (armen kraschar ej).
                try:
                    lab.teleport(BOT, BEN[CYKEL[(j + 1) % len(CYKEL)]]["start"])
                except (TimeoutError, ConnectionError, OSError, KontrollportDod) as exc:
                    # frusen kontrollport: skriv kasserad-meta för nästa ben
                    # (ofullständig data) och bryt cykeln.
                    nxt = CYKEL[(j + 1) % len(CYKEL)]
                    nrec = os.path.join(cykeldir, "%s.jsonl" % nxt)
                    nmeta = _meta("teleport_efter_fel", "kasserad", None, 0,
                                   "teleport: %s" % exc)
                    nmeta["cykel"] = cykel
                    nmeta["ben"] = nxt
                    try:
                        with open(nrec.replace(".jsonl", "_meta.json"), "w") as f:
                            json.dump(nmeta, f, ensure_ascii=False, indent=1)
                    except OSError:
                        pass
                    print(json.dumps(nmeta, ensure_ascii=False), flush=True)
                    return cykel
                time.sleep(0.6)
                kedjad = False
        if dry:
            break
    return cykel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["A", "B"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--minuter", type=float, default=60.0)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    outdir = os.path.join(os.path.expanduser(args.out), args.arm)
    os.makedirs(outdir, exist_ok=True)
    lab = Lab(port=int(os.environ.get("RTX_PORT", "27990")))
    lab.set("rtx_telemetry", "1")
    # starta på toppen (cykeln börjar med ut_ring ur rörelse från toppen)
    lab.teleport(BOT, TOPP)
    time.sleep(0.6)
    n = koda_arm(lab, args.arm, outdir, args.minuter, dry=args.dry)
    print("arm %s klar: %d cykler" % (args.arm, n), flush=True)


if __name__ == "__main__":
    main()
