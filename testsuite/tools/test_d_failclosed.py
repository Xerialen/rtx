#!/usr/bin/env python3
"""Offline tests for fail-closed gates 3+4+7. No rig."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import d_failclosed as fc
from d_recipe import REGISTERED_IDS, load_recipe

HERE = Path(__file__).resolve().parent
RECEPT = HERE / "recept"

BAS = {
    "cells": 5977,
    "links": 48207,
    "rj_links": 0,
    "graph_stamp": "906595427771298736",
    "graph_content_hash": "58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9",
}
K2_ON = {
    "cells": 5977,
    "links": 48205,
    "rj_links": 0,
    "graph_stamp": "14344244513446609626",
    "graph_content_hash": "feb503b67a0b4a4fa394f6066fa5a3b45d373d57b154d079e2b4910329e7eb7e",
}
RAIL_ON = {
    "cells": 5983,
    "links": 48213,
    "rj_links": 0,
    "graph_stamp": "8774822664048001128",
    "graph_content_hash": "1d8df1d9fa4685554cb6ab55911276bf6b104bdeec025a3381c0261055edebf9",
}
# Same counts/FNV as BAS, other graph (K3-class / K2+prevent coincidence).
BAS_COUNTS_OTHER_HASH = {
    **BAS,
    "graph_content_hash": "ab0974e0f2518b63d87f427143404b2b7f455d39a541b45a3f007518190be9a3",
}


def _recipe(**extra):
    doc = {
        "id": "unit-compose",
        "map": "dm3",
        "off": dict(BAS),
        "on_expected": dict(K2_ON),
        "remove_links": [
            {
                "id": 10447,
                "from": 1416,
                "to": 1461,
                "kind": "walk",
                "origin": [288.0, -844.0, 264.0],
            }
        ],
        "source": {"cell": 1416, "origin": [288.0, -844.0, 264.0]},
    }
    doc.update(extra)
    return doc


class FreezeTests(unittest.TestCase):
    def test_absent_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".change-freeze"
            self.assertIsNone(fc.change_freeze_reason(p))
            fc.check_change_freeze(p)

    def test_present_refuses_with_owner_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".change-freeze"
            p.write_text("fable 2026-08-17T07:15:00Z Xerial T20m\n", encoding="utf-8")
            why = fc.change_freeze_reason(p)
            self.assertIsNotNone(why)
            self.assertIn("fable 2026-08-17T07:15:00Z", why)
            self.assertIn("vägrar mutation", why)
            with self.assertRaises(fc.FailClosed) as ctx:
                fc.check_change_freeze(p)
            self.assertEqual(ctx.exception.gate, "freeze")

    def test_empty_file_still_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".change-freeze"
            p.write_text("", encoding="utf-8")
            why = fc.change_freeze_reason(p)
            self.assertIn("tom fil", why)

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "flag"
            p.write_text("fable now\n", encoding="utf-8")
            old = os.environ.get("D_CHANGE_FREEZE")
            os.environ["D_CHANGE_FREEZE"] = str(p)
            try:
                self.assertEqual(fc.freeze_path(), p)
                self.assertIn("fable now", fc.change_freeze_reason())
            finally:
                if old is None:
                    os.environ.pop("D_CHANGE_FREEZE", None)
                else:
                    os.environ["D_CHANGE_FREEZE"] = old

    def test_guard_blocks_all_four_verbs(self):
        rec = _recipe()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".change-freeze"
            p.write_text("fable freeze\n", encoding="utf-8")
            for verb in ("apply", "undo", "plant", "portvakt"):
                with self.assertRaises(fc.FailClosed) as ctx:
                    fc.guard_mutation(
                        verb, recipe=rec, live=BAS, freeze_path_override=p
                    )
                self.assertEqual(ctx.exception.gate, "freeze")
                self.assertNotIn("fixa", str(ctx.exception))


class CrashDetectorTests(unittest.TestCase):
    def test_off_and_on_are_sealed(self):
        rec = _recipe()
        self.assertIsNone(fc.live_in_sealed(BAS, rec))
        self.assertIsNone(fc.live_in_sealed(K2_ON, rec))
        fc.check_live_sealed(BAS, rec)

    def test_intermediate_is_sealed(self):
        rec = _recipe(intermediates=[dict(K2_ON)], on_expected=dict(RAIL_ON))
        self.assertIsNone(fc.live_in_sealed(K2_ON, rec))
        self.assertIsNone(fc.live_in_sealed(RAIL_ON, rec))

    def test_ops_mellanstamp_is_sealed(self):
        rec = _recipe(
            ops=[{"mellanstamp": dict(K2_ON)}],
            on_expected=dict(RAIL_ON),
        )
        self.assertIsNone(fc.live_in_sealed(K2_ON, rec))

    def test_unknown_live_refused(self):
        rec = _recipe()
        why = fc.live_in_sealed(RAIL_ON, rec)
        self.assertIsNotNone(why)
        self.assertIn("∉ förseglad mängd", why)
        with self.assertRaises(fc.FailClosed) as ctx:
            fc.guard_mutation("apply", recipe=rec, live=RAIL_ON)
        self.assertEqual(ctx.exception.gate, "crash-detector")

    def test_same_counts_other_hash_refused(self):
        """K2+prevent / K3-class: same FNV as bas, other nivå-2."""
        rec = _recipe()
        why = fc.live_in_sealed(BAS_COUNTS_OTHER_HASH, rec)
        self.assertIsNotNone(why)
        self.assertIn("∉ förseglad mängd", why)

    def test_counts_only_live_refused(self):
        rec = _recipe()
        why = fc.live_in_sealed({"cells": 5977, "links": 48207, "rj_links": 0}, rec)
        self.assertIsNotNone(why)
        self.assertIn("saknar nivå-1", why)

    def test_missing_live_refused(self):
        rec = _recipe()
        with self.assertRaises(fc.FailClosed):
            fc.guard_mutation("apply", recipe=rec, live=None)

    def test_undo_also_checks_live(self):
        rec = _recipe()
        with self.assertRaises(fc.FailClosed) as ctx:
            fc.guard_mutation("undo", recipe=rec, live=RAIL_ON)
        self.assertEqual(ctx.exception.gate, "crash-detector")


class AnchorTests(unittest.TestCase):
    def test_registered_recipes_pass(self):
        for rid in sorted(REGISTERED_IDS):
            rec = load_recipe(RECEPT / f"{rid}.json")
            why = fc.validate_anchors(rec)
            self.assertIsNone(why, f"{rid}: {why}")

    def test_k2_id_with_walk_anchor_ok(self):
        rec = load_recipe(RECEPT / "haz1462-k2.json")
        self.assertIsNone(fc.validate_anchors(rec))
        ids = {op["id"] for op in rec["remove_links"]}
        self.assertEqual(ids, {10446, 10447})

    def test_k2_id_without_walk_refused(self):
        rec = _recipe(remove_links=[{"id": 10447, "from": 1416, "to": 1461}])
        # kind missing
        why = fc.validate_anchors(rec)
        self.assertIsNotNone(why)
        self.assertIn("from/to/kind", why)

    def test_k2_id_wrong_endpoints_refused(self):
        rec = _recipe(
            remove_links=[{
                "id": 10447,
                "from": 1416,
                "to": 1459,
                "kind": "walk",
                "origin": [288.0, -844.0, 264.0],
            }]
        )
        why = fc.validate_anchors(rec)
        self.assertIsNotNone(why)
        self.assertIn("walk-ankaret", why)

    def test_foreign_link_id_refused(self):
        rec = _recipe(
            remove_links=[{
                "id": 48131,
                "from": 1167,
                "to": 1191,
                "kind": "speedjump",
                "origin": [96.0, -568.0, 296.0],
            }]
        )
        why = fc.validate_anchors(rec)
        self.assertIsNotNone(why)
        self.assertIn("48131", why)
        self.assertIn("link_vid_cert-klassen", why)

    def test_link_vid_cert_key_is_poison(self):
        rec = _recipe(link_vid_cert=48131)
        why = fc.validate_anchors(rec)
        self.assertIsNotNone(why)
        self.assertIn("gift", why)

    def test_link_vid_cert_nested_in_op_is_poison(self):
        rec = _recipe()
        rec["ops"] = [{"link_vid_cert": 48147, "from": 1167, "to": 1191, "kind": "speedjump"}]
        why = fc.validate_anchors(rec)
        self.assertIsNotNone(why)
        self.assertIn("gift", why)

    def test_op_without_origin_refused(self):
        rec = {
            "id": "bare",
            "off": dict(BAS),
            "on_expected": dict(K2_ON),
            "remove_links": [{"from": 1416, "to": 1461, "kind": "walk"}],
        }
        why = fc.validate_anchors(rec)
        self.assertIsNotNone(why)
        self.assertIn("origin", why)

    def test_origin_from_recipe_source_ok(self):
        rec = {
            "id": "via-source",
            "off": dict(BAS),
            "on_expected": dict(K2_ON),
            "remove_links": [{"from": 1416, "to": 1461, "kind": "walk"}],
            "source": {"cell": 1416, "origin": [288.0, -844.0, 264.0]},
        }
        self.assertIsNone(fc.validate_anchors(rec))

    def test_id_without_from_to_kind_refused(self):
        rec = _recipe(remove_links=[{"id": 10447}])
        why = fc.validate_anchors(rec)
        self.assertIsNotNone(why)

    def test_west_shelf_carve_has_no_raw_ids(self):
        rec = load_recipe(RECEPT / "west-shelf.json")
        self.assertIsNone(fc.validate_anchors(rec))
        self.assertFalse(rec.get("remove_links"))
        self.assertNotIn("link_vid_cert", rec)

    def test_prevent_drops_are_origin_anchored(self):
        rec = load_recipe(RECEPT / "ram-prevent.json")
        self.assertIsNone(fc.validate_anchors(rec))
        for d in rec["drops"]:
            self.assertEqual(d["kind"], "drop")
            self.assertEqual(len(d["from"]), 3)


class GuardIntegrationTests(unittest.TestCase):
    def test_apply_ok_on_bas(self):
        fc.guard_mutation("apply", recipe=_recipe(), live=BAS)

    def test_plant_does_not_require_live(self):
        fc.guard_plant(_recipe())

    def test_plant_still_checks_anchors(self):
        with self.assertRaises(fc.FailClosed) as ctx:
            fc.guard_plant(_recipe(remove_links=[{"id": 48131, "from": 1, "to": 2, "kind": "walk"}]))
        self.assertEqual(ctx.exception.gate, "anchor")

    def test_unknown_verb_refused(self):
        with self.assertRaises(fc.FailClosed):
            fc.guard_mutation("dry-run", recipe=_recipe(), live=BAS)


if __name__ == "__main__":
    unittest.main()
