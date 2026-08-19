#!/usr/bin/env python3
"""Lista dm3:s artefakter och megaplatser ur BSP:ns entitetslump (read-only)."""
import re
import struct
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "/home/xerial/.local/share/qw-fasttrack/runtime/qw/maps/dm3.bsp"
d = open(p, "rb").read()
off, ln = struct.unpack("<ii", d[4:12])
ents = d[off:off + ln].split(b"\0")[0].decode("latin-1")
blocks = re.findall(r"\{(.*?)\}", ents, re.S)
rows = []
for b in blocks:
    kv = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', b))
    cn = kv.get("classname", "")
    if cn.startswith("item_artifact") or cn in ("item_health", "item_armorInv", "item_armor2", "item_armor1"):
        rows.append((cn, kv.get("origin"), kv.get("spawnflags"), kv.get("targetname")))
for r in sorted(rows):
    print(r)
print("n=", len(rows))
print()
print("--- ALLA classnames i dm3 (antal) ---")
from collections import Counter
c = Counter()
for b in blocks:
    kv = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', b))
    if "classname" in kv:
        c[kv["classname"]] += 1
for k, v in sorted(c.items()):
    print("  %-32s %d" % (k, v))
