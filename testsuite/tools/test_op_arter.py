#!/usr/bin/env python3
"""Op-artvalideringen — nattens läxa som en grind.

Egen fil (deepseek äger test_d_deploy.py).

18/8: alla tre på/av-varianterna var författade som `remove_links` när verbet bara
kunde `Recipe` och `PlanLink`. Recepten skrevs mot vad MOTORN kan göra i grafen,
inte mot vad VERBET kan uttrycka — två olika mängder, och skarven kontrollerades
ingenstans. Den upptäcktes vid apply, efter kontrasignatur och fem grindvarv.

Ingen riggkontakt.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_lab_guard  # noqa: F401 — suite-global lab-vakt
import d_failclosed as fc  # noqa: E402
from d_failclosed import (  # noqa: E402
    KOMPONAT_OP_ARTER,
    FailClosed,
    komponat_op_wire,
    validate_op_arter,
)

RECEPT = HERE / "recept"


def rec(*ops) -> dict:
    return {"id": "prov", "map": "dm3", "ops": list(ops)}


def remove_op(**over) -> dict:
    op = {
        "op": "remove_links",
        "name": "prov-remove",
        "links": [{"id": 10447, "from": 1416, "to": 1461, "kind": "walk"}],
    }
    op.update(over)
    return op


class Ordforradet(unittest.TestCase):
    def test_de_tre_arterna(self):
        self.assertEqual(KOMPONAT_OP_ARTER, {"shelf_patch", "plan_link", "remove_links"})

    def test_okand_art_vagras_med_alternativen_utskrivna(self):
        with self.assertRaises(FailClosed) as cm:
            validate_op_arter(rec({"op": "retype_links", "name": "x"}))
        why = str(cm.exception)
        self.assertIn("retype_links", why)
        self.assertIn("shelf_patch", why)
        self.assertIn("remove_links", why)

    def test_tomt_recept_vagras(self):
        with self.assertRaises(FailClosed) as cm:
            validate_op_arter(rec())
        self.assertIn("inga ops", str(cm.exception))

    def test_ratt_art_men_trasig_payload_vagras_ocksa(self):
        """Att bara jämföra artnamnet hade missat det här. Validatorn bygger
        wire-formen och kastar bort den — det är den enda kontroll som bevisar att
        op:en går att skicka."""
        with self.assertRaises(FailClosed) as cm:
            validate_op_arter(rec(remove_op(links=[{"id": 10447}])))
        self.assertIn("saknar from, to, kind", str(cm.exception))


class WireFormen(unittest.TestCase):
    def test_remove_links_blir_RemoveLinks(self):
        w = komponat_op_wire(remove_op())
        self.assertEqual(list(w), ["RemoveLinks"])
        self.assertEqual(
            w["RemoveLinks"]["links"],
            [{"id": 10447, "from": 1416, "to": 1461, "kind": "walk"}],
        )

    def test_arten_normaliseras_till_gemener(self):
        w = komponat_op_wire(remove_op(links=[{"id": 1, "from": 2, "to": 3, "kind": "WALK"}]))
        self.assertEqual(w["RemoveLinks"]["links"][0]["kind"], "walk")

    def test_tom_lanklista_vagras(self):
        with self.assertRaises(FailClosed) as cm:
            komponat_op_wire(remove_op(links=[]))
        self.assertIn("utan länkar", str(cm.exception))

    def test_ankarlost_id_vagras(self):
        """Ett rått länk-id utan ankare pekar på fel länk i en annan graf med full
        säkerhet — det är den enda farliga formen."""
        for saknad in ("from", "to", "kind"):
            l = {"id": 10447, "from": 1416, "to": 1461, "kind": "walk"}
            del l[saknad]
            with self.subTest(saknad):
                with self.assertRaises(FailClosed) as cm:
                    komponat_op_wire(remove_op(links=[l]))
                self.assertIn(saknad, str(cm.exception))

    def test_payload_sha_binder_ankaret_inte_bara_id(self):
        a = fc.op_payload_sha256(remove_op())
        b = fc.op_payload_sha256(remove_op(links=[{"id": 10447, "from": 1416, "to": 9999, "kind": "walk"}]))
        self.assertNotEqual(a, b, "samma id, annat ankare — måste ge olika payload-sha")
        self.assertEqual(a, fc.op_payload_sha256(remove_op()), "deterministisk")


class MotDeRiktigaRecepten(unittest.TestCase):
    def test_alla_tre_paav_recepten_gar_igenom_verbets_ordforrad(self):
        vantat = {"paav-g-v1": "Recipe", "paav-f-v1": "RemoveLinks", "paav-o-v1": "RemoveLinks"}
        for namn, art in vantat.items():
            p = RECEPT / f"{namn}.json"
            if not p.is_file():
                self.skipTest(f"{namn} saknas")
            with self.subTest(namn):
                d = json.loads(p.read_text(encoding="utf-8"))
                validate_op_arter(d)
                self.assertEqual([list(komponat_op_wire(o))[0] for o in d["ops"]], [art])

    def test_v296_komponatet_gar_fortfarande_igenom(self):
        p = RECEPT / "komponat-v296-ram.json"
        if not p.is_file():
            self.skipTest("v296-receptet saknas")
        validate_op_arter(json.loads(p.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
