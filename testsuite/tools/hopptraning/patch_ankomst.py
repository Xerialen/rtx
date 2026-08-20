#!/usr/bin/env python3
"""MATAPPARAT: doem facitets ankomstpredikat pa MOTORNS bilder, inte pa min avlasare.

Hopp 2 avslojade en defekt i apparaten, inte i boten. Vid teleportutgangen ar man
i luften i ~300 u/s. Agarens EGEN demo (ring2quad1, facitkallan) far sin forsta
markkontakt 54,6 u fran malet och ar utanfor 64 u redan 0,03 s senare — ett
fonster pa tre bilder. En avlasare pa 20 Hz flyttar boten 15 u per prov och
missar det fonstret nastan alltid. Alla tio forsok i hopp 2:s forsta korning
blev timeout trots att boten gick ut genom teleporten.

Predikatet ar OFORANDRAT — det ar samma facit, ord for ord. Det som andras ar
var det lases av: motorns egen flygregistrator, ~97 bilder i sekunden, som
harnessen redan hamtar per forsok. Markkontakt lases ur registratorns
`flags & FL_ONGROUND`; saknas flaggan anvands vz == 0, som i alla granskade band
sammanfaller exakt med golvhojd.

Fallet doms fortfarande fore ankomsten: dyker banan under fall_z innan malet ar
natt ar forsoket ett fall.
"""
import pathlib

p = pathlib.Path("/home/xerial/hopptraning/hoppa.py")
s = p.read_text()

# 1) Registratorn maste bara flaggan.
old = '''AUDIT_FALT = ("t", "origin", "vel", "speed", "peak", "bhop", "hops", "leg", "runup", "wp", "lip",
              "cell", "target", "route_goal", "route_len", "route_pos", "band", "frozen",
              "air", "posture", "gate", "goal_cell", "commit")'''
new = '''AUDIT_FALT = ("t", "origin", "vel", "speed", "peak", "bhop", "hops", "leg", "runup", "wp", "lip",
              "cell", "target", "route_goal", "route_len", "route_pos", "band", "frozen",
              "air", "posture", "gate", "goal_cell", "commit", "flags")

FL_ONGROUND = 512


def pa_mark(f):
    """Markkontakt i en registratorbild: FL_ONGROUND om flaggan finns, annars vz == 0."""
    fl = f.get("flags")
    if isinstance(fl, int):
        return bool(fl & FL_ONGROUND)
    v = f.get("vel") or [0.0, 0.0, 1.0]
    return abs(v[2]) < 1e-6


def bedom_ur_registrator(audit, mal, cfg):
    """Facitets predikat, last pa motorns egna bilder i stallet for min avlasare.

    Returnerar (klass, tid) dar klass ar "lyckad", "fall" eller None. Fallet doms
    fore ankomsten: dyker banan under fall_z innan malet ar natt ar det ett fall.
    """
    if not audit:
        return None, None
    t0 = audit[0]["t"]
    for f in audit:
        o = f.get("origin") or []
        if len(o) < 3:
            continue
        dt = f["t"] - t0
        if o[2] < cfg["fall_z"]:
            return "fall", round(dt, 2)
        if (dt > 0.4 and pa_mark(f)
                and hdist(o, mal) <= cfg["ankomst_r"]
                and abs(o[2] - mal[2]) <= cfg["ankomst_dz"]):
            return "lyckad", round(dt, 2)
    return None, None'''
assert s.count(old) == 1
s = s.replace(old, new, 1)

# 2) Klassningen laser registratorn nar den live-avlasta domen inte ar "lyckad".
old2 = '''    tid = round(tid_klass if tid_klass is not None else (time.monotonic() - t0), 2)
    audit = audit_slice(lab, bot, t_audit0)'''
new2 = '''    tid = round(tid_klass if tid_klass is not None else (time.monotonic() - t0), 2)
    audit = audit_slice(lab, bot, t_audit0)
    # Facitet doms pa motorns bilder. Min 20 Hz-avlasare far bara avgora NAR
    # forsoket slutar — den ar for grov for att avgora OM malet natts.
    reg_klass, reg_tid = bedom_ur_registrator(audit, mal, cfg)
    if reg_klass is not None and klass != reg_klass:
        klass, tid = reg_klass, reg_tid
    elif reg_klass is None and klass == "lyckad":
        # Avlasaren sag en ankomst registratorn inte bekraftar — registratorn galler.
        klass = "fel_mal"'''
assert s.count(old2) == 1
s = s.replace(old2, new2, 1)

p.write_text(s)
print("ankomstpredikatet lases nu ur flygregistratorn")
