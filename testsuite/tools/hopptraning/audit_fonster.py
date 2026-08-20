#!/usr/bin/env python3
"""Vad sager botens EGEN flygskrivare i ogonblicket planen faller?

Pollningen sag planen forsvinna vid t~6,63 s vaggklocka. Flygskrivaren gar i
motorns takt och bar route_len/route_pos, cell, target, route_goal, leg och
off_reason per bild — den kan visa overgangen sjalv i stallet for att jag ska
gissa mellan tva avlasningar.
"""
import json
from pathlib import Path


def txt(f):
    b = f.get("buf") or []
    return bytes(b[: f.get("len", 0)]).decode("latin1")


d = json.loads(Path("/home/xerial/hopptraning/fonstret.json").read_text())
for rad in d:
    fr = rad["audit"].get("frames") or []
    if not fr:
        print("forsok %d: ingen audit" % rad["i"])
        continue
    # forsta biden dar rutten ar tom, efter att ha varit icke-tom
    fall = None
    for i in range(1, len(fr)):
        if fr[i - 1]["route_len"] > 0 and fr[i]["route_len"] == 0:
            fall = i
    print("== forsok %d — %d bilder, t %.2f..%.2f ==" % (rad["i"], len(fr), fr[0]["t"], fr[-1]["t"]))
    if fall is None:
        print("   rutten foll aldrig till 0 i fonstret (route_len sist = %d)" % fr[-1]["route_len"])
        continue
    for j in range(max(0, fall - 3), min(len(fr), fall + 3)):
        f = fr[j]
        o = [round(v) for v in f["origin"]]
        print("   %s t=%8.3f pos=%-20s cell=%-6s len=%-3s pos_i=%-3s leg=%-10s target=%-6s goal_cell=%-6s goal_dist=%-7s posture=%-8s off=%s"
              % (">>" if j == fall else "  ", f["t"], str(o), f.get("cell"), f["route_len"], f["route_pos"],
                 f.get("leg"), f.get("target"), f.get("goal_cell"),
                 round(f["goal_dist"], 1) if isinstance(f.get("goal_dist"), (int, float)) else f.get("goal_dist"),
                 f.get("posture"), txt(f.get("off_reason") or {})))
    print()
