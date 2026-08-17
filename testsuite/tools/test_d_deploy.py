#!/usr/bin/env python3
"""Mock-ctl tests for deploy-runner varv 3 (Sol 7670f9a + Fable 5c66a6d). No rig.

Production-realistic mock mirrors 5c66a6d: registered recipes for
apply/dry-run; undoable = plant handles + recipes; chain is read-only.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import test_lab_guard  # noqa: F401
import d_failclosed as fc
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


class ProdEngineMock:
    """5c66a6d do_fixa: chain before lookup; undo via undoable_name; apply via register.

    Plant handles are undo-only. PlanLink is a separate verb.
    """

    REGISTERED = ENGINE_REGISTERED
    UNDO_HANDLES = ENGINE_UNDO_HANDLES
    UNDOABLE = ENGINE_UNDOABLE

    def __init__(self, idents, *, corrupt_after=None, refuse_apply=None, drop_reply_after_commit=None):
        self.idents = [dict(x) for x in idents]
        self.idx = 0
        self.cmds: list = []
        self.stack: list[str] = []
        self.corrupt_after = corrupt_after
        self.refuse_apply = refuse_apply
        self.drop_reply_after_commit = drop_reply_after_commit
        self.n_apply = 0

    def _cur(self):
        return self.idents[self.idx]

    def _chain_reply(self):
        ident = _reply(self._cur(), "chained" if self.stack else "empty")
        data = ident["data"]
        if not self.stack:
            data["recipe"] = ""
            data["mode"] = "chain"
            data["outcome"] = "empty"
            data["audit"] = (
                "rtx: navpatch undo chain: empty (live graph is this session's base)\n"
            )
            return ident
        top = self.stack[-1]
        names = " -> ".join(self.stack)
        data["recipe"] = top
        data["mode"] = "chain"
        data["outcome"] = "chained"
        data["audit"] = (
            f"rtx: navpatch undo chain: depth={len(self.stack)} [{names}], "
            f"next undo={top}\n"
        )
        return ident

    def request(self, cmd):
        self.cmds.append(cmd)
        if isinstance(cmd, dict) and "PlanLink" in cmd:
            return self._push("plan-link", "applied")
        if not isinstance(cmd, str):
            raise RuntimeError(f"okänt cmd {cmd!r}")
        parts = cmd.split()
        if len(parts) < 3 or parts[0] != "fixa":
            raise RuntimeError(f"okänt cmd {cmd!r}")
        rid, mode = parts[1], parts[2]
        if mode == "chain":
            return self._chain_reply()
        if mode == "undo":
            if rid not in self.UNDOABLE:
                raise fc.FailClosed(
                    "deploy",
                    f"unknown undo target {rid!r}; undoable: {list(self.UNDOABLE)}",
                )
            if not self.stack:
                raise fc.FailClosed("deploy", "no apply snapshot — nothing to undo")
            if self.stack[-1] != rid:
                raise fc.FailClosed(
                    "deploy",
                    f"top of the undo chain is '{self.stack[-1]}', not '{rid}' "
                    f"— undo in reverse order",
                )
            self.stack.pop()
            if self.idx <= 0:
                raise RuntimeError("undo under bas")
            self.idx -= 1
            return _reply(self._cur(), "undone")
        if rid not in self.REGISTERED:
            raise fc.FailClosed(
                "deploy",
                f"unknown recipe {rid!r}; registered: {list(self.REGISTERED)}",
            )
        if mode == "dry-run":
            return _reply(self._cur(), "dry_run_ok")
        if mode == "apply":
            if self.refuse_apply and rid == self.refuse_apply:
                raise fc.FailClosed("deploy", f"steg {rid} vägrad")
            return self._push(rid, "applied")
        raise RuntimeError(f"okänt cmd {cmd!r}")

    def _push(self, name, outcome):
        self.n_apply += 1
        self.stack.append(name)
        self.idx = min(self.idx + 1, len(self.idents) - 1)
        ident = dict(self.idents[self.idx])
        if self.drop_reply_after_commit is not None and self.n_apply == self.drop_reply_after_commit:
            raise OSError("reply lost after commit")
        if self.corrupt_after is not None and self.n_apply == self.corrupt_after:
            ident["graph_content_hash"] = "ab" * 32
            self.idents[self.idx] = ident
        return _reply(ident, outcome)


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

    def tearDown(self):
        fc.reset_deploy_state()
        shutil.rmtree(self.td, ignore_errors=True)

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

    def _run(self, ctl, **kw):
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
        return run_deploy(ctl, **args)

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
        ctl = ProdEngineMock(self.idents)
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
        self.assertEqual(doc["qwprogs_sha256"], self.qw_sha)
        self.assertEqual(doc["mvdsv_sha256"], self.mv_sha)
        self.assertEqual(doc["lock_owner"], "fable")
        self.assertEqual(doc["ctl_port"], 27996)
        self.assertEqual(doc["unit"], "tbx-d1")
        self.assertTrue(any(isinstance(c, dict) and "PlanLink" in c for c in ctl.cmds))
        self.assertFalse(any(
            isinstance(c, str) and " compose " in f" {c} " for c in ctl.cmds
        ))
        self.assertIsNone(fc.active_deploy_context())

    def test_d3_pair_accepted(self):
        self._write_lock(unit="tbx-d3", ctl=27998, game=27594)
        ctl = ProdEngineMock(self.idents)
        doc = self._run(ctl, unit="tbx-d3", ctl_port=27998, game_port=27594, outdir=self.td / "d3")
        self.assertEqual(doc["outcome"], "applied")
        self.assertEqual(doc["unit"], "tbx-d3")

    def test_avvikelse_steg_2_full_undo_via_chain(self):
        """Recovery reads chain top, then undoes that name — including plan-link."""
        ctl = ProdEngineMock(self.idents, corrupt_after=2)
        doc = self._run(ctl, outdir=self.td / "abort")
        self.assertEqual(doc["outcome"], "aborted")
        self.assertIn("steg 2", doc["abort_reason"] or "")
        self.assertEqual(doc["undid"], ["ram-rail-v2", "plan-link"])
        self.assertEqual(ctl.idx, 0)
        self.assertEqual(ctl.stack, [])
        chains = [c for c in ctl.cmds if isinstance(c, str) and c.endswith(" chain")]
        undos = [c for c in ctl.cmds if isinstance(c, str) and " undo " in f" {c} "]
        self.assertGreaterEqual(len(chains), 2)
        self.assertTrue(any("fixa plan-link undo" in c for c in undos))
        self.assertTrue(any("fixa ram-rail-v2 undo" in c for c in undos))
        self.assertFalse(any("fixa compose undo" in c for c in undos))
        self.assertFalse(any("plan_link" in c for c in undos))
        self.assertTrue((self.td / "abort" / "deploy-run.json").is_file())

    def test_apply_refuse_after_v296_undos_via_chain(self):
        ctl = ProdEngineMock(self.idents, refuse_apply="ram-rail-v2")
        doc = self._run(ctl, outdir=self.td / "refuse")
        self.assertEqual(doc["outcome"], "aborted")
        self.assertEqual(doc["applied"], ["v296-vasthoppet"])
        self.assertEqual(doc["undid"], ["plan-link"])
        self.assertEqual(ctl.idx, 0)
        self.assertTrue(any(
            isinstance(c, str) and c.endswith(" chain") for c in ctl.cmds
        ))
        self.assertTrue(any(
            isinstance(c, str) and "fixa plan-link undo" in c for c in ctl.cmds
        ))
        self.assertTrue((self.td / "refuse" / "deploy-run.json").is_file())

    def test_f4_oserror_after_apply_still_undos_and_writes_run(self):
        """Sol F4: I/O after mutation must not skip undo/receipt."""
        ctl = ProdEngineMock(self.idents)

        def boom(*a, **k):
            raise OSError("disk full")

        with mock.patch("d_deploy._write_steg_kvitto", side_effect=boom):
            doc = self._run(ctl, outdir=self.td / "io")
        self.assertEqual(doc["outcome"], "aborted")
        self.assertTrue((self.td / "io" / "deploy-run.json").is_file())
        self.assertGreaterEqual(ctl.n_apply, 1)
        self.assertEqual(ctl.idx, 0)
        self.assertIn("disk full", doc["abort_reason"] or "")
        self.assertTrue(any(
            isinstance(c, str) and "fixa plan-link undo" in c for c in ctl.cmds
        ))

    def test_frysflagga_vagrar(self):
        fc.write_change_freeze("fable", freeze=self.ctx)
        ctl = ProdEngineMock(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, outdir=self.td / "fz")
        self.assertEqual(cm.exception.gate, "freeze")
        self.assertEqual(ctl.n_apply, 0)
        self.assertTrue((self.td / "fz" / "deploy-run.json").is_file())

    def test_ej_deploy_eller_okand_sha_vagrar(self):
        ctl = ProdEngineMock(self.idents)
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
        ctl = ProdEngineMock([foreign] + idents[1:])
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, outdir=self.td / "bas")
        self.assertEqual(cm.exception.gate, "crash-detector")
        self.assertIn("pin=bas", str(cm.exception))
        self.assertEqual(ctl.n_apply, 0)

    def test_f1_unknown_basename_still_requires_sealed_sha(self):
        evil = self.td / "evil-extra-op.manifest.json"
        evil.write_bytes(MANIFEST.read_bytes() + b"\n")
        ctl = ProdEngineMock(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, manifest_path=evil, outdir=self.td / "f1")
        self.assertEqual(cm.exception.gate, "crash-detector")
        self.assertIn("okänd identitet", str(cm.exception))
        self.assertEqual(ctl.n_apply, 0)

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
        ctl = ProdEngineMock(self.idents + [self.idents[-1]])
        with self.assertRaises(fc.FailClosed):
            self._run(ctl, manifest_path=mp, recept_path=rp, outdir=self.td / "f2")
        self.assertEqual(ctl.n_apply, 0)

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
        ctl = ProdEngineMock(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(
                ctl,
                qwprogs_path=None,
                outdir=self.td / "binstr",
            )
        self.assertEqual(cm.exception.gate, "binary")
        self.assertIn("callersträng", str(cm.exception))
        self.assertEqual(ctl.n_apply, 0)

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
        ctl = ProdEngineMock(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, outdir=self.td / "atk")
        self.assertEqual(cm.exception.gate, "lock")
        self.assertEqual(ctl.n_apply, 0)

    def test_lock_wrong_unit_or_token_refused(self):
        self._write_lock(unit="tbx-d3", ctl=27998, game=27594)
        ctl = ProdEngineMock(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, outdir=self.td / "unit")
        self.assertEqual(cm.exception.gate, "lock")

    def test_missing_lock_refuses(self):
        ctl = ProdEngineMock(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, lock_path=self.td / "no-lock", outdir=self.td / "nolock")
        self.assertEqual(cm.exception.gate, "lock")

    # --- Sol F7 / B / C1–C3 ---

    def test_f7_sol_b_child_labb_rail_without_context(self):
        """Sol B: fixa ram-rail-v2 apply via LABB fixture, no campaign."""
        import fixa
        ctl = ProdEngineMock(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            fixa.run_fixa(
                ctl, recipe_id="ram-rail-v2", mode="apply",
                from_cell=None, to_cell=None, freeze=self.ctx,
                lock_token="attacker",
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(ctl.n_apply, 0)

    def test_f7_sol_b_prevent_without_context(self):
        import fixa
        ctl = ProdEngineMock(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            fixa.run_fixa(
                ctl, recipe_id="ram-prevent", mode="apply",
                from_cell=None, to_cell=None, freeze=self.ctx,
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(ctl.n_apply, 0)

    def test_f7_v296_payload_requires_context(self):
        ctl = ProdEngineMock(self.idents)
        rec = json.loads((RECEPT / "west-shelf.json").read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed) as cm:
            fc.send_plan_link(ctl, _v296_payload(), recipe=rec, freeze=self.ctx)
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(ctl.n_apply, 0)

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
        ctl = ProdEngineMock(self.idents)
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed):
            fc.send_plan_link(
                ctl, _v296_payload(), recipe=rec, freeze=self.ctx,
                deploy=True, deploy_ctx=forged,
            )
        self.assertEqual(ctl.n_apply, 0)
        self.assertIsNone(fc.active_deploy_context())

    def test_c1_mint_without_preflight_refused(self):
        seal = fc.PreflightSeal("nope", SEALED_MANIFEST_SHA256, SEALED_RECEPT_SHA256)
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed) as cm:
            fc.mint_deploy_context(seal, bound_steps(rec))
        self.assertIn("självaktiverbar", str(cm.exception))

    def test_c2_extra_planlink_on_live_context_refused(self):
        ctl = ProdEngineMock(self.idents)
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        man, recept, live, man_sha, rec_sha, lock_fields, qw, mv, unit, seal = preflight(
            manifest_path=MANIFEST,
            recept_path=RECEPT_JSON,
            ctl=ctl,
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
        n0 = ctl.n_apply
        with self.assertRaises(fc.FailClosed) as cm:
            fc.send_plan_link(
                ctl,
                _v296_payload(v_req=999.0, gain=999.0),
                recipe=syn,
                freeze=self.ctx,
                deploy=True,
                deploy_ctx=ctx,
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertIn("payload", str(cm.exception).lower() + str(cm.exception))
        self.assertEqual(ctl.n_apply, n0)
        fc.clear_deploy_context(ctx.token)

    def test_c2_reuse_and_reorder_refused(self):
        ctl = ProdEngineMock(self.idents)
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        *_, seal = preflight(
            manifest_path=MANIFEST, recept_path=RECEPT_JSON, ctl=ctl,
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
                ctl, recipe_id="ram-rail-v2", mode="apply",
                from_cell=None, to_cell=None, freeze=self.ctx,
                deploy=True, deploy_ctx=ctx, recipe=syn,
            )
        self.assertEqual(ctl.n_apply, 0)
        fc.send_plan_link(
            ctl, _v296_payload(), recipe=syn, freeze=self.ctx,
            deploy=True, deploy_ctx=ctx,
        )
        self.assertEqual(ctl.n_apply, 1)
        with self.assertRaises(fc.FailClosed) as cm:
            fc.send_plan_link(
                ctl, _v296_payload(), recipe=syn, freeze=self.ctx,
                deploy=True, deploy_ctx=ctx,
            )
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertEqual(ctl.n_apply, 1)
        fc.clear_deploy_context(ctx.token)

    def test_c2_borrowed_context_refused(self):
        ctl = ProdEngineMock(self.idents)
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        *_, seal = preflight(
            manifest_path=MANIFEST, recept_path=RECEPT_JSON, ctl=ctl,
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
        ctl = ProdEngineMock(self.idents)
        komponat = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        west = json.loads((RECEPT / "west-shelf.json").read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed):
            fc.send_plan_link(ctl, _v296_payload(), recipe=None, freeze=self.ctx)
        with self.assertRaises(fc.FailClosed):
            d_plant.plant(ctl, _v296_payload(), recipe=None)
        with self.assertRaises(fc.FailClosed):
            fc.send_plan_link(ctl, _v296_payload(), recipe=west, freeze=self.ctx)
        with self.assertRaises(fc.FailClosed):
            fc.send_plan_link(ctl, _v296_payload(), recipe=komponat, freeze=self.ctx)
        with self.assertRaises(fc.FailClosed):
            fixa.run_fixa(
                ctl, recipe_id="ram-rail-v2", mode="apply",
                from_cell=None, to_cell=None, freeze=self.ctx,
                lock_token="attacker",
            )
        with self.assertRaises(fc.FailClosed):
            fixa.run_fixa(
                ctl, recipe_id="ram-prevent", mode="apply",
                from_cell=None, to_cell=None, freeze=self.ctx,
            )
        self.assertEqual(ctl.n_apply, 0)
        self.assertFalse(any(isinstance(c, dict) and "PlanLink" in c for c in ctl.cmds))
        self.assertFalse(any(
            isinstance(c, str) and c.startswith("fixa ") and " apply" in c
            for c in ctl.cmds
        ))
        self.assertIsNone(fc.active_deploy_context())

    def test_west_shelf_labb_apply_still_works(self):
        """Generic LABB of a non-compose recipe is not closed."""
        import fixa
        ctl = ProdEngineMock(self.idents)
        fixa.run_fixa(
            ctl, recipe_id="west-shelf", mode="apply",
            from_cell=None, to_cell=None, freeze=self.ctx, lock_token="fable",
        )
        self.assertEqual(ctl.n_apply, 1)

    def test_plant_handles_never_appliable(self):
        import fixa
        ctl = ProdEngineMock(self.idents)
        for h in ENGINE_UNDO_HANDLES:
            with self.subTest(h=h):
                with self.assertRaises(fc.FailClosed) as cm:
                    fixa.run_fixa(
                        ctl, recipe_id=h, mode="apply",
                        from_cell=None, to_cell=None, freeze=self.ctx,
                    )
                self.assertIn("undo-handtag", str(cm.exception))
        self.assertEqual(ctl.n_apply, 0)
        self.assertFalse(any(
            isinstance(c, str) and " apply" in c for c in ctl.cmds
        ))

    def test_undo_typo_refused_as_unknown_target(self):
        ctl = ProdEngineMock(self.idents)
        ctl.stack.append("plan-link")
        ctl.idx = 1
        for typo in ("plan_link", "planlink", "plan-links", "PLAN-LINK", "compose"):
            with self.subTest(typo=typo):
                with self.assertRaises(fc.FailClosed) as cm:
                    ctl.request(f"fixa {typo} undo")
                self.assertIn("unknown undo target", str(cm.exception))
        self.assertEqual(ctl.stack, ["plan-link"])

    def test_chain_is_read_only_and_names_top(self):
        from d_deploy import read_undo_chain
        ctl = ProdEngineMock(self.idents)
        empty = read_undo_chain(ctl)
        self.assertEqual(empty["next"], "")
        self.assertEqual(empty["depth"], 0)
        self.assertEqual(empty["outcome"], "empty")
        ctl.stack.extend(["plan-link", "ram-rail-v2"])
        ctl.idx = 2
        chained = read_undo_chain(ctl)
        self.assertEqual(chained["next"], "ram-rail-v2")
        self.assertEqual(chained["depth"], 2)
        self.assertEqual(chained["names"], ["plan-link", "ram-rail-v2"])
        self.assertFalse(any(
            isinstance(c, str) and (" apply" in c or " undo " in f" {c} ")
            for c in ctl.cmds
        ))
        self.assertTrue(all(
            isinstance(c, str) and c.endswith(" chain") for c in ctl.cmds
        ))

    def test_qwprogs_pin_is_5c66a6d(self):
        from d_deploy import EXPECTED_QWPROGS_SHA256
        self.assertEqual(
            EXPECTED_QWPROGS_SHA256,
            "4e71191c5dce641be593834dcf2f4736724f24685e84ea5b2b2de905087392f0",
        )

    def test_v2_self_issued_ticket_refused(self):
        """Sol V2: public issue_preflight_seal + mint + extra PlanLink."""
        ctl = ProdEngineMock(self.idents)
        rec = json.loads(RECEPT_JSON.read_text(encoding="utf-8"))
        with self.assertRaises(fc.FailClosed) as cm:
            fc.issue_preflight_seal("attacker-manifest", "attacker-recipe")
        self.assertEqual(cm.exception.gate, "deploy-context")
        self.assertIn("stängd", str(cm.exception))
        fake = fc.PreflightSeal("forged", "attacker-manifest", "attacker-recipe")
        with self.assertRaises(fc.FailClosed):
            fc.mint_deploy_context(fake, bound_steps(rec))
        self.assertEqual(ctl.n_apply, 0)
        self.assertIsNone(fc.active_deploy_context())

    def test_v3_reply_lost_after_commit_undos_via_chain(self):
        """Sol V3: PlanLink commits, reply lost, applied empty — motor chain is truth."""
        ctl = ProdEngineMock(self.idents, drop_reply_after_commit=1)
        doc = self._run(ctl, outdir=self.td / "lost")
        self.assertEqual(doc["outcome"], "aborted")
        self.assertIn("reply lost after commit", doc["abort_reason"] or "")
        self.assertEqual(ctl.idx, 0)
        self.assertEqual(ctl.stack, [])
        self.assertTrue(any(
            isinstance(c, str) and c.endswith(" chain") for c in ctl.cmds
        ))
        self.assertTrue(any(
            isinstance(c, str) and "fixa plan-link undo" in c for c in ctl.cmds
        ))
        self.assertTrue((self.td / "lost" / "deploy-run.json").is_file())
        run = json.loads((self.td / "lost" / "deploy-run.json").read_text())
        self.assertIn("plan-link", run.get("undid") or [])

    def test_v5_attacker_lock_refused(self):
        """Sol V5: owner=attacker, ts=garbage, self-written lock."""
        self.lock.write_text(
            "owner=attacker\nunit=tbx-d1\nctl_port=27996\ngame_port=27592\n"
            f"token=attacker\nqwprogs_sha256={self.qw_sha}\n"
            f"mvdsv_sha256={self.mv_sha}\nts=garbage\n",
            encoding="utf-8",
        )
        ctl = ProdEngineMock(self.idents)
        with self.assertRaises(fc.FailClosed) as cm:
            self._run(ctl, lock_token="attacker", outdir=self.td / "atklock")
        self.assertEqual(cm.exception.gate, "lock")
        self.assertTrue(
            "owner" in str(cm.exception) or "ts" in str(cm.exception)
        )
        self.assertEqual(ctl.n_apply, 0)


if __name__ == "__main__":
    unittest.main()
