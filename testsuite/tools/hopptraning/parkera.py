#!/usr/bin/env python3
"""AGARREGEL: boten far ALDRIG strova fritt medan servern ar publicerad.

Orsaken var min egen harness: den avslutade varje forsok OCH varje varv med
Cmd::Stop, vilket lamnar tillbaka puppeten till sin egen AI — och da springer den
runt banan medan agaren tittar.

Fixen: `parkera()` — teleportera till startpunkten och satt Hold. Anvands
  - mellan forsok (i stallet for Stop)
  - vid fall, direkt (fall=FAIL-regeln: reset pa stallet)
  - vid varvets slut

Da ar boten antingen i en tight forsoksloop eller stillastaende pa startpunkten.
"""
import pathlib
p = pathlib.Path("/home/xerial/hopptraning/hoppa.py")
s = p.read_text()

hjalp = '''

def parkera(lab, bot, pos):
    """Stall boten pa `pos` och HALL den dar. Aldrig fritt strov nar riggen visas."""
    try:
        lab.request({"Teleport": {"bot": bot, "pos": [float(v) for v in pos], "vel": [0.0, 0.0, 0.0]}})
        lab.request({"Hold": {"bot": bot}})
    except Exception:
        pass

'''
anchor = "\ndef forsok(lab, bot, start, mal, cfg, i, namn):"
assert s.count(anchor) == 1
s = s.replace(anchor, hjalp + anchor, 1)

# fall: parkera direkt i stallet for Stop
old_fall = '''            fall_vid = nu
            try:
                lab.request({"Stop": {"bot": bot}})
            except Exception:
                pass
            break'''
new_fall = '''            fall_vid = nu
            parkera(lab, bot, start)   # FAIL direkt: tillbaka till startpunkten pa stallet
            break'''
assert s.count(old_fall) == 1
s = s.replace(old_fall, new_fall, 1)

# slutet av forsok: parkera i stallet for Stop
old_slut = '''    try:
        lab.request({"Stop": {"bot": bot}})
    except Exception:
        pass
    return rad'''
new_slut = '''    parkera(lab, bot, start)       # mellan forsok: stillastaende, aldrig fritt strov
    return rad'''
assert s.count(old_slut) == 1
s = s.replace(old_slut, new_slut, 1)

# slutet av varvet: parkera pa forsta startpunkten i stallet for Stop
old_varv = '''    try:
        lab.request({"Stop": {"bot": a.bot}})
    except Exception:
        pass
    lab.close()'''
new_varv = '''    parkera(lab, a.bot, cfg["starter"][0][1])   # varvet slut: boten star kvar pa startpunkten
    print("boten parkerad pa %s" % (cfg["starter"][0][1],))
    lab.close()'''
assert s.count(old_varv) == 1
s = s.replace(old_varv, new_varv, 1)

p.write_text(s)
print("harnessen parkerar nu boten: mellan forsok, vid fall och vid varvets slut")
