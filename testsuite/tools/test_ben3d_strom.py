"""Hermetiskt syntetiskt broadcasttest (A6d): CLI-läsare + navviewer-formatläsare
(två OLIKA läsare) mot samma syntetiska ström. D3-livscykel fail-closed."""

from __future__ import annotations
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "ben3d"))
import ben3d_strom as S  # noqa: E402
import navviewer_strom_reader as N  # noqa: E402


def _rot() -> str:
    return "a" * 64


class StromTests(unittest.TestCase):
    def _stream(self, td: Path, n=5):
        sid = "syn-1"
        lines = [S.make_header(sid, {"dataset": "t1h", "arm": "fork"})]
        for i in range(n):
            lines.append(S.make_tick(sid, "t1h:fork:c001:in_vast", str(i), i + 1,
                                     {"t": f"{i}.0", "x": f"{i*10}.0", "y": "0.0", "z": "0.0", "cell": i}))
        lines.append(S.make_end(sid, n + 1, n, _rot()))
        p = td / "stream.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
        return p

    def test_broadcast_tva_olika_lasare(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-strom-"))
        p = self._stream(td)
        # läsare 1: CLI (subprocess)
        cli = subprocess.run([sys.executable, str(HERE / "ben3d" / "ben3d_strom.py"), "tail", str(p)],
                             capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn("FRUSEN", cli.stdout)
        # läsare 2: navviewer-formatläsare (annan kod)
        nv = N.consume(str(p))
        self.assertEqual(nv["stream_id"], "syn-1")
        self.assertEqual(nv["status"], "FRUSEN")
        self.assertEqual(len(nv["ticks"]), 5)
        self.assertEqual(nv["slutrot"], _rot())
        # samma payload-sha hos båda
        self.assertEqual(nv["ticks"][0]["payload_sha256"],
                         S.make_tick("syn-1", "t1h:fork:c001:in_vast", "0", 1, {"t": "0.0", "x": "0.0", "y": "0.0", "z": "0.0", "cell": 0})["payload_sha256"])

    def test_rot_abc_vagras_och_gap_ger_exit2(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-strom-"))
        # ogiltig slutrot "rot-abc" -> ej frysbar
        p = self._stream(td)
        body = [json.loads(l) for l in p.read_text().splitlines()]
        body[-1]["slutrot"] = "rot-abc"
        p.write_text("\n".join(json.dumps(r) for r in body) + "\n")
        self.assertNotEqual(N.consume(str(p))["status"], "FRUSEN")
        # gap => CLI exit != 0
        p2 = self._stream(td, n=3)
        body2 = [l for l in p2.read_text().splitlines() if json.loads(l).get("typ") != "tick" or json.loads(l)["seq"] != 2]
        p2.write_text("\n".join(body2) + "\n")
        cli = subprocess.run([sys.executable, str(HERE / "ben3d" / "ben3d_strom.py"), "tail", str(p2)],
                             capture_output=True, text=True)
        self.assertNotEqual(cli.returncode, 0)

    def test_abort_ar_oforseglad(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-strom-"))
        p = td / "abort.jsonl"
        p.write_text(json.dumps(S.make_header("s")) + "\n" + json.dumps(S.make_abort("s", 1, "krasch")) + "\n")
        self.assertEqual(N.consume(str(p))["status"], "LIVE/OFÖRSEGLAD STRÖM")

    def test_payload_sha_valideras(self):
        rec = S.make_tick("s", "b", "0", 1, {"x": "1.0"})
        rec["payload_sha256"] = "0" * 64
        with self.assertRaises(S.StromError):
            S.validate_record(rec)


if __name__ == "__main__":
    unittest.main()
