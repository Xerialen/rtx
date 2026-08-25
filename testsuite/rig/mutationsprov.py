#!/usr/bin/env python3
"""Mutationsprov for testsuite/rig/.

En grind som aldrig setts falla ar en gron lampa, inte en grind. Provet
muterar en assertion i taget och kraver att EXAKT det test som pastar sig
bevaka den faller.

En mutation som ingen fangar ar dod kod som ser ut som en grind. Det ar
inte hypotetiskt: forsta korningen har hittade en sadan i `granska()`,
dar reset-kedjans kontroll var logiskt omojlig att fa att falla eftersom
ett tidigare steg redan garanterade dess villkor.

    python3 testsuite/rig/mutationsprov.py

Skriver i arbetstradet under korningen och lagger tillbaka originalet i
ett `finally`. Kor det inte mot ett trad du har ocommittade andringar i.

**Avbrott ar farligt och darfor bevakat.** Ett `finally` overlever inte
SIGKILL, och en avbruten korning lamnar da en AVVAPNAD grind (`if False:`)
kvar i arbetstradet — den ser ut som kod, och den foljer med in i en commit
om ingen tittar. Det hande i riggens eget prov 2026-08-25: ett bakgrundsjobb
stoppades och `portar.py` bar `if klass not in KLASSER:` -> `if False:`.
Darfor: en pagaende korning skriver `PAGAR`-filen bredvid sig med filen den
just nu har muterat, och nasta korning VAGRAR starta sa lange den ligger
kvar. Ligger den dar: stall tillbaka den namngivna filen
(`git checkout -- <fil>`) och ta bort `PAGAR`. Samma vakt, samma ordning och
samma konvention som `testsuite/tools/mutationsprov_dom.py`.
"""
import re
import subprocess
import sys
from pathlib import Path

RIG = Path(__file__).resolve().parent

#: Skrivs medan en fil ar muterad, tas bort nar den ar tillbakalagd. En
#: kvarlamnad fil ar beviset pa att arbetstradet INTE ar rort tillbaka.
#: Ignorerad via `testsuite/.gitignore` — den far aldrig committas.
PAGAR = RIG / "mutationsprov.PAGAR"

#: Ankare som anvands av flera mutationer. De star som konstanter for att en
#: felskriven kopia av en lang rad annars blir en tyst `HOPPAR` i stallet for
#: en mutation — och en mutation som inte kordes ser ut precis som en som
#: inte fangades.
ALLA_KLASSER = '{"forbjuden", "orord", "deploy", "lab", "ra-kontroll", "testsvit", "rigg"}'
UTAN_TESTSVIT = '{"forbjuden", "orord", "deploy", "lab", "ra-kontroll", "rigg"}'
UTAN_RIGG = '{"forbjuden", "orord", "deploy", "lab", "ra-kontroll", "testsvit"}'
SOM_JAMFORELSEN = 'if som is not None and som == rad.grupp:'

#: Vaktarnamn som INTE ar tester. `test_rig` ar `unittest.loader._FailedTest`
#: — den uppstar nar `portar.py` inte gar att IMPORTERA. Under M36/M37 (som
#: stader bort en klass ur BADE `KLASSER` och `ATKOMST`) far modulens egen
#: `assert set(ATKOMST) == set(KLASSER)` pa portar.py:46 importen att smalla,
#: sviten kor da 1 test i stallet for 81, och det som faller ar modulen som
#: vaktar sig sjalv — inte testsviten. De tva raknas darfor separat i
#: slutraden: talet "N/N" far inte lasas som "N bevakade av tester".
#: (QA:s avvikelse A-4, 2026-08-25.)
#:
#: De kan inte ges riktiga testvaktare utan att bryta agarregeln om nya
#: filer: varje test i `test_rig.py` ligger i den modul som inte gar att
#: importera under mutationen, sa ett fangande test maste bo i en NY
#: testmodul. Darfor markning i stallet for omskrivning.
IMPORTVAKTARE = {"test_rig"}

MUTATIONER = [
    ("portar.py", 'if klass not in KLASSER:', 'if False:', "test_neg_okand_klass"),
    ("portar.py", 'if roll not in ROLLER:', 'if False:', "test_neg_okand_roll"),
    ("portar.py", 'if port in ut:', 'if False:', "test_neg_dubbel_port"),
    ("portar.py", '"deploy": "neka",', '"deploy": "tillat",', "test_neg_deploypar_nekas"),
    ("portar.py", '"ra-kontroll": "order",', '"ra-kontroll": "tillat",', "test_neg_ra_kontroll_kraver_order"),
    ("portar.py", 'if rad is None:', 'if False:', "test_neg_okand_port_ar_inte_ledig"),
    ("gamedir.py", '"k_count": "45",', '"k_count": "10",', "test_riggkraven_ar_pinnade"),
    ("gamedir.py", '"k_noframechecks": "1",', '"k_noframechecks": "0",', "test_riggkraven_ar_pinnade"),
    ("gamedir.py", '"k_overtime": "0",', '"k_overtime": "1",', "test_riggkraven_ar_pinnade"),
    ("gamedir.py", 'if saknas:', 'if False:', "test_neg_sist_korda_filen_saknar_en_cvar"),
    ("gamedir.py", 'if s.lower().startswith("exec ") and var_mode_vag in s:', 'if False:', "test_neg_resetkedjan_kor_var_fil_sjalv"),
    ("gamedir.py", 'krockar = sorted(n for n, v in deras.items() if v != vara.get(n))', 'krockar = []', "test_neg_annat_usermode_avvapnar_riggen"),
    ("gamedir.py", '        if strukna:', '        if False:', "test_bygg_avvapnar_andra_lagen"),
    ("gamedir.py", 'if kalla in mal.parents:', 'if False:', "test_neg_gamedir_inuti_delade_tradet"),
    ("riggvakt.py", 'if not text:', 'if False:', "test_neg_las_tomt_ar_ledigt"),
    ("riggvakt.py", 'if ("korning=%s" % korning) not in text:', 'if False:', "test_neg_las_hos_annan_korning"),
    ("riggvakt.py", 'if r.returncode != 0:', 'if False:', "test_neg_frogbotdata_stammer_inte"),
    ("riggvakt.py", 'if vantad_sha and sha != vantad_sha:', 'if False:', "test_neg_qw_analyze_fel_sha"),
    ("aterstall.py", 'if verb in FORBJUDNA_SYSTEMD_VERB:', 'if False:', "test_neg_forbjudna_systemd_verb"),
    ("starta.py", '"--working-directory=%s" % basedir,', '"--working-directory=%s" % gamedir,', "test_kor_i_basedir_inte_i_gamediren"),
    ("res.sh", 'if ! python3 "$HAR/starta.py" "${STARTA[@]}"; then', 'if ! systemd-run --user --unit="$UNIT" "${STARTA[@]}"; then', "test_res_sh_reser_via_livsgrinden"),
    ("starta.py", '            if n:', '            if True:', "test_neg_faller_nar_ingen_lyssnare"),
    ("starta.py", 'if tillstand in DODA_TILLSTAND:', 'if False:', "test_neg_faller_direkt_pa_failad_unit"),
    ("starta.py", 'elif not Path("/proc/%d" % pid).is_dir():', 'elif False:', "test_neg_faller_nar_pid_inte_finns_i_proc"),
    ("starta.py", 'if gamedir.parent.resolve() != basedir:', 'if False:', "test_neg_gamedir_utanfor_basedir"),
    ("starta.py", 'if not (basedir / "id1").is_dir():', 'if False:', "test_neg_basedir_utan_id1"),
    ("starta.py", 'if not marker or not any(', 'if False and not any(', "test_lamnar_frammande_gamedir_ifred"),
    ("aterstall.py", 'bevis.append(_systemctl("reset-failed", bild["unit"], verkstall=verkstall))', 'pass', "test_aterstallningskedjan_gor_reset_failed"),
    ("gamedir.py", 'logg.append(kopiera_qwprogs(kalla, mal, pin))', 'pass', "test_spelkoden_kopieras_in_och_granskas"),
    ("gamedir.py", 'sha = sha256_av(dll)\n    if sha != vantad_sha:', 'sha = sha256_av(dll)\n    if False:', "test_neg_spelkod_med_fel_sha_vagras"),
    ("gamedir.py", 'sha = sha256_av(kall_fil)\n    if sha != vantad_sha:', 'sha = sha256_av(kall_fil)\n    if False:', "test_neg_kallans_sha_stammer_inte_med_pinnen"),
    ("gamedir.py", 'if not dll.is_file():', 'if False:', "test_neg_gamedir_utan_spelkod_vagras"),
    ("gamedir.py", 'if not kall_fil.is_file():', 'if False:', "test_neg_pinnad_bygga_saknas_i_tradet"),
    ("gamedir.py", 'if "/" in namn:', 'if False:', "test_neg_pinnen_far_inte_vara_en_sokvag"),
    ("gamedir.py", 'if len(rader) != 1:', 'if False:', "test_neg_pinnen_har_tva_rader"),

    # --- atkomsten `egen` (testsvit/rigg) och `som`-jamforelsen ---
    #
    # De tolv nedan kordes for hand i en scratchpad vid PR #71 och fanns
    # darfor ingenstans i repot: inte repeterbara av nagon annan, och de
    # kordes inte om vid nasta andring. Nu ar de en grind som andra kan
    # trycka pa. M11/M12 ar de tva som OVERLEVDE hela sviten (78/78 gron)
    # tills sina vaktare skrevs — de star har for att ingen ska behova
    # upptacka samma lucka en tredje gang.
    #
    # ALLA_KLASSER ar ankaret for `KLASSER`-frozensetet i portar.py.
    #
    # M36/M37: vaktaren `test_rig` ar INTE ett test utan
    # `unittest.loader._FailedTest` — se `IMPORTVAKTARE` ovan. Dessa tva
    # bevakas av portar.py:46:s egen importassertion, och skrivs ut och raknas
    # som just det.
    ("portar.py", ALLA_KLASSER, UTAN_TESTSVIT, "test_rig"),
    ("portar.py", ALLA_KLASSER, UTAN_RIGG, "test_rig"),
    ("portar.py", '"rigg": "egen",', '"rigg": "tillat",', "test_rigg_kraver_att_anroparen_uppger_sin_grupp"),
    ("portar.py", '"testsvit": "egen",', '"testsvit": "tillat",', "test_testsvit_kraver_att_anroparen_uppger_sin_grupp"),
    ("portar.py", SOM_JAMFORELSEN, 'if som is not None and rad.grupp.startswith(som):', "test_neg_rigg_matchar_inte_pa_prefix"),
    ("portar.py", '    if rad.atkomst == "tillat":\n        return rad',
     '    if som is not None and som == rad.grupp:\n        return rad\n'
     '    if rad.atkomst == "tillat":\n        return rad',
     "test_som_oppnar_inte_nagon_annan_klass"),
    ("portar.py", '    rad = tabell.get(port)\n    if rad is None:',
     '    rad = tabell.get(port)\n    if rad is None and som is None:',
     "test_neg_oredovisad_port_vagras_aven_med_som"),
    ("portar.py", SOM_JAMFORELSEN, 'if som is None or som == rad.grupp:', "test_neg_tomt_som_ar_inte_ett_ja"),
    # M9/M10: bada mappningarna samtidigt — kommer forbi assertionen, sa det
    # ar facittestet mot det riktiga valvet som maste ta dem.
    ("portar.py", (ALLA_KLASSER, '    "testsvit": "egen",\n'), (UTAN_TESTSVIT, ''),
     "test_riktiga_listan_gar_att_lasa"),
    ("portar.py", (ALLA_KLASSER, '    "rigg": "egen",\n'), (UTAN_RIGG, ''),
     "test_riktiga_listan_gar_att_lasa"),
    # M11/M12: overlevarna. En "hjalpsam" normalisering av `som`.
    ("portar.py", SOM_JAMFORELSEN, 'if som is not None and som.lower() == rad.grupp.lower():',
     "test_neg_skiftlage_i_som_ar_inte_ratt_grupp"),
    ("portar.py", SOM_JAMFORELSEN, 'if som is not None and som.strip() == rad.grupp:',
     "test_neg_blanksteg_kring_som_ar_inte_ratt_grupp"),
]


def kor_tester():
    r = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(RIG), "-t", str(RIG), "-v"],
        capture_output=True, text=True, cwd=str(RIG.parent.parent),
    )
    # Las sammanfattningsblocken, inte progressraderna: ett test med
    # docstring skriver "... FAIL" pa en ANNAN rad an testnamnet, sa en
    # regex mot progressraden missar precis de tester som ar bast
    # dokumenterade. (Hittat genom att harnesket sade OFANGAD om en
    # assertion som i sjalva verket foll.)
    fallna = set(re.findall(r"^(?:FAIL|ERROR): (\w+) \(", r.stderr, re.M))
    return r.returncode, fallna


def _vaktartext(vaktare):
    """Vad som SKRIVS UT om en vaktare. Rors inte av domslogiken.

    Domen ar och forblir `vaktare in fallna` pa det ra namnet; det har ar
    enbart etiketten i utdatan. En vaktare i `IMPORTVAKTARE` skrivs ut som
    vad den ar — en importassertion — sa att raden inte kan citeras som att
    ett test bevakade mutationen.
    """
    if vaktare in IMPORTVAKTARE:
        return "importassertion portar.py:46 (EJ test; unittest sager %s)" % vaktare
    return vaktare


def _fordelning():
    """Slutradens arliga fordelning, raknad ur MUTATIONER — inte hardkodad.

    Beskriver de DEKLARERADE vaktarna, sa raden ar sann aven nar nagon
    mutation gick utan ratt vaktare (det talet star pa raden ovanfor).
    """
    imp = sum(1 for m in MUTATIONER if m[3] in IMPORTVAKTARE)
    test = len(MUTATIONER) - imp
    return (
        "deklarerade vaktare: %d namngivna tester + %d importassertion"
        " (M36/M37, portar.py:46).\n"
        "Under de tva gar modulen inte att importera, sa hela sviten kokar ihop"
        " till ETT\nfelrapporterande test — det ar modulen som vaktar sig sjalv,"
        " inte testsviten.\n"
        "Citera darfor \"%d/%d\" bara med den fordelningen: mot testsviten ar"
        " den arliga\nsiffran %d av %d."
        % (test, imp, len(MUTATIONER), len(MUTATIONER), test, len(MUTATIONER))
    )


def main():
    if PAGAR.exists():
        # Texten sager INNEHALLA i stallet for "bara". Tools-varianten bar
        # QA:s avvikelse A6 (2026-08-25) — ett tappat "bära" som en operator
        # laser fel under tidspress. Den kopierades aldrig hit, och sedan
        # QA:s atgardspunkt B5 sager tools-varianten samma sak som denna.
        print("VAGRAR STARTA — en tidigare korning avbrots och arbetstradet")
        print("kan innehalla en avvapnad grind. Aterstall filen nedan och")
        print("ta bort %s:" % PAGAR)
        print(PAGAR.read_text(encoding="utf-8").strip())
        return 2
    rc, fallna = kor_tester()
    if rc != 0:
        print("BASLINJEN AR INTE GRON — mutationsprov meningslost")
        print(sorted(fallna))
        return 1
    print("baslinje: gron\n")

    dåliga = 0
    for fil, gammal, ny, vaktare in MUTATIONER:
        p = RIG / fil
        orig = p.read_text(encoding="utf-8")
        # En mutation kan behova rora TVA stallen samtidigt. Den som stader
        # bort en klass ur BADE `KLASSER` och `ATKOMST` kommer forbi modulens
        # `assert set(ATKOMST) == set(KLASSER)` — och da maste facittestet ta
        # den. Med ett enda ankare gick den mutationen inte att uttrycka, sa
        # `gammal`/`ny` far ocksa vara lika langa tuplar.
        par = list(zip(gammal, ny)) if isinstance(gammal, tuple) else [(gammal, ny)]
        etikett = par[0][0].strip()[:45]
        traffar = [orig.count(g) for g, _ in par]
        if traffar != [1] * len(par):
            print("HOPPAR %s %r: ankartraffar %s" % (fil, etikett, traffar))
            dåliga += 1
            continue
        muterad = orig
        for g, n in par:
            muterad = muterad.replace(g, n)
        # PAGAR skrivs FORE mutationen och tas bort EFTER aterstallningen, sa
        # fonstret dar tradet ar muterat utan markor ar tomt. Ett avbrott kan
        # da bara ge falsklarm, aldrig tyst skada.
        PAGAR.write_text(
            "muterad fil: %s\nankare: %s\n" % (p, etikett),
            encoding="utf-8",
        )
        p.write_text(muterad, encoding="utf-8")
        try:
            _, f = kor_tester()
        finally:
            p.write_text(orig, encoding="utf-8")
            PAGAR.unlink(missing_ok=True)

        if not f:
            print("OFANGAD  %-12s %-45s  INGEN test foll" % (fil, etikett))
            dåliga += 1
        elif vaktare in f:
            extra = sorted(f - {vaktare})
            print("FALLD    %-12s %-45s  av %s%s"
                  % (fil, etikett, _vaktartext(vaktare),
                     (" (+%d till)" % len(extra)) if extra else ""))
        else:
            print("FEL VAKT %-12s %-45s  vantade %s, foll %s"
                  % (fil, etikett, vaktare, sorted(f)))
            dåliga += 1

    print("\n%d mutationer, %d utan ratt vaktare" % (len(MUTATIONER), dåliga))
    print(_fordelning())
    return 1 if dåliga else 0


if __name__ == "__main__":
    sys.exit(main())
