#!/usr/bin/env python3
"""Mutationsprov for domarna: T4-domen (v7) och K2-lagskadegrinden.

Tredje mutationsprovet i sviten. `rig/mutationsprov.py` provar riggskripten
mot unittest-sviten, `tools/mutationsprov_c.py` provar C-instrumentet mot sitt
eget sjalvtest, och den har provar `runner/` + `dashboard/` mot de tva
selftesten. Samma form, samma regel, olika mal.

Regeln ar densamma och den ar hela poangen: en grind som aldrig setts falla ar
en gron lampa, inte en grind. Varje mutation ska fallas av EXAKT den kontroll
som pastar sig bevaka den — darfor bar varje rad en vantad markor, och en
mutation som faller pa nagot annat raknas som ett fynd, inte som en trast.

    python3 testsuite/tools/mutationsprov_dom.py

Skriver i arbetstradet under korningen och lagger tillbaka originalet i ett
`finally`. Kor det inte mot ett trad du har ocommittade andringar i.

**Avbrott ar farligt och darfor bevakat.** Ett `finally` overlever inte
SIGKILL, och en avbruten korning lamnar da en AVVAPNAD grind (`if False:`) i
arbetstradet — den ser ut som kod, selftestet ar gront eftersom mutationen
sitter i validatorn, och den foljer med in i en commit om ingen tittar. Det
hande 2026-08-25. Darfor: en pagaende korning skriver `PAGAR`-filen bredvid
sig med filen den just nu har muterat, och nasta korning VAGRAR starta sa
lange den ligger kvar. Ligger den dar: stall tillbaka den namngivna filen
(`git checkout -- <fil>`) och ta bort `PAGAR`.
"""
import subprocess
import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parent.parent
REPO = SUITE.parent

#: (fil relativt testsuite/, ankare, mutation, vantad markor i utdatan)
MUTATIONER = [
    # --- K2: sjalva grinden och dess som -------------------------------
    ("runner/team_damage.py",
     "TEAM_DAMAGE_SHARE_MAX = 0.20",
     "TEAM_DAMAGE_SHARE_MAX = 0.50",
     "k2.threshold"),
    ("runner/team_damage.py",
     "return [GATE_TEAM_DAMAGE] if (team + own) / total > ceiling else []",
     "return [GATE_TEAM_DAMAGE] if (team + own) / total >= ceiling else []",
     "k2.seam_at"),
    ("runner/team_damage.py",
     "return [GATE_TEAM_DAMAGE] if (team + own) / total > ceiling else []",
     "return [GATE_TEAM_DAMAGE] if team / total > ceiling else []",
     "k2.seam_above"),
    # --- K2: omatbart ar aldrig noll -----------------------------------
    ("runner/team_damage.py",
     "share = round((given_team + given_self) / total, 6) if total else None",
     "share = round((given_team + given_self) / total, 6) if total else 0.0",
     "k2.zero_share"),
    ("runner/team_damage.py",
     "        if enemy is None or mate is None or own is None:\n            return blank",
     "        if enemy is None or mate is None or own is None:\n            continue",
     "k2.malformed"),
    # --- K2: delkomponenternas negativkontroll -------------------------
    ("runner/team_damage.py",
     "    if not expressed or not isinstance(row, dict):",
     "    if not isinstance(row, dict):",
     "k2.components_silent_card"),
    ("runner/team_damage.py",
     "        if team is None:\n            return None\n        total += team",
     "        if team is None:\n            continue\n        total += team",
     "k2.components_unreadable"),
    # --- K2: validatorns egen omrakning --------------------------------
    ("runner/checks.py",
     "    if live:\n        _t3_team_damage(data, path, capabilities)",
     "    if False:\n        _t3_team_damage(data, path, capabilities)",
     "broken fixture accepted"),
    ("runner/checks.py",
     '    if list(block["failed_gates"]) != expected_gates:',
     "    if False:",
     "t3_k2_claims_ok_over_the_gate"),
    ("runner/checks.py",
     "    if limits != canonical:\n        _fail(\n            f\"{block_path}.thresholds\",",
     "    if False:\n        _fail(\n            f\"{block_path}.thresholds\",",
     "t3_k2_threshold_loosened"),
    ("runner/checks.py",
     "    if declared != wanted:",
     "    if False:",
     "t3_k2_absence_not_declared"),
    # --- T4 v7: draw-domen ---------------------------------------------
    ("runner/t4_dom.py",
     'VERDICTS = ("VINST", "OK", "FAIL", "OMÄTT")',
     'VERDICTS = ("VINST", "OK", "FAIL", "OMÄTT", "OAVGJORD")',
     "nk22.four_values"),
    ("runner/t4_dom.py",
     'DRAW_SEMANTICS = "stanna, ägarbeslut 2026-08-25"',
     'DRAW_SEMANTICS = "ägarbeslut saknas"',
     "nk4.semantics_text"),
    ("runner/checks.py",
     '    if outcome["drew"] is True:\n        if semantics is None:',
     "    if False:\n        if semantics is None:",
     "t4_draw_without_semantics"),
    ("runner/checks.py",
     "    if verdict == t4_dom.RETIRED_VERDICT:",
     "    if False:",
     "t4_retired_oavgjord"),
    # --- T4 v7 + K2: sidan ----------------------------------------------
    ("dashboard/build_dashboard.py",
     'T4_VERDICTS = ("VINST", "OK", "FAIL", "OMÄTT")',
     'T4_VERDICTS = ("VINST", "OK", "FAIL", "OMÄTT", "OAVGJORD")',
     'assert "OAVGJORD" not in T4_VERDICTS'),
    ("dashboard/build_dashboard.py",
     '        if text(payload.get("draw_semantik"), ""):',
     "        if False:",
     'assert "sista benet oavgjort" in drew["key"]'),
    ("dashboard/build_dashboard.py",
     "        teamDamage=normalize_team_damage(payload.get(\"team_damage\")),",
     "        teamDamage=None,",
     'assert k2["teamDamage"]["verdict"] == "FAIL"'),
    # --- T3-rorets egen vag ---------------------------------------------
    ("runner/t3.py",
     '                    "t3_schema": team_damage.T3_SCHEMA,',
     '                    "t3_schema": 99,',
     "t3path"),
]


#: Skrivs medan en fil ar muterad, tas bort nar den ar tillbakalagd. En kvarlamnad
#: fil ar beviset pa att arbetstradet INTE ar rort tillbaka.
PAGAR = Path(__file__).resolve().parent / "mutationsprov_dom.PAGAR"


def kor() -> tuple[int, str]:
    """Kor bada selftesten. Returnerar (returkod, all utdata)."""
    utdata = []
    rc = 0
    for argv in (
        [sys.executable, "-c",
         "from testsuite.runner.selftest import run;"
         " print('SELFTEST', run('testsuite/schema/fixtures'))"],
        [sys.executable, "testsuite/dashboard/build_dashboard.py", "--selftest"],
    ):
        r = subprocess.run(argv, capture_output=True, text=True, cwd=str(REPO))
        utdata.append(r.stdout + r.stderr)
        rc = rc or r.returncode
    return rc, "\n".join(utdata)


def main() -> int:
    if PAGAR.exists():
        # QA:s avvikelse A6 (2026-08-25, atgardspunkt B5): texten lod
        # "tradet kan bara / en avvapnad grind" — ett tappat "bära" som gor
        # meningen obegriplig for en operator under tidspress. Rattad till
        # samma lydelse som `rig/mutationsprov.py`, dar inget diakritiskt
        # tecken kan falla bort. Ingen logik rord.
        print("VAGRAR STARTA — en tidigare korning avbrots och arbetstradet")
        print("kan innehalla en avvapnad grind. Aterstall filen nedan och")
        print("ta bort %s:" % PAGAR)
        print(PAGAR.read_text(encoding="utf-8").strip())
        return 2
    rc, _ = kor()
    if rc != 0:
        print("BASLINJEN AR INTE GRON — mutationsprov meningslost")
        return 1
    print("baslinje: gron\n")

    dåliga = 0
    for fil, gammal, ny, markor in MUTATIONER:
        p = SUITE / fil
        orig = p.read_text(encoding="utf-8")
        n = orig.count(gammal)
        if n != 1:
            print("HOPPAR   %-28s %-46s %d ankartraffar"
                  % (fil, gammal.splitlines()[0][:46], n))
            dåliga += 1
            continue
        PAGAR.write_text(
            "muterad fil: %s\nankare: %s\n" % (p, gammal.splitlines()[0]),
            encoding="utf-8",
        )
        p.write_text(orig.replace(gammal, ny), encoding="utf-8")
        try:
            muterad_rc, ut = kor()
        finally:
            p.write_text(orig, encoding="utf-8")
            PAGAR.unlink(missing_ok=True)

        etikett = "%-28s %-46s" % (fil, gammal.splitlines()[0][:46])
        if muterad_rc == 0:
            print("OFANGAD  %s  INGEN kontroll foll" % etikett)
            dåliga += 1
        elif markor in ut:
            print("FALLD    %s  av %s" % (etikett, markor))
        else:
            print("FEL VAKT %s  vantade %r" % (etikett, markor))
            dåliga += 1

    print("\n%d mutationer, %d utan ratt vaktare" % (len(MUTATIONER), dåliga))
    return 1 if dåliga else 0


if __name__ == "__main__":
    sys.exit(main())
