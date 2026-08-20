#!/usr/bin/env python3
import json
import sys
import time

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa

START = [478.0, -515.0, 56.0]
MAL = [946.0, 334.0, 56.0]

lab = hoppa.Lab()
hoppa.ensure_ready(lab, 1)
print("cell vid start:", json.dumps(lab.request({"Cell": {"pos": START}}))[:600])
print("cell vid mal  :", json.dumps(lab.request({"Cell": {"pos": MAL}}))[:600])
lab.request({"Teleport": {"bot": 1, "pos": START, "vel": [0.0, 0.0, 0.0]}})
time.sleep(0.2)
b = hoppa.bot_row(lab, 1)
print("efter teleport:", {k: b.get(k) for k in ("ent", "alive", "posture", "order", "origin")})
lab.request({"Goto": {"bot": 1, "pos": MAL}})
time.sleep(0.3)
r = lab.request({"Route": {"bot": 1}})
print("rutt nycklar:", list(r.keys()))
print("rutt:", json.dumps(r)[:1500])
t0 = time.monotonic()
while time.monotonic() - t0 < 12:
    b = hoppa.bot_row(lab, 1)
    print("%5.1f %s posture=%s order=%s ground=%s" % (
        time.monotonic() - t0, [round(v) for v in b["origin"]], b.get("posture"), b.get("order"),
        b.get("on_ground")))
    time.sleep(0.5)
print("botrad nycklar:", list(b.keys()))
