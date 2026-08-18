#!/usr/bin/env python3
"""Regression: F-fällan flaggas, O passerar. Hermetisk fixtur, ingen ~/lab."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_lab_guard  # noqa: F401

import recept_lint as rl

# Hörnet 1367 / 1416 / 1459 / 1461 med verkliga länk-id. 1461 når 1367
# via gång 10779→1416→10441 före opsen.
CORNER = {
    "schema": "qw-nav-graph/1",
    "map": "dm3",
    "cell_ids": [1367, 1416, 1459, 1461],
    "cells": [[256, -844, 264], [288, -844, 264], [323, -834, 264], [328, -800, 264]],
    "link_ids": [10084, 10447, 10779, 10441, 10781, 10768, 10446, 10765],
    "links": [
        {"from": 1367, "to_cell": 1461, "kind": "jump", "T": 1},
        {"from": 1416, "to_cell": 1461, "kind": "walk", "T": 1},
        {"from": 1461, "to_cell": 1416, "kind": "walk", "T": 1},
        {"from": 1416, "to_cell": 1367, "kind": "walk", "T": 1},
        {"from": 1461, "to_cell": 1459, "kind": "walk", "T": 1},
        {"from": 1459, "to_cell": 1461, "kind": "walk", "T": 1},
        {"from": 1416, "to_cell": 1459, "kind": "walk", "T": 1},
        {"from": 1459, "to_cell": 1416, "kind": "walk", "T": 1},
    ],
    "rj_links": 0,
}


def _recept(*lids: int) -> dict:
    return {
        "schema": "komponat/1",
        "id": "prov",
        "ops": [
            {
                "op": "remove_links",
                "name": "prov",
                "links": [{"id": i, "from": 0, "to": 0, "kind": "walk"} for i in lids],
            }
        ],
    }


class FallaFO(unittest.TestCase):
    def setUp(self):
        self.links = rl.load_dump(self._write(CORNER))

    def _write(self, doc):
        self._td = tempfile.TemporaryDirectory()
        p = Path(self._td.name) / "dump.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def tearDown(self):
        self._td.cleanup()

    def test_f_falla_10779_bort_10084_kvar_flaggas(self):
        """F-fällan: återvägen 1461→1416 försvinner, hoppet 1367→1461 är kvar."""
        r = rl.lint(_recept(10779), self.links)
        self.assertFalse(r["ok"], r)
        cells_from = {(f["cell"], f["from"], f["via_link"]) for f in r["envag"]}
        self.assertIn((1461, 1367, 10084), cells_from)
        delta_1461 = next(d for d in r["cell_delta"] if d["cell"] == 1461)
        self.assertEqual(delta_1461["d_out_ws"], -1)
        self.assertEqual(delta_1461["out_ws_before"] - 1, delta_1461["out_ws_after"])

    def test_o_bada_bort_passerar(self):
        """O: 10084 och 10779 båda bort — ingen kvarvarande enväg 1367→1461."""
        r = rl.lint(_recept(10084, 10779), self.links)
        self.assertTrue(r["ok"], r["envag"])
        self.assertEqual(r["envag"], [])

    def test_o_tre_lankar_som_paav_o_passerar(self):
        r = rl.lint(_recept(10084, 10447, 10779), self.links)
        self.assertTrue(r["ok"], r["envag"])

    def test_ingen_op_passerar(self):
        r = rl.lint({"ops": []}, self.links)
        self.assertTrue(r["ok"])

    def test_cli_exit_koder(self):
        td = tempfile.TemporaryDirectory()
        dump = Path(td.name) / "d.json"
        rec_f = Path(td.name) / "f.json"
        rec_o = Path(td.name) / "o.json"
        dump.write_text(json.dumps(CORNER), encoding="utf-8")
        rec_f.write_text(json.dumps(_recept(10779)), encoding="utf-8")
        rec_o.write_text(json.dumps(_recept(10084, 10779)), encoding="utf-8")
        self.assertEqual(rl.main([str(rec_f), str(dump)]), 2)
        self.assertEqual(rl.main([str(rec_o), str(dump)]), 0)
        td.cleanup()


if __name__ == "__main__":
    unittest.main()
