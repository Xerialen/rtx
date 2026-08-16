#!/usr/bin/env python3
"""Fixture tests for verify_d_kvitto / d_kvitto (no rig)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from d_kvitto import (  # noqa: E402
    SCHEMA,
    WEST_SHELF_OFF,
    WEST_SHELF_RECIPE,
    astar_from_route_resp,
    astar_path,
    make_kvitto,
    write_kvitto,
)
from verify_d_kvitto import verify  # noqa: E402


def _valid(**overrides) -> dict:
    path = {
        "found": True,
        "cells": [10, 11],
        "links": [3],
        "cost": 1.25,
        "mask_links": [],
    }
    next_best = {
        "found": True,
        "cells": [10, 12, 11],
        "links": [4, 5],
        "cost": 2.0,
        "mask_links": [3],
    }
    kwargs = dict(
        riglock_owner="fable",
        riglock_issued_at="2026-08-16T08:00:00+00:00",
        riglock_valid_from="2026-08-16T08:00:00+00:00",
        riglock_valid_to="2026-08-16T12:00:00+00:00",
        riglock_path="/home/xerial/lab/.rig-lock",
        run_started_at="2026-08-16T08:05:00+00:00",
        run_ended_at="2026-08-16T08:20:00+00:00",
        endpoint_host="127.0.0.1",
        endpoint_ctl_port=27996,
        endpoint_game_port=27591,
        map_name="dm3",
        binary_sha256="ab" * 32,
        commit="4403dc4a1e3043b44490dc4f69aa8091e9065696",
        stamps_off_expected=WEST_SHELF_OFF,
        stamps_off_observed=WEST_SHELF_OFF,
        stamps_on_expected={
            **WEST_SHELF_OFF,
            "cells": 5981,
            "links": 48215,
            "graph_stamp": "1",
            "graph_content_hash": "cd" * 32,
        },
        stamps_on_observed={
            **WEST_SHELF_OFF,
            "cells": 5981,
            "links": 48215,
            "graph_stamp": "1",
            "graph_content_hash": "cd" * 32,
        },
        stamps_undo_expected=WEST_SHELF_OFF,
        stamps_undo_observed=WEST_SHELF_OFF,
        recipe=WEST_SHELF_RECIPE,
        seed=20260816,
        stratum={"id": "T0", "start": [-865.0, -48.0, 90.0], "goal": [-864.0, -96.0, -16.0]},
        raw_pointer="/tmp/trap-repro.jsonl#T0",
        gate_velocity=None,
        gate_cell=None,
        gate_aim_hit=False,
        astar_before=path,
        astar_after=path,
        astar_next_best=next_best,
    )
    kwargs.update(overrides)
    return make_kvitto(**kwargs)


class VerifyTests(unittest.TestCase):
    def test_valid_receipt_is_green(self):
        doc = _valid()
        self.assertEqual(doc["schema"], SCHEMA)
        self.assertEqual(verify(doc), [])

    def test_write_roundtrip(self):
        doc = _valid()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kvitto.json"
            write_kvitto(path, doc)
            loaded = json.loads(path.read_text())
        self.assertEqual(verify(loaded), [])

    def test_missing_field_fails(self):
        doc = _valid()
        del doc["raw_pointer"]
        errs = verify(doc)
        self.assertTrue(any("raw_pointer" in e for e in errs), errs)

    def test_missing_taxonomy_class_fails(self):
        doc = _valid()
        del doc["recipe"]["taxonomy_class"]
        errs = verify(doc)
        self.assertTrue(any("taxonomy_class" in e for e in errs), errs)
        doc = _valid()
        doc["recipe"]["taxonomy_class"] = None
        errs = verify(doc)
        self.assertTrue(any("taxonomy_class" in e for e in errs), errs)
        doc = _valid()
        doc["recipe"]["taxonomy_class"] = ""
        errs = verify(doc)
        self.assertTrue(any("taxonomy_class" in e for e in errs), errs)
        doc = _valid()
        doc["recipe"]["taxonomy_class"] = "   "
        errs = verify(doc)
        self.assertTrue(any("taxonomy_class" in e for e in errs), errs)

    def test_after_the_fact_lock_fails(self):
        doc = _valid(
            riglock_issued_at="2026-08-16T09:00:00+00:00",
            run_started_at="2026-08-16T08:05:00+00:00",
        )
        errs = verify(doc)
        self.assertTrue(any("after-the-fact" in e for e in errs), errs)

    def test_forbidden_ra_port_fails(self):
        doc = _valid(endpoint_ctl_port=27990, endpoint_game_port=27540)
        errs = verify(doc)
        self.assertTrue(any("27990" in e for e in errs), errs)
        self.assertTrue(any("27540" in e for e in errs), errs)

    def test_forbidden_ra_port_as_string_fails(self):
        # Regression: RA/main contact must fail even when the port is a string.
        doc = _valid()
        doc["endpoint"]["ctl_port"] = "27990"
        doc["endpoint"]["game_port"] = "27540"
        errs = verify(doc)
        self.assertTrue(any("27990" in e for e in errs), errs)
        self.assertTrue(any("27540" in e for e in errs), errs)
        doc2 = _valid()
        doc2["endpoint"]["ctl_port"] = "27993"
        errs2 = verify(doc2)
        self.assertTrue(any("27993" in e for e in errs2), errs2)

    def test_stamp_mismatch_fails(self):
        observed = dict(WEST_SHELF_OFF)
        observed["cells"] = 1
        doc = _valid(stamps_off_observed=observed)
        errs = verify(doc)
        self.assertTrue(any("stamps.off" in e and "cells" in e for e in errs), errs)

    def test_stamp_as_number_fails(self):
        bad = dict(WEST_SHELF_OFF)
        bad["graph_stamp"] = 906595427771298736
        doc = _valid(stamps_off_expected=bad, stamps_off_observed=bad)
        errs = verify(doc)
        self.assertTrue(any("decimal string" in e for e in errs), errs)

    def test_undo_must_match_off(self):
        on_like = {
            **WEST_SHELF_OFF,
            "cells": 5981,
            "graph_stamp": "1",
            "graph_content_hash": "cd" * 32,
        }
        doc = _valid(stamps_undo_expected=on_like, stamps_undo_observed=on_like)
        errs = verify(doc)
        self.assertTrue(any("undo.expected must equal" in e for e in errs), errs)

    def test_west_shelf_wrong_off_pin_fails(self):
        other = dict(WEST_SHELF_OFF)
        other["cells"] = 5978
        other["links"] = 48208
        other["graph_stamp"] = "13090435456435551592"
        doc = _valid(stamps_off_expected=other, stamps_off_observed=other)
        errs = verify(doc)
        self.assertTrue(any("OFF expected must be the facit" in e for e in errs), errs)

    def test_missing_next_best_fails(self):
        doc = _valid()
        del doc["astar"]["next_best"]
        errs = verify(doc)
        self.assertTrue(any("next_best" in e for e in errs), errs)

    def test_next_best_must_mask_entire_chosen_path(self):
        doc = _valid()
        doc["astar"]["before"]["links"] = [3, 8]
        doc["astar"]["after"]["links"] = [3, 8]
        doc["astar"]["next_best"]["mask_links"] = [3]  # one hop, not the path
        errs = verify(doc)
        self.assertTrue(any("entire chosen path" in e for e in errs), errs)
        doc["astar"]["next_best"]["mask_links"] = []
        errs = verify(doc)
        self.assertTrue(any("entire chosen path" in e for e in errs), errs)

    def test_wrong_schema_fails(self):
        doc = _valid()
        doc["schema"] = "verktygslada/d-kvitto/0"
        errs = verify(doc)
        self.assertTrue(any("schema" in e for e in errs), errs)

    def test_astar_from_route_resp(self):
        dumped = astar_from_route_resp(
            {
                "legs": [
                    {"link": 7, "src_cell": 1, "tgt_cell": 2},
                    {"link": 8, "src_cell": 2, "tgt_cell": 3},
                ],
                "astar": {"found": True, "cost": 4.5, "mask_links": [9]},
            }
        )
        self.assertEqual(dumped["cells"], [1, 2, 3])
        self.assertEqual(dumped["links"], [7, 8])
        self.assertEqual(dumped["cost"], 4.5)
        self.assertEqual(dumped["mask_links"], [9])
        empty = astar_path(found=False)
        self.assertFalse(empty["found"])
        self.assertEqual(empty["links"], [])

    def test_tag_raw_pointer_fails(self):
        doc = _valid(raw_pointer="roktest:T0-OFF-01")
        errs = verify(doc)
        self.assertTrue(any("jsonl" in e or "path" in e for e in errs), errs)
        doc = _valid(raw_pointer="d_sjalvbevis:A1-ON-01")
        errs = verify(doc)
        self.assertTrue(any("jsonl" in e or "path" in e for e in errs), errs)

    def test_missing_gate_fails(self):
        doc = _valid()
        del doc["gate"]
        errs = verify(doc)
        self.assertTrue(any("gate" in e for e in errs), errs)
        doc = _valid()
        del doc["gate"]["velocity"]
        errs = verify(doc)
        self.assertTrue(any("gate.velocity" in e for e in errs), errs)

    def test_demo_file_absent_is_ok(self):
        doc = _valid()
        self.assertNotIn("demo_file", doc)
        self.assertEqual(verify(doc), [])

    def test_demo_file_nonexistent_path_is_ok(self):
        doc = _valid(demo_file="qw/demos/never-written-rotated.mvd")
        self.assertFalse(Path("qw/demos/never-written-rotated.mvd").is_file())
        self.assertEqual(verify(doc), [])
        doc2 = _valid(
            demo_file="/tmp/definitely-missing-d-demo-xyzzy.mvd"
        )
        self.assertFalse(Path(doc2["demo_file"]).exists())
        self.assertEqual(verify(doc2), [])

    def test_demo_file_empty_or_not_mvd_fails(self):
        doc = _valid(demo_file="")
        errs = verify(doc)
        self.assertTrue(any("demo_file" in e for e in errs), errs)
        doc = _valid()
        doc["demo_file"] = "   "
        errs = verify(doc)
        self.assertTrue(any("demo_file" in e for e in errs), errs)
        doc = _valid()
        doc["demo_file"] = None
        errs = verify(doc)
        self.assertTrue(any("demo_file" in e for e in errs), errs)
        doc = _valid()
        doc["demo_file"] = 12
        errs = verify(doc)
        self.assertTrue(any("demo_file" in e for e in errs), errs)
        doc = _valid(demo_file="qw/demos/foo.jsonl")
        errs = verify(doc)
        self.assertTrue(any(".mvd" in e for e in errs), errs)

    def test_fixture_sha_and_candidate_shape(self):
        doc = _valid(fixture_sha256="ab" * 32, candidate="haz1462-k1")
        self.assertEqual(verify(doc), [])
        doc = _valid()
        doc["fixture_sha256"] = "zz" * 32
        errs = verify(doc)
        self.assertTrue(any("fixture_sha256" in e for e in errs), errs)
        doc = _valid()
        doc["candidate"] = ""
        errs = verify(doc)
        self.assertTrue(any("candidate" in e for e in errs), errs)

    def test_knockback_fields_shape(self):
        kb = {
            "point": "K1",
            "incoming_velocity": [0.0, 60.0, 0.0],
            "land_hit": True,
            "t_land": 0.7,
        }
        doc = _valid(knockback=kb)
        self.assertEqual(verify(doc), [])
        doc = _valid()
        doc["knockback"] = {"point": "", "incoming_velocity": [0, 1], "land_hit": "yes"}
        errs = verify(doc)
        self.assertTrue(any("knockback.point" in e for e in errs), errs)
        self.assertTrue(any("incoming_velocity" in e for e in errs), errs)
        self.assertTrue(any("land_hit" in e for e in errs), errs)

    def test_cvars_only_nav_and_r1_may_differ(self):
        ok = {
            "off": {"rtx_nav_patch": "0", "rtx_r1_lite": "0"},
            "on": {"rtx_nav_patch": "1", "rtx_r1_lite": "1"},
        }
        self.assertEqual(verify(_valid(cvars=ok)), [])
        bad = {
            "off": {"rtx_nav_patch": "0", "rtx_r1_lite": "0", "sv_maxspeed": "320"},
            "on": {"rtx_nav_patch": "1", "rtx_r1_lite": "1", "sv_maxspeed": "400"},
        }
        errs = verify(_valid(cvars=bad))
        self.assertTrue(any("sv_maxspeed" in e for e in errs), errs)


class TrapReproGuardTests(unittest.TestCase):
    def test_refuse_ra_port_before_connect(self):
        import trap_repro

        with self.assertRaises(SystemExit) as ctx:
            trap_repro.require_riglock(27990)
        self.assertIn("RA/main", str(ctx.exception))

    def test_refuse_missing_lock(self):
        import trap_repro

        trap_repro.RIG_LOCK = Path("/tmp/definitely-missing-d-rig-lock")
        with self.assertRaises(SystemExit) as ctx:
            trap_repro.require_riglock(27996)
        self.assertIn("hold the lock", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
