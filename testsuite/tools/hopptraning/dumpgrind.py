#!/usr/bin/env python3
"""GRIND: matchar den levererade dm3-dumpen den har riggens LEVANDE graf?

Matchar den inte — eller racker den inte till en nivå-2-harledning — sa ar
svaret BLOCKED. Ingen improvisation.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa

P = Path(sys.argv[1] if len(sys.argv) > 1
         else "/home/xerial/rtx-testsuite/testsuite/dashboard/assets/maps/dm3/graph.json")

raw = P.read_bytes()
print("fil        ", P)
print("           bytes %d  sha256 %s" % (len(raw), hashlib.sha256(raw).hexdigest()))
g = json.loads(raw)
print("nycklar    ", sorted(g.keys()))

cells = g.get("cells") or []
links = g.get("links") or []
cell_ids = g.get("cell_ids") or []
kinds = g.get("linkKinds") or g.get("link_kinds") or []
n_cells = len(cells) // 3 if cells and not isinstance(cells[0], (list, tuple)) else len(cells)
n_links = len(links) // 3 if links and not isinstance(links[0], (list, tuple)) else len(links)
print("celler     ", n_cells, " cell_ids:", len(cell_ids))
print("lankar     ", n_links, " linkKinds:", kinds)

# --- riggens levande graf ------------------------------------------------
lab = hoppa.Lab()
st = lab.status()
live = {k: st.get(k) for k in ("map", "cells", "links", "rj_links")}
print("\nRIGGEN LEVANDE:", json.dumps(live))


def fnv1a64(b):
    h = 0xCBF29CE484222325
    for c in b:
        h ^= c
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


stamp = fnv1a64(live["map"].encode() + struct.pack("<III", live["cells"], live["links"], live["rj_links"]))
print("stamp (rakning ur riggen):", stamp)

kedja = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("nivå-2 (motorns egen)    :", kedja.get("content_hash"))

# --- jamforelser ---------------------------------------------------------
print("\n--- GRIND ---")
ok = True
if n_cells != live["cells"]:
    print("MISS  cellantal: dump %d != rigg %d" % (n_cells, live["cells"]))
    ok = False
else:
    print("OK    cellantal %d" % n_cells)

if n_links != live["links"]:
    print("MISS  lankantal: dump %d != rigg %d (motorn raknar hela lankarrayen)" % (n_links, live["links"]))
    ok = False

# Cellernas positioner mot riggen, stickprov + alla id
if cell_ids:
    flat = cells if not isinstance(cells[0], (list, tuple)) else [v for c in cells for v in c]
    fel = 0
    prov = list(range(0, len(cell_ids), max(1, len(cell_ids) // 400)))
    for i in prov:
        cid = cell_ids[i]
        try:
            r = lab.request({"CellById": {"cell": int(cid)}}, timeout=5)
        except Exception as exc:
            print("MISS  CellById %s: %s" % (cid, exc))
            fel += 1
            continue
        o = r.get("origin") or []
        d = [flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]]
        if max(abs(a - b) for a, b in zip(o, d)) > 0.6:
            if fel < 5:
                print("MISS  cell id %s: dump %s != rigg %s" % (cid, d, [round(v, 2) for v in o]))
            fel += 1
    print("%s   cellpositioner: %d avvikelser av %d stickprov"
          % ("OK  " if fel == 0 else "MISS", fel, len(prov)))
    if fel:
        ok = False

print("\n--- RACKER DEN TILL NIVÅ-2? ---")
print("nivå-2 hashas over HELA inventariet: alla C-poster OCH alla L-poster")
print("inklusive de prunade (T=0). Dumpen bar %d lankposter." % n_links)
print("VERDIKT:", "kan racka" if n_links >= live["links"] else
      "RACKER INTE — %d av %d lankposter saknas" % (live["links"] - n_links, live["links"]))
print("\nSAMMANTAGET:", "GRIND OK" if ok and n_links >= live["links"] else "BLOCKED")
