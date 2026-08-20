#!/usr/bin/env python3
"""Uppsattning for F-serien: tredje vinkeln + de fyra loggfalten.

Tre etablerade vinklar ur tidigare hopptraning, alla mot quad [946,334,56]:
  syd        [478,-515,56]   ring2quad2
  nord       [193,-45,56]    ring2quad3
  ringitemet [240,-32,56]    Ring of Shadows, agarens egen uppslagna punkt

Hopp 1:s ursprungliga tvavinkelkonfiguration lamnas OROD (v1-v12 ar matta pa
den); F-serien far en egen post "1f".
"""
import pathlib

p = pathlib.Path("/home/xerial/hopptraning/hoppa.py")
s = p.read_text()
old = '''    "2": {
        "namn": "RA-spawnen ut genom teleporten",'''
new = '''    "1f": {
        "namn": "ring2quad, tre etablerade vinklar (F-serien)",
        "starter": [
            ("syd", [478.0, -515.0, 56.0]),        # ring2quad2
            ("nord", [193.0, -45.0, 56.0]),        # ring2quad3
            ("ringitemet", [240.0, -32.0, 56.0]),  # Ring of Shadows
        ],
        "mal": [946.0, 334.0, 56.0],
        "ankomst_r": 56.0,
        "ankomst_dz": 12.0,
        "fall_z": 48.0,
        "budget_s": 12.0,
    },
    "2": {
        "namn": "RA-spawnen ut genom teleporten",'''
assert s.count(old) == 1
p.write_text(s.replace(old, new, 1))
print("vinkel 3 tillagd som hopp '1f'; hopp '1' orord")
