#!/usr/bin/env python3
"""Varfor ar planen TOM nar boten star pa cell 1216?

Tva forklaringar ger samma tomma plan, och de pekar at helt olika hall:

  (a) MALET RAKNAS SOM NATT — boten har slappt sin order och slutat routa.
      Da ska ett FARSKT Goto till samma mal ge en ny plan pa stallet.
  (b) INGEN RUTT HITTAS FRAN 1216 — routern klarar inte steget just darifran.
      Da ar aven ett farskt Goto tomt.

Provet: kor fram boten till kanten, las `order` och plan, skicka sedan Goto
igen och las om. Skillnaden avgor.
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
VASTKANT = {1214, 1216, 1218}

g = json.load(open("/home/xerial/hopptraning/graf/mainref-live-graph.json"))
P = g["cells"]


def cell_vid(p):
    return min(range(len(P)), key=lambda i: math.dist(P[i], p))


def las(lab):
    b = None
    for x in lab.bots():
        if x["ent"] == BOT:
            b = x
    try:
        legs = lab.route(BOT)["Route"].get("legs") or []
    except Exception:
        legs = []
    return b, legs


def kort(legs, n=4):
    return [(L["link"], L["kind"], L["src_cell"], L["tgt_cell"]) for L in legs[:n]] or "TOM"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    lab = Lab(port=27970)
    ut = []
    for i in range(n):
        lab.stop(BOT)
        lab.teleport(BOT, START)
        time.sleep(0.7)
        lab.goto(BOT, TOPP)
        t0 = time.time()
        pa_kant = False
        while time.time() - t0 < 25.0:
            b, legs = las(lab)
            if b is None:
                break
            if cell_vid(b["origin"]) in VASTKANT and b["origin"][2] >= 320:
                pa_kant = True
                break
            time.sleep(0.2)
        if not pa_kant:
            print("  %d: naddes aldrig vastkanten" % (i + 1))
            continue
        time.sleep(1.2)                       # lat den sta stilla
        b1, legs1 = las(lab)
        c = cell_vid(b1["origin"])
        lab.goto(BOT, TOPP)                   # FARSKT Goto, samma mal
        time.sleep(0.8)
        b2, legs2 = las(lab)
        time.sleep(2.0)                       # gick den nagonstans?
        b3, _ = las(lab)
        rad = {
            "i": i + 1, "cell": c,
            "pos_fore": [round(v) for v in b1["origin"]],
            "order_fore": b1.get("order"), "posture_fore": b1.get("posture"),
            "plan_fore": kort(legs1),
            "order_efter": b2.get("order"), "plan_efter": kort(legs2),
            "pos_2s_senare": [round(v) for v in b3["origin"]],
            "cell_2s_senare": cell_vid(b3["origin"]),
        }
        ut.append(rad)
        print("  %d: cell %d %s | FORE order=%s plan=%s" % (i + 1, c, rad["pos_fore"], rad["order_fore"], rad["plan_fore"]))
        print("       EFTER farskt Goto: order=%s plan=%s" % (rad["order_efter"], rad["plan_efter"]))
        print("       2 s senare: %s cell %d" % (rad["pos_2s_senare"], rad["cell_2s_senare"]))
    lab.teleport(BOT, START)
    lab.hold(BOT)
    fick_plan = sum(1 for r in ut if r["plan_efter"] != "TOM")
    ror_sig = sum(1 for r in ut if r["cell_2s_senare"] != r["cell"])
    print()
    print("SUMMA %d prov: %d fick ny plan av ett farskt Goto, %d flyttade sig darefter"
          % (len(ut), fick_plan, ror_sig))
    print("DOM: %s" % ("(a) MALET RAKNADES SOM NATT — ordern var slappt, routern klarar steget"
                       if fick_plan == len(ut) and ut else
                       "(b) INGEN RUTT FRAN 1216 — routern klarar inte steget darifran"
                       if fick_plan == 0 and ut else "blandat, se raderna"))
    json.dump(ut, open("/home/xerial/hopptraning/kanten.json", "w"), indent=1)


if __name__ == "__main__":
    main()
