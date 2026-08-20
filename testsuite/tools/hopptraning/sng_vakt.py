#!/usr/bin/env python3
"""SNG-VAKTEN — bot 2 i agarens trebotsuppstallning.

Tva ben, bada mot SNG-megan:
  A  SNG-spawnen  [-880,-232,-16]  -> SNG-megan
  B  liftspawnen  [512, 768, 216]  -> SNG-megan

Uppslag ur kartans entitetslump (hopptraning/dm3_items.py, dm3_plats.py):
  SNG-megan   = item_health spawnflags 2, origin [-720, 80, 160]
                itemets origin svavar; matbar STABAR cell ar 264 [-736, 96, 184]
  SNG-spawnen = info_player_deathmatch [-880,-232,-16] (392 u fran megan)
  liftspawnen = info_player_deathmatch [512, 768, 216] — 148 u fran func_plat *3
                (mitt [512,864,104]); nast narmaste spawn ligger 940 u fran en
                lift, sa identifieringen ar entydig

Domen ar agarens kriterium: inte falla, inte fastna, malen nas. Fall raknas med
husets egen definition (peak_drop_150: dz > 150 fran en lopande topp, aterstalld
vid mark) — samma som patrol.py och obducera anvander.

Agaren vill se RUTTER OCH TIDER per varv, inte bara gront/rott. Darfor skrivs
varje forsoks rutt (icke-walk-ben), tid och avfarter ut och arkiveras.

Ror aldrig 27990. Anvander hoppa.py:s maskineri — ingen ny harness.
"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa

MEGA = [-736.0, 96.0, 184.0]          # cell 264, stabar; itemet svavar pa [-720,80,160]
# Budgetarna ar KALIBRERADE, inte gissade: forsta matningen (bas1, 20/40 s) fangade
# boten mitt i den norra klattringen — z=120 vid y~820 — nar tiden tog slut. Vagen
# upp pa SNG-hyllan gar inte rakt utan runt och uppfor; hyllans 40 infarter kommer
# alla fran z=184/176, ingen fran golvet. Budgeten ar darfor satt efter vad vagen
# faktiskt kostar, med marginal.
BEN = [
    ("sng-spawn", [-880.0, -232.0, -16.0], MEGA, 60.0),
    ("liftspawn", [512.0, 768.0, 216.0], MEGA, 90.0),
]
UT = Path("/home/xerial/hopptraning/sng-vakt")
UT.mkdir(parents=True, exist_ok=True)

# Baslinje fylls i efter matning; tills dess ar den None och vakten RAPPORTERAR
# utan att falla dom (en okalibrerad vakt far inte doma nagot).
BASLINJE = json.loads((UT / "baslinje.json").read_text()) if (UT / "baslinje.json").exists() else None


def peak_drop_150(audit):
    """Husets falldefinition: dz > 150 fran lopande topp, toppen aterstalls vid mark."""
    fall, peak = [], None
    for f in audit:
        o = f.get("origin") or []
        if len(o) < 3:
            continue
        fl = f.get("flags")
        mark = bool(fl & 512) if isinstance(fl, int) else abs((f.get("vel") or [0, 0, 1])[2]) < 1e-6
        if mark:
            peak = o[2]
        elif peak is not None:
            if peak - o[2] > 150.0:
                fall.append([round(v) for v in o])
                peak = None
            else:
                peak = max(peak, o[2])
    return fall



def forsok_med_omutfardande(lab, start, mal, cfg, i, namn):
    """Som hoppa.forsok, men utfardar Goto pa nytt nar boten tappat ordern.

    Utan detta gar boten till sitt eget itemmal sa fort ordern lapsar, och pa ett
    60-90 s-ben hinner det alltid handa. Samma botemedel som patrol.py:s reissue.
    """
    import math
    import time
    lab.events = []
    hoppa.ensure_ready(lab, hoppa.BOT)
    if hoppa.wait_clear(lab, hoppa.BOT, start):
        return {"i": i, "start_namn": namn, "start": start, "klass": "start_blockerad", "audit": []}
    lab.request({"Teleport": {"bot": hoppa.BOT, "pos": list(start), "vel": [0.0, 0.0, 0.0]}})
    lab.request({"Goto": {"bot": hoppa.BOT, "pos": list(mal)}})
    t_audit0 = hoppa.audit_nu(lab, hoppa.BOT)
    rutt0 = hoppa.hamta_rutt(lab, hoppa.BOT)

    t0 = time.monotonic()
    klass, tid_klass, sista, omutfardat = None, None, None, 0
    while True:
        nu = time.monotonic() - t0
        if nu >= cfg["budget_s"]:
            klass, tid_klass = klass or "timeout", tid_klass or nu
            break
        b = hoppa.bot_row(lab, hoppa.BOT)
        if b is None:
            time.sleep(0.05)
            continue
        sista = b
        o = b.get("origin") or [0, 0, 0]
        if not b.get("alive"):
            klass, tid_klass = "fall", nu
            break
        if (b.get("on_ground") and nu > 0.4
                and math.hypot(o[0] - mal[0], o[1] - mal[1]) <= cfg["ankomst_r"]
                and abs(o[2] - mal[2]) <= cfg["ankomst_dz"]):
            klass, tid_klass = "lyckad", nu
            break
        # Ordern tappad? Utfarda den igen — annars vandrar boten till sitt eget mal.
        if b.get("order") not in ("goto",) and omutfardat < 40:
            lab.request({"Goto": {"bot": hoppa.BOT, "pos": list(mal)}})
            omutfardat += 1
        time.sleep(0.05)

    audit = hoppa.audit_slice(lab, hoppa.BOT, t_audit0)
    try:
        lab.request({"Stop": {"bot": hoppa.BOT}})
    except Exception:
        pass
    return {"i": i, "start_namn": namn, "start": start, "mal": mal, "klass": klass,
            "tid_s": round(tid_klass if tid_klass is not None else 0.0, 2),
            "slut_pos": [round(v, 1) for v in (sista or {}).get("origin", start)],
            "omutfardat": omutfardat, "rutt0": rutt0, "audit": audit}


def main():
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    tag = sys.argv[1]
    lab = hoppa.Lab()
    st = lab.status()
    ident = {k: st.get(k) for k in ("map", "cells", "links", "rj_links")}
    print("SNG-VAKT %s   graf %s" % (tag, json.dumps(ident)))

    rader, alla_fall, missade = [], 0, 0
    for namn, start, mal, budget in BEN:
        cfg = {"mal": mal, "ankomst_r": 64.0, "ankomst_dz": 40.0,
               "fall_z": -1e9, "budget_s": budget}   # fall doms av peak_drop_150, inte av z-grans
        for i in range(n):
            r = forsok_med_omutfardande(lab, start, mal, cfg, i + 1, namn)
            fall = peak_drop_150(r.get("audit") or [])
            legs = (r.get("rutt0") or {}).get("legs") or []
            ejwalk = [(g["link"], g["kind"]) for g in legs if g["kind"] != "walk"]
            rad = {"ben": namn, "i": i + 1, "klass": r["klass"], "tid_s": r.get("tid_s"),
                   "slut_pos": r.get("slut_pos"), "fall": fall, "n_ben_i_rutt": len(legs),
                   "icke_walk": ejwalk, "omutfardat": r.get("omutfardat")}
            rader.append(rad)
            alla_fall += len(fall)
            if r["klass"] != "lyckad":
                missade += 1
            print("  %-10s %2d/%d  %-8s tid=%-6s fall=%d  rutt %2d ben, icke-walk %s  slut=%s"
                  % (namn, i + 1, n, r["klass"], r.get("tid_s"), len(fall), len(legs),
                     ejwalk if ejwalk else "-", r.get("slut_pos")))

    natta = sum(1 for r in rader if r["klass"] == "lyckad")
    tider = {}
    for b, _, _, _ in BEN:
        t = [r["tid_s"] for r in rader if r["ben"] == b and r["klass"] == "lyckad"]
        tider[b] = {"n_lyckade": len(t), "median_s": sorted(t)[len(t) // 2] if t else None,
                    "min_s": min(t) if t else None, "max_s": max(t) if t else None}

    print()
    print("SNG-VAKT %s" % tag)
    print("  mal natta : %d/%d" % (natta, len(rader)))
    print("  fall      : %d" % alla_fall)
    for b, v in tider.items():
        print("  tider %-10s lyckade %d  median %s  min %s  max %s"
              % (b, v["n_lyckade"], v["median_s"], v["min_s"], v["max_s"]))

    ut = {"tag": tag, "graf": ident, "natta": natta, "n": len(rader),
          "fall": alla_fall, "tider": tider, "rader": rader}
    if BASLINJE is None:
        ut["dom"] = "OKALIBRERAD"
        print("  DOM       : OKALIBRERAD — baslinje saknas, vakten rapporterar men domer inte")
        kod = 0
    else:
        gron = alla_fall <= BASLINJE["fall"] and natta >= BASLINJE["natta_min"]
        ut["dom"] = "GRON" if gron else "ROD"
        print("  baslinje  : fall <= %d, natta >= %d" % (BASLINJE["fall"], BASLINJE["natta_min"]))
        print("  DOM       : %s" % ut["dom"])
        kod = 0 if gron else 1
    (UT / ("sng-%s.json" % tag)).write_text(json.dumps(ut, indent=1))
    sys.exit(kod)


if __name__ == "__main__":
    main()
