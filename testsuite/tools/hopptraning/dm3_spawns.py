#!/usr/bin/env python3
"""Lista dm3:s spawnpunkter ur BSP:ns entitetslump (read-only)."""
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
    if cn.startswith("info_player") or cn.startswith("info_teleport") or cn == "trigger_teleport":
        rows.append((cn, kv.get("origin"), kv.get("angle"), kv.get("target"), kv.get("targetname")))
for r in sorted(rows):
    print(r)
print("n=", len(rows))
