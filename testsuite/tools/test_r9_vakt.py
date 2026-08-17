#!/usr/bin/env python3
"""R9-vakt. Fixture text + injected grind days. No rig."""

from __future__ import annotations

from datetime import date
import tempfile
import unittest
from pathlib import Path

import test_lab_guard  # noqa: F401
import r9_vakt as r9

DOM = """# d
## DOM M1-EFTER — STOPP (mätprotokollet) — 2026-08-17 00:35 CEST
## DOM GAP4-SJALVBEVIS — EJ KÖRBAR (STOPP) — 2026-08-16 11:38 CEST
## DOM A1 — GRÖNT — 2026-08-16 00:28 CEST
"""


class R9Tests(unittest.TestCase):
    def test_stopp_without_grind_alarms_from_since(self):
        doc = r9.lint(DOM, grind_days=[], since=date(2026, 8, 17))
        self.assertFalse(doc["ok"])
        self.assertEqual([a["punkt"] for a in doc["alarms"]], ["M1-EFTER"])

    def test_same_day_grind_clears(self):
        doc = r9.lint(DOM, grind_days=["2026-08-17"], since=date(2026, 8, 17))
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["alarms"], [])

    def test_older_stopp_ignored_before_since(self):
        doc = r9.lint(DOM, grind_days=[], since=date(2026, 8, 17))
        self.assertNotIn("GAP4-SJALVBEVIS", [a["punkt"] for a in doc["alarms"]])

    def test_cli_never_blocks(self):
        td = tempfile.TemporaryDirectory()
        p = Path(td.name) / "d.md"
        p.write_text(DOM, encoding="utf-8")
        self.assertEqual(r9.main(["--domfil", str(p), "--since", "2026-08-17"]), 0)
        td.cleanup()


if __name__ == "__main__":
    unittest.main()
