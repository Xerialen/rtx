#!/usr/bin/env python3
"""HELKEDJEVARV — hopp 1 over -> hopp 2 -> hopp 3 i foljd, i SAMMA forsok.

Kedjan ar malet, inte hoppen var for sig. Ett kedjeforsok raknas som lyckat
endast om ALLA TRE benen lyckas. Fall var som helst = FAIL direkt for hela
kedjeforsoket: boten parkeras pa stallet och nasta kedjeforsok borjar.

Benen kors med sina egna, redan matta protokoll — varje ben teleporteras till sin
egen startpunkt, precis som nar hoppen mattes var for sig. Det ar den tolkning
som bevarar jamforbarheten bakat; om kedjan i stallet ska vara sammanhangande
fardvag mellan benen ar per-bens-datat kvar och gar att skiva om.

Ben 1 roterar mellan de tre etablerade vinklarna sa att alla tacks.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa

VINKLAR = [("syd", [478.0, -515.0, 56.0]),
           ("nord", [193.0, -45.0, 56.0]),
           ("ringitemet", [240.0, -32.0, 56.0])]
QUAD = [946.0, 334.0, 56.0]

BEN = [
    # (namn, start, mal, cfg)
    ("1-over", None, QUAD, {"ankomst_r": 56.0, "ankomst_dz": 12.0, "fall_z": 48.0, "budget_s": 12.0}),
    ("2-teleport", [-632.0, -680.0, -16.0], [224.0, -320.0, 75.0],
     {"ankomst_r": 64.0, "ankomst_dz": 40.0, "fall_z": -260.0, "budget_s": 15.0}),
    ("3-ringitem", [240.0, -32.0, 56.0], QUAD,
     {"ankomst_r": 56.0, "ankomst_dz": 12.0, "fall_z": 48.0, "budget_s": 14.0}),
]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    tag = sys.argv[2] if len(sys.argv) > 2 else "kedja1"
    lab = hoppa.Lab()
    st = lab.status()
    ident = {k: st.get(k) for k in ("map", "cells", "links", "rj_links")}
    print("HELKEDJA %s  graf %s  botar %d" % (tag, json.dumps(ident), len(st.get("bots") or [])))

    ut = Path("/home/xerial/hopptraning/kedja") / tag
    ut.mkdir(parents=True, exist_ok=True)
    rader, hela = [], 0
    ben_ok = {b[0]: [0, 0] for b in BEN}

    for i in range(n):
        vnamn, vstart = VINKLAR[i % len(VINKLAR)]
        legs, ok = [], True
        for bnamn, bstart, bmal, bcfg in BEN:
            start = vstart if bstart is None else bstart
            cfg = dict(bcfg, mal=bmal)
            r = hoppa.forsok(lab, hoppa.BOT, start, bmal, cfg, i + 1, bnamn)
            ben_ok[bnamn][1] += 1
            lyckad = r["klass"] == "lyckad"
            ben_ok[bnamn][0] += int(lyckad)
            legs.append({"ben": bnamn, "vinkel": vnamn if bstart is None else None,
                         "klass": r["klass"], "tid_s": r.get("tid_s"),
                         "min_z": r.get("min_z"), "slut": r.get("slut_pos")})
            if not lyckad:
                ok = False
                hoppa.parkera(lab, hoppa.BOT, start)   # FAIL direkt: kedjan bruten har
                break
        hela += int(ok)
        rader.append({"i": i + 1, "vinkel": vnamn, "hela_kedjan": ok, "ben": legs})
        tid = sum(l["tid_s"] or 0 for l in legs)
        print("  kedja %2d/%d  vinkel %-11s %-8s  ben: %s  (summa %.1f s)"
              % (i + 1, n, vnamn, "HEL" if ok else "BRUTEN",
                 " | ".join("%s %s" % (l["ben"], l["klass"]) for l in legs), tid))

    print()
    print("HELKEDJA %s: %d av %d hela kedjor" % (tag, hela, n))
    for b, (o, t) in ben_ok.items():
        print("  ben %-12s %d/%d" % (b, o, t))
    hoppa.parkera(lab, hoppa.BOT, VINKLAR[0][1])
    print("  boten parkerad pa %s" % (VINKLAR[0][1],))
    (ut / "kedja.json").write_text(json.dumps(
        {"tag": tag, "graf": ident, "n": n, "hela": hela,
         "ben_ok": {k: {"ok": v[0], "n": v[1]} for k, v in ben_ok.items()},
         "rader": rader}, indent=1))


if __name__ == "__main__":
    main()
