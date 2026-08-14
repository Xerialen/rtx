#!/usr/bin/env python3
"""Maskingenererad K1-rapport (auditsyntes åtgärd 3): läser summary.json +
manifest ur ~/lab/<tag>/{A,B}/ och skriver markdown. Inga handförda siffror.
Användning: python3 k1_rapport.py [tag] > rapport.md"""
import json, os, subprocess, sys

TAG = sys.argv[1] if len(sys.argv) > 1 else "k1"
BAS = os.path.expanduser("~/lab/" + TAG)
FASER = ["ut_ring", "ut_tunnel", "ut_vast", "in_ring", "in_tunnel", "in_vast"]
FACIT = {"ut_ring": 1.48, "ut_tunnel": 1.87, "ut_vast": 2.21,
         "in_ring": 4.94, "in_tunnel": 6.87, "in_vast": 8.18}
NAMN = {"ut_ring": "UT → ring", "ut_tunnel": "UT → tunnel", "ut_vast": "UT → väst/sng",
        "in_ring": "IN ring → topp", "in_tunnel": "IN tunnel → topp", "in_vast": "IN väst → topp"}

def ladda(arm, fas):
    p = os.path.join(BAS, arm, fas, "summary.json")
    return json.load(open(p)) if os.path.exists(p) else None

def cell(s):
    if s is None:
        return "— (saknas)"
    if s["n_ok"] == 0:
        t = "0/%d ok" % s["n_attempt"]
    else:
        t = "**%.2f** (%d/%d) bästa %.2f" % (s["median"], s["n_ok"], s["n_attempt"], s["basta"])
        if s["iqr"][0] is not None:
            t += " IQR %.2f–%.2f" % tuple(s["iqr"])
    extra = []
    if s["n_timeout"]:
        extra.append("%d timeout" % s["n_timeout"])
    if s["n_kasserad"]:
        extra.append("%d kasserade" % s["n_kasserad"])
    if s["n_ogiltig_tic"]:
        extra.append("%d tic-ogiltiga" % s["n_ogiltig_tic"])
    if s["falls_tot"]:
        extra.append("%d fall" % s["falls_tot"])
    if s.get("inkomplett"):
        extra.append("INKOMPLETT %d/%d" % (s["n_attempt"], s["N_planerat"]))
    return t + (" · " + ", ".join(extra) if extra else "")

stamp = subprocess.run(["date", "+%Y-%m-%d %H:%M:%S %Z"], capture_output=True,
                       text=True, env={"TZ": "Europe/Stockholm"}).stdout.strip()
manA = json.load(open(os.path.join(BAS, "A", "manifest.json")))
manB = json.load(open(os.path.join(BAS, "B", "manifest.json")))

print("# %s — baslinje mot KANONEN (genererad %s)" % (TAG.upper(), stamp))
print()
print("Kanon: reference/ra-room/README.md @ 91a6e34. K1 täcker ENBART")
print("kanonens in/ut-rutter — inte gamla-måls-ned/kant-sviterna (grok krav 8).")
print("Arm A = kod **+ plantering** (inte kod-vs-kod); main-IN väntas via")
print("västspiralen; teleportstart ger lägre gränsfart än Xerials löppassager")
print("— bot-mot-facit är konservativt (plan v2 §9.8).")
print()
print("| Rutt | Arm A (senaste + plant) | Arm B (main) | Xerial-facit |")
print("|---|---|---|---|")
for fas in FASER:
    print("| %s | %s | %s | %.2f |" % (NAMN[fas], cell(ladda("A", fas)),
                                       cell(ladda("B", fas)), FACIT[fas]))
print()
print("Median över lyckade klipp (sann median); IQR = linjär interpolation")
print("P25/P75; timeouts i nämnaren (grok krav 9). OBS: IN väst-facit 8,18 är")
print("PRELIMINÄRT (n=2, ej målmedvetna körningar — kimi villkor 2); be Xerial")
print("om riktade väst→topp-inspelningar innan det låses.")
print("fall_def=%s · tic-gräns %.1f%% per försök." % (
    manA.get("fall_def"), manA.get("tic_grans_pct", 1.0)))
print("OBS: på UT-rutterna räknar falldetektorn även ruttens AVSEDDA nedhopp")
print("(topp→golv är >150u) — UT-fall är deskriptiva, inte felsignal;")
print("på IN-rutterna är fall en verklig felsignal.")
print()
print("## Obligatoriska läsflaggor (granskarnas villkor, K1-pilotreviewerna)")
print()
print("1. **Jämför median mot facit, aldrig bästa.** Bästa-under-facit på UT")
print("   tunnel är läpp-geometri (klippet startar på 70u-diskens norra kant")
print("   med ansatsfart över skivan) och uppstår i BÅDA armarna — inte bevis")
print("   att någon arm slår Xerial (grok 3a; verifierad i")
print("   2026-08-14-utunnel-149-verifiering.md). Gäller även mains UT väst-")
print("   bästa under facit — UT-jämförelsen är kriteriebunden, inte")
print("   färdighetsbunden (kimi villkor 5).")
print("2. **IN ring för main är strukturell, inte en prestandaförlust:** main")
print("   når RA-plattan men stannar utanför kanonens 70u-toppdisk (hoppar på")
print("   västkanten [152,−704]) — ingen upp-länk till disken (grok 3c,")
print("   deepseek c). 0/N redovisas; jämför aldrig mot facit 4,94.")
print("3. **A:s IN väst citeras alltid med full nämnare + timeout + fall** —")
print("   fallen är riktiga klätterras från västra övre hyllan (~[60–136,")
print("   −660..−690]), inte ruttens nedhopp (fallklassningen i")
print("   verifieringsdokumentet; terra §2, grok 3b).")
print("4. **K-serien täcker ENBART kanonens in/ut** — ned-/kant-sviterna har")
print("   ingen K-baslinje. Ny so eller ny plant-JSON ⇒ ny K-serie (K2),")
print("   aldrig 'K + delta' (grok villkor 6–7).")
print()
import hashlib
print("## Manifest (fulla hashar och statebevis i manifest.json + fas_state_*.json)")
for m, arm in ((manA, "A"), (manB, "B")):
    kalla = (m["kalla"]["head_full"][:12] + " (" + m["kalla"]["dirty"] + ")"
             if "head_full" in m["kalla"] else m["kalla"]["beskrivning"])
    mp = os.path.join(BAS, arm, "manifest.json")
    print("- arm %s: %s · so sha256 %s… · start %s · taskset srv/harn %s/%s" % (
        m["arm"], kalla, m["qwprogs"]["sha256"][:12], m["start_cest"],
        m["taskset"]["server"], m["taskset"]["harness"]))
    print("  manifest sha256: %s" % hashlib.sha256(open(mp, "rb").read()).hexdigest())
print("- klippmodul sha256 %s… · harness %s… · N %s" % (
    manA["verktyg"]["klippmodul"]["sha256"][:12],
    manA["verktyg"]["harness"]["sha256"][:12], json.dumps(manA["N"])))
tic = []
for arm in ("A", "B"):
    dr = [abs(f.get("tic_drift_pct", 0)) for fas in FASER
          for f in (ladda(arm, fas) or {}).get("forsok", [])]
    hz = [f.get("poll_hz", 0) for fas in FASER
          for f in (ladda(arm, fas) or {}).get("forsok", [])]
    if dr:
        tic.append("arm %s: maxdrift %.2f%% · poll %d–%d Hz" % (
            arm, max(dr), min(hz), max(hz)))
print("- tic-vakt: " + " · ".join(tic))
