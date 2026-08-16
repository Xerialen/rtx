#!/usr/bin/env python3
"""Mock tests for the RAM knockback / prevention runner. No rig."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from d_ram_sjalvbevis import RamRunner, knockback_points  # noqa: E402
from d_recipe import load_recipe  # noqa: E402


def _rail_gates():
    return json.loads((HERE / "recept" / "ram-rail-gates.json").read_text(encoding="utf-8"))


def _prevent_gates():
    return json.loads((HERE / "recept" / "ram-prevent-gates.json").read_text(encoding="utf-8"))


class KnockbackLoaderTests(unittest.TestCase):
    def test_six_points_k1_to_k6(self):
        pts = knockback_points(_rail_gates())
        self.assertEqual([p["id"] for p in pts], ["K1", "K2", "K3", "K4", "K5", "K6"])
        self.assertEqual(pts[0]["pos"], [-360.0, -784.0, 128.03125])
        self.assertEqual(pts[0]["velocity"], [0.0, 60.0, 0.0])
        self.assertEqual(pts[0]["budget_s"], 2.0)
        self.assertEqual(pts[5]["velocity"], [0.0, -100.0, 40.0])


class RamScoringTests(unittest.TestCase):
    def test_perfect_rail_knockback(self):
        def kb(**k):
            arm = k["arm"]
            return {
                "t_stall_gate": 0.3 if arm == "off" else None,
                "t_arrive": None if arm == "off" else 0.7,
                "land_hit": arm == "on",
                "events": [{"ev": "bot_stall"}] if arm == "off" else [],
                "samples": [],
                "stamp_ok": True,
            }

        def trial(**k):
            return {
                "vel": [100.0, 0.0, 0.0],
                "measured_vel": [100.0, 0.0, 0.0],
                "stamp_ok": True,
                "t_arrive": 4.0,
                "t_stall_gate": None,
                "events": [{"ev": "arrived", "t": 4.0}],
                "samples": [],
            }

        runner = RamRunner(
            recipe=load_recipe(HERE / "recept" / "ram-rail.json"),
            rail_gates=_rail_gates(),
            prevent_gates=_prevent_gates(),
            exec_knockback=kb,
            exec_trial=trial,
            ctl_port=0,
            game_port=27595,
            n_knock=1,
            n_chain=0,
        )
        report = runner.run()
        d = report.as_dict()
        self.assertTrue(d["valid"], d["invalid_reasons"])
        self.assertTrue(d["knockback"]["off_ok"])
        self.assertTrue(d["knockback"]["on_ok"])
        self.assertTrue(d["godkand"], d)

    def test_on_stall_fails_knockback(self):
        def kb(**k):
            return {
                "t_stall_gate": 0.2,
                "t_arrive": None,
                "land_hit": False,
                "events": [{"ev": "bot_stall"}],
                "samples": [],
                "stamp_ok": True,
            }

        runner = RamRunner(
            recipe=load_recipe(HERE / "recept" / "ram-rail.json"),
            rail_gates=_rail_gates(),
            exec_knockback=kb,
            exec_trial=lambda **k: {},
            ctl_port=0,
            game_port=27595,
            n_knock=1,
            n_chain=0,
        )
        report = runner.run()
        self.assertTrue(report.knockback_off_ok)
        self.assertFalse(report.knockback_on_ok)

    def test_ra_preflight(self):
        runner = RamRunner(
            recipe=load_recipe(HERE / "recept" / "ram-rail.json"),
            rail_gates=_rail_gates(),
            exec_knockback=lambda **k: {},
            exec_trial=lambda **k: {},
            ctl_port=27990,
            game_port=27595,
        )
        why = runner.preflight()
        self.assertTrue(any("RA/main" in w for w in why), why)

    def test_p5_port_without_lock_is_rc2_never_stub_green(self):
        """P5: --port against a real ctl must not stub-GODKAND."""
        from d_ram_sjalvbevis import main as ram_main

        rc = ram_main([
            "--port", "27999",
            "--recipe", "ram-rail",
            "--smoke",
            "--lock", "/tmp/rtx-no-such-rig-lock",
        ])
        self.assertEqual(rc, 2)

    def test_p5_ra_port_refused(self):
        from d_ram_sjalvbevis import main as ram_main

        rc = ram_main(["--port", "27990", "--recipe", "ram-rail", "--smoke"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
