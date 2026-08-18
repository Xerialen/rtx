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


if __name__ == "__main__":
    unittest.main(verbosity=2)
