#!/usr/bin/env python3
"""Enhetstester + negativkontroller för testsuite/rig/.

Varje assertion som påstår sig bevaka en fallgrop har ett test som matar
den en känt trasig indata och kräver att den faller. En grind som aldrig
setts falla är en grön lampa, inte en grind.

Körs offline: `python3 -m unittest discover -s testsuite/rig -v`.
Ingen rigg, ingen server, ingen port reses.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HAR = Path(__file__).resolve().parent
sys.path.insert(0, str(HAR))

import aterstall  # noqa: E402
import gamedir  # noqa: E402
import portar  # noqa: E402
import riggvakt  # noqa: E402
import starta  # noqa: E402

PORTLISTA = HAR.parent.parent / "docs" / "PORTAR.md"

MINI = """# rubrik

| port | klass | roll | grupp | ägare / belägg |
|---|---|---|---|---|
| 27570 | lab | spel | lab-b | trio B |
| 27970 | lab | ctl | lab-b | trio B |
| 29570 | lab | qtv | lab-b | trio B |
| 27550 | forbjuden | spel | main | main |
| 28502 | orord | spel | ktx | pid 1331 |
| 27996 | deploy | ctl | tbx-d1 | par |
| 27990 | ra-kontroll | ctl | fasttrack-ra | RA |
| 27580 | lab | spel | halv | halv trio |
| 27700 | testsvit | spel | t3 | T3:s match_server |
| 28150 | rigg | ctl | navdok-1-klient-a | parallellriggen navdok-1 |

efter tabellen
"""


def skriv(text: str) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    f.write(text)
    f.close()
    return Path(f.name)


class TestPortlistan(unittest.TestCase):
    def test_riktiga_listan_gar_att_lasa(self):
        t = portar.las_tabell(PORTLISTA)
        # Ordervillkoren: de forbjudna, KTX-paret, trior, RA-kontrollen.
        for p in (27550, 27991, 27530):
            self.assertEqual(t[p].klass, "forbjuden", "port %d" % p)
        for p in (28502, 28503):
            self.assertEqual(t[p].klass, "orord", "port %d" % p)
        self.assertEqual(t[27990].klass, "ra-kontroll")
        # 27700 stod som `forbjuden` har tills agarbeslutet 2026-08-24 gjorde
        # den till T3:s match_server. Testet kravde den gamla klassen i ett
        # dygn utan att nagon sag det, for ingen CI kor den har sviten.
        for p in (27700, 27710, 27980, 28100, 28101, 28110, 29701, 29711):
            self.assertEqual(t[p].klass, "testsvit", "port %d" % p)
        for p in (28150, 28151, 28160):
            self.assertEqual(t[p].klass, "rigg", "port %d" % p)
        self.assertEqual(
            portar.trior(t),
            {
                "lab-a": {"spel": 27580, "ctl": 27960, "qtv": 29580},
                "lab-b": {"spel": 27570, "ctl": 27970, "qtv": 29570},
            },
        )

    def test_halv_trio_returneras_inte(self):
        t = portar.las_tabell(skriv(MINI))
        self.assertIn("lab-b", portar.trior(t))
        self.assertNotIn("halv", portar.trior(t))

    # --- negativkontroller: parsern ska falla, inte tolka valvilligt ---

    def test_neg_okand_klass(self):
        bad = MINI.replace("| 27550 | forbjuden |", "| 27550 | nastan-forbjuden |")
        with self.assertRaises(portar.Portfel) as cm:
            portar.las_tabell(skriv(bad))
        self.assertIn("okänd klass", str(cm.exception))

    def test_neg_okand_roll(self):
        bad = MINI.replace("| 27570 | lab | spel |", "| 27570 | lab | spelport |")
        with self.assertRaises(portar.Portfel) as cm:
            portar.las_tabell(skriv(bad))
        self.assertIn("okänd roll", str(cm.exception))

    def test_neg_dubbel_port(self):
        bad = MINI.replace("| 29570 | lab | qtv | lab-b |", "| 27570 | lab | qtv | lab-b |")
        with self.assertRaises(portar.Portfel) as cm:
            portar.las_tabell(skriv(bad))
        self.assertIn("två gånger", str(cm.exception))

    def test_neg_fel_rubrik(self):
        bad = MINI.replace("| port | klass | roll | grupp |", "| portnr | klass | roll | grupp |")
        with self.assertRaises(portar.Portfel) as cm:
            portar.las_tabell(skriv(bad))
        self.assertIn("hittade ingen tabell", str(cm.exception))

    def test_neg_fel_antal_kolumner(self):
        bad = MINI.replace("| 27550 | forbjuden | spel | main | main |", "| 27550 | forbjuden | spel | main |")
        with self.assertRaises(portar.Portfel) as cm:
            portar.las_tabell(skriv(bad))
        self.assertIn("kolumner", str(cm.exception))

    def test_neg_port_inte_tal(self):
        bad = MINI.replace("| 27550 |", "| 27550/udp |")
        with self.assertRaises(portar.Portfel):
            portar.las_tabell(skriv(bad))

    def test_neg_saknad_fil(self):
        with self.assertRaises(portar.Portfel):
            portar.las_tabell("/finns/inte/PORTAR.md")


class TestAtkomst(unittest.TestCase):
    def setUp(self):
        self.t = portar.las_tabell(skriv(MINI))

    def test_lab_tillaten(self):
        self.assertEqual(portar.krav_tillaten(self.t, 27570, "spel").grupp, "lab-b")

    def test_neg_forbjuden_nekas(self):
        with self.assertRaises(portar.Portfel):
            portar.krav_tillaten(self.t, 27550, "spel")

    def test_neg_orord_nekas(self):
        with self.assertRaises(portar.Portfel):
            portar.krav_tillaten(self.t, 28502, "spel")

    def test_neg_deploypar_nekas(self):
        with self.assertRaises(portar.Portfel):
            portar.krav_tillaten(self.t, 27996, "ctl")

    def test_neg_ra_kontroll_kraver_order(self):
        with self.assertRaises(portar.Portfel) as cm:
            portar.krav_tillaten(self.t, 27990, "ctl")
        self.assertIn("uttrycklig order", str(cm.exception))

    def test_neg_okand_port_ar_inte_ledig(self):
        # 28000 och 27599 sag lediga ut just for att de saknades i listan.
        with self.assertRaises(portar.Portfel) as cm:
            portar.krav_tillaten(self.t, 28000, "qtv")
        self.assertIn("Oredovisad", str(cm.exception))

    # --- klasserna med atkomsten `egen`: testsvit och rigg ---
    #
    # Forvalet ar vagran. Den som inte sager vem den ar far nej, och den som
    # sager fel namn far ocksa nej. Bada riktningarna provas, for en grind
    # som bara setts saga ja ar ingen grind.

    def test_testsvit_kraver_att_anroparen_uppger_sin_grupp(self):
        with self.assertRaises(portar.Portfel) as cm:
            portar.krav_tillaten(self.t, 27700, "spel")
        self.assertIn("tillhör", str(cm.exception))
        self.assertIn("'t3'", str(cm.exception))

    def test_testsvit_slapps_igenom_for_ratt_grupp(self):
        rad = portar.krav_tillaten(self.t, 27700, "spel", som="t3")
        self.assertEqual(rad.grupp, "t3")
        self.assertEqual(rad.klass, "testsvit")

    def test_neg_testsvit_nekas_for_fel_grupp(self):
        with self.assertRaises(portar.Portfel):
            portar.krav_tillaten(self.t, 27700, "spel", som="t4")

    def test_rigg_kraver_att_anroparen_uppger_sin_grupp(self):
        with self.assertRaises(portar.Portfel):
            portar.krav_tillaten(self.t, 28150, "ctl")

    def test_rigg_slapps_igenom_for_ratt_grupp(self):
        rad = portar.krav_tillaten(self.t, 28150, "ctl", som="navdok-1-klient-a")
        self.assertEqual(rad.klass, "rigg")

    def test_neg_rigg_matchar_inte_pa_prefix(self):
        """En rigg som heter `navdok` far INTE ta `navdok-1`:s portar.

        Prefixmatchning ar hur fel rigg reses pa ratt portar. Matchningen ar
        exakt, och det ar just den narapa-traffen som provas har.
        """
        for nastan in ("navdok", "navdok-1", "navdok-1-klient", "navdok-1-klient-b"):
            with self.assertRaises(portar.Portfel, msg=nastan):
                portar.krav_tillaten(self.t, 28150, "ctl", som=nastan)

    def test_neg_tomt_som_ar_inte_ett_ja(self):
        for tomt in ("", None):
            with self.assertRaises(portar.Portfel, msg=repr(tomt)):
                portar.krav_tillaten(self.t, 28150, "ctl", som=tomt)

    def test_som_oppnar_inte_nagon_annan_klass(self):
        """`som` ar ingen huvudnyckel: den galler bara atkomsten `egen`.

        Utan det har testet hade ett `som`-varde kunnat bli en universell
        forbigang av forbjuden/orord/deploy/ra-kontroll.
        """
        for port, grupp in ((27550, "main"), (28502, "ktx"), (27996, "tbx-d1"), (27990, "fasttrack-ra")):
            with self.assertRaises(portar.Portfel, msg="port %d" % port):
                portar.krav_tillaten(self.t, port, "spel", som=grupp)

    def test_neg_oredovisad_port_vagras_aven_med_som(self):
        """Regeln «port utanfor valvet => vagra» far inte forsvagas av `som`."""
        with self.assertRaises(portar.Portfel) as cm:
            portar.krav_tillaten(self.t, 28160, "ctl", som="navdok-1-kastbot")
        self.assertIn("Oredovisad", str(cm.exception))

    def test_atkomst_tacker_hela_ordforradet(self):
        self.assertEqual(set(portar.ATKOMST), set(portar.KLASSER))
        self.assertEqual(portar.ATKOMST["testsvit"], "egen")
        self.assertEqual(portar.ATKOMST["rigg"], "egen")


def bygg_kallträd(rot: Path, *, ktx_cfg: str | None = None, setmaster: bool = True) -> Path:
    """Ett minimalt KTX-trad med reset-kedjans fallor pa plats."""
    kalla = rot / "delad-ktx"
    (kalla / "configs" / "usermodes" / "4on4").mkdir(parents=True)
    (kalla / "configs" / "usermodes" / "4on4" / "default.cfg").write_text("set k_pow 1\n")
    # Ett andra lage, for att prova att ett lagesbyte inte avvapnar riggen.
    (kalla / "configs" / "usermodes" / "3on3").mkdir(parents=True)
    (kalla / "configs" / "usermodes" / "3on3" / "default.cfg").write_text("set k_pow 1\n")
    (kalla / "server.cfg").write_text(
        ("setmaster master.quakeservers.net:27000\n" if setmaster else "")
        + "set k_fb_enabled 0\nset k_demotxt_format json\n"
    )
    (kalla / "mvdsv.cfg").write_text(
        "maxclients 32\nsv_crypt_rcon 1\nsv_demodir demos\nsv_timeout 65\n"
    )
    (kalla / "ktx.cfg").write_text(
        ktx_cfg
        if ktx_cfg is not None
        else (
            "set k_noframechecks 0\nset k_membercount 1\nset k_count 10\n"
            "set k_overtime 1\nset k_exttime 3\nset k_matchless 0\nset timelimit 10\n"
        )
    )
    (kalla / "pwd.cfg").write_text('rcon_password "hemligt"\n')
    skriv_pin(rot, kalla)
    return kalla


VAL_T3 = dict(seats_per_side=4, timelimit_min=5, demodir="demos", rcon_password="x")

#: Den pinnade spelkoden i testerna. Innehallet ar godtyckligt; det ar
#: pinnens sha256 som ar kontraktet.
PIN_NAMN = "qwprogs-kbot-9.9.9-prov.so"
PIN_DATA = b"latsas-spelkod\n"
PIN_SHA = hashlib.sha256(PIN_DATA).hexdigest()


def skriv_pin(rot: Path, kalla: Path, *, sha: str | None = None, namn: str | None = None) -> Path:
    """Lagger spelkoden i det delade tradet och skriver en pinne for den."""
    (kalla / PIN_NAMN).write_bytes(PIN_DATA)
    pin = rot / "qwprogs.pin"
    pin.write_text(
        "# provpinne\n%s  %s\n" % (sha or PIN_SHA, namn or PIN_NAMN), encoding="utf-8"
    )
    return pin


class TestGamedir(unittest.TestCase):
    def test_bygger_och_haller(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            bevis = gamedir.granska(rot / "privat", val, rot / "qwprogs.pin")
            self.assertTrue(any("riggkritiska cvars satta" in b for b in bevis))
            self.assertIn("inga masterservrar", bevis)

    def test_neg_sist_korda_filen_saknar_en_cvar(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            cfg = rot / "privat" / "configs" / "usermodes" / "4on4" / "default.cfg"
            kvar = [r for r in cfg.read_text().splitlines() if "k_noframechecks" not in r]
            cfg.write_text("\n".join(kvar) + "\n")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val, rot / "qwprogs.pin")
            self.assertIn("k_noframechecks", str(cm.exception))

    def test_neg_fel_varde_i_sist_korda_filen(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            cfg = rot / "privat" / "configs" / "usermodes" / "4on4" / "default.cfg"
            cfg.write_text(cfg.read_text().replace("set k_count 45", "set k_count 10"))
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val, rot / "qwprogs.pin")
            self.assertIn("k_count", str(cm.exception))

    def test_neg_resetkedjan_kor_var_fil_sjalv(self):
        """Fallan: kor reset-kedjan sjalv var sist korda fil, sa kors den FORE
        KTX:s mode-ominitiering i stallet for efter, och ominitieringen
        stampar tillbaka. Filen ser ratt ut och riggen ar anda av."""
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            s = rot / "privat" / "server.cfg"
            s.write_text(s.read_text() + "\nexec configs/usermodes/4on4/default.cfg\n")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val, rot / "qwprogs.pin")
            self.assertIn("mode-ominitiering", str(cm.exception))

    def test_bygg_avvapnar_andra_lagen(self):
        """Det stock-tradet faktiskt ser ut sa: 10on10/default.cfg satter
        timelimit och overtime-paret. Bygget maste stryka dem ur VAR kopia,
        annars gar riggen inte att resa alls."""
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            farlig = kalla / "configs" / "usermodes" / "3on3" / "default.cfg"
            farlig.write_text("set k_pow 1\nset k_overtime 1\nset timelimit 20\n")
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            # Bygget far inte rora det delade tradet.
            self.assertIn("set k_overtime 1", farlig.read_text())
            # Var kopia ska vara avvapnad, sa granskningen haller.
            gamedir.granska(rot / "privat", val, rot / "qwprogs.pin")

    def test_neg_annat_usermode_avvapnar_riggen(self):
        """Fallan: byter servern lage kors DEN modens default.cfg sist, och
        var ar aldrig med. Ett lage som satter nagot riggkritiskt annorlunda
        avvapnar riggen tyst."""
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            annat = rot / "privat" / "configs" / "usermodes" / "3on3" / "default.cfg"
            annat.write_text(annat.read_text() + "set k_noframechecks 0\n")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val, rot / "qwprogs.pin")
            self.assertIn("k_noframechecks", str(cm.exception))
            self.assertIn("3on3", str(cm.exception))

    def test_neg_masterservrar_kvar(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            s = rot / "privat" / "server.cfg"
            s.write_text(s.read_text() + "\nsetmaster master.quakeworld.nu:27000\n")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val, rot / "qwprogs.pin")
            self.assertIn("masterservrar", str(cm.exception))

    def test_neg_gamedir_inuti_delade_tradet(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.bygg(kalla, kalla / "privat", val, None, rot / "qwprogs.pin")
            self.assertIn("delade källträdet", str(cm.exception))

    def test_neg_t4_utan_frogbotdata(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t4", **VAL_T3)
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            self.assertIn("frogbot", str(cm.exception))

    def test_t4_far_frogbots_t3_far_inte(self):
        self.assertEqual(gamedir.overrides(gamedir.Riggval(tier="t3", **VAL_T3))["k_fb_enabled"], "0")
        self.assertEqual(gamedir.overrides(gamedir.Riggval(tier="t4", **VAL_T3))["k_fb_enabled"], "1")

    def test_riggkraven_ar_pinnade(self):
        """Utan den har pinningen ar granskningen cirkular: `granska()` jamfor
        mot `overrides()`, sa en andring i `overrides()` skulle andra bade
        riggen och forvantan och passera tyst. Varden ur testsuite/README.md."""
        o = gamedir.overrides(gamedir.Riggval(tier="t3", **VAL_T3))
        self.assertEqual(o["k_noframechecks"], "1")  # headless klienter
        self.assertEqual(o["k_lockmode"], "0")  # natverksklienter far joina
        self.assertEqual(o["k_count"], "45")  # navmesh hinner byggas
        self.assertEqual(o["k_overtime"], "0")  # overtime spranger matchslutet
        self.assertEqual(o["k_exttime"], "0")
        self.assertEqual(o["k_matchless"], "0")  # matchless tvingar teamplay 0
        self.assertEqual(o["sv_crypt_rcon"], "0")
        self.assertEqual(o["maxclients"], "16")  # spoken efter rivna klienter
        self.assertEqual(o["sv_timeout"], "30")
        self.assertEqual(o["k_demotxt_format"], "json")  # enda poangoraklet
        self.assertEqual(o["timelimit"], str(VAL_T3["timelimit_min"]))
        self.assertEqual(o["sv_demodir"], VAL_T3["demodir"])

    def test_membercount_foljer_seats(self):
        # Vid 3 vagrar KTX tyst den fjarde frogboten.
        val = gamedir.Riggval(tier="t4", seats_per_side=4, timelimit_min=5, demodir="d", rcon_password="x")
        self.assertEqual(gamedir.overrides(val)["k_membercount"], "4")


class TestSpelkoden(unittest.TestCase):
    """Skarpvalideringens fynd 3: gamediren saknade KTX-spelkoden, sa mvdsv
    foll tillbaka pa qwprogs.qvm och dog med
    `PR1_LoadProgs: couldn't load progs.dat`."""

    def test_spelkoden_kopieras_in_och_granskas(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            logg = gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            dll = rot / "privat" / "qwprogs.so"
            self.assertTrue(dll.is_file(), logg)
            self.assertEqual(dll.read_bytes(), PIN_DATA)
            bevis = gamedir.granska(rot / "privat", val, rot / "qwprogs.pin")
            self.assertTrue(any("pinnad spelkod" in b for b in bevis), bevis)

    def test_neg_gamedir_utan_spelkod_vagras(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            (rot / "privat" / "qwprogs.so").unlink()
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val, rot / "qwprogs.pin")
            self.assertIn("qwprogs.so", str(cm.exception))
            self.assertIn("progs.dat", str(cm.exception))

    def test_neg_spelkod_med_fel_sha_vagras(self):
        """En annan bygga an den pinnade ar en annan matning."""
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            (rot / "privat" / "qwprogs.so").write_bytes(b"nagon annan bygga\n")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val, rot / "qwprogs.pin")
            self.assertIn("pinnen säger", str(cm.exception))

    def test_neg_kallans_sha_stammer_inte_med_pinnen(self):
        """Det delade tradet bar 81 qwprogs-varianter. Stammer inte den
        pinnade byggan ska riggen inte resas alls."""
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            skriv_pin(rot, kalla, sha="0" * 64)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            self.assertIn("fel sha256", str(cm.exception))

    def test_neg_pinnad_bygga_saknas_i_tradet(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            (kalla / PIN_NAMN).unlink()
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.bygg(kalla, rot / "privat", val, None, rot / "qwprogs.pin")
            self.assertIn("ägarbeslut", str(cm.exception))

    def test_neg_pinnen_far_inte_vara_en_sokvag(self):
        """Symlanklaxan: pinnen ar ett FILNAMN i det delade tradet. En sokvag
        hade flyttat kallan ut ur tradet."""
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            skriv_pin(rot, kalla, namn="/nagon/annanstans/qwprogs.so")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.las_pin(rot / "qwprogs.pin")
            self.assertIn("inte en", str(cm.exception))

    def test_neg_pinnen_saknas(self):
        with self.assertRaises(gamedir.Riggfel):
            gamedir.las_pin(Path("/finns/inte/qwprogs.pin"))

    def test_neg_pinnen_har_tva_rader(self):
        with tempfile.TemporaryDirectory() as d:
            pin = Path(d) / "qwprogs.pin"
            pin.write_text("%s  a.so\n%s  b.so\n" % (PIN_SHA, PIN_SHA))
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.las_pin(pin)
            self.assertIn("två pinnar", str(cm.exception))

    def test_neg_pinnen_har_trasig_sha(self):
        with tempfile.TemporaryDirectory() as d:
            pin = Path(d) / "qwprogs.pin"
            pin.write_text("inte-en-sha  a.so\n")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.las_pin(pin)
            self.assertIn("64 hextecken", str(cm.exception))

    def test_den_incheckade_pinnen_ar_lasbar(self):
        """Pinnen som faktiskt galler ska ga att lasa, och namnge en bygga."""
        namn, sha = gamedir.las_pin(gamedir.PIN_STANDARD)
        self.assertTrue(namn.startswith("qwprogs"), namn)
        self.assertEqual(len(sha), 64)


class TestRiggvakt(unittest.TestCase):
    def test_las_taget_i_var_korning(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".rig-lock"
            p.write_text("korning=t3-prov pid=1234\n")
            self.assertIn("t3-prov", riggvakt.kolla_las(p, "t3-prov"))

    def test_neg_las_saknas(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(riggvakt.Vaktfel) as cm:
                riggvakt.kolla_las(Path(d) / ".rig-lock", "t3-prov")
            self.assertIn("finns inte", str(cm.exception))

    def test_neg_las_tomt_ar_ledigt(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".rig-lock"
            p.write_text("")
            with self.assertRaises(riggvakt.Vaktfel) as cm:
                riggvakt.kolla_las(p, "t3-prov")
            self.assertIn("tomt", str(cm.exception))

    def test_neg_las_hos_annan_korning(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".rig-lock"
            p.write_text("korning=ra-reg-full pid=99\n")
            with self.assertRaises(riggvakt.Vaktfel) as cm:
                riggvakt.kolla_las(p, "t3-prov")
            self.assertIn("någon annan", str(cm.exception))

    def test_neg_frogbotdata_stammer_inte(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            bots = rot / "bots"
            (bots / "maps").mkdir(parents=True)
            (bots / "maps" / "dm3.bot").write_text("ratt innehall\n")
            man = rot / "bots.sha256"
            r = subprocess.run(
                ["sha256sum", "maps/dm3.bot"], cwd=bots, capture_output=True, text=True, check=True
            )
            man.write_text(r.stdout)
            self.assertEqual(riggvakt.kolla_frogbotdata(bots, man), ["frogbot-data: 1 filer, alla sha256 stämmer"])
            # Kant trasig indata: datat andras efter att manifestet skrevs.
            (bots / "maps" / "dm3.bot").write_text("nagot annat\n")
            with self.assertRaises(riggvakt.Vaktfel) as cm:
                riggvakt.kolla_frogbotdata(bots, man)
            self.assertIn("stämmer inte", str(cm.exception))

    def test_neg_frogbotdata_saknas(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(riggvakt.Vaktfel):
                riggvakt.kolla_frogbotdata(Path(d) / "bots", Path(d) / "m.sha256")

    def test_neg_qw_analyze_fel_sha(self):
        with tempfile.TemporaryDirectory() as d:
            b = Path(d) / "qw-analyze"
            b.write_text("#!/bin/sh\n")
            b.chmod(0o755)
            with self.assertRaises(riggvakt.Vaktfel) as cm:
                riggvakt.kolla_qw_analyze(b, "0" * 64)
            self.assertIn("fel sha256", str(cm.exception))

    def test_neg_qw_analyze_saknas(self):
        with self.assertRaises(riggvakt.Vaktfel):
            riggvakt.kolla_qw_analyze(Path("/finns/inte/qw-analyze"), None)


class TestAterstallning(unittest.TestCase):
    def test_neg_forbjudna_systemd_verb(self):
        for verb in sorted(aterstall.FORBJUDNA_SYSTEMD_VERB):
            with self.assertRaises(riggvakt.Vaktfel, msg=verb) as cm:
                aterstall._systemctl(verb, "nagon.service", verkstall=False)
            self.assertIn("körs aldrig", str(cm.exception))

    def test_torrkorning_ror_ingenting(self):
        self.assertTrue(
            aterstall._systemctl("stop", "x.service", verkstall=False).startswith("SKULLE KÖRA")
        )

    def test_utan_ogonblicksbild_vagras(self):
        with tempfile.TemporaryDirectory() as d:
            rc = aterstall.main(
                [
                    "aterstall",
                    "--ogonblicksbild",
                    str(Path(d) / "finns-inte.json"),
                    "--verdikt-ut",
                    str(Path(d) / "v.json"),
                ]
            )
            self.assertEqual(rc, 2)

    def test_neg_las_kvar_i_vart_namn_ger_fail(self):
        with tempfile.TemporaryDirectory() as d:
            las = Path(d) / ".rig-lock"
            las.write_text("korning=t3-prov pid=1\n")
            bild = {"korning": "t3-prov", "unit": "rtx-t3-prov", "portar": {}, "aktiva_units": [], "moduler": {}}
            _, avvik = aterstall.aterstall(bild, verkstall=False, lasfil=las)
            self.assertTrue(any("rigglåset står kvar" in a for a in avvik), avvik)

    def test_las_slappt_ger_inget_avvik(self):
        with tempfile.TemporaryDirectory() as d:
            las = Path(d) / ".rig-lock"
            las.write_text("")
            bild = {"korning": "t3-prov", "unit": "rtx-t3-prov", "portar": {}, "aktiva_units": [], "moduler": {}}
            _, avvik = aterstall.aterstall(bild, verkstall=False, lasfil=las)
            self.assertEqual(avvik, [])


def bygg_basedir(rot: Path, *, med_id1: bool = True) -> tuple[Path, Path]:
    """Minimal basedir: mvdsv + id1/ + en gamedir som syskon."""
    base = rot / "basedir"
    base.mkdir(parents=True)
    mvdsv = base / "mvdsv"
    mvdsv.write_text("#!/bin/sh\n")
    mvdsv.chmod(0o755)
    if med_id1:
        (base / "id1").mkdir()
    gd = base / "privat-t3"
    gd.mkdir()
    return mvdsv, gd


class TestStart(unittest.TestCase):
    """Skarpvalideringen 2026-08-23: servern dog i samma sekund den startades,
    for att arbetskatalogen var gamediren i stallet for basedir."""

    def test_kor_i_basedir_inte_i_gamediren(self):
        with tempfile.TemporaryDirectory() as d:
            mvdsv, gd = bygg_basedir(Path(d))
            argv = starta.start_argv("rtx-t3-prov", mvdsv, gd, 27580, 10800)
            base = mvdsv.parent
            # Arbetskatalogen ar basedir. mvdsv loser id1/ och -game mot cwd.
            self.assertIn("--working-directory=%s" % base, argv)
            self.assertNotIn("--working-directory=%s" % gd, argv)
            # Gamediren namnges med -game, som ett NAMN, inte en sokvag.
            self.assertIn("-game", argv)
            self.assertEqual(argv[argv.index("-game") + 1], "privat-t3")
            self.assertEqual(argv[argv.index("-port") + 1], "27580")

    def test_mvdsv_som_symlank_flyttar_inte_basedir(self):
        """`mvdsv` ar normalt en symlank till en versionsstamplad binar.
        Foljer man den ut ur katalogen blir basedir fel katalog."""
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            riktig = rot / "annan-plats" / "mvdsv-1.20-dev-abc123"
            riktig.parent.mkdir(parents=True)
            riktig.write_text("#!/bin/sh\n")
            riktig.chmod(0o755)
            base = rot / "basedir"
            base.mkdir()
            (base / "id1").mkdir()
            lank = base / "mvdsv"
            lank.symlink_to(riktig)
            gd = base / "privat-t3"
            gd.mkdir()
            argv = starta.start_argv("u", lank, gd, 27580, 10800)
            self.assertIn("--working-directory=%s" % base, argv)
            self.assertNotIn("--working-directory=%s" % riktig.parent, argv)

    def test_neg_gamedir_utanfor_basedir(self):
        with tempfile.TemporaryDirectory() as d:
            mvdsv, _ = bygg_basedir(Path(d))
            annan = Path(d) / "nagon-annanstans"
            annan.mkdir()
            with self.assertRaises(riggvakt.Vaktfel) as cm:
                starta.start_argv("u", mvdsv, annan, 27580, 10800)
            self.assertIn("basedir", str(cm.exception))

    def test_neg_basedir_utan_id1(self):
        """Utan id1/ hittar mvdsv varken pak eller maps/start.bsp och dor med
        'Couldn't spawn a server' - exakt felet skarpvalideringen sag."""
        with tempfile.TemporaryDirectory() as d:
            mvdsv, gd = bygg_basedir(Path(d), med_id1=False)
            with self.assertRaises(riggvakt.Vaktfel) as cm:
                starta.start_argv("u", mvdsv, gd, 27580, 10800)
            self.assertIn("id1", str(cm.exception))


class TestLivsgrind(unittest.TestCase):
    """Skarpvalideringen: res.sh skrev 'klar' rc=0 med noll lyssnare och en
    failad unit. systemd-run atervander nar uniten ar startad, inte nar
    servern lever."""

    def _tillstand(self, aktiv="active", pid=None):
        pid = str(os.getpid()) if pid is None else str(pid)
        return lambda unit, egenskap: aktiv if egenskap == "ActiveState" else pid

    def test_slapper_igenom_nar_servern_svarar(self):
        bevis = starta.vanta_liv(
            "u", 27580, 5,
            las_tillstand=self._tillstand(),
            las_lyssnare=lambda p: 2,
            sov=lambda s: None,
        )
        self.assertTrue(any("lyssnare" in b for b in bevis), bevis)

    def test_neg_faller_nar_ingen_lyssnare(self):
        """Processen lever men porten ar tyst - riggen svarar inte."""
        with self.assertRaises(riggvakt.Vaktfel) as cm:
            starta.vanta_liv(
                "u", 27580, 2,
                las_tillstand=self._tillstand(),
                las_lyssnare=lambda p: 0,
                sov=lambda s: None,
            )
        self.assertIn("noll lyssnare", str(cm.exception))

    def test_neg_faller_direkt_pa_failad_unit(self):
        with self.assertRaises(riggvakt.Vaktfel) as cm:
            starta.vanta_liv(
                "u", 27580, 60,
                las_tillstand=self._tillstand(aktiv="failed"),
                las_lyssnare=lambda p: 0,
                sov=lambda s: None,
            )
        self.assertIn("kom aldrig upp", str(cm.exception))

    def test_neg_faller_nar_mainpid_ar_noll(self):
        with self.assertRaises(riggvakt.Vaktfel) as cm:
            starta.vanta_liv(
                "u", 27580, 2,
                las_tillstand=self._tillstand(pid=0),
                las_lyssnare=lambda p: 5,
                sov=lambda s: None,
            )
        self.assertIn("MainPID", str(cm.exception))

    def test_neg_faller_nar_pid_inte_finns_i_proc(self):
        """Falskt negativ: servern dor mitt i. PID:en finns kvar i unitens
        egenskaper men inte i /proc."""
        with self.assertRaises(riggvakt.Vaktfel) as cm:
            starta.vanta_liv(
                "u", 27580, 2,
                las_tillstand=self._tillstand(pid=999999),
                las_lyssnare=lambda p: 5,
                sov=lambda s: None,
            )
        self.assertIn("/proc", str(cm.exception))

    def test_servern_far_komma_upp_langsamt(self):
        """Riggen ska inte fallas for att den behover nagra sekunder."""
        svar = iter([0, 0, 3])
        bevis = starta.vanta_liv(
            "u", 27580, 30,
            las_tillstand=self._tillstand(),
            las_lyssnare=lambda p: next(svar),
            sov=lambda s: None,
        )
        self.assertTrue(bevis)


class TestStadning(unittest.TestCase):
    def test_lamnar_frammande_gamedir_ifred(self):
        """En katalog utan var genererade markor ar inte var att radera."""
        with tempfile.TemporaryDirectory() as d:
            mvdsv, gd = bygg_basedir(Path(d))
            (gd / "viktigt.txt").write_text("inte vart\n")
            gjort = starta.stada("rtx-t3-prov-finns-ej", gd, mvdsv)
            self.assertTrue(gd.is_dir())
            self.assertTrue(any("inte vår" in g for g in gjort), gjort)

    def test_stadar_var_egen_gamedir(self):
        with tempfile.TemporaryDirectory() as d:
            mvdsv, gd = bygg_basedir(Path(d))
            mode = gd / "configs" / "usermodes" / "4on4"
            mode.mkdir(parents=True)
            (mode / "default.cfg").write_text("// GENERERAD av testsuite/rig\n")
            gjort = starta.stada("rtx-t3-prov-finns-ej", gd, mvdsv)
            self.assertFalse(gd.exists())
            self.assertTrue(any("tog bort" in g for g in gjort), gjort)

    def test_stadning_gor_reset_failed(self):
        """En failad transient unit blockerar nasta systemd-run med samma namn."""
        with tempfile.TemporaryDirectory() as d:
            mvdsv, gd = bygg_basedir(Path(d))
            gjort = starta.stada("rtx-t3-prov-finns-ej", gd, mvdsv)
            self.assertTrue(any("reset-failed" in g for g in gjort), gjort)


class TestSkriptenSjalva(unittest.TestCase):
    SKRIPT = ["res.sh", "res_t3.sh", "res_t4.sh", "aterstall.sh"]

    def test_inga_armerande_systemd_verb_i_skripten(self):
        """enable/disable/daemon-reload aktiverar armerade drop-ins retroaktivt.
        Den knappen ar riggsatets, och far inte finnas i de har filerna."""
        for namn in self.SKRIPT:
            text = (HAR / namn).read_text(encoding="utf-8")
            for verb in ("enable", "disable", "daemon-reload", "mask"):
                self.assertNotIn(
                    "systemctl --user %s" % verb, text, "%s innehaller %s" % (namn, verb)
                )
                self.assertNotIn("systemctl %s" % verb, text, "%s innehaller %s" % (namn, verb))

    def test_res_sh_reser_via_livsgrinden(self):
        """res.sh far inte starta uniten sjalv. Gor den det ar vi tillbaka i
        skarpvalideringens lage: 'klar' rc=0 med en dod server."""
        text = (HAR / "res.sh").read_text(encoding="utf-8")
        self.assertIn("starta.py", text)
        # Ingen egen systemd-run forbi grinden.
        kod = "\n".join(r.split("#", 1)[0] for r in text.splitlines())
        self.assertNotIn("systemd-run", kod)
        # Och en misslyckad start far inte fortsatta till "klar".
        self.assertIn("exit 3", text)

    def test_aterstallningskedjan_gor_reset_failed(self):
        """En failad transient unit blockerar nasta systemd-run med samma namn."""
        bild = {
            "korning": "prov", "unit": "rtx-t3-prov",
            "portar": {}, "aktiva_units": [], "moduler": {},
        }
        bevis, _ = aterstall.aterstall(bild, verkstall=False, lasfil=None)
        self.assertTrue(
            any("reset-failed rtx-t3-prov" in b for b in bevis), bevis
        )

    def test_skripten_ar_syntaktiskt_hela(self):
        for namn in self.SKRIPT:
            r = subprocess.run(["bash", "-n", str(HAR / namn)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (namn, r.stderr))

    def test_inga_hardkodade_portnummer_i_riggkoden(self):
        """Portnummer far bara komma ur docs/PORTAR.md. Testfilen sjalv ar
        undantagen — den maste namna portar for att kunna prova dem."""
        import re

        for namn in self.SKRIPT + ["portar.py", "riggvakt.py", "gamedir.py", "aterstall.py", "starta.py"]:
            text = (HAR / namn).read_text(encoding="utf-8")
            kod = "\n".join(
                r.split("#", 1)[0] for r in text.splitlines() if not r.strip().startswith("//")
            )
            traffar = re.findall(r"\b2[5-9]\d{3}\b", kod)
            self.assertEqual(traffar, [], "%s hardkodar portnummer: %s" % (namn, traffar))


if __name__ == "__main__":
    unittest.main(verbosity=2)
