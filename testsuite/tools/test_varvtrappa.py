#!/usr/bin/env python3
"""Varvtrappe-räknare. Fixture markdown only — no real domfil, no rig."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import test_lab_guard  # noqa: F401
import varvtrappa as vt

FIXTURE = """# testdomfil
## DOM GAP4-SJALVBEVIS-R3 — UNDERKÄND — 2026-08-16 12:00 CEST
- x
## DOM GAP4-SJALVBEVIS-R2 — RÖTT — 2026-08-16 11:00 CEST
- x
## DOM GAP4-SJALVBEVIS — STOPP — 2026-08-16 10:00 CEST
- x
## DOM H4-r2 — GRÖNT — 2026-08-16 01:30 CEST
- x
## DOM H4 — RÖTT — 2026-08-16 01:15 CEST
- x
## DOM K1-R2 — JUSTERAS — 2026-08-16 02:00 CEST
- x
## DOM K1 — JUSTERAS — 2026-08-16 01:00 CEST
- x
## PROTOKOLLNOTIS — inte en dom
## DOM A1 — GRÖNT — 2026-08-16 00:28 CEST
"""


class VarvtrappaTests(unittest.TestCase):
    def test_punkt_strips_revision(self):
        self.assertEqual(vt.punkt_id("H4-r2"), "H4")
        self.assertEqual(vt.punkt_id("GAP4-SJALVBEVIS-R3"), "GAP4-SJALVBEVIS")
        self.assertEqual(vt.punkt_id("TURNERING-K2-R6B"), "TURNERING-K2")
        self.assertEqual(vt.punkt_id("GAP4-SJALVBEVIS-R6R4"), "GAP4-SJALVBEVIS")
        self.assertEqual(vt.punkt_id("I3-r3-beslut"), "I3")
        self.assertEqual(vt.punkt_id("A1"), "A1")

    def test_three_fail_is_reconstruct(self):
        doc = vt.report(FIXTURE)
        self.assertIn("GAP4-SJALVBEVIS", doc["konstruera_om"])
        self.assertNotIn("GAP4-SJALVBEVIS", doc["live_session"])

    def test_two_fail_is_live_session(self):
        doc = vt.report(FIXTURE)
        self.assertIn("K1", doc["live_session"])
        self.assertNotIn("K1", doc["konstruera_om"])

    def test_newer_pass_resets_older_fails(self):
        text = (
            "## DOM GAP4-SJALVBEVIS-R6R4 — GODKÄND — 2026-08-16 14:29 CEST\n"
            "## DOM GAP4-SJALVBEVIS-R3 — UNDERKÄND — 2026-08-16 12:00 CEST\n"
            "## DOM GAP4-SJALVBEVIS — STOPP — 2026-08-16 10:00 CEST\n"
        )
        doc = vt.report(text)
        self.assertNotIn("GAP4-SJALVBEVIS", doc["konstruera_om"])
        g = next(p for p in doc["punkter"] if p["punkt"] == "GAP4-SJALVBEVIS")
        self.assertEqual(g["consecutive_fail"], 0)

    def test_pass_resets_staircase(self):
        doc = vt.report(FIXTURE)
        h4 = next(p for p in doc["punkter"] if p["punkt"] == "H4")
        self.assertEqual(h4["consecutive_fail"], 0)
        self.assertIsNone(h4["action"])
        self.assertNotIn("H4", doc["live_session"])

    def test_green_only_is_quiet(self):
        doc = vt.report("## DOM A1 — GRÖNT — 2026-08-16 00:28 CEST\n")
        self.assertEqual(doc["live_session"], [])
        self.assertEqual(doc["konstruera_om"], [])

    def test_cli_exit_codes(self):
        td = tempfile.TemporaryDirectory()
        p = Path(td.name) / "dom.md"
        p.write_text(FIXTURE, encoding="utf-8")
        self.assertEqual(vt.main(["--domfil", str(p)]), 2)
        p.write_text("## DOM K1-R2 — JUSTERAS — t\n## DOM K1 — RÖTT — t\n", encoding="utf-8")
        self.assertEqual(vt.main(["--domfil", str(p)]), 1)
        p.write_text("## DOM A1 — GRÖNT — t\n", encoding="utf-8")
        self.assertEqual(vt.main(["--domfil", str(p)]), 0)
        td.cleanup()

    def test_named_punkter_slash_and_plus(self):
        self.assertEqual(vt.named_punkter("A1"), ["A1"])
        self.assertEqual(
            vt.named_punkter("TURNERING-K1/K2/K3 + RAM-SJALVBEVIS"),
            ["TURNERING-K1", "TURNERING-K2", "TURNERING-K3", "RAM-SJALVBEVIS"],
        )

    def test_samling_stopp_verbatim_from_domfil(self):
        # Ordagrant ur WORK_LOGS/kimi-testprotokoll-domar.md (rad 657).
        heading = (
            "## DOM TURNERING-K1/K2/K3 + RAM-SJALVBEVIS — STOPP "
            "(EJ KÖRBARA: turneringsrunnern existerar inte; gäller ALLA fyra armar) "
            "— 2026-08-16 18:28 CEST (lanister date -u 16:28:14 UTC)"
        )
        rows = vt.parse_domfil(heading + "\n")
        self.assertEqual(
            [r["punkt"] for r in rows],
            ["TURNERING-K1", "TURNERING-K2", "TURNERING-K3", "RAM-SJALVBEVIS"],
        )
        self.assertTrue(all(r["verdict"] == "FAIL" for r in rows))
        self.assertTrue(all(r["heading"] == heading for r in rows))
        doc = vt.report(heading + "\n")
        self.assertEqual(doc["n_domar"], 1)
        self.assertEqual(doc["n_punkter"], 4)
        for name in ("TURNERING-K1", "TURNERING-K2", "TURNERING-K3", "RAM-SJALVBEVIS"):
            step = next(p for p in doc["punkter"] if p["punkt"] == name)
            self.assertEqual(step["consecutive_fail"], 1)

    def test_samling_stopp_increments_each_named_punkt(self):
        heading = (
            "## DOM TURNERING-K1/K2/K3 + RAM-SJALVBEVIS — STOPP "
            "(EJ KÖRBARA: turneringsrunnern existerar inte; gäller ALLA fyra armar) "
            "— 2026-08-16 18:28 CEST (lanister date -u 16:28:14 UTC)"
        )
        text = (
            heading + "\n"
            "## DOM TURNERING-K1-R2 — JUSTERAS — t\n"
            "## DOM TURNERING-K2 — RÖTT — t\n"
        )
        doc = vt.report(text)
        self.assertEqual(
            next(p for p in doc["punkter"] if p["punkt"] == "TURNERING-K1")["consecutive_fail"],
            2,
        )
        self.assertEqual(
            next(p for p in doc["punkter"] if p["punkt"] == "TURNERING-K2")["consecutive_fail"],
            2,
        )
        self.assertEqual(
            next(p for p in doc["punkter"] if p["punkt"] == "TURNERING-K3")["consecutive_fail"],
            1,
        )
        self.assertIn("TURNERING-K1", doc["live_session"])
        self.assertIn("TURNERING-K2", doc["live_session"])
        self.assertNotIn("TURNERING-K3", doc["live_session"])
        self.assertNotIn("RAM-SJALVBEVIS", doc["live_session"])


if __name__ == "__main__":
    unittest.main()

