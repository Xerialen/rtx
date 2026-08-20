#!/usr/bin/env python3
"""Steg 1: protokoll for TILLBAKAHOPPET (quad -> ring), samma klyfta.

Valt upplagg: start pa quadsidan i stallet for att forlanga overhoppets forsok.
Skalet ar matt — harnessen bryter vid quad-ankomst, och att lata bandet rulla
vidare hade gett en ospecificerad startpunkt for returen (boten star nagonstans
inom 56 u av quad, med olika fart och kurs). En egen start pa quad ger samma
kontrollerade utgangslage som overhoppet har: exakt punkt, nollfart.

  start  quad          [946, 334, 56]   agarens egen quadpunkt
  mal    ringens lapp  [438, 142, 56]   dit de vastgaende korsningarna landar
                                        (34547 fran [712,144], 35627 fran [800,320])

Fall-troskeln ar densamma som for overhoppet (z < 48) — samma klyfta, samma plan.
"""
import pathlib
p = pathlib.Path("/home/xerial/hopptraning/hoppa.py")
s = p.read_text()
old = '''    "2": {
        "namn": "RA-spawnen ut genom teleporten",'''
new = '''    "4": {
        "namn": "TILLBAKAHOPPET: quad over klyftan till ringen",
        "starter": [("quad", [946.0, 334.0, 56.0])],
        "mal": [438.0, 142.0, 56.0],
        "ankomst_r": 56.0,
        "ankomst_dz": 12.0,
        "fall_z": 48.0,
        "budget_s": 12.0,
    },
    "2": {
        "namn": "RA-spawnen ut genom teleporten",'''
assert s.count(old) == 1
p.write_text(s.replace(old, new, 1))
print("hopp '4' (tillbakahoppet) tillagt: quad [946,334,56] -> ringens lapp [438,142,56]")
