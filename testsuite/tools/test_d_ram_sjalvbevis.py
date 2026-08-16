#!/usr/bin/env python3
"""Mock tests for the RAM knockback / prevention runner. No rig."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from d_ram_sjalvbevis import RamRunner, knockback_points, sha256_file  # noqa: E402
from d_recipe import load_recipe  # noqa: E402
from verify_d_kvitto import verify  # noqa: E402


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


def _kb(**k):
    arm = k.get("arm", "off")
    return {
        "t_stall_gate": 0.3 if arm == "off" else None,
        "t_arrive": None if arm == "off" else 0.7,
        "land_hit": arm == "on",
        "commanded_vel": list(k.get("vel") or [0.0, 60.0, 0.0]),
        "vel": list(k.get("vel") or [0.0, 60.0, 0.0]),
        "events": [{"ev": "bot_stall"}] if arm == "off" else [{"ev": "arrived", "t": 0.7}],
        "samples": [],
        "stamp_ok": True,
    }


def _trial(**k):
    return {
        "vel": [100.0, 0.0, 0.0],
        "measured_vel": [100.0, 0.0, 0.0],
        "stamp_ok": True,
        "t_arrive": 4.0,
        "t_stall_gate": None,
        "events": [{"ev": "arrived", "t": 4.0}],
        "samples": [],
        "landing_cell": 638,
    }


class RamKvittoTests(unittest.TestCase):
    def test_knockback_receipts_written_and_verify(self):
        import tempfile

        fx = HERE / "recept" / "ram-rail.json"
        with tempfile.TemporaryDirectory() as td:
            runner = RamRunner(
                recipe=load_recipe(fx),
                rail_gates=_rail_gates(),
                prevent_gates=_prevent_gates(),
                exec_knockback=_kb,
                exec_trial=_trial,
                ctl_port=0,
                game_port=27595,
                n_knock=1,
                n_chain=0,
                kvitto_dir=Path(td),
                demo_file="qw/demos/ram.mvd",
                binaries={"qwprogs_sha256": "ab" * 32, "mvdsv_sha256": "cd" * 32},
                fixture_sha256=sha256_file(fx),
            )
            report = runner.run()
            self.assertTrue(report.as_dict()["godkand"], report.as_dict())
            files = sorted(Path(td).glob("*.json"))
            self.assertEqual(len(files), 12, [p.name for p in files])
            points = set()
            for path in files:
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(verify(doc), [], path.name)
                self.assertEqual(doc["candidate"], "ram-rail")
                self.assertEqual(len(doc["fixture_sha256"]), 64)
                self.assertEqual(doc["demo_file"], "qw/demos/ram.mvd")
                self.assertEqual(doc["binaries"]["qwprogs_sha256"], "ab" * 32)
                self.assertEqual(doc["binaries"]["mvdsv_sha256"], "cd" * 32)
                kb = doc["knockback"]
                self.assertIn(kb["point"], {"K1", "K2", "K3", "K4", "K5", "K6"})
                self.assertEqual(len(kb["incoming_velocity"]), 3)
                self.assertIsInstance(kb["land_hit"], bool)
                points.add(kb["point"])
                if kb["land_hit"]:
                    self.assertIsInstance(kb["t_land"], (int, float))
                    self.assertGreater(kb["t_land"], 0.0)
            self.assertEqual(points, {"K1", "K2", "K3", "K4", "K5", "K6"})

    def test_prevention_and_heldout_receipts_verify(self):
        import tempfile

        fx = HERE / "recept" / "ram-prevent.json"
        with tempfile.TemporaryDirectory() as td:
            runner = RamRunner(
                recipe=load_recipe(fx),
                rail_gates=_rail_gates(),
                prevent_gates=_prevent_gates(),
                exec_knockback=_kb,
                exec_trial=_trial,
                ctl_port=0,
                game_port=27595,
                n_knock=0,
                n_chain=1,
                kvitto_dir=Path(td),
                demo_file="qw/demos/ram-prevent.mvd",
                binaries={"qwprogs_sha256": "ab" * 32, "mvdsv_sha256": "cd" * 32},
                fixture_sha256=sha256_file(fx),
            )
            report = runner.run()
            self.assertTrue(report.as_dict()["godkand"], report.as_dict())
            files = sorted(Path(td).glob("*.json"))
            strata = {json.loads(p.read_text())["stratum"]["id"] for p in files}
            self.assertEqual(strata, {"P1", "P2", "H1", "H2", "H3", "H4"})
            self.assertEqual(len(files), 12)
            for path in files:
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(verify(doc), [], path.name)
                self.assertEqual(doc["candidate"], "ram-prevent")
                self.assertNotIn("knockback", doc)
                self.assertEqual(doc["landing_cell"], 638)

    def test_cli_kvitto_dir_is_used(self):
        import tempfile
        from d_ram_sjalvbevis import main as ram_main

        with tempfile.TemporaryDirectory() as td:
            rc = ram_main([
                "--port", "0",
                "--recipe", "ram-rail",
                "--smoke",
                "--kvitto-dir", td,
            ])
            self.assertEqual(rc, 0)
            files = list(Path(td).glob("*.json"))
            self.assertTrue(files, "CLI --kvitto-dir must write receipts")
            doc = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(verify(doc), [])
            self.assertIn(doc["knockback"]["point"], {"K1", "K2", "K3", "K4", "K5", "K6"})


if __name__ == "__main__":
    unittest.main()
