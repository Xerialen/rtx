#!/usr/bin/env python3
"""Fix i SNG-vakten: utfarda Goto pa nytt nar boten tappar ordern.

Matt: pa ett 40 s-ben var botens route_goal cell 501 [-544,808,120] i 1517 av
2000 bilder — aldrig malcellen 264. Malet omdirigerades inte (route_goal ==
target hela tiden); boten hade helt enkelt slutat lyda Goto och gatt till sitt
EGET itemmal. Med 15 s-budgetar i hoppvarven hinner det aldrig hanta, men SNG-
benen ar 60-90 s.

patrol.py loser samma sak: "Reissue om ordern tappades (hold/None = ingen aktiv
order)", upp till 8 ganger. SNG-vakten far samma logik — i sin EGEN forsoksloop,
sa att hoppa.py (harnessen som domer hoppvarven) lamnas orord mitt i kampanjen.
"""
import pathlib

p = pathlib.Path("/home/xerial/hopptraning/sng_vakt.py")
s = p.read_text()

gammal = """        for i in range(n):
            r = hoppa.forsok(lab, hoppa.BOT, start, mal, cfg, i + 1, namn)"""
ny = """        for i in range(n):
            r = forsok_med_omutfardande(lab, start, mal, cfg, i + 1, namn)"""
assert s.count(gammal) == 1
s = s.replace(gammal, ny, 1)

hjalp = '''

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

'''
s = s.replace("\ndef main():", hjalp + "\ndef main():", 1)
# bokfor omutfardandet i raden
s = s.replace('''                   "icke_walk": ejwalk}''',
              '''                   "icke_walk": ejwalk, "omutfardat": r.get("omutfardat")}''', 1)
p.write_text(s)
print("SNG-vakten utfardar nu Goto pa nytt; hoppa.py orord")
