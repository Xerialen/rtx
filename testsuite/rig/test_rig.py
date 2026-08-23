#!/usr/bin/env python3
"""Enhetstester + negativkontroller för testsuite/rig/.

Varje assertion som påstår sig bevaka en fallgrop har ett test som matar
den en känt trasig indata och kräver att den faller. En grind som aldrig
setts falla är en grön lampa, inte en grind.

Körs offline: `python3 -m unittest discover -s testsuite/rig -v`.
Ingen rigg, ingen server, ingen port reses.
"""
from __future__ import annotations

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
        for p in (27550, 27991, 27530, 27700):
            self.assertEqual(t[p].klass, "forbjuden", "port %d" % p)
        for p in (28502, 28503):
            self.assertEqual(t[p].klass, "orord", "port %d" % p)
        self.assertEqual(t[27990].klass, "ra-kontroll")
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
    return kalla


VAL_T3 = dict(seats_per_side=4, timelimit_min=5, demodir="demos", rcon_password="x")


class TestGamedir(unittest.TestCase):
    def test_bygger_och_haller(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None)
            bevis = gamedir.granska(rot / "privat", val)
            self.assertTrue(any("riggkritiska cvars satta" in b for b in bevis))
            self.assertIn("inga masterservrar", bevis)

    def test_neg_sist_korda_filen_saknar_en_cvar(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None)
            cfg = rot / "privat" / "configs" / "usermodes" / "4on4" / "default.cfg"
            kvar = [r for r in cfg.read_text().splitlines() if "k_noframechecks" not in r]
            cfg.write_text("\n".join(kvar) + "\n")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val)
            self.assertIn("k_noframechecks", str(cm.exception))

    def test_neg_fel_varde_i_sist_korda_filen(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None)
            cfg = rot / "privat" / "configs" / "usermodes" / "4on4" / "default.cfg"
            cfg.write_text(cfg.read_text().replace("set k_count 45", "set k_count 10"))
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val)
            self.assertIn("k_count", str(cm.exception))

    def test_neg_resetkedjan_kor_var_fil_sjalv(self):
        """Fallan: kor reset-kedjan sjalv var sist korda fil, sa kors den FORE
        KTX:s mode-ominitiering i stallet for efter, och ominitieringen
        stampar tillbaka. Filen ser ratt ut och riggen ar anda av."""
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None)
            s = rot / "privat" / "server.cfg"
            s.write_text(s.read_text() + "\nexec configs/usermodes/4on4/default.cfg\n")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val)
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
            gamedir.bygg(kalla, rot / "privat", val, None)
            # Bygget far inte rora det delade tradet.
            self.assertIn("set k_overtime 1", farlig.read_text())
            # Var kopia ska vara avvapnad, sa granskningen haller.
            gamedir.granska(rot / "privat", val)

    def test_neg_annat_usermode_avvapnar_riggen(self):
        """Fallan: byter servern lage kors DEN modens default.cfg sist, och
        var ar aldrig med. Ett lage som satter nagot riggkritiskt annorlunda
        avvapnar riggen tyst."""
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None)
            annat = rot / "privat" / "configs" / "usermodes" / "3on3" / "default.cfg"
            annat.write_text(annat.read_text() + "set k_noframechecks 0\n")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val)
            self.assertIn("k_noframechecks", str(cm.exception))
            self.assertIn("3on3", str(cm.exception))

    def test_neg_masterservrar_kvar(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            gamedir.bygg(kalla, rot / "privat", val, None)
            s = rot / "privat" / "server.cfg"
            s.write_text(s.read_text() + "\nsetmaster master.quakeworld.nu:27000\n")
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.granska(rot / "privat", val)
            self.assertIn("masterservrar", str(cm.exception))

    def test_neg_gamedir_inuti_delade_tradet(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t3", **VAL_T3)
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.bygg(kalla, kalla / "privat", val, None)
            self.assertIn("delade källträdet", str(cm.exception))

    def test_neg_t4_utan_frogbotdata(self):
        with tempfile.TemporaryDirectory() as d:
            rot = Path(d)
            kalla = bygg_kallträd(rot)
            val = gamedir.Riggval(tier="t4", **VAL_T3)
            with self.assertRaises(gamedir.Riggfel) as cm:
                gamedir.bygg(kalla, rot / "privat", val, None)
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

    def test_skripten_ar_syntaktiskt_hela(self):
        for namn in self.SKRIPT:
            r = subprocess.run(["bash", "-n", str(HAR / namn)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (namn, r.stderr))

    def test_inga_hardkodade_portnummer_i_riggkoden(self):
        """Portnummer far bara komma ur docs/PORTAR.md. Testfilen sjalv ar
        undantagen — den maste namna portar for att kunna prova dem."""
        import re

        for namn in self.SKRIPT + ["portar.py", "riggvakt.py", "gamedir.py", "aterstall.py"]:
            text = (HAR / namn).read_text(encoding="utf-8")
            kod = "\n".join(
                r.split("#", 1)[0] for r in text.splitlines() if not r.strip().startswith("//")
            )
            traffar = re.findall(r"\b2[5-9]\d{3}\b", kod)
            self.assertEqual(traffar, [], "%s hardkodar portnummer: %s" % (namn, traffar))


if __name__ == "__main__":
    unittest.main(verbosity=2)
