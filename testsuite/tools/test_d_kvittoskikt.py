#!/usr/bin/env python3
"""Kvitto-layer isolation: per-owner subdir, O_EXCL, verify-before-write, pointer⇒file.

Live-formed inputs come from lab/turnering-k1-r3b (read-only) or the
checked-in testdata/kvittoskikt/ extract of those receipts.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_lab_guard  # noqa: F401 — suite-global lab-vakt
from d_kvitto import (  # noqa: E402
    foreign_kvitto_entries,
    recipe_kvitto_paths,
    refuse_shared_kvitto_dir,
    write_exclusive,
    write_kvitto,
)
from d_ram_sjalvbevis import RamRunner, sha256_file  # noqa: E402
from d_recipe import load_recipe  # noqa: E402
from d_turnering import TournamentRunner, file_sha256  # noqa: E402
from test_d_ram_sjalvbevis import _kb, _prevent_gates, _rail_gates, _trial  # noqa: E402
from test_d_turnering import _clean_on, _gates, _hearth_raw, _recipe  # noqa: E402
from verify_d_kvitto import verify  # noqa: E402

LAB_R3B = Path.home() / "lab" / "turnering-k1-r3b"
TD = HERE / "testdata" / "kvittoskikt"

_FZ_PATCH = None


def setUpModule():
    import test_lab_guard
    test_lab_guard.install_lab_guard(suite_global=True)
    global _FZ_PATCH
    _ctx, _FZ_PATCH = test_lab_guard.inject_test_freeze()


def tearDownModule():
    global _FZ_PATCH
    if _FZ_PATCH is not None:
        _FZ_PATCH.stop()
        _FZ_PATCH = None



def _live_pair(stem: str) -> tuple[Path, Path]:
    for root in (LAB_R3B, TD):
        js, jl = root / f"{stem}.json", root / f"{stem}.jsonl"
        if js.is_file() and jl.is_file():
            return js, jl
    raise FileNotFoundError(stem)


class RefuseSharedTests(unittest.TestCase):
    def test_empty_dir_ok_without_flag(self):
        with tempfile.TemporaryDirectory() as td:
            refuse_shared_kvitto_dir(Path(td), "haz1462-k1", allow_shared=False)

    def test_foreign_flat_r3b_receipt_refused(self):
        js, _ = _live_pair("1416-1461-ON-01")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dest = root / js.name
            dest.write_bytes(js.read_bytes())
            self.assertEqual(foreign_kvitto_entries(root, "haz1462-k2"), [js.name])
            with self.assertRaises(RuntimeError) as ctx:
                refuse_shared_kvitto_dir(root, "haz1462-k2", allow_shared=False)
            self.assertIn("allow-shared", str(ctx.exception))
            refuse_shared_kvitto_dir(root, "haz1462-k2", allow_shared=True)
            self.assertEqual(dest.read_bytes(), js.read_bytes())

    def test_other_candidate_subdir_refused_without_flag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "haz1462-k1").mkdir()
            with self.assertRaises(RuntimeError):
                refuse_shared_kvitto_dir(root, "haz1462-k2", allow_shared=False)
            refuse_shared_kvitto_dir(root, "haz1462-k1", allow_shared=False)


class TournamentIsolationTests(unittest.TestCase):
    def test_two_candidates_share_dir_only_with_flag(self):
        js, jl = _live_pair("1416-1461-ON-01")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            k1_json, k1_jsonl = recipe_kvitto_paths(root, "haz1462-k1", "1416-1461-ON-01")
            k1_json.parent.mkdir(parents=True)
            k1_json.write_bytes(js.read_bytes())
            k1_jsonl.write_bytes(jl.read_bytes())

            with self.assertRaises(RuntimeError):
                TournamentRunner(
                    recipe=_recipe("haz1462-k3"),
                    gates=_gates(),
                    exec_trial=lambda **k: _clean_on(),
                    ctl_port=0,
                    game_port=27592,
                    fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k3.json"),
                    n_repro=1,
                    n_heldout=0,
                    kvitto_dir=root,
                )

            def exec_trial(**k):
                if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                    return _hearth_raw()
                return _clean_on()

            runner = TournamentRunner(
                recipe=_recipe("haz1462-k3"),
                gates=_gates(),
                exec_trial=exec_trial,
                ctl_port=0,
                game_port=27592,
                fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k3.json"),
                n_repro=1,
                n_heldout=0,
                kvitto_dir=root,
                allow_shared=True,
                binaries={"qwprogs_sha256": "ab" * 32, "mvdsv_sha256": "cd" * 32},
            )
            runner.app_routes = []
            runner.run()
            self.assertEqual(k1_json.read_bytes(), js.read_bytes())
            self.assertEqual(k1_jsonl.read_bytes(), jl.read_bytes())
            k3_files = list((root / "haz1462-k3").glob("*.json"))
            self.assertTrue(k3_files)
            self.assertFalse(list(root.glob("*.json")))

    def test_mock_pointer_file_exists_and_r3b_raw_is_readable(self):
        js, jl = _live_pair("1416-1124-ON-01")
        live_doc = json.loads(js.read_text(encoding="utf-8"))
        live_raw = jl.read_text(encoding="utf-8")
        self.assertEqual(live_doc["candidate"], "haz1462-k1")
        self.assertEqual(live_doc["stratum"]["attempt"], "1416-1124-ON-01")
        self.assertIn("1416-1124", live_raw)
        self.assertTrue(
            '"ev": "arrived"' in live_raw or '"ev":"arrived"' in live_raw,
            "r3b 1416-1124-ON-01 raw must carry the live arrived event",
        )

        with tempfile.TemporaryDirectory() as td:
            def exec_trial(**k):
                if k["arm"] == "off" and k["stratum_id"] in {"in_vast", "in_tunnel"}:
                    return _hearth_raw()
                return _clean_on()

            runner = TournamentRunner(
                recipe=_recipe(),
                gates=_gates(),
                exec_trial=exec_trial,
                ctl_port=0,
                game_port=27592,
                fixture_sha256=file_sha256(HERE / "recept" / "haz1462-k1.json"),
                n_repro=1,
                n_heldout=0,
                kvitto_dir=Path(td),
                binaries={"qwprogs_sha256": "ab" * 32, "mvdsv_sha256": "cd" * 32},
            )
            runner.app_routes = []
            runner.run()
            written = list((Path(td) / "haz1462-k1").glob("*.json"))
            self.assertTrue(written)
            for path in written:
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(verify(doc), [], path.name)
                raw_p = Path(doc["raw_pointer"])
                self.assertTrue(raw_p.is_file(), doc["raw_pointer"])
                self.assertGreater(raw_p.stat().st_size, 0)


class VerifyBeforeWriteTests(unittest.TestCase):
    def test_malformed_never_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "haz1462-k1" / "bad.json"
            with self.assertRaises(RuntimeError) as ctx:
                write_kvitto(path, {"schema": "nope"}, exclusive=True, verify_first=True)
            self.assertIn("verify", str(ctx.exception))
            self.assertFalse(path.exists(), "verify failure must not O_EXCL-lock the name")

    def test_second_write_same_path_raises_and_preserves_r3b_bytes(self):
        js, _ = _live_pair("1416-1461-ON-01")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "keep.json"
            write_exclusive(path, js.read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                write_exclusive(path, "clobber\n")
            self.assertEqual(path.read_bytes(), js.read_bytes())


class RamMockPointerTests(unittest.TestCase):
    def test_mock_writes_jsonl_named_by_pointer(self):
        fx = HERE / "recept" / "ram-rail.json"
        with tempfile.TemporaryDirectory() as td:
            runner = RamRunner(
                recipe=load_recipe(fx),
                rail_gates=_rail_gates(),
                prevent_gates=_prevent_gates(),
                exec_knockback=_kb,
                exec_trial=_trial,
                ctl_port=0,
                game_port=27595,
                n_knock=1,
                n_chain=0,
                kvitto_dir=Path(td),
                demo_file="qw/demos/ram.mvd",
                binaries={"qwprogs_sha256": "ab" * 32, "mvdsv_sha256": "cd" * 32},
                fixture_sha256=sha256_file(fx),
            )
            runner.run()
            files = list((Path(td) / "ram-rail").glob("*.json"))
            self.assertTrue(files)
            for path in files:
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(Path(doc["raw_pointer"]).is_file(), doc["raw_pointer"])

    def test_prevent_without_allow_shared_refuses_occupied_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "ram-rail-v2").mkdir()
            with self.assertRaises(RuntimeError):
                RamRunner(
                    recipe=load_recipe(HERE / "recept" / "ram-prevent.json"),
                    rail_gates=_rail_gates(),
                    prevent_gates=_prevent_gates(),
                    exec_knockback=_kb,
                    exec_trial=_trial,
                    ctl_port=0,
                    game_port=27595,
                    n_knock=0,
                    n_chain=1,
                    kvitto_dir=root,
                )


if __name__ == "__main__":
    unittest.main()
