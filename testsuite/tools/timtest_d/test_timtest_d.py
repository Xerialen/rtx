#!/usr/bin/env python3
"""Snabbtester: portvakt + enarmad mock-flöde. Ingen riggkontakt."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from timtest_d_ports import EXIT_REFUSED, port_fel  # noqa: E402
from timtest_d_kluster import (
    MEASURE_ID, MEASURE_ID_FALL_EPISODER,
    klassa_utfall, kvot_krav_samma_measure, rapport_rad, samla_kluster,
)  # noqa: E402
import timtest_d  # noqa: E402
import timtest_ben as ben  # noqa: E402


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

    def test_cli_freeze_rc2(self):
        td = tempfile.mkdtemp(prefix="timtest-d-freeze-")
        flag = Path(td) / "change-freeze"
        flag.write_text("fable 2026-08-17T07:15:00Z\n", encoding="utf-8")
        env = dict(os.environ)
        env["D_CHANGE_FREEZE"] = str(flag)
        r = subprocess.run(
            [sys.executable, str(HERE / "timtest_d.py"),
             "--port", "27999", "--game-port", "27595",
             "--out", td, "--mock"],
            cwd=str(HERE), capture_output=True, text=True, env=env,
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


# --- T1h-timslut vs --duration (grok §A4) ---------------------------------
# Fryst koda_arm: grind ENDAST vid cykelstart
#   if not dry and (monotonic()-t0)/60 >= minuter: break
# Påbörjad cykel fullföljer alla sex ben. Benet klipps inte.

CYKEL = ["ut_ring", "in_ring", "ut_tunnel", "in_tunnel", "ut_vast", "in_vast"]


class FakeClock:
    """Styrbar monotonic. time.sleep → advance, ingen riktig vägg."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)

    def set(self, seconds: float) -> None:
        self.t = float(seconds)


@contextmanager
def patched_ben_clock(clock: FakeClock):
    old_mono = ben.time.monotonic
    old_sleep = ben.time.sleep
    ben.time.monotonic = clock.monotonic
    ben.time.sleep = lambda s: clock.advance(float(s))
    try:
        yield
    finally:
        ben.time.monotonic = old_mono
        ben.time.sleep = old_sleep


class ClockLab(timtest_d.FakeLab):
    """FakeLab utan riktig sleep. Klockan styrs; ev. hopp efter N goto."""

    def __init__(self, clock: FakeClock, jump_after_gotos=None, jump_to_s=None,
                 tick_s: float = 0.05):
        super().__init__()
        self.clock = clock
        self.n_goto = 0
        self.jump_after_gotos = jump_after_gotos
        self.jump_to_s = jump_to_s
        self.tick_s = tick_s

    def goto(self, bot, pos):
        super().goto(bot, pos)
        self.n_goto += 1
        if (self.jump_after_gotos is not None
                and self.n_goto == self.jump_after_gotos
                and self.jump_to_s is not None
                and self.clock.t < self.jump_to_s):
            self.clock.set(self.jump_to_s)
        return {}

    def request(self, cmd, timeout=8.0):
        self.clock.advance(self.tick_s)
        self.cmds.append(("request", cmd))
        if cmd == "Status":
            if self.goal is not None:
                self.origin = list(self.goal)
            self.t += 1.0
            return {
                "Status": {
                    "time": self.t,
                    "map": "dm3",
                    "cells": 0,
                    "links": 0,
                    "bots": [{
                        "ent": ben.BOT,
                        "alive": True,
                        "origin": list(self.origin),
                        "on_ground": True,
                    }],
                }
            }
        if isinstance(cmd, dict) and "RunCmd" in cmd:
            return {"Queued": True}
        if isinstance(cmd, dict) and "Cell" in cmd:
            return {"Cell": {"cell": None}}
        if isinstance(cmd, dict) and "Set" in cmd:
            return {}
        raise RuntimeError("ClockLab: okänt cmd %r" % (cmd,))


def _metas(outdir: Path, cykel: int) -> list[str]:
    d = outdir / ("c%03d" % cykel)
    if not d.is_dir():
        return []
    return [b for b in CYKEL if (d / ("%s_meta.json" % b)).is_file()]


def _beslut(outdir: Path) -> dict:
    """Beslut vid fönstergränsen: vilka cykler startades och om de är hela."""
    n = 0
    hela = []
    while (outdir / ("c%03d" % (n + 1))).is_dir():
        n += 1
        hela.append(_metas(outdir, n) == CYKEL)
    return {
        "n_cykler": n,
        "hela": tuple(hela),
        "klippt_sista_ben": bool(n) and not hela[-1],
        "c_efter_sista": (outdir / ("c%03d" % (n + 1))).is_dir(),
    }


def _koda_fonster(minuter: float, outdir: Path, lab: ClockLab) -> int:
    """Samma anrop som T1h-orkestern och som timtest_d vid --duration (ej mock)."""
    return ben.koda_arm(lab, "D", str(outdir), minuter, dry=False)


class T1hFonsterRegelTests(unittest.TestCase):
    """§A4: duration<cykel och mitt-i-cykel. T1h-väg och T20m-väg bit-identiska."""

    def test_duration_lt_cykeltid_en_hel_cykel_t1h_eq_t20m(self):
        """duration < en cykels vägg ⇒ en HEL cykel (inte noll, inte klippt ben).

        T1h-regeln (gate vid cykelstart): elapsed≈0 < duration ⇒ starta;
        efter cykeln elapsed ≥ duration ⇒ stopp. Samma funktion som T1h
        (koda_arm) körs med litet fönster och med 20 min + uppskalad
        benvägg så cykeln överskrider fönstret i båda fallen.
        """
        # Väg A: litet fönster, naturlig FakeLab-cykel (~sekunder) > 0.05 min.
        a = self._kor_lt_cykel(minuter=0.01, tick_s=0.1)
        # Väg B: 20-minutersvägen, men varje Status är 30 s så 6 ben >> 20 min.
        b = self._kor_lt_cykel(minuter=20.0, tick_s=30.0)
        self.assertEqual(a, b)
        self.assertEqual(a["n_cykler"], 1)
        self.assertEqual(a["hela"], (True,))
        self.assertFalse(a["klippt_sista_ben"])
        self.assertFalse(a["c_efter_sista"])

    def _kor_lt_cykel(self, minuter: float, tick_s: float) -> dict:
        td = Path(tempfile.mkdtemp(prefix="timtest-d-ltcykel-"))
        clock = FakeClock()
        lab = ClockLab(clock, tick_s=tick_s)
        with patched_ben_clock(clock):
            n = _koda_fonster(minuter, td, lab)
        self.assertLess(n, 8, "koda_arm stack iväg — grind trasig")
        return _beslut(td)

    def test_mitt_i_cykel_fullfoljs_t1h_eq_t20m(self):
        """Grind precis före fönstergränsen: cykeln startar och fullföljs
        även när väggen passerar mitt i de sex benen. T1h (60) och T20m
        (20) mot samma relativa sekvens ⇒ identiskt beslut.
        """
        t1h = self._kor_grans(window_min=60.0)
        t20 = self._kor_grans(window_min=20.0)
        self.assertEqual(t1h, t20)
        self.assertEqual(t1h["n_cykler"], 2)
        self.assertEqual(t1h["hela"], (True, True))
        self.assertFalse(t1h["klippt_sista_ben"])
        self.assertFalse(t1h["c_efter_sista"])

    def _kor_grans(self, window_min: float) -> dict:
        td = Path(tempfile.mkdtemp(prefix="timtest-d-grans-"))
        clock = FakeClock()
        window_s = window_min * 60.0
        # Efter första cykelns sista goto: hoppa till 99,5 % av fönstret.
        lab = ClockLab(clock, jump_after_gotos=6, jump_to_s=0.995 * window_s)
        with patched_ben_clock(clock):
            n = _koda_fonster(window_min, td, lab)
        self.assertLess(n, 8, "koda_arm stack iväg — grind trasig")
        # Fönstret ska ha passerats under cykel 2 (klockan ≥ window).
        self.assertGreaterEqual(clock.t, window_s)
        return _beslut(td)


if __name__ == "__main__":
    unittest.main()
