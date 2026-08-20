#!/usr/bin/env python3
"""Hopp 3: agarens ord — 'ringspawnen' = RING-ITEMETS plats.

Ring of Shadows ar Quakes `item_artifact_invisibility`. Uppslagen i dm3:s egen
entitetslump (samma kalla som megaplatserna):

    "classname" "item_artifact_invisibility"
    "origin"    "240 -32 56"

Kalla: maps/dm3.bsp, entitetslumpen, last med hopptraning/dm3_items.py.
Narmaste stabara navmeshcell ar [224,-32,56] — 16 u vasterut, samma plan.

Kontrollpunkt pa samma uppslagning: `item_artifact_super_damage` (quad) star pa
[952,296,56], vilket ligger 40 u fran agarens givna quadmal [946,334,56]. Bada
kommer ur samma lump, sa avlasningen ar bunden.

Startpositionen i harnessen ar itemets egen origin — det ar den punkt agaren
pekade ut. Malet ar oforandrat: quad [946,334,56].
"""
import pathlib

p = pathlib.Path("/home/xerial/hopptraning/hoppa.py")
s = p.read_text()

old = '''    "3": {
        "namn": "ringspawnen till quad",
        "starter": [("ringspawn", [224.0, -320.0, 75.0])],'''
new = '''    "3": {
        "namn": "ringspawnen (Ring of Shadows) till quad",
        # Agarens ord: ringspawnen = RING-ITEMETS plats, inte teleportavslappet.
        # dm3.bsp entitetslump: item_artifact_invisibility origin "240 -32 56".
        # Narmaste stabara cell [224,-32,56]; samma lump ger quaditemet [952,296,56].
        "starter": [("ringitemet", [240.0, -32.0, 56.0])],'''
assert s.count(old) == 1, s.count(old)
s = s.replace(old, new, 1)
p.write_text(s)
print("hopp 3 startposition satt till ringitemets origin [240,-32,56]")
