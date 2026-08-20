#!/usr/bin/env python3
"""AGARREGEL 20/8 kvall: RAMLAR BOTEN NER ar forsoket FAIL DIREKT.

Reset omedelbart, nytt forsok pa en gang. Ingen aterklattring, ingen vantan pa
timeout.

Fore: harnessen satte klass=fall och lat bandet rulla upp till 2,5 s till, sa att
obduktionen kunde binda handelsen till LANDNINGEN. Den bindningen offras nu —
agarens varvprotokoll gar fore. Klassningen ar oforandrad (fall ar fall), bara
tiden i fritt fall kortas, sa varvens SIFFROR ar jamforbara bakat.
"""
import pathlib
p = pathlib.Path("/home/xerial/hopptraning/hoppa.py")
s = p.read_text()
old = """        if klass is None and (not b.get("alive") or o[2] < cfg["fall_z"]):
            # Fallet ar avgjort har. Bandet rullar vidare till nedslaget sa att
            # obduktionen kan binda handelsen till LANDNINGEN (aldrig till
            # luft-triggerticken) — klassningen ar redan satt och andras inte.
            klass = "fall"
            tid_klass = nu
            fall_vid = nu
        if klass == "fall" and (nu - fall_vid > 2.5 or (b.get("on_ground") and nu - fall_vid > 0.3)):
            break"""
new = """        if klass is None and (not b.get("alive") or o[2] < cfg["fall_z"]):
            # AGARREGEL: ramlar boten ner ar forsoket FAIL DIREKT. Bryt pa stallet,
            # resetta, nytt forsok. Ingen aterklattring, ingen vantan pa timeout.
            klass = "fall"
            tid_klass = nu
            fall_vid = nu
            try:
                lab.request({"Stop": {"bot": bot}})
            except Exception:
                pass
            break"""
assert s.count(old) == 1
p.write_text(s.replace(old, new, 1))
print("varvprotokollet uppdaterat: fall = FAIL direkt, reset omedelbart")
