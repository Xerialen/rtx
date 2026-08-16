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
    raw_from_jsonl,
    reproduction_routes,
    score_side_by_side,
    selected_traversed_shelf_from_1416,
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

    def test_next_best_ok_is_per_obligation(self):
        shelf_1416 = {"start_cell": 1416, "kind": "heldout", "id": "1416-1461"}
        must_1158 = {"start_cell": 1158, "kind": "heldout", "id": "1158-1461"}
        floor_796 = {"start_cell": 1416, "kind": "heldout", "id": "1416-796"}
        # 10446 on next-best only when selected left 1416 via 10447.
        self.assertTrue(next_best_ok(
            [10446, 10767], "haz1462-k1",
            spec=shelf_1416, selected=[10447], mask=[10447],
        ))
        self.assertFalse(next_best_ok(
            [10441, 10084], "haz1462-k1",
            spec=shelf_1416, selected=[10447], mask=[10447],
        ))
        # 10446 was selected (10447 gone) — actual next-best is 10445, not 10446 again.
        self.assertTrue(next_best_ok(
            [10445, 10751], "haz1462-k1",
            spec=shelf_1416, selected=[10446, 10767], mask=[10446, 10767],
        ))
        # must-starter: 1416-link structurally impossible.
        self.assertFalse(selected_traversed_shelf_from_1416(must_1158, [8560, 8796]))
        self.assertTrue(next_best_ok(
            [8560, 8796], "haz1462-k1",
            spec=must_1158, selected=[8559, 8773], mask=[8559, 8773],
        ))
        # floor dest from 1416 via 10441 — not the 10446 shelf.
        self.assertFalse(selected_traversed_shelf_from_1416(floor_796, [10441, 10076]))
        self.assertTrue(next_best_ok(
            [10440, 10059], "haz1462-k1",
            spec=floor_796, selected=[10441, 10076], mask=[10441, 10076],
        ))
        self.assertFalse(next_best_ok([], "haz1462-k1", spec=shelf_1416, selected=[10447]))
        self.assertFalse(next_best_ok([FLOOR_DROP, 10453], "haz1462-k1", spec=shelf_1416, selected=[10447]))
        self.assertFalse(next_best_ok(
            [10446], "haz1462-k1",
            spec=shelf_1416, selected=[10447], mask=[10447, 34419],
        ))
        self.assertIn(10440, K2_NEXT_HIGH_FIRST)
        self.assertIn("10446", next_best_fail_reason(
            [10441, 10084], "haz1462-k1",
            spec=shelf_1416, selected=[10447], mask=[10447],
        ))

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
        # facit r2: 12 dests + 4 must-starters + separate 1461-1124 = 17
        self.assertEqual(len(held), 17)
        sj = [h for h in held if h.get("require_speedjump")]
        self.assertEqual(len(sj), 1)
        self.assertEqual(sj[0]["id"], "1461-1124")
        self.assertEqual(sj[0]["speedjump"], SPEEDJUMP)
        h1124 = next(h for h in held if h["id"] == "1416-1124")
        self.assertFalse(h1124["require_speedjump"])
        self.assertTrue(h1124["require_corridor"])
        self.assertEqual(sorted(h1124["avsett_drop_cells"]), [1090, 1122, 1123])

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
                # facit r2: the ACTUAL route is the drop corridor, attested
                # by peak_drop_150 inside the pre-registered cells.
                raw["astar_after"] = {
                    "found": True, "cells": [1416, 1122, 1124],
                    "links": [9001, 9002], "cost": 1.4, "mask_links": [],
                }
                raw["events"] = list(raw.get("events") or []) + [
                    {"ev": "peak_drop_150", "cell": 1122, "origin": [64.0, -844.0, 60.0]},
                ]
            if sid == "1461-1124":
                raw["astar_after"] = {
                    "found": True, "cells": [1461, 1124],
                    "links": [SPEEDJUMP, 9003], "cost": 2.0, "mask_links": [],
                }
            if arm == "on":
                sel = list((raw.get("astar_after") or {}).get("links") or [])
                if cid == "haz1462-k2":
                    raw["astar_next_best"] = {
                        "found": True, "cells": [1416, 1461],
                        "links": [10441, 10084], "cost": 3.0, "mask_links": sel,
                    }
                else:
                    raw["astar_next_best"] = {
                        "found": True, "cells": [1416, 1459],
                        "links": [10446, 10768], "cost": 3.0, "mask_links": sel,
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

    def test_1461_1124_without_34419_is_invalid(self):
        # facit r2: the SpeedJump attest lives on 1461-1124, not 1416-1124.
        def exec_trial(**k):
            if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                return _hearth_raw()
            raw = _clean_on()
            if k["stratum_id"] == "1416-1124":
                raw["events"] = list(raw.get("events") or []) + [
                    {"ev": "peak_drop_150", "cell": 1122, "origin": [64.0, -844.0, 60.0]},
                ]
            if k["stratum_id"] == "1461-1124":
                raw["astar_after"] = {
                    "found": True, "cells": [1461, 1124],
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
        bad = [a for a in report.attempts if not a.valid and "34419" in a.reason]
        self.assertTrue(bad)
        self.assertTrue(all(a.route_id == "1461-1124" for a in bad))

    def test_1416_1124_drop_route_passes_and_outside_corridor_fails(self):
        # facit r2 live-shaped: the K1-R3B raw evidence (drop corridor
        # 1122/1090/1123, arrive ~1.4 s) must PASS; a >150-drop outside the
        # pre-registered corridor must FAIL the corridor attest.
        def make_exec(outside: bool):
            def exec_trial(**k):
                if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                    return _hearth_raw()
                raw = _clean_on()
                if k["stratum_id"] == "1416-1124":
                    cell = 671 if outside else 1122
                    raw["events"] = list(raw.get("events") or []) + [
                        {"ev": "peak_drop_150", "cell": cell,
                         "origin": [64.0, -844.0, 60.0]},
                    ]
                if k["stratum_id"] == "1461-1124":
                    raw["astar_after"] = {
                        "found": True, "cells": [1461, 1124],
                        "links": [SPEEDJUMP, 9003], "cost": 2.0, "mask_links": [],
                    }
                return raw
            return exec_trial

        def run_with(outside: bool):
            runner = TournamentRunner(
                recipe=_recipe(),
                gates=_gates(),
                exec_trial=make_exec(outside),
                ctl_port=0,
                game_port=27592,
                fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
                n_repro=1,
                n_heldout=1,
            )
            runner.app_routes = []
            return runner.run()

        ok_report = run_with(outside=False)
        r1124 = [a for a in ok_report.attempts if a.route_id == "1416-1124"]
        self.assertTrue(all(a.valid for a in r1124), [a.reason for a in r1124])
        bad_report = run_with(outside=True)
        bad = [a for a in bad_report.attempts
               if a.route_id == "1416-1124" and not a.valid]
        self.assertTrue(bad)
        self.assertIn("avsett_drop", bad[0].reason)

    def test_corridor_goal_cell_drop_and_unstamped_drop_fail(self):
        # deepseek FYND 1a/1b: a >150-drop in the GOAL cell (1124) or an
        # UNSTAMPED drop (cell=None) is "every other drop" — fail-closed.
        def make_exec(extra_drop: dict):
            def exec_trial(**k):
                if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                    return _hearth_raw()
                raw = _clean_on()
                if k["stratum_id"] == "1416-1124":
                    raw["events"] = list(raw.get("events") or []) + [
                        {"ev": "peak_drop_150", "cell": 1122,
                         "origin": [64.0, -844.0, 60.0]},
                        dict(extra_drop),
                    ]
                if k["stratum_id"] == "1461-1124":
                    raw["astar_after"] = {
                        "found": True, "cells": [1461, 1124],
                        "links": [SPEEDJUMP, 9003], "cost": 2.0, "mask_links": [],
                    }
                return raw
            return exec_trial

        for extra in (
            {"ev": "peak_drop_150", "cell": 1124, "origin": [64.0, -800.0, 40.0]},
            {"ev": "peak_drop_150", "cell": None, "origin": [64.0, -820.0, 40.0]},
        ):
            runner = TournamentRunner(
                recipe=_recipe(),
                gates=_gates(),
                exec_trial=make_exec(extra),
                ctl_port=0,
                game_port=27592,
                fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
                n_repro=1,
                n_heldout=1,
            )
            runner.app_routes = []
            report = runner.run()
            bad = [a for a in report.attempts
                   if a.route_id == "1416-1124" and not a.valid]
            self.assertTrue(bad, f"extra={extra}")
            self.assertIn("avsett_drop", bad[0].reason)

    def test_1461_1124_off_arm_also_requires_34419(self):
        # deepseek FYND 2: facit r2 says "both arms" — an OFF substitute
        # path on 1461-1124 must fail too.
        def exec_trial(**k):
            if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                return _hearth_raw()
            raw = _clean_on()
            if k["stratum_id"] == "1416-1124":
                raw["events"] = list(raw.get("events") or []) + [
                    {"ev": "peak_drop_150", "cell": 1122, "origin": [64.0, -844.0, 60.0]},
                ]
            if k["stratum_id"] == "1461-1124":
                if k["arm"] == "off":
                    raw["astar_after"] = {
                        "found": True, "cells": [1461, 1124],
                        "links": [1, 2], "cost": 1.0, "mask_links": [],
                    }
                else:
                    raw["astar_after"] = {
                        "found": True, "cells": [1461, 1124],
                        "links": [SPEEDJUMP, 9003], "cost": 2.0, "mask_links": [],
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
        bad = [a for a in report.attempts
               if a.route_id == "1461-1124" and a.arm == "off" and not a.valid]
        self.assertTrue(bad)
        self.assertIn("34419", bad[0].reason)

    def test_campaign_off_hearth_survives_one_zero_route(self):
        # facit r2: >=1 OFF hearth across the COMBINED campaign; a single
        # stochastically zeroed route must not fail reproduction.
        def exec_trial(**k):
            if k["arm"] == "off" and k["stratum_id"] == "in_tunnel":
                return _hearth_raw()
            raw = _clean_on()
            if k["stratum_id"] == "1416-1124":
                raw["events"] = list(raw.get("events") or []) + [
                    {"ev": "peak_drop_150", "cell": 1122, "origin": [64.0, -844.0, 60.0]},
                ]
            if k["stratum_id"] == "1461-1124":
                raw["astar_after"] = {
                    "found": True, "cells": [1461, 1124],
                    "links": [SPEEDJUMP, 9003], "cost": 2.0, "mask_links": [],
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
        self.assertTrue(report.repro_off_ok)

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

    def test_live_jsonl_off41_off45_are_hearth(self):
        """Bug 2: K1 in_vast-OFF-41/45 (cell=1462, t≈8.3) must be hearth=True."""
        hearth = _gates()["reproduction"]["hearth"]
        live_dir = HERE / "testdata" / "turnering-k1"
        for name in ("in_vast-OFF-41.jsonl", "in_vast-OFF-45.jsonl"):
            raw = raw_from_jsonl(live_dir / name)
            drops = [e for e in raw["events"] if e.get("ev") == "peak_drop_150"]
            self.assertEqual(drops[0]["cell"], 1462, name)
            self.assertGreater(drops[0]["t"], 8.2)
            self.assertLess(drops[0]["t"], 8.4)
            # mid-air z (~105) is >15 u from centroid z=-16 — 3D dist must not veto.
            self.assertGreater(drops[0]["origin"][2], 100.0)
            self.assertTrue(hearth_hit(raw, hearth), name)
            self.assertEqual(raw["landing_cell"], 1462)

    def test_live_astar_next_best_per_obligation(self):
        """Bug 1: live K1 kvitton — 10446 only when 1416 shelf was selected."""
        live_dir = HERE / "testdata" / "turnering-k1"

        def load(name):
            return json.loads((live_dir / name).read_text(encoding="utf-8"))

        shelf = load("1416-1461-ON-01.json")
        must = load("1158-1461-ON-01.json")
        floor = load("1416-796-ON-01.json")
        from_1035 = load("1035-1461-ON-01.json")

        def ok(doc, start_cell, oid):
            after = doc["astar"]["after"]
            nb = doc["astar"]["next_best"]
            spec = {"start_cell": start_cell, "kind": "heldout", "id": oid}
            return next_best_ok(
                list(nb["links"]), "haz1462-k1",
                spec=spec, selected=list(after["links"]), mask=list(nb["mask_links"]),
            )

        self.assertEqual(shelf["astar"]["after"]["links"][:1], [10446])
        self.assertEqual(shelf["astar"]["next_best"]["links"][:1], [10445])
        self.assertTrue(ok(shelf, 1416, "1416-1461"))
        self.assertTrue(ok(must, 1158, "1158-1461"))
        self.assertTrue(ok(floor, 1416, "1416-796"))
        self.assertTrue(ok(from_1035, 1035, "1035-1461"))
        # 10444 substitute still forbidden on a 1416-shelf obligation.
        self.assertFalse(next_best_ok(
            [FLOOR_DROP, 10453], "haz1462-k1",
            spec={"start_cell": 1416, "kind": "heldout", "id": "1416-1461"},
            selected=list(shelf["astar"]["after"]["links"]),
            mask=list(shelf["astar"]["after"]["links"]),
        ))

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
            if k["stratum_id"] == "1416-1124" and k["arm"] == "on":
                # facit r2: drop-corridor route, corridor attested
                raw["events"] = list(raw.get("events") or []) + [
                    {"ev": "peak_drop_150", "cell": 1122, "origin": [64.0, -844.0, 60.0]},
                ]
            if k["stratum_id"] == "1461-1124":
                raw["astar_after"] = {
                    "found": True, "cells": [1461, 1124],
                    "links": [SPEEDJUMP, 9003], "cost": 2.0, "mask_links": [],
                }
            sel = list((raw.get("astar_after") or {}).get("links") or [])
            raw["astar_next_best"] = {
                "found": True, "cells": [1416, 1461],
                "links": [10441, 10084], "cost": 2.0, "mask_links": sel,
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
