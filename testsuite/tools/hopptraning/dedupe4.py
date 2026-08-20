#!/usr/bin/env python3
"""Ta bort dubbletten av hopp '4' — setup-skriptet kordes tva ganger.

Python tar sista nyckeln vid dubblett, sa beteendet var oforandrat, men kallan
ska inte ha tva identiska block.
"""
import pathlib
p = pathlib.Path("/home/xerial/hopptraning/hoppa.py")
s = p.read_text()
block = '''    "4": {
        "namn": "TILLBAKAHOPPET: quad over klyftan till ringen",
        "starter": [("quad", [946.0, 334.0, 56.0])],
        "mal": [438.0, 142.0, 56.0],
        "ankomst_r": 56.0,
        "ankomst_dz": 12.0,
        "fall_z": 48.0,
        "budget_s": 12.0,
    },
'''
n = s.count(block)
assert n == 2, "vantade 2 block, hittade %d" % n
s = s.replace(block + block, block, 1)
assert s.count(block) == 1
p.write_text(s)
print("dubbletten borttagen; ett block kvar")
