#!/usr/bin/env python3
"""Deploy-runner tests against the REAL Control class + in-process fake ctl.

The fake server speaks rtx-ctlproto msgpack frames. Engine session mirrors
5c66a6d. No hand-rolled request() that accepts dicts Control would refuse.
No rig.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import test_lab_guard  # noqa: F401
import d_failclosed as fc
from ctl_fake import EngineSession, FakeCtlServer
from runner.control import Control, ControlError
from d_deploy import (
    EXPECTED_QWPROGS_SHA256,
    SEALED_MANIFEST_SHA256,
    SEALED_RECEPT_SHA256,
    _step_recipe,
    bound_steps,
    check_binary_pin,
    check_portvakt,
    file_sha256,
    live_to_motor,
    motor_ident,
    mutating_steg,
    preflight,
    run_deploy,
    same_identity,
)
from d_recipe import HERE as TOOLS

RECEPT = Path(TOOLS) / "recept"
MANIFEST = RECEPT / "komponat-v296-ram.manifest.json"
RECEPT_JSON = RECEPT / "komponat-v296-ram.json"
K2_MANIFEST = RECEPT / "komponat-k2-v296-ram.manifest.json"

_FZ_PATCH = None

# 5c66a6d registered_recipe_names() + undoable_names()
ENGINE_REGISTERED = (
    "west-shelf",
    "ram-rail",
    "ram-rail-v2",
    "ram-prevent",
    "haz1462-k1",
    "haz1462-k2",
    "haz1462-k3",
)
ENGINE_UNDO_HANDLES = ("plan-cell", "plan-drop", "plan-link")
ENGINE_UNDOABLE = ENGINE_UNDO_HANDLES + ENGINE_REGISTERED


def setUpModule():
    test_lab_guard.install_lab_guard(suite_global=True)
    global _FZ_PATCH
    _ctx, _FZ_PATCH = test_lab_guard.inject_test_freeze()


def tearDownModule():
    global _FZ_PATCH
    if _FZ_PATCH is not None:
        _FZ_PATCH.stop()
        _FZ_PATCH = None
    fc.reset_deploy_state()


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


def _v296_payload(**over):
    p = {
        "from": [107.0, -582.0, 296.0],
        "takeoff": [92.0, -588.0, 296.0],
        "tgt": [138.1, -701.0, 328.0],
        "v_req": 320.0,
        "gain": 5.5,
        "carried": True,
    }
    p.update(over)
    return p


def _is_planlink(cmd) -> bool:
    return isinstance(cmd, dict) and "PlanLink" in cmd


def _is_komponat(cmd) -> bool:
    return isinstance(cmd, dict) and "Komponat" in cmd


def _is_fixa(cmd, mode: str | None = None, recipe: str | None = None) -> bool:
    if not isinstance(cmd, dict) or "Fixa" not in cmd:
        return False
    body = cmd["Fixa"]
    if mode is not None and body.get("mode") != mode:
        return False
    if recipe is not None and body.get("recipe") != recipe:
        return False
    return True


class DeployRunnerTests(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="d-deploy-"))
        self.idents = _idents()
        self.ctx = fc.FreezeContext.for_test(self.td / ".change-freeze")
        self.qw = self.td / "qwprogs.so"
        self.mv = self.td / "mvdsv"
        self.qw.write_bytes(b"qw-test")
        self.mv.write_bytes(b"mv-test")
        self.qw_sha, self.mv_sha = fc.install_test_pin(self.qw, self.mv)
        self.lock = self.td / "rig.lock"
        self._write_lock()
        self.ctl = None
        self.server = None
        self.engine = None
        self._boot()

    def tearDown(self):
        if self.ctl is not None:
            try:
                self.ctl.close()
            except Exception:
                pass
            self.ctl = None
        if self.server is not None:
            self.server.stop()
            self.server = None
        fc.reset_deploy_state()
        shutil.rmtree(self.td, ignore_errors=True)

    def _boot(self, idents=None, **ek):
        if self.ctl is not None:
            try:
                self.ctl.close()
            except Exception:
                pass
            self.ctl = None
        if self.server is not None:
            self.server.stop()
            self.server = None
        self.engine = EngineSession(idents if idents is not None else self.idents, **ek)
        self.server = FakeCtlServer(self.engine)
        self.ctl = Control("127.0.0.1", self.server.port, protocol="msgpack", timeout=3.0)
        return self.ctl

    def _write_lock(
        self,
        *,
        unit="tbx-d1",
        ctl=27996,
        game=27592,
        token="fable",
        qw=None,
        mv=None,
        body=None,
    ):
        if body is not None:
            self.lock.write_text(body, encoding="utf-8")
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.lock.write_text(
            f"owner=fable\nunit={unit}\nctl_port={ctl}\ngame_port={game}\n"
            f"token={token}\nqwprogs_sha256={qw or self.qw_sha}\n"
            f"mvdsv_sha256={mv or self.mv_sha}\nts={ts}\n",
            encoding="utf-8",
        )

    def _run(self, ctl=None, **kw):
        args = dict(
            manifest_path=MANIFEST,
            recept_path=RECEPT_JSON,
            freeze=self.ctx,
            lock_token="fable",
            lock_path=self.lock,
            qwprogs_path=self.qw,
            mvdsv_path=self.mv,
            ctl_port=27996,
            game_port=27592,
            unit="tbx-d1",
            commit="test",
            outdir=self.td / "out",
        )
        args.update(kw)
        return run_deploy(ctl if ctl is not None else self.ctl, **args)

    def test_engine_register_and_undo_handles_mirror_5c66a6d(self):
        self.assertEqual(fc.ENGINE_REGISTERED_RECIPES, ENGINE_REGISTERED)
        self.assertEqual(fc.ENGINE_UNDO_HANDLES, ENGINE_UNDO_HANDLES)
        self.assertEqual(fc.ENGINE_UNDOABLE, ENGINE_UNDOABLE)
        self.assertNotIn("plan-link", fc.ENGINE_REGISTERED_RECIPES)
        for h in ENGINE_UNDO_HANDLES:
            self.assertIn(h, fc.ENGINE_UNDOABLE)
            self.assertNotIn(h, fc.ENGINE_REGISTERED_RECIPES)

    def test_sealed_bytes(self):
        self.assertEqual(file_sha256(MANIFEST), SEALED_MANIFEST_SHA256)
        self.assertEqual(file_sha256(RECEPT_JSON), SEALED_RECEPT_SHA256)
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        self.assertEqual(fc.op_payload_sha256(rec["ops"][0]), fc.SEALED_V296_PAYLOAD_SHA256)

    def test_lyckad_kedja(self):
        doc = self._run()
        self.assertEqual(doc["outcome"], "applied")
        self.assertIsNone(doc["abort_reason"])
        self.assertEqual(doc["applied"], ["v296-vasthoppet", "ram-rail-v2", "ram-prevent"])
        self.assertEqual(doc["motor_outcome"], "applied")
        self.assertEqual(
            [o["name"] for o in doc["ops"]],
            ["v296-vasthoppet", "ram-rail-v2", "ram-prevent"],
        )
        self.assertEqual(doc["slut_observed"]["cells"], 5983)
        self.assertEqual(doc["slut_observed"]["links"], 48216)
        self.assertIsNone(same_identity(doc["slut_observed"], doc["slut_expected"]))
        self.assertEqual(self.engine.idx, 3)
        self.assertTrue(Path(doc["run_kvitto"]).is_file())
        self.assertEqual(doc["qwprogs_sha256"], self.qw_sha)
        self.assertEqual(doc["mvdsv_sha256"], self.mv_sha)
        self.assertEqual(doc["lock_owner"], "fable")
        self.assertEqual(doc["ctl_port"], 27996)
        self.assertEqual(doc["unit"], "tbx-d1")
        komponat = [c for c in self.engine.cmds if _is_komponat(c)]
        self.assertEqual(len(komponat), 1)
        body = komponat[0]["Komponat"]
        self.assertEqual(len(body["steps"]), 3)
        self.assertTrue(body["lock_token"])
        self.assertEqual(body["recept_id"], "v296-ram")
        self.assertIn("base", body)
        self.assertIn("expect_final", body)
        self.assertFalse(any(_is_planlink(c) for c in self.engine.cmds))
        self.assertFalse(any(_is_fixa(c, mode="apply") for c in self.engine.cmds))
        self.assertFalse(any(_is_fixa(c, mode="undo") for c in self.engine.cmds))
        self.assertFalse(any(_is_fixa(c, mode="chain") for c in self.engine.cmds))
        self.assertEqual(doc["undo_name"], "komponat")
        self.assertEqual(doc["komponat"]["outcome"], "applied")
        self.assertEqual(doc["komponat"]["undo_name"], "komponat")
        self.assertEqual(len(doc["komponat"]["steps"]), 3)
        self.assertIsNone(fc.active_deploy_context())

    def test_d3_pair_accepted(self):
        self._write_lock(unit="tbx-d3", ctl=27998, game=27594)
        doc = self._run(unit="tbx-d3", ctl_port=27998, game_port=27594, outdir=self.td / "d3")
        self.assertEqual(doc["outcome"], "applied")
        self.assertEqual(doc["unit"], "tbx-d3")

    def test_applied_wrong_slut_aborts_without_step_undo(self):
        """Engine said applied but live ≠ slut. Deploy does not chain-undo."""
        self._boot(corrupt_after=True)
        doc = self._run(outdir=self.td / "abort")
        self.assertEqual(doc["outcome"], "aborted")
        self.assertIn("slutverifiering", doc["abort_reason"] or "")
        self.assertEqual(self.engine.idx, 3)
        self.assertFalse(any(_is_fixa(c, mode="chain") for c in self.engine.cmds))
        self.assertFalse(any(_is_fixa(c, mode="undo") for c in self.engine.cmds))
        self.assertTrue((self.td / "abort" / "deploy-run.json").is_file())

    def test_verb_refuse_stays_on_start_stamp(self):
        """Atomic fail: no mutation, live is still the pin. No chain read."""
        self._boot(refuse_apply="ram-rail-v2")
        doc = self._run(outdir=self.td / "refuse")
        self.assertEqual(doc["outcome"], "aborted")
        self.assertIn("ram-rail-v2", doc["abort_reason"] or "")
        self.assertEqual(doc["applied"], [])
        self.assertIsNone(doc["leftover"])
        self.assertEqual(self.engine.idx, 0)
        self.assertEqual(self.engine.stack, [])
        self.assertFalse(any(_is_fixa(c, mode="chain") for c in self.engine.cmds))
        self.assertFalse(any(_is_fixa(c, mode="undo") for c in self.engine.cmds))
        self.assertTrue((self.td / "refuse" / "deploy-run.json").is_file())
        self.assertIsNone(same_identity(doc["slut_observed"], doc["pin"]))
        self.assertEqual(doc["komponat"]["outcome"], "refused")
        self.assertEqual(doc["undo_name"], "komponat")
        self.assertEqual(doc["komponat"]["undo_name"], "komponat")
        self.assertTrue(any(s.get("outcome") == "refused" for s in doc["ops"]))

    def test_receipt_io_failure_does_not_undo_atomic_apply(self):
        """Successful Komponat is not rolled back because a receipt write failed."""
        calls = {"n": 0}

        def boom(path, text):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full")
            Path(path).write_text(text, encoding="utf-8")

        with mock.patch("d_deploy._write_reserved", side_effect=boom):
            doc = self._run(outdir=self.td / "io")
        self.assertEqual(doc["outcome"], "applied")
        self.assertEqual(self.engine.idx, 3)
        self.assertFalse(any(_is_fixa(c, mode="undo") for c in self.engine.cmds))
        self.assertTrue(
            (self.td / "io" / "deploy-run.json").is_file()
            or (self.td / "io" / "deploy-run-recovery.json").is_file()
        )

    def test_frysflagga_vagrar(self):
        fc.write_change_freeze("fable", freeze=self.ctx)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(outdir=self.td / "fz")
        self.assertEqual(cm.exception.gate, "freeze")
        self.assertEqual(self.engine.n_apply, 0)
        self.assertTrue((self.td / "fz" / "deploy-run.json").is_file())

    def test_ej_deploy_eller_okand_sha_vagrar(self):
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(
                manifest_path=K2_MANIFEST,
                recept_path=RECEPT / "komponat-k2-v296-ram.json",
                outdir=self.td / "ej",
            )
        self.assertIn(cm.exception.gate, {"crash-detector", "deploy-status"})
        self.assertEqual(self.engine.n_apply, 0)

    def test_fel_bas_vagrar(self):
        idents = _idents()
        foreign = dict(idents[0])
        foreign["graph_content_hash"] = "cd" * 32
        self._boot(idents=[foreign] + idents[1:])
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(outdir=self.td / "bas")
        self.assertEqual(cm.exception.gate, "crash-detector")
        self.assertIn("pin=bas", str(cm.exception))
        self.assertEqual(self.engine.n_apply, 0)

    def test_f1_unknown_basename_still_requires_sealed_sha(self):
        evil = self.td / "evil-extra-op.manifest.json"
        evil.write_bytes(MANIFEST.read_bytes() + b"\n")
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(manifest_path=evil, outdir=self.td / "f1")
        self.assertEqual(cm.exception.gate, "crash-detector")
        self.assertIn("okänd identitet", str(cm.exception))
        self.assertEqual(self.engine.n_apply, 0)

    def test_f2_extra_op_refused_before_mutation(self):
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        extra = dict(rec["ops"][-1])
        extra["name"] = "ram-prevent-extra"
        rec["ops"].append(extra)
        man["steg"].append({
            "index": 4, "name": "ram-prevent-extra", "op": "shelf_patch",
            "identitet": man["slut"],
        })
        mp = self.td / "extra.manifest.json"
        rp = self.td / "extra.json"
        mp.write_text(json.dumps(man), encoding="utf-8")
        rp.write_text(json.dumps(rec), encoding="utf-8")
        self._boot(idents=self.idents + [self.idents[-1]])
        with self.assertRaises(fc.FailClosed):
            self._run(manifest_path=mp, recept_path=rp, outdir=self.td / "f2")
        self.assertEqual(self.engine.n_apply, 0)

    def test_f3_compares_utan_params_not_params_hash(self):
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        v296 = man["steg"][1]["identitet"]
        motor = motor_ident(v296)
        self.assertEqual(motor["graph_content_hash"], v296["graph_content_hash_utan_params"])
        live = {
            "cells": v296["cells"], "links": v296["links"], "rj_links": 0,
            "graph_stamp": v296["graph_stamp"],
            "graph_content_hash": v296["graph_content_hash_utan_params"],
        }
        self.assertIsNone(same_identity(live, motor))
        live_wrong = dict(live)
        live_wrong["graph_content_hash"] = v296["graph_content_hash"]
        self.assertIsNotNone(same_identity(live_wrong, motor))

    def test_binary_pin_hashes_files_not_caller_strings(self):
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(
                qwprogs_path=None,
                outdir=self.td / "binstr",
            )
        self.assertEqual(cm.exception.gate, "binary")
        self.assertIn("callersträng", str(cm.exception))
        self.assertEqual(self.engine.n_apply, 0)

    def test_binary_pin_refuses_file_mismatch(self):
        fc.clear_test_pin()
        with self.assertRaises(fc.FailClosed) as cm:
            check_binary_pin(self.qw, self.mv)
        self.assertEqual(cm.exception.gate, "binary")

    def test_portvakt_refuses_non_d1_d3_pairs(self):
        for unit, ctl, game in (
            ("tbx-d2", 27997, 27593),
            ("tbx-d4", 27999, 27595),
            (None, 0, 0),
            (None, 12345, 23456),
            ("tbx-d1", 27996, 27594),
            (None, 27990, 27540),
        ):
            with self.subTest(unit=unit, ctl=ctl, game=game):
                with self.assertRaises(fc.FailClosed) as cm:
                    check_portvakt(ctl, game, unit)
                self.assertEqual(cm.exception.gate, "portvakt")

    def test_portvakt_accepts_d1_and_d3_pairs(self):
        self.assertEqual(check_portvakt(27996, 27592, "tbx-d1"), "tbx-d1")
        self.assertEqual(check_portvakt(27998, 27594, "tbx-d3"), "tbx-d3")
        self.assertEqual(check_portvakt(27996, 27592, None), "tbx-d1")

    def test_lock_attacker_anything_refused(self):
        self._write_lock(body="attacker anything\n")
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(outdir=self.td / "atk")
        self.assertEqual(cm.exception.gate, "lock")
        self.assertEqual(self.engine.n_apply, 0)

    def test_lock_wrong_unit_or_token_refused(self):
        self._write_lock(unit="tbx-d3", ctl=27998, game=27594)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(outdir=self.td / "unit")
        self.assertEqual(cm.exception.gate, "lock")

    def test_missing_lock_refuses(self):
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(lock_path=self.td / "no-lock", outdir=self.td / "nolock")
        self.assertEqual(cm.exception.gate, "lock")

    # --- Sol F7 / B / C1–C3 ---

    def test_f7_sol_b_child_labb_rail_without_context(self):
        """Sol B: fixa ram-rail-v2 apply via LABB fixture, no campaign."""
        import fixa
        with self.assertRaises(fc.FailClosed) as cm:
            fixa.run_fixa(
                self.ctl, recipe_id="ram-rail-v2", mode="apply",
                from_cell=None, to_cell=None, freeze=self.ctx,
                lock_token="attacker",
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(self.engine.n_apply, 0)

    def test_f7_sol_b_prevent_without_context(self):
        import fixa
        with self.assertRaises(fc.FailClosed) as cm:
            fixa.run_fixa(
                self.ctl, recipe_id="ram-prevent", mode="apply",
                from_cell=None, to_cell=None, freeze=self.ctx,
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(self.engine.n_apply, 0)

    def test_f7_v296_payload_requires_context(self):
        rec = json.loads((RECEPT / "west-shelf.json").read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed) as cm:
            fc.send_plan_link(self.ctl, _v296_payload(), recipe=rec, freeze=self.ctx)
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(self.engine.n_apply, 0)

    def test_c1_forged_activate_refused(self):
        forged = fc.DeployContext(
            token="forged",
            manifest_sha256="not-sealed-manifest",
            recept_sha256="not-sealed-recipe",
        )
        with self.assertRaises(fc.FailClosed) as cm:
            fc.activate_deploy_context(forged)
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertTrue("stängd" in str(cm.exception) or "självaktiverbar" in str(cm.exception))
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed):
            fc.send_plan_link(
                self.ctl, _v296_payload(), recipe=rec, freeze=self.ctx,
                deploy=True, deploy_ctx=forged,
            )
        self.assertEqual(self.engine.n_apply, 0)
        self.assertIsNone(fc.active_deploy_context())

    def test_c1_mint_without_preflight_refused(self):
        seal = fc.PreflightSeal("nope", SEALED_MANIFEST_SHA256, SEALED_RECEPT_SHA256)
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed) as cm:
            fc.mint_deploy_context(seal, bound_steps(rec))
        self.assertIn("självaktiverbar", str(cm.exception))

    def test_c2_extra_planlink_on_live_context_refused(self):
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        man, recept, live, man_sha, rec_sha, lock_fields, qw, mv, unit, seal = preflight(
            manifest_path=MANIFEST,
            recept_path=RECEPT_JSON,
            ctl=self.ctl,
            freeze=self.ctx,
            lock_path=self.lock,
            lock_token="fable",
            qwprogs_path=self.qw,
            mvdsv_path=self.mv,
            ctl_port=27996,
            game_port=27592,
            unit="tbx-d1",
        )
        ctx = fc.mint_deploy_context(seal, bound_steps(recept))
        syn = _step_recipe(man, [live, live])
        n0 = self.engine.n_apply
        with self.assertRaises(fc.FailClosed) as cm:
            fc.send_plan_link(
                self.ctl,
                _v296_payload(v_req=999.0, gain=999.0),
                recipe=syn,
                freeze=self.ctx,
                deploy=True,
                deploy_ctx=ctx,
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertIn("payload", str(cm.exception).lower() + str(cm.exception))
        self.assertEqual(self.engine.n_apply, n0)
        fc.clear_deploy_context(ctx.token)

    def test_c2_reuse_and_reorder_refused(self):
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        *_, seal = preflight(
            manifest_path=MANIFEST, recept_path=RECEPT_JSON, ctl=self.ctl,
            freeze=self.ctx, lock_path=self.lock, lock_token="fable",
            qwprogs_path=self.qw, mvdsv_path=self.mv,
            ctl_port=27996, game_port=27592, unit="tbx-d1",
        )
        ctx = fc.mint_deploy_context(seal, bound_steps(rec))
        syn = _step_recipe(
            json.loads(MANIFEST.read_text(encoding="utf-8")),
            [self.idents[0], self.idents[1]],
        )
        import fixa
        with self.assertRaises(fc.FailClosed):
            fixa.run_fixa(
                self.ctl, recipe_id="ram-rail-v2", mode="apply",
                from_cell=None, to_cell=None, freeze=self.ctx,
                deploy=True, deploy_ctx=ctx, recipe=syn,
            )
        self.assertEqual(self.engine.n_apply, 0)
        fc.send_plan_link(
            self.ctl, _v296_payload(), recipe=syn, freeze=self.ctx,
            deploy=True, deploy_ctx=ctx, lock_token="fable",
        )
        self.assertEqual(self.engine.n_apply, 1)
        with self.assertRaises(fc.FailClosed) as cm:
            fc.send_plan_link(
                self.ctl, _v296_payload(), recipe=syn, freeze=self.ctx,
                deploy=True, deploy_ctx=ctx,
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(self.engine.n_apply, 1)
        fc.clear_deploy_context(ctx.token)

    def test_c2_borrowed_context_refused(self):
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        *_, seal = preflight(
            manifest_path=MANIFEST, recept_path=RECEPT_JSON, ctl=self.ctl,
            freeze=self.ctx, lock_path=self.lock, lock_token="fable",
            qwprogs_path=self.qw, mvdsv_path=self.mv,
            ctl_port=27996, game_port=27592, unit="tbx-d1",
        )
        real = fc.mint_deploy_context(seal, bound_steps(rec))
        borrowed = fc.DeployContext(
            token="other",
            manifest_sha256=real.manifest_sha256,
            recept_sha256=real.recept_sha256,
            steps=real.steps,
        )
        with self.assertRaises(fc.FailClosed) as cm:
            fc.require_deploy_context(borrowed)
        self.assertIn("lånad", str(cm.exception))
        fc.clear_deploy_context(real.token)

    def test_f7_combination_no_silent_deploy(self):
        """Sol F7 as a whole: the three compose mutations cannot be rebuilt."""
        import d_plant
        import fixa
        komponat = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        west = json.loads((RECEPT / "west-shelf.json").read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed):
            fc.send_plan_link(self.ctl, _v296_payload(), recipe=None, freeze=self.ctx)
        with self.assertRaises(fc.FailClosed):
            d_plant.plant(self.ctl, _v296_payload(), recipe=None)
        with self.assertRaises(fc.FailClosed):
            fc.send_plan_link(self.ctl, _v296_payload(), recipe=west, freeze=self.ctx)
        with self.assertRaises(fc.FailClosed):
            fc.send_plan_link(self.ctl, _v296_payload(), recipe=komponat, freeze=self.ctx)
        with self.assertRaises(fc.FailClosed):
            fixa.run_fixa(
                self.ctl, recipe_id="ram-rail-v2", mode="apply",
                from_cell=None, to_cell=None, freeze=self.ctx,
                lock_token="attacker",
            )
        with self.assertRaises(fc.FailClosed):
            fixa.run_fixa(
                self.ctl, recipe_id="ram-prevent", mode="apply",
                from_cell=None, to_cell=None, freeze=self.ctx,
            )
        self.assertEqual(self.engine.n_apply, 0)
        self.assertFalse(any(_is_planlink(c) for c in self.engine.cmds))
        self.assertFalse(any(_is_fixa(c, mode="apply") for c in self.engine.cmds))
        self.assertIsNone(fc.active_deploy_context())

    def test_west_shelf_labb_apply_still_works(self):
        """Generic LABB of a non-compose recipe is not closed."""
        import fixa
        fixa.run_fixa(
            self.ctl, recipe_id="west-shelf", mode="apply",
            from_cell=None, to_cell=None, freeze=self.ctx, lock_token="fable",
        )
        self.assertEqual(self.engine.n_apply, 1)

    def test_plant_handles_never_appliable(self):
        import fixa
        for h in ENGINE_UNDO_HANDLES:
            with self.subTest(h=h):
                with self.assertRaises(fc.FailClosed) as cm:
                    fixa.run_fixa(
                        self.ctl, recipe_id=h, mode="apply",
                        from_cell=None, to_cell=None, freeze=self.ctx,
                    )
                self.assertIn("undo-handtag", str(cm.exception))
        self.assertEqual(self.engine.n_apply, 0)
        self.assertFalse(any(_is_fixa(c, mode="apply") for c in self.engine.cmds))

    def test_undo_typo_refused_as_unknown_target(self):
        self.engine.stack.append("plan-link")
        self.engine.idx = 1
        for typo in ("plan_link", "planlink", "plan-links", "PLAN-LINK", "compose"):
            with self.subTest(typo=typo):
                with self.assertRaises(ControlError) as cm:
                    self.ctl.request(f"fixa {typo} undo")
                self.assertIn("unknown undo target", str(cm.exception))
        self.assertEqual(self.engine.stack, ["plan-link"])

    def test_chain_is_read_only_and_names_top(self):
        from d_deploy import read_undo_chain
        empty = read_undo_chain(self.ctl)
        self.assertEqual(empty["next"], "")
        self.assertEqual(empty["depth"], 0)
        self.assertEqual(empty["outcome"], "empty")
        self.engine.stack.extend(["plan-link", "ram-rail-v2"])
        self.engine.idx = 2
        chained = read_undo_chain(self.ctl)
        self.assertEqual(chained["next"], "ram-rail-v2")
        self.assertEqual(chained["depth"], 2)
        self.assertEqual(chained["names"], ["plan-link", "ram-rail-v2"])
        self.assertFalse(any(_is_fixa(c, mode="apply") or _is_fixa(c, mode="undo") for c in self.engine.cmds))
        self.assertTrue(all(_is_fixa(c, mode="chain") for c in self.engine.cmds if _is_fixa(c)))

    def test_qwprogs_pin_is_3187fa6(self):
        """Vakten som fällde slutsvepet, nu mot rätt bygge.

        Den pekade på 79c8457 (02bf8d0d…) och gjorde sitt jobb: konstanten hade
        aldrig bumpats när Komponat-verbet landade i 72d5733, så den förseglade
        runnern vägrade den binär den förseglade monteringen kräver. 79c8457 har
        noll förekomster av Komponat i ctlproto; 3fe70a8c-bygget hade Komponat-verbet; 3187fa6 lade till RemoveLinks-op:en.
        Liggaren r2e förseglar 3fe70a8c som slutsvepets binär — koden rättas i
        linje med förseglingen, inte tvärtom (Fables procedurbeslut 13:1xZ).
        """
        from d_deploy import EXPECTED_QWPROGS_SHA256
        self.assertEqual(
            EXPECTED_QWPROGS_SHA256,
            "65be9bdab2b3790c5d05a79c27003e6fa16039ba220c3067b259c9092015d71f",
        )

    def test_v2_self_issued_ticket_refused(self):
        """Sol V2: public issue_preflight_seal + mint + extra PlanLink."""
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed) as cm:
            fc.issue_preflight_seal("attacker-manifest", "attacker-recipe")
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertIn("stängd", str(cm.exception))
        fake = fc.PreflightSeal("forged", "attacker-manifest", "attacker-recipe")
        with self.assertRaises(fc.FailClosed):
            fc.mint_deploy_context(fake, bound_steps(rec))
        self.assertEqual(self.engine.n_apply, 0)
        self.assertIsNone(fc.active_deploy_context())

    def test_non_ok_after_commit_is_motorbugg(self):
        """Non-OK reply after the verb mutated: atomicity broken → motorbugg."""
        self._boot(drop_reply_after_commit=True)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(outdir=self.td / "lost")
        self.assertEqual(cm.exception.gate, "crash-detector")
        self.assertIn("motorbugg", str(cm.exception))
        self.assertEqual(self.engine.idx, 3)
        self.assertFalse(any(_is_fixa(c, mode="chain") for c in self.engine.cmds))
        self.assertFalse(any(_is_fixa(c, mode="undo") for c in self.engine.cmds))
        self.assertTrue((self.td / "lost" / "deploy-run.json").is_file())
        run = json.loads((self.td / "lost" / "deploy-run.json").read_text())
        self.assertIn("motorbugg", run.get("leftover") or "")

    def test_v5_attacker_lock_refused(self):
        """Sol V5: owner=attacker, ts=garbage, self-written lock."""
        self.lock.write_text(
            "owner=attacker\nunit=tbx-d1\nctl_port=27996\ngame_port=27592\n"
            f"token=attacker\nqwprogs_sha256={self.qw_sha}\n"
            f"mvdsv_sha256={self.mv_sha}\nts=garbage\n",
            encoding="utf-8",
        )
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(lock_token="attacker", outdir=self.td / "atklock")
        self.assertEqual(cm.exception.gate, "lock")
        self.assertTrue(
            "owner" in str(cm.exception) or "ts" in str(cm.exception)
        )
        self.assertEqual(self.engine.n_apply, 0)

    def test_text_planlink_verb_still_omits_gain_and_carried(self):
        """Existing text consumers must not change. Structured path is separate."""
        from runner.control import _parse_verb
        typed = _parse_verb(
            "planlink 107 -582 296 92 -588 296 138.1 -701 328 320"
        )
        self.assertEqual(set(typed["PlanLink"]), {"from", "takeoff", "tgt", "v_req"})

    def test_v296_payload_survives_transport_bit_faithfully(self):
        """Recipe payload → Control dict wire → deserialized. Seal at both ends."""
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        op = rec["ops"][0]
        payload = {
            "from": op["from"],
            "takeoff": op["takeoff"],
            "tgt": op["tgt"],
            "v_req": float(op["v_req"]),
            "gain": float(op["gain"]),
            "carried": True,
        }
        wire = fc.planlink_wire_cmd(payload, lock_token="fable")
        sender_sha = fc.planlink_payload_sha256(wire["PlanLink"])
        self.assertEqual(sender_sha, fc.SEALED_V296_PAYLOAD_SHA256)
        self.ctl.request(wire)
        recv = self.engine.last_planlink
        self.assertIsNotNone(recv)
        for key in ("from", "takeoff", "tgt", "v_req", "gain", "carried", "lock_token"):
            self.assertIn(key, recv)
        self.assertTrue(recv["carried"])
        self.assertEqual(recv["lock_token"], "fable")
        self.assertAlmostEqual(float(recv["gain"]), 5.5, places=5)
        self.assertEqual(self.engine.last_planlink_sha, fc.planlink_payload_sha256(recv))
        sent = next(c["PlanLink"] for c in self.engine.cmds if _is_planlink(c))
        self.assertEqual(
            set(sent),
            {"from", "takeoff", "tgt", "v_req", "gain", "carried", "lock_token"},
        )

    def test_komponat_wire_sends_utan_params_not_params_hash(self):
        """Nivå-2 on the wire is graph_content_hash_utan_params. Params-bearing
        hash would fail every step (engine: send the params-FREE hash)."""
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        wire = fc.komponat_wire_cmd(rec, man, lock_token="fable")
        body = wire["Komponat"]
        pin = man["steg"][0]["identitet"]
        self.assertEqual(
            body["base"]["graph_content_hash"],
            pin["graph_content_hash_utan_params"],
        )
        for i, st in enumerate(body["steps"], start=1):
            ident = man["steg"][i]["identitet"]
            utan = ident["graph_content_hash_utan_params"]
            params = ident["graph_content_hash"]
            self.assertEqual(st["expect_after"]["graph_content_hash"], utan)
            self.assertNotEqual(utan, params, f"steg {i} must distinguish the two hashes")
            self.assertNotEqual(st["expect_after"]["graph_content_hash"], params)
        slut = man["slut"]
        self.assertEqual(
            body["expect_final"]["graph_content_hash"],
            slut["graph_content_hash_utan_params"],
        )
        self.assertNotEqual(
            body["expect_final"]["graph_content_hash"],
            slut["graph_content_hash"],
        )

    def test_komponat_wire_carries_full_payload_and_lock(self):
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        wire = fc.komponat_wire_cmd(rec, man, lock_token="fable")
        body = wire["Komponat"]
        steps = body["steps"]
        self.assertEqual(len(steps), 3)
        self.assertEqual(body["lock_token"], "fable")
        self.assertEqual(body["recept_id"], rec["id"])
        self.assertEqual(body["base"]["cells"], 5977)
        self.assertEqual(body["expect_final"]["cells"], 5983)
        pl = steps[0]["op"]["PlanLink"]
        self.assertEqual(fc.op_payload_sha256(rec["ops"][0]), fc.SEALED_V296_PAYLOAD_SHA256)
        self.assertNotIn("carried", pl)
        self.assertNotIn("lock_token", pl)
        self.assertAlmostEqual(float(pl["gain"]), 5.5, places=5)
        self.assertEqual(steps[1]["op"]["Recipe"]["name"], "ram-rail-v2")
        self.assertEqual(steps[2]["op"]["Recipe"]["name"], "ram-prevent")
        self.ctl.request(wire)
        self.assertIsNotNone(self.engine.last_komponat)
        recv = self.engine.last_planlink
        self.assertIsNotNone(recv)
        self.assertAlmostEqual(float(recv["gain"]), 5.5, places=5)
        self.assertNotIn("carried", recv)


if __name__ == "__main__":
    unittest.main()
