#!/usr/bin/env python3
"""R1-vakt: receipt format + judged-run refuse. No rig, no ~/lab."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import test_lab_guard  # noqa: F401
import d_failclosed as fc
import r1_vakt
from r1_vakt import (
    SCHEMA,
    parse_rokdeploy_kvitto,
    refuse_judged_run,
    require_rokdeploy,
    write_rokdeploy_kvitto,
)

SLUT = {
    "cells": 5983,
    "links": 48216,
    "rj_links": 0,
    "graph_stamp": "11908727279900740725",
    "graph_content_hash": "aa" * 32,
}
MAN_SHA = "bcba5897a9af7887d63fcf7466081a52d0bc84885c6d227002ca3d67727bc8e8"


def _fields(**over):
    base = dict(
        runner_commit="aabb3a9edde929d79d81000bc74200f9f5c34458",
        manifest_sha256=MAN_SHA,
        lock_token="fable-kampanj-token",
        slut_observed=dict(SLUT),
    )
    base.update(over)
    return base


class R1KvittoFormatTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def _write(self, name="rok.json", **over):
        p = self.dir / name
        write_rokdeploy_kvitto(p, **_fields(**over))
        return p

    def test_valid_receipt_accepted(self):
        p = self._write()
        doc = require_rokdeploy(p)
        self.assertEqual(doc["schema"], SCHEMA)
        self.assertEqual(doc["outcome"], "applied")
        self.assertEqual(doc["unit"], "tbx-d1")

    def test_cli_ok_and_refuse(self):
        p = self._write()
        self.assertEqual(r1_vakt.main(["--kvitto", str(p)]), 0)
        self.assertEqual(r1_vakt.main(["--kvitto", str(self.dir / "gone.json")]), 2)

    def test_missing_path_refused(self):
        with self.assertRaises(fc.FailClosed) as cm:
            require_rokdeploy(None)
        self.assertEqual(cm.exception.gate, "r1")
        self.assertIn("utan föregående rökdeploy-kvitto", str(cm.exception))
        self.assertIsNotNone(refuse_judged_run(None))
        self.assertIsNone(refuse_judged_run(self._write()))

    def test_missing_file_refused(self):
        with self.assertRaises(fc.FailClosed) as cm:
            require_rokdeploy(self.dir / "nope.json")
        self.assertEqual(cm.exception.gate, "r1")
        self.assertIn("saknas", str(cm.exception))

    def test_aborted_outcome_refused(self):
        p = self._write(outcome="aborted")
        with self.assertRaises(fc.FailClosed) as cm:
            require_rokdeploy(p)
        self.assertIn("applied", str(cm.exception))

    def test_wrong_schema_refused(self):
        p = self._write()
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["schema"] = "deploy-run/1"
        p.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(fc.FailClosed) as cm:
            require_rokdeploy(p)
        self.assertIn("schema", str(cm.exception))

    def test_bad_iso_and_empty_token_refused(self):
        p = self._write(written_at="not-a-date")
        with self.assertRaises(fc.FailClosed):
            require_rokdeploy(p)
        p = self._write(lock_token="")
        with self.assertRaises(fc.FailClosed) as cm:
            require_rokdeploy(p)
        self.assertIn("lock_token", str(cm.exception))

    def test_ra_unit_and_mixed_ports_refused(self):
        p = self._write(unit="tbx-d2", ctl_port=27997, game_port=27593)
        with self.assertRaises(fc.FailClosed) as cm:
            require_rokdeploy(p)
        self.assertIn("tbx-d1/d3", str(cm.exception))
        p = self._write(unit="tbx-d1", ctl_port=27996, game_port=27594)
        with self.assertRaises(fc.FailClosed) as cm:
            require_rokdeploy(p)
        self.assertIn("portpar", str(cm.exception))

    def test_d3_pair_accepted(self):
        p = self._write(unit="tbx-d3", ctl_port=27998, game_port=27594)
        doc = require_rokdeploy(p)
        self.assertEqual(doc["unit"], "tbx-d3")

    def test_slut_observed_must_be_complete(self):
        bad = dict(SLUT)
        del bad["graph_content_hash"]
        p = self._write(slut_observed=bad)
        with self.assertRaises(fc.FailClosed) as cm:
            require_rokdeploy(p)
        self.assertIn("graph_content_hash", str(cm.exception))

    def test_parse_rejects_non_object(self):
        with self.assertRaises(fc.FailClosed):
            parse_rokdeploy_kvitto([1, 2, 3])

    def test_never_touches_real_lab(self):
        # The suite-global lab-guard would raise if we opened ~/lab.
        p = self._write()
        require_rokdeploy(p)
        self.assertTrue(str(p).startswith(str(self.dir)))


if __name__ == "__main__":
    unittest.main()
