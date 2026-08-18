#!/usr/bin/env python3
"""undo_bevis — att två undo av samma variant går att skilja åt.

Groks fynd 18/8: paav-undo-o-bevis.json och paav-undo-o-timprov-bevis.json blev
byte-identiska (sha afd82f20…) trots två olika händelser, för att formatet bara
bar graftillstånd. Innehållet var sant; identiteten saknades.

Ingen riggkontakt.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_lab_guard  # noqa: F401 — suite-global lab-vakt
import undo_bevis as ub  # noqa: E402

FORE = {"cells": 5983, "links": 48213, "graph_stamp": "8774822664048001128",
        "graph_content_hash": "8297ada3" + "0" * 56}
EFTER = dict(ub.REN_FORK)


def bevis(**over):
    d = dict(ts="2026-08-18T02:56:00Z", unit="tbx-d3", ctl_port=27998,
             handelse="riggstädning efter dom", variant="O",
             fore=FORE, efter=EFTER, undo_outcome="undone")
    d.update(over)
    return ub.bygg_bevis(**d)


class Handelseidentitet(unittest.TestCase):
    def test_samma_graftillstand_olika_tid_ger_olika_bevis(self):
        """Kärnan i groks fynd. Två undo av samma variant mot samma fork har
        identiskt graftillstånd — de får inte bli identiska filer."""
        a = bevis(ts="2026-08-18T02:56:00Z")
        b = bevis(ts="2026-08-18T04:29:00Z")
        self.assertEqual(a["fore"], b["fore"])
        self.assertEqual(a["efter"], b["efter"])
        self.assertNotEqual(a["handelse_id"], b["handelse_id"])
        self.assertNotEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_olika_unit_ger_olika_bevis(self):
        self.assertNotEqual(bevis(unit="tbx-d1")["handelse_id"], bevis(unit="tbx-d3")["handelse_id"])

    def test_olika_handelsekontext_ger_olika_bevis(self):
        self.assertNotEqual(
            bevis(handelse="före binärdeploy")["handelse_id"],
            bevis(handelse="riggstädning efter dom")["handelse_id"],
        )

    def test_andrat_graftillstand_med_samma_tid_ger_olika_id(self):
        """Id:t ska vara falskt om någon behåller tidsstämpeln men ändrar tillståndet."""
        annat = dict(EFTER, graph_content_hash="ff" * 32)
        self.assertNotEqual(bevis()["handelse_id"], bevis(efter=annat)["handelse_id"])

    def test_ordningstalet_skiljer_tva_undo_inom_samma_sekund(self):
        """Armväxling hinner två undo på en sekund mot samma graf.

        Utan ordningstal är det de byte-identiska O-bevisen igen, en tidsskala ner.
        """
        self.assertNotEqual(bevis(seq=0)["handelse_id"], bevis(seq=1)["handelse_id"])

    def test_identiska_indata_ger_identiskt_id(self):
        self.assertEqual(bevis()["handelse_id"], bevis()["handelse_id"])


class Bitidentitet(unittest.TestCase):
    def test_ren_fork_ger_true(self):
        self.assertTrue(bevis()["bitidentisk_mot_forvantat"])

    def test_avvikande_efterlage_ger_false(self):
        self.assertFalse(bevis(efter=dict(EFTER, links=48215))["bitidentisk_mot_forvantat"])

    def test_avvikande_niva2_ger_false(self):
        """Counts kan stämma medan grafen är en annan — nivå-2 är det som avgör."""
        self.assertFalse(bevis(efter=dict(EFTER, graph_content_hash="ab" * 32))["bitidentisk_mot_forvantat"])


class Skrivning(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="undobevis-"))
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)

    def test_skrivs_en_gang_och_ar_skrivskyddad(self):
        p = self.td / "b.json"
        ub.skriv(bevis(), p)
        self.assertEqual(p.stat().st_mode & 0o222, 0)
        self.assertEqual(json.loads(p.read_text())["handelse_id"], bevis()["handelse_id"])

    def test_andra_skrivningen_vagras(self):
        p = self.td / "b.json"
        ub.skriv(bevis(), p)
        with self.assertRaises(ub.Vagran) as cm:
            ub.skriv(bevis(ts="2026-08-18T04:29:00Z"), p)
        self.assertIn("skrivs aldrig över", str(cm.exception))


class Portgrind(unittest.TestCase):
    def test_bara_matriggarna(self):
        self.assertEqual(ub.UNIT_FOR_CTL, {27996: "tbx-d1", 27998: "tbx-d3"})

    def test_okand_port_vagras(self):
        rc = ub.main(["--variant", "O", "--port", "27991", "--lock-token", "x",
                      "--handelse", "prov", "--ut", "/tmp/finns-inte-undo.json"])
        self.assertEqual(rc, 2)

    def test_tom_handelse_vagras(self):
        rc = ub.main(["--variant", "O", "--port", "27998", "--lock-token", "x",
                      "--handelse", "   ", "--ut", "/tmp/finns-inte-undo2.json"])
        self.assertEqual(rc, 2)


class Utfallsgrinden(unittest.TestCase):
    """En undo utan bevis rapporteras inte som undone."""

    def test_undone_med_bevis_ar_undone(self):
        self.assertEqual(ub.rapporterat_utfall("undone", bevis_skrivet=True), "undone")

    def test_undone_utan_bevis_ar_obevisad(self):
        self.assertEqual(
            ub.rapporterat_utfall("undone", bevis_skrivet=False), ub.UNDONE_OBEVISAD
        )
        self.assertNotEqual(ub.UNDONE_OBEVISAD, ub.UNDONE)

    def test_andra_utfall_gar_igenom_oforandrade(self):
        """En undo som inte lyckades behöver inget bevis — den behöver sitt skäl."""
        for ut in ("no_txn", "refused", "stamp_mismatch"):
            with self.subTest(utfall=ut):
                self.assertEqual(ub.rapporterat_utfall(ut, bevis_skrivet=False), ut)
                self.assertEqual(ub.rapporterat_utfall(ut, bevis_skrivet=True), ut)


class Reservationen(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="undores-"))
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)

    def test_sokvagen_tas_fore_mutationen(self):
        res = ub.reservera(self.td / "b.json")
        self.assertTrue(res.path.exists(), "sökvägen ska vara tagen, inte bara tänkt")
        self.assertFalse(res.ledger)

    def test_upptagen_sokvag_vagras_fore_mutationen(self):
        ub.reservera(self.td / "b.json")
        with self.assertRaises(ub.Vagran):
            ub.reservera(self.td / "b.json")

    def test_ledgerform_reserverar_utan_att_ta_filen(self):
        res = ub.reservera(self.td / "l.jsonl", ledger=True)
        self.assertTrue(res.ledger)
        ub.fullfolj(res, bevis())
        ub.fullfolj(ub.reservera(self.td / "l.jsonl", ledger=True), bevis(ts="2026-08-18T04:29:00Z"))
        rader = [json.loads(r) for r in res.path.read_text().splitlines()]
        self.assertEqual(len(rader), 2)
        self.assertNotEqual(rader[0]["handelse_id"], rader[1]["handelse_id"])
        self.assertEqual({r["schema"] for r in rader}, {ub.SCHEMA_LEDGER})


class Operationen(unittest.TestCase):
    """undo_med_bevis: läs före → undo → läs efter → skriv bevis → rapportera."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="undoop-"))
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        self.spar = []

    def _kor(self, ut, motor="undone", **kw):
        idents = [FORE, EFTER]

        def las():
            self.spar.append("las")
            return idents.pop(0) if idents else EFTER

        def undo():
            self.spar.append("undo")
            return {"outcome": motor}

        return ub.undo_med_bevis(
            las_identitet=las, gor_undo=undo,
            reservation=ub.reservera(ut),
            unit="tbx-d3", ctl_port=27998,
            handelse="riggstädning efter dom", variant="O",
            freeze=self.tinat(), **kw
        )

    def tinat(self):
        """Injicerad frysning som inte finns. Enhetstester läser aldrig ~/lab."""
        return ub.FreezeContext.for_test(self.td / "ingen-frysning")

    def test_ordningen_ar_las_undo_las_skriv(self):
        ut = self.td / "b.json"
        r = self._kor(ut)
        self.assertEqual(self.spar, ["las", "undo", "las"])
        self.assertEqual(r["utfall"], ub.UNDONE)
        self.assertTrue(ut.exists())
        self.assertEqual(ut.stat().st_mode & 0o222, 0)
        self.assertEqual(json.loads(ut.read_text())["unit"], "tbx-d3")

    def test_redan_last_fore_identitet_sparar_ett_varv(self):
        r = self._kor(self.td / "b.json", fore=FORE)
        self.assertEqual(self.spar, ["undo", "las"])
        self.assertEqual(r["bevis"]["fore"], FORE)

    def test_motorns_utfall_star_kvar_bredvid_det_rapporterade(self):
        r = self._kor(self.td / "b.json", motor="no_txn")
        self.assertEqual(r["motor_outcome"], "no_txn")
        self.assertEqual(r["utfall"], "no_txn")

    def test_tom_handelse_vagras_fore_undo(self):
        idents = [FORE, EFTER]
        with self.assertRaises(ub.Vagran):
            ub.undo_med_bevis(
                las_identitet=lambda: idents.pop(0),
                gor_undo=lambda: self.fail("undo får inte köras"),
                reservation=ub.reservera(self.td / "b.json"),
                unit="tbx-d3", ctl_port=27998, handelse="  ", variant="O",
                freeze=self.tinat(),
            )

    def test_misslyckad_bevisskrivning_efter_mutation_tystas_inte(self):
        """Det värsta läget: undo:t har hänt och beviset går inte att skriva.

        Då ska det höras, och det får inte heta undone.
        """
        ut = self.td / "b.json"
        res = ub.reservera(ut)
        with mock.patch.object(ub, "fullfolj", side_effect=OSError("disk full")):
            with self.assertRaises(ub.Vagran) as cm:
                ub.undo_med_bevis(
                    las_identitet=lambda: FORE, gor_undo=lambda: {"outcome": "undone"},
                    reservation=res, unit="tbx-d3", ctl_port=27998,
                    handelse="riggstädning", variant="O", freeze=self.tinat(),
                )
        self.assertIn(ub.UNDONE_OBEVISAD, str(cm.exception))
        self.assertIn("undo genomfört", str(cm.exception))


class Frysgrinden(unittest.TestCase):
    """Hålet som fanns i morse: verktyget talar ctlproto direkt och gick förbi frysen.

    `check_change_freeze` sitter i fixa.py och d_deploy.py, inte i motorn, så en undo
    härifrån kunde köras rakt igenom en satt flagga. Grinden ligger nu i
    `undo_med_bevis` — den enda funktion varje undo-väg går genom.
    """

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="frys-"))
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        self.flagga = self.td / "flagga"

    def _kor(self, freeze):
        rord = []
        return ub.undo_med_bevis(
            las_identitet=lambda: (rord.append("las"), FORE)[1],
            gor_undo=lambda: (rord.append("undo"), {"outcome": "undone"})[1],
            reservation=ub.reservera(self.td / "b.json"),
            unit="tbx-d3", ctl_port=27998,
            handelse="riggstädning", variant="O", freeze=freeze,
        ), rord

    def test_satt_flagga_vagrar_innan_nagot_rors(self):
        self.flagga.write_text("fable 2026-08-18T18:38:30Z räkning pågår\n", encoding="utf-8")
        rord = []
        with self.assertRaises(Exception) as cm:
            ub.undo_med_bevis(
                las_identitet=lambda: (rord.append("las"), FORE)[1],
                gor_undo=lambda: (rord.append("undo"), {"outcome": "undone"})[1],
                reservation=ub.reservera(self.td / "b.json"),
                unit="tbx-d3", ctl_port=27998,
                handelse="riggstädning", variant="O",
                freeze=ub.FreezeContext.for_test(self.flagga),
            )
        self.assertEqual(type(cm.exception).__name__, "FailClosed")
        self.assertEqual(rord, [], "varken identitet eller undo fick köras")

    def test_ingen_flagga_slapper_igenom(self):
        ut, rord = self._kor(ub.FreezeContext.for_test(self.flagga))
        self.assertEqual(ut["utfall"], ub.UNDONE)
        self.assertEqual(rord, ["las", "undo", "las"])

    def test_grinden_ligger_fore_reservationen_i_cli(self):
        """CLI:t reserverar före socketen; grinden ska ändå vägra utan att lämna spår
        som hindrar en senare, tillåten körning."""
        self.flagga.write_text("fable 2026-08-18T18:38:30Z räkning pågår\n", encoding="utf-8")
        res = ub.reservera(self.td / "c.json")
        with self.assertRaises(Exception):
            ub.undo_med_bevis(
                las_identitet=lambda: FORE, gor_undo=lambda: {"outcome": "undone"},
                reservation=res, unit="tbx-d3", ctl_port=27998,
                handelse="riggstädning", variant="O",
                freeze=ub.FreezeContext.for_test(self.flagga),
            )
        self.assertEqual(res.path.stat().st_size, 0, "reservationen är tom, inte ett halvskrivet bevis")


if __name__ == "__main__":
    unittest.main(verbosity=2)
