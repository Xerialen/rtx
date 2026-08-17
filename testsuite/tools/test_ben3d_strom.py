"""Hermetiskt syntetiskt broadcasttest (A6d): CLI + två viewerklienter mot en
syntetisk ström, samma stream_id/seq/payload_sha256. Ingen socket, ingen rigg."""

from __future__ import annotations
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "ben3d"))
import ben3d_strom as S  # noqa: E402


class StromTests(unittest.TestCase):
    def _stream(self, td: Path, n=5):
        sid = "syn-1"
        lines = [S.make_header(sid, {"dataset": "t1h", "arm": "fork"})]
        for i in range(n):
            lines.append(S.make_tick(sid, "t1h:fork:c001:in_vast", str(i), i + 1,
                                     {"t": f"{i}.0", "x": f"{i*10}.0", "y": "0.0", "z": "0.0", "cell": i}))
        lines.append(S.make_end(sid, n + 1, n, "rot-abc"))
        p = td / "stream.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
        return p, lines

    def test_broadcast_samma_kalla(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-strom-"))
        p, lines = self._stream(td)
        # tre oberoende läsare: CLI + två "viewerklienter"
        readers = []
        for _ in range(2):
            readers.append(list(S.read_stream(str(p))))
        cli = subprocess.run([sys.executable, str(HERE / "ben3d" / "ben3d_strom.py"), "tail", str(p)],
                             capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn("ok=7", cli.stdout)  # header + 5 ticks + end
        for r in readers:
            ticks = [rec for rec, st in r if rec and rec["typ"] == "tick" and st == "ok"]
            self.assertEqual(len(ticks), 5)
            self.assertEqual([t["tick_id"] for t in ticks], [str(i) for i in range(5)])
            self.assertEqual([t["payload_sha256"] for t in ticks],
                             [S.make_tick("syn-1", "t1h:fork:c001:in_vast", str(i), i + 1,
                                          {"t": f"{i}.0", "x": f"{i*10}.0", "y": "0.0", "z": "0.0", "cell": i})["payload_sha256"]
                              for i in range(5)])
        # end binder sista seq + antal
        end = [rec for rec, _ in readers[0] if rec and rec["typ"] == "end"][0]
        self.assertEqual(end["antal_ticks"], 5)
        self.assertEqual(end["seq"], 6)

    def test_lucka_dubblett_partial_abort(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-strom-"))
        p, lines = self._stream(td, n=3)
        # lucka: ta bort seq 2
        body = p.read_text().splitlines()
        body = [l for l in body if json.loads(l).get("typ") != "tick" or json.loads(l)["seq"] != 2]
        p.write_text("\n".join(body) + "\n")
        sts = [st for rec, st in S.read_stream(str(p)) if rec and rec["typ"] == "tick"]
        self.assertIn("gap", sts)
        # dubblett: duplicera en tick-rad
        p2, _ = self._stream(td, n=2)
        lines2 = p2.read_text().splitlines()
        tickline = next(l for l in lines2 if json.loads(l).get("typ") == "tick")
        p2.write_text("\n".join(lines2[:1] + [tickline, tickline] + lines2[1:]) + "\n")
        sts2 = [st for rec, st in S.read_stream(str(p2)) if rec and rec["typ"] == "tick"]
        self.assertIn("dup", sts2)
        # abort: ström förblir OFÖRSEGLAD (inget end)
        p3 = td / "abort.jsonl"
        p3.write_text(json.dumps(S.make_header("s")) + "\n" + json.dumps(S.make_abort("s", 1, "krasch")) + "\n")
        typs = [rec["typ"] for rec, _ in S.read_stream(str(p3))]
        self.assertEqual(typs, ["header", "abort"])

    def test_payload_sha_valideras(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-strom-"))
        rec = S.make_tick("s", "b", "0", 1, {"x": "1.0"})
        rec["payload_sha256"] = "0" * 64
        with self.assertRaises(S.StromError):
            S.validate_record(rec)


if __name__ == "__main__":
    unittest.main()
