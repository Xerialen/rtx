#!/usr/bin/env python3
"""Mock tests for the RAM knockback / prevention runner. No rig."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from d_ram_sjalvbevis import (  # noqa: E402
    EAST_FLOOR_CELLS,
    RamRunner,
    attribute_pair,
    knockback_points,
    knockback_zone,
    raw_from_jsonl,
    recipe_kvitto_paths,
    score_knockback_raw,
    sha256_file,
)
from d_recipe import load_recipe  # noqa: E402
from verify_d_kvitto import verify  # noqa: E402

LAB = Path.home() / "lab" / "ram-v2-sjalvbevis"
TD = HERE / "testdata" / "ram-v3"


def _rail_gates():
    return json.loads((HERE / "recept" / "ram-rail-gates.json").read_text(encoding="utf-8"))


def _rail_v2_gates():
    return json.loads((HERE / "recept" / "ram-rail-v2-gates.json").read_text(encoding="utf-8"))


def _prevent_gates():
    return json.loads((HERE / "recept" / "ram-prevent-gates.json").read_text(encoding="utf-8"))


def _live_jsonl(stem: str) -> Path:
    for p in (LAB / f"{stem}.jsonl", TD / f"{stem}.jsonl"):
        if p.is_file():
            return p
    raise FileNotFoundError(stem)


def _live_astar(stem: str) -> dict:
    lab = LAB / f"{stem}.json"
    if lab.is_file():
        return json.loads(lab.read_text(encoding="utf-8"))
    td = TD / f"{stem}.astar.json"
    return json.loads(td.read_text(encoding="utf-8"))


def _inject_astar(raw: dict, rec: dict) -> dict:
    ast = rec.get("astar") or rec
    out = dict(raw)
    out["astar_before"] = ast.get("before") or {}
    out["astar_after"] = ast.get("after") or {}
    out["astar_next_best"] = ast.get("next_best") or {}
    if rec.get("landing_cell") is not None:
        out["landing_cell"] = rec["landing_cell"]
    return out


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
            files = sorted((Path(td) / "ram-rail").glob("*.json"))
            self.assertEqual(len(files), 12, [p.name for p in files])
            self.assertFalse(list(Path(td).glob("*.json")), "receipts must not land flat in --kvitto-dir")
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
            files = sorted((Path(td) / "ram-prevent").glob("*.json"))
            self.assertFalse(list(Path(td).glob("*.json")), "receipts must not land flat in --kvitto-dir")
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
            files = list((Path(td) / "ram-rail").glob("*.json"))
            self.assertTrue(files, "CLI --kvitto-dir must write receipts under recipe subdir")
            self.assertFalse(list(Path(td).glob("*.json")), "CLI must not write flat H1-OFF-01 names")
            doc = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(verify(doc), [])
            self.assertIn(doc["knockback"]["point"], {"K1", "K2", "K3", "K4", "K5", "K6"})

    def test_shared_kvitto_dir_does_not_clobber_other_recipe(self):
        """Prevent must not overwrite rail heldout JSON/JSONL in the same --kvitto-dir."""
        import tempfile

        marker = '{"recipe":"ram-rail-v2","keep":true}\n'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rail_json, rail_jsonl = recipe_kvitto_paths(root, "ram-rail-v2", "H1-OFF-01")
            rail_json.parent.mkdir(parents=True, exist_ok=True)
            rail_json.write_text(marker, encoding="utf-8")
            rail_jsonl.write_text("rail-raw\n", encoding="utf-8")

            fx = HERE / "recept" / "ram-prevent.json"
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
                kvitto_dir=root,
                demo_file="qw/demos/ram-prevent.mvd",
                binaries={"qwprogs_sha256": "ab" * 32, "mvdsv_sha256": "cd" * 32},
                fixture_sha256=sha256_file(fx),
            )
            report = runner.run()
            self.assertTrue(report.as_dict()["godkand"], report.as_dict())

            self.assertEqual(rail_json.read_text(encoding="utf-8"), marker)
            self.assertEqual(rail_jsonl.read_text(encoding="utf-8"), "rail-raw\n")
            prev_json, _ = recipe_kvitto_paths(root, "ram-prevent", "H1-OFF-01")
            self.assertTrue(prev_json.is_file(), prev_json)
            self.assertNotEqual(prev_json, rail_json)
            prev_doc = json.loads(prev_json.read_text(encoding="utf-8"))
            self.assertEqual(prev_doc["candidate"], "ram-prevent")
            self.assertFalse((root / "H1-OFF-01.json").exists())

            with self.assertRaises(FileExistsError):
                runner._write_attempt_kvitto(report.attempts[0], {})
            from d_kvitto import write_exclusive

            with self.assertRaises(FileExistsError):
                write_exclusive(rail_jsonl, "clobber\n")
            self.assertEqual(rail_json.read_text(encoding="utf-8"), marker)
            self.assertEqual(rail_jsonl.read_text(encoding="utf-8"), "rail-raw\n")


class RamR3ZoneTests(unittest.TestCase):
    def test_gates_knockback_zone_is_union_698_704(self):
        for gates in (_rail_gates(), _rail_v2_gates()):
            zone = knockback_zone(gates)
            cells = list(zone["cells"])
            self.assertEqual(cells, [698, 699, 700, 701, 702, 703, 704], cells)
            self.assertEqual(len(cells), 7)
            self.assertIn(701, cells)
            u = zone["union"]
            self.assertEqual(u["x"], -288.0)
            self.assertGreaterEqual(u["x_tol"], 32.0)
            self.assertLessEqual(u["y_lo"], -800.0)
            self.assertGreaterEqual(u["y_hi"], -608.0)

    def test_k3_k4_k6_live_land_hit_and_idle(self):
        zone = knockback_zone(_rail_v2_gates())
        want_cell = {
            "K3-ON-01": 699,
            "K3-ON-02": 699,
            "K4-ON-01": 704,
            "K6-ON-01": 703,
        }
        for stem, cell in want_cell.items():
            raw = raw_from_jsonl(_live_jsonl(stem))
            scored = score_knockback_raw(raw, zone, 2.0)
            self.assertTrue(scored["land_hit"], (stem, scored))
            self.assertTrue(scored["post_recovery_idle"], (stem, scored))
            self.assertFalse(scored["stall"], (stem, scored))
            self.assertEqual(scored["landing_cell"], cell, stem)
            self.assertIn(scored["landing_cell"], EAST_FLOOR_CELLS)

    def test_stall_before_land_is_still_fail(self):
        zone = knockback_zone(_rail_v2_gates())
        raw = {
            "events": [
                {
                    "ev": "bot_stall",
                    "t": 0.2,
                    "cell": 5979,
                    "origin": [-360.0, -720.0, 128.03125],
                    "speed": 0.0,
                }
            ],
            "samples": [],
            "t_stall_gate": 0.2,
        }
        scored = score_knockback_raw(raw, zone, 2.0)
        self.assertFalse(scored["land_hit"], scored)
        self.assertTrue(scored["stall"], scored)
        self.assertFalse(scored["post_recovery_idle"], scored)


class RamR3AttributionTests(unittest.TestCase):
    def _h2_pair(self):
        off_raw = _inject_astar(raw_from_jsonl(_live_jsonl("H2-OFF-04")), _live_astar("H2-OFF-04"))
        on_raw = _inject_astar(raw_from_jsonl(_live_jsonl("H2-ON-04")), _live_astar("H2-ON-04"))
        return off_raw, on_raw

    def test_h2_off_pattern_emits_hazard_post(self):
        off_raw, on_raw = self._h2_pair()
        gates = _prevent_gates()
        result = attribute_pair(
            off_raw,
            on_raw,
            stratum="H2",
            off_id="H2-OFF-04",
            on_id="H2-ON-04",
            recipe=load_recipe(HERE / "recept" / "ram-prevent.json"),
            avsett=gates["avsett_drop"],
            clusters=gates["baseline_clusters"],
            off_receipt=_live_astar("H2-OFF-04"),
            on_receipt=_live_astar("H2-ON-04"),
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(all(result["legs"].values()), result["legs"])
        self.assertEqual(result["post"]["kind"], "preexisting_hazard")
        self.assertEqual(result["post"]["off_attempt"], "H2-OFF-04")
        self.assertEqual(result["post"]["on_attempt"], "H2-ON-04")
        self.assertIn("bot_stall", result["post"]["event_signature"])

    def test_h2_off_pattern_scorer_counts(self):
        off_raw, on_raw = self._h2_pair()

        def kb(**k):
            return _kb(**k)

        def trial(**k):
            sid = k.get("stratum_id")
            arm = k.get("arm")
            if sid == "H2":
                return off_raw if arm == "off" else on_raw
            return _trial(**k)

        runner = RamRunner(
            recipe=load_recipe(HERE / "recept" / "ram-prevent.json"),
            rail_gates=_rail_gates(),
            prevent_gates=_prevent_gates(),
            exec_knockback=kb,
            exec_trial=trial,
            ctl_port=0,
            game_port=27595,
            n_knock=0,
            n_chain=1,
        )
        report = runner.run()
        d = report.as_dict()
        held = d["heldout"]
        self.assertGreaterEqual(held["attempted"], 1)
        self.assertGreaterEqual(held["preexisting_hazards"], 1, d)
        self.assertEqual(held["non_attributed_failures"], 0, held)
        self.assertTrue(held["on_ok"], held)
        posts = d["preexisting_hazard_posts"]
        self.assertTrue(posts, "H2-OFF pattern must emit a preexisting_hazard post")
        self.assertTrue(any(p.get("kind") == "preexisting_hazard" and p.get("stratum") == "H2" for p in posts), posts)
        self.assertIn("attempted", d["prevention"])
        self.assertIn("eligible", d["prevention"])
        self.assertIn("preexisting_hazards", d["prevention"])
        self.assertIn("non_attributed_failures", d["prevention"])

    def test_missing_m1_stays_failure(self):
        off_raw, on_raw = self._h2_pair()
        gates = _prevent_gates()
        result = attribute_pair(
            off_raw,
            on_raw,
            stratum="H2",
            off_id="H2-OFF-04",
            on_id="H2-ON-04",
            recipe=load_recipe(HERE / "recept" / "ram-prevent.json"),
            avsett=gates["avsett_drop"],
            clusters=[],
            off_receipt=_live_astar("H2-OFF-04"),
            on_receipt=_live_astar("H2-ON-04"),
        )
        self.assertFalse(result["ok"], result)
        self.assertFalse(result["legs"]["m1"])
        self.assertEqual(result["post"]["kind"], "preexisting_hazard")
        self.assertFalse(result["post"]["attributed"])


if __name__ == "__main__":
    unittest.main()
