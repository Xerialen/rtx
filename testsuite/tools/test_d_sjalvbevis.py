#!/usr/bin/env python3
"""Fixture tests for d_sjalvbevis / d_strata (no rig)."""

from __future__ import annotations

import unittest

from d_kvitto import WEST_SHELF_OFF
from d_recipe import load_avsett_drop, on_expected
import d_sjalvbevis as drill
from d_sjalvbevis import score_heldout, score_t0
from d_strata import (
    FallTracker,
    STRATA,
    direction_dot,
    in_avsett_geometry,
    is_trap,
    pair_start_vel_ok,
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


def _gates(cells=None, stratum_at="start") -> dict:
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
    doc["t0_budget_s"] = STRATA["T0"]["budget_s"]
    return doc


def _avsett() -> dict:
    return load_avsett_drop()


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
        ok, _ = stratum_ok("A1", [-200.0, 0, 0], start, goal)  # 200 is A2 (r2 160-260)
        self.assertFalse(ok)
        ok, _ = stratum_ok("A1", [-170.0, 0, 0], start, goal)  # 170 is A2 in r2
        self.assertFalse(ok)
        ok, _ = stratum_ok("A1", [120.0, 0, 0], start, goal)  # opposite
        self.assertFalse(ok)
        ok, _ = stratum_ok("A2", [-200.0, 0, 0], start, STRATA["A2"]["goal"])
        self.assertTrue(ok)

    def test_r2_shipped_locus_is_start(self):
        from d_recipe import load_gates
        from d_strata import heldout_stratum_at
        locus, err = heldout_stratum_at(load_gates())
        self.assertIsNone(err)
        self.assertEqual(locus, "start")
        self.assertEqual(STRATA["A1"]["vh_hi"], 160.0)
        self.assertEqual(STRATA["A2"]["vh_lo"], 160.0)
        self.assertEqual(STRATA["A2"]["vh_hi"], 260.0)

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

    def test_one_descent_is_one_episode(self):
        # R6: 4–5 rows at ~60 ms were one physical fall.
        t = FallTracker()
        self.assertFalse(t.update(360.0, False))
        zs = [200.0, 140.0, 80.0, 20.0, -10.0]
        fires = [z for z in zs if t.update(z, False)]
        self.assertEqual(fires, [200.0])
        self.assertAlmostEqual(t.episode_dz or 0.0, 370.0, places=5)
        self.assertTrue(t.in_episode)

    def test_two_separate_falls_are_two_episodes(self):
        t = FallTracker()
        self.assertFalse(t.update(360.0, False))
        self.assertTrue(t.update(200.0, False))
        self.assertFalse(t.update(40.0, False))
        self.assertFalse(t.update(-16.0, True))
        self.assertFalse(t.update(200.0, True))
        self.assertFalse(t.update(200.0, False))
        self.assertTrue(t.update(20.0, False))
        self.assertFalse(t.update(-10.0, False))

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

    def test_pair_start_vel_tol(self):
        ok, _ = pair_start_vel_ok([-120.0, 4.0, 0.0], [-119.0, 1.0, 3.0])
        self.assertTrue(ok)
        ok, why = pair_start_vel_ok([-120.0, 0.0, 0.0], [-114.0, 0.0, 0.0])
        self.assertFalse(ok)
        self.assertIn("vx", why)
        ok, why = pair_start_vel_ok([-120.0, 0.0, 0.0], None)
        self.assertFalse(ok)

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
        def exec_trial(stratum_id, arm, spec, seq, match_vel=None):
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
        def exec_trial(stratum_id, arm, spec, seq, match_vel=None):
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
    def test_t0_budget_is_1_10(self):
        self.assertAlmostEqual(STRATA["T0"]["budget_s"], 1.10)
        from d_recipe import load_gates
        self.assertAlmostEqual(load_gates()["t0_budget_s"], 1.10)

    def test_t0_arrived_at_0_95_is_a_hit(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="T0",
            arm="on",
            vel=[0, 0, 0],
            events=[{"ev": "arrived"}],
            samples=[{"z": -16.0, "on_ground": True}],
            gate=gate,
            t_arrive=0.95,
        )
        self.assertTrue(att.valid)
        self.assertTrue(att.arrived)

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

    def test_t0_arrived_just_over_1_10_is_not_a_hit(self):
        gate = _gates()["gates"]["west-shelf"]
        att = drill.classify_trial(
            stratum_id="T0",
            arm="on",
            vel=[0, 0, 0],
            events=[{"ev": "arrived"}],
            samples=[{"z": -16.0, "on_ground": True}],
            gate=gate,
            t_arrive=1.11,
        )
        self.assertTrue(att.valid)
        self.assertFalse(att.arrived)

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

    def test_pair_mismatch_is_replaced_not_counted(self):
        def exec_trial(stratum_id, arm, spec, seq, match_vel=None):
            if stratum_id == "T0":
                vel = [0, 0, 0]
                if arm == "off":
                    return {
                        "vel": vel,
                        "measured_vel": vel,
                        "events": [{"ev": "bot_stall", "cell": 9001, "origin": [-865.0, -48.0, 90.0]}],
                        "samples": [{"z": 90.0, "on_ground": True}],
                    }
                return {
                    "vel": vel,
                    "measured_vel": vel,
                    "events": [{"ev": "arrived"}],
                    "samples": [{"z": -16.0, "on_ground": True}],
                    "t_arrive": 0.4,
                }
            sign = -1.0 if spec["goal"][0] < spec["start"][0] else 1.0
            mid = spec["vh_lo"] + 10
            vel = [sign * mid, 0.0, 0.0]
            if arm == "on" and stratum_id == "A1":
                vel = [sign * mid - 8.0, 0.0, 0.0]  # > 5 u/s off
            extra = {
                "vel": vel,
                "measured_vel": vel,
                "samples": [{"z": 0.0, "on_ground": True}],
            }
            if arm == "on":
                return {**extra, "events": [{"ev": "arrived"}], "t_arrive": 1.0}
            return {**extra, "events": []}

        runner = drill.DrillRunner(
            recipe=_recipe_with_on(),
            gates=_gates(stratum_at="start"),
            exec_trial=exec_trial,
            ctl_port=27996,
            game_port=27591,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
        )
        rep = runner.run()
        a1 = [a for a in rep.attempts if a.stratum == "A1"]
        # 5 needed + 5 replacements = 10 full pairs, none counted
        self.assertEqual(len(a1), 20)
        self.assertTrue(all(not a.valid for a in a1))
        self.assertTrue(any("delta" in a.reason for a in a1))
        self.assertTrue(any(a.attempt_id.endswith("-10") for a in a1))
        self.assertTrue(any("could not assemble" in r for r in rep.invalid_reasons))
        self.assertEqual(sum(1 for a in a1 if a.valid), 0)
        self.assertFalse(score_heldout(rep.attempts))

    def test_pair_mismatch_then_valid_replacement_counts(self):
        def exec_trial(stratum_id, arm, spec, seq, match_vel=None):
            if stratum_id == "T0":
                vel = [0, 0, 0]
                if arm == "off":
                    return {
                        "vel": vel,
                        "measured_vel": vel,
                        "events": [{"ev": "bot_stall", "cell": 9001, "origin": [-865.0, -48.0, 90.0]}],
                        "samples": [{"z": 90.0, "on_ground": True}],
                    }
                return {
                    "vel": vel,
                    "measured_vel": vel,
                    "events": [{"ev": "arrived"}],
                    "samples": [{"z": -16.0, "on_ground": True}],
                    "t_arrive": 0.4,
                }
            sign = -1.0 if spec["goal"][0] < spec["start"][0] else 1.0
            mid = spec["vh_lo"] + 10
            vel = [sign * mid, 0.0, 0.0]
            if arm == "on" and stratum_id == "A1" and seq == 1:
                vel = [sign * mid - 8.0, 0.0, 0.0]
            extra = {
                "vel": vel,
                "measured_vel": vel,
                "samples": [{"z": 0.0, "on_ground": True}],
            }
            if arm == "on":
                return {**extra, "events": [{"ev": "arrived"}], "t_arrive": 1.0}
            return {**extra, "events": []}

        runner = drill.DrillRunner(
            recipe=_recipe_with_on(),
            gates=_gates(stratum_at="start"),
            exec_trial=exec_trial,
            ctl_port=27996,
            game_port=27591,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
        )
        rep = runner.run()
        a1 = [a for a in rep.attempts if a.stratum == "A1"]
        a1_valid = [a for a in a1 if a.valid]
        a1_bad = [a for a in a1 if not a.valid]
        self.assertEqual(len(a1_bad), 2)  # first pair replaced
        self.assertEqual(len(a1_valid), 10)  # 5 off + 5 on
        self.assertTrue(any(a.attempt_id == "A1-OFF-06" for a in a1_valid))
        self.assertTrue(rep.valid)
        self.assertTrue(score_heldout(rep.attempts))

    def test_off_stamp_fail_skips_on(self):
        calls = []

        def exec_trial(stratum_id, arm, spec, seq, match_vel=None):
            calls.append((stratum_id, arm, seq, match_vel is not None))
            if stratum_id == "T0":
                vel = [0, 0, 0]
                if arm == "off":
                    return {
                        "vel": vel,
                        "measured_vel": vel,
                        "events": [{"ev": "bot_stall", "cell": 9001, "origin": [-865.0, -48.0, 90.0]}],
                        "samples": [{"z": 90.0, "on_ground": True}],
                    }
                return {
                    "vel": vel,
                    "measured_vel": vel,
                    "events": [{"ev": "arrived"}],
                    "samples": [{"z": -16.0, "on_ground": True}],
                    "t_arrive": 0.4,
                }
            return {
                "vel": [0, 0, 0],
                "measured_vel": [0, 0, 0],
                "stamp_ok": False,
                "stamp_reason": "start-vel out of stratum",
                "events": [],
                "samples": [],
            }

        runner = drill.DrillRunner(
            recipe=_recipe_with_on(),
            gates=_gates(stratum_at="start"),
            exec_trial=exec_trial,
            ctl_port=27996,
            game_port=27591,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
        )
        rep = runner.run()
        heldout_on = [c for c in calls if c[0] != "T0" and c[1] == "on"]
        self.assertEqual(heldout_on, [])
        self.assertFalse(score_heldout(rep.attempts))

    def test_on_stamp_exhaustion_replaces_without_watch(self):
        def exec_trial(stratum_id, arm, spec, seq, match_vel=None):
            if stratum_id == "T0":
                vel = [0, 0, 0]
                if arm == "off":
                    return {
                        "vel": vel,
                        "measured_vel": vel,
                        "events": [{"ev": "bot_stall", "cell": 9001, "origin": [-865.0, -48.0, 90.0]}],
                        "samples": [{"z": 90.0, "on_ground": True}],
                    }
                return {
                    "vel": vel,
                    "measured_vel": vel,
                    "events": [{"ev": "arrived"}],
                    "samples": [{"z": -16.0, "on_ground": True}],
                    "t_arrive": 0.4,
                }
            sign = -1.0 if spec["goal"][0] < spec["start"][0] else 1.0
            mid = spec["vh_lo"] + 10
            vel = [sign * mid, 0.0, 0.0]
            extra = {
                "vel": vel,
                "measured_vel": vel,
                "samples": [{"z": 0.0, "on_ground": True}],
            }
            if arm == "off":
                return {**extra, "events": [], "stamp_ok": True}
            self.assertIsNotNone(match_vel)
            return {
                **extra,
                "vel": [vel[0] + 40.0, 0.0, 0.0],
                "measured_vel": [vel[0] + 40.0, 0.0, 0.0],
                "stamp_ok": False,
                "stamp_reason": "start-vel stamp exhausted vs partner (8 tries, tol=5.0)",
                "events": [],
            }

        runner = drill.DrillRunner(
            recipe=_recipe_with_on(),
            gates=_gates(stratum_at="start"),
            exec_trial=exec_trial,
            ctl_port=27996,
            game_port=27591,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
        )
        rep = runner.run()
        ons = [a for a in rep.attempts if a.stratum != "T0" and a.arm == "on"]
        self.assertTrue(ons)
        self.assertTrue(all(not a.valid for a in ons))
        self.assertTrue(all(a.events == [] for a in ons))
        self.assertTrue(any("stamp" in a.reason for a in ons))
        self.assertTrue(any("could not assemble" in r for r in rep.invalid_reasons))
        self.assertFalse(score_heldout(rep.attempts))

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


class AvsettDropTests(unittest.TestCase):
    def test_shipped_fixture_pins_off_stamp_and_a12_only(self):
        geom = _avsett()
        self.assertEqual(geom["graph_stamp"], WEST_SHELF_OFF["graph_stamp"])
        self.assertEqual(geom["graph_content_hash"], WEST_SHELF_OFF["graph_content_hash"])
        self.assertEqual(set(geom["applies_to"]), {"A1", "A2"})
        self.assertEqual(geom["source"]["cell"], 1372)
        self.assertEqual(geom["target"]["cell"], 403)
        self.assertIn(1214, geom["drop_cells"])
        self.assertIn(1156, geom["landing_cells"])
        self.assertEqual(geom["path_cells"][0], 1372)
        self.assertEqual(geom["path_cells"][-1], 403)

    def test_a1_peak_drop_inside_geometry_is_avsett_not_fall(self):
        gate = _gates()["gates"]["west-shelf"]
        geom = _avsett()
        # airborne origin on the BFS descent (graph, not r2 observed)
        att = drill.classify_trial(
            stratum_id="A1",
            arm="on",
            vel=[-120.0, 0.0, 0.0],
            events=[],
            samples=[
                {"x": 160.0, "y": -728.0, "z": 328.0, "on_ground": True},
                {"x": 140.0, "y": -740.0, "z": 360.0, "on_ground": False},
                {"x": 120.0, "y": -750.0, "z": 200.0, "on_ground": False},
                {"x": 110.0, "y": -755.0, "z": 80.0, "on_ground": False},
                {"x": 100.0, "y": -760.0, "z": -10.0, "on_ground": False},
            ],
            gate=gate,
            stratum_at="start",
            avsett_drop=geom,
        )
        self.assertTrue(att.valid)
        self.assertTrue(att.avsett_drop)
        self.assertFalse(att.fall)

    def test_a1_peak_drop_outside_geometry_is_fall(self):
        gate = _gates()["gates"]["west-shelf"]
        geom = _avsett()
        att = drill.classify_trial(
            stratum_id="A1",
            arm="on",
            vel=[-120.0, 0.0, 0.0],
            events=[],
            samples=[
                {"x": -865.0, "y": -48.0, "z": 200.0, "on_ground": False},
                {"x": -865.0, "y": -48.0, "z": 40.0, "on_ground": False},
            ],
            gate=gate,
            stratum_at="start",
            avsett_drop=geom,
        )
        self.assertTrue(att.valid)
        self.assertTrue(att.fall)
        self.assertFalse(att.avsett_drop)

    def test_a3_peak_drop_is_fall_even_inside_a12_geometry(self):
        gate = _gates()["gates"]["west-shelf"]
        geom = _avsett()
        att = drill.classify_trial(
            stratum_id="A3",
            arm="on",
            vel=[120.0, 0.0, 0.0],
            events=[],
            samples=[
                {"x": 160.0, "y": -728.0, "z": 328.0, "on_ground": True},
                {"x": 140.0, "y": -740.0, "z": 360.0, "on_ground": False},
                {"x": 120.0, "y": -750.0, "z": 200.0, "on_ground": False},
            ],
            gate=gate,
            stratum_at="start",
            avsett_drop=geom,
        )
        self.assertTrue(att.valid)
        self.assertTrue(att.fall)
        self.assertFalse(att.avsett_drop)

    def test_a2_inside_same_pin(self):
        gate = _gates()["gates"]["west-shelf"]
        geom = _avsett()
        att = drill.classify_trial(
            stratum_id="A2",
            arm="off",
            vel=[-200.0, 0.0, 0.0],
            events=[],
            samples=[
                {"x": 96.0, "y": -768.0, "z": 328.0, "on_ground": False},
                {"x": 96.0, "y": -768.0, "z": 160.0, "on_ground": False},
            ],
            gate=gate,
            stratum_at="start",
            avsett_drop=geom,
        )
        self.assertTrue(att.valid)
        self.assertTrue(att.avsett_drop)
        self.assertFalse(att.fall)

    def test_corridor_rejects_west_shelf_and_accepts_descent(self):
        geom = _avsett()
        self.assertTrue(in_avsett_geometry(geom, origin=[160.0, -728.0, 328.0], cell_id=1214))
        self.assertTrue(in_avsett_geometry(geom, origin=[96.0, -768.0, -16.0], cell_id=1156))
        self.assertFalse(in_avsett_geometry(geom, origin=[-865.0, -48.0, 90.0]))
        self.assertFalse(in_avsett_geometry(geom, origin=[400.0, -704.0, 328.0]))

    def test_missing_avsett_fixture_preflight(self):
        runner = drill.DrillRunner(
            recipe=_recipe_with_on(),
            gates=_gates(),
            exec_trial=lambda **k: {},
            ctl_port=27996,
            game_port=27591,
            off_profile={"rtx_nav_patch": "0"},
            on_profile={"rtx_nav_patch": "1"},
            avsett_drop={},
        )
        reasons = runner.preflight()
        self.assertTrue(any("avsett_drop" in r for r in reasons), reasons)


class _AliasGuard(unittest.TestCase):
    def test_import_real_module(self):
        self.assertTrue(hasattr(drill, "DrillRunner"))


if __name__ == "__main__":
    unittest.main()
