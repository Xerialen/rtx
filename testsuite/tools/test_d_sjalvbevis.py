#!/usr/bin/env python3
"""Fixture tests for d_sjalvbevis / d_strata (no rig)."""

from __future__ import annotations

import unittest

from d_kvitto import WEST_SHELF_OFF
from d_recipe import on_expected
import d_sjalvbevis as drill
from d_sjalvbevis import score_heldout, score_t0
from d_strata import (
    FallTracker,
    STRATA,
    direction_dot,
    is_trap,
    profiles_ok,
    stratum_ok,
)


def _recipe_with_on() -> dict:
    on = {
        **WEST_SHELF_OFF,
        "cells": 5981,
        "links": 48215,
        "graph_stamp": "9",
        "graph_content_hash": "11" * 32,
    }
    return {
        "id": "west-shelf",
        "map": "dm3",
        "taxonomy_class": "carve_origin",
        "evidence": "test",
        "off": dict(WEST_SHELF_OFF),
        "on_expected": on,
    }


def _gates(cells=None, stratum_at="gate") -> dict:
    doc = {
        "recipe": "west-shelf",
        "graph_stamp": WEST_SHELF_OFF["graph_stamp"],
        "graph_content_hash": WEST_SHELF_OFF["graph_content_hash"],
        "gates": {
            "west-shelf": {
                "cell_ids": cells if cells is not None else [9001, 9002],
                "aim_points": [[-865.0, -48.0, 90.0]],
                "tolerance_xy": 24.0,
                "tolerance_z": 16.0,
            }
        },
    }
    if stratum_at is not None:
        doc["heldout_stratum_at"] = stratum_at
    return doc


class StratumTests(unittest.TestCase):
    def test_t0_rest(self):
        ok, _ = stratum_ok("T0", [0, 0, 0], STRATA["T0"]["start"], STRATA["T0"]["goal"])
        self.assertTrue(ok)
        ok, why = stratum_ok("T0", [5, 0, 0], STRATA["T0"]["start"], STRATA["T0"]["goal"])
        self.assertFalse(ok)
        self.assertIn("rest", why)

    def test_a1_band_and_direction(self):
        start, goal = STRATA["A1"]["start"], STRATA["A1"]["goal"]
        # heading is west-ish (negative x)
        vel = [-120.0, 0.0, 0.0]
        self.assertGreaterEqual(direction_dot(vel, start, goal), 0.8)
        ok, _ = stratum_ok("A1", vel, start, goal)
        self.assertTrue(ok)
        ok, _ = stratum_ok("A1", [-200.0, 0, 0], start, goal)  # 200 is A2
        self.assertFalse(ok)
        ok, _ = stratum_ok("A1", [120.0, 0, 0], start, goal)  # opposite
        self.assertFalse(ok)

    def test_heldout_is_kedjad_not_teleport(self):
        self.assertEqual(STRATA["T0"]["population"], "teleport_drill")
        for sid in ("A1", "A2", "A3", "A4"):
            self.assertEqual(STRATA[sid]["population"], "kedjad")
            self.assertNotEqual(STRATA[sid]["start"], STRATA["T0"]["start"])

    def test_peak_drop(self):
        t = FallTracker()
        self.assertFalse(t.update(200.0, False))
        self.assertFalse(t.update(100.0, False))
        self.assertTrue(t.update(40.0, False))
        t2 = FallTracker()
        self.assertFalse(t2.update(200.0, True))
        self.assertFalse(t2.update(40.0, True))

    def test_trap_stall_then_arrived_still_trap(self):
        gate = _gates()["gates"]["west-shelf"]
        events = [
            {"ev": "bot_stall", "cell": 9001, "origin": [-865.0, -48.0, 90.0]},
            {"ev": "arrived"},
        ]
        self.assertTrue(is_trap(events, gate, arrived_after_stall=True))

    def test_timeout_is_not_trap(self):
        gate = _gates()["gates"]["west-shelf"]
        self.assertFalse(is_trap([{"ev": "timeout"}], gate, arrived_after_stall=False))

    def test_cvar_profile(self):
        self.assertIsNone(profiles_ok({"rtx_nav_patch": "0", "sv_gravity": "800"}, {"rtx_nav_patch": "1", "sv_gravity": "800"}))
        self.assertIn("sv_gravity", profiles_ok({"rtx_nav_patch": "0", "sv_gravity": "800"}, {"rtx_nav_patch": "1", "sv_gravity": "100"}))


class DrillTests(unittest.TestCase):
    def test_missing_on_expected_invalidates(self):
        recipe = _recipe_with_on()
        recipe["on_expected"] = None
        runner = drill.DrillRunner(
            recipe=recipe,
            gates=_gates(),
            exec_trial=lambda **k: {},
            ctl_port=27996,
            game_port=27591,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
        )
        rep = runner.run()
        self.assertFalse(rep.valid)
        self.assertTrue(any("ON expected" in r for r in rep.invalid_reasons))

    def test_missing_gate_cells_invalidates(self):
        runner = drill.DrillRunner(
            recipe=_recipe_with_on(),
            gates=_gates(cells=[]),
            exec_trial=lambda **k: {},
            ctl_port=27996,
            game_port=27591,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
        )
        rep = runner.run()
        self.assertFalse(rep.valid)
        self.assertTrue(any("cell_ids" in r for r in rep.invalid_reasons))

    def test_ra_port_invalidates(self):
        runner = drill.DrillRunner(
            recipe=_recipe_with_on(),
            gates=_gates(),
            exec_trial=lambda **k: {},
            ctl_port=27990,
            game_port=27540,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
        )
        self.assertIn("RA/main", runner.preflight()[0])

    def test_perfect_t0_and_heldout(self):
        def exec_trial(stratum_id, arm, spec, seq):
            if stratum_id == "T0":
                vel = [0, 0, 0]
                if arm == "off":
                    return {
                        "vel": vel,
                        "events": [{"ev": "bot_stall", "cell": 9001, "origin": [-865.0, -48.0, 90.0]}],
                        "samples": [{"z": 90.0, "on_ground": True}],
                    }
                return {
                    "vel": vel,
                    "events": [{"ev": "arrived"}],
                    "samples": [{"z": 90.0, "on_ground": True}, {"z": -16.0, "on_ground": True}],
                    "t_arrive": 0.4,
                }
            # heldout A*: heading toward goal
            start, goal = spec["start"], spec["goal"]
            mid = spec["vh_lo"] + 10
            sign = -1.0 if goal[0] < start[0] else 1.0
            vel = [sign * mid, 0.0, 0.0]
            extra = {
                "vel": vel,
                "gate_velocity": vel,
                "gate_cell": 9001,
                "gate_origin": [-865.0, -48.0, 90.0],
                "samples": [{"z": 0.0, "on_ground": True}],
            }
            if arm == "on":
                return {
                    **extra,
                    "events": [{"ev": "arrived"}],
                    "t_arrive": 1.0,
                }
            return {**extra, "events": []}

        runner = drill.DrillRunner(
            recipe=_recipe_with_on(),
            gates=_gates(),
            exec_trial=exec_trial,
            ctl_port=27996,
            game_port=27591,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
        )
        rep = runner.run()
        self.assertEqual(rep.invalid_reasons, [])
        self.assertTrue(rep.valid)
        self.assertTrue(score_t0(rep.attempts))
        self.assertTrue(score_heldout(rep.attempts))
        self.assertTrue(rep.as_dict()["godkand"])
        ids = [a.attempt_id for a in rep.attempts]
        self.assertTrue(any(i.startswith("T0-OFF-") for i in ids))
        self.assertTrue(any(i.startswith("A1-ON-") for i in ids))
        self.assertFalse(any(i.startswith("T0-") and "A1" in i for i in ids))

    def test_bad_stratum_not_counted(self):
        def exec_trial(stratum_id, arm, spec, seq):
            return {"vel": [0, 0, 0], "events": [], "samples": []}  # T0 ok, heldout |vh|=0 fail

        runner = drill.DrillRunner(
            recipe=_recipe_with_on(),
            gates=_gates(),
            exec_trial=exec_trial,
            ctl_port=27996,
            game_port=27591,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
        )
        rep = runner.run()
        self.assertFalse(rep.valid)
        self.assertFalse(score_heldout(rep.attempts))

    def test_on_expected_never_from_observed(self):
        rec = _recipe_with_on()
        pinned = dict(rec["on_expected"])
        rec["on_expected"]["cells"] = 1  # mutate fixture
        # caller must pass the fixture; drill refuses to rebuild from live
        with self.assertRaises(ValueError):
            on_expected({"off": WEST_SHELF_OFF, "on_expected": None})
        self.assertEqual(pinned["graph_stamp"], "9")


# keep the accidental alias unused
class BudgetTests(unittest.TestCase):
    def test_t0_late_arrived_is_not_a_hit(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="T0",
            arm="on",
            vel=[0, 0, 0],
            events=[{"ev": "arrived"}],
            samples=[{"z": -16.0, "on_ground": True}],
            gate=gate,
            t_arrive=2.0,
        )
        self.assertTrue(att.valid)
        self.assertFalse(att.arrived)
        self.assertIn("budget", att.reason)
        self.assertFalse(drill.score_t0([att]))

    def test_heldout_late_arrived_is_not_a_hit(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="A1",
            arm="on",
            vel=[-120.0, 0.0, 0.0],
            events=[{"ev": "arrived"}],
            samples=[{"z": 0.0, "on_ground": True}],
            gate=gate,
            t_arrive=30.0,
            gate_velocity=[-120.0, 0.0, 0.0],
            gate_cell=9001,
            stratum_at="gate",
        )
        self.assertTrue(att.valid)
        self.assertFalse(att.arrived)
        self.assertIn("budget", att.reason)

    def test_arrived_without_time_is_not_a_hit(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="T0",
            arm="on",
            vel=[0, 0, 0],
            events=[{"ev": "arrived"}],
            samples=[{"z": -16.0, "on_ground": True}],
            gate=gate,
        )
        self.assertFalse(att.arrived)
        self.assertIn("without time", att.reason)


class GateLocusTests(unittest.TestCase):
    def test_heldout_without_gate_vel_invalid_when_locus_gate(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="A1",
            arm="on",
            vel=[-120.0, 0.0, 0.0],
            events=[],
            samples=[{"z": 0.0, "on_ground": True}],
            gate=gate,
            gate_cell=9001,
            stratum_at="gate",
        )
        self.assertFalse(att.valid)
        self.assertIn("gate_velocity", att.reason)

    def test_heldout_start_vel_without_gate_hit_rejected(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="A1",
            arm="off",
            vel=[-120.0, 0.0, 0.0],
            events=[],
            samples=[{"z": 0.0, "on_ground": True}],
            gate=gate,
            gate_velocity=[-120.0, 0.0, 0.0],
            stratum_at="gate",
        )
        self.assertFalse(att.valid)
        self.assertIn("gate", att.reason)

    def test_heldout_valid_with_gate_vel_and_cell(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="A1",
            arm="on",
            vel=[0.0, 0.0, 0.0],
            events=[{"ev": "arrived"}],
            samples=[{"z": 0.0, "on_ground": True}],
            gate=gate,
            t_arrive=1.0,
            gate_velocity=[-120.0, 0.0, 0.0],
            gate_cell=9001,
            stratum_at="gate",
        )
        self.assertTrue(att.valid)
        self.assertTrue(att.arrived)

    def test_heldout_valid_with_aim_hit(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="A1",
            arm="on",
            vel=[0.0, 0.0, 0.0],
            events=[{"ev": "arrived"}],
            samples=[{"z": 0.0, "on_ground": True}],
            gate=gate,
            t_arrive=1.0,
            gate_velocity=[-120.0, 0.0, 0.0],
            gate_origin=[-865.0, -48.0, 90.0],
            stratum_at="gate",
        )
        self.assertTrue(att.valid)

    def test_heldout_start_locus_uses_start_vel(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="A1",
            arm="off",
            vel=[-120.0, 0.0, 0.0],
            events=[],
            samples=[{"z": 0.0, "on_ground": True}],
            gate=gate,
            stratum_at="start",
        )
        self.assertTrue(att.valid)

    def test_missing_stratum_at_preflight(self):
        runner = drill.DrillRunner(
            recipe=_recipe_with_on(),
            gates=_gates(stratum_at=None),
            exec_trial=lambda **k: {},
            ctl_port=27996,
            game_port=27591,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
        )
        reasons = runner.preflight()
        self.assertTrue(any("heldout_stratum_at" in r for r in reasons), reasons)

    def test_missing_stratum_at_classify_fail_closed(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="A1",
            arm="on",
            vel=[-120.0, 0.0, 0.0],
            events=[],
            samples=[],
            gate=gate,
        )
        self.assertFalse(att.valid)
        self.assertIn("heldout_stratum_at", att.reason)


class _AliasGuard(unittest.TestCase):
    def test_import_real_module(self):
        self.assertTrue(hasattr(drill, "DrillRunner"))


if __name__ == "__main__":
    unittest.main()
