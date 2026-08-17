#!/usr/bin/env python3
"""Mock-ctl tests for the compose deploy-runner. No rig."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import test_lab_guard  # noqa: F401
import d_failclosed as fc
from d_deploy import (
    DEFAULT_MANIFEST,
    DEFAULT_RECEPT,
    SEALED_MANIFEST_SHA256,
    apply_ops_of,
    file_sha256,
    ident_of,
    run_deploy,
    same_identity,
)
from d_recipe import HERE as TOOLS

RECEPT = Path(TOOLS) / "recept"
MANIFEST = RECEPT / "komponat-v296-ram.manifest.json"
RECEPT_JSON = RECEPT / "komponat-v296-ram.json"
K2_MANIFEST = RECEPT / "komponat-k2-v296-ram.manifest.json"

_FZ_PATCH = None


def setUpModule():
    test_lab_guard.install_lab_guard(suite_global=True)
    global _FZ_PATCH
    _ctx, _FZ_PATCH = test_lab_guard.inject_test_freeze()


def tearDownModule():
    global _FZ_PATCH
    if _FZ_PATCH is not None:
        _FZ_PATCH.stop()
        _FZ_PATCH = None


def _idents():
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out = [ident_of(man["steg"][0]["identitet"])]
    for s in apply_ops_of(man):
        out.append(ident_of(s["identitet"]))
    return out


def _reply(ident, outcome="dry_run_ok"):
    return {
        "ok": True,
        "data": {
            "cells": ident["cells"],
            "links": ident["links"],
            "rj_links": ident["rj_links"],
            "stamp": ident["graph_stamp"],
            "content_hash": ident["graph_content_hash"],
            "outcome": outcome,
            "recipe": "mock",
            "mode": outcome,
        },
    }


class MockDeployCtl:
    """Identity stack. apply/PlanLink push; undo pops. Optional corrupt after N applies."""

    def __init__(self, idents, *, corrupt_after=None):
        self.idents = [dict(x) for x in idents]
        self.idx = 0
        self.cmds: list = []
        self.corrupt_after = corrupt_after
        self.n_apply = 0

    def _cur(self):
        return self.idents[self.idx]

    def request(self, cmd):
        self.cmds.append(cmd)
        if isinstance(cmd, dict) and "PlanLink" in cmd:
            return self._push("applied")
        if not isinstance(cmd, str):
            raise RuntimeError(f"okänt cmd {cmd!r}")
        parts = cmd.split()
        if len(parts) >= 3 and parts[0] == "fixa" and parts[2] == "dry-run":
            return _reply(self._cur(), "dry_run_ok")
        if len(parts) >= 3 and parts[0] == "fixa" and parts[2] == "apply":
            return self._push("applied")
        if len(parts) >= 3 and parts[0] == "fixa" and parts[2] == "undo":
            if self.idx <= 0:
                raise RuntimeError("undo under bas")
            self.idx -= 1
            return _reply(self._cur(), "undone")
        raise RuntimeError(f"okänt cmd {cmd!r}")

    def _push(self, outcome):
        self.n_apply += 1
        self.idx = min(self.idx + 1, len(self.idents) - 1)
        ident = dict(self.idents[self.idx])
        if self.corrupt_after is not None and self.n_apply == self.corrupt_after:
            ident["graph_content_hash"] = "ab" * 32
            self.idents[self.idx] = ident
        return _reply(ident, outcome)


class DeployRunnerTests(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="d-deploy-"))
        self.idents = _idents()
        self.ctx = fc.FreezeContext.for_test(self.td / ".change-freeze")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_sealed_sha_matches_disk(self):
        self.assertEqual(
            file_sha256(MANIFEST),
            SEALED_MANIFEST_SHA256["komponat-v296-ram.manifest.json"],
        )
        self.assertTrue(SEALED_MANIFEST_SHA256["komponat-v296-ram.manifest.json"].startswith("bcba5897"))

    def test_lyckad_kedja(self):
        ctl = MockDeployCtl(self.idents)
        doc = run_deploy(
            ctl,
            manifest_path=MANIFEST,
            recept_path=RECEPT_JSON,
            freeze=self.ctx,
            lock_token="fable",
            outdir=self.td / "ok",
        )
        self.assertEqual(doc["outcome"], "applied")
        self.assertIsNone(doc["abort_reason"])
        self.assertEqual(doc["applied"], ["v296-vasthoppet", "ram-rail-v2", "ram-prevent"])
        self.assertEqual(doc["undid"], [])
        self.assertEqual(doc["slut_observed"]["cells"], 5983)
        self.assertEqual(doc["slut_observed"]["links"], 48216)
        self.assertIsNone(same_identity(doc["slut_observed"], doc["slut_expected"]))
        self.assertEqual(ctl.idx, 3)
        self.assertTrue((self.td / "ok" / "deploy-run.json").is_file())
        self.assertEqual(len(doc["steg_kvitton"]), 3)
        kinds = [c.split()[0] if isinstance(c, str) else next(iter(c)) for c in ctl.cmds if c]
        self.assertIn("PlanLink", kinds)
        self.assertTrue(any(isinstance(c, str) and "ram-rail-v2 apply" in c for c in ctl.cmds))
        self.assertTrue(any(isinstance(c, str) and "ram-prevent apply" in c for c in ctl.cmds))
        self.assertFalse(any(isinstance(c, str) and " undo " in f" {c} " for c in ctl.cmds if isinstance(c, str)))

    def test_avvikelse_steg_2_full_undo(self):
        """Wrong nivå-2 after rail (same counts/FNV as 5983/48214 trap) → abort + undo to bas."""
        ctl = MockDeployCtl(self.idents, corrupt_after=2)
        doc = run_deploy(
            ctl,
            manifest_path=MANIFEST,
            recept_path=RECEPT_JSON,
            freeze=self.ctx,
            lock_token="fable",
            outdir=self.td / "abort",
        )
        self.assertEqual(doc["outcome"], "aborted")
        self.assertIn("steg 2", doc["abort_reason"] or "")
        self.assertIn("nivå-1 räcker inte", doc["abort_reason"] or "")
        self.assertEqual(doc["undid"], ["ram-rail-v2", "v296-vasthoppet"])
        self.assertEqual(ctl.idx, 0)
        self.assertIsNone(same_identity(doc["slut_observed"], doc["pin"]))
        undos = [c for c in ctl.cmds if isinstance(c, str) and " undo " in f" {c} "]
        self.assertGreaterEqual(len(undos), 2)

    def test_frysflagga_vagrar(self):
        fc.write_change_freeze("fable", freeze=self.ctx)
        ctl = MockDeployCtl(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            run_deploy(
                ctl,
                manifest_path=MANIFEST,
                recept_path=RECEPT_JSON,
                freeze=self.ctx,
                outdir=self.td / "fz",
            )
        self.assertEqual(cm.exception.gate, "freeze")
        self.assertEqual(ctl.n_apply, 0)
        self.assertFalse(any(isinstance(c, dict) and "PlanLink" in c for c in ctl.cmds))
        self.assertFalse(any(isinstance(c, str) and " apply " in f" {c} " for c in ctl.cmds))

    def test_ej_deploy_vagrar(self):
        ctl = MockDeployCtl(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            run_deploy(
                ctl,
                manifest_path=K2_MANIFEST,
                recept_path=RECEPT / "komponat-k2-v296-ram.json",
                freeze=self.ctx,
                outdir=self.td / "ej",
            )
        self.assertEqual(cm.exception.gate, "deploy-status")
        self.assertIn("EJ-DEPLOY", str(cm.exception))
        self.assertEqual(ctl.n_apply, 0)

    def test_fel_bas_vagrar(self):
        idents = _idents()
        foreign = dict(idents[0])
        foreign["graph_content_hash"] = "cd" * 32
        ctl = MockDeployCtl([foreign] + idents[1:])
        with self.assertRaises(fc.FailClosed) as cm:
            run_deploy(
                ctl,
                manifest_path=MANIFEST,
                recept_path=RECEPT_JSON,
                freeze=self.ctx,
                outdir=self.td / "bas",
            )
        self.assertEqual(cm.exception.gate, "crash-detector")
        self.assertIn("pin=bas", str(cm.exception))
        self.assertEqual(ctl.n_apply, 0)


if __name__ == "__main__":
    unittest.main()
