#!/usr/bin/env python3
"""facts2_lint: summor, banner, raw-förbud. Hermetiska fixturer, ingen ~/lab."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import test_lab_guard  # noqa: F401

HERE = Path(__file__).resolve().parent
import sys

sys.path.insert(0, str(HERE.parent / "dashboard"))
import facts2_lint as fl  # noqa: E402


def _page(doc: dict) -> str:
    return (
        "<html><script id=\"facts2\" type=\"application/json\">"
        + json.dumps(doc, ensure_ascii=False)
        + "</script></html>"
    )


GOOD = {
    "ours_column": {
        "stats": [
            {"route": "in_ring", "value": "64/75", "detail": "F 2 · S 7 · M 2"},
            {"route": "in_vast", "value": "73/75", "detail": "F 0 · S 0 · M 2"},
        ],
        "banner": "137/150",
    }
}


class Facts2Lint(unittest.TestCase):
    def test_god_kolumn_passerar(self):
        r = fl.lint(GOOD)
        self.assertTrue(r["ok"], r["errors"])

    def test_detail_summerar_inte_till_hard(self):
        doc = {
            "rows": [
                {"value": "11/75", "detail": "F 2 · S 2 · M 2"},
            ]
        }
        r = fl.lint(doc)
        self.assertFalse(r["ok"])
        self.assertTrue(any("detail-summa" in e for e in r["errors"]))

    def test_banner_matchar_inte_radsumma(self):
        doc = {
            "ours_column": {
                "stats": [
                    {"value": "64/75", "detail": "F 2 · S 7 · M 2"},
                    {"value": "73/75", "detail": "F 0 · S 0 · M 2"},
                ],
                "banner": "100/150",
            }
        }
        r = fl.lint(doc)
        self.assertFalse(r["ok"])
        self.assertTrue(any("banner" in e for e in r["errors"]))

    def test_raw_i_value_vagras(self):
        doc = {"rows": [{"value": "8/78 raw", "detail": "F 0 · S 0 · M 8"}]}
        r = fl.lint(doc)
        self.assertFalse(r["ok"])
        self.assertTrue(any("raw" in e.lower() for e in r["errors"]))

    def test_raw_i_detail_vagras(self):
        doc = {"rows": [{"value": "11/75", "detail": "raw 78 · F 2"}]}
        r = fl.lint(doc)
        self.assertFalse(r["ok"])

    def test_html_block_och_cli(self):
        td = tempfile.TemporaryDirectory()
        okp = Path(td.name) / "ok.html"
        badp = Path(td.name) / "bad.html"
        okp.write_text(_page(GOOD), encoding="utf-8")
        badp.write_text(
            _page({"rows": [{"value": "11/75", "detail": "F 1 · S 1 · M 1"}]}),
            encoding="utf-8",
        )
        self.assertEqual(fl.main([str(okp)]), 0)
        self.assertEqual(fl.main([str(badp)]), 2)
        td.cleanup()

    def test_nofailure_detail_kraver_noll_hard(self):
        doc = {"rows": [{"value": "75/75", "detail": "no failures"}]}
        self.assertTrue(fl.lint(doc)["ok"])
        doc2 = {"rows": [{"value": "70/75", "detail": "no failures"}]}
        self.assertFalse(fl.lint(doc2)["ok"])


if __name__ == "__main__":
    unittest.main()
