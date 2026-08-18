#!/usr/bin/env python3
"""bind_ops för N ops — generaliseringen och att Sols F2 står kvar.

Egen fil för att inte krocka med test_d_deploy.py (deepseeks).

Trean i `len(ops) != 3` var v296-ram-komponatets op-antal, inte en princip. Den
låste runnern vid just det receptet, så på/av-provets enops-varianter inte kunde
köras alls. Kravet är nu sammanhängande 0..N med pin först; den PARVISA
op_projection-kontrollen är oförändrad, och det är den som uppfyller F2.

Ingen riggkontakt: rena dict-kontroller plus de förseglade filerna på disk.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_lab_guard  # noqa: F401 — suite-global lab-vakt
from d_deploy import bind_ops  # noqa: E402
from d_failclosed import FailClosed  # noqa: E402

RECEPT = HERE / "recept"
V296_RECEPT = RECEPT / "komponat-v296-ram.json"
V296_MANIFEST = RECEPT / "komponat-v296-ram.manifest.json"


def manifest_for(recept: dict, n: int) -> dict:
    """Ett minimalt manifest som speglar receptets n ops."""
    steg = [{"index": 0, "op": "pin", "name": recept["id"]}]
    for i, op in enumerate(recept["ops"], start=1):
        steg.append({"index": i, "op": op["op"], "name": op["name"], "kalla": op.get("kalla")})
    assert len(steg) == n + 1
    return {"recept_id": recept["id"], "map": recept["map"], "steg": steg}


def recept_med(*namn: str) -> dict:
    return {
        "id": "prov",
        "map": "dm3",
        "ops": [{"op": "remove_links", "name": n, "kalla": f"recept/{n}.json"} for n in namn],
    }


class V296StårKvar(unittest.TestCase):
    """Villkor 1: det förseglade fyrstegsmanifestet passerar oförändrat."""

    def test_v296_fyra_steg_tre_ops_passerar(self):
        rec = json.loads(V296_RECEPT.read_text(encoding="utf-8"))
        man = json.loads(V296_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual([s["index"] for s in man["steg"]], [0, 1, 2, 3])
        self.assertEqual(len(rec["ops"]), 3)
        bind_ops(rec, man)  # ska inte kasta


class EnOpsVariant(unittest.TestCase):
    """Villkor 2: på/av-provets enops-recept passerar."""

    def test_en_op_passerar(self):
        rec = recept_med("paav-g")
        bind_ops(rec, manifest_for(rec, 1))

    def test_tva_ops_passerar_ocksa(self):
        rec = recept_med("a", "b")
        bind_ops(rec, manifest_for(rec, 2))

    def test_riktiga_paav_recepten_passerar_mot_speglat_manifest(self):
        for namn in ("paav-g-v1", "paav-f-v1", "paav-o-v1"):
            p = RECEPT / f"{namn}.json"
            if not p.is_file():
                self.skipTest(f"{namn} inte committad än")
            rec = json.loads(p.read_text(encoding="utf-8"))
            with self.subTest(namn):
                self.assertEqual(len(rec["ops"]), 1)
                bind_ops(rec, manifest_for(rec, 1))


class SolsF2StårKvar(unittest.TestCase):
    """Villkor 3: extra, saknad och omkastad op vägras — oförändrat."""

    def test_extra_op_i_receptet_vagras(self):
        rec = recept_med("a", "b")
        man = manifest_for(recept_med("a"), 1)
        man["recept_id"] = "prov"
        with self.assertRaises(FailClosed) as cm:
            bind_ops(rec, man)
        self.assertIn("op-antalet", str(cm.exception))

    def test_saknad_op_i_receptet_vagras(self):
        rec = recept_med("a")
        man = manifest_for(recept_med("a", "b"), 2)
        with self.assertRaises(FailClosed) as cm:
            bind_ops(rec, man)
        self.assertIn("op-antalet", str(cm.exception))

    def test_omkastad_op_vagras(self):
        """Samma antal, samma namn, fel ordning — den parvisa projektionen fäller."""
        rec = recept_med("a", "b")
        man = manifest_for(recept_med("b", "a"), 2)
        with self.assertRaises(FailClosed) as cm:
            bind_ops(rec, man)
        self.assertIn("≠", str(cm.exception))

    def test_noll_ops_vagras(self):
        rec = {"id": "prov", "map": "dm3", "ops": []}
        man = {"recept_id": "prov", "map": "dm3", "steg": [{"index": 0, "op": "pin", "name": "prov"}]}
        with self.assertRaises(FailClosed) as cm:
            bind_ops(rec, man)
        self.assertIn("minst 1", str(cm.exception))

    def test_hal_i_stegindex_vagras(self):
        rec = recept_med("a", "b")
        man = manifest_for(rec, 2)
        man["steg"][2]["index"] = 5
        with self.assertRaises(FailClosed) as cm:
            bind_ops(rec, man)
        self.assertIn("sammanhängande", str(cm.exception))

    def test_omkastade_stegindex_vagras(self):
        rec = recept_med("a", "b")
        man = manifest_for(rec, 2)
        man["steg"][1]["index"], man["steg"][2]["index"] = 2, 1
        with self.assertRaises(FailClosed):
            bind_ops(rec, man)

    def test_saknad_pin_vagras(self):
        rec = recept_med("a")
        man = manifest_for(rec, 1)
        man["steg"][0]["op"] = "remove_links"
        with self.assertRaises(FailClosed) as cm:
            bind_ops(rec, man)
        self.assertIn("steg 0 måste vara pin", str(cm.exception))

    def test_tomt_manifest_vagras(self):
        with self.assertRaises(FailClosed) as cm:
            bind_ops(recept_med("a"), {"recept_id": "prov", "map": "dm3", "steg": []})
        self.assertIn("saknar steg", str(cm.exception))

    def test_fel_recept_id_vagras(self):
        rec = recept_med("a")
        man = manifest_for(rec, 1)
        man["recept_id"] = "nagot-annat"
        with self.assertRaises(FailClosed):
            bind_ops(rec, man)


if __name__ == "__main__":
    unittest.main(verbosity=2)
