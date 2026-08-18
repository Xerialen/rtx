#!/usr/bin/env python3
"""Envägsgrinden — linten som ett steg i flödet, inte ett verktyg man minns.

Fyndet som motiverar grinden är verkligt: paav-f tar bort 10779 och lämnar 10084,
vilket skapar ingången 1367→1461 utan gång/step-retur. Linten hittade det när någon
körde den för hand. Testerna nedan kör den mot de RIKTIGA dumparna och recepten, så
att grinden inte kan bli grön mot en fixtur som är snällare än verkligheten.

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

import recept_lint_grind as G  # noqa: E402
from d_failclosed import FailClosed  # noqa: E402

RECEPT = HERE / "recept"
REGISTER = G.DEFAULT_DUMPREGISTER

BAS = {
    "cells": 5977, "links": 48207, "rj_links": 0,
    "graph_stamp": "906595427771298736",
    "graph_content_hash": "58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9",
}
FORK = {
    "cells": 5983, "links": 48216, "rj_links": 0,
    "graph_stamp": "11908727279900740725",
    "graph_content_hash": "cd800200cad72431e0cbfe0a2fc947bd94309e334103d6cc0abd076155ecf051",
}


def recept(namn: str) -> dict:
    return json.loads((RECEPT / namn).read_text(encoding="utf-8"))


def _dumpar_finns() -> bool:
    try:
        reg = json.loads(REGISTER.read_text(encoding="utf-8"))
    except OSError:
        return False
    return all(Path(d["path"]).is_file() for d in reg.get("dumps") or [])


HAR_DUMPAR = _dumpar_finns()
kraver_dumpar = unittest.skipUnless(HAR_DUMPAR, "dumparna finns inte på denna maskin")


@kraver_dumpar
class MotVerkligaGrafer(unittest.TestCase):
    def test_v296_ram_passerar_mot_basen(self):
        """Deploy-komponatet skapar inga envägslägen — grinden får inte hitta på fynd."""
        b = G.kor_grind(recept("komponat-v296-ram.json"), BAS)
        self.assertEqual(b["utfall"], G.PASS)
        self.assertEqual(b["envag"], [])
        self.assertEqual(b["dump_identitet_harledd"]["graph_stamp"], BAS["graph_stamp"])

    def test_paav_f_vagras_med_fallan_utskriven(self):
        """Nattens F-fälla. Det här är hela skälet till att grinden finns."""
        with self.assertRaises(FailClosed) as cm:
            G.kor_grind(recept("paav-f-v1.json"), FORK)
        txt = str(cm.exception)
        self.assertEqual(cm.exception.gate, "recept-lint")
        self.assertIn("1367", txt)
        self.assertIn("1461", txt)
        self.assertIn("envag_medveten", txt)

    def test_paav_g_och_o_passerar_mot_forken(self):
        for namn in ("paav-g-v1.json", "paav-o-v1.json"):
            with self.subTest(recept=namn):
                b = G.kor_grind(recept(namn), FORK)
                self.assertEqual(b["utfall"], G.PASS)

    def test_kvittoblocket_bar_dumpens_harledda_identitet(self):
        """Kvittot ska kunna svara på VILKEN graf linten kördes mot, inte bara att den kördes."""
        b = G.kor_grind(recept("paav-g-v1.json"), FORK)
        self.assertEqual(b["dump_identitet_harledd"]["cells"], 5983)
        self.assertEqual(b["dump_identitet_harledd"]["links"], 48216)
        self.assertEqual(
            b["dump_identitet_harledd"]["graph_content_hash_utan_params"],
            FORK["graph_content_hash"],
        )
        self.assertEqual(len(b["dump_sha256"]), 64)


@kraver_dumpar
class Undantaget(unittest.TestCase):
    def test_medveten_med_skal_passerar_och_bokfors(self):
        r = dict(recept("paav-f-v1.json"))
        r["envag_medveten"] = True
        r["envag_skal"] = "1461 ska bara nås uppifrån i det här provet"
        b = G.kor_grind(r, FORK)
        self.assertEqual(b["utfall"], G.PASS_MEDVETEN)
        self.assertTrue(b["envag_medveten"])
        self.assertIn("1461", b["envag_skal"])
        self.assertEqual(len(b["envag"]), 1, "undantaget döljer inte fyndet, det motiverar det")

    def test_medveten_utan_skal_vagras(self):
        """En flagga utan skäl är tyst passage med en flagga framför."""
        r = dict(recept("paav-f-v1.json"))
        r["envag_medveten"] = True
        with self.assertRaises(FailClosed) as cm:
            G.kor_grind(r, FORK)
        self.assertIn("envag_skal", str(cm.exception))

    def test_tomt_skal_raknas_som_inget_skal(self):
        r = dict(recept("paav-f-v1.json"))
        r["envag_medveten"] = True
        r["envag_skal"] = "   "
        with self.assertRaises(FailClosed):
            G.kor_grind(r, FORK)

    def test_medveten_utan_fynd_markeras_sarskilt(self):
        """Ett undantag som inte behövdes är inte ett fel, men det ska synas."""
        r = dict(recept("paav-g-v1.json"))
        r["envag_medveten"] = True
        r["envag_skal"] = "för säkerhets skull"
        b = G.kor_grind(r, FORK)
        self.assertEqual(b["utfall"], G.PASS_MEDVETEN_UTAN_FYND)


class DumpenMasteVaraLiveGrafen(unittest.TestCase):
    """Registret är uppslagning; dumpens byte är sanning."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="lintgrind-"))
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)

    def _reg(self, dumps) -> Path:
        p = self.td / "reg.json"
        p.write_text(json.dumps({"schema": "dumpregister/1", "dumps": dumps}), encoding="utf-8")
        return p

    def _minidump(self, name="d.json") -> Path:
        p = self.td / name
        p.write_text(
            json.dumps(
                {
                    "map": "dm3",
                    "cells": [[0, 0, 0], [64, 0, 0]],
                    "cell_ids": [0, 1],
                    "links": [{"from": 0, "to": 1, "kind": "walk"}],
                    "link_ids": [0],
                }
            ),
            encoding="utf-8",
        )
        return p

    def test_register_saknas_vagras(self):
        with self.assertRaises(FailClosed) as cm:
            G.kor_grind({}, BAS, dumpregister=self.td / "finns-inte.json")
        self.assertIn("dumpregister saknas", str(cm.exception))

    def test_tomt_register_vagras(self):
        with self.assertRaises(FailClosed):
            G.kor_grind({}, BAS, dumpregister=self._reg([]))

    def test_ingen_dump_for_live_identiteten_vagras(self):
        """En okörd lint passerar inte som PASS — det är hela poängen med en grind."""
        reg = self._reg([{
            "id": "annan", "path": str(self._minidump()),
            "graph_stamp": "1", "graph_content_hash": "aa",
        }])
        with self.assertRaises(FailClosed) as cm:
            G.kor_grind({}, BAS, dumpregister=reg)
        self.assertIn("ingen registrerad dump", str(cm.exception))

    def test_tvetydigt_register_vagras(self):
        d = self._minidump()
        post = {
            "id": "a", "path": str(d),
            "graph_stamp": BAS["graph_stamp"],
            "graph_content_hash": BAS["graph_content_hash"],
        }
        reg = self._reg([post, dict(post, id="b")])
        with self.assertRaises(FailClosed) as cm:
            G.kor_grind({}, BAS, dumpregister=reg)
        self.assertIn("tvetydigt", str(cm.exception))

    def test_dumpfil_saknas_pa_disk_vagras(self):
        reg = self._reg([{
            "id": "borta", "path": str(self.td / "nope.json"),
            "graph_stamp": BAS["graph_stamp"],
            "graph_content_hash": BAS["graph_content_hash"],
        }])
        with self.assertRaises(FailClosed) as cm:
            G.kor_grind({}, BAS, dumpregister=reg)
        self.assertIn("saknas på disk", str(cm.exception))

    def test_andrad_dumpfil_vagras(self):
        """Registrerad sha ≠ filens sha: filen har ändrats efter registreringen."""
        d = self._minidump()
        reg = self._reg([{
            "id": "manipulerad", "path": str(d), "byte_sha256": "00" * 32,
            "graph_stamp": BAS["graph_stamp"],
            "graph_content_hash": BAS["graph_content_hash"],
        }])
        with self.assertRaises(FailClosed) as cm:
            G.kor_grind({}, BAS, dumpregister=reg)
        self.assertIn("ändrats efter registreringen", str(cm.exception))

    def test_olasbar_dump_vagras_i_denna_grind(self):
        """Skälet ska peka på dumpen, inte falla ut som ett generiskt crash-detector."""
        trasig = self.td / "trasig.json"
        trasig.write_text(json.dumps({"map": "dm3", "cells": [], "links": []}), encoding="utf-8")
        reg = self._reg([{
            "id": "trasig", "path": str(trasig),
            "graph_stamp": BAS["graph_stamp"],
            "graph_content_hash": BAS["graph_content_hash"],
        }])
        with self.assertRaises(FailClosed) as cm:
            G.kor_grind({}, BAS, dumpregister=reg)
        self.assertEqual(cm.exception.gate, "recept-lint")
        self.assertIn("går inte att härleda", str(cm.exception))

    def test_registrets_pastaende_faller_mot_dumpens_byte(self):
        """Kärnegenskapen: registret kan ljuga, byten kan inte.

        Posten gör anspråk på basens identitet, men filen är två celler och en länk.
        Härledningen avgör, och grinden vägrar i stället för att linta fel graf.
        """
        d = self._minidump()
        reg = self._reg([{
            "id": "ljuger", "path": str(d),
            "graph_stamp": BAS["graph_stamp"],
            "graph_content_hash": BAS["graph_content_hash"],
        }])
        with self.assertRaises(FailClosed) as cm:
            G.kor_grind({}, BAS, dumpregister=reg)
        self.assertIn("härleder inte live-identiteten", str(cm.exception))


class Registret(unittest.TestCase):
    def test_produktionsregistret_ar_lasbart_och_komplett(self):
        reg = json.loads(REGISTER.read_text(encoding="utf-8"))
        self.assertEqual(reg["schema"], "dumpregister/1")
        ids = {d["id"] for d in reg["dumps"]}
        self.assertIn("dm3-bas-v296-pin", ids, "deploy-komponatets bas måste gå att linta mot")
        self.assertIn("dm3-fork-v296-ram", ids, "varianternas bas måste gå att linta mot")
        for d in reg["dumps"]:
            self.assertEqual(len(d["byte_sha256"]), 64)
            self.assertTrue(str(d["graph_stamp"]).isdigit())


if __name__ == "__main__":
    unittest.main(verbosity=2)
