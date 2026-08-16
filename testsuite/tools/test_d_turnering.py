#!/usr/bin/env python3
"""Mock tests for the HAZ-1462 tournament runner. No rig."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from d_recipe import load_recipe, on_expected  # noqa: E402
from d_turnering import (  # noqa: E402
    CANDIDATES,
    FLOOR_DROP,
    K2_NEXT_HIGH_FIRST,
    NEXT_HIGH,
    SPEEDJUMP,
    TournamentRunner,
    appendix_obligations,
    attests_speedjump,
    file_sha256,
    hearth_hit,
    heldout_obligations,
    next_best_fail_reason,
    next_best_is_floor_drop,
    next_best_ok,
    reproduction_routes,
    score_side_by_side,
)


def _gates():
    return json.loads((HERE / "recept" / "haz1462-gates.json").read_text(encoding="utf-8"))


def _recipe(cid="haz1462-k1"):
    return load_recipe(HERE / "recept" / f"{cid}.json")


def _hearth_raw(**extra):
    raw = {
        "vel": [0.0, 0.0, 0.0],
        "measured_vel": [0.0, 0.0, 0.0],
        "stamp_ok": True,
        "t_arrive": 8.0,
        "t_stall_gate": None,
        "events": [{
            "ev": "peak_drop_150",
            "origin": [317.64, -758.40, -15.97],
            "cell": 1462,
        }],
        "samples": [],
        "landing_cell": 1462,
        "astar_after": {"found": True, "cells": [1416, 1461], "links": [10447], "cost": 1.0, "mask_links": []},
        "astar_next_best": {"found": True, "cells": [1416, 1459, 1461], "links": [10446, 10768], "cost": 2.0, "mask_links": [10447]},
    }
    raw.update(extra)
    return raw


def _clean_on(**extra):
    raw = _hearth_raw()
    raw["events"] = [{"ev": "arrived", "t": 8.0}]
    raw["landing_cell"] = 1372
    raw["t_arrive"] = 8.0
    raw.update(extra)
    return raw


class PredicateTests(unittest.TestCase):
    def test_hearth_requires_1462_and_15u(self):
        hearth = _gates()["reproduction"]["hearth"]
        self.assertTrue(hearth_hit(_hearth_raw(), hearth))
        far = _hearth_raw(events=[{"ev": "peak_drop_150", "origin": [0.0, 0.0, 0.0], "cell": 1462}])
        self.assertFalse(hearth_hit(far, hearth))
        wrong_cell = _hearth_raw(events=[{"ev": "peak_drop_150", "origin": [317.64, -758.40, -15.97], "cell": 1418}])
        wrong_cell["landing_cell"] = 1418
        self.assertFalse(hearth_hit(wrong_cell, hearth))

    def test_next_best_rejects_floor_drop(self):
        self.assertTrue(next_best_is_floor_drop([FLOOR_DROP, 10453]))
        self.assertFalse(next_best_is_floor_drop([10446, 10768]))
        self.assertFalse(next_best_is_floor_drop([]))

    def test_next_best_ok_is_candidate_aware(self):
        high = list(NEXT_HIGH)
        hop = [10441, 10084]
        self.assertTrue(next_best_ok(high, "haz1462-k1"))
        self.assertTrue(next_best_ok(high, "haz1462-k3"))
        self.assertFalse(next_best_ok(high, "haz1462-k2"))
        self.assertTrue(next_best_ok(hop, "haz1462-k2"))
        self.assertIn(hop[0], K2_NEXT_HIGH_FIRST)
        self.assertFalse(next_best_ok(hop, "haz1462-k1"))
        self.assertFalse(next_best_ok([], "haz1462-k1"))
        self.assertFalse(next_best_ok([FLOOR_DROP, 10453], "haz1462-k1"))
        self.assertFalse(next_best_ok([FLOOR_DROP], "haz1462-k2"))
        self.assertIn("10446", next_best_fail_reason(hop, "haz1462-k1"))

    def test_speedjump_attested(self):
        self.assertTrue(attests_speedjump([10446, SPEEDJUMP, 1]))
        self.assertFalse(attests_speedjump([10446, 10768]))


class LoaderTests(unittest.TestCase):
    def test_gates_yield_2_repro_and_16_heldout(self):
        g = _gates()
        repro = reproduction_routes(g)
        self.assertEqual([r["id"] for r in repro], ["in_vast", "in_tunnel"])
        self.assertEqual(repro[0]["budget_s"], 25.0)
        self.assertEqual(repro[1]["budget_s"], 23.0)
        held = heldout_obligations(g)
        self.assertEqual(len(held), 16)
        sj = [h for h in held if h.get("require_speedjump")]
        self.assertEqual(len(sj), 1)
        self.assertEqual(sj[0]["id"], "1416-1124")
        self.assertEqual(sj[0]["speedjump"], SPEEDJUMP)

    def test_k2_appendix_is_49(self):
        app = json.loads((HERE / "recept" / "haz1462-k2-appendix.json").read_text())
        rows = appendix_obligations(app, [288.0, -844.0, 264.0])
        self.assertEqual(len(rows), 49)


class ScoringTests(unittest.TestCase):
    def _perfect(self, cid="haz1462-k1", n_repro=1, n_heldout=1, n_app=0):
        calls = []

        def exec_trial(**k):
            calls.append(k)
            arm = k["arm"]
            sid = k["stratum_id"]
            if arm == "off" and sid in {"in_vast", "in_tunnel"}:
                return _hearth_raw()
            raw = _clean_on()
            if sid == "1416-1124" and arm == "on":
                raw["astar_after"] = {
                    "found": True, "cells": [1416, 1461, 1124],
                    "links": [10447, SPEEDJUMP], "cost": 2.0, "mask_links": [],
                }
            if arm == "on":
                if cid == "haz1462-k2":
                    raw["astar_next_best"] = {
                        "found": True, "cells": [1416, 1461],
                        "links": [10441, 10084], "cost": 3.0, "mask_links": [10447, 10446],
                    }
                else:
                    raw["astar_next_best"] = {
                        "found": True, "cells": [1416, 1459],
                        "links": [10446, 10768], "cost": 3.0, "mask_links": [10447],
                    }
            return raw

        rec = _recipe(cid)
        app = None
        if cid == "haz1462-k2":
            app = json.loads((HERE / "recept" / "haz1462-k2-appendix.json").read_text())
        runner = TournamentRunner(
            recipe=rec,
            gates=_gates(),
            appendix=app,
            exec_trial=exec_trial,
            ctl_port=0,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / f"{cid}.json"),
            n_repro=n_repro,
            n_heldout=n_heldout,
        )
        if n_app == 0:
            runner.app_routes = []
        return runner.run(), calls

    def test_perfect_k1_is_green(self):
        report, _ = self._perfect("haz1462-k1")
        d = report.as_dict()
        self.assertTrue(d["valid"], d["invalid_reasons"])
        self.assertTrue(d["reproduction"]["off_ok"])
        self.assertTrue(d["reproduction"]["on_ok"])
        self.assertTrue(d["heldout"]["on_ok"])
        self.assertTrue(d["godkand"])
        self.assertEqual(d["candidate"], "haz1462-k1")
        self.assertEqual(len(d["fixture_sha256"]), 64)

    def test_off_without_hearth_fails_reproduction(self):
        def exec_trial(**k):
            return _clean_on()

        runner = TournamentRunner(
            recipe=_recipe(),
            gates=_gates(),
            exec_trial=exec_trial,
            ctl_port=0,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
            n_repro=1,
            n_heldout=1,
        )
        runner.app_routes = []
        report = runner.run()
        self.assertFalse(report.repro_off_ok)
        self.assertFalse(report.as_dict()["godkand"])

    def test_on_hearth_fails_reproduction(self):
        def exec_trial(**k):
            return _hearth_raw()

        runner = TournamentRunner(
            recipe=_recipe(),
            gates=_gates(),
            exec_trial=exec_trial,
            ctl_port=0,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
            n_repro=1,
            n_heldout=0,
        )
        runner.app_routes = []
        report = runner.run()
        self.assertTrue(report.repro_off_ok)
        self.assertFalse(report.repro_on_ok)

    def test_floor_drop_next_best_is_invalid(self):
        def exec_trial(**k):
            if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                return _hearth_raw()
            raw = _clean_on()
            raw["astar_next_best"] = {
                "found": True, "cells": [1416, 1417],
                "links": [FLOOR_DROP, 10453], "cost": 2.0, "mask_links": [10447],
            }
            return raw

        runner = TournamentRunner(
            recipe=_recipe(),
            gates=_gates(),
            exec_trial=exec_trial,
            ctl_port=0,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
            n_repro=1,
            n_heldout=1,
        )
        runner.app_routes = []
        report = runner.run()
        self.assertFalse(report.heldout_on_ok)
        self.assertTrue(any("10444" in a.reason for a in report.attempts if not a.valid))

    def test_1124_without_34419_is_invalid(self):
        def exec_trial(**k):
            if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                return _hearth_raw()
            raw = _clean_on()
            if k["stratum_id"] == "1416-1124":
                raw["astar_after"] = {
                    "found": True, "cells": [1416, 1124],
                    "links": [1, 2], "cost": 1.0, "mask_links": [],
                }
            return raw

        runner = TournamentRunner(
            recipe=_recipe(),
            gates=_gates(),
            exec_trial=exec_trial,
            ctl_port=0,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
            n_repro=1,
            n_heldout=1,
        )
        runner.app_routes = []
        report = runner.run()
        self.assertTrue(any("34419" in a.reason for a in report.attempts if not a.valid))

    def test_side_by_side_does_not_borrow(self):
        ok, _ = self._perfect("haz1462-k1")
        bad, _ = self._perfect("haz1462-k2")
        bad.pass_ok = False
        bad.repro_on_ok = False
        table = score_side_by_side({"haz1462-k1": ok, "haz1462-k2": bad})
        self.assertTrue(table["candidates"]["haz1462-k1"]["godkand"])
        self.assertFalse(table["candidates"]["haz1462-k2"]["godkand"])
        self.assertEqual(table["winner"], "haz1462-k1")
        self.assertNotEqual(
            table["candidates"]["haz1462-k1"]["fixture_sha256"],
            table["candidates"]["haz1462-k2"]["fixture_sha256"],
        )

    def test_ra_preflight(self):
        runner = TournamentRunner(
            recipe=_recipe(),
            gates=_gates(),
            exec_trial=lambda **k: _clean_on(),
            ctl_port=27990,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
        )
        why = runner.preflight()
        self.assertTrue(any("RA/main" in w for w in why), why)

    def test_k2_preflight_requires_49(self):
        runner = TournamentRunner(
            recipe=_recipe("haz1462-k2"),
            gates=_gates(),
            appendix=None,
            exec_trial=lambda **k: _clean_on(),
            ctl_port=0,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k2.json"),
        )
        why = runner.preflight()
        self.assertTrue(any("appendix" in w for w in why), why)

    def test_on_expected_is_pinned(self):
        for cid in CANDIDATES:
            on = on_expected(_recipe(cid))
            self.assertEqual(len(on["graph_content_hash"]), 64)

    def test_p3_live_shaped_on_hearth_fails_repro(self):
        """P3: ON peak_drop at 1462 with live cell must fail reproduction_on."""
        def exec_trial(**k):
            if k["arm"] == "off":
                return _hearth_raw()
            return _hearth_raw()  # live-shaped ON fall at 1462

        runner = TournamentRunner(
            recipe=_recipe(),
            gates=_gates(),
            exec_trial=exec_trial,
            ctl_port=0,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
            n_repro=1,
            n_heldout=0,
        )
        runner.app_routes = []
        report = runner.run()
        self.assertTrue(report.repro_off_ok)
        self.assertFalse(report.repro_on_ok)
        self.assertFalse(report.as_dict()["godkand"])

    def test_p4_k2_hop_fails_k1_next_best(self):
        """P4: [10441,10084] is K2 hop-SP, not K1's 10446→10768."""
        def exec_trial(**k):
            if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                return _hearth_raw()
            raw = _clean_on()
            raw["astar_next_best"] = {
                "found": True, "cells": [1416, 1461],
                "links": [10441, 10084], "cost": 2.0, "mask_links": [10447],
            }
            if k["stratum_id"] == "1416-1124" and k["arm"] == "on":
                raw["astar_after"] = {
                    "found": True, "cells": [1416, 1461, 1124],
                    "links": [10447, SPEEDJUMP], "cost": 2.0, "mask_links": [],
                }
            return raw

        k1 = TournamentRunner(
            recipe=_recipe("haz1462-k1"),
            gates=_gates(),
            exec_trial=exec_trial,
            ctl_port=0,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
            n_repro=1,
            n_heldout=1,
        )
        k1.app_routes = []
        r1 = k1.run()
        self.assertFalse(r1.heldout_on_ok)
        self.assertTrue(any("10446" in a.reason for a in r1.attempts if not a.valid), [a.reason for a in r1.attempts])

        k2 = TournamentRunner(
            recipe=_recipe("haz1462-k2"),
            gates=_gates(),
            appendix=json.loads((HERE / "recept" / "haz1462-k2-appendix.json").read_text()),
            exec_trial=exec_trial,
            ctl_port=0,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k2.json"),
            n_repro=1,
            n_heldout=1,
        )
        r2 = k2.run()
        self.assertTrue(r2.heldout_on_ok, [a.reason for a in r2.attempts if not a.valid])

    def test_empty_next_best_fails_heldout(self):
        def exec_trial(**k):
            if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                return _hearth_raw()
            raw = _clean_on()
            raw["astar_next_best"] = {"found": False, "cells": [], "links": [], "cost": None, "mask_links": []}
            return raw

        runner = TournamentRunner(
            recipe=_recipe(),
            gates=_gates(),
            exec_trial=exec_trial,
            ctl_port=0,
            game_port=27592,
            fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
            n_repro=1,
            n_heldout=1,
        )
        runner.app_routes = []
        report = runner.run()
        self.assertFalse(report.heldout_on_ok)
        self.assertTrue(any("empty" in a.reason for a in report.attempts if not a.valid))

    def test_p6_kvitto_dir_writes_per_attempt(self):
        import tempfile
        from verify_d_kvitto import verify

        with tempfile.TemporaryDirectory() as td:
            def exec_trial(**k):
                if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                    return _hearth_raw()
                return _clean_on()

            runner = TournamentRunner(
                recipe=_recipe(),
                gates=_gates(),
                exec_trial=exec_trial,
                ctl_port=0,
                game_port=27592,
                fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
                n_repro=1,
                n_heldout=0,
                kvitto_dir=Path(td),
                demo_file="qw/demos/e967900_20260816T160000Z.mvd",
                binaries={"qwprogs_sha256": "ab" * 32, "mvdsv_sha256": "cd" * 32},
            )
            runner.app_routes = []
            runner.run()
            files = sorted(Path(td).glob("*.json"))
            self.assertTrue(files, "kvitto-dir must receive per-attempt receipts")
            landings = []
            for path in files:
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(len(doc["fixture_sha256"]), 64)
                self.assertEqual(doc["candidate"], "haz1462-k1")
                self.assertEqual(doc["demo_file"], "qw/demos/e967900_20260816T160000Z.mvd")
                self.assertEqual(doc["binaries"]["qwprogs_sha256"], "ab" * 32)
                self.assertEqual(doc["binaries"]["mvdsv_sha256"], "cd" * 32)
                self.assertEqual(verify(doc), [])
                landings.append(doc.get("landing_cell"))
            self.assertTrue(any(c in {1462, 1372} for c in landings), landings)

    def test_p6_side_by_side_cli(self):
        import tempfile
        from d_turnering import main as turn_main

        ok, _ = self._perfect("haz1462-k1")
        bad, _ = self._perfect("haz1462-k2")
        bad.pass_ok = False
        bad.repro_on_ok = False
        with tempfile.TemporaryDirectory() as td:
            p1 = Path(td) / "k1.json"
            p2 = Path(td) / "k2.json"
            p1.write_text(json.dumps(ok.as_dict()), encoding="utf-8")
            p2.write_text(json.dumps(bad.as_dict()), encoding="utf-8")
            out = Path(td) / "table.json"
            rc = turn_main(["--side-by-side", str(p1), str(p2), "--out", str(out)])
            self.assertEqual(rc, 0)
            table = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(table["kind"], "haz1462-side-by-side")
            self.assertEqual(table["winner"], "haz1462-k1")
            self.assertTrue(table["candidates"]["haz1462-k1"]["godkand"])
            self.assertFalse(table["candidates"]["haz1462-k2"]["godkand"])


class KvittoFieldTests(unittest.TestCase):
    def test_make_kvitto_carries_fixture_sha_and_candidate(self):
        from d_kvitto import WEST_SHELF_OFF, WEST_SHELF_RECIPE, astar_path, make_kvitto
        from verify_d_kvitto import verify

        path = astar_path(found=True, cells=[1, 2], links=[3], cost=1.0)
        nb = astar_path(found=True, cells=[1, 3, 2], links=[4, 5], cost=2.0, mask_links=[3])
        doc = make_kvitto(
            riglock_owner="fable",
            riglock_issued_at="2026-08-16T08:00:00+00:00",
            riglock_valid_from="2026-08-16T08:00:00+00:00",
            riglock_valid_to="2026-08-16T12:00:00+00:00",
            riglock_path="/home/xerial/lab/.rig-lock",
            run_started_at="2026-08-16T08:05:00+00:00",
            run_ended_at="2026-08-16T08:20:00+00:00",
            endpoint_host="127.0.0.1",
            endpoint_ctl_port=27996,
            endpoint_game_port=27592,
            map_name="dm3",
            binary_sha256="ab" * 32,
            commit="e967900",
            stamps_off_expected=WEST_SHELF_OFF,
            stamps_off_observed=WEST_SHELF_OFF,
            stamps_on_expected={**WEST_SHELF_OFF, "links": 48206, "graph_stamp": "1", "graph_content_hash": "cd" * 32},
            stamps_on_observed={**WEST_SHELF_OFF, "links": 48206, "graph_stamp": "1", "graph_content_hash": "cd" * 32},
            stamps_undo_expected=WEST_SHELF_OFF,
            stamps_undo_observed=WEST_SHELF_OFF,
            recipe=WEST_SHELF_RECIPE,
            seed=0,
            stratum={"id": "in_vast", "attempt": "in_vast-OFF-01"},
            raw_pointer="/tmp/t.jsonl",
            astar_before=path,
            astar_after=path,
            astar_next_best=nb,
            demo_file="qw/demos/e967900_20260816T160000Z.mvd",
            fixture_sha256="ab" * 32,
            candidate="haz1462-k1",
            landing_cell=1462,
            selected_link=10447,
        )
        self.assertEqual(doc["fixture_sha256"], "ab" * 32)
        self.assertEqual(doc["candidate"], "haz1462-k1")
        self.assertEqual(doc["landing_cell"], 1462)
        self.assertEqual(doc["selected_link"], 10447)
        self.assertEqual(verify(doc), [])


if __name__ == "__main__":
    unittest.main()
