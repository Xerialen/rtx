#!/usr/bin/env python3
"""Slå upp dm3:s func_plat (liftar) och spawnpunkter, och para ihop dem.

Ägarens ord: "liftspawnen". Vi tar reda på vilken spawnpunkt som faktiskt ligger
vid en lift, i stället för att gissa.
"""
import math
import re
import struct
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "/home/xerial/.local/share/qw-fasttrack/runtime/qw/maps/dm3.bsp"
d = open(p, "rb").read()
off, ln = struct.unpack("<ii", d[4:12])
ents = d[off:off + ln].split(b"\0")[0].decode("latin-1")
blocks = re.findall(r"\{(.*?)\}", ents, re.S)

plats, spawns, models = [], [], {}
for b in blocks:
    kv = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', b))
    cn = kv.get("classname", "")
    if cn == "func_plat":
        plats.append(kv)
    if cn.startswith("info_player"):
        o = [float(x) for x in kv["origin"].split()]
        spawns.append((cn, o))

# Modellernas bbox ur BSP:ns model-lump ger func_plat sin plats (de har ingen origin).
mo, ml = struct.unpack("<ii", d[4 + 8 * 14:4 + 8 * 14 + 8])
nmod = ml // 64
mins = []
for i in range(nmod):
    base = mo + i * 64
    mn = struct.unpack("<fff", d[base:base + 12])
    mx = struct.unpack("<fff", d[base + 12:base + 24])
    mins.append((mn, mx))

print("func_plat i dm3: %d" % len(plats))
for kv in plats:
    m = kv.get("model", "")
    idx = int(m[1:]) if m.startswith("*") else None
    if idx is not None and idx < len(mins):
        mn, mx = mins[idx]
        mid = [round((mn[k] + mx[k]) / 2) for k in range(3)]
        print("   model %-5s mitt %s  height=%s" % (m, mid, kv.get("height")))
        kv["_mid"] = mid

print()
print("spawnpunkter och avstånd till närmaste lift:")
for cn, o in sorted(spawns, key=lambda s: s[1]):
    best = None
    for kv in plats:
        if "_mid" not in kv:
            continue
        dd = math.dist(o, kv["_mid"])
        if best is None or dd < best[0]:
            best = (dd, kv["_mid"])
    print("   %-24s %-20s  närmaste lift %6.0f u  %s" % (cn, [round(v) for v in o],
                                                         best[0] if best else -1,
                                                         best[1] if best else "-"))
print()
print("SNG-megan (ägarens mål): [-720, 80, 160]")
for cn, o in spawns:
    print("   %-24s %-20s  avstånd till SNG-megan %6.0f u" % (cn, [round(v) for v in o],
                                                              math.dist(o, [-720, 80, 160])))
