#!/usr/bin/env python3
"""Varfor toms planen mellan t~4,9 s och t~6,6 s?

Grokks design: IN-ring med TAT plantelemetri i fonstret t = 4-8 s. Varje
avlasning tar tre saker:

  * `Route`  -> route_pos + alla ben (lank, kind, src_cell, tgt_cell)
  * `Status` -> origin, order, posture, on_ground
  * `Cell`   -> MOTORNS egen cellupplosning for botens punkt (inte min naiva
                narmaste-avstand; den domde inget forra gangen och gor det inte
                nu heller)

Utover pollningen dras botens egen flygskrivare (`Audit`) efter varje forsok.
Den gar i motorns takt, inte min, och bar `leg` och `target` per bild — det ar
den som kan visa vad boten trodde att den gjorde nar planen foll.

Ingen grafandring utover K2, som ar det lage fragan stalldes i.
"""
import json
import math
import sys
import time

sys.path.insert(0, "/home/xerial/rtx-tools")
from labctl import Lab

BOT = 1
START = [449.0, -338.0, 56.0]
TOPP = [256.0, -704.0, 328.0]
CENTRUM = [250.0, -703.0]
FONSTER = (3.5, 9.0)
BUDGET = 14.0


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    lab = Lab(port=27970)
    lab.set("rtx_telemetry", "1")
    lab.set("rtx_bot_debug", "1")
    ut = []
    for i in range(n):
        lab.stop(BOT)
        lab.teleport(BOT, START)
        time.sleep(0.8)
        lab.goto(BOT, TOPP)
        t0 = time.time()
        prov = []
        while True:
            t = time.time() - t0
            if t > BUDGET:
                break
            if t < FONSTER[0]:
                time.sleep(0.05)
                continue
            rad = {"t": round(t, 3)}
            try:
                b = None
                for x in lab.bots():
                    if x["ent"] == BOT:
                        b = x
                if b is None:
                    break
                o = b["origin"]
                rad["pos"] = [round(v, 1) for v in o]
                rad["order"] = b.get("order")
                rad["posture"] = b.get("posture")
                rad["on_ground"] = b.get("on_ground")
                rad["dxy_centrum"] = round(math.hypot(o[0] - CENTRUM[0], o[1] - CENTRUM[1]), 1)
                r = lab.route(BOT)["Route"]
                rad["route_pos"] = r.get("route_pos")
                legs = r.get("legs") or []
                rad["n_ben"] = len(legs)
                rad["ben"] = [(L["link"], L["kind"], L["src_cell"], L["tgt_cell"]) for L in legs[:5]]
                c = lab.cell([float(v) for v in o])["Cell"]
                rad["motor_cell"] = c.get("cell")
                rad["motor_cell_origin"] = [round(v) for v in (c.get("origin") or [0, 0, 0])]
                rad["motor_cell_ut"] = len(c.get("out") or [])
            except Exception as e:
                rad["fel"] = "%s: %s" % (type(e).__name__, e)
            prov.append(rad)
            if t > FONSTER[1]:
                break
        try:
            au = lab.audit(BOT, lines=400)["Audit"]
        except Exception as e:
            au = {"fel": str(e)}
        ut.append({"i": i + 1, "prov": prov, "audit": au})

        # kort sammandrag: nar forsvann planen, och vad sa motorn da
        tom_vid = next((p["t"] for p in prov if p.get("n_ben") == 0), None)
        sist_med = None
        for p in prov:
            if p.get("n_ben"):
                sist_med = p
        print("  %2d/%d  plan tom fran t=%s | sista planen t=%s n_ben=%s ben0=%s | slut %s cell %s"
              % (i + 1, n, tom_vid,
                 sist_med["t"] if sist_med else "-",
                 sist_med["n_ben"] if sist_med else "-",
                 sist_med["ben"][0] if sist_med and sist_med["ben"] else "-",
                 prov[-1].get("pos") if prov else "-",
                 prov[-1].get("motor_cell") if prov else "-"))
    lab.teleport(BOT, START)
    lab.hold(BOT)
    json.dump(ut, open("/home/xerial/hopptraning/fonstret.json", "w"), indent=1)
    print("skrivet: ~/hopptraning/fonstret.json")


if __name__ == "__main__":
    main()
