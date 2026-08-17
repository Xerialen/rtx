"""Hermetiska fixturtester för ben3d_verify (etapp 6). Ingen socket, ingen ~/lab."""

from __future__ import annotations
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "ben3d"))
from ben3d_verify import canonical, sha  # noqa: E402


class CanonicalTests(unittest.TestCase):
    def test_rfc8785_vektorer(self):
        self.assertEqual(canonical({"b": 1, "a": "x\"y", "c": [True, None, "n\n"]}),
                         '{"a":"x\\"y","b":1,"c":[true,null,"n\\n"]}')
        self.assertEqual(canonical({"z": None, "å": "ö"}), '{"z":null,"å":"ö"}')

    def test_flyttal_vagras(self):
        with self.assertRaises(SystemExit):
            canonical({"a": 1.5})


class VerifyTests(unittest.TestCase):
    def _bunt(self, ben_id):
        b = {
            "schema": "ben3d-bunt/1",
            "ben_id": ben_id,
            "geometri": {"dump_id": "d", "dump_sha256": "0"*64, "dump_schema": "qw-nav-graph/1",
                         "arm": "fork", "cells": 2, "links": 1},
            "tickserie": {"farg2_observerad_bana": [
                {"raw_row_index": 0, "t": "1.5", "x": "0.0", "y": "0.0", "z": "0.0", "cell": 0}],
                "farg3": {"klass": "okänd", "skal": "s", "antal": 0}},
            "proveniens": {"dataset_manifest": {"sha256": "a"*64}},
        }
        payload = {"schema": b["schema"], "ben_id": b["ben_id"],
                   "geometri": b["geometri"], "tickserie": b["tickserie"]}
        b["bundle_payload_sha256"] = sha(canonical(payload))
        return b

    def test_verifiera_och_stopp(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-verify-"))
        buntar = td / "buntar"
        buntar.mkdir()
        for name, ben_id in [("a", "t1h:fork:c001:in_vast"), ("b", "t1h:fork:c002:in_vast")]:
            (buntar / f"{name}.bunt.json").write_text(json.dumps(self._bunt(ben_id)))
        for f in ["t1h.sha256", "t20m.sha256", "fork.json", "base.json", "bin", "viewer.html"]:
            (td / f).write_bytes(b"x")
        args = [sys.executable, str(HERE / "ben3d" / "ben3d_verify.py"),
                "--buntar", str(buntar), "--t1h", str(td/"t1h.sha256"), "--t20m", str(td/"t20m.sha256"),
                "--fork-dump", str(td/"fork.json"), "--base-dump", str(td/"base.json"),
                "--extractor-bin", str(td/"bin"), "--viewer", str(td/"viewer.html"),
                "--n", "2", "--out", str(td/"verify.json")]
        r = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 buntar OK", r.stdout)
        # korrupta en payload-sha -> STOPP
        b = json.loads((buntar/"a.bunt.json").read_text())
        b["bundle_payload_sha256"] = "0"*64
        (buntar/"a.bunt.json").write_text(json.dumps(b))
        r2 = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(r2.returncode, 2)
        self.assertIn("payload-sha", r2.stderr)


if __name__ == "__main__":
    unittest.main()
