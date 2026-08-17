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
    KANDA_STATUSAR,
    LABB_OK,
    FailClosed,
    FreezeContext,
    check_deploy_status,
    deploy_status,
    guard_mutation,
)
from d_recipe import FIXTURE_SHA256, REGISTERED_IDS  # noqa: E402

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

    def test_saknad_status_vagras_villkorslost(self):
        """Fables disposition: ALLA recept måste ta ställning, schema eller ej."""
        for artefakt in (
            {"id": "x", "schema": "komponat/1"},
            {"recept_id": "x", "schema": "komponat-manifest/1"},
            {"id": "ram-rail-v2", "off": {}, "on_expected": {}},
            {"id": "nagot-nytt-nagon-lade-till"},
        ):
            with self.subTest(artefakt.get("id") or artefakt.get("recept_id")):
                with self.assertRaises(FailClosed) as cm:
                    check_deploy_status(artefakt)
                self.assertEqual(cm.exception.gate, "deploy-status")
                self.assertIn("Tystnad är inte ett godkännande", str(cm.exception))

    def test_okant_statusvarde_vagras_inte_ignoreras(self):
        """Ett stavfel får inte tolkas som godkänt."""
        with self.assertRaises(FailClosed) as cm:
            check_deploy_status({"id": "x", "schema": "komponat/1", "status": "DEPLOY-KANDIAT"})
        self.assertIn("okänd status", str(cm.exception))

    def test_labb_far_matas(self):
        """LABB är hela poängen med att fixturerna finns: de mäts ensamma mot bas."""
        check_deploy_status({"id": "ram-rail-v2", "status": LABB_OK})

    def test_labb_far_aldrig_deployas(self):
        with self.assertRaises(FailClosed) as cm:
            check_deploy_status({"id": "ram-rail-v2", "status": LABB_OK}, deploy=True)
        self.assertIn("får mätas, aldrig deployas", str(cm.exception))

    def test_labb_pa_ett_komponat_ar_en_sjalvmotsagelse(self):
        """Ett komponat ÄR deploy-vägen; att kalla det labb får inte öppna den."""
        with self.assertRaises(FailClosed) as cm:
            check_deploy_status({"id": "x", "schema": "komponat/1", "status": LABB_OK})
        self.assertIn(DEPLOY_OK, str(cm.exception))

    def test_deploy_kandidat_far_ocksa_matas_i_labb(self):
        check_deploy_status({"id": "v296-ram", "status": DEPLOY_OK})

    def test_en_op_fixtur_med_ej_deploy_vagras_anda(self):
        """Bär den EJ-DEPLOY gäller det — utan undantag, schema eller ej."""
        with self.assertRaises(FailClosed):
            check_deploy_status({"id": "ram-rail-v2", "status": "EJ-DEPLOY"})

    def test_ordforradet_ar_slutet(self):
        self.assertEqual(set(KANDA_STATUSAR), {DEPLOY_OK, LABB_OK, "EJ-DEPLOY", "OKAND"})


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

    #: Vad de sju registrerade fixturerna är märkta som, och varför.
    SJU = {
        "west-shelf.json": LABB_OK,
        "ram-rail.json": LABB_OK,
        "ram-rail-v2.json": LABB_OK,
        "ram-prevent.json": LABB_OK,
        "haz1462-k1.json": LABB_OK,
        "haz1462-k2.json": "EJ-DEPLOY",
        "haz1462-k3.json": LABB_OK,
    }

    def test_de_sju_bar_sin_avsedda_status(self):
        for namn, vantad in self.SJU.items():
            with self.subTest(namn):
                self.assertEqual(self.ladda(namn).get("status"), vantad)

    def test_de_sju_genom_grinden_labbvagen(self):
        """LABB-märkta mäts vidare som förut; k2 stoppas av domen."""
        for namn, status in self.SJU.items():
            with self.subTest(namn):
                if status == LABB_OK:
                    check_deploy_status(self.ladda(namn))
                else:
                    with self.assertRaises(FailClosed):
                        check_deploy_status(self.ladda(namn))

    def test_de_sju_genom_grinden_deployvagen(self):
        """Ingen av dem får deployas fristående — deploy går via komponatet."""
        for namn in self.SJU:
            with self.subTest(namn):
                with self.assertRaises(FailClosed) as cm:
                    check_deploy_status(self.ladda(namn), deploy=True)
                self.assertEqual(cm.exception.gate, "deploy-status")

    def test_varje_receptfil_med_id_ar_markt(self):
        """Villkorslös läsning: en omärkt receptfil ska hittas här, inte i drift."""
        omarkta = []
        for p in sorted(RECEPT.glob("*.json")):
            doc = json.loads(p.read_text(encoding="utf-8"))
            if (doc.get("id") or doc.get("recept_id")) and doc.get("status") is None:
                omarkta.append(p.name)
        self.assertEqual(omarkta, [], "omärkta receptfiler vägras nu av grinden")

    def test_fixtursigillen_foljer_med_statusandringen(self):
        """Statusfältet ändrar filens byte, och sigillet måste följa med.

        Missas det vägrar `verify_fixture_seal` varje registrerat recept — grinden
        hade då stoppat kedjan via en helt annan väg än den vi ville införa.
        """
        import hashlib

        for rid in sorted(REGISTERED_IDS):
            with self.subTest(rid):
                blob = (RECEPT / f"{rid}.json").read_bytes()
                self.assertEqual(hashlib.sha256(blob).hexdigest(), FIXTURE_SHA256[rid])


if __name__ == "__main__":
    unittest.main(verbosity=2)
