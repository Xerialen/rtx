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
sys.path.insert(0, str(HERE.parent))

from test_lab_guard import install_lab_guard, uninstall_lab_guard  # noqa: E402
from timtest_d_ports import EXIT_REFUSED, port_fel  # noqa: E402
from timtest_d_kluster import (
    MEASURE_ID, MEASURE_ID_FALL_EPISODER,
    klassa_utfall, kvot_krav_samma_measure, rapport_rad, samla_kluster,
    skriv_kluster,
)  # noqa: E402
import timtest_d  # noqa: E402
import timtest_ben as ben  # noqa: E402


def setUpModule():
    install_lab_guard()


def tearDownModule():
    uninstall_lab_guard()


def _idle_freeze_args():
    """--freeze-path mot tmp, fil saknas = ingen freeze. Aldrig passwd-hem."""
    flag = str(Path(tempfile.mkdtemp(prefix="timtest-d-idlefz-")) / ".change-freeze")
    return ["--freeze-path", flag]


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
                 "--out", "/tmp/timtest-d-should-not-exist", "--mock",
                 *_idle_freeze_args()],
                cwd=str(HERE), capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, EXIT_REFUSED, r.stderr)
            self.assertIn("VÄGRAR", r.stderr)

    def test_cli_freeze_rc2(self):
        td = tempfile.mkdtemp(prefix="timtest-d-freeze-")
        import d_failclosed as fc
        flag = Path(td) / ".change-freeze"
        ctx = fc.FreezeContext.for_test(flag)
        fc.write_change_freeze("fable", freeze=ctx)
        r = subprocess.run(
            [sys.executable, str(HERE / "timtest_d.py"),
             "--port", "27999", "--game-port", "27595",
             "--out", td, "--mock", "--freeze-path", str(flag)],
            cwd=str(HERE), capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, EXIT_REFUSED, r.stderr)
        self.assertIn("change-freeze", r.stderr)
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
            *_idle_freeze_args(),
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
        # rev 3 (punkt 8): varje rå-kvitto bär measure_id — meta OCH jsonl-rader
        for ben_namn in ("ut_ring", "in_ring", "ut_tunnel", "in_tunnel",
                         "ut_vast", "in_vast"):
            meta = json.loads((out / "c001" / ("%s_meta.json" % ben_namn)).read_text())
            self.assertEqual(meta["measure_id"], "klassa_utfall@r6", ben_namn)
            self.assertEqual(meta["falls_measure_id"], "fall_peak_drop_150@r6", ben_namn)
            rader = [json.loads(ln) for ln in
                     (out / "c001" / ("%s.jsonl" % ben_namn)).read_text().splitlines()
                     if ln.strip()]
            self.assertTrue(rader, ben_namn)
            for rad in rader:
                self.assertEqual(rad["measure_id"], "klassa_utfall@r6", ben_namn)
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
            *_idle_freeze_args(),
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
            *_idle_freeze_args(),
        ])
        self.assertEqual(rc, 0)
        man = json.loads((Path(td) / "manifest.json").read_text())
        self.assertEqual(man["duration"], 20.0)

    def test_duration_noll_vagrar(self):
        r = subprocess.run(
            [sys.executable, str(HERE / "timtest_d.py"),
             "--port", "27999", "--game-port", "27595",
             "--out", "/tmp/timtest-d-should-not-exist-dur",
             "--mock", "--duration", "0", *_idle_freeze_args()],
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
             "--mock", "--duration", "20", *_idle_freeze_args()],
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

    def test_kvot_vagrar_saknat_measure_id(self):
        # grok2-p8 (iii): None==None får inte bli en tyst kvot.
        with self.assertRaises(ValueError):
            kvot_krav_samma_measure(113, 900, None, None)
        with self.assertRaises(ValueError):
            kvot_krav_samma_measure(113, 900, "", "")
        with self.assertRaises(ValueError):
            kvot_krav_samma_measure(113, 900, None, MEASURE_ID)

    def test_kvot_tillat_omarkta(self):
        self.assertAlmostEqual(
            kvot_krav_samma_measure(30, 372, None, None, tillat_omarkta=True),
            30 / 372)

    def test_rapport_rad_vagrar_saknat(self):
        with self.assertRaises(ValueError):
            rapport_rad("fall/ben", 30, 372, None)

    def test_skriv_kluster_vagrar_oexcl_overwrite(self):
        td = Path(tempfile.mkdtemp(prefix="timtest-d-oexcl-"))
        self._skriv_ben(td, "c001", "ut_vast", "fall",
                        [-360.0, -700.0, -16.0], cell=1462)
        skriv_kluster(td)
        with self.assertRaises(FileExistsError):
            skriv_kluster(td)

    def test_kluster_buckets_bar_measure_id(self):
        td = Path(tempfile.mkdtemp(prefix="timtest-d-bucket-"))
        self._skriv_ben(td, "c001", "ut_vast", "fall",
                        [-360.0, -700.0, -16.0], cell=1462)
        doc = samla_kluster(td)
        self.assertEqual(doc["kluster"][0]["measure_id"], "klassa_utfall@r6")

    def test_ben_meta_och_skribent_bar_measure_id(self):
        meta = ben._meta("kedjad", "framme", 1.0, 0)
        self.assertEqual(meta["measure_id"], "klassa_utfall@r6")
        self.assertEqual(meta["falls_measure_id"], "fall_peak_drop_150@r6")

    def test_ben_skribent_oexcl_vagrar(self):
        td = Path(tempfile.mkdtemp(prefix="timtest-d-raw-oexcl-"))
        p = td / "c001" / "ut_vast.jsonl"
        ben._write_exclusive(p, "{\"t\":1.0}\n")
        with self.assertRaises(FileExistsError):
            ben._write_exclusive(p, "{\"t\":2.0}\n")

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
        # timtest_ben.py fick punkt 8 rev 3: measure_id i meta + jsonl-rader via
        # O_EXCL-vägen. Mätlogiken (ben_forsok/utfall/falls) är oförändrad — bara
        # skrivningen. Ny sha låst nedan; orkestern och granskriterier är orörda.
        want = {
            "timtest_ben.py": "40442ce9d4c2f9cec1998c3c67afa1b546e1bbd675f3a6fb20351a7eb3027baa",
            "timtest_orkester.py": "f42c9078904d4d8b34ceb97c2ba57f778daa44b91685d4c7a8afa13950877d3f",
            "granskriterier.py": "f19ffd18f75a56c5441dbc90f6ec3df0634be6adbdfb53108e9db0a598764cf9",
        }
        for name, h in want.items():
            p = HERE / name
            self.assertTrue(p.is_file(), name)
            import hashlib
            got = hashlib.sha256(p.read_bytes()).hexdigest()
            self.assertEqual(got, h, name)
            # Låset är SHA-256. git checkout är skrivbar; 444 gäller inte arkiv/extrakt.


# --- T1h-timslut vs T20m: EN generator, ETT dataset ----------------------

def _giltigt_ben(namn: str) -> dict:
    return {"ben": namn, "utfall": "framme", "falls": 0, "tic_drift_pct": 0.0}


def _ogiltigt_ben(namn: str) -> dict:
    return {"ben": namn, "utfall": "ogiltig_tic", "falls": 0,
            "tic_drift_pct": 50.0, "skal": "tic-drift 50% > 1%"}


def _cykel_kvitton(*, ogiltig=None) -> dict:
    bens = {b: _giltigt_ben(b) for b in timtest_d.CYKEL_BEN}
    if ogiltig:
        bens[ogiltig] = _ogiltigt_ben(ogiltig)
    return bens


def generera_sekvens():
    """Ett dataset. frac_start = andel av fönstret vid grind.
    Samma lista matas till T1h-vägen (W=60) och T20m-vägen (W=20)."""
    return [
        {"frac_start": 0.0, "bens": _cykel_kvitton()},
        {"frac_start": 0.99, "bens": _cykel_kvitton()},
        {"frac_start": 1.10, "bens": _cykel_kvitton()},
    ]


def generera_lt_cykel():
    """duration < cykelvägg: cykel 1 tar 2 fönster, cykel 2 startar efter."""
    return [
        {"frac_start": 0.0, "bens": _cykel_kvitton()},
        {"frac_start": 2.0, "bens": _cykel_kvitton()},
    ]


class T1hFonsterRegelTests(unittest.TestCase):
    """Varv 2: samma dataset genom T1h (60) och T20m (20). hela ur n_regel."""

    def test_samma_sekvens_lt_cykel(self):
        seq = generera_lt_cykel()
        t1h = timtest_d.t1h_beslut(seq, 60.0)
        t20 = timtest_d.t1h_beslut(seq, 20.0)
        self.assertIs(seq, seq)
        self.assertEqual(t1h, t20)
        self.assertEqual(t1h, {
            "n_cykler": 1,
            "hela": (True,),
            "klippt_sista_ben": False,
        })

    def test_samma_sekvens_mitt_i_cykel(self):
        seq = generera_sekvens()
        t1h = timtest_d.t1h_beslut(seq, 60.0)
        t20 = timtest_d.t1h_beslut(seq, 20.0)
        self.assertEqual(id(seq), id(seq))
        self.assertEqual(t1h, t20)
        # C1 @ 0 och C2 @ 0.99·W startar; C3 @ 1.10·W startar inte.
        # C2 påbörjas före W och räknas hel (benet klipps inte).
        self.assertEqual(t1h, {
            "n_cykler": 2,
            "hela": (True, True),
            "klippt_sista_ben": False,
        })

    def test_hela_ur_t1h_n_regel_inte_filnarvaro(self):
        """Sex ben med ett ogiltig_tic är INTE hel (n_regel), även om alla 'finns'."""
        seq = [
            {"frac_start": 0.0, "bens": _cykel_kvitton(ogiltig="in_vast")},
        ]
        t1h = timtest_d.t1h_beslut(seq, 60.0)
        t20 = timtest_d.t1h_beslut(seq, 20.0)
        self.assertEqual(t1h, t20)
        self.assertEqual(t1h["n_cykler"], 1)
        self.assertEqual(t1h["hela"], (False,))
        self.assertTrue(timtest_d.t1h_cykel_hel(_cykel_kvitton()))
        self.assertFalse(timtest_d.t1h_cykel_hel(_cykel_kvitton(ogiltig="ut_ring")))
        self.assertFalse(timtest_d.t1h_cykel_hel({"ut_ring": _giltigt_ben("ut_ring")}))


class DurationLasningTests(unittest.TestCase):
    """(3) --duration låst på dömd --run; fri bara mock/dry."""

    def test_run_refuses_duration(self):
        r = subprocess.run(
            [sys.executable, str(HERE / "timtest_d.py"),
             "--port", "27999", "--game-port", "27595",
             "--out", "/tmp/timtest-d-should-not-exist-run-dur",
             "--run", "--duration", "20", *_idle_freeze_args()],
            cwd=str(HERE), capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, EXIT_REFUSED)
        self.assertIn("judged --run refuses --duration/--minuter", r.stderr)

    def test_run_refuses_minuter_alias(self):
        r = subprocess.run(
            [sys.executable, str(HERE / "timtest_d.py"),
             "--port", "27999", "--game-port", "27595",
             "--out", "/tmp/timtest-d-should-not-exist-run-min",
             "--run", "--minuter", "20", *_idle_freeze_args()],
            cwd=str(HERE), capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, EXIT_REFUSED)
        self.assertIn("judged --run refuses --duration/--minuter", r.stderr)

    def test_run_lasar_duration_ur_gates(self):
        td = tempfile.mkdtemp(prefix="timtest-d-gates-")
        gates = Path(td) / "gates.json"
        gates.write_text(json.dumps({"id": "timtest-d", "duration_min": 20}),
                         encoding="utf-8")
        out = Path(td) / "out"
        rc = timtest_d.main([
            "--port", "27999", "--game-port", "27595",
            "--out", str(out), "--run", "--mock", "--gates", str(gates),
            *_idle_freeze_args(),
        ])
        self.assertEqual(rc, 0)
        man = json.loads((out / "manifest.json").read_text())
        self.assertEqual(man["duration"], 20.0)
        self.assertEqual(man["duration_source"], "gates")
        self.assertTrue(man["judged"])

    def test_live_cli_duration_vagrar(self):
        r = subprocess.run(
            [sys.executable, str(HERE / "timtest_d.py"),
             "--port", "27999", "--game-port", "27595",
             "--out", "/tmp/timtest-d-should-not-exist-live-dur",
             "--duration", "20", *_idle_freeze_args()],
            cwd=str(HERE), capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, EXIT_REFUSED)
        self.assertIn("CLI --duration bara med --mock/--dry", r.stderr)


if __name__ == "__main__":
    unittest.main()
