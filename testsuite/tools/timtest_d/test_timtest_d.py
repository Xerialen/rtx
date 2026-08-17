#!/usr/bin/env python3
"""Snabbtester: portvakt + enarmad mock-flöde. Ingen riggkontakt."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from timtest_d_ports import EXIT_REFUSED, port_fel  # noqa: E402
from timtest_d_kluster import (
    MEASURE_ID, MEASURE_ID_FALL_EPISODER,
    klassa_utfall, kvot_krav_samma_measure, rapport_rad, samla_kluster,
)  # noqa: E402
import timtest_d  # noqa: E402


class PortvaktTests(unittest.TestCase):
    def test_fyra_forbjudna(self):
        self.assertIsNotNone(port_fel(27990, 27595))
        self.assertIsNotNone(port_fel(27993, 27595))
        self.assertIsNotNone(port_fel(27999, 27540))
        self.assertIsNotNone(port_fel(27999, 27570))

    def test_utanfor_tbx(self):
        self.assertIsNotNone(port_fel(27991, 27595))
        self.assertIsNotNone(port_fel(27999, 27591))
        self.assertIsNotNone(port_fel(28000, 27595))

    def test_tillaten_d4(self):
        self.assertIsNone(port_fel(27999, 27595))
        self.assertIsNone(port_fel(27996, 27592))

    def test_cli_forbjuden_rc2(self):
        for port, game in ((27990, 27595), (27993, 27595),
                           (27999, 27540), (27999, 27570)):
            r = subprocess.run(
                [sys.executable, str(HERE / "timtest_d.py"),
                 "--port", str(port), "--game-port", str(game),
                 "--out", "/tmp/timtest-d-should-not-exist", "--mock"],
                cwd=str(HERE), capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, EXIT_REFUSED, r.stderr)
            self.assertIn("VÄGRAR", r.stderr)

    def test_cli_tillaten_nar_mock(self):
        """Portvakten släpper d4; mock tar aldrig socket."""
        self.assertIsNone(port_fel(27999, 27595))


class EnarmsMockTests(unittest.TestCase):
    def test_mock_en_cykel_skriver_jsonl_meta_kluster(self):
        td = tempfile.mkdtemp(prefix="timtest-d-mock-")
        rc = timtest_d.main([
            "--host", "127.0.0.1",
            "--port", "27999",
            "--game-port", "27595",
            "--out", td,
            "--mock",
        ])
        self.assertEqual(rc, 0)
        out = Path(td)
        self.assertTrue((out / "manifest.json").is_file())
        man = json.loads((out / "manifest.json").read_text())
        self.assertTrue(man["ingen_systemctl"])
        self.assertTrue(man["ingen_replant"])
        self.assertEqual(man["arm"], "D")
        self.assertEqual(man["port"]["kontroll"], 27999)
        # sex ben i c001
        for ben in ("ut_ring", "in_ring", "ut_tunnel", "in_tunnel",
                    "ut_vast", "in_vast"):
            self.assertTrue((out / "c001" / ("%s.jsonl" % ben)).is_file(), ben)
            self.assertTrue((out / "c001" / ("%s_meta.json" % ben)).is_file(), ben)
        kl = json.loads((out / "kluster.json").read_text())
        self.assertEqual(kl["schema"], "timtest-d/kluster/1")
        self.assertEqual(kl["n_ben"], 6)
        # originalen orörda
        self.assertFalse(man.get("ab_tabell"))
        # default-fönster är 60; mock tvingar effektiv 1 cykel
        self.assertEqual(man["duration"], 60.0)
        self.assertEqual(man["minuter"], 1)


class DurationFlagTests(unittest.TestCase):
    """Punkt 6: --duration parametriserar bara fönstret. Ingen rigg."""

    def test_duration_20_mock_stays_one_cycle(self):
        td = tempfile.mkdtemp(prefix="timtest-d-dur20-")
        rc = timtest_d.main([
            "--port", "27999", "--game-port", "27595",
            "--out", td, "--mock", "--duration", "20",
        ])
        self.assertEqual(rc, 0)
        man = json.loads((Path(td) / "manifest.json").read_text())
        self.assertEqual(man["duration"], 20.0)
        self.assertEqual(man["minuter"], 1)
        self.assertTrue((Path(td) / "c001" / "in_vast.jsonl").is_file())

    def test_minuter_alias(self):
        td = tempfile.mkdtemp(prefix="timtest-d-min-")
        rc = timtest_d.main([
            "--port", "27999", "--game-port", "27595",
            "--out", td, "--mock", "--minuter", "20",
        ])
        self.assertEqual(rc, 0)
        man = json.loads((Path(td) / "manifest.json").read_text())
        self.assertEqual(man["duration"], 20.0)

    def test_duration_noll_vagrar(self):
        r = subprocess.run(
            [sys.executable, str(HERE / "timtest_d.py"),
             "--port", "27999", "--game-port", "27595",
             "--out", "/tmp/timtest-d-should-not-exist-dur",
             "--mock", "--duration", "0"],
            cwd=str(HERE), capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, EXIT_REFUSED)
        self.assertIn("VÄGRAR", r.stderr)
        self.assertIn("duration", r.stderr)

    def test_portvakt_oforandrad_med_duration(self):
        r = subprocess.run(
            [sys.executable, str(HERE / "timtest_d.py"),
             "--port", "27990", "--game-port", "27595",
             "--out", "/tmp/timtest-d-should-not-exist-dur2",
             "--mock", "--duration", "20"],
            cwd=str(HERE), capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, EXIT_REFUSED)
        self.assertIn("VÄGRAR", r.stderr)


class KlusterKlassTests(unittest.TestCase):
    def test_measure_id_pa_rakvitto(self):
        td = Path(tempfile.mkdtemp(prefix="timtest-d-mid-"))
        self._skriv_ben(td, "c001", "ut_vast", "fall",
                        [-360.0, -700.0, -16.0], cell=1462)
        doc = samla_kluster(td)
        self.assertEqual(doc["measure_id"], "klassa_utfall@r6")

    def test_kvot_vagrar_blandmatt(self):
        # −36 %-läxan: fall-EPISODER (113) ställdes mot fall-UTFALL (30/ben).
        with self.assertRaises(ValueError):
            kvot_krav_samma_measure(113, 900, MEASURE_ID_FALL_EPISODER, MEASURE_ID)
        # samma mått: tillåten
        self.assertAlmostEqual(
            kvot_krav_samma_measure(30, 372, MEASURE_ID, MEASURE_ID),
            30 / 372)

    def test_rapport_rad_med_measure_id(self):
        rad = rapport_rad("fall/ben", 30, 372, MEASURE_ID)
        self.assertEqual(rad["measure_id"], "klassa_utfall@r6")
        self.assertEqual(rad["pct"], 8.1)

    def test_klassa_utfall(self):
        self.assertEqual(klassa_utfall("fall"), (True, False, False))
        self.assertEqual(klassa_utfall("fastnad"), (False, True, False))
        self.assertEqual(klassa_utfall("fall_plus_fastnad"), (True, True, False))
        self.assertEqual(klassa_utfall("fall_efter_framme"), (False, False, True))
        self.assertEqual(klassa_utfall("framme"), (False, False, False))
        self.assertEqual(klassa_utfall("ogiltig_tic"), (False, False, False))

    def _skriv_ben(self, root: Path, cykel: str, ben: str, utfall: str, origin, cell=None):
        d = root / cykel
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s_meta.json" % ben)).write_text(json.dumps({
            "utfall": utfall, "ben": ben, "cykel": 1, "falls": 1,
        }), encoding="utf-8")
        row = {"t": 1.0, "players": [{"ent": 1, "origin": origin}]}
        if cell is not None:
            row["cell"] = cell
        (d / ("%s.jsonl" % ben)).write_text(json.dumps(row) + "\n", encoding="utf-8")

    def test_fall_plus_fastnad_i_bada_buckets(self):
        td = Path(tempfile.mkdtemp(prefix="timtest-d-kl-"))
        self._skriv_ben(td, "c001", "ut_vast", "fall_plus_fastnad",
                        [-360.0, -700.0, -16.0], cell=1462)
        doc = samla_kluster(td)
        self.assertEqual(doc["n_fall"], 1)
        self.assertEqual(doc["n_fastnad"], 1)
        self.assertEqual(doc["n_miss"], 0)
        typer = {r["typ"]: r for r in doc["kluster"]}
        self.assertIn("fall", typer)
        self.assertIn("fastnad", typer)
        self.assertEqual(typer["fall"]["n"], 1)
        self.assertEqual(typer["fastnad"]["n"], 1)
        self.assertEqual(typer["fall"]["cell"], 1462)
        self.assertEqual(typer["fastnad"]["cell"], 1462)
        self.assertNotIn("miss", typer)

    def test_fall_efter_framme_ar_miss_inte_fall(self):
        td = Path(tempfile.mkdtemp(prefix="timtest-d-miss-"))
        self._skriv_ben(td, "c001", "ut_ring", "fall_efter_framme",
                        [498.0, -304.0, 56.0], cell=1194)
        doc = samla_kluster(td)
        self.assertEqual(doc["n_fall"], 0)
        self.assertEqual(doc["n_fastnad"], 0)
        self.assertEqual(doc["n_miss"], 1)
        self.assertIn("miss efter ankomst", doc["miss_not"])
        typer = {r["typ"]: r for r in doc["kluster"]}
        self.assertIn("miss", typer)
        self.assertNotIn("fall", typer)
        self.assertEqual(typer["miss"]["n"], 1)
        self.assertEqual(typer["miss"]["cell"], 1194)
        self.assertEqual(typer["miss"]["utfall"], ["fall_efter_framme"])


class OriginalOrordaTests(unittest.TestCase):
    def test_frysta_kopior_matchar_sha(self):
        # sha låst vid kopiering 2026-08-16; om någon rört originalen failar detta
        want = {
            "timtest_ben.py": "7b6fadf4d7f1de2fb69d6edca2348c8fb2e658630d248ad45073548df6d42e98",
            "timtest_orkester.py": "f42c9078904d4d8b34ceb97c2ba57f778daa44b91685d4c7a8afa13950877d3f",
            "granskriterier.py": "f19ffd18f75a56c5441dbc90f6ec3df0634be6adbdfb53108e9db0a598764cf9",
        }
        for name, h in want.items():
            p = HERE / name
            self.assertTrue(p.is_file(), name)
            import hashlib
            got = hashlib.sha256(p.read_bytes()).hexdigest()
            self.assertEqual(got, h, name)
            self.assertFalse(os.access(p, os.W_OK), "%s ska vara 444" % name)


if __name__ == "__main__":
    unittest.main()
