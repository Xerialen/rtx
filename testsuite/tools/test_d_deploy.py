#!/usr/bin/env python3
"""Mock-ctl tests for deploy-runner varv 2 (Sol af56481). No rig."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import test_lab_guard  # noqa: F401
import d_failclosed as fc
from d_deploy import (
    EXPECTED_QWPROGS_SHA256,
    PLAN_LINK_UNDO_ID,
    SEALED_MANIFEST_SHA256,
    SEALED_RECEPT_SHA256,
    file_sha256,
    live_to_motor,
    motor_ident,
    mutating_steg,
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
    if fc.active_deploy_context() is not None:
        fc.clear_deploy_context(fc.active_deploy_context().token)


def _idents():
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out = [motor_ident(man["steg"][0]["identitet"])]
    for s in mutating_steg(man):
        out.append(motor_ident(s["identitet"]))
    return out


def _reply(ident, outcome="dry_run_ok"):
    m = live_to_motor(ident)
    return {
        "ok": True,
        "data": {
            "cells": m["cells"],
            "links": m["links"],
            "rj_links": m["rj_links"],
            "stamp": m["graph_stamp"],
            "content_hash": m["graph_content_hash"],
            "outcome": outcome,
            "recipe": "mock",
            "mode": outcome,
        },
    }


class MockDeployCtl:
    """Stack of named frames. Undo requires the engine name (plan-link / recipe)."""

    def __init__(self, idents, *, corrupt_after=None, refuse_apply=None):
        self.idents = [dict(x) for x in idents]
        self.idx = 0
        self.cmds: list = []
        self.stack: list[str] = []
        self.corrupt_after = corrupt_after
        self.refuse_apply = refuse_apply
        self.n_apply = 0

    def _cur(self):
        return self.idents[self.idx]

    def request(self, cmd):
        self.cmds.append(cmd)
        if isinstance(cmd, dict) and "PlanLink" in cmd:
            return self._push("plan-link", "applied")
        if not isinstance(cmd, str):
            raise RuntimeError(f"okänt cmd {cmd!r}")
        parts = cmd.split()
        if len(parts) >= 3 and parts[0] == "fixa" and parts[2] == "dry-run":
            return _reply(self._cur(), "dry_run_ok")
        if len(parts) >= 3 and parts[0] == "fixa" and parts[2] == "apply":
            rid = parts[1]
            if self.refuse_apply and rid == self.refuse_apply:
                raise fc.FailClosed("deploy", f"steg {rid} vägrad")
            return self._push(rid, "applied")
        if len(parts) >= 3 and parts[0] == "fixa" and parts[2] == "undo":
            rid = parts[1]
            if not self.stack:
                raise RuntimeError("undo under tom stack")
            if self.stack[-1] != rid:
                raise fc.FailClosed(
                    "deploy",
                    f"unknown recipe {rid!r}; stacktop {self.stack[-1]!r}",
                )
            self.stack.pop()
            if self.idx <= 0:
                raise RuntimeError("undo under bas")
            self.idx -= 1
            return _reply(self._cur(), "undone")
        raise RuntimeError(f"okänt cmd {cmd!r}")

    def _push(self, name, outcome):
        self.n_apply += 1
        self.stack.append(name)
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
        self.lock = self.td / "rig.lock"
        self.lock.write_text("fable 2026-08-17T00:00:00Z\n", encoding="utf-8")

    def tearDown(self):
        if fc.active_deploy_context() is not None:
            fc.clear_deploy_context(fc.active_deploy_context().token)
        shutil.rmtree(self.td, ignore_errors=True)

    def _run(self, ctl, **kw):
        args = dict(
            manifest_path=MANIFEST,
            recept_path=RECEPT_JSON,
            freeze=self.ctx,
            lock_token="fable",
            lock_path=self.lock,
            qwprogs_sha256=EXPECTED_QWPROGS_SHA256,
            ctl_port=27996,
            game_port=27592,
            unit="tbx-d1",
            commit="test",
            outdir=self.td / "out",
        )
        args.update(kw)
        return run_deploy(ctl, **args)

    def test_sealed_bytes(self):
        self.assertEqual(file_sha256(MANIFEST), SEALED_MANIFEST_SHA256)
        self.assertEqual(file_sha256(RECEPT_JSON), SEALED_RECEPT_SHA256)
        self.assertTrue(SEALED_MANIFEST_SHA256.startswith("bcba5897"))

    def test_lyckad_kedja(self):
        ctl = MockDeployCtl(self.idents)
        doc = self._run(ctl)
        self.assertEqual(doc["outcome"], "applied")
        self.assertIsNone(doc["abort_reason"])
        self.assertEqual(doc["applied"], ["v296-vasthoppet", "ram-rail-v2", "ram-prevent"])
        self.assertEqual(doc["undid"], [])
        self.assertEqual(doc["slut_observed"]["cells"], 5983)
        self.assertEqual(doc["slut_observed"]["links"], 48216)
        self.assertIsNone(same_identity(doc["slut_observed"], doc["slut_expected"]))
        self.assertEqual(ctl.idx, 3)
        self.assertTrue(Path(doc["run_kvitto"]).is_file())
        self.assertEqual(doc["qwprogs_sha256"], EXPECTED_QWPROGS_SHA256)
        self.assertEqual(doc["lock_owner"], "fable")
        self.assertEqual(doc["ctl_port"], 27996)
        self.assertTrue(any(isinstance(c, dict) and "PlanLink" in c for c in ctl.cmds))
        self.assertFalse(any(
            isinstance(c, str) and " compose " in f" {c} " for c in ctl.cmds
        ))

    def test_avvikelse_steg_2_full_undo_plan_link_name(self):
        ctl = MockDeployCtl(self.idents, corrupt_after=2)
        doc = self._run(ctl, outdir=self.td / "abort")
        self.assertEqual(doc["outcome"], "aborted")
        self.assertIn("steg 2", doc["abort_reason"] or "")
        self.assertEqual(doc["undid"], ["ram-rail-v2", "v296-vasthoppet"])
        self.assertEqual(ctl.idx, 0)
        undos = [c for c in ctl.cmds if isinstance(c, str) and " undo " in f" {c} "]
        self.assertTrue(any(f"fixa {PLAN_LINK_UNDO_ID} undo" in c for c in undos))
        self.assertFalse(any("fixa compose undo" in c for c in undos))
        self.assertTrue((self.td / "abort" / "deploy-run.json").is_file())

    def test_apply_refuse_after_v296_still_undos(self):
        """Sol F4: FailClosed from steg 2 must undo V296 and write run kvitto."""
        ctl = MockDeployCtl(self.idents, refuse_apply="ram-rail-v2")
        doc = self._run(ctl, outdir=self.td / "refuse")
        self.assertEqual(doc["outcome"], "aborted")
        self.assertIn("vägrades", doc["abort_reason"] or "")
        self.assertEqual(doc["applied"], ["v296-vasthoppet"])
        self.assertEqual(doc["undid"], ["v296-vasthoppet"])
        self.assertEqual(ctl.idx, 0)
        self.assertTrue((self.td / "refuse" / "deploy-run.json").is_file())
        self.assertTrue(any(
            isinstance(c, str) and f"fixa {PLAN_LINK_UNDO_ID} undo" in c
            for c in ctl.cmds
        ))

    def test_frysflagga_vagrar(self):
        fc.write_change_freeze("fable", freeze=self.ctx)
        ctl = MockDeployCtl(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, outdir=self.td / "fz")
        self.assertEqual(cm.exception.gate, "freeze")
        self.assertEqual(ctl.n_apply, 0)
        self.assertTrue((self.td / "fz" / "deploy-run.json").is_file())

    def test_ej_deploy_eller_okand_sha_vagrar(self):
        ctl = MockDeployCtl(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(
                ctl,
                manifest_path=K2_MANIFEST,
                recept_path=RECEPT / "komponat-k2-v296-ram.json",
                outdir=self.td / "ej",
            )
        self.assertIn(cm.exception.gate, {"crash-detector", "deploy-status"})
        self.assertEqual(ctl.n_apply, 0)

    def test_fel_bas_vagrar(self):
        idents = _idents()
        foreign = dict(idents[0])
        foreign["graph_content_hash"] = "cd" * 32
        ctl = MockDeployCtl([foreign] + idents[1:])
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, outdir=self.td / "bas")
        self.assertEqual(cm.exception.gate, "crash-detector")
        self.assertIn("pin=bas", str(cm.exception))
        self.assertEqual(ctl.n_apply, 0)

    def test_f1_unknown_basename_still_requires_sealed_sha(self):
        evil = self.td / "evil-extra-op.manifest.json"
        evil.write_bytes(MANIFEST.read_bytes() + b"\n")
        ctl = MockDeployCtl(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, manifest_path=evil, outdir=self.td / "f1")
        self.assertEqual(cm.exception.gate, "crash-detector")
        self.assertIn("okänd identitet", str(cm.exception))
        self.assertEqual(ctl.n_apply, 0)

    def test_f2_extra_op_refused_before_mutation(self):
        """Sol's 7th probe: extra trailing op must not apply."""
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        extra = dict(rec["ops"][-1])
        extra["name"] = "ram-prevent-extra"
        rec["ops"].append(extra)
        man["steg"].append({
            "index": 4, "name": "ram-prevent-extra", "op": "shelf_patch",
            "identitet": man["slut"],
        })
        # Even if SHA were skipped, bind_ops refuses. SHA fails first on copy.
        mp = self.td / "extra.manifest.json"
        rp = self.td / "extra.json"
        mp.write_text(json.dumps(man), encoding="utf-8")
        rp.write_text(json.dumps(rec), encoding="utf-8")
        ctl = MockDeployCtl(self.idents + [self.idents[-1]])
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, manifest_path=mp, recept_path=rp, outdir=self.td / "f2")
        self.assertEqual(ctl.n_apply, 0)
        self.assertNotEqual(getattr(cm.exception, "gate", ""), "")

    def test_f3_compares_utan_params_not_params_hash(self):
        """Live motor hash is utan_params. Params-hash must not be required."""
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        v296 = man["steg"][1]["identitet"]
        self.assertNotEqual(
            v296["graph_content_hash"],
            v296["graph_content_hash_utan_params"],
        )
        motor = motor_ident(v296)
        self.assertEqual(motor["graph_content_hash"], v296["graph_content_hash_utan_params"])
        self.assertNotEqual(motor["graph_content_hash"], v296["graph_content_hash"])
        live = {
            "cells": v296["cells"],
            "links": v296["links"],
            "rj_links": 0,
            "graph_stamp": v296["graph_stamp"],
            "graph_content_hash": v296["graph_content_hash_utan_params"],
        }
        self.assertIsNone(same_identity(live, motor))
        live_wrong = dict(live)
        live_wrong["graph_content_hash"] = v296["graph_content_hash"]
        self.assertIsNotNone(same_identity(live_wrong, motor))

    def test_binary_pin_refuses_mismatch(self):
        ctl = MockDeployCtl(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, qwprogs_sha256="00" * 32, outdir=self.td / "bin")
        self.assertEqual(cm.exception.gate, "binary")
        self.assertEqual(ctl.n_apply, 0)

    def test_portvakt_refuses_ra(self):
        ctl = MockDeployCtl(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, ctl_port=27990, outdir=self.td / "port")
        self.assertEqual(cm.exception.gate, "portvakt")

    def test_missing_lock_refuses(self):
        ctl = MockDeployCtl(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, lock_path=self.td / "no-lock", outdir=self.td / "nolock")
        self.assertEqual(cm.exception.gate, "lock")

    def test_f7_planlink_none_refused(self):
        ctl = MockDeployCtl(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            fc.send_plan_link(ctl, {"from": [107.0, -582.0, 296.0]}, recipe=None, freeze=self.ctx)
        self.assertEqual(cm.exception.gate, "deploy-status")
        self.assertEqual(ctl.n_apply, 0)

    def test_f7_plant_wrapper_none_refused(self):
        """d_plant.plant is a listed production callsite (Sol F7)."""
        import d_plant
        ctl = MockDeployCtl(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            d_plant.plant(ctl, {"from": [107.0, -582.0, 296.0]}, recipe=None)
        self.assertEqual(cm.exception.gate, "deploy-status")
        self.assertEqual(ctl.n_apply, 0)

    def test_f7_check_deploy_none_in_deploy_mode(self):
        with self.assertRaises(fc.FailClosed) as cm:
            fc.check_deploy_status(None, deploy=True)
        self.assertEqual(cm.exception.gate, "deploy-status")

    def test_f7_guard_plant_none_in_deploy_mode(self):
        with self.assertRaises(fc.FailClosed) as cm:
            fc.guard_plant(None, freeze=self.ctx, deploy=True)
        self.assertEqual(cm.exception.gate, "deploy-status")

    def test_f7_lab_path_komponat_without_context(self):
        import fixa
        ctl = MockDeployCtl(self.idents)
        komponat = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed) as cm:
            fixa.run_fixa(
                ctl, recipe_id="v296-ram", mode="apply",
                from_cell=None, to_cell=None, recipe=komponat, freeze=self.ctx,
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(ctl.n_apply, 0)

    def test_f7_planlink_komponat_without_context(self):
        ctl = MockDeployCtl(self.idents)
        komponat = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed) as cm:
            fc.send_plan_link(
                ctl, {"from": [107.0, -582.0, 296.0]}, recipe=komponat, freeze=self.ctx,
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(ctl.n_apply, 0)

    def test_f7_forged_context_is_not_active(self):
        forged = fc.DeployContext(token="forged", manifest_sha256="aa", recept_sha256="bb")
        ctl = MockDeployCtl(self.idents)
        komponat = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed) as cm:
            fc.send_plan_link(
                ctl, {"from": [107.0, -582.0, 296.0]}, recipe=komponat,
                freeze=self.ctx, deploy=True, deploy_ctx=forged,
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(ctl.n_apply, 0)
        self.assertIsNone(fc.active_deploy_context())

    def test_f7_combination_no_context_no_mutation(self):
        """Sol F7 as a whole: PlanLink(None) + child apply is not a silent deploy."""
        import d_plant
        import fixa
        ctl = MockDeployCtl(self.idents)
        komponat = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        payload = {
            "from": [107.0, -582.0, 296.0], "takeoff": [92.0, -588.0, 296.0],
            "tgt": [138.1, -701.0, 328.0], "v_req": 320.0, "gain": 5.5,
        }
        with self.assertRaises(fc.FailClosed):
            fc.send_plan_link(ctl, payload, recipe=None, freeze=self.ctx)
        with self.assertRaises(fc.FailClosed):
            d_plant.plant(ctl, payload, recipe=None)
        with self.assertRaises(fc.FailClosed):
            fc.send_plan_link(ctl, payload, recipe=komponat, freeze=self.ctx)
        with self.assertRaises(fc.FailClosed):
            fixa.run_fixa(
                ctl, recipe_id="ram-rail-v2", mode="apply",
                from_cell=None, to_cell=None, recipe=komponat, freeze=self.ctx,
            )
        with self.assertRaises(fc.FailClosed):
            fc.guard_plant(None, freeze=self.ctx, deploy=True)
        with self.assertRaises(fc.FailClosed):
            fc.check_deploy_status(None, deploy=True)
        self.assertEqual(ctl.n_apply, 0)
        self.assertFalse(any(isinstance(c, dict) and "PlanLink" in c for c in ctl.cmds))
        self.assertFalse(any(isinstance(c, str) and c.startswith("fixa ") for c in ctl.cmds))
        self.assertIsNone(fc.active_deploy_context())


if __name__ == "__main__":
    unittest.main()
