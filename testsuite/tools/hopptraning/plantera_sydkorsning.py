#!/usr/bin/env python3
"""ANDRING 1 (hopp 1, varv 2): plantera agarens EGEN sydkorsning over gapet.

Obduktionsfynd v1: alla 6 fall ar samma mekanism — boten passerar ringens
ostra oppning vid x~496 for LAGT (origin z 82-89; troskeln ligger over feet 58
och under feet 67) och stoppas dod i luften, varefter den ramlar i grytan.
De fallande korsningarna kor de KEDJADE, curl-losa speedjumpen 34501 (syd) /
34503 (nord), vars certifierade avfart ligger pa cellcentrum ~50 u fore
oppningen — for sent for att banan ska vara uppe vid troskeln. Varje lyckad
korsning (och agarens egna tva demon) lamnar marken >= 64 u fore x=496 och
passerar oppningen pa z >= 90.

Andringen: plantera EN speedjump med avfarten flyttad bakat till [412,-202],
som ar en redan surveyed avfartspunkt i motorns egen curl-tabell (link 35502),
och som ligger pa agarens egen linje. Samma oppning, samma sida, landning pa
cell 2026 [676,-234] — en cell som redan ar landningsmal for speedjump 35716,
alltsa ingen ny landningsyta.

Bana fran den avfarten: vid x=496 ar d=84 u, t=0.20 s, z=56+52,6-16,4 = 92 —
9 u over det som fallerar och i niva med agarens 91-96.

Minsta verksamma ingrepp: INGEN lank tas bort i detta varv. Om planeraren anda
valjer de kedjade lankarna ar nasta steg en kombination (plant + close) med
recept_lint pa in/ut for varje rord cell.
"""
import json
import sys

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa

LOCK = "/home/xerial/hopptraning/.rig-lock"
FRAN = [352.0, -224.0, 56.0]
AVFART = [412.0, -202.0, 56.0]
MAL = [676.0, -234.0, 56.0]
V_REQ = 415.0
GAIN = 12.0


def ident(lab):
    s = lab.status()
    return {k: s.get(k) for k in ("map", "cells", "links", "rj_links")}


def main():
    tok = open(LOCK).read().strip().splitlines()[0]
    lab = hoppa.Lab()
    fore = ident(lab)
    print("FORE:", json.dumps(fore))
    r = lab.request({"PlanLink": {"from": FRAN, "takeoff": AVFART, "tgt": MAL,
                                  "v_req": V_REQ, "gain": GAIN, "lock_token": tok}}, timeout=30)
    print("PLANT:", json.dumps(r))
    efter = ident(lab)
    print("EFTER:", json.dumps(efter))
    json.dump({"fore": fore, "efter": efter, "plant": r,
               "fran": FRAN, "avfart": AVFART, "mal": MAL, "v_req": V_REQ, "gain": GAIN},
              open("/home/xerial/hopptraning/andring1-plant.json", "w"), indent=1)


if __name__ == "__main__":
    main()
