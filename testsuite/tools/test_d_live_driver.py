#!/usr/bin/env python3
"""Mock-ctl tests for the GAP 4 live driver. No rig."""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from d_kvitto import WEST_SHELF_OFF  # noqa: E402
from d_live_driver import (  # noqa: E402
    LiveTrialDriver,
    annotate_event,
    heading_vel,
    mid_band_speed,
    refuse_ra,
)
from d_recipe import load_gates, load_recipe, on_expected  # noqa: E402
from d_sjalvbevis import classify_trial  # noqa: E402
from d_strata import STRATA, direction_dot, vh  # noqa: E402
from verify_d_kvitto import verify  # noqa: E402


QW = "49f6f330fa6e199169ea2ddaf564134499a561724c229417c1017a65a7cc2133"
MV = "858465007c7bea52c5c790cdfdd07c0d65cce17b48110b327595bb8c2e051f15"


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += float(s)


class MockCtl:
    """In-memory ctl: status/fixa/teleport/goto/events/cell/route."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.cmds: list[str] = []
        self.origin = [-865.0, -48.0, 88.0]
        self.vel = [0.0, 0.0, 0.0]
        self.speed = 0.0
        self.on_ground = True
        self.cell_id = 108
        self.ent = 1
        self.arm = "off"
        self.token_ok = "fable"
        self.emit_after_goto: list[dict] = []
        self.drop_after_goto = False
        self._goto = False
        recipe = load_recipe(HERE / "recept" / "west-shelf.json")
        self.off = dict(recipe["off"])
        self.on = on_expected(recipe)

    def _stamp(self) -> dict:
        s = self.on if self.arm == "on" else self.off
        return {
            "cells": s["cells"],
            "links": s["links"],
            "rj_links": s["rj_links"],
            "stamp": s["graph_stamp"],
            "content_hash": s["graph_content_hash"],
        }

    def request(self, cmd: str) -> dict:
        self.cmds.append(cmd)
        parts = cmd.split()
        verb = parts[0]
        if verb == "status":
            if self._goto and self.vel != [0.0, 0.0, 0.0]:
                dt = 0.05
                self.origin = [
                    self.origin[0] + self.vel[0] * dt,
                    self.origin[1] + self.vel[1] * dt,
                    self.origin[2] + self.vel[2] * dt,
                ]
            if self._goto and self.drop_after_goto:
                self.origin = [self.origin[0], self.origin[1], self.origin[2] - 80.0]
                self.on_ground = False
            return {
                "ok": True,
                "data": {
                    "navmesh": "ready",
                    "map": "dm3",
                    "cells": self._stamp()["cells"],
                    "links": self._stamp()["links"],
                    "rj_links": 0,
                    "bots": [{
                        "ent": self.ent,
                        "origin": list(self.origin),
                        "speed": math.hypot(self.vel[0], self.vel[1]),
                        "on_ground": self.on_ground,
                    }],
                },
            }
        if verb == "fixa":
            mode = parts[2]
            token = ""
            if "lock" in parts:
                token = parts[parts.index("lock") + 1]
            if mode == "dry-run":
                d = self._stamp()
                d["outcome"] = "dry_run_ok"
                d["recipe"] = "west-shelf"
                d["mode"] = "dry-run"
                return {"ok": True, "data": d}
            if mode in {"apply", "undo"}:
                if not token:
                    return {"ok": True, "data": {"outcome": "failed", "reason": "requires lock_token"}}
                if token != self.token_ok:
                    return {"ok": True, "data": {"outcome": "failed", "reason": "does not match rig-lock"}}
                self.arm = "on" if mode == "apply" else "off"
                d = self._stamp()
                d["outcome"] = "applied" if mode == "apply" else "undone"
                d["recipe"] = "west-shelf"
                d["mode"] = mode
                return {"ok": True, "data": d}
            return {"ok": True, "data": {"outcome": "failed", "reason": mode}}
        if verb == "prep":
            return {"ok": True, "data": {}}
        if verb == "teleport":
            self.origin = [float(parts[2]), float(parts[3]), float(parts[4])]
            if len(parts) >= 8:
                self.vel = [float(parts[5]), float(parts[6]), float(parts[7])]
            self.on_ground = True
            return {"ok": True, "data": {}}
        if verb == "goto":
            self._goto = True
            self.events.extend(self.emit_after_goto)
            return {"ok": True, "data": {}}
        if verb == "stop":
            self._goto = False
            return {"ok": True, "data": {}}
        if verb == "cell":
            if len(parts) >= 2 and parts[1] == "id":
                return {"ok": True, "data": {"cell": int(parts[2]), "origin": list(self.origin)}}
            return {"ok": True, "data": {"cell": self.cell_id, "origin": list(self.origin)}}
        if verb == "get":
            name = parts[1]
            if name == "rtx_nav_patch":
                return {"ok": True, "data": {"string": "0" if self.arm == "off" else "1"}}
            return {"ok": True, "data": {"string": "0"}}
        if verb == "set":
            return {"ok": True, "data": {}}
        if verb == "route":
            return {
                "ok": True,
                "data": {
                    "astar": {"found": True, "cost": 1.0, "mask_links": []},
                    "legs": [{"link": 3, "src_cell": 10, "tgt_cell": 11}],
                },
            }
        raise RuntimeError(f"unsupported mock cmd {cmd!r}")


def _driver(ctl=None, token="fable", port=27996, game=27592, stratum_at=None) -> tuple[LiveTrialDriver, MockCtl, FakeClock]:
    ctl = ctl or MockCtl()
    clock = FakeClock()
    recipe = load_recipe(HERE / "recept" / "west-shelf.json")
    gates = load_gates(HERE / "recept" / "west-shelf-gates.json")
    from d_strata import heldout_stratum_at
    locus = stratum_at if stratum_at is not None else heldout_stratum_at(gates)[0]
    drv = LiveTrialDriver(
        ctl,
        gate=gates["gates"]["west-shelf"],
        recipe=recipe,
        lock_token=token,
        qwprogs_sha=QW,
        mvdsv_sha=MV,
        commit="d08f8e0deadbeef",
        ctl_port=port,
        game_port=game,
        sleep=clock.sleep,
        now=clock.now,
        stratum_at=locus,
    )
    return drv, ctl, clock


class RefuseTests(unittest.TestCase):
    def test_ra_ctl_refused(self):
        self.assertIn("RA/main", refuse_ra(27990, 27592) or "")
        self.assertIn("RA/main", refuse_ra(27996, 27540) or "")
        self.assertIsNone(refuse_ra(27996, 27592))

    def test_driver_rejects_ra_port(self):
        with self.assertRaises(RuntimeError) as ctx:
            _driver(port=27990)
        self.assertIn("RA/main", str(ctx.exception))

    def test_main_refuses_ra_before_connect(self):
        import d_sjalvbevis as drill
        rc = drill.main(["--port", "27990"])
        self.assertEqual(rc, 2)
        rc = drill.main(["--port", "27996", "--game-port", "27540"])
        self.assertEqual(rc, 2)

    def test_run_requires_commit_when_git_missing(self):
        import d_sjalvbevis as drill
        orig = drill._git_commit
        drill._git_commit = lambda _repo: ""
        try:
            rc = drill.main(["--port", "27996", "--run"])
        finally:
            drill._git_commit = orig
        self.assertEqual(rc, 2)

    def test_restart_mentions_reset_failed(self):
        from d_live_driver import RESTART
        self.assertIn("reset-failed", RESTART)


class SequenceTests(unittest.TestCase):
    def test_t0_teleport_is_rest(self):
        drv, ctl, _ = _driver()
        ctl.emit_after_goto = [{
            "ev": "bot_stall",
            "bot": 1,
            "t": 1001.2,
            "cell": 5977,
            "origin": [-865.0, -48.0, 88.0],
            "reason": "displacement",
        }]
        spec = STRATA["T0"]
        raw = drv.exec_trial(stratum_id="T0", arm="off", spec=spec, seq=1, window_s=2.0)
        tele = [c for c in ctl.cmds if c.startswith("teleport")]
        self.assertEqual(len(tele), 1)
        toks = tele[0].split()
        self.assertEqual(toks[2:5], ["-865.0", "-48.0", "90.0"])
        self.assertEqual([float(x) for x in toks[5:8]], [0.0, 0.0, 0.0])
        self.assertTrue(any(c.startswith("prep ") for c in ctl.cmds))
        self.assertTrue(any(c.startswith("goto ") for c in ctl.cmds))
        self.assertTrue(any(c.startswith("stop ") for c in ctl.cmds))
        self.assertLessEqual(math.sqrt(sum(v * v for v in raw["vel"])), 1.0)
        stall = next(e for e in raw["events"] if e["ev"] == "bot_stall")
        self.assertIn("t", stall)
        self.assertIn("rel_t", stall)
        self.assertEqual(stall["engine_t"], 1001.2)
        self.assertLess(stall["t"], 5.0)
        self.assertEqual(raw["t_stall_gate"], stall["t"])
        att = classify_trial(
            stratum_id="T0", arm="off", vel=raw["vel"],
            events=raw["events"], samples=raw["samples"],
            gate=drv.gate, t_arrive=raw.get("t_arrive"),
        )
        self.assertTrue(att.valid)
        self.assertTrue(att.trap)

    def test_a1_commanded_vel_in_band(self):
        drv, ctl, _ = _driver()
        spec = STRATA["A1"]
        ctl.origin = list(spec["start"])
        # integrate vel on every status so the pre-goto pair also measures heading
        ctl._goto = True
        raw = drv.exec_trial(stratum_id="A1", arm="off", spec=spec, seq=1, window_s=0.3)
        tele = [c for c in ctl.cmds if c.startswith("teleport")][0].split()
        cmd = [float(x) for x in tele[5:8]]
        self.assertGreaterEqual(vh(cmd), 80.0)
        self.assertLess(vh(cmd), 160.0)
        self.assertGreaterEqual(direction_dot(cmd, spec["start"], spec["goal"]), 0.8)
        self.assertGreaterEqual(vh(raw["measured_vel"]), 80.0)
        self.assertLess(vh(raw["measured_vel"]), 160.0)
        self.assertGreaterEqual(
            direction_dot(raw["measured_vel"], spec["start"], spec["goal"]), 0.8
        )

    def test_a2_high_band(self):
        spec = STRATA["A2"]
        v = heading_vel(spec["start"], spec["goal"], mid_band_speed(spec))
        self.assertGreaterEqual(vh(v), 160.0)
        self.assertLessEqual(vh(v), 260.0)

    def test_arrived_carries_budget_time(self):
        drv, ctl, _ = _driver()
        ctl.emit_after_goto = [{
            "ev": "arrived",
            "bot": 1,
            "t": 50.0,
            "origin": [-864.0, -96.0, -16.0],
            "target": [-864.0, -96.0, -16.0],
            "dist": 1.0,
        }]
        raw = drv.exec_trial(stratum_id="T0", arm="on", spec=STRATA["T0"], seq=1, window_s=2.0)
        self.assertIsNotNone(raw["t_arrive"])
        self.assertLess(raw["t_arrive"], 0.6)
        ev = next(e for e in raw["events"] if e["ev"] == "arrived")
        self.assertEqual(ev["t"], raw["t_arrive"])
        att = classify_trial(
            stratum_id="T0", arm="on", vel=raw["vel"],
            events=raw["events"], samples=raw["samples"],
            gate=drv.gate, t_arrive=raw["t_arrive"],
        )
        self.assertTrue(att.arrived)

    def test_gate_velocity_against_registered_cell(self):
        drv, ctl, _ = _driver()
        ctl.cell_id = 5979
        ctl.origin = [-865.0, -48.0, 90.0]
        ctl.vel = [-130.0, 4.0, 0.0]
        ctl._goto = True
        spec = STRATA["A1"]
        watched = drv.watch(spec, 0.3)
        self.assertIsNotNone(watched["gate_velocity"])
        self.assertEqual(watched["gate_cell"], 5979)
        self.assertTrue(any(c.startswith("cell ") for c in ctl.cmds))

    def test_peak_drop_event_has_time(self):
        drv, ctl, _ = _driver()
        ctl.origin = [256.0, -704.0, 328.0]
        ctl.drop_after_goto = True
        ctl.on_ground = False
        raw = drv.exec_trial(stratum_id="A1", arm="off", spec=STRATA["A1"], seq=1, window_s=1.0)
        drops = [e for e in raw["events"] if e["ev"] == "peak_drop_150"]
        self.assertTrue(drops)
        self.assertIn("t", drops[0])
        self.assertGreater(drops[0]["t"], 0.0)


class ArmTests(unittest.TestCase):
    def test_apply_undo_send_lock_token(self):
        drv, ctl, _ = _driver(token="fable")
        drv.confirm("off")
        drv.apply()
        self.assertEqual(ctl.arm, "on")
        self.assertTrue(any("fixa west-shelf apply lock fable" == c for c in ctl.cmds))
        drv.undo()
        self.assertEqual(ctl.arm, "off")
        self.assertTrue(any("fixa west-shelf undo lock fable" == c for c in ctl.cmds))
        self.assertEqual(drv.last_stamps["on"]["cells"], 5981)
        self.assertEqual(drv.last_stamps["off"]["cells"], 5977)

    def test_apply_undo_quiesce_before_fixa(self):
        drv, ctl, _ = _driver(token="fable")
        drv.confirm("off")
        drv.apply()
        apply_i = ctl.cmds.index("fixa west-shelf apply lock fable")
        before = ctl.cmds[:apply_i]
        self.assertTrue(any(c.startswith("stop ") for c in before), before)
        self.assertTrue(any(c.startswith("hold ") for c in before), before)
        drv.undo()
        undo_i = ctl.cmds.index("fixa west-shelf undo lock fable")
        mid = ctl.cmds[apply_i + 1 : undo_i]
        self.assertTrue(any(c.startswith("stop ") for c in mid), mid)

    def test_apply_without_token_refused(self):
        drv, ctl, _ = _driver(token="")
        with self.assertRaises(RuntimeError):
            drv.apply()
        self.assertFalse(any("apply" in c for c in ctl.cmds))

    def test_apply_wrong_token_refused(self):
        drv, ctl, _ = _driver(token="not-fable")
        with self.assertRaises(RuntimeError) as ctx:
            drv.apply()
        self.assertIn("lock", str(ctx.exception).lower())
        self.assertEqual(ctl.arm, "off")

    def test_ensure_arm_roundtrip(self):
        drv, ctl, _ = _driver()
        drv.ensure_arm("off")
        drv.ensure_arm("on")
        self.assertEqual(ctl.arm, "on")
        drv.ensure_arm("off")
        self.assertEqual(ctl.arm, "off")


class KvittoTests(unittest.TestCase):
    def test_kvitto_carries_qwprogs_and_mvdsv(self):
        drv, ctl, _ = _driver()
        drv.confirm("off")
        drv.apply()
        drv.undo()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "T0-OFF-01.json"
            doc = drv.write_attempt_kvitto(
                path,
                attempt_id="T0-OFF-01",
                stratum_id="T0",
                raw_pointer=str(Path(td) / "T0-OFF-01.jsonl"),
                started_at="2026-08-16T09:50:00+00:00",
                ended_at="2026-08-16T09:50:02+00:00",
                lock_owner="fable",
                lock_issued="2026-08-16T08:40:06Z",
                gate_velocity=None,
                gate_cell=None,
            )
            self.assertEqual(doc["binary_sha256"], QW)
            self.assertEqual(doc["binaries"]["qwprogs_sha256"], QW)
            self.assertEqual(doc["binaries"]["mvdsv_sha256"], MV)
            self.assertEqual(verify(doc), [])
            disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(disk["binaries"]["qwprogs_sha256"], QW)
            self.assertEqual(disk["schema"], "verktygslada/d-kvitto/1")

    def test_kvitto_on_expected_from_fixture_not_observed_invention(self):
        drv, _, _ = _driver()
        drv.confirm("off")
        drv.apply()
        drv.undo()
        with tempfile.TemporaryDirectory() as td:
            doc = drv.write_attempt_kvitto(
                Path(td) / "x.json",
                attempt_id="T0-OFF-01",
                stratum_id="T0",
                raw_pointer=str(Path(td) / "x.jsonl"),
                started_at="2026-08-16T09:50:00+00:00",
                ended_at="2026-08-16T09:50:02+00:00",
                lock_owner="fable",
                lock_issued="2026-08-16T08:40:06Z",
            )
            self.assertEqual(doc["stamps"]["on"]["expected"]["cells"], 5981)
            self.assertEqual(
                doc["stamps"]["on"]["expected"]["graph_stamp"],
                "17645347086516095554",
            )
            self.assertEqual(doc["stamps"]["on"]["observed"]["cells"], 5981)

    def test_kvitto_refuses_unconfirmed_on(self):
        drv, _, _ = _driver()
        drv.confirm("off")
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError) as ctx:
                drv.write_attempt_kvitto(
                    Path(td) / "x.json",
                    attempt_id="T0-OFF-01",
                    stratum_id="T0",
                    raw_pointer=str(Path(td) / "x.jsonl"),
                    started_at="2026-08-16T09:50:00+00:00",
                    ended_at="2026-08-16T09:50:02+00:00",
                    lock_owner="fable",
                    lock_issued="2026-08-16T08:40:06Z",
                )
        self.assertIn("invent observed", str(ctx.exception))

    def test_kvitto_carries_gate_and_jsonl_pointer(self):
        drv, _, _ = _driver()
        drv.confirm("off")
        drv.apply()
        drv.undo()
        with tempfile.TemporaryDirectory() as td:
            jsonl = Path(td) / "A1-ON-01.jsonl"
            drv.write_attempt_raw(jsonl, {
                "stratum_id": "A1",
                "arm": "on",
                "seq": 1,
                "gate_velocity": [-130.0, 4.0, 0.0],
                "gate_cell": 5979,
                "events": [{"ev": "arrived", "t": 0.4}],
                "samples": [{"t": 0.1, "z": 90.0, "on_ground": True}],
            })
            doc = drv.write_attempt_kvitto(
                Path(td) / "A1-ON-01.json",
                attempt_id="A1-ON-01",
                stratum_id="A1",
                raw_pointer=str(jsonl),
                started_at="2026-08-16T09:50:00+00:00",
                ended_at="2026-08-16T09:50:02+00:00",
                lock_owner="fable",
                lock_issued="2026-08-16T08:40:06Z",
                gate_velocity=[-130.0, 4.0, 0.0],
                gate_cell=5979,
                gate_aim_hit=False,
            )
            self.assertEqual(doc["gate"]["velocity"], [-130.0, 4.0, 0.0])
            self.assertEqual(doc["gate"]["cell"], 5979)
            self.assertTrue(doc["raw_pointer"].endswith(".jsonl"))
            self.assertIn("/", doc["raw_pointer"])
            self.assertTrue(jsonl.is_file())
            self.assertEqual(verify(doc), [])

    def test_astar_keyed_by_stratum_not_stale(self):
        drv, _, _ = _driver()
        drv.confirm("off")
        drv.apply()
        drv.undo()
        drv.snapshot_astar(STRATA["T0"], "T0")
        with tempfile.TemporaryDirectory() as td:
            doc = drv.write_attempt_kvitto(
                Path(td) / "A1.json",
                attempt_id="A1-OFF-01",
                stratum_id="A1",
                raw_pointer=str(Path(td) / "A1.jsonl"),
                started_at="2026-08-16T09:50:00+00:00",
                ended_at="2026-08-16T09:50:02+00:00",
                lock_owner="fable",
                lock_issued="2026-08-16T08:40:06Z",
            )
            self.assertFalse(doc["astar"]["before"]["found"])
            drv.snapshot_astar(STRATA["A1"], "A1")
            doc2 = drv.write_attempt_kvitto(
                Path(td) / "A1b.json",
                attempt_id="A1-OFF-01",
                stratum_id="A1",
                raw_pointer=str(Path(td) / "A1b.jsonl"),
                started_at="2026-08-16T09:50:00+00:00",
                ended_at="2026-08-16T09:50:02+00:00",
                lock_owner="fable",
                lock_issued="2026-08-16T08:40:06Z",
            )
            self.assertTrue(doc2["astar"]["before"]["found"])

    def test_exec_trial_heldout_no_start_vel_fallback(self):
        drv, ctl, _ = _driver(stratum_at="gate")
        spec = STRATA["A1"]
        ctl.origin = list(spec["start"])
        raw = drv.exec_trial(stratum_id="A1", arm="off", spec=spec, seq=1, window_s=0.3)
        self.assertIsNone(raw["gate_velocity"])
        self.assertIsNone(raw["vel"])
        self.assertIsNotNone(raw["measured_vel"])

    def test_exec_trial_start_locus_uses_measured(self):
        drv, ctl, _ = _driver(stratum_at="start")
        spec = STRATA["A1"]
        ctl.origin = list(spec["start"])
        ctl._goto = True
        raw = drv.exec_trial(stratum_id="A1", arm="off", spec=spec, seq=1, window_s=0.3)
        self.assertEqual(raw["vel"], raw["measured_vel"])
        self.assertIsNotNone(raw["vel"])


class CellVerbTests(unittest.TestCase):
    def test_control_parses_cell_and_cell_id(self):
        from runner.control import _parse_verb
        self.assertEqual(_parse_verb("cell -865 -48 90"), {"Cell": {"pos": [-865.0, -48.0, 90.0]}})
        self.assertEqual(_parse_verb("cell id 5979"), {"CellById": {"cell": 5979}})


class AnnotateTests(unittest.TestCase):
    def test_engine_t_preserved(self):
        row = annotate_event({"ev": "arrived", "t": 99.5}, 0.4)
        self.assertEqual(row["engine_t"], 99.5)
        self.assertEqual(row["t"], 0.4)
        self.assertEqual(row["rel_t"], 0.4)

    def test_pmove_is_dropped(self):
        drv, ctl, _ = _driver()
        ctl.emit_after_goto = [
            {"ev": "pmove", "bot": 1, "t": 1.0},
            {"ev": "bot_stall", "bot": 1, "t": 1.1, "cell": 5977,
             "origin": [-865.0, -48.0, 88.0], "reason": "displacement"},
            {"ev": "seat_heartbeat", "t": 1.2},
        ]
        raw = drv.exec_trial(stratum_id="T0", arm="off", spec=STRATA["T0"], seq=1, window_s=1.0)
        kinds = [e["ev"] for e in raw["events"]]
        self.assertIn("bot_stall", kinds)
        self.assertNotIn("pmove", kinds)
        self.assertNotIn("seat_heartbeat", kinds)


if __name__ == "__main__":
    unittest.main()
