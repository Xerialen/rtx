#!/usr/bin/env python3
"""Mutationsprov for fixklass_c.py.

    python3 testsuite/tools/mutationsprov_c.py

Ordern kraver: matchregelns VARJE kriterium muterat ska fallas av
sjalvtestet, och identitetsjamforelsen kortsluten ska fallas.

Provet muterar en sak i taget, kor `--sjalvtest`, och kraver att exakt det
fall som pastar sig bevaka kriteriet faller. En mutation som ingen fangar ar
dod kod som ser ut som en grind.

Forsta korningen har fallde fyra av mina EGNA testfall: (ii) och (iii)
forsokte isolera start- respektive landningskriteriet men forskot punkten
LANGS hoppriktningen, sa steglangden gick ur intervallet och steg-kriteriet
fallde bandet i stallet. Fallen sag grona ut och provade inte det de sa sig
prova. Forskjutningen ar darfor vinkelratt nu.

Skriver i arbetstradet under korningen och lagger tillbaka originalet i ett
`finally`. Kor det inte mot ett trad du har ocommittade andringar i.
"""
import re
import subprocess
import sys
from pathlib import Path

FIL = Path(__file__).resolve().parent / "fixklass_c.py"

MUTATIONER = [
    # --- matchregelns fem kriterier ---
    ("kriterium 1 startpunkt <=30u",
     "TOL_START = 30.0", "TOL_START = 300.0", "ii_start_40u"),
    ("kriterium 2 landning <=20u",
     "TOL_LAND = 20.0", "TOL_LAND = 200.0", "iii_landning_35u"),
    ("kriterium 3 steglangd 776+-10",
     "STEP_TOL = 10.0", "STEP_TOL = 500.0", "viii_steg_utanfor"),
    ("kriterium 4 riktning (ordningsokanslig)",
     "    start_dist = dist3(a, CELL_START)\n    land_dist = dist3(b, CELL_LAND)",
     "    start_dist = min(dist3(a, CELL_START), dist3(b, CELL_START))\n"
     "    land_dist = min(dist3(b, CELL_LAND), dist3(a, CELL_LAND))",
     "ix_omvand"),
    ("kriterium 5 stampel (identitetsledet)",
     '    if not dom["ok"]:', "    if False:", "iv_utan_stampel"),

    # --- identitetsjamforelsen kortsluten (Sols villkor 5) ---
    ("identitetsdom kortsluten helt",
     '    return {"ok": True, "skal": "tre led lika", "led": led}\n',
     '    return {"ok": True, "skal": "tre led lika", "led": led}\n',  # ersatts nedan
     "NK-A_fel_artefaktidentitet"),
    ("bandstampel != artefaktidentitet slopad",
     '    if led["bandstampel"] != led["artefaktidentitet"]:',
     "    if False:", "NK-B_fel_bandstampel"),
    ("levande grafavlasning slopad",
     '    if led["levande_graf"] != led["artefaktidentitet"]:',
     "    if False:", "NK-C_fel_levande_graf"),

    # --- forseglingen av proveniensartefakten ---
    ("sigillkravet slopat",
     '        forseglad = sidofil.read_text(encoding="utf-8").split()[0].lower()',
     '        forseglad = _sha256(p)', "NK-D_oforseglad_artefakt"),
    ("sigillbrott ignorerat",
     "    if sha != forseglad:", "    if False:", "NK-E_brutet_sigill"),

    # --- kanonpinnen ---
    ("kanonpinnen slopad",
     "    if sha != KANON_SHA256:", "    if False:", "NK-H_fel_kanon"),

    # --- PlanTick: saknad strom far aldrig bli 0 ---
    ("saknad PlanTick blir 0 i stallet for oattesterad",
     '        return {"attesterad": False, "planerade": None, "oplanerade": None,\n'
     '                "status": "oattesterad", "seq_luckor": None}',
     '        return {"attesterad": True, "planerade": 0, "oplanerade": 0,\n'
     '                "status": "attesterad", "seq_luckor": 0}',
     "NK-F_plantick_saknas"),

    # --- at_topp-kravet ---
    ("at_topp-kravet slopat",
     "    if trafficad is not None and framme:", "    if trafficad is not None:",
     "x_teleport_utan_topp"),

    # --- max_steg fore/efter ---
    ("max_steg fore/efter slopat",
     '        m["steg_krav_ok"] = (\n'
     '            m["max_steg_fore_u"] <= MAX_STEG and m["max_steg_efter_u"] <= MAX_STEG\n'
     "        )",
     '        m["steg_krav_ok"] = True', "xi_max_steg_efter"),

    # --- villkor 1 bokstavligt: kanonkatalogen far inte smutsas ner ---
    ("bytecode-sparr slopad (skriver __pycache__ i kanon)",
     "    sys.dont_write_bytecode = True", "    sys.dont_write_bytecode = _bytecode",
     "NK-J_kanonkatalog_orord"),

    # --- fail-closed-grinden ---
    ("fail-closed sjalvtestgrind slopad",
     "    if not _SJALVTEST_GRON:", "    if False:", "NK-I_fail_closed"),
]

# Kortslutningen av hela identitetsdomen behover en egen form.
KORTSLUT_FRAN = 'def identitetsdom(bandstampel: Optional[str], artefakt_id: str, levande_graf: Optional[str]) -> dict:'
KORTSLUT_TILL = (
    'def identitetsdom(bandstampel: Optional[str], artefakt_id: str, levande_graf: Optional[str]) -> dict:\n'
    '    return {"ok": True, "skal": "KORTSLUTEN", "led": {}}\n'
    '    # noqa'
)


def kor_sjalvtest():
    r = subprocess.run(
        [sys.executable, str(FIL), "--sjalvtest"], capture_output=True, text=True
    )
    fallna = set(re.findall(r"^\s*\((\S+)\)[^\n]*-> FEL$", r.stdout, re.M))
    kraschade = r.returncode not in (0, 2)
    return r.returncode, fallna, kraschade, r


def main():
    rc, fallna, krasch, r = kor_sjalvtest()
    if rc != 0 or fallna:
        print("BASLINJEN AR INTE GRON — mutationsprov meningslost")
        print(r.stdout[-2000:], r.stderr[-2000:])
        return 1
    print("baslinje: gron\n")

    orig = FIL.read_text(encoding="utf-8")
    daliga = 0
    for namn, gammal, ny, vaktare in MUTATIONER:
        if namn == "identitetsdom kortsluten helt":
            gammal, ny = KORTSLUT_FRAN, KORTSLUT_TILL
        n = orig.count(gammal)
        if n != 1:
            print("HOPPAR   %-45s %d ankartraffar" % (namn, n))
            daliga += 1
            continue
        FIL.write_text(orig.replace(gammal, ny), encoding="utf-8")
        try:
            _, f, krasch, rr = kor_sjalvtest()
        finally:
            FIL.write_text(orig, encoding="utf-8")

        if krasch:
            print("KRASCH   %-45s (raknas som ofangad)" % namn)
            daliga += 1
        elif not f:
            print("OFANGAD  %-45s INGEN test foll" % namn)
            daliga += 1
        elif vaktare is None:
            print("FALLD    %-45s av %s" % (namn, ", ".join(sorted(f))))
        elif vaktare in f:
            extra = sorted(f - {vaktare})
            print("FALLD    %-45s av %s%s"
                  % (namn, vaktare, (" (+%d till)" % len(extra)) if extra else ""))
        else:
            print("FEL VAKT %-45s vantade %s, foll %s" % (namn, vaktare, sorted(f)))
            daliga += 1

    print("\n%d mutationer, %d utan ratt vaktare" % (len(MUTATIONER), daliga))
    return 1 if daliga else 0


if __name__ == "__main__":
    sys.exit(main())
