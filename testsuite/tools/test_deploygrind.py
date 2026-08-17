#!/usr/bin/env python3
"""Deploy-statusgrinden i apply-vägen (deepseeks trio-review, flagga ii).

Egen fil för att inte krocka med test_d_failclosed.py, som ägs av deepseek.
Ingen riggkontakt: grinden är ren datavalidering och rör aldrig en ctl-kanal.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from d_failclosed import (  # noqa: E402
    DEPLOY_OK,
    FailClosed,
    FreezeContext,
    check_deploy_status,
    deploy_status,
    guard_mutation,
)

RECEPT = HERE / "recept"


def tinat(tmp: Path) -> FreezeContext:
    """En freeze-kontext utan frysning, så grinden vi testar är den som talar."""
    return FreezeContext(path=tmp / "ingen-frysning")


class Statuslasning(unittest.TestCase):
    def test_saknad_status_ar_none_inte_tom_strang(self):
        self.assertIsNone(deploy_status({"id": "x"}))

    def test_status_normaliseras(self):
        self.assertEqual(deploy_status({"status": " deploy-kandidat "}), DEPLOY_OK)

    def test_icke_mappning_ger_none(self):
        self.assertIsNone(deploy_status(None))
        self.assertIsNone(deploy_status("en sträng"))


class Grenarna(unittest.TestCase):
    """En gren per statusvärde. Bara en av dem släpper igenom."""

    def test_deploy_kandidat_slapps_igenom(self):
        check_deploy_status({"id": "v296-ram", "schema": "komponat/1", "status": DEPLOY_OK})

    def test_ej_deploy_vagras_med_domens_skal(self):
        with self.assertRaises(FailClosed) as cm:
            check_deploy_status({
                "id": "haz1462-k2-v296-ram",
                "schema": "komponat/1",
                "status": "EJ-DEPLOY",
                "status_skal": "K2 utesluten efter DOM M1-EFTER-OFF",
            })
        self.assertEqual(cm.exception.gate, "deploy-status")
        text = str(cm.exception)
        self.assertIn("EJ-DEPLOY", text)
        self.assertIn("M1-EFTER-OFF", text, "skälet ska stå i klartext, inte bara statusen")

    def test_okand_vagras(self):
        with self.assertRaises(FailClosed) as cm:
            check_deploy_status({"id": "x", "schema": "komponat/1", "status": "OKAND"})
        self.assertIn("OKAND", str(cm.exception))

    def test_saknad_status_pa_komponat_vagras(self):
        with self.assertRaises(FailClosed) as cm:
            check_deploy_status({"id": "x", "schema": "komponat/1"})
        self.assertIn("Tystnad är inte ett godkännande", str(cm.exception))

    def test_saknad_status_pa_manifest_vagras_ocksa(self):
        with self.assertRaises(FailClosed):
            check_deploy_status({"recept_id": "x", "schema": "komponat-manifest/1"})

    def test_okant_statusvarde_vagras_inte_ignoreras(self):
        """Ett stavfel får inte tolkas som godkänt."""
        with self.assertRaises(FailClosed):
            check_deploy_status({"id": "x", "schema": "komponat/1", "status": "DEPLOY-KANDIAT"})

    def test_en_op_fixtur_utan_status_slapps_igenom(self):
        """Avgränsningen, uttalad: de sju registrerade fixturerna bär inget
        statusfält och styrs av sina egna sigill. En villkorslös läsning hade
        vägrat varje apply i kedjan."""
        check_deploy_status({"id": "ram-rail-v2", "off": {}, "on_expected": {}})

    def test_en_op_fixtur_med_ej_deploy_vagras_anda(self):
        """Bär den en status gäller den — utan undantag, schema eller ej."""
        with self.assertRaises(FailClosed):
            check_deploy_status({"id": "ram-rail-v2", "status": "EJ-DEPLOY"})


class IApplyVagen(unittest.TestCase):
    """Grinden måste sitta i guard_mutation, annars är den bara en funktion."""

    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())
        self.freeze = tinat(self.tmp)

    def komponat(self, status=None):
        r = {
            "id": "komponat-under-test",
            "schema": "komponat/1",
            "base": {},
            "ops": [],
        }
        if status:
            r["status"] = status
        return r

    def test_apply_av_ej_deploy_stoppas_av_guard_mutation(self):
        with self.assertRaises(FailClosed) as cm:
            guard_mutation(
                "apply",
                recipe=self.komponat("EJ-DEPLOY"),
                require_live=False,
                freeze=self.freeze,
            )
        self.assertEqual(cm.exception.gate, "deploy-status")

    def test_undo_av_ej_deploy_stoppas_ocksa(self):
        """Undo muterar grafen precis som apply gör."""
        with self.assertRaises(FailClosed) as cm:
            guard_mutation(
                "undo", recipe=self.komponat("EJ-DEPLOY"), require_live=False, freeze=self.freeze
            )
        self.assertEqual(cm.exception.gate, "deploy-status")

    def test_plant_av_ej_deploy_stoppas_ocksa(self):
        with self.assertRaises(FailClosed) as cm:
            guard_mutation(
                "plant", recipe=self.komponat("EJ-DEPLOY"), require_live=False, freeze=self.freeze
            )
        self.assertEqual(cm.exception.gate, "deploy-status")

    def test_statusgrinden_gar_fore_ankargrinden(self):
        """Ett recept som inte får köras ska vägras på DEN grunden.

        Komponatet nedan har både fel status och ett giftigt ankare. Kommer
        ankarfelet först får läsaren fel besked om varför den stoppades.
        """
        r = self.komponat("EJ-DEPLOY")
        r["remove_links"] = [{"id": 48131}]
        with self.assertRaises(FailClosed) as cm:
            guard_mutation("apply", recipe=r, require_live=False, freeze=self.freeze)
        self.assertEqual(cm.exception.gate, "deploy-status")


@unittest.skipUnless((RECEPT / "komponat-v296-ram.json").exists(), "behöver recepten")
class MotDeReellaRecepten(unittest.TestCase):
    def ladda(self, namn):
        return json.loads((RECEPT / namn).read_text(encoding="utf-8"))

    def test_deploykomponatet_slapps_igenom(self):
        check_deploy_status(self.ladda("komponat-v296-ram.json"))

    def test_k2_komponatet_vagras(self):
        with self.assertRaises(FailClosed) as cm:
            check_deploy_status(self.ladda("komponat-k2-v296-ram.json"))
        self.assertIn("M1-EFTER-OFF", str(cm.exception))

    def test_bada_manifesten_bar_samma_status_som_sina_recept(self):
        for namn in ("komponat-v296-ram", "komponat-k2-v296-ram"):
            with self.subTest(namn):
                recept = self.ladda(f"{namn}.json")
                manifest = self.ladda(f"{namn}.manifest.json")
                self.assertEqual(manifest["status"], recept["status"])

    def test_alla_registrerade_enop_fixturer_passerar_oforandrat(self):
        """Regressionsvakt: grinden får inte brickra den befintliga apply-kedjan."""
        for namn in (
            "west-shelf.json",
            "ram-rail.json",
            "ram-rail-v2.json",
            "ram-prevent.json",
            "haz1462-k1.json",
            "haz1462-k2.json",
            "haz1462-k3.json",
        ):
            with self.subTest(namn):
                check_deploy_status(self.ladda(namn))


if __name__ == "__main__":
    unittest.main(verbosity=2)
